from __future__ import annotations

import json
import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from anime_subscription_agent.autobangumi import (
    AutoBangumiRequestError,
    ReadOnlyPolicyError,
)
from anime_subscription_agent.autobangumi_subscription import (
    AutoBangumiSubscriptionPreviewConnector,
)
from anime_subscription_agent.dify_adapter import DifyAutoBangumiError
from anime_subscription_agent.subscription_service import (
    SubscriptionPreparationError,
    prepare_ab_subscription,
)


class FakeResponse:
    status = 200

    def __init__(self, value):
        self.body = json.dumps(value).encode("utf-8")

    def read(self, amount=None):
        return self.body if amount is None else self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[Request] = []

    def open(self, request, timeout):
        self.requests.append(request)
        return FakeResponse(self.responses.pop(0))


class AnalysisErrorOpener(FakeOpener):
    def open(self, request, timeout):
        self.requests.append(request)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return FakeResponse(value)


class FakePreviewConnector:
    def __init__(self, analysis, rss=None, rules=None):
        self.analysis = analysis
        self.rss = rss or []
        self.rules = rules or []
        self.analysis_urls = []

    def analyze_rss(self, rss_url):
        self.analysis_urls.append(rss_url)
        return dict(self.analysis)

    def list_rss(self):
        return list(self.rss)

    def list_bangumi_rules(self):
        return list(self.rules)


class FailingAnalysisConnector(FakePreviewConnector):
    def __init__(self, error):
        super().__init__({})
        self.error = error

    def analyze_rss(self, rss_url):
        raise self.error


ANALYSIS = {
    "official_title": "碧蓝之海",
    "year": 2025,
    "title_raw": "碧蓝之海 S03",
    "title_aliases": '["碧蓝之海"]',
    "season": 3,
    "season_raw": "S03",
    "group_name": "LoliHouse",
    "dpi": "1080P",
    "subtitle": "简体",
    "source": "Mikan",
    "needs_review": False,
    "rss_link": "must-not-return",
    "poster_link": "must-not-return",
    "filter": "must-not-return",
    "save_path": "must-not-return",
}


class PreviewConnectorTests(unittest.TestCase):
    def test_only_analysis_and_required_reads_are_allowed(self):
        opener = FakeOpener(
            [
                {"access_token": "secret"},
                {"status": True, "version": "3.2.8"},
                ANALYSIS,
            ]
        )
        connector = AutoBangumiSubscriptionPreviewConnector(opener=opener)
        connector.login("user", "pass")
        result = connector.analyze_rss(
            "https://mikanime.tv/RSS/Bangumi?bangumiId=4014&subgroupid=370"
        )
        self.assertEqual(result["season"], 3)
        self.assertEqual(
            [(request.method, request.full_url.rsplit("/api", 1)[-1]) for request in opener.requests],
            [
                ("POST", "/v1/auth/login"),
                ("GET", "/v1/status"),
                ("POST", "/v1/rss/analysis"),
            ],
        )
        payload = json.loads(opener.requests[-1].data.decode("utf-8"))
        self.assertEqual(payload["parser"], "mikan")
        self.assertFalse(payload["aggregate"])
        self.assertTrue(payload["enabled"])

    def test_subscribe_and_all_other_mutations_are_blocked_before_network(self):
        connector = AutoBangumiSubscriptionPreviewConnector(opener=FakeOpener([]))
        for method, path in (
            ("POST", "/api/v1/rss/subscribe"),
            ("POST", "/api/v1/rss/add"),
            ("POST", "/api/v1/rss/collect"),
            ("GET", "/api/v1/rss/refresh/1"),
            ("POST", "/api/v1/bangumi/update"),
            ("DELETE", "/api/v1/bangumi/delete/1"),
        ):
            with self.subTest(method=method, path=path):
                with self.assertRaises(ReadOnlyPolicyError):
                    connector._request_json(method, path)
        self.assertEqual(connector._opener.requests, [])

    def test_analysis_http_error_keeps_safe_status_and_bounded_json_message(self):
        error = HTTPError(
            "http://127.0.0.1:7892/api/v1/rss/analysis",
            406,
            "Not Acceptable",
            Message(),
            BytesIO(b'{"detail":"RSS parser rejected this request."}'),
        )
        connector = AutoBangumiSubscriptionPreviewConnector(
            opener=AnalysisErrorOpener(
                [
                    {"access_token": "secret"},
                    {"status": True, "version": "3.2.8"},
                    error,
                ]
            )
        )
        connector.login("user", "pass")
        with self.assertRaises(AutoBangumiRequestError) as caught:
            connector.analyze_rss(
                "https://mikanime.tv/RSS/Bangumi?bangumiId=2402&subgroupid=415"
            )
        self.assertEqual(caught.exception.status_code, 406)
        self.assertEqual(
            caught.exception.upstream_message, "RSS parser rejected this request."
        )

    def test_analysis_http_error_discards_sensitive_or_html_body(self):
        for body in (
            b'{"message":"token=secret-value"}',
            b"<html><body>proxy error</body></html>",
        ):
            with self.subTest(body=body):
                error = HTTPError(
                    "http://127.0.0.1:7892/api/v1/rss/analysis",
                    406,
                    "Not Acceptable",
                    Message(),
                    BytesIO(body),
                )
                connector = AutoBangumiSubscriptionPreviewConnector(
                    opener=AnalysisErrorOpener(
                        [
                            {"access_token": "secret"},
                            {"status": True, "version": "3.2.8"},
                            error,
                        ]
                    )
                )
                connector.login("user", "pass")
                with self.assertRaises(AutoBangumiRequestError) as caught:
                    connector.analyze_rss(
                        "https://mikanime.tv/RSS/Bangumi?bangumiId=2402&subgroupid=415"
                    )
                self.assertIsNone(caught.exception.upstream_message)


class PreviewServiceTests(unittest.TestCase):
    credentials = {"ab_username": "hidden", "ab_password": "hidden"}
    rss_url = "https://mikanani.me/RSS/Bangumi?subgroupid=370&bangumiId=4014"

    def prepare(self, connector, **overrides):
        values = {
            "rss_url": self.rss_url,
            "expected_title": "碧蓝之海",
            "expected_season": 3,
            "expected_group": "LoliHouse",
        }
        values.update(overrides)
        with patch(
            "anime_subscription_agent.subscription_service._project_environment",
            return_value={"MIKAN_BASE_URL": "https://mikanime.tv"},
        ), patch(
            "anime_subscription_agent.subscription_service._preview_connector",
            return_value=connector,
        ):
            return prepare_ab_subscription(self.credentials, **values)

    def test_ready_preview_normalizes_url_redacts_analysis_and_is_deterministic(self):
        connector = FakePreviewConnector(ANALYSIS)
        first = self.prepare(connector)
        second = self.prepare(FakePreviewConnector(ANALYSIS))
        self.assertEqual(first["status"], "ready_for_confirmation")
        self.assertEqual(first["requested"]["rss_url"], "https://mikanime.tv/RSS/Bangumi?bangumiId=4014&subgroupid=370")
        self.assertEqual(first["preview_id"], second["preview_id"])
        self.assertTrue(first["preview_id"].startswith("abp1_"))
        self.assertEqual(
            first["group_match"],
            {
                "selected_group": "LoliHouse",
                "parsed_group": "LoliHouse",
                "effective_group": "LoliHouse",
                "parsed_members": ["LoliHouse"],
                "match_type": "exact",
                "confirmation_required": False,
            },
        )
        for field in ("rss_link", "poster_link", "filter", "save_path", "rule_name"):
            self.assertNotIn(field, first["parsed"])

    def test_joint_group_member_is_confirmable_with_structured_warning(self):
        analysis = dict(ANALYSIS, group_name="千夏字幕组&amp;LoliHouse")
        result = self.prepare(FakePreviewConnector(analysis))
        self.assertEqual(result["status"], "ready_for_confirmation")
        self.assertEqual(result["requested"]["selected_group"], "LoliHouse")
        self.assertEqual(
            result["target"]["effective_group"], "千夏字幕组&amp;LoliHouse"
        )
        self.assertEqual(result["target"]["group"], "千夏字幕组&amp;LoliHouse")
        self.assertEqual(
            result["group_match"],
            {
                "selected_group": "LoliHouse",
                "parsed_group": "千夏字幕组&amp;LoliHouse",
                "effective_group": "千夏字幕组&amp;LoliHouse",
                "parsed_members": ["千夏字幕组", "LoliHouse"],
                "match_type": "joint",
                "confirmation_required": True,
            },
        )
        self.assertIn(
            "group_expansion", {warning["code"] for warning in result["warnings"]}
        )

    def test_joint_group_matches_first_member_and_nfkc_separators(self):
        for parsed_group in (
            "千夏字幕组＆ＬｏｌｉＨｏｕｓｅ",
            "千夏字幕组＋LoliHouse",
            "千夏字幕组×LoliHouse",
            "千夏字幕组/LoliHouse",
        ):
            with self.subTest(parsed_group=parsed_group):
                expected = "千夏字幕组" if "＆" in parsed_group else "LoliHouse"
                result = self.prepare(
                    FakePreviewConnector(dict(ANALYSIS, group_name=parsed_group)),
                    expected_group=expected,
                )
                self.assertEqual(result["status"], "ready_for_confirmation")
                self.assertEqual(result["group_match"]["match_type"], "joint")

    def test_group_matching_never_uses_substrings(self):
        for selected, parsed in (("ani", "ANi-One"), ("Loli", "LoliHouse")):
            with self.subTest(selected=selected, parsed=parsed):
                result = self.prepare(
                    FakePreviewConnector(dict(ANALYSIS, group_name=parsed)),
                    expected_group=selected,
                )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["group_match"]["match_type"], "mismatch")
                self.assertIn(
                    "group_mismatch",
                    {warning["code"] for warning in result["warnings"]},
                )

    def test_title_adjustment_is_confirmable(self):
        result = self.prepare(
            FakePreviewConnector(ANALYSIS), expected_title="碧蓝之海：最终标题"
        )
        self.assertEqual(result["status"], "ready_for_confirmation")
        self.assertEqual(result["parsed"]["official_title"], "碧蓝之海")
        self.assertEqual(result["target"]["title"], "碧蓝之海：最终标题")
        self.assertEqual(
            result["metadata_adjustment"]["fields"],
            [
                {
                    "field": "official_title",
                    "parsed": "碧蓝之海",
                    "target": "碧蓝之海：最终标题",
                }
            ],
        )

    def test_season_adjustment_is_confirmable(self):
        result = self.prepare(FakePreviewConnector(ANALYSIS), expected_season=1)
        self.assertEqual(result["status"], "ready_for_confirmation")
        self.assertEqual(result["parsed"]["season"], 3)
        self.assertEqual(result["target"]["season"], 1)
        self.assertEqual(
            {item["field"] for item in result["metadata_adjustment"]["fields"]},
            {"season"},
        )

    def test_title_season_and_year_adjustments_are_confirmable(self):
        result = self.prepare(
            FakePreviewConnector(ANALYSIS),
            expected_title="碧蓝之海 Series",
            expected_season=2,
            expected_year=2018,
        )
        self.assertEqual(result["status"], "ready_for_confirmation")
        self.assertEqual(
            result["target"],
            {
                "rss_url": "https://mikanime.tv/RSS/Bangumi?bangumiId=4014&subgroupid=370",
                "title": "碧蓝之海 Series",
                "season": 2,
                "year": 2018,
                "group": "LoliHouse",
                "selected_group": "LoliHouse",
                "effective_group": "LoliHouse",
            },
        )
        self.assertEqual(
            {item["field"] for item in result["metadata_adjustment"]["fields"]},
            {"official_title", "season", "year"},
        )
        self.assertIn(
            "metadata_adjustment", {warning["code"] for warning in result["warnings"]}
        )

    def test_group_mismatch_and_review_still_block_preview(self):
        analysis = dict(ANALYSIS, group_name="Other", needs_review=True)
        result = self.prepare(FakePreviewConnector(analysis))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            {warning["code"] for warning in result["warnings"]},
            {"group_mismatch", "analysis_needs_review"},
        )

    def test_missing_analysis_identity_fields_block_preview(self):
        analysis = dict(ANALYSIS)
        analysis.pop("official_title")
        result = self.prepare(FakePreviewConnector(analysis))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("analysis_missing_fields", {warning["code"] for warning in result["warnings"]})

    def test_existing_rss_or_same_group_is_a_blocking_duplicate(self):
        connector = FakePreviewConnector(
            ANALYSIS,
            rss=[{"url": self.rss_url}],
            rules=[{"official_title": "碧蓝之海", "season": 3, "group_name": "LoliHouse"}],
        )
        result = self.prepare(connector)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["duplicate"],
            {
                "rss_url_exists": True,
                "same_title_season_exists": True,
                "same_title_season_group_exists": True,
            },
        )

    def test_same_title_and_season_with_different_group_is_a_conflict(self):
        connector = FakePreviewConnector(
            ANALYSIS,
            rules=[{"official_title": "碧蓝之海", "season": 3, "group_name": "ANi"}],
        )
        result = self.prepare(connector)
        self.assertEqual(result["status"], "conflict_requires_confirmation")
        self.assertTrue(result["duplicate"]["same_title_season_exists"])
        self.assertFalse(result["duplicate"]["same_title_season_group_exists"])

    def test_joint_duplicate_uses_effective_group_and_output_is_safe_json(self):
        joint = "千夏字幕组&LoliHouse"
        analysis = dict(
            ANALYSIS,
            group_name=joint,
            filter="secret-filter",
            rss_link="https://private.invalid/rss?token=secret",
        )
        connector = FakePreviewConnector(
            analysis,
            rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": joint,
                    "save_path": "D:/private",
                }
            ],
        )
        result = self.prepare(connector)
        self.assertTrue(result["duplicate"]["same_title_season_group_exists"])
        self.assertEqual(result["status"], "blocked")
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("secret-filter", encoded)
        self.assertNotIn("private.invalid", encoded)
        self.assertNotIn("D:/private", encoded)

    def test_invalid_url_and_season_are_rejected_before_connector_creation(self):
        with patch("anime_subscription_agent.subscription_service._preview_connector") as connector:
            with self.assertRaises(DifyAutoBangumiError):
                prepare_ab_subscription(self.credentials, "https://example.com/RSS/Bangumi?bangumiId=1&subgroupid=2", "Title", 1, "Group")
            with self.assertRaises(DifyAutoBangumiError):
                prepare_ab_subscription(self.credentials, "https://mikanime.tv:444/RSS/Bangumi?bangumiId=1&subgroupid=2", "Title", 1, "Group")
            with self.assertRaises(DifyAutoBangumiError):
                prepare_ab_subscription(self.credentials, self.rss_url, "Title", 0, "Group")
            with self.assertRaises(DifyAutoBangumiError):
                prepare_ab_subscription(
                    self.credentials, self.rss_url, "Title", 1, "Group", 99
                )
            connector.assert_not_called()

    def test_analysis_406_is_labeled_before_any_readback(self):
        error = AutoBangumiRequestError(
            "AutoBangumi returned HTTP 406",
            status_code=406,
            upstream_message="RSS parser rejected this request.",
        )
        connector = FailingAnalysisConnector(error)
        with self.assertRaises(SubscriptionPreparationError) as caught:
            self.prepare(connector)
        self.assertEqual(caught.exception.failure_stage, "autobangumi_analysis")
        self.assertEqual(caught.exception.upstream_status_code, 406)
        self.assertEqual(
            caught.exception.upstream_message, "RSS parser rejected this request."
        )
        self.assertEqual(connector.analysis_urls, [])


if __name__ == "__main__":
    unittest.main()
