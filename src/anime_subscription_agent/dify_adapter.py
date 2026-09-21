"""Safe adapter between Dify tools and the existing read-only connector."""

from __future__ import annotations

import os
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .autobangumi import AutoBangumiError, AutoBangumiReadOnlyConnector
from .redaction import redact_sensitive


class DifyAutoBangumiError(RuntimeError):
    """A non-sensitive error suitable for a Dify tool invocation."""


def _project_environment() -> Mapping[str, str]:
    """Load project-root .env without logging or exposing its secret values."""
    dotenv_path = Path(__file__).resolve().parents[2] / ".env"
    values = {
        key: value
        for key, value in dotenv_values(dotenv_path).items()
        if value is not None
    }
    values.update(os.environ)
    return values


def _value(
    credentials: Mapping[str, Any], name: str, environment: Mapping[str, str]
) -> str:
    value = credentials.get(name)
    if value is None or not str(value).strip():
        value = environment.get(name.upper())
    return str(value).strip() if value is not None else ""


def connector_from_credentials(
    credentials: Mapping[str, Any], environment: Mapping[str, str] | None = None
) -> AutoBangumiReadOnlyConnector:
    if environment is None:
        environment = _project_environment()
    base_url = _value(credentials, "ab_base_url", environment) or "http://127.0.0.1:7892"
    expected_version = _value(credentials, "ab_expected_version", environment) or "3.2.8"
    username = _value(credentials, "ab_username", environment)
    password = _value(credentials, "ab_password", environment)
    if not username or not password:
        raise DifyAutoBangumiError("AutoBangumi credentials are not configured.")
    try:
        connector = AutoBangumiReadOnlyConnector(
            base_url, expected_version=expected_version
        )
        connector.login(username, password)
        return connector
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError("Could not authenticate to AutoBangumi.") from exc


def get_ab_status(credentials: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return redact_sensitive(dict(connector_from_credentials(credentials).get_status()))
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError("Could not read AutoBangumi status.") from exc


def list_ab_subscriptions(credentials: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        return redact_sensitive(
            [dict(item) for item in connector_from_credentials(credentials).list_rss()]
        )
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError("Could not read AutoBangumi RSS subscriptions.") from exc


def get_ab_rss_records(
    credentials: Mapping[str, Any], rss_id: Any
) -> list[dict[str, Any]]:
    if isinstance(rss_id, bool) or not isinstance(rss_id, (int, float)):
        raise DifyAutoBangumiError("rss_id must be a positive integer.")
    if isinstance(rss_id, float) and not rss_id.is_integer():
        raise DifyAutoBangumiError("rss_id must be a positive integer.")
    normalized_id = int(rss_id)
    if normalized_id < 1:
        raise DifyAutoBangumiError("rss_id must be a positive integer.")
    try:
        return redact_sensitive(
            [
                dict(item)
                for item in connector_from_credentials(credentials).get_rss_torrents(
                    normalized_id
                )
            ]
        )
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError("Could not read AutoBangumi RSS records.") from exc


_BANGUMI_OUTPUT_FIELDS = (
    "id", "official_title", "year", "title_raw", "season", "season_raw", "title_aliases",
    "group_name", "subtitle", "source", "added", "archived", "deleted", "needs_review",
    "needs_review_reason", "episode_offset", "season_offset", "suggested_season_offset",
    "suggested_episode_offset",
)


def _title_aliases(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _title_matches(record: Mapping[str, Any], title: str) -> bool:
    needle = "".join(title.casefold().split())
    values = [record.get("official_title"), record.get("title_raw"), *_title_aliases(record.get("title_aliases"))]
    return any(isinstance(value, str) and needle in "".join(value.casefold().split()) for value in values)


def _project_bangumi_rule(record: Mapping[str, Any]) -> dict[str, Any]:
    projected = {field: record.get(field) for field in _BANGUMI_OUTPUT_FIELDS}
    projected["title_aliases"] = _title_aliases(record.get("title_aliases"))
    projected["rule_ready"] = (
        record.get("added") is True
        and isinstance(record.get("save_path"), str)
        and bool(record["save_path"].strip())
    )
    return projected


def list_ab_bangumi_rules(
    credentials: Mapping[str, Any], title: Any = None, season: Any = None, limit: Any = None
) -> dict[str, Any]:
    if title is not None and not isinstance(title, str):
        raise DifyAutoBangumiError("title must be text when provided.")
    if isinstance(season, bool) or (season is not None and not isinstance(season, int)):
        raise DifyAutoBangumiError("season must be a positive integer when provided.")
    if season is not None and season < 1:
        raise DifyAutoBangumiError("season must be a positive integer when provided.")
    if limit is None:
        limit = 20
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise DifyAutoBangumiError("limit must be an integer from 1 to 50.")
    normalized_title = title.strip() if isinstance(title, str) else ""
    if title is not None and not normalized_title:
        raise DifyAutoBangumiError("title must not be empty when provided.")
    try:
        records = connector_from_credentials(credentials).list_bangumi_rules()
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError("Could not read AutoBangumi Bangumi rules.") from exc
    filtered = [record for record in records if (not normalized_title or _title_matches(record, normalized_title)) and (season is None or record.get("season") == season)]
    projected = [_project_bangumi_rule(record) for record in filtered]
    return {
        "title": normalized_title or None,
        "season": season,
        "total_count": len(projected),
        "returned_count": min(len(projected), limit),
        "truncated": len(projected) > limit,
        "rules": projected[:limit],
    }
