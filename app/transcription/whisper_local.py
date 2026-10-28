"""Offline speech-to-text with faster-whisper (CTranslate2). Audio never leaves the PC.

The model is downloaded once from Hugging Face on first use into the app's data folder.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
from dataclasses import dataclass

import numpy as np

from app.audio.processing import TARGET_RATE, UtteranceSegmenter
from app.config import AudioSettings, TranscriptionSettings
from app.logging_setup import transcript_preview
from app.models import Speaker, TranscriptEvent
from app.paths import models_dir
from app.transcription.base import (
    ErrorCallback,
    EventCallback,
    StatusCallback,
    TranscriptionError,
    TranscriptionProvider,
)

log = logging.getLogger(__name__)

# Phrases Whisper commonly hallucinates on near-silent audio.
_HALLUCINATIONS = {
    "you",
    "thank you",
    "thank you.",
    "thanks for watching",
    "thanks for watching!",
    "bye",
    "bye.",
    "okay.",
    "so",
    ".",
}


def _load_error_message(settings: TranscriptionSettings, exc: BaseException) -> str:
    name = settings.whisper_model
    if getattr(exc, "winerror", None) == 206 or "too long" in str(exc).lower():
        return (
            f"Could not load speech model '{name}': the model folder path exceeds the Windows "
            "path-length limit. Set the CIA_MODELS_DIR environment variable to a short folder "
            "such as C:\\cia-models, or enable Windows long paths, then try again."
        )
    lower = str(exc).lower()
    if settings.whisper_device == "cuda" and ("cuda" in lower or "cudnn" in lower or "cublas" in lower):
        return (
            f"Could not load speech model '{name}' on the GPU ({exc}). Install CUDA 12 and cuDNN, "
            "or switch Whisper device to CPU in Settings > Transcription."
        )
    return (
        f"Could not load speech model '{name}': {exc}. The first run needs internet access to "
        "download the model."
    )


@dataclass(slots=True)
class _Job:
    speaker: Speaker
    utterance_id: str
    audio: np.ndarray
    is_final: bool


class LocalWhisperProvider(TranscriptionProvider):
    display_name = "Local Whisper (offline, on this PC)"
    sends_audio_externally = False

    def __init__(self, settings: TranscriptionSettings, audio: AudioSettings, model_factory=None) -> None:
        self._settings = settings
        self._audio = audio
        self._model_factory = model_factory  # injectable for tests
        self._model = None
        self._segmenters: dict[Speaker, UtteranceSegmenter] = {}
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._seg_lock = threading.Lock()
        self._on_event: EventCallback | None = None
        self._on_error: ErrorCallback | None = None

    # --- lifecycle -----------------------------------------------------------------
    def start(self, on_event: EventCallback, on_status: StatusCallback, on_error: ErrorCallback) -> None:
        self._on_event, self._on_error = on_event, on_error
        if self._model is None:
            on_status(f"Loading speech model '{self._settings.whisper_model}' (first run downloads it)…")
            self._model = self._load_model()
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="whisper-worker", daemon=True)
        self._worker.start()
        on_status("Speech model ready")

    def _load_model(self):
        started = time.monotonic()
        try:
            if self._model_factory is not None:
                model = self._model_factory()
            else:
                # Windows without Developer Mode can't symlink; the HF cache copies instead.
                os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
                from faster_whisper import WhisperModel

                model = WhisperModel(
                    self._settings.whisper_model,
                    device=self._settings.whisper_device,
                    compute_type=self._settings.whisper_compute_type,
                    download_root=str(models_dir()),
                )
        except ImportError as exc:
            raise TranscriptionError("faster-whisper is not installed.") from exc
        except Exception as exc:  # noqa: BLE001 - network/download/CUDA problems
            raise TranscriptionError(_load_error_message(self._settings, exc)) from exc
        log.info("Loaded Whisper model %s in %.1fs", self._settings.whisper_model, time.monotonic() - started)
        return model

    def stop(self) -> None:
        self.flush()
        self._stop.set()
        self._jobs.put(None)
        if self._worker is not None:
            self._worker.join(timeout=10)
            self._worker = None
        with self._seg_lock:
            self._segmenters.clear()
        # Drop anything left so no audio is processed after Stop.
        while not self._jobs.empty():
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break

    # --- audio in ------------------------------------------------------------------
    def _segmenter(self, speaker: Speaker) -> UtteranceSegmenter:
        seg = self._segmenters.get(speaker)
        if seg is None:
            seg = UtteranceSegmenter(
                sample_rate=TARGET_RATE,
                frame_ms=self._audio.chunk_ms,
                silence_ms=self._audio.silence_ms,
                max_utterance_s=self._audio.max_utterance_s,
                min_rms=self._audio.min_rms,
                interim_interval_s=self._settings.interim_interval_s if self._settings.interim_results else None,
            )
            self._segmenters[speaker] = seg
        return seg

    def feed(self, speaker: Speaker, samples: np.ndarray) -> None:
        if self._stop.is_set():
            return
        with self._seg_lock:
            events = self._segmenter(speaker).feed(samples)
        for ev in events:
            self._enqueue(_Job(speaker, ev.utterance_id, ev.audio, ev.is_final))

    def poll(self) -> None:
        with self._seg_lock:
            pending = [(spk, seg.poll()) for spk, seg in self._segmenters.items()]
        for speaker, ev in pending:
            if ev is not None:
                self._enqueue(_Job(speaker, ev.utterance_id, ev.audio, True))

    def flush(self) -> None:
        with self._seg_lock:
            pending = [(spk, seg.flush()) for spk, seg in self._segmenters.items()]
        for speaker, ev in pending:
            if ev is not None:
                self._enqueue(_Job(speaker, ev.utterance_id, ev.audio, True))

    def _enqueue(self, job: _Job) -> None:
        self._jobs.put(job)

    # --- worker --------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._jobs.get()
            if job is None:
                break
            # Skip stale interim jobs: if a newer job for the same utterance is queued,
            # transcribing this one would only waste CPU and add latency.
            if not job.is_final and self._newer_job_queued(job):
                continue
            try:
                text = self._transcribe(job.audio, final=job.is_final)
            except Exception as exc:  # noqa: BLE001
                log.exception("Transcription failed")
                if self._on_error:
                    self._on_error(f"Transcription error: {exc}", False)
                continue
            if text and self._on_event and not self._stop.is_set():
                log.debug("Transcribed %s (%s)", transcript_preview(text), "final" if job.is_final else "interim")
                self._on_event(TranscriptEvent(job.speaker, text, job.is_final, job.utterance_id))

    def _newer_job_queued(self, job: _Job) -> bool:
        with self._jobs.mutex:
            return any(j is not None and j.utterance_id == job.utterance_id for j in self._jobs.queue)

    def _transcribe(self, audio: np.ndarray, final: bool) -> str:
        if self._model is None or len(audio) < TARGET_RATE * 0.3:
            return ""
        segments, _info = self._model.transcribe(
            audio,
            language=self._settings.language or None,
            beam_size=5 if final else 1,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,  # our segmenter already did VAD
        )
        parts = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > 0.6 and getattr(seg, "avg_logprob", 0.0) < -1.0:
                continue
            parts.append(seg.text.strip())
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if text.lower() in _HALLUCINATIONS:
            return ""
        return text
