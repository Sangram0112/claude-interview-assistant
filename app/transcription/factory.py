"""Build audio sources and the transcription provider from settings."""

from __future__ import annotations

from app.audio.base import AudioSource
from app.config import Settings, TranscriptionProviderId
from app.security.secrets import SecretName, SecretStore
from app.transcription.base import TranscriptionProvider


def build_sources(settings: Settings, microphone: bool | None = None, system_audio: bool | None = None) -> list[AudioSource]:
    from app.audio.pyaudio_sources import LoopbackSource, MicrophoneSource

    mic = settings.audio.capture_microphone if microphone is None else microphone
    loop = settings.audio.capture_system_audio if system_audio is None else system_audio
    sources: list[AudioSource] = []
    if loop:
        sources.append(LoopbackSource(settings.audio.loopback_device, settings.audio.chunk_ms))
    if mic:
        sources.append(MicrophoneSource(settings.audio.microphone_device, settings.audio.chunk_ms))
    return sources


def build_provider(settings: Settings, secrets: SecretStore) -> TranscriptionProvider:
    if settings.transcription.provider == TranscriptionProviderId.DEEPGRAM:
        from app.transcription.deepgram import DeepgramProvider

        return DeepgramProvider(
            settings.transcription,
            secrets.get(SecretName.DEEPGRAM_API_KEY),
            silence_ms=settings.audio.silence_ms,
        )
    from app.transcription.whisper_local import LocalWhisperProvider

    return LocalWhisperProvider(settings.transcription, settings.audio)


def provider_display_name(settings: Settings) -> tuple[str, bool]:
    """(name, sends audio externally) without constructing the provider."""
    if settings.transcription.provider == TranscriptionProviderId.DEEPGRAM:
        return "Deepgram (cloud streaming)", True
    return "Local Whisper (offline, on this PC)", False
