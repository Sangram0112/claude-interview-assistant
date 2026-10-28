"""Logging configuration: rotating file + console, with secret redaction.

Transcript text is not logged by default; modules that handle transcript content
call :func:`transcript_preview`, which returns only a length unless the user has
explicitly enabled transcript logging.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from app.paths import log_dir
from app.security.redaction import RedactingFilter

_log_transcripts = False


def configure_logging(level: int = logging.INFO, log_transcripts: bool = False) -> None:
    global _log_transcripts
    _log_transcripts = log_transcripts

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    redactor = RedactingFilter()

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir() / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redactor)
    root.addHandler(file_handler)

    if sys.stderr is not None:  # windowed PyInstaller builds have no stderr
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console.addFilter(redactor)
        root.addHandler(console)

    # Third-party HTTP libraries can log request details at DEBUG; keep them quiet.
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "websockets", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def set_transcript_logging(enabled: bool) -> None:
    global _log_transcripts
    _log_transcripts = enabled


def transcript_preview(text: str) -> str:
    """What to put in a log line in place of transcript content."""
    if _log_transcripts:
        return repr(text[:200])
    return f"<{len(text)} chars>"
