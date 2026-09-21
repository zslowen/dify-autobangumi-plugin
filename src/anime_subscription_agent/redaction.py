"""Data redaction shared by command-line and Dify entry points."""

from __future__ import annotations

from typing import Any


SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "cookie",
    "url",
    "link",
    "username",
    "authorization",
    "credential",
    "secret",
)


def redact_sensitive(value: Any) -> Any:
    """Return a deep copy suitable for logs and Dify messages."""
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(part in key.lower() for part in SENSITIVE_KEY_PARTS)
            else redact_sensitive(item)
            for key, item in value.items()
        }
    return value
