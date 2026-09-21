"""Restricted AutoBangumi connector for subscription previews only.

This connector intentionally has no subscription method. Its sole non-read request
after authentication is `/api/v1/rss/analysis`, which parses an RSS feed without
creating an AutoBangumi subscription or download task.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .autobangumi import (
    AutoBangumiProtocolError,
    AutoBangumiReadOnlyConnector,
    ReadOnlyPolicyError,
)


_PREVIEW_ALLOWLIST = frozenset(
    {
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/status"),
        ("GET", "/api/v1/rss"),
        ("GET", "/api/v1/bangumi/get/all"),
        ("POST", "/api/v1/rss/analysis"),
    }
)


class AutoBangumiSubscriptionPreviewConnector(AutoBangumiReadOnlyConnector):
    """AutoBangumi client limited to analysis and the reads needed for preview."""

    @staticmethod
    def _assert_allowed(method: str, path: str) -> None:
        if (method.upper(), path) not in _PREVIEW_ALLOWLIST:
            raise ReadOnlyPolicyError(
                f"request blocked by preview policy: {method} {path}"
            )

    def analyze_rss(self, rss_url: str) -> Mapping[str, Any]:
        self._require_login()
        self.get_status()
        payload = json.dumps(
            {
                "name": "",
                "url": rss_url,
                "aggregate": False,
                "parser": "mikan",
                "enabled": True,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        result = self._request_json(
            "POST",
            "/api/v1/rss/analysis",
            body=payload,
            content_type="application/json; charset=utf-8",
        )
        if not isinstance(result, dict):
            raise AutoBangumiProtocolError("RSS analysis response must be an object")
        return result

