"""Speech-to-text provider interface.

Providers receive mono float32 audio at 16 kHz tagged with a speaker, and emit
:class:`app.models.TranscriptEvent` objects through the callback given to ``start``.
Callbacks may be invoked from any thread.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

import numpy as np

from app.models import Speaker, TranscriptEvent

EventCallback = Callable[[TranscriptEvent], None]
StatusCallback = Callable[[str], None]
ErrorCallback = Callable[[str, bool], None]  # (message, fatal)


class TranscriptionError(Exception):
    """User-presentable transcription failure."""


class TranscriptionProvider(ABC):
    #: Display name, e.g. "Local Whisper (offline)".
    display_name: str = "provider"
    #: True if audio leaves this computer.
    sends_audio_externally: bool = False

    @abstractmethod
    def start(self, on_event: EventCallback, on_status: StatusCallback, on_error: ErrorCallback) -> None:
        """Prepare the provider (load models, open connections). May block briefly; raise
        TranscriptionError on configuration problems."""

    @abstractmethod
    def feed(self, speaker: Speaker, samples: np.ndarray) -> None:
        """Queue 16 kHz mono float32 audio. Must be fast and non-blocking."""

    def poll(self) -> None:  # noqa: B027 - optional hook
        """Called periodically (~10x/s) so providers can finalize utterances when audio stops."""

    def flush(self) -> None:  # noqa: B027 - optional hook
        """Finalize buffered speech (pause/stop)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop and release resources. Idempotent."""
