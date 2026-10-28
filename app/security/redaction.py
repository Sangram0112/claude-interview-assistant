"""Scrub credentials from any text that could reach logs or the UI."""

from __future__ import annotations

import logging
import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Anthropic keys: sk-ant-api03-..., sk-ant-admin01-...
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "sk-ant-[REDACTED]"),
    # Generic "sk-" style keys
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}"), "sk-[REDACTED]"),
    # Authorization headers: "Token abc", "Bearer abc", x-api-key: abc
    (re.compile(r"(?i)\b(token|bearer)\s+[A-Za-z0-9._\-]{16,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)(x-api-key['\"]?\s*[:=]\s*['\"]?)[^\s'\",]+"), r"\1[REDACTED]"),
    # Long hex strings (e.g. Deepgram keys are 40 hex chars)
    (re.compile(r"\b[a-f0-9]{32,}\b"), "[REDACTED-HEX]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from the final formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging crash the app
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True
