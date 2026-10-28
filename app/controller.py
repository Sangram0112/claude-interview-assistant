"""Session orchestration: pipeline -> question detection -> Claude answers -> UI signals.

All heavy work happens off the GUI thread; the controller only emits Qt signals, which Qt
delivers on the GUI thread (queued connections), so widgets are never touched from workers.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from app.ai.client import ClaudeService
from app.ai.errors import AIError, map_exception
from app.ai.prompts import default_mode_for
from app.audio.base import AudioSource
from app.config import MODEL_PRICING, Retention, Settings
from app.models import (
    AnswerMode,
    AnswerSuggestion,
    DetectedQuestion,
    PipelineState,
    Speaker,
    TranscriptEvent,
)
from app.profiles.repository import ProfileRepository
from app.questions.dedup import QuestionDeduplicator
from app.questions.detector import QuestionDetector, classify
from app.security.secrets import SecretStore
from app.storage.database import Database
from app.transcription.base import TranscriptionProvider
from app.transcription.pipeline import PipelineError, TranscriptionPipeline
from app.ui.async_runner import AsyncRunner

log = logging.getLogger(__name__)

SPEAKER_LABELS = {Speaker.INTERVIEWER: "Interviewer", Speaker.CANDIDATE: "You"}
_PARTIAL_EMIT_INTERVAL_S = 0.08


@dataclass
class SessionStatsView:
    started_at: float | None = None
    questions: int = 0
    answers: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    last_latency_ms: int = 0
    by_model: dict[str, tuple[int, int]] = field(default_factory=dict)

    def add_usage(self, s: AnswerSuggestion) -> None:
        self.answers += 1
        self.input_tokens += s.input_tokens
        self.cache_read_tokens += s.cache_read_tokens
        self.output_tokens += s.output_tokens
        self.last_latency_ms = s.latency_ms
        price_in, price_out = MODEL_PRICING.get(s.model, MODEL_PRICING.get(_base_model(s.model), (0.0, 0.0)))
        # Cache reads bill at ~10% of the input rate.
        self.estimated_cost_usd += (
            s.input_tokens * price_in + s.cache_read_tokens * price_in * 0.1 + s.output_tokens * price_out
        ) / 1_000_000


def _base_model(model: str) -> str:
    return model.split("@")[0]


SourcesFactory = Callable[[Settings], list[AudioSource]]
ProviderFactory = Callable[[Settings, SecretStore], TranscriptionProvider]


class SessionController(QObject):
    state_changed = Signal(object)  # PipelineState
    transcript = Signal(object)  # TranscriptEvent
    question_detected = Signal(object)  # DetectedQuestion
    answer_started = Signal(str)  # question id
    answer_partial = Signal(object)  # AnswerSuggestion
    answer_ready = Signal(object)  # AnswerSuggestion
    answer_failed = Signal(str, str)  # question id, message
    status = Signal(str)
    error = Signal(str)
    level = Signal(object, float)  # Speaker, rms
    stats_changed = Signal(object)  # SessionStatsView

    def __init__(
        self,
        settings: Settings,
        db: Database,
        secrets: SecretStore,
        claude: ClaudeService,
        runner: AsyncRunner,
        sources_factory: SourcesFactory | None = None,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.db = db
        self.secrets = secrets
        self.claude = claude
        self.runner = runner
        self.profiles = ProfileRepository(db)
        if sources_factory is None or provider_factory is None:
            from app.transcription.factory import build_provider, build_sources

            sources_factory = sources_factory or build_sources
            provider_factory = provider_factory or build_provider
        self._sources_factory = sources_factory
        self._provider_factory = provider_factory

        self._pipeline: TranscriptionPipeline | None = None
        self._state = PipelineState.IDLE
        self._session_id: int | None = None
        self._platform: str | None = None
        self.stats = SessionStatsView()
        self._questions: dict[str, DetectedQuestion] = {}
        self._answers: dict[str, AnswerSuggestion] = {}
        self._tasks: dict[str, Future] = {}
        self._recent: deque[tuple[str, str]] = deque(maxlen=max(1, settings.ai.recent_context_turns))
        self._detector = self._new_detector()

    # --- properties ------------------------------------------------------------------
    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state in (PipelineState.STARTING, PipelineState.LISTENING, PipelineState.PAUSED)

    def latest_question_id(self) -> str | None:
        if not self._questions:
            return None
        return max(self._questions.values(), key=lambda q: q.timestamp).id

    def question(self, question_id: str) -> DetectedQuestion | None:
        return self._questions.get(question_id)

    def _new_detector(self) -> QuestionDetector:
        d = self.settings.detection
        speakers = set()
        if d.detect_from_interviewer:
            speakers.add(Speaker.INTERVIEWER)
        if d.detect_from_candidate:
            speakers.add(Speaker.CANDIDATE)
        return QuestionDetector(
            deduplicator=QuestionDeduplicator(threshold=d.duplicate_threshold, window_s=d.duplicate_window_s),
            speakers=frozenset(speakers),
            min_words=d.min_words,
        )

    def _set_state(self, state: PipelineState) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    # --- session lifecycle -------------------------------------------------------------
    def start_session(self, platform_id: str) -> None:
        if self.is_active:
            return
        self._platform = platform_id
        self._detector = self._new_detector()
        self._recent = deque(maxlen=max(1, self.settings.ai.recent_context_turns))
        self.stats = SessionStatsView(started_at=time.time())
        self.stats_changed.emit(self.stats)
        try:
            sources = self._sources_factory(self.settings)
            provider = self._provider_factory(self.settings, self.secrets)
            pipeline = TranscriptionPipeline(
                sources,
                provider,
                on_event=self._on_transcript_event,
                on_status=self.status.emit,
                on_error=self._on_pipeline_error,
                on_level=lambda spk, lvl: self.level.emit(spk, lvl),
                on_state=self._on_pipeline_state,
            )
        except PipelineError as exc:
            self.error.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - e.g. audio backend missing
            log.exception("Could not prepare session")
            self.error.emit(f"Could not prepare the session: {exc}")
            return

        self._pipeline = pipeline
        self._set_state(PipelineState.STARTING)
        self.runner.run_in_thread(pipeline.start, self._on_started, self._on_start_failed, name="pipeline-start")

    def _on_started(self, _result: object) -> None:
        if self._pipeline is None:
            return
        self._session_id = self.db.start_session("live", self._platform, self.settings.ai.model)
        self.status.emit("Listening")

    def _on_start_failed(self, exc: BaseException) -> None:
        self._pipeline = None
        self._set_state(PipelineState.ERROR)
        self.error.emit(str(exc))

    def stop_session(self) -> None:
        pipeline = self._pipeline
        if pipeline is None:
            self._set_state(PipelineState.IDLE)
            return
        self._pipeline = None
        self._set_state(PipelineState.STOPPING)
        # pipeline.stop() closes the audio devices first, then drains the provider.
        self.runner.run_in_thread(pipeline.stop, lambda _r: self._finish_session(), lambda e: self._finish_session(), name="pipeline-stop")

    def _finish_session(self) -> None:
        if self._session_id is not None:
            self.db.end_session(self._session_id, self.stats.questions, self.stats.input_tokens, self.stats.output_tokens)
            self._session_id = None
        self._set_state(PipelineState.IDLE)
        self.status.emit("Session stopped")

    def pause(self) -> None:
        if self._pipeline is not None:
            self.runner.run_in_thread(self._pipeline.pause, name="pipeline-pause", on_error=lambda e: self.error.emit(str(e)))

    def resume(self) -> None:
        if self._pipeline is not None:
            self.runner.run_in_thread(self._pipeline.resume, name="pipeline-resume", on_error=lambda e: self.error.emit(str(e)))

    def toggle_pause(self) -> None:
        if self._state == PipelineState.LISTENING:
            self.pause()
        elif self._state == PipelineState.PAUSED:
            self.resume()

    def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._pipeline is not None:
            pipeline, self._pipeline = self._pipeline, None
            pipeline.stop()
            self._finish_session()

    # --- pipeline callbacks (any thread) -------------------------------------------------
    def _on_pipeline_state(self, state: PipelineState) -> None:
        self.runner.on_gui(lambda: self._apply_pipeline_state(state))

    def _apply_pipeline_state(self, state: PipelineState) -> None:
        # Once the user pressed Stop (or start failed) the controller owns the state:
        # IDLE is emitted by _finish_session after the session record is closed.
        if self._pipeline is None:
            return
        if state == PipelineState.IDLE:
            # Provider stopped itself after a fatal error.
            self._pipeline = None
            self._finish_session()
            return
        self._set_state(state)

    def _on_pipeline_error(self, message: str, fatal: bool) -> None:
        self.runner.on_gui(lambda: self.error.emit(message))

    def _on_transcript_event(self, event: TranscriptEvent) -> None:
        self.runner.on_gui(lambda: self._handle_transcript(event))

    def _handle_transcript(self, event: TranscriptEvent) -> None:
        self.transcript.emit(event)
        if not event.is_final:
            return
        self._recent.append((SPEAKER_LABELS[event.speaker], event.text))
        if self._session_id is not None and self.settings.privacy.retention != Retention.NONE:
            self.db.add_transcript_line(self._session_id, event.speaker.value, event.text, event.timestamp)
        for question in self._detector.process(event):
            self._add_question(question)

    # --- questions & answers -----------------------------------------------------------
    def ask_manual(self, text: str) -> DetectedQuestion | None:
        """User typed a question (or the transcript missed one)."""
        text = text.strip()
        if not text:
            return None
        question = DetectedQuestion(text=text, category=classify(text), speaker=Speaker.INTERVIEWER)
        self._detector.deduplicator.remember(text)
        self._add_question(question)
        return question

    def _add_question(self, question: DetectedQuestion) -> None:
        self._questions[question.id] = question
        self.stats.questions += 1
        self.question_detected.emit(question)
        self.stats_changed.emit(self.stats)
        if self.settings.detection.auto_generate:
            self.generate(question.id)

    def generate(self, question_id: str, mode: AnswerMode | None = None) -> None:
        question = self._questions.get(question_id)
        if question is None:
            return
        previous = self._tasks.pop(question_id, None)
        if previous is not None:
            previous.cancel()
        mode = mode or default_mode_for(question.category, self.settings.ai.default_mode)
        _pid, profile = self.profiles.active()
        length = profile.preferred_answer_length if not profile.is_empty() else self.settings.ai.answer_length
        recent = list(self._recent)
        # Drop the line that contains the question itself; it is sent separately.
        if recent and question.text.lower().rstrip("?") in recent[-1][1].lower():
            recent = recent[:-1]

        last_emit = [0.0]

        def on_partial(partial: AnswerSuggestion) -> None:  # asyncio thread
            now = time.monotonic()
            if now - last_emit[0] >= _PARTIAL_EMIT_INTERVAL_S:
                last_emit[0] = now
                self.answer_partial.emit(partial)

        self.answer_started.emit(question_id)
        coro = self.claude.stream_answer(question, profile, mode, length, recent, on_partial)
        self._tasks[question_id] = self.runner.submit(
            coro,
            on_success=lambda s, qid=question_id: self._on_answer(qid, s),
            on_error=lambda e, qid=question_id: self._on_answer_error(qid, e),
        )

    def regenerate_latest(self, mode: AnswerMode | None = None) -> None:
        qid = self.latest_question_id()
        if qid:
            self.generate(qid, mode)

    def _on_answer(self, question_id: str, suggestion: AnswerSuggestion) -> None:
        self._tasks.pop(question_id, None)
        self._answers[question_id] = suggestion
        self.stats.add_usage(suggestion)
        self.stats_changed.emit(self.stats)
        question = self._questions.get(question_id)
        if question and self._session_id is not None and self.settings.privacy.retention != Retention.NONE:
            self.db.add_qa(self._session_id, question.text, question.category.value, suggestion.spoken_answer)
        self.answer_ready.emit(suggestion)

    def _on_answer_error(self, question_id: str, exc: BaseException) -> None:
        self._tasks.pop(question_id, None)
        error = exc if isinstance(exc, AIError) else map_exception(exc)
        log.warning("Answer generation failed: %s", error.kind.value)
        self.answer_failed.emit(question_id, error.message)

    def apply_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.claude.invalidate()
        if not self.is_active:
            self._detector = self._new_detector()


__all__ = ["SessionController", "SessionStatsView", "SPEAKER_LABELS"]
