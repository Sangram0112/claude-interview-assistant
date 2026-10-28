"""API key storage.

Lookup order for each secret:
1. Environment variable (e.g. ``ANTHROPIC_API_KEY``) - convenient for development and CI.
2. Windows Credential Manager via :mod:`keyring` - what the Settings screen writes to.

Keys are never written to settings.json, logs, or the packaged application.
"""

from __future__ import annotations

import logging
import os
from enum import Enum

from app import APP_ID

log = logging.getLogger(__name__)

try:  # keyring is optional at import time so tests and headless tools still work
    import keyring
    from keyring.errors import KeyringError, PasswordDeleteError
except ImportError:  # pragma: no cover - dependency is declared in requirements
    keyring = None  # type: ignore[assignment]
    KeyringError = PasswordDeleteError = Exception  # type: ignore[misc,assignment]


class SecretName(str, Enum):
    ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"
    DEEPGRAM_API_KEY = "DEEPGRAM_API_KEY"


class SecretSource(str, Enum):
    ENVIRONMENT = "environment variable"
    KEYRING = "Windows Credential Manager"
    MISSING = "not configured"


class SecretStore:
    def __init__(self, service: str = APP_ID) -> None:
        self._service = service

    def get(self, name: SecretName) -> str | None:
        value, _ = self.lookup(name)
        return value

    def lookup(self, name: SecretName) -> tuple[str | None, SecretSource]:
        env_value = os.environ.get(name.value, "").strip()
        if env_value:
            return env_value, SecretSource.ENVIRONMENT
        if keyring is not None:
            try:
                stored = keyring.get_password(self._service, name.value)
            except KeyringError as exc:
                log.warning("Credential store unavailable: %s", type(exc).__name__)
                stored = None
            if stored:
                return stored, SecretSource.KEYRING
        return None, SecretSource.MISSING

    def set(self, name: SecretName, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("Secret value must not be empty")
        if keyring is None:
            raise RuntimeError("The keyring package is not installed")
        keyring.set_password(self._service, name.value, value)
        log.info("Stored %s in the credential store", name.value)

    def delete(self, name: SecretName) -> None:
        if keyring is None:
            return
        try:
            keyring.delete_password(self._service, name.value)
            log.info("Deleted %s from the credential store", name.value)
        except PasswordDeleteError:
            pass  # nothing stored


def mask_secret(value: str | None) -> str:
    """Return a display-safe hint such as ``sk-ant-…a1b2``."""
    if not value:
        return ""
    if len(value) <= 12:
        return "•" * len(value)
    return f"{value[:7]}…{value[-4:]}"
