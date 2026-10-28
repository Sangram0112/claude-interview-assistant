"""Application settings.

Non-secret settings are stored as JSON in the user data directory. Secrets (API keys)
are never stored here - see :mod:`app.security.secrets`.
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.models import AnswerMode
from app.paths import settings_path

log = logging.getLogger(__name__)

# Model IDs offered in the UI. The first entry is the default.
SUPPORTED_MODELS: dict[str, str] = {
    "claude-opus-5-5": "Claude Opus 5.5 (best quality)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (faster)",
    "claude-haiku-5-5": "Claude Haiku 5.5 (fastest, lowest cost)",
}
DEFAULT_MODEL = next(iter(SUPPORTED_MODELS))

# USD per million tokens (input, output), Anthropic first-party API rates.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


class TranscriptionProviderId(str, Enum):
    LOCAL_WHISPER = "local_whisper"
    DEEPGRAM = "deepgram"


class Retention(str, Enum):
    NONE = "none"  # never write transcripts / Q&A to disk
    DAYS_1 = "1d"
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    FOREVER = "forever"

    @property
    def days(self) -> int | None:
        return {"none": 0, "1d": 1, "7d": 7, "30d": 30, "forever": None}[self.value]


class AnswerLength(str, Enum):
    SHORT = "short"  # ~30 s spoken
    MEDIUM = "medium"  # ~60 s
    LONG = "long"  # ~90 s

    @property
    def target_words(self) -> int:
        return {"short": 80, "medium": 150, "long": 220}[self.value]


class Effort(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AISettings(BaseModel):
    model: str = DEFAULT_MODEL
    # Reasoning effort replaces "temperature": since API version 2026-10-21 the
    # Messages API rejects temperature/top_p/top_k on every model.
    effort: Effort = Effort.LOW
    answer_length: AnswerLength = AnswerLength.MEDIUM
    default_mode: AnswerMode = AnswerMode.CONCISE
    max_tokens: int = Field(default=16000, ge=1024, le=64000)
    request_timeout_s: float = Field(default=60.0, ge=5, le=600)
    max_retries: int = Field(default=2, ge=0, le=6)
    recent_context_turns: int = Field(default=6, ge=0, le=30)
    refusal_fallback: bool = True

    @field_validator("model")
    @classmethod
    def _known_model(cls, value: str) -> str:
        # Unknown IDs are allowed (new models ship often) but logged.
        if value not in SUPPORTED_MODELS:
            log.warning("Model %s is not in the built-in list; using it as configured", value)
        return value


class AudioSettings(BaseModel):
    capture_microphone: bool = True
    capture_system_audio: bool = True
    microphone_device: int | None = None  # None = Windows default
    loopback_device: int | None = None  # None = loopback of default output device
    chunk_ms: int = Field(default=30, ge=10, le=100)  # VAD frame size
    silence_ms: int = Field(default=700, ge=200, le=3000)  # end-of-utterance silence
    max_utterance_s: float = Field(default=15.0, ge=3, le=60)
    min_rms: float = Field(default=0.006, ge=0.0, le=0.5)  # speech energy threshold


class TranscriptionSettings(BaseModel):
    provider: TranscriptionProviderId = TranscriptionProviderId.LOCAL_WHISPER
    whisper_model: str = "base.en"  # tiny.en | base.en | small.en | medium.en ...
    whisper_device: str = "cpu"  # cpu | cuda
    whisper_compute_type: str = "int8"
    interim_results: bool = True
    interim_interval_s: float = Field(default=1.5, ge=0.5, le=10)
    language: str = "en"
    deepgram_model: str = "nova-3"


class DetectionSettings(BaseModel):
    detect_from_interviewer: bool = True
    detect_from_candidate: bool = False
    min_words: int = Field(default=4, ge=1, le=20)
    duplicate_threshold: float = Field(default=0.82, ge=0.5, le=1.0)
    duplicate_window_s: float = Field(default=600.0, ge=10, le=7200)
    auto_generate: bool = True


class PrivacySettings(BaseModel):
    retention: Retention = Retention.NONE
    log_transcripts: bool = False  # never on by default
    consent_acknowledged_version: int = 0  # version of the disclosure the user accepted


class UISettings(BaseModel):
    always_on_top: bool = False
    compact: bool = False
    font_scale: float = Field(default=1.0, ge=0.7, le=2.0)
    global_hotkeys: bool = True
    platform: str = "zoom"


class Settings(BaseModel):
    ai: AISettings = Field(default_factory=AISettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    transcription: TranscriptionSettings = Field(default_factory=TranscriptionSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    ui: UISettings = Field(default_factory=UISettings)

    # --- persistence -----------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or settings_path()
        settings = cls()
        if path.exists():
            try:
                settings = cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, ValidationError) as exc:
                # A corrupt file must never stop the app from starting.
                log.error("Ignoring invalid settings file (%s); using defaults", type(exc).__name__)
        settings._apply_env_overrides()
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def _apply_env_overrides(self) -> None:
        model = os.environ.get("CIA_CLAUDE_MODEL")
        if model:
            self.ai.model = model
        provider = os.environ.get("CIA_TRANSCRIPTION_PROVIDER")
        if provider:
            try:
                self.transcription.provider = TranscriptionProviderId(provider)
            except ValueError:
                log.warning("Unknown CIA_TRANSCRIPTION_PROVIDER=%s ignored", provider)
        whisper_model = os.environ.get("CIA_WHISPER_MODEL")
        if whisper_model:
            self.transcription.whisper_model = whisper_model


# Bumped whenever the data-flow disclosure text changes so users re-acknowledge it.
CONSENT_VERSION = 1
