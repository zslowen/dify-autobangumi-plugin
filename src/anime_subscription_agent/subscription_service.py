"""Deterministic, non-subscribing AutoBangumi subscription preview service."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from .autobangumi import AutoBangumiError, AutoBangumiRequestError
from .autobangumi_subscription import AutoBangumiSubscriptionPreviewConnector
from .autobangumi_writer import (
    AutoBangumiSubscriptionWriteConnector,
    AutoBangumiWriteOutcomeUncertain,
    AutoBangumiWriteRejected,
)
from .dify_adapter import DifyAutoBangumiError, _project_environment, _value
from .mikan import MikanPolicyError, canonical_public_url


_PARSED_OUTPUT_FIELDS = (
    "official_title",
    "year",
    "season",
    "season_raw",
    "group_name",
    "dpi",
    "subtitle",
    "source",
    "needs_review",
    "needs_review_reason",
)

_GROUP_SEPARATOR_RE = re.compile(r"[&+\u00d7/]")
_HTML_AMP_RE = re.compile(r"&amp;", re.IGNORECASE)


class SubscriptionPreparationError(DifyAutoBangumiError):
    """A structured, non-sensitive failure from a subscription preview stage."""

    def __init__(
        self,
        message: str,
        *,
        failure_stage: str,
        upstream_status_code: int | None = None,
        upstream_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_stage = failure_stage
        self.upstream_status_code = upstream_status_code
        self.upstream_message = upstream_message

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "failed",
            "failure_stage": self.failure_stage,
            "upstream_status_code": self.upstream_status_code,
            "write_attempted": False,
            "diagnostics": [
                {"code": "prepare_failed", "message": str(self)}
            ],
        }
        if self.upstream_message:
            result["upstream_message"] = self.upstream_message
        return result


def _preparation_error(
    failure_stage: str, message: str, error: Exception
) -> SubscriptionPreparationError:
    if isinstance(error, AutoBangumiRequestError):
        return SubscriptionPreparationError(
            message,
            failure_stage=failure_stage,
            upstream_status_code=error.status_code,
            upstream_message=error.upstream_message,
        )
    return SubscriptionPreparationError(message, failure_stage=failure_stage)


def _normalise_text(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _group_match(selected_group: str, parsed_group: Any) -> dict[str, Any]:
    """Compare one selected group with an exact or safely split joint credit."""
    effective_group = (
        " ".join(parsed_group.split())
        if isinstance(parsed_group, str) and parsed_group.strip()
        else None
    )
    result: dict[str, Any] = {
        "selected_group": selected_group,
        "parsed_group": effective_group,
        "effective_group": effective_group,
        "parsed_members": [],
        "match_type": "mismatch",
        "confirmation_required": False,
    }
    if effective_group is None:
        return result
    if _normalise_text(selected_group) == _normalise_text(effective_group):
        result["parsed_members"] = [effective_group]
        result["match_type"] = "exact"
        return result

    normalized_credit = unicodedata.normalize("NFKC", effective_group)
    normalized_credit = _HTML_AMP_RE.sub("&", normalized_credit)
    raw_members = _GROUP_SEPARATOR_RE.split(normalized_credit)
    if len(raw_members) < 2 or any(not member.strip() for member in raw_members):
        return result
    members = [" ".join(member.split()) for member in raw_members]
    result["parsed_members"] = members
    selected_key = _normalise_text(selected_group)
    if any(_normalise_text(member) == selected_key for member in members):
        result["match_type"] = "joint"
        result["confirmation_required"] = True
    return result


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DifyAutoBangumiError(f"{label} must be non-empty text.")
    return " ".join(value.split())


def _preview_connector(
    credentials: Mapping[str, Any], environment: Mapping[str, str]
) -> AutoBangumiSubscriptionPreviewConnector:
    base_url = _value(credentials, "ab_base_url", environment) or "http://127.0.0.1:7892"
    version = _value(credentials, "ab_expected_version", environment) or "3.2.8"
    username = _value(credentials, "ab_username", environment)
    password = _value(credentials, "ab_password", environment)
    if not username or not password:
        raise DifyAutoBangumiError("AutoBangumi credentials are not configured.")
    try:
        connector = AutoBangumiSubscriptionPreviewConnector(
            base_url, expected_version=version
        )
        connector.login(username, password)
        return connector
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError("Could not authenticate to AutoBangumi.") from exc


def _write_connector(
    credentials: Mapping[str, Any], environment: Mapping[str, str]
) -> AutoBangumiSubscriptionWriteConnector:
    base_url = _value(credentials, "ab_base_url", environment) or "http://127.0.0.1:7892"
    version = _value(credentials, "ab_expected_version", environment) or "3.2.8"
    username = _value(credentials, "ab_username", environment)
    password = _value(credentials, "ab_password", environment)
    if not username or not password:
        raise DifyAutoBangumiError("AutoBangumi credentials are not configured.")
    try:
        connector = AutoBangumiSubscriptionWriteConnector(
            base_url, expected_version=version
        )
        connector.login(username, password)
        return connector
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError("Could not authenticate to AutoBangumi.") from exc


def _canonical_existing_url(value: Any, base_url: str) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return canonical_public_url(value, base_url)
    except (MikanPolicyError, ValueError):
        return None


def _rule_title_matches(official_title: str, rule: Mapping[str, Any]) -> bool:
    value = rule.get("official_title")
    return isinstance(value, str) and _normalise_text(value) == _normalise_text(official_title)


def _preview_id(
    normalized_url: str,
    requested: Mapping[str, Any],
    parsed: Mapping[str, Any],
    group_match: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "version": 1,
            "rss_url": normalized_url,
            "requested": requested,
            "parsed": parsed,
            "group_match": group_match,
            "analysis": analysis,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "abp1_" + hashlib.sha256(canonical).hexdigest()[:32]


def _validated_request(
    rss_url: Any,
    expected_title: Any,
    expected_season: Any,
    expected_group: Any,
    mikan_base_url: str,
    expected_year: Any = None,
) -> tuple[str, str, int, str, int | None]:
    title = _require_text(expected_title, "expected_title")
    group = _require_text(expected_group, "expected_group")
    if (
        isinstance(expected_season, bool)
        or not isinstance(expected_season, int)
        or expected_season < 1
    ):
        raise DifyAutoBangumiError("expected_season must be a positive integer.")
    if expected_year is not None and (
        isinstance(expected_year, bool)
        or not isinstance(expected_year, int)
        or expected_year < 1000
        or expected_year > 9999
    ):
        raise DifyAutoBangumiError(
            "expected_year must be a four-digit integer when provided."
        )
    if not isinstance(rss_url, str):
        raise SubscriptionPreparationError(
            "rss_url must be text.", failure_stage="mikan_url_validation"
        )
    try:
        normalized_url = canonical_public_url(rss_url, mikan_base_url)
    except (MikanPolicyError, ValueError) as exc:
        raise SubscriptionPreparationError(
            "rss_url must be a public single-anime Mikan RSS URL.",
            failure_stage="mikan_url_validation",
        ) from exc
    return normalized_url, title, expected_season, group, expected_year


def _evaluate_preview(
    normalized_url: str,
    title: str,
    expected_season: int,
    group: str,
    analysis: Mapping[str, Any],
    existing_rss: list[Mapping[str, Any]],
    existing_rules: list[Mapping[str, Any]],
    mikan_base_url: str,
    expected_year: int | None = None,
) -> dict[str, Any]:
    """Compute the one canonical preview used by both prepare and subscribe."""
    parsed = {field: analysis.get(field) for field in _PARSED_OUTPUT_FIELDS}
    parsed_title = analysis.get("official_title")
    parsed_year = analysis.get("year")
    parsed_season = analysis.get("season")
    parsed_group = analysis.get("group_name")
    group_match = _group_match(group, parsed_group)
    effective_group = group_match["effective_group"]
    required_missing = [
        field
        for field, value in (
            ("official_title", parsed_title),
            ("season", parsed_season),
            ("group_name", parsed_group),
        )
        if value is None or (isinstance(value, str) and not value.strip())
    ]

    rss_url_exists = any(
        _canonical_existing_url(item.get("url"), mikan_base_url) == normalized_url
        for item in existing_rss
    )
    matching_title_season = [
        rule
        for rule in existing_rules
        if _rule_title_matches(title, rule)
        and rule.get("season") == expected_season
    ]
    same_title_season_group_exists = any(
        isinstance(rule.get("group_name"), str)
        and isinstance(effective_group, str)
        and _normalise_text(rule["group_name"]) == _normalise_text(effective_group)
        for rule in matching_title_season
    )
    duplicate = {
        "rss_url_exists": rss_url_exists,
        "same_title_season_exists": bool(matching_title_season),
        "same_title_season_group_exists": same_title_season_group_exists,
    }

    warnings: list[dict[str, str]] = []
    blocking = False
    if required_missing:
        blocking = True
        warnings.append(
            {
                "code": "analysis_missing_fields",
                "message": "AutoBangumi analysis omitted: "
                + ", ".join(required_missing),
            }
        )
    target_year = expected_year if expected_year is not None else parsed_year
    adjustments: list[dict[str, Any]] = []
    if isinstance(parsed_title, str) and _normalise_text(parsed_title) != _normalise_text(title):
        adjustments.append(
            {"field": "official_title", "parsed": parsed_title, "target": title}
        )
    if parsed_season != expected_season:
        adjustments.append(
            {"field": "season", "parsed": parsed_season, "target": expected_season}
        )
    if expected_year is not None and parsed_year != expected_year:
        adjustments.append(
            {"field": "year", "parsed": parsed_year, "target": expected_year}
        )
    if adjustments:
        warnings.append(
            {
                "code": "metadata_adjustment",
                "message": "Confirmed target metadata differs from AutoBangumi analysis.",
            }
        )
    if group_match["match_type"] == "joint":
        warnings.append(
            {
                "code": "group_expansion",
                "message": "The selected subtitle group is one member of the parsed joint credit; the joint credit requires explicit confirmation.",
            }
        )
    elif group_match["match_type"] == "mismatch":
        blocking = True
        warnings.append(
            {
                "code": "group_mismatch",
                "message": "Parsed subtitle group does not match the selected group.",
            }
        )
    if analysis.get("needs_review") is True:
        blocking = True
        warnings.append(
            {
                "code": "analysis_needs_review",
                "message": "AutoBangumi marked the parsed rule for review.",
            }
        )
    if rss_url_exists:
        blocking = True
        warnings.append(
            {
                "code": "rss_url_exists",
                "message": "The normalized RSS URL already exists.",
            }
        )
    if same_title_season_group_exists:
        blocking = True
        warnings.append(
            {
                "code": "same_title_season_group_exists",
                "message": "The same title, season, and subtitle group already exists.",
            }
        )

    different_group_conflict = bool(
        matching_title_season
    ) and not same_title_season_group_exists
    if different_group_conflict:
        warnings.append(
            {
                "code": "same_title_season_different_group",
                "message": "The title and season exist with a different RSS source.",
            }
        )

    if blocking:
        status = "blocked"
    elif different_group_conflict:
        status = "conflict_requires_confirmation"
    else:
        status = "ready_for_confirmation"

    requested = {
        "rss_url": normalized_url,
        "title": title,
        "season": expected_season,
        "year": target_year,
        "group": group,
        "selected_group": group,
        "effective_group": effective_group,
    }
    target = dict(requested)
    target["group"] = effective_group
    return {
        "status": status,
        "requested": requested,
        "target": target,
        "parsed": parsed,
        "group_match": group_match,
        "metadata_adjustment": {
            "required": bool(adjustments),
            "fields": adjustments,
        },
        "duplicate": duplicate,
        "warnings": warnings,
        "preview_id": _preview_id(
            normalized_url, requested, parsed, group_match, analysis
        ),
    }


def prepare_ab_subscription(
    credentials: Mapping[str, Any],
    rss_url: Any,
    expected_title: Any,
    expected_season: Any,
    expected_group: Any,
    expected_year: Any = None,
) -> dict[str, Any]:
    environment = _project_environment()
    mikan_base_url = environment.get("MIKAN_BASE_URL", "https://mikanime.tv")
    normalized_url, title, season, group, year = _validated_request(
        rss_url,
        expected_title,
        expected_season,
        expected_group,
        mikan_base_url,
        expected_year,
    )

    try:
        connector = _preview_connector(credentials, environment)
    except (DifyAutoBangumiError, AutoBangumiError, ValueError) as exc:
        raise _preparation_error(
            "autobangumi_authentication",
            "Could not authenticate to AutoBangumi.",
            exc,
        ) from exc
    try:
        analysis = connector.analyze_rss(normalized_url)
    except (AutoBangumiError, ValueError) as exc:
        raise _preparation_error(
            "autobangumi_analysis",
            "AutoBangumi analysis failed for this RSS candidate.",
            exc,
        ) from exc
    try:
        existing_rss = connector.list_rss()
        existing_rules = connector.list_bangumi_rules()
    except (AutoBangumiError, ValueError) as exc:
        raise _preparation_error(
            "autobangumi_readback",
            "Could not read current AutoBangumi state for the preview.",
            exc,
        ) from exc
    return _evaluate_preview(
        normalized_url,
        title,
        season,
        group,
        analysis,
        existing_rss,
        existing_rules,
        mikan_base_url,
        year,
    )


def _verification_state(
    normalized_url: str,
    target: Mapping[str, Any],
    rss_items: list[Mapping[str, Any]],
    rules: list[Mapping[str, Any]],
    mikan_base_url: str,
) -> dict[str, bool]:
    rss_exists = any(
        _canonical_existing_url(item.get("url"), mikan_base_url) == normalized_url
        for item in rss_items
    )
    title = target.get("title")
    season = target.get("season")
    group = target.get("effective_group")
    rule_exists = any(
        isinstance(title, str)
        and _rule_title_matches(title, rule)
        and rule.get("season") == season
        and isinstance(group, str)
        and isinstance(rule.get("group_name"), str)
        and _normalise_text(rule["group_name"]) == _normalise_text(group)
        for rule in rules
    )
    return {"rss_exists": rss_exists, "rule_exists": rule_exists}


def _write_result(
    status: str,
    preview: Mapping[str, Any],
    *,
    write_attempted: bool,
    verification: Mapping[str, bool],
    warning_code: str | None = None,
    warning_message: str | None = None,
) -> dict[str, Any]:
    warnings = list(preview.get("warnings", []))
    if warning_code and warning_message:
        warnings.append({"code": warning_code, "message": warning_message})
    return {
        "status": status,
        "requested": preview["requested"],
        "target": preview["target"],
        "parsed": preview["parsed"],
        "group_match": preview["group_match"],
        "metadata_adjustment": preview["metadata_adjustment"],
        "duplicate": preview["duplicate"],
        "preview_id": preview["preview_id"],
        "write_attempted": write_attempted,
        "verification": dict(verification),
        "warnings": warnings,
    }


def subscribe_ab_rss(
    credentials: Mapping[str, Any],
    rss_url: Any,
    expected_title: Any,
    expected_season: Any,
    expected_group: Any,
    preview_id: Any,
    expected_year: Any = None,
) -> dict[str, Any]:
    """Execute one controlled write after re-analysis, re-checks, and fingerprint validation."""
    confirmed_preview_id = _require_text(preview_id, "preview_id")
    if not re.fullmatch(r"abp1_[0-9a-f]{32}", confirmed_preview_id):
        raise DifyAutoBangumiError("preview_id has an invalid format.")

    environment = _project_environment()
    mikan_base_url = environment.get("MIKAN_BASE_URL", "https://mikanime.tv")
    normalized_url, title, season, group, year = _validated_request(
        rss_url,
        expected_title,
        expected_season,
        expected_group,
        mikan_base_url,
        expected_year,
    )
    connector = _write_connector(credentials, environment)

    try:
        analysis = connector.analyze_rss(normalized_url)
        existing_rss = connector.list_rss()
        existing_rules = connector.list_bangumi_rules()
    except (AutoBangumiError, ValueError) as exc:
        raise DifyAutoBangumiError(
            "Could not revalidate the AutoBangumi subscription preview."
        ) from exc

    current_preview = _evaluate_preview(
        normalized_url,
        title,
        season,
        group,
        analysis,
        existing_rss,
        existing_rules,
        mikan_base_url,
        year,
    )
    current_verification = _verification_state(
        normalized_url,
        current_preview["target"],
        existing_rss,
        existing_rules,
        mikan_base_url,
    )
    duplicate = current_preview["duplicate"]
    if current_preview["preview_id"] != confirmed_preview_id:
        return _write_result(
            "analysis_mismatch",
            current_preview,
            write_attempted=False,
            verification=current_verification,
            warning_code="preview_changed",
            warning_message="The current analysis or target metadata no longer matches the confirmed preview.",
        )
    if duplicate["rss_url_exists"] or duplicate["same_title_season_group_exists"]:
        return _write_result(
            "already_exists_noop",
            current_preview,
            write_attempted=False,
            verification=current_verification,
        )
    if (
        current_preview["status"] == "blocked"
    ):
        return _write_result(
            "analysis_mismatch",
            current_preview,
            write_attempted=False,
            verification=current_verification,
            warning_code="preview_changed",
            warning_message="The current analysis no longer matches the confirmed preview.",
        )

    write_analysis = dict(analysis)
    write_analysis["official_title"] = title
    write_analysis["season"] = season
    if year is not None:
        write_analysis["year"] = year

    write_outcome = "response_received"
    try:
        connector.subscribe_rss_once(write_analysis, normalized_url)
    except AutoBangumiWriteRejected:
        write_outcome = "rejected"
    except AutoBangumiWriteOutcomeUncertain:
        write_outcome = "uncertain"

    try:
        post_rss = connector.list_rss()
        post_rules = connector.list_bangumi_rules()
    except (AutoBangumiError, ValueError):
        return _write_result(
            "unknown_outcome",
            current_preview,
            write_attempted=True,
            verification={"rss_exists": False, "rule_exists": False},
            warning_code="verification_unavailable",
            warning_message="The write was attempted, but read-back verification was unavailable.",
        )

    verification = _verification_state(
        normalized_url,
        current_preview["target"],
        post_rss,
        post_rules,
        mikan_base_url,
    )
    if verification["rss_exists"] and verification["rule_exists"]:
        status = "created_verified"
        warning_code = warning_message = None
    elif verification["rss_exists"] or verification["rule_exists"]:
        status = "partial_state"
        warning_code = "partial_state"
        warning_message = "Only part of the expected AutoBangumi state was found after the write."
    elif write_outcome == "rejected":
        status = "write_rejected"
        warning_code = "write_rejected"
        warning_message = "AutoBangumi rejected the write and no resulting state was found."
    else:
        status = "unknown_outcome"
        warning_code = "write_outcome_unknown"
        warning_message = "No expected state was found; the request must not be retried automatically."
    return _write_result(
        status,
        current_preview,
        write_attempted=True,
        verification=verification,
        warning_code=warning_code,
        warning_message=warning_message,
    )
