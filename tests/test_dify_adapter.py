from __future__ import annotations

import unittest
from unittest.mock import patch

from anime_subscription_agent.dify_adapter import (
    DifyAutoBangumiError,
    _value,
    get_ab_rss_records,
    get_ab_status,
    list_ab_bangumi_rules,
    list_ab_subscriptions,
)


class FakeConnector:
    def get_status(self):
        return {"status": True, "version": "3.2.8", "token": "never-return"}

    def list_rss(self):
        return [
            {
                "id": 4,
                "title": "Example",
                "url": "https://private.example/rss?user=me",
                "username": "private-user",
            }
        ]

    def get_rss_torrents(self, rss_id):
        return [{"rss_id": rss_id, "magnet_link": "magnet:?secret", "title": "E01"}]

    def list_bangumi_rules(self):
        return [
            {
                "id": 1, "official_title": "Example", "year": 2024, "title_raw": "Example Raw",
                "season": 1, "season_raw": "S1", "title_aliases": '["Example Alias", "Alias Two"]',
                "group_name": "Group A", "subtitle": "CHS", "source": "rss", "added": True,
                "archived": False, "deleted": False, "needs_review": False, "needs_review_reason": None,
                "episode_offset": 0, "season_offset": 0, "suggested_season_offset": None,
                "suggested_episode_offset": None, "rule_name": "rule", "save_path": "D:/downloads",
                "rss_link": "private", "poster_link": "private", "eps_collect": True, "filter": "private",
            },
            {
                "id": 2, "official_title": "Example", "title_raw": "Other", "title_aliases": '["Other Alias"]',
                "season": 2, "group_name": "Group B", "added": True, "archived": True,
                "deleted": True, "needs_review": True, "needs_review_reason": "check", "rule_name": "",
                "save_path": "D:/downloads",
            },
            {
                "id": 3, "official_title": "Different", "title_raw": "Alias Match", "title_aliases": '["Example Alias"]',
                "season": 1, "group_name": "Group C", "added": False, "archived": False,
                "deleted": False, "needs_review": False, "rule_name": "rule", "save_path": "",
            },
            {
                "id": 4, "official_title": "No Path", "title_raw": "No Path", "title_aliases": "[]",
                "season": 1, "group_name": "Group D", "added": True, "rule_name": "", "archived": False,
            },
        ]


class DifyAdapterTests(unittest.TestCase):
    credentials = {
        "ab_base_url": "http://127.0.0.1:7892",
        "ab_username": "not-returned",
        "ab_password": "not-returned",
        "ab_expected_version": "3.2.8",
    }

    def test_provider_credentials_take_precedence_over_dotenv_values(self):
        environment = {"AB_USERNAME": "dotenv-user"}
        self.assertEqual(
            _value({"ab_username": "provider-user"}, "ab_username", environment),
            "provider-user",
        )
        self.assertEqual(_value({}, "ab_username", environment), "dotenv-user")

    @patch("anime_subscription_agent.dify_adapter.connector_from_credentials")
    def test_get_status_redacts_sensitive_data(self, connector):
        connector.return_value = FakeConnector()
        result = get_ab_status(self.credentials)
        self.assertEqual(result["status"], True)
        self.assertEqual(result["version"], "3.2.8")
        self.assertEqual(result["token"], "[REDACTED]")

    @patch("anime_subscription_agent.dify_adapter.connector_from_credentials")
    def test_list_subscriptions_redacts_rss_url_and_username(self, connector):
        connector.return_value = FakeConnector()
        result = list_ab_subscriptions(self.credentials)
        self.assertEqual(result[0]["id"], 4)
        self.assertEqual(result[0]["url"], "[REDACTED]")
        self.assertEqual(result[0]["username"], "[REDACTED]")

    @patch("anime_subscription_agent.dify_adapter.connector_from_credentials")
    def test_get_rss_records_requires_integer_and_redacts_links(self, connector):
        connector.return_value = FakeConnector()
        result = get_ab_rss_records(self.credentials, 4.0)
        self.assertEqual(result[0]["rss_id"], 4)
        self.assertEqual(result[0]["magnet_link"], "[REDACTED]")
        with self.assertRaises(DifyAutoBangumiError):
            get_ab_rss_records(self.credentials, 4.5)
        with self.assertRaises(DifyAutoBangumiError):
            get_ab_rss_records(self.credentials, 0)

    @patch("anime_subscription_agent.dify_adapter.connector_from_credentials")
    def test_bangumi_rules_match_titles_seasons_and_hide_sensitive_fields(self, connector):
        connector.return_value = FakeConnector()
        result = list_ab_bangumi_rules(self.credentials, title="example alias", season=1, limit=1)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(result["returned_count"], 1)
        self.assertTrue(result["truncated"])
        rule = result["rules"][0]
        self.assertEqual(rule["title_aliases"], ["Example Alias", "Alias Two"])
        self.assertTrue(rule["rule_ready"])
        for field in ("rss_link", "save_path", "poster_link", "rule_name", "eps_collect", "filter"):
            self.assertNotIn(field, rule)
        all_rules = list_ab_bangumi_rules(self.credentials, title="Example", limit=20)
        self.assertEqual([rule["id"] for rule in all_rules["rules"]], [1, 2, 3])
        self.assertTrue(all_rules["rules"][1]["archived"])
        self.assertTrue(all_rules["rules"][1]["deleted"])
        self.assertTrue(all_rules["rules"][1]["needs_review"])
        self.assertTrue(all_rules["rules"][1]["rule_ready"])
        self.assertEqual([(rule["season"], rule["group_name"]) for rule in all_rules["rules"]], [(1, "Group A"), (2, "Group B"), (1, "Group C")])
        self.assertEqual(list_ab_bangumi_rules(self.credentials, title="Example Raw")["rules"][0]["id"], 1)
        self.assertEqual(list_ab_bangumi_rules(self.credentials, title="Alias Match")["rules"][0]["id"], 3)
        self.assertFalse(list_ab_bangumi_rules(self.credentials, title="Alias Match")["rules"][0]["rule_ready"])
        self.assertFalse(list_ab_bangumi_rules(self.credentials, title="No Path")["rules"][0]["rule_ready"])

    @patch("anime_subscription_agent.dify_adapter.connector_from_credentials")
    def test_bangumi_rules_reject_invalid_season_and_limit_before_network(self, connector):
        with self.assertRaises(DifyAutoBangumiError):
            list_ab_bangumi_rules(self.credentials, season=1.0)
        with self.assertRaises(DifyAutoBangumiError):
            list_ab_bangumi_rules(self.credentials, season=0)
        with self.assertRaises(DifyAutoBangumiError):
            list_ab_bangumi_rules(self.credentials, limit=51)
        connector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
