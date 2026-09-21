"""Minimal-permission AutoBangumi connector for one controlled subscription write."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .autobangumi import AutoBangumiError, ReadOnlyPolicyError
from .autobangumi_subscription import AutoBangumiSubscriptionPreviewConnector


class AutoBangumiWriteRejected(AutoBangumiError):
    """The server explicitly rejected the one subscription request."""


class AutoBangumiWriteOutcomeUncertain(AutoBangumiError):
    """The request may have run, so callers must only perform read-back checks."""


_WRITE_ALLOWLIST = frozenset(
    {
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/status"),
        ("GET", "/api/v1/rss"),
        ("GET", "/api/v1/bangumi/get/all"),
        ("POST", "/api/v1/rss/analysis"),
        ("POST", "/api/v1/rss/subscribe"),
    }
)


class AutoBangumiSubscriptionWriteConnector(
    AutoBangumiSubscriptionPreviewConnector
):
    """Connector that exposes exactly one non-retrying subscription operation."""

    @staticmethod
    def _assert_allowed(method: str, path: str) -> None:
        if (method.upper(), path) not in _WRITE_ALLOWLIST:
            raise ReadOnlyPolicyError(
                f"request blocked by controlled-write policy: {method} {path}"
            )

    def subscribe_rss_once(
        self, analysis: Mapping[str, Any], rss_url: str
    ) -> Any:
        """Send `/rss/subscribe` exactly once and never retry internally."""
        self._require_login()
        self._assert_allowed("POST", "/api/v1/rss/subscribe")
        payload = json.dumps(
            {
                "data": dict(analysis),
                "rss": {
                    "name": "",
                    "url": rss_url,
                    "aggregate": False,
                    "parser": "mikan",
                    "enabled": True,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self.config.base_url}/api/v1/rss/subscribe",
            data=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with self._opener.open(
                request, timeout=self.config.timeout_seconds
            ) as response:
                raw = response.read(self.config.max_response_bytes + 1)
        except HTTPError as exc:
            if 400 <= exc.code < 500:
                raise AutoBangumiWriteRejected(
                    f"AutoBangumi rejected the subscription request with HTTP {exc.code}"
                ) from exc
            raise AutoBangumiWriteOutcomeUncertain(
                "AutoBangumi subscription response was uncertain"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AutoBangumiWriteOutcomeUncertain(
                "AutoBangumi subscription response was uncertain"
            ) from exc
        if len(raw) > self.config.max_response_bytes:
            raise AutoBangumiWriteOutcomeUncertain(
                "AutoBangumi subscription response exceeded the size limit"
            )
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AutoBangumiWriteOutcomeUncertain(
                "AutoBangumi subscription response was invalid"
            ) from exc

