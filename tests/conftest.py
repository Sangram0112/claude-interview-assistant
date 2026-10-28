from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Every test gets its own data dir and no real API keys."""
    monkeypatch.setenv("CIA_DATA_DIR", str(tmp_path / "data"))
    for name in ("ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY", "CIA_CLAUDE_MODEL", "CIA_TRANSCRIPTION_PROVIDER", "CIA_WHISPER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    yield


class MemoryKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        from keyring.errors import PasswordDeleteError

        if (service, name) not in self.store:
            raise PasswordDeleteError(name)
        del self.store[(service, name)]


@pytest.fixture
def memory_keyring(monkeypatch):
    import app.security.secrets as secrets_mod

    fake = MemoryKeyring()
    monkeypatch.setattr(secrets_mod.keyring, "get_password", fake.get_password)
    monkeypatch.setattr(secrets_mod.keyring, "set_password", fake.set_password)
    monkeypatch.setattr(secrets_mod.keyring, "delete_password", fake.delete_password)
    return fake


@pytest.fixture
def db(tmp_path):
    from app.storage.database import Database

    database = Database(tmp_path / "test.db")
    yield database
    database.close()
