"""Strictly read-only AutoBangumi 3.2.8 connector.

The sole write-shaped operation is authentication. All other allowed requests are
explicitly enumerated reads. AutoBangumi has state-changing GET endpoints, so a
method-only policy is insufficient.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)


class AutoBangumiError(RuntimeError):
    """Base connector error."""


class ReadOnlyPolicyError(AutoBangumiError):
    """Raised before a request that is outside the read-only allowlist."""


class AutoBangumiAuthenticationError(AutoBangumiError):
    """Authentication was rejected or returned an invalid response."""


class AutoBangumiRequestError(AutoBangumiError):
    """The AutoBangumi request failed."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        upstream_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstream_message = upstream_message


class AutoBangumiProtocolError(AutoBangumiError):
    """The server response did not match the expected API contract."""


class AutoBangumiVersionMismatch(AutoBangumiError):
    """The connected server is not the configured AutoBangumi version."""


class ResponseLike(Protocol):
    status: int

    def read(self, amount: int | None = None) -> bytes: ...

    def __enter__(self) -> "ResponseLike": ...

    def __exit__(self, *args: object) -> None: ...


class OpenerLike(Protocol):
    def open(self, request: Request, timeout: float) -> ResponseLike: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_TORRENT_PATH = re.compile(r"/api/v1/rss/torrent/[1-9][0-9]*\Z")
_BANGUMI_DETAIL_PATH = re.compile(r"/api/v1/bangumi/get/[1-9][0-9]*\Z")
_STATIC_ALLOWLIST = frozenset(
    {
        ("POST", "/api/v1/auth/login"),
        ("GET", "/api/v1/status"),
        ("GET", "/api/v1/rss"),
        ("GET", "/api/v1/bangumi/get/all"),
    }
)
_MAX_SAFE_ERROR_BYTES = 2048
_MAX_SAFE_ERROR_TEXT = 300
_SENSITIVE_ERROR_TERMS = (
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
    "username",
)


def _safe_http_error_message(error: HTTPError) -> str | None:
    """Return one bounded JSON error string without headers, HTML, URLs, or secrets."""
    try:
        raw = error.read(_MAX_SAFE_ERROR_BYTES + 1)
    except (OSError, ValueError):
        return None
    if not raw or len(raw) > _MAX_SAFE_ERROR_BYTES:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = next(
        (
            payload.get(key)
            for key in ("detail", "message", "error")
            if isinstance(payload.get(key), str)
        ),
        None,
    )
    if value is None:
        return None
    value = " ".join(value.split())
    folded = value.casefold()
    if (
        not value
        or len(value) > _MAX_SAFE_ERROR_TEXT
        or "<" in value
        or ">" in value
        or "http://" in folded
        or "https://" in folded
        or any(term in folded for term in _SENSITIVE_ERROR_TERMS)
    ):
        return None
    return value


@dataclass(frozen=True, slots=True)
class ConnectorConfig:
    base_url: str = "http://127.0.0.1:7892"
    expected_version: str = "3.2.8"
    timeout_seconds: float = 10.0
    max_response_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("base_url must contain a host and no credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a path, query, or fragment")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")


class AutoBangumiReadOnlyConnector:
    """Authenticated client whose network surface is limited by an allowlist."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7892",
        *,
        expected_version: str = "3.2.8",
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 10 * 1024 * 1024,
        opener: OpenerLike | None = None,
    ) -> None:
        self.config = ConnectorConfig(
            base_url=base_url.rstrip("/"),
            expected_version=expected_version,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        self._cookies = CookieJar()
        self._opener = opener or build_opener(
            ProxyHandler({}), HTTPCookieProcessor(self._cookies), _NoRedirect()
        )
        self._authenticated = False

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def login(self, username: str, password: str) -> None:
        if not username or not password:
            raise ValueError("username and password are required")
        payload = urlencode({"username": username, "password": password}).encode()
        result = self._request_json(
            "POST",
            "/api/v1/auth/login",
            body=payload,
            content_type="application/x-www-form-urlencoded",
        )
        if not isinstance(result, dict) or not isinstance(
            result.get("access_token"), str
        ):
            raise AutoBangumiAuthenticationError("login was rejected")
        self._authenticated = True

    def get_status(self) -> Mapping[str, Any]:
        self._require_login()
        result = self._request_json("GET", "/api/v1/status")
        if not isinstance(result, dict):
            raise AutoBangumiProtocolError("status response must be an object")
        version = result.get("version")
        if version != self.config.expected_version:
            raise AutoBangumiVersionMismatch(
                f"expected AutoBangumi {self.config.expected_version}, got {version!r}"
            )
        return result

    def list_rss(self) -> list[Mapping[str, Any]]:
        self._require_login()
        result = self._request_json("GET", "/api/v1/rss")
        return self._require_object_list(result, "RSS list")

    def get_rss_torrents(self, rss_id: int) -> list[Mapping[str, Any]]:
        self._require_login()
        if isinstance(rss_id, bool) or not isinstance(rss_id, int) or rss_id < 1:
            raise ValueError("rss_id must be a positive integer")
        result = self._request_json("GET", f"/api/v1/rss/torrent/{rss_id}")
        return self._require_object_list(result, "RSS torrent list")

    def list_bangumi_rules(self) -> list[Mapping[str, Any]]:
        """Read all Bangumi rules after checking the authenticated server version."""
        self._require_login()
        self.get_status()
        result = self._request_json("GET", "/api/v1/bangumi/get/all")
        return self._require_object_list(result, "Bangumi rule list")

    def get_bangumi_rule(self, bangumi_id: int) -> Mapping[str, Any]:
        self._require_login()
        if isinstance(bangumi_id, bool) or not isinstance(bangumi_id, int) or bangumi_id < 1:
            raise ValueError("bangumi_id must be a positive integer")
        self.get_status()
        result = self._request_json("GET", f"/api/v1/bangumi/get/{bangumi_id}")
        if not isinstance(result, dict):
            raise AutoBangumiProtocolError("Bangumi rule response must be an object")
        return result

    @staticmethod
    def _require_object_list(value: Any, label: str) -> list[Mapping[str, Any]]:
        if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
            raise AutoBangumiProtocolError(f"{label} response must be a list of objects")
        return value

    def _require_login(self) -> None:
        if not self._authenticated:
            raise AutoBangumiAuthenticationError("login is required before reading data")

    @staticmethod
    def _assert_allowed(method: str, path: str) -> None:
        normalized_method = method.upper()
        allowed = (normalized_method, path) in _STATIC_ALLOWLIST
        allowed = allowed or (
            normalized_method == "GET" and _TORRENT_PATH.fullmatch(path) is not None
        )
        allowed = allowed or (
            normalized_method == "GET" and _BANGUMI_DETAIL_PATH.fullmatch(path) is not None
        )
        if not allowed:
            raise ReadOnlyPolicyError(f"request blocked by read-only policy: {method} {path}")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        self._assert_allowed(method, path)
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(
            f"{self.config.base_url}{path}",
            data=body,
            headers=headers,
            method=method.upper(),
        )
        try:
            with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read(self.config.max_response_bytes + 1)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                if path == "/api/v1/auth/login":
                    raise AutoBangumiAuthenticationError("login was rejected") from exc
                raise AutoBangumiAuthenticationError("session is unauthorized") from exc
            if 300 <= exc.code < 400:
                raise AutoBangumiRequestError(
                    "redirect response was blocked", status_code=exc.code
                ) from exc
            raise AutoBangumiRequestError(
                f"AutoBangumi returned HTTP {exc.code}",
                status_code=exc.code,
                upstream_message=_safe_http_error_message(exc),
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AutoBangumiRequestError("could not reach AutoBangumi") from exc
        if len(raw) > self.config.max_response_bytes:
            raise AutoBangumiProtocolError("response exceeded the configured size limit")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AutoBangumiProtocolError("response was not valid UTF-8 JSON") from exc
