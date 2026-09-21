from __future__ import annotations

import json
import unittest
from email.message import Message
from urllib.parse import urlsplit
from urllib.request import Request
from urllib.error import URLError
from urllib.error import HTTPError

from anime_subscription_agent.mikan import (
    MikanPolicyError,
    MikanReadOnlyConnector,
    MikanProtocolError,
    MikanRequestError,
)


SEARCH_FIXTURE = """
<html><body><h1>搜索结果</h1>
<a href="/Home/Bangumi/42">Sample Anime</a>
</body></html>
"""

DETAIL_FIXTURE = """
<html><body>
<p class="bangumi-title">Sample Anime <a href="/RSS/Bangumi?bangumiId=42">RSS</a></p>
<a href="/Home/PublishGroup/100">Example Subs</a>
<a href="/RSS/Bangumi?bangumiId=42&amp;subgroupid=200">RSS</a>
<a href="/Home/PublishGroup/101">Other Group</a>
<a href="/RSS/Bangumi?bangumiId=42&amp;subgroupid=201">RSS</a>
</body></html>
"""

RSS_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<rss><channel>
<item><title>[Example Subs] Sample Anime - 01 [1080p][简繁内封]</title></item>
<item><title>[Example Subs] Sample Anime - 02 [720p][CHT]</title></item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def read(self, amount=None):
        return self.body if amount is None else self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FakeOpener:
    def __init__(self):
        self.requests: list[Request] = []

    def open(self, request, timeout):
        self.requests.append(request)
        parsed = urlsplit(request.full_url)
        if parsed.path == "/Home/Search":
            return FakeResponse(SEARCH_FIXTURE)
        if parsed.path == "/Home/Bangumi/42":
            return FakeResponse(DETAIL_FIXTURE)
        if parsed.path == "/RSS/Bangumi":
            return FakeResponse(RSS_FIXTURE)
        raise AssertionError(request.full_url)


class FailingOpener:
    def open(self, request, timeout):
        raise URLError("fixture network failure")


class AmbiguousOpener(FakeOpener):
    def open(self, request, timeout):
        self.requests.append(request)
        parsed = urlsplit(request.full_url)
        if parsed.path == "/Home/Search":
            return FakeResponse(
                '<h1>搜索结果</h1><a href="/Home/Bangumi/42">Sample Anime</a>'
                '<a href="/Home/Bangumi/43">Sample Anime Season 2</a>'
            )
        if parsed.path == "/Home/Bangumi/43":
            return FakeResponse(DETAIL_FIXTURE.replace("Sample Anime", "Sample Anime Season 2").replace("42", "43"))
        return super().open(request, timeout)


class RedirectingOpener:
    def __init__(self, locations):
        self.locations = list(locations)
        self.requests: list[Request] = []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.locations:
            location = self.locations.pop(0)
            headers = Message()
            headers["Location"] = location
            raise HTTPError(request.full_url, 302, "Found", headers, None)
        return FakeResponse("<html><body>ok</body></html>")


class MikanConnectorTests(unittest.TestCase):
    def connector(self):
        opener = FakeOpener()
        return MikanReadOnlyConnector(opener=opener), opener

    def test_search_group_rss_returns_public_structured_candidates(self):
        connector, opener = self.connector()
        results = connector.search_rss("Sample Anime", "Example Subs")
        self.assertEqual(len(results), 1)
        candidate = results[0]
        self.assertEqual(candidate["anime_title"], "Sample Anime")
        self.assertEqual(candidate["fansub_group"], "Example Subs")
        self.assertEqual(candidate["subgroup_id"], 200)
        self.assertEqual(candidate["resolutions"], ["1080p", "720p"])
        self.assertEqual(candidate["subtitle_languages"], ["简体中文", "繁体中文"])
        self.assertEqual(candidate["rss_item_count"], 2)
        self.assertEqual(
            candidate["rss_url"],
            "https://mikanime.tv/RSS/Bangumi?bangumiId=42&subgroupid=200",
        )
        self.assertTrue(all(request.method == "GET" for request in opener.requests))

    def test_unsafe_domains_methods_and_paths_are_blocked_before_network(self):
        with self.assertRaises(ValueError):
            MikanReadOnlyConnector("https://example.com")
        connector, opener = self.connector()
        with self.assertRaises(MikanPolicyError):
            connector._assert_allowed("POST", "/Home/Search", {"searchstr": "test"})
        with self.assertRaises(MikanPolicyError):
            connector._assert_allowed("GET", "/RSS/MyBangumi", {})
        self.assertEqual(opener.requests, [])

    def test_page_structure_errors_are_explicit(self):
        connector, _ = self.connector()
        with self.assertRaisesRegex(ValueError, "anime_title is required"):
            connector.search_rss(" ")
        with self.assertRaises(MikanProtocolError):
            connector._parse_search_results("<html><body>unexpected</body></html>")

    def test_multiple_results_and_network_errors_are_handled(self):
        connector, _ = self.connector()
        results = connector._parse_search_results(
            '<h1>搜索结果</h1><a href="/Home/Bangumi/42">One</a>'
            '<a href="/Home/Bangumi/43">Two</a>'
        )
        self.assertEqual(results, [(42, "One"), (43, "Two")])
        failing = MikanReadOnlyConnector(opener=FailingOpener())
        with self.assertRaises(MikanRequestError):
            failing.search_rss("Sample Anime")

    def test_search_anime_returns_distinct_work_candidates_without_inferred_metadata(self):
        result = MikanReadOnlyConnector(opener=AmbiguousOpener()).search_anime("Sample Anime")
        self.assertEqual(result["candidate_total"], 2)
        self.assertEqual([item["bangumi_id"] for item in result["candidates"]], [42, 43])
        self.assertEqual(result["candidates"][1]["anime_title"], "Sample Anime Season 2")
        self.assertNotIn("season", result["candidates"][1])

    def test_list_rss_candidates_filters_and_reports_truncation(self):
        connector, _ = self.connector()
        result = connector.list_rss_candidates(42, resolution="1080p", limit=1)
        self.assertEqual(result["candidate_total"], 2)
        self.assertEqual(result["returned_count"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["candidates"][0]["resolutions"], ["1080p", "720p"])
        language = connector.list_rss_candidates(42, subtitle_language="简体中文", limit=5)
        self.assertEqual(language["candidate_total"], 2)
        no_match = connector.list_rss_candidates(42, fansub_group="Missing", limit=5)
        self.assertEqual(no_match["candidate_total"], 0)

    def test_list_rss_candidates_rejects_invalid_ids_and_limits_before_network(self):
        connector, opener = self.connector()
        for bangumi_id in (0, -1, True):
            with self.subTest(bangumi_id=bangumi_id):
                with self.assertRaisesRegex(ValueError, "bangumi_id"):
                    connector.list_rss_candidates(bangumi_id)
        with self.assertRaisesRegex(ValueError, "limit"):
            connector.list_rss_candidates(42, limit=11)
        self.assertEqual(opener.requests, [])

    def test_same_mirror_redirect_keeps_the_public_get_request(self):
        opener = RedirectingOpener(["https://mikanime.tv:443/Home/Bangumi/42"])
        connector = MikanReadOnlyConnector(opener=opener)
        self.assertEqual(connector._get_text("/Home/Bangumi/42"), "<html><body>ok</body></html>")
        self.assertEqual(len(opener.requests), 2)
        self.assertTrue(all(request.method == "GET" for request in opener.requests))

    def test_official_mirror_redirect_is_allowed(self):
        opener = RedirectingOpener(["https://mikanani.me/home/bangumi/42"])
        connector = MikanReadOnlyConnector(opener=opener)
        connector._get_text("/Home/Bangumi/42")
        self.assertEqual(urlsplit(opener.requests[1].full_url).hostname, "mikanani.me")

    def test_unsafe_redirect_targets_are_blocked(self):
        cases = [
            "https://example.com/Home/Bangumi/42",
            "http://mikanime.tv/Home/Bangumi/42",
            "https://mikanime.tv:444/Home/Bangumi/42",
            "/RSS/MyBangumi",
            "/RSS/Bangumi?bangumiId=42&subgroupid=200",
        ]
        for location in cases:
            with self.subTest(location=location):
                connector = MikanReadOnlyConnector(opener=RedirectingOpener([location]))
                with self.assertRaisesRegex(MikanRequestError, "redirect was blocked"):
                    connector._get_text("/Home/Bangumi/42")

    def test_redirect_query_changes_and_loops_are_blocked(self):
        changed = MikanReadOnlyConnector(
            opener=RedirectingOpener(["/Home/Search?searchstr=changed"])
        )
        with self.assertRaisesRegex(MikanRequestError, "redirect was blocked"):
            changed._get_text("/Home/Search", {"searchstr": "original"})
        polluted = MikanReadOnlyConnector(
            opener=RedirectingOpener(["/Home/Search?searchstr=original&extra=value"])
        )
        with self.assertRaisesRegex(MikanRequestError, "redirect was blocked"):
            polluted._get_text("/Home/Search", {"searchstr": "original"})
        loop = MikanReadOnlyConnector(opener=RedirectingOpener(["/Home/Bangumi/42"]))
        with self.assertRaisesRegex(MikanRequestError, "redirect loop was blocked"):
            loop._get_text("/Home/Bangumi/42")

    def test_redirect_limit_is_blocked(self):
        opener = RedirectingOpener([
            "https://mikanani.me/Home/Bangumi/42",
            "https://mikanani.me:443/Home/Bangumi/42",
            "https://mikanime.tv:443/Home/Bangumi/42",
        ])
        connector = MikanReadOnlyConnector(opener=opener)
        with self.assertRaisesRegex(MikanRequestError, "redirect limit reached"):
            connector._get_text("/Home/Bangumi/42")


if __name__ == "__main__":
    unittest.main()
