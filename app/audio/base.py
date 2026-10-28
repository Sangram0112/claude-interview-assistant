"""Audio capture provider interface.

Sources deliver mono float32 frames in [-1, 1] at their native sample rate. Resampling to
the transcription rate is done by the pipeline, so sources stay simple and swappable
(microphone, Windows loopback, or a future authorised meeting-audio integration).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from app.models import Speaker

# (source, mono float32 samples, sample_rate)
FrameCallback = Callable[["AudioSource", np.ndarray, int], None]
ErrorCallback = Callable[["AudioSource", str], None]


class AudioCaptureError(Exception):
    """User-presentable capture failure (device missing, access denied, ...)."""


@dataclass(slots=True, frozen=True)
class AudioDeviceInfo:
    index: int
    name: str
    sample_rate: int
    channels: int
    is_loopback: bool
    is_default: bool = False


class AudioSource(ABC):
    def __init__(self, speaker: Speaker, label: str) -> None:
        self.speaker = speaker
        self.label = label

    @property
    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def start(self, on_frames: FrameCallback, on_error: ErrorCallback) -> None:
        """Begin capturing. Must not block. Raise AudioCaptureError if the device can't open."""

    @abstractmethod
    def stop(self) -> None:
        """Stop capturing immediately and release the device. Idempotent."""
