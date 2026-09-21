from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.request import Request

from anime_subscription_agent.autobangumi import ReadOnlyPolicyError
from anime_subscription_agent.autobangumi_writer import (
    AutoBangumiSubscriptionWriteConnector,
    AutoBangumiWriteOutcomeUncertain,
    AutoBangumiWriteRejected,
)
from anime_subscription_agent.dify_adapter import DifyAutoBangumiError
from anime_subscription_agent.subscription_service import (
    _evaluate_preview,
    subscribe_ab_rss,
)


BASE_URL = "https://mikanime.tv"
RSS_URL = "https://mikanime.tv/RSS/Bangumi?bangumiId=4014&subgroupid=370"
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
    "filter": "server-generated-filter",
    "rss_link": RSS_URL,
    "poster_link": "https://example.invalid/poster.jpg",
}


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


class FakeWriteConnector:
    def __init__(
        self,
        *,
        analysis=None,
        pre_rss=None,
        pre_rules=None,
        post_rss=None,
        post_rules=None,
        write_error=None,
        readback_error=None,
    ):
        self.analysis = dict(analysis or ANALYSIS)
        self.rss_results = [list(pre_rss or []), list(post_rss or [])]
        self.rule_results = [list(pre_rules or []), list(post_rules or [])]
        self.write_error = write_error
        self.readback_error = readback_error
        self.subscribe_calls = []
        self.rss_calls = 0
        self.rule_calls = 0

    def analyze_rss(self, rss_url):
        return dict(self.analysis)

    def list_rss(self):
        if self.readback_error and self.rss_calls > 0:
            raise self.readback_error
        result = self.rss_results[min(self.rss_calls, len(self.rss_results) - 1)]
        self.rss_calls += 1
        return result

    def list_bangumi_rules(self):
        if self.readback_error and self.rule_calls > 0:
            raise self.readback_error
        result = self.rule_results[min(self.rule_calls, len(self.rule_results) - 1)]
        self.rule_calls += 1
        return result

    def subscribe_rss_once(self, analysis, rss_url):
        self.subscribe_calls.append((dict(analysis), rss_url))
        if self.write_error:
            raise self.write_error
        return {"result": "accepted"}


def preview_id(
    analysis=None,
    rss=None,
    rules=None,
    *,
    title="碧蓝之海",
    season=3,
    group="LoliHouse",
    year=None,
):
    return _evaluate_preview(
        RSS_URL,
        title,
        season,
        group,
        analysis or ANALYSIS,
        rss or [],
        rules or [],
        BASE_URL,
        year,
    )["preview_id"]


class WriteConnectorTests(unittest.TestCase):
    def test_subscribe_payload_uses_analysis_and_fixed_rss_settings_once(self):
        opener = FakeOpener([{"access_token": "secret"}, {"accepted": True}])
        connector = AutoBangumiSubscriptionWriteConnector(opener=opener)
        connector.login("user", "pass")
        connector.subscribe_rss_once(ANALYSIS, RSS_URL)
        write_requests = [
            request
            for request in opener.requests
            if request.full_url.endswith("/api/v1/rss/subscribe")
        ]
        self.assertEqual(len(write_requests), 1)
        payload = json.loads(write_requests[0].data.decode("utf-8"))
        self.assertEqual(payload["data"], ANALYSIS)
        self.assertEqual(
            payload["rss"],
            {
                "name": "",
                "url": RSS_URL,
                "aggregate": False,
                "parser": "mikan",
                "enabled": True,
            },
        )

    def test_all_adjacent_write_paths_are_blocked_before_network(self):
        opener = FakeOpener([])
        connector = AutoBangumiSubscriptionWriteConnector(opener=opener)
        for method, path in (
            ("POST", "/api/v1/rss/add"),
            ("POST", "/api/v1/rss/collect"),
            ("GET", "/api/v1/rss/refresh/1"),
            ("POST", "/api/v1/rss/update"),
            ("DELETE", "/api/v1/rss/delete/1"),
            ("POST", "/api/v1/bangumi/update"),
            ("DELETE", "/api/v1/bangumi/delete/1"),
        ):
            with self.subTest(method=method, path=path):
                with self.assertRaises(ReadOnlyPolicyError):
                    connector._request_json(method, path)
        self.assertEqual(opener.requests, [])


class SubscriptionServiceTests(unittest.TestCase):
    credentials = {"ab_username": "hidden", "ab_password": "hidden"}

    def subscribe(self, connector, *, confirmed_preview_id=None, **overrides):
        values = {
            "rss_url": RSS_URL,
            "expected_title": "碧蓝之海",
            "expected_season": 3,
            "expected_group": "LoliHouse",
            "preview_id": confirmed_preview_id or preview_id(),
        }
        values.update(overrides)
        with patch(
            "anime_subscription_agent.subscription_service._project_environment",
            return_value={"MIKAN_BASE_URL": BASE_URL},
        ), patch(
            "anime_subscription_agent.subscription_service._write_connector",
            return_value=connector,
        ):
            return subscribe_ab_rss(self.credentials, **values)

    def test_success_requires_rss_and_rule_readback(self):
        connector = FakeWriteConnector(
            post_rss=[{"url": RSS_URL}],
            post_rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": "LoliHouse",
                }
            ],
        )
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "created_verified")
        self.assertTrue(result["write_attempted"])
        self.assertEqual(len(connector.subscribe_calls), 1)
        self.assertEqual(connector.subscribe_calls[0][0], ANALYSIS)
        for field in ("filter", "rss_link", "poster_link", "save_path", "token"):
            self.assertNotIn(field, result["parsed"])

    def test_preview_fingerprint_or_analysis_change_blocks_write(self):
        changed = dict(ANALYSIS, season=2)
        connector = FakeWriteConnector(analysis=changed)
        result = self.subscribe(connector, confirmed_preview_id=preview_id())
        self.assertEqual(result["status"], "analysis_mismatch")
        self.assertFalse(result["write_attempted"])
        self.assertEqual(connector.subscribe_calls, [])

        hidden_change = dict(ANALYSIS, filter="changed-server-filter")
        hidden_connector = FakeWriteConnector(analysis=hidden_change)
        hidden_result = self.subscribe(
            hidden_connector, confirmed_preview_id=preview_id()
        )
        self.assertEqual(hidden_result["status"], "analysis_mismatch")
        self.assertEqual(hidden_connector.subscribe_calls, [])

    def test_preview_id_binds_all_target_metadata(self):
        confirmed = preview_id()
        cases = (
            {"expected_title": "Different"},
            {"expected_season": 2},
            {"expected_year": 2024},
            {"expected_group": "Other"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                connector = FakeWriteConnector()
                result = self.subscribe(
                    connector, confirmed_preview_id=confirmed, **overrides
                )
                self.assertEqual(result["status"], "analysis_mismatch")
                self.assertFalse(result["write_attempted"])
                self.assertEqual(connector.subscribe_calls, [])

    def test_preview_id_binds_joint_members_order_and_match_type(self):
        joint = dict(ANALYSIS, group_name="千夏字幕组&LoliHouse")
        confirmed = preview_id(analysis=joint)
        variants = (
            dict(ANALYSIS, group_name="LoliHouse&千夏字幕组"),
            dict(ANALYSIS, group_name="别组&LoliHouse"),
            dict(ANALYSIS, group_name="LoliHouse"),
        )
        for changed in variants:
            with self.subTest(group_name=changed["group_name"]):
                connector = FakeWriteConnector(analysis=changed)
                result = self.subscribe(
                    connector, confirmed_preview_id=confirmed
                )
                self.assertEqual(result["status"], "analysis_mismatch")
                self.assertFalse(result["write_attempted"])
                self.assertEqual(connector.subscribe_calls, [])

    def test_joint_write_preserves_effective_group_and_verifies_readback(self):
        joint_name = "千夏字幕组&LoliHouse"
        joint_analysis = dict(ANALYSIS, group_name=joint_name)
        connector = FakeWriteConnector(
            analysis=joint_analysis,
            post_rss=[{"url": RSS_URL}],
            post_rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": joint_name,
                }
            ],
        )
        result = self.subscribe(
            connector,
            confirmed_preview_id=preview_id(analysis=joint_analysis),
        )
        self.assertEqual(result["status"], "created_verified")
        self.assertEqual(result["verification"]["rule_exists"], True)
        self.assertEqual(result["group_match"]["match_type"], "joint")
        self.assertEqual(connector.subscribe_calls[0][0]["group_name"], joint_name)

    def test_joint_duplicate_uses_effective_group_not_selected_member(self):
        joint_name = "千夏字幕组&LoliHouse"
        joint_analysis = dict(ANALYSIS, group_name=joint_name)
        confirmed = preview_id(analysis=joint_analysis)
        existing_joint = FakeWriteConnector(
            analysis=joint_analysis,
            pre_rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": joint_name,
                }
            ],
        )
        result = self.subscribe(
            existing_joint, confirmed_preview_id=confirmed
        )
        self.assertEqual(result["status"], "already_exists_noop")
        self.assertEqual(existing_joint.subscribe_calls, [])

        selected_member_only = FakeWriteConnector(
            analysis=joint_analysis,
            pre_rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": "LoliHouse",
                }
            ],
            post_rss=[{"url": RSS_URL}],
            post_rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": joint_name,
                }
            ],
        )
        written = self.subscribe(
            selected_member_only, confirmed_preview_id=confirmed
        )
        self.assertEqual(written["status"], "created_verified")
        self.assertEqual(len(selected_member_only.subscribe_calls), 1)

    def test_target_metadata_is_written_while_source_fields_are_preserved(self):
        source_analysis = dict(
            ANALYSIS,
            official_title="悠哉日常大王 Nonstop",
            title_raw="悠哉日常大王 Nonstop",
            title_aliases='["Non Non Biyori Nonstop"]',
            season=1,
            season_raw="",
            year=2021,
            group_name="桜都字幕组",
            dpi="1080p@60FPS",
            subtitle="繁日内嵌",
        )
        target = {
            "expected_title": "悠哉日常大王",
            "expected_season": 3,
            "expected_year": 2013,
            "expected_group": "桜都字幕组",
        }
        connector = FakeWriteConnector(
            analysis=source_analysis,
            post_rss=[{"url": RSS_URL}],
            post_rules=[
                {
                    "official_title": "悠哉日常大王",
                    "season": 3,
                    "year": 2013,
                    "group_name": "桜都字幕组",
                }
            ],
        )
        confirmed = preview_id(
            analysis=source_analysis,
            title=target["expected_title"],
            season=target["expected_season"],
            year=target["expected_year"],
            group=target["expected_group"],
        )
        result = self.subscribe(
            connector, confirmed_preview_id=confirmed, **target
        )
        self.assertEqual(result["status"], "created_verified")
        self.assertEqual(result["parsed"]["official_title"], "悠哉日常大王 Nonstop")
        self.assertEqual(result["target"]["title"], "悠哉日常大王")
        payload = connector.subscribe_calls[0][0]
        self.assertEqual(payload["official_title"], "悠哉日常大王")
        self.assertEqual(payload["season"], 3)
        self.assertEqual(payload["year"], 2013)
        for field in (
            "title_raw",
            "title_aliases",
            "season_raw",
            "group_name",
            "rss_link",
            "filter",
            "subtitle",
            "dpi",
            "source",
        ):
            self.assertEqual(payload[field], source_analysis[field])

    def test_duplicate_state_returns_noop_without_write(self):
        connector = FakeWriteConnector(pre_rss=[{"url": RSS_URL}])
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "already_exists_noop")
        self.assertFalse(result["write_attempted"])
        self.assertEqual(connector.subscribe_calls, [])

        rule_connector = FakeWriteConnector(
            pre_rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": "LoliHouse",
                }
            ]
        )
        rule_result = self.subscribe(rule_connector)
        self.assertEqual(rule_result["status"], "already_exists_noop")
        self.assertEqual(rule_connector.subscribe_calls, [])

    def test_duplicate_and_readback_use_final_target_metadata(self):
        source_analysis = dict(
            ANALYSIS,
            official_title="悠哉日常大王 Nonstop",
            season=1,
            year=2021,
            group_name="桜都字幕组",
        )
        target = {
            "expected_title": "悠哉日常大王",
            "expected_season": 3,
            "expected_year": 2013,
            "expected_group": "桜都字幕组",
        }
        confirmed = preview_id(
            analysis=source_analysis,
            title=target["expected_title"],
            season=target["expected_season"],
            year=target["expected_year"],
            group=target["expected_group"],
        )
        existing_target = FakeWriteConnector(
            analysis=source_analysis,
            pre_rules=[
                {
                    "official_title": "悠哉日常大王",
                    "season": 3,
                    "group_name": "桜都字幕组",
                }
            ],
        )
        result = self.subscribe(
            existing_target, confirmed_preview_id=confirmed, **target
        )
        self.assertEqual(result["status"], "already_exists_noop")
        self.assertEqual(existing_target.subscribe_calls, [])

        source_only = FakeWriteConnector(
            analysis=source_analysis,
            pre_rules=[
                {
                    "official_title": "悠哉日常大王 Nonstop",
                    "season": 1,
                    "group_name": "桜都字幕组",
                }
            ],
            post_rss=[{"url": RSS_URL}],
            post_rules=[
                {
                    "official_title": "悠哉日常大王",
                    "season": 3,
                    "group_name": "桜都字幕组",
                }
            ],
        )
        written = self.subscribe(
            source_only, confirmed_preview_id=confirmed, **target
        )
        self.assertEqual(written["status"], "created_verified")
        self.assertEqual(len(source_only.subscribe_calls), 1)

    def test_http_rejection_without_state_is_write_rejected(self):
        connector = FakeWriteConnector(
            write_error=AutoBangumiWriteRejected("HTTP 406")
        )
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "write_rejected")
        self.assertEqual(len(connector.subscribe_calls), 1)

    def test_timeout_with_successful_readback_is_created_verified(self):
        connector = FakeWriteConnector(
            write_error=AutoBangumiWriteOutcomeUncertain("timeout"),
            post_rss=[{"url": RSS_URL}],
            post_rules=[
                {
                    "official_title": "碧蓝之海",
                    "season": 3,
                    "group_name": "LoliHouse",
                }
            ],
        )
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "created_verified")
        self.assertEqual(len(connector.subscribe_calls), 1)

    def test_one_readback_object_is_partial_state(self):
        connector = FakeWriteConnector(post_rss=[{"url": RSS_URL}])
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "partial_state")
        self.assertEqual(
            result["verification"], {"rss_exists": True, "rule_exists": False}
        )

    def test_uncertain_write_without_state_is_unknown_and_not_retried(self):
        connector = FakeWriteConnector(
            write_error=AutoBangumiWriteOutcomeUncertain("timeout")
        )
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertEqual(len(connector.subscribe_calls), 1)

    def test_http_success_without_readback_state_is_still_unknown(self):
        connector = FakeWriteConnector()
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertEqual(len(connector.subscribe_calls), 1)

    def test_unavailable_readback_is_unknown_and_not_retried(self):
        from anime_subscription_agent.autobangumi import AutoBangumiRequestError

        connector = FakeWriteConnector(
            readback_error=AutoBangumiRequestError("unavailable")
        )
        result = self.subscribe(connector)
        self.assertEqual(result["status"], "unknown_outcome")
        self.assertEqual(len(connector.subscribe_calls), 1)

    def test_invalid_preview_id_is_rejected_before_connector_creation(self):
        with patch(
            "anime_subscription_agent.subscription_service._write_connector"
        ) as connector:
            with self.assertRaises(DifyAutoBangumiError):
                subscribe_ab_rss(
                    self.credentials,
                    RSS_URL,
                    "碧蓝之海",
                    3,
                    "LoliHouse",
                    "invalid",
                )
            connector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
