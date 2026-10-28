"""Pure-numpy audio utilities: format conversion, resampling, and utterance segmentation."""

from __future__ import annotations

import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

import numpy as np

TARGET_RATE = 16_000


def pcm16_bytes_to_mono(data: bytes, channels: int) -> np.ndarray:
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return interleaved_to_mono(samples, channels)


def float32_bytes_to_mono(data: bytes, channels: int) -> np.ndarray:
    samples = np.frombuffer(data, dtype=np.float32)
    return interleaved_to_mono(samples, channels)


def interleaved_to_mono(samples: np.ndarray, channels: int) -> np.ndarray:
    if channels <= 1:
        return samples.astype(np.float32, copy=False)
    usable = len(samples) - (len(samples) % channels)
    return samples[:usable].reshape(-1, channels).mean(axis=1).astype(np.float32)


def resample(samples: np.ndarray, src_rate: int, dst_rate: int = TARGET_RATE) -> np.ndarray:
    """Resample with a box low-pass + linear interpolation.

    Good enough for speech recognition (energy above 8 kHz is irrelevant for 16 kHz STT)
    and avoids a SciPy dependency.
    """
    if src_rate == dst_rate or len(samples) == 0:
        return samples.astype(np.float32, copy=False)
    if src_rate > dst_rate:
        width = int(math.ceil(src_rate / dst_rate))
        if width > 1:
            kernel = np.ones(width, dtype=np.float32) / width
            samples = np.convolve(samples, kernel, mode="same")
    duration = len(samples) / src_rate
    n_out = max(1, int(round(duration * dst_rate)))
    x_old = np.linspace(0.0, duration, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, duration, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def float_to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def rms(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


@dataclass(slots=True)
class SegmentEvent:
    utterance_id: str
    audio: np.ndarray
    is_final: bool


@dataclass
class UtteranceSegmenter:
    """Energy-based voice activity detection that splits a stream into utterances.

    * Tracks an adaptive noise floor so steady background noise isn't treated as speech.
    * Emits interim segments every ``interim_interval_s`` while speech continues.
    * Emits a final segment after ``silence_ms`` of quiet, after ``max_utterance_s``,
      or - via :meth:`poll` - when audio stops arriving entirely (WASAPI loopback delivers
      no frames while nothing is playing).
    """

    sample_rate: int = TARGET_RATE
    frame_ms: int = 30
    silence_ms: int = 700
    max_utterance_s: float = 15.0
    min_rms: float = 0.006
    noise_multiplier: float = 3.0
    pre_roll_ms: int = 300
    min_speech_ms: int = 250
    interim_interval_s: float | None = 1.5

    _pending: np.ndarray = field(default_factory=lambda: np.zeros(0, np.float32), repr=False)
    _pre_roll: list[np.ndarray] = field(default_factory=list, repr=False)
    _speech: list[np.ndarray] = field(default_factory=list, repr=False)
    _in_speech: bool = False
    _silence_frames: int = 0
    _speech_frames: int = 0
    noise_window_ms: int = 3000
    _levels: deque[float] = field(default_factory=deque, repr=False)
    _utterance_id: str = ""
    _last_interim_samples: int = 0
    _last_audio_time: float = 0.0

    @property
    def frame_len(self) -> int:
        return int(self.sample_rate * self.frame_ms / 1000)

    @property
    def noise_floor(self) -> float:
        # 10th percentile of recent frame energy: speech has gaps, so this tracks the
        # background level even while someone talks, and from the very first frame.
        return float(np.percentile(self._levels, 10)) if self._levels else 0.0

    @property
    def threshold(self) -> float:
        return max(self.min_rms, self.noise_floor * self.noise_multiplier)

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, samples: np.ndarray, now: float | None = None) -> list[SegmentEvent]:
        self._last_audio_time = now if now is not None else time.monotonic()
        self._pending = np.concatenate([self._pending, samples.astype(np.float32, copy=False)])
        events: list[SegmentEvent] = []
        n = self.frame_len
        while len(self._pending) >= n:
            frame, self._pending = self._pending[:n], self._pending[n:]
            event = self._process_frame(frame)
            if event:
                events.append(event)
        return events

    def poll(self, now: float | None = None) -> SegmentEvent | None:
        """Finalize the current utterance if no audio has arrived for ``silence_ms``."""
        now = now if now is not None else time.monotonic()
        if self._in_speech and (now - self._last_audio_time) * 1000 >= self.silence_ms:
            return self._finish()
        return None

    def flush(self) -> SegmentEvent | None:
        """Finalize whatever speech is buffered (used on pause/stop)."""
        return self._finish() if self._in_speech else None

    # ------------------------------------------------------------------------------
    def _process_frame(self, frame: np.ndarray) -> SegmentEvent | None:
        level = rms(frame)
        self._levels.append(level)
        while len(self._levels) > max(1, self.noise_window_ms // self.frame_ms):
            self._levels.popleft()
        is_speech = level >= self.threshold
        silence_limit = max(1, self.silence_ms // self.frame_ms)
        pre_roll_frames = max(1, self.pre_roll_ms // self.frame_ms)

        if not self._in_speech:
            if not is_speech:
                self._pre_roll.append(frame)
                if len(self._pre_roll) > pre_roll_frames:
                    self._pre_roll.pop(0)
                return None
            self._in_speech = True
            self._utterance_id = uuid.uuid4().hex[:12]
            self._speech = list(self._pre_roll)
            self._pre_roll = []
            self._silence_frames = 0
            self._speech_frames = 0
            self._last_interim_samples = 0

        self._speech.append(frame)
        if is_speech:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        total_samples = sum(len(f) for f in self._speech)
        if self._silence_frames >= silence_limit or total_samples >= self.max_utterance_s * self.sample_rate:
            return self._finish()

        if self.interim_interval_s:
            step = int(self.interim_interval_s * self.sample_rate)
            if total_samples - self._last_interim_samples >= step:
                self._last_interim_samples = total_samples
                return SegmentEvent(self._utterance_id, np.concatenate(self._speech), is_final=False)
        return None

    def _finish(self) -> SegmentEvent | None:
        speech_ms = self._speech_frames * self.frame_ms
        audio = np.concatenate(self._speech) if self._speech else np.zeros(0, np.float32)
        utterance_id = self._utterance_id
        self._in_speech = False
        self._speech = []
        self._silence_frames = 0
        self._speech_frames = 0
        if speech_ms < self.min_speech_ms:
            return None  # a click or cough, not an utterance
        return SegmentEvent(utterance_id, audio, is_final=True)
