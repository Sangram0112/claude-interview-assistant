"""Connects audio sources to a transcription provider.

Lifecycle guarantees:
* No source is opened before :meth:`start`.
* :meth:`pause` closes the audio devices (nothing is captured while paused).
* :meth:`stop` closes devices first, then stops the provider, so no audio is captured or
  sent after Stop is pressed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np

from app.audio.base import AudioCaptureError, AudioSource
from app.audio.processing import TARGET_RATE, resample, rms
from app.models import PipelineState, Speaker, TranscriptEvent
from app.transcription.base import TranscriptionError, TranscriptionProvider

log = logging.getLogger(__name__)

LevelCallback = Callable[[Speaker, float], None]


class PipelineError(Exception):
    pass


class TranscriptionPipeline:
    def __init__(
        self,
        sources: list[AudioSource],
        provider: TranscriptionProvider,
        on_event: Callable[[TranscriptEvent], None],
        on_status: Callable[[str], None] = lambda _m: None,
        on_error: Callable[[str, bool], None] = lambda _m, _f: None,
        on_level: LevelCallback = lambda _s, _l: None,
        on_state: Callable[[PipelineState], None] = lambda _s: None,
        poll_interval_s: float = 0.1,
    ) -> None:
        if not sources:
            raise PipelineError("Select at least one audio source (microphone or system audio).")
        self._sources = sources
        self._provider = provider
        self._on_event = on_event
        self._on_status = on_status
        self._on_error = on_error
        self._on_level = on_level
        self._on_state = on_state
        self._poll_interval_s = poll_interval_s
        self._state = PipelineState.IDLE
        self._lock = threading.RLock()
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def sources(self) -> list[AudioSource]:
        return list(self._sources)

    def _set_state(self, state: PipelineState) -> None:
        self._state = state
        self._on_state(state)

    # --- control ---------------------------------------------------------------------
    def start(self) -> None:
        """Blocking: loads the provider then opens devices. Call from a worker thread."""
        with self._lock:
            if self._state not in (PipelineState.IDLE, PipelineState.ERROR):
                return
            self._set_state(PipelineState.STARTING)
            try:
                self._provider.start(self._handle_event, self._on_status, self._handle_provider_error)
                self._open_sources()
            except (TranscriptionError, AudioCaptureError) as exc:
                self._close_sources()
                self._provider.stop()
                self._set_state(PipelineState.ERROR)
                raise PipelineError(str(exc)) from exc
            self._start_polling()
            self._set_state(PipelineState.LISTENING)

    def pause(self) -> None:
        with self._lock:
            if self._state != PipelineState.LISTENING:
                return
            self._close_sources()
            self._provider.flush()
            self._set_state(PipelineState.PAUSED)

    def resume(self) -> None:
        with self._lock:
            if self._state != PipelineState.PAUSED:
                return
            try:
                self._open_sources()
            except AudioCaptureError as exc:
                self._set_state(PipelineState.ERROR)
                raise PipelineError(str(exc)) from exc
            self._set_state(PipelineState.LISTENING)

    def stop(self) -> None:
        with self._lock:
            if self._state == PipelineState.IDLE:
                return
            self._set_state(PipelineState.STOPPING)
            self._close_sources()  # first: capture ends immediately
            self._stop_polling()
            try:
                self._provider.stop()
            finally:
                self._set_state(PipelineState.IDLE)

    # --- internals -------------------------------------------------------------------
    def _open_sources(self) -> None:
        opened: list[AudioSource] = []
        try:
            for source in self._sources:
                source.start(self._handle_frames, self._handle_source_error)
                opened.append(source)
        except AudioCaptureError:
            for source in opened:
                source.stop()
            raise

    def _close_sources(self) -> None:
        for source in self._sources:
            try:
                source.stop()
            except Exception:  # noqa: BLE001
                log.exception("Failed to stop %s", source.label)

    def _handle_frames(self, source: AudioSource, samples: np.ndarray, rate: int) -> None:
        if self._state != PipelineState.LISTENING:
            return
        audio = resample(samples, rate, TARGET_RATE)
        self._on_level(source.speaker, rms(audio))
        self._provider.feed(source.speaker, audio)

    def _handle_source_error(self, source: AudioSource, message: str) -> None:
        log.error("Audio source %s failed", source.label)
        self._on_error(message, False)

    def _handle_event(self, event: TranscriptEvent) -> None:
        if self._state in (PipelineState.LISTENING, PipelineState.PAUSED, PipelineState.STOPPING):
            self._on_event(event)

    def _handle_provider_error(self, message: str, fatal: bool) -> None:
        self._on_error(message, fatal)
        if fatal:
            threading.Thread(target=self.stop, daemon=True).start()

    def _start_polling(self) -> None:
        self._poll_stop.clear()

        def loop() -> None:
            while not self._poll_stop.wait(self._poll_interval_s):
                try:
                    self._provider.poll()
                except Exception:  # noqa: BLE001
                    log.exception("Provider poll failed")

        self._poll_thread = threading.Thread(target=loop, name="stt-poll", daemon=True)
        self._poll_thread.start()

    def _stop_polling(self) -> None:
        self._poll_stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2)
            self._poll_thread = None
