from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.get_ab_rss_records import GetAbRssRecordsTool
from tools.get_ab_status import GetAbStatusTool
from tools.list_ab_subscriptions import ListAbSubscriptionsTool
from tools.list_ab_bangumi_rules import ListAbBangumiRulesTool
from tools.search_mikan_rss import SearchMikanRssTool
from tools.search_mikan_anime import SearchMikanAnimeTool
from tools.list_mikan_rss_candidates import ListMikanRssCandidatesTool
from tools.inspect_mikan_rss_candidate import InspectMikanRssCandidateTool
from tools.prepare_ab_subscription import PrepareAbSubscriptionTool
from tools.subscribe_ab_rss import SubscribeAbRssTool


class DifyToolTests(unittest.TestCase):
    def tool(self, tool_type):
        return tool_type(SimpleNamespace(credentials={"not": "exposed"}), None)

    @patch("tools.get_ab_status.get_ab_status")
    def test_get_status_emits_a_json_message(self, get_status):
        get_status.return_value = {"status": True, "version": "3.2.8"}
        message = next(self.tool(GetAbStatusTool)._invoke({}))
        self.assertEqual(message.message.json_object, get_status.return_value)
        get_status.assert_called_once_with({"not": "exposed"})

    @patch("tools.list_ab_subscriptions.list_ab_subscriptions")
    def test_list_subscriptions_emits_a_json_message(self, list_subscriptions):
        list_subscriptions.return_value = [{"id": 4, "url": "[REDACTED]"}]
        message = next(self.tool(ListAbSubscriptionsTool)._invoke({}))
        self.assertEqual(message.message.json_object, list_subscriptions.return_value)
        list_subscriptions.assert_called_once_with({"not": "exposed"})

    @patch("tools.list_ab_bangumi_rules.list_ab_bangumi_rules")
    def test_list_bangumi_rules_passes_optional_filters(self, list_rules):
        list_rules.return_value = {"total_count": 1, "rules": []}
        message = next(self.tool(ListAbBangumiRulesTool)._invoke({"title": "Example", "season": 2, "limit": 10}))
        self.assertEqual(message.message.json_object, list_rules.return_value)
        list_rules.assert_called_once_with({"not": "exposed"}, "Example", 2, 10)

    @patch("tools.get_ab_rss_records.get_ab_rss_records")
    def test_get_rss_records_passes_only_rss_id(self, get_records):
        get_records.return_value = [{"rss_id": 4, "magnet_link": "[REDACTED]"}]
        message = next(self.tool(GetAbRssRecordsTool)._invoke({"rss_id": 4}))
        self.assertEqual(message.message.json_object, get_records.return_value)
        get_records.assert_called_once_with({"not": "exposed"}, 4)

    @patch("tools.search_mikan_rss.search_mikan_rss")
    def test_search_mikan_rss_passes_title_and_optional_group(self, search_mikan):
        search_mikan.return_value = {"candidate_count": 1, "candidates": []}
        message = next(
            self.tool(SearchMikanRssTool)._invoke(
                {"anime_title": "Sample Anime", "fansub_group": "Example Subs"}
            )
        )
        self.assertEqual(message.message.json_object, search_mikan.return_value)
        search_mikan.assert_called_once_with("Sample Anime", "Example Subs")

    @patch("tools.search_mikan_anime.search_mikan_anime")
    def test_search_mikan_anime_passes_title(self, search_anime):
        search_anime.return_value = {"candidate_total": 1, "candidates": []}
        message = next(self.tool(SearchMikanAnimeTool)._invoke({"title": "Sample Anime"}))
        self.assertEqual(message.message.json_object, search_anime.return_value)
        search_anime.assert_called_once_with("Sample Anime")

    @patch("tools.list_mikan_rss_candidates.list_mikan_rss_candidates")
    def test_list_mikan_rss_candidates_passes_filters(self, list_candidates):
        list_candidates.return_value = {"candidate_total": 1, "candidates": []}
        message = next(self.tool(ListMikanRssCandidatesTool)._invoke({"bangumi_id": 42, "resolution": "1080p", "limit": 3}))
        self.assertEqual(message.message.json_object, list_candidates.return_value)
        list_candidates.assert_called_once_with(42, None, None, "1080p", 3)

    @patch("tools.inspect_mikan_rss_candidate.inspect_mikan_rss_candidate")
    def test_inspect_mikan_rss_candidate_passes_only_public_url(self, inspect):
        inspect.return_value = {"status": "ok", "sample_items": []}
        rss_url = "https://mikanime.tv/RSS/Bangumi?bangumiId=42&subgroupid=200"
        message = next(
            self.tool(InspectMikanRssCandidateTool)._invoke({"rss_url": rss_url})
        )
        self.assertEqual(message.message.json_object, inspect.return_value)
        inspect.assert_called_once_with(rss_url)

    @patch("tools.prepare_ab_subscription.prepare_ab_subscription")
    def test_prepare_subscription_passes_only_expected_fields(self, prepare):
        prepare.return_value = {"status": "ready_for_confirmation", "preview_id": "abp1_test"}
        parameters = {
            "rss_url": "https://mikanime.tv/RSS/Bangumi?bangumiId=1&subgroupid=2",
            "expected_title": "Example",
            "expected_season": 2,
            "expected_year": 2024,
            "expected_group": "Group",
        }
        message = next(self.tool(PrepareAbSubscriptionTool)._invoke(parameters))
        self.assertEqual(message.message.json_object, prepare.return_value)
        prepare.assert_called_once_with(
            {"not": "exposed"},
            rss_url=parameters["rss_url"],
            expected_title="Example",
            expected_season=2,
            expected_group="Group",
            expected_year=2024,
        )

    @patch("tools.prepare_ab_subscription.prepare_ab_subscription")
    def test_prepare_joint_group_message_is_serializable_and_redacted(self, prepare):
        from anime_subscription_agent.subscription_service import _evaluate_preview

        rss_url = "https://mikanime.tv/RSS/Bangumi?bangumiId=2402&subgroupid=370"
        prepare.return_value = _evaluate_preview(
            rss_url,
            "悠哉日常大王",
            3,
            "LoliHouse",
            {
                "official_title": "悠哉日常大王 Nonstop",
                "season": 1,
                "group_name": "千夏字幕组&LoliHouse",
                "needs_review": False,
                "filter": "secret-filter",
                "rss_link": "https://private.invalid/rss?token=secret",
                "save_path": "D:/private",
            },
            [],
            [],
            "https://mikanime.tv",
        )
        message = next(
            self.tool(PrepareAbSubscriptionTool)._invoke(
                {
                    "rss_url": rss_url,
                    "expected_title": "悠哉日常大王",
                    "expected_season": 3,
                    "expected_group": "LoliHouse",
                }
            )
        )
        encoded = json.dumps(message.message.json_object, ensure_ascii=False)
        self.assertIn('"match_type": "joint"', encoded)
        self.assertNotIn("secret-filter", encoded)
        self.assertNotIn("private.invalid", encoded)
        self.assertNotIn("D:/private", encoded)

    @patch("tools.prepare_ab_subscription.prepare_ab_subscription")
    def test_prepare_subscription_returns_structured_stage_failure(self, prepare):
        from anime_subscription_agent.subscription_service import (
            SubscriptionPreparationError,
        )

        prepare.side_effect = SubscriptionPreparationError(
            "AutoBangumi analysis failed for this RSS candidate.",
            failure_stage="autobangumi_analysis",
            upstream_status_code=406,
            upstream_message="RSS parser rejected the request.",
        )
        message = next(
            self.tool(PrepareAbSubscriptionTool)._invoke(
                {
                    "rss_url": "https://mikanime.tv/RSS/Bangumi?bangumiId=1&subgroupid=2",
                    "expected_title": "Example",
                    "expected_season": 1,
                    "expected_group": "Group",
                }
            )
        )
        result = message.message.json_object
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_stage"], "autobangumi_analysis")
        self.assertEqual(result["upstream_status_code"], 406)
        self.assertFalse(result["write_attempted"])

    @patch("tools.subscribe_ab_rss.subscribe_ab_rss")
    def test_subscribe_rss_passes_only_confirmed_preview_fields(self, subscribe):
        subscribe.return_value = {"status": "created_verified"}
        parameters = {
            "rss_url": "https://mikanime.tv/RSS/Bangumi?bangumiId=1&subgroupid=2",
            "expected_title": "Example",
            "expected_season": 2,
            "expected_year": 2024,
            "expected_group": "Group",
            "preview_id": "abp1_0123456789abcdef0123456789abcdef",
        }
        message = next(self.tool(SubscribeAbRssTool)._invoke(parameters))
        self.assertEqual(message.message.json_object, subscribe.return_value)
        subscribe.assert_called_once_with(
            {"not": "exposed"},
            rss_url=parameters["rss_url"],
            expected_title="Example",
            expected_season=2,
            expected_group="Group",
            preview_id=parameters["preview_id"],
            expected_year=2024,
        )


if __name__ == "__main__":
    unittest.main()
