from __future__ import annotations

import unittest
from unittest.mock import patch

from anime_subscription_agent.mikan import MikanPolicyError, canonical_public_url
from anime_subscription_agent.mikan_adapter import (
    DifyMikanError,
    _redact_mikan_candidates,
    list_mikan_rss_candidates,
    inspect_mikan_rss_candidate,
    search_mikan_anime,
    search_mikan_rss,
)


class MikanAdapterTests(unittest.TestCase):
    @patch("anime_subscription_agent.mikan_adapter.MikanReadOnlyConnector")
    def test_inspector_preserves_safe_structured_result(self, connector_type):
        rss_url = "https://mikanime.tv/RSS/Bangumi?bangumiId=42&subgroupid=200"
        connector_type.return_value.inspect_rss_candidate.return_value = {
            "rss_url": rss_url,
            "normalized_rss_url": rss_url,
            "bangumi_id": 42,
            "subgroup_id": 200,
            "status": "ok",
            "reachable": True,
            "http_status": 200,
            "sample_items": [],
            "diagnostics": [],
        }
        result = inspect_mikan_rss_candidate(rss_url)
        self.assertEqual(result["status"], "ok")
        connector_type.return_value.inspect_rss_candidate.assert_called_once_with(
            rss_url
        )

    @patch("anime_subscription_agent.mikan_adapter.MikanReadOnlyConnector")
    def test_public_rss_urls_are_preserved_while_sensitive_fields_are_redacted(self, connector_type):
        connector_type.return_value.search_rss.return_value = [
            {
                "anime_title": "Sample Anime",
                "fansub_group": "Example Subs",
                "rss_url": "https://mikanani.me/RSS/Bangumi?bangumiId=42&subgroupid=200",
                "source_page": "https://mikanani.me/Home/Bangumi/42",
                "token": "must-not-leak",
                "cookie": "must-not-leak",
            }
        ]
        result = search_mikan_rss("Sample Anime")
        candidate = result["candidates"][0]
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(candidate["rss_url"], "https://mikanime.tv/RSS/Bangumi?bangumiId=42&subgroupid=200")
        self.assertEqual(candidate["source_page"], "https://mikanime.tv/Home/Bangumi/42")
        self.assertEqual(candidate["token"], "[REDACTED]")
        self.assertEqual(candidate["cookie"], "[REDACTED]")

    @patch("anime_subscription_agent.mikan_adapter.MikanReadOnlyConnector")
    def test_no_candidates_returns_a_clear_message(self, connector_type):
        connector_type.return_value.search_rss.return_value = []
        result = search_mikan_rss("Missing Anime", "No Group")
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("No public Mikan RSS candidates", result["message"])

    @patch("anime_subscription_agent.mikan_adapter._project_environment")
    @patch("anime_subscription_agent.mikan_adapter.MikanReadOnlyConnector")
    def test_configured_mirror_is_used_for_the_connector(self, connector_type, environment):
        environment.return_value = {"MIKAN_BASE_URL": "https://mikanime.tv"}
        connector_type.return_value.search_rss.return_value = []
        search_mikan_rss("Sample Anime")
        connector_type.assert_called_once_with(base_url="https://mikanime.tv")

    def test_mirror_urls_are_normalized_and_deduplicated(self):
        candidates = [
            {"rss_url": "https://mikanani.me/RSS/Bangumi?subgroupid=200&bangumiId=42", "source_page": "https://mikanani.me/Home/Bangumi/42"},
            {"rss_url": "https://mikanime.tv/RSS/Bangumi?bangumiId=42&subgroupid=200", "source_page": "https://mikanime.tv/Home/Bangumi/42"},
        ]
        result = _redact_mikan_candidates(candidates, "https://mikanime.tv")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["rss_url"], "https://mikanime.tv/RSS/Bangumi?bangumiId=42&subgroupid=200")

    def test_non_allowlisted_url_is_rejected(self):
        with self.assertRaises(MikanPolicyError):
            canonical_public_url("https://example.com/RSS/Bangumi?bangumiId=42&subgroupid=200", "https://mikanime.tv")
        with self.assertRaises(MikanPolicyError):
            canonical_public_url("https://mikanani.me/RSS/Bangumi?bangumiId=42&subgroupid=200", "https://example.com")

    @patch("anime_subscription_agent.mikan_adapter.MikanReadOnlyConnector")
    def test_two_stage_adapters_preserve_counts_and_filters(self, connector_type):
        connector = connector_type.return_value
        connector.search_anime.return_value = {
            "candidate_total": 2, "truncated": False,
            "candidates": [{"bangumi_id": 42, "anime_title": "One", "source_page": "https://mikanani.me/Home/Bangumi/42"}],
        }
        anime = search_mikan_anime("One")
        self.assertEqual(anime["candidate_total"], 2)
        connector.list_rss_candidates.return_value = {
            "anime_title": "One", "candidate_total": 8, "truncated": True,
            "candidates": [{"rss_url": "https://mikanani.me/RSS/Bangumi?bangumiId=42&subgroupid=200", "source_page": "https://mikanani.me/Home/Bangumi/42"}],
        }
        rss = list_mikan_rss_candidates(42, resolution="1080p", limit=3)
        self.assertEqual(rss["candidate_total"], 8)
        self.assertTrue(rss["truncated"])
        connector.list_rss_candidates.assert_called_once_with(42, fansub_group=None, subtitle_language=None, resolution="1080p", limit=3)

    def test_two_stage_adapter_rejects_invalid_bangumi_id(self):
        with self.assertRaises(DifyMikanError):
            list_mikan_rss_candidates("42")


if __name__ == "__main__":
    unittest.main()
