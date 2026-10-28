"""Filesystem locations. All user data lives outside the install/source directory."""

from __future__ import annotations

import os
from pathlib import Path

from app import APP_ID


def data_dir() -> Path:
    """Per-user data directory (settings, SQLite DB, logs).

    ``CIA_DATA_DIR`` overrides the location (used by tests and portable installs).
    """
    override = os.environ.get("CIA_DATA_DIR")
    if override:
        base = Path(override)
    else:
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        base = Path(root) / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return data_dir() / "settings.json"


def database_path() -> Path:
    return data_dir() / "assistant.db"


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    """Where local speech-to-text models are cached.

    ``CIA_MODELS_DIR`` overrides it, e.g. a short path such as ``C:\\cia-models`` when the
    profile path is long enough to hit Windows' 260-character path limit.
    """
    override = os.environ.get("CIA_MODELS_DIR")
    path = Path(override) if override else data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
