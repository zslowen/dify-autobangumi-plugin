from __future__ import annotations

import json
import unittest
from urllib.request import Request

from anime_subscription_agent.autobangumi import (
    AutoBangumiAuthenticationError,
    AutoBangumiReadOnlyConnector,
    AutoBangumiVersionMismatch,
    ReadOnlyPolicyError,
)


class FakeResponse:
    status = 200

    def __init__(self, value):
        self._body = json.dumps(value).encode()

    def read(self, amount=None):
        return self._body if amount is None else self._body[:amount]

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


class ConnectorTests(unittest.TestCase):
    def connector(self, responses):
        opener = FakeOpener(responses)
        connector = AutoBangumiReadOnlyConnector(opener=opener)
        return connector, opener

    def test_login_status_and_rss_reads_use_only_expected_paths(self):
        connector, opener = self.connector(
            [
                {"access_token": "secret", "token_type": "bearer"},
                {"status": True, "version": "3.2.8", "first_run": False},
                [{"id": 7, "url": "private"}],
                [{"id": 9, "title": "episode"}],
            ]
        )
        connector.login("user", "pass")
        self.assertTrue(connector.get_status()["status"])
        self.assertEqual(connector.list_rss()[0]["id"], 7)
        self.assertEqual(connector.get_rss_torrents(7)[0]["id"], 9)
        self.assertEqual(
            [(r.method, r.full_url) for r in opener.requests],
            [
                ("POST", "http://127.0.0.1:7892/api/v1/auth/login"),
                ("GET", "http://127.0.0.1:7892/api/v1/status"),
                ("GET", "http://127.0.0.1:7892/api/v1/rss"),
                ("GET", "http://127.0.0.1:7892/api/v1/rss/torrent/7"),
            ],
        )

    def test_reads_require_login_without_sending_a_request(self):
        connector, opener = self.connector([])
        with self.assertRaises(AutoBangumiAuthenticationError):
            connector.list_rss()
        self.assertEqual(opener.requests, [])

    def test_state_changing_get_is_blocked_before_network(self):
        connector, opener = self.connector([])
        with self.assertRaises(ReadOnlyPolicyError):
            connector._request_json("GET", "/api/v1/start")
        self.assertEqual(opener.requests, [])

    def test_all_non_login_posts_are_blocked_before_network(self):
        connector, opener = self.connector([])
        with self.assertRaises(ReadOnlyPolicyError):
            connector._request_json("POST", "/api/v1/rss/add", body=b"{}")
        self.assertEqual(opener.requests, [])

    def test_version_mismatch_fails_closed(self):
        connector, _ = self.connector(
            [
                {"access_token": "secret"},
                {"status": True, "version": "3.3.0", "first_run": False},
            ]
        )
        connector.login("user", "pass")
        with self.assertRaises(AutoBangumiVersionMismatch):
            connector.get_status()

    def test_invalid_rss_id_is_blocked_before_network(self):
        connector, opener = self.connector([{"access_token": "secret"}])
        connector.login("user", "pass")
        with self.assertRaises(ValueError):
            connector.get_rss_torrents(0)
        self.assertEqual(len(opener.requests), 1)

    def test_bangumi_rule_reads_are_allowlisted_and_writes_are_blocked(self):
        connector, opener = self.connector(
            [
                {"access_token": "secret"},
                {"status": True, "version": "3.2.8"},
                [{"id": 7, "official_title": "Example"}],
            ]
        )
        connector.login("user", "pass")
        self.assertEqual(connector.list_bangumi_rules()[0]["id"], 7)
        self.assertEqual(opener.requests[-1].full_url, "http://127.0.0.1:7892/api/v1/bangumi/get/all")
        for path in ("/api/v1/bangumi/update/7", "/api/v1/bangumi/delete/7", "/api/v1/bangumi/disable/7", "/api/v1/bangumi/enable/7", "/api/v1/bangumi/archive/7", "/api/v1/bangumi/reset/7", "/api/v1/bangumi/refresh/7"):
            with self.subTest(path=path):
                with self.assertRaises(ReadOnlyPolicyError):
                    connector._request_json("GET", path)
        self.assertEqual(len(opener.requests), 3)


if __name__ == "__main__":
    unittest.main()
