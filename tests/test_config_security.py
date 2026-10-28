import json
import logging

from app.config import DEFAULT_MODEL, Retention, Settings, TranscriptionProviderId
from app.security.redaction import RedactingFilter, redact
from app.security.secrets import SecretName, SecretSource, SecretStore, mask_secret

FAKE_KEY = "sk-ant-api03-" + "A1b2C3d4" * 10


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings()
    s.ai.model = "claude-sonnet-5-5"
    s.privacy.retention = Retention.DAYS_7
    s.save(path)
    loaded = Settings.load(path)
    assert loaded.ai.model == "claude-sonnet-5-5"
    assert loaded.privacy.retention == Retention.DAYS_7


def test_settings_never_contain_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    path = tmp_path / "settings.json"
    Settings.load(path).save(path)
    assert "sk-ant" not in path.read_text()
    assert "api_key" not in json.loads(path.read_text())["ai"]


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert Settings.load(path).ai.model == DEFAULT_MODEL


def test_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("CIA_CLAUDE_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("CIA_TRANSCRIPTION_PROVIDER", "deepgram")
    s = Settings.load(tmp_path / "missing.json")
    assert s.ai.model == "claude-haiku-5-5"
    assert s.transcription.provider == TranscriptionProviderId.DEEPGRAM


def test_default_privacy_settings_are_conservative():
    s = Settings()
    assert s.privacy.retention == Retention.NONE
    assert s.privacy.log_transcripts is False


def test_secret_store_env_takes_precedence(memory_keyring, monkeypatch):
    store = SecretStore()
    store.set(SecretName.ANTHROPIC_API_KEY, "sk-ant-stored-key-123456")
    assert store.lookup(SecretName.ANTHROPIC_API_KEY) == ("sk-ant-stored-key-123456", SecretSource.KEYRING)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-key-123456")
    assert store.lookup(SecretName.ANTHROPIC_API_KEY)[1] == SecretSource.ENVIRONMENT


def test_secret_store_delete(memory_keyring):
    store = SecretStore()
    store.set(SecretName.DEEPGRAM_API_KEY, "abc123")
    store.delete(SecretName.DEEPGRAM_API_KEY)
    store.delete(SecretName.DEEPGRAM_API_KEY)  # idempotent
    assert store.lookup(SecretName.DEEPGRAM_API_KEY) == (None, SecretSource.MISSING)


def test_mask_secret():
    assert mask_secret(FAKE_KEY).startswith("sk-ant-")
    assert FAKE_KEY not in mask_secret(FAKE_KEY)
    assert mask_secret(None) == ""


def test_redact_patterns():
    text = f"key={FAKE_KEY} header Authorization: Token {'f' * 40} x-api-key: secretvalue123"
    cleaned = redact(text)
    assert FAKE_KEY not in cleaned
    assert "f" * 40 not in cleaned
    assert "secretvalue123" not in cleaned


def test_logging_filter_redacts(caplog):
    logger = logging.getLogger("test.redact")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="test.redact"):
        logger.info("Using key %s", FAKE_KEY)
    assert FAKE_KEY not in caplog.text
    assert "REDACTED" in caplog.text
