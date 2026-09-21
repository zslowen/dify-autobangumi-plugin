from __future__ import annotations

import json
import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from anime_subscription_agent.mikan import MikanReadOnlyConnector
from anime_subscription_agent.mikan_adapter import inspect_mikan_rss_candidate


RSS_URL = "https://mikanani.me/RSS/Bangumi?subgroupid=370&bangumiId=2402"
NORMALIZED_URL = "https://mikanime.tv/RSS/Bangumi?bangumiId=2402&subgroupid=370"


class FakeResponse:
    status = 200

    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def read(self, amount=None):
        return self.body if amount is None else self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class StaticOpener:
    def __init__(self, body: str):
        self.body = body
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return FakeResponse(self.body)


class HttpErrorOpener:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        raise HTTPError(
            request.full_url,
            self.status,
            "upstream failure",
            Message(),
            BytesIO(self.body),
        )


def feed(items: str, title: str = "LoliHouse - Nonstop") -> str:
    return f"<?xml version='1.0'?><rss><channel><title>{title}</title>{items}</channel></rss>"


class MikanInspectorTests(unittest.TestCase):
    def test_valid_rss_returns_bounded_structured_samples_in_feed_order(self):
        items = "".join(
            f"<item><title>Episode {number:02d} [1080p][CHS]</title>"
            f"<pubDate>Mon, {number:02d} Jan 2024 00:00:00 +0000</pubDate></item>"
            for number in range(1, 8)
        )
        opener = StaticOpener(feed(items))
        result = MikanReadOnlyConnector(opener=opener).inspect_rss_candidate(RSS_URL)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["normalized_rss_url"], NORMALIZED_URL)
        self.assertEqual((result["bangumi_id"], result["subgroup_id"]), (2402, 370))
        self.assertEqual(result["feed_title"], "LoliHouse - Nonstop")
        self.assertEqual(result["item_count"], 7)
        self.assertEqual(len(result["sample_items"]), 5)
        self.assertEqual(
            [item["title"] for item in result["sample_items"]],
            [f"Episode {number:02d} [1080p][CHS]" for number in range(1, 6)],
        )
        self.assertEqual(result["sample_order"], "feed_order_first_5")
        self.assertEqual(result["latest_published_at"], "2024-01-07T00:00:00+00:00")
        self.assertEqual(result["sample_items"][0]["resolutions"], ["1080p"])
        self.assertEqual(
            result["sample_items"][0]["subtitle_languages"], ["简体中文"]
        )
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0].method, "GET")

    def test_empty_feed_is_reachable_and_explicit(self):
        result = MikanReadOnlyConnector(
            opener=StaticOpener(feed(""))
        ).inspect_rss_candidate(RSS_URL)
        self.assertEqual(result["status"], "empty_feed")
        self.assertTrue(result["reachable"])
        self.assertEqual(result["item_count"], 0)
        self.assertIn("empty_feed", {item["code"] for item in result["diagnostics"]})

    def test_invalid_xml_and_non_rss_xml_are_explicit(self):
        for document, code in (
            ("not xml <", "invalid_xml"),
            ("<html><body>not rss</body></html>", "invalid_rss_structure"),
        ):
            with self.subTest(code=code):
                result = MikanReadOnlyConnector(
                    opener=StaticOpener(document)
                ).inspect_rss_candidate(RSS_URL)
                self.assertEqual(result["status"], "invalid_rss")
                self.assertTrue(result["reachable"])
                self.assertIn(code, {item["code"] for item in result["diagnostics"]})

    def test_missing_time_does_not_fail_the_feed(self):
        items = "<item><title>Episode 01</title></item>"
        result = MikanReadOnlyConnector(
            opener=StaticOpener(feed(items))
        ).inspect_rss_candidate(RSS_URL)
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["latest_published_at"])
        self.assertIsNone(result["sample_items"][0]["published_at"])
        self.assertIn(
            "item_dates_missing", {item["code"] for item in result["diagnostics"]}
        )

    def test_namespaced_nested_publication_time_is_parsed(self):
        document = """<?xml version='1.0'?>
        <rss xmlns:torrent='https://mikanani.me/0.1/'><channel><title>Feed</title>
        <item><title>Episode 01</title><torrent:metadata>
        <torrent:pubDate>2024-01-08T12:30:00Z</torrent:pubDate>
        </torrent:metadata></item></channel></rss>"""
        result = MikanReadOnlyConnector(
            opener=StaticOpener(document)
        ).inspect_rss_candidate(RSS_URL)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["latest_published_at"], "2024-01-08T12:30:00+00:00")
        self.assertEqual(
            result["sample_items"][0]["published_at"],
            "2024-01-08T12:30:00+00:00",
        )

    def test_upstream_http_statuses_are_structured_without_response_leakage(self):
        sensitive_body = b'{"message":"token=secret-value cookie=session-value"}'
        for status in (403, 406, 503):
            with self.subTest(status=status), patch(
                "anime_subscription_agent.mikan_adapter._mikan_connector",
                return_value=(
                    MikanReadOnlyConnector(
                        opener=HttpErrorOpener(status, sensitive_body)
                    ),
                    "https://mikanime.tv",
                ),
            ):
                result = inspect_mikan_rss_candidate(RSS_URL)
                self.assertEqual(result["status"], "request_failed")
                self.assertEqual(result["failure_stage"], "mikan_preflight")
                self.assertEqual(result["http_status"], status)
                self.assertTrue(result["reachable"])
                self.assertEqual(result["normalized_rss_url"], NORMALIZED_URL)
                rendered = json.dumps(result, ensure_ascii=False)
                self.assertNotIn("secret-value", rendered)
                self.assertNotIn("session-value", rendered)

    def test_url_allowlist_and_query_validation_happen_before_network(self):
        opener = StaticOpener(feed(""))
        connector = MikanReadOnlyConnector(opener=opener)
        cases = (
            "https://example.com/RSS/Bangumi?bangumiId=2402&subgroupid=370",
            "https://mikanime.tv/RSS/Bangumi?bangumiId=2402",
            "https://mikanime.tv/RSS/Bangumi?bangumiId=2402&subgroupid=370&token=x",
            "https://user:password@mikanime.tv/RSS/Bangumi?bangumiId=2402&subgroupid=370",
            "https://mikanime.tv/RSS/Bangumi?bangumiId=0&subgroupid=370",
        )
        for rss_url in cases:
            with self.subTest(rss_url=rss_url), patch(
                "anime_subscription_agent.mikan_adapter._mikan_connector",
                return_value=(connector, "https://mikanime.tv"),
            ):
                result = inspect_mikan_rss_candidate(rss_url)
                self.assertEqual(result["status"], "url_validation_failed")
                self.assertEqual(result["failure_stage"], "mikan_url_validation")
                rendered = json.dumps(result, ensure_ascii=False)
                self.assertNotIn("token=x", rendered)
                self.assertNotIn("user:password", rendered)
        self.assertEqual(opener.requests, [])


if __name__ == "__main__":
    unittest.main()
