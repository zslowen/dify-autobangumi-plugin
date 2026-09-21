"""Safe Dify-facing functions for public Mikan search results."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

from .dify_adapter import _project_environment
from .mikan import (
    MikanError,
    MikanPolicyError,
    MikanProtocolError,
    MikanReadOnlyConnector,
    MikanRequestError,
    canonical_public_url,
)
from .redaction import redact_sensitive


class DifyMikanError(RuntimeError):
    """A non-sensitive Mikan search error suitable for Dify."""


def _redact_mikan_candidates(
    candidates: list[dict[str, Any]], base_url: str
) -> list[dict[str, Any]]:
    redacted = redact_sensitive(candidates)
    unique: list[dict[str, Any]] = []
    seen_rss: set[str] = set()
    for source, target in zip(candidates, redacted, strict=True):
        try:
            rss_url = canonical_public_url(source["rss_url"], base_url)
            source_page = canonical_public_url(source["source_page"], base_url)
        except (KeyError, TypeError, MikanPolicyError):
            continue
        if rss_url in seen_rss:
            continue
        seen_rss.add(rss_url)
        target["rss_url"] = rss_url
        target["source_page"] = source_page
        unique.append(target)
    return unique


def _redact_mikan_anime(candidates: list[dict[str, Any]], base_url: str) -> list[dict[str, Any]]:
    redacted = redact_sensitive(candidates)
    safe: list[dict[str, Any]] = []
    for source, target in zip(candidates, redacted, strict=True):
        try:
            target["source_page"] = canonical_public_url(source["source_page"], base_url)
        except (KeyError, TypeError, MikanPolicyError):
            continue
        safe.append(target)
    return safe


def _mikan_connector() -> tuple[MikanReadOnlyConnector, str]:
    base_url = _project_environment().get("MIKAN_BASE_URL", "https://mikanime.tv")
    return MikanReadOnlyConnector(base_url=base_url), base_url


def search_mikan_anime(title: Any) -> dict[str, Any]:
    if not isinstance(title, str):
        raise DifyMikanError("title must be text.")
    try:
        connector, base_url = _mikan_connector()
        result = connector.search_anime(title)
    except (MikanError, ValueError) as exc:
        raise DifyMikanError(str(exc)) from exc
    candidates = _redact_mikan_anime(result["candidates"], base_url)
    return {
        "title": title,
        "candidate_total": result["candidate_total"],
        "returned_count": len(candidates),
        "truncated": result["truncated"],
        "candidates": candidates,
    }


def list_mikan_rss_candidates(
    bangumi_id: Any,
    fansub_group: Any = None,
    subtitle_language: Any = None,
    resolution: Any = None,
    limit: Any = None,
) -> dict[str, Any]:
    if isinstance(bangumi_id, bool) or not isinstance(bangumi_id, int):
        raise DifyMikanError("bangumi_id must be a positive integer.")
    for label, value in (("fansub_group", fansub_group), ("subtitle_language", subtitle_language), ("resolution", resolution)):
        if value is not None and not isinstance(value, str):
            raise DifyMikanError(f"{label} must be text when provided.")
    if limit is None:
        limit = 5
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise DifyMikanError("limit must be an integer.")
    try:
        connector, base_url = _mikan_connector()
        result = connector.list_rss_candidates(
            bangumi_id, fansub_group=fansub_group, subtitle_language=subtitle_language,
            resolution=resolution, limit=limit,
        )
    except (MikanError, ValueError) as exc:
        raise DifyMikanError(str(exc)) from exc
    candidates = _redact_mikan_candidates(result["candidates"], base_url)
    return {
        "bangumi_id": bangumi_id,
        "anime_title": result["anime_title"],
        "filters": {
            "fansub_group": fansub_group or None,
            "subtitle_language": subtitle_language or None,
            "resolution": resolution or None,
        },
        "candidate_total": result["candidate_total"],
        "returned_count": len(candidates),
        "truncated": result["truncated"],
        "candidates": candidates,
    }


def inspect_mikan_rss_candidate(rss_url: Any) -> dict[str, Any]:
    """Inspect one public Mikan RSS candidate without using AutoBangumi."""
    if not isinstance(rss_url, str):
        return {
            "rss_url": None,
            "normalized_rss_url": None,
            "bangumi_id": None,
            "subgroup_id": None,
            "status": "url_validation_failed",
            "failure_stage": "mikan_url_validation",
            "reachable": False,
            "http_status": None,
            "diagnostics": [
                {"code": "invalid_rss_url", "message": "rss_url must be text."}
            ],
        }
    try:
        connector, base_url = _mikan_connector()
        normalized_url = canonical_public_url(rss_url, base_url)
        query = parse_qs(urlsplit(normalized_url).query)
        identity = {
            "rss_url": rss_url,
            "normalized_rss_url": normalized_url,
            "bangumi_id": int(query["bangumiId"][0]),
            "subgroup_id": int(query["subgroupid"][0]),
        }
        return connector.inspect_rss_candidate(rss_url)
    except (MikanPolicyError, ValueError):
        return {
            "rss_url": "[REJECTED]",
            "normalized_rss_url": None,
            "bangumi_id": None,
            "subgroup_id": None,
            "status": "url_validation_failed",
            "failure_stage": "mikan_url_validation",
            "reachable": False,
            "http_status": None,
            "diagnostics": [
                {
                    "code": "invalid_rss_url",
                    "message": "rss_url must be an allowlisted public single-anime Mikan RSS URL.",
                }
            ],
        }
    except MikanRequestError as exc:
        return {
            **identity,
            "status": "request_failed",
            "failure_stage": "mikan_preflight",
            "reachable": exc.status_code is not None,
            "http_status": exc.status_code,
            "diagnostics": [
                {
                    "code": "mikan_request_failed",
                    "message": "The public Mikan RSS request failed.",
                }
            ],
        }
    except MikanProtocolError:
        return {
            **identity,
            "status": "invalid_response",
            "failure_stage": "mikan_preflight",
            "reachable": True,
            "http_status": None,
            "diagnostics": [
                {
                    "code": "mikan_response_invalid",
                    "message": "Mikan returned a response that could not be safely parsed.",
                }
            ],
        }


def search_mikan_rss(anime_title: Any, fansub_group: Any = None) -> dict[str, Any]:
    if not isinstance(anime_title, str):
        raise DifyMikanError("anime_title must be text.")
    if fansub_group is not None and not isinstance(fansub_group, str):
        raise DifyMikanError("fansub_group must be text when provided.")
    try:
        connector, base_url = _mikan_connector()
        candidates = connector.search_rss(
            anime_title, fansub_group
        )
    except (MikanError, ValueError) as exc:
        raise DifyMikanError(str(exc)) from exc
    candidates = _redact_mikan_candidates(candidates, base_url)
    result: dict[str, Any] = {
        "anime_title": anime_title,
        "fansub_group": fansub_group or None,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    if not candidates:
        result["message"] = "No public Mikan RSS candidates matched the query."
    return result
