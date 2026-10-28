"""Practice mode: Claude asks role-specific questions, then evaluates typed or spoken answers."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.ai.errors import AIError, map_exception
from app.ai.practice import PracticeCoach, PracticeEvaluation, PracticeQuestion
from app.config import Retention, Settings
from app.models import PipelineState, TranscriptEvent
from app.profiles.repository import ProfileRepository
from app.security.secrets import SecretStore
from app.storage.database import Database
from app.transcription.pipeline import TranscriptionPipeline
from app.ui.async_runner import AsyncRunner
from app.ui.widgets import ListeningIndicator, Spinner

log = logging.getLogger(__name__)

FOCUS_OPTIONS = [
    ("Mixed interview", "mixed: intro, behavioral, technical, project and career questions"),
    ("Behavioral (STAR)", "behavioral questions answerable with STAR"),
    ("Technical", "technical questions for the role"),
    ("Projects on my resume", "questions about the candidate's specific projects"),
    ("SQL & Python", "SQL and Python questions"),
    ("React & APIs", "React and REST API development questions"),
    ("Data analyst", "data analyst questions: SQL, Excel/BI, statistics, case questions"),
    ("HR & career", "HR questions: strengths, weaknesses, motivation, career goals"),
]


class PracticeTab(QWidget):
    status = Signal(str)

    def __init__(
        self,
        settings: Callable[[], Settings],
        repo: ProfileRepository,
        coach: PracticeCoach,
        runner: AsyncRunner,
        db: Database,
        secrets: SecretStore,
        live_session_active: Callable[[], bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._repo = repo
        self._coach = coach
        self._runner = runner
        self._db = db
        self._secrets = secrets
        self._live_active = live_session_active
        self._questions: list[PracticeQuestion] = []
        self._pipeline: TranscriptionPipeline | None = None
        self._session_id: int | None = None

        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.role = QLineEdit()
        self.role.setMinimumWidth(140)
        self.role.setPlaceholderText("Role (defaults to your profile's target job title)")
        self.focus = QComboBox()
        for label, value in FOCUS_OPTIONS:
            self.focus.addItem(label, value)
        self.count = QSpinBox()
        self.count.setRange(1, 15)
        self.count.setValue(5)
        self.generate_btn = QPushButton("Generate questions")
        self.generate_btn.setObjectName("Primary")
        self.generate_btn.clicked.connect(self._generate)
        controls.addWidget(self.role, 2)
        controls.addWidget(self.focus, 1)
        controls.addWidget(QLabel("Count"))
        controls.addWidget(self.count)
        controls.addWidget(self.generate_btn)
        outer.addLayout(controls)
        self.gen_spinner = Spinner("Claude is writing questions…")
        outer.addWidget(self.gen_spinner)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.list = QListWidget()
        self.list.setMinimumWidth(170)
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._select)
        custom_box = QWidget()
        cl = QVBoxLayout(custom_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.list, 1)
        self.custom_q = QLineEdit()
        self.custom_q.setPlaceholderText("…or type your own practice question and press Enter")
        self.custom_q.returnPressed.connect(self._add_custom)
        cl.addWidget(self.custom_q)
        split.addWidget(custom_box)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.question_label = QLabel("Generate or type a question to begin.")
        self.question_label.setObjectName("Question")
        self.question_label.setWordWrap(True)
        rl.addWidget(self.question_label)
        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        rl.addWidget(self.hint)
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText("Type your answer, or press 'Answer verbally' and speak.")
        rl.addWidget(self.answer, 1)
        row = QHBoxLayout()
        self.record_btn = QPushButton("🎤 Answer verbally")
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._toggle_recording)
        self.indicator = ListeningIndicator()
        self.evaluate_btn = QPushButton("Evaluate my answer")
        self.evaluate_btn.setObjectName("Primary")
        self.evaluate_btn.clicked.connect(self._evaluate)
        row.addWidget(self.record_btn)
        row.addWidget(self.indicator)
        row.addStretch(1)
        row.addWidget(self.evaluate_btn)
        rl.addLayout(row)
        self.eval_spinner = Spinner("Claude is evaluating…")
        rl.addWidget(self.eval_spinner)

        self.feedback_area = QScrollArea()
        self.feedback_area.setWidgetResizable(True)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.RichText)
        self.feedback.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.feedback.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.feedback_area.setWidget(self.feedback)
        rl.addWidget(self.feedback_area, 2)
        self.copy_improved = QPushButton("Copy improved answer")
        self.copy_improved.setEnabled(False)
        self.copy_improved.clicked.connect(self._copy_improved)
        rl.addWidget(self.copy_improved, alignment=Qt.AlignmentFlag.AlignRight)
        split.addWidget(right)
        split.setSizes([320, 680])
        outer.addWidget(split, 1)
        self._improved = ""

    # --- questions --------------------------------------------------------------------
    def _generate(self) -> None:
        _pid, profile = self._repo.active()
        self.generate_btn.setEnabled(False)
        self.gen_spinner.start()

        def done(result) -> None:
            questions, _tin, _tout = result
            self.gen_spinner.stop()
            self.generate_btn.setEnabled(True)
            self._questions = list(questions)
            self.list.clear()
            for q in self._questions:
                self.list.addItem(QListWidgetItem(q.question))
            if self._questions:
                self.list.setCurrentRow(0)

        def fail(exc: BaseException) -> None:
            self.gen_spinner.stop()
            self.generate_btn.setEnabled(True)
            self._show_error(exc)

        self._runner.submit(
            self._coach.generate_questions(profile, self.role.text().strip(), self.count.value(), self.focus.currentData()),
            done,
            fail,
        )

    def _add_custom(self) -> None:
        text = self.custom_q.text().strip()
        if not text:
            return
        self._questions.append(PracticeQuestion(question=text, category="general", what_a_strong_answer_covers=""))
        self.list.addItem(QListWidgetItem(text))
        self.list.setCurrentRow(self.list.count() - 1)
        self.custom_q.clear()

    def _select(self, row: int) -> None:
        if 0 <= row < len(self._questions):
            q = self._questions[row]
            self.question_label.setText(q.question)
            self.hint.setText(f"A strong answer covers: {q.what_a_strong_answer_covers}" if q.what_a_strong_answer_covers else "")
            self.answer.clear()
            self.feedback.setText("")
            self.copy_improved.setEnabled(False)

    def _current_question(self) -> str | None:
        row = self.list.currentRow()
        return self._questions[row].question if 0 <= row < len(self._questions) else None

    # --- verbal answers ---------------------------------------------------------------
    def _toggle_recording(self, on: bool) -> None:
        if on:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self) -> None:
        if self._live_active():
            QMessageBox.information(self, "Live session running", "Stop the live session before recording a practice answer.")
            self.record_btn.setChecked(False)
            return
        from app.transcription.factory import build_provider, build_sources

        settings = self._settings()
        try:
            sources = build_sources(settings, microphone=True, system_audio=False)
            provider = build_provider(settings, self._secrets)
            self._pipeline = TranscriptionPipeline(
                sources,
                provider,
                on_event=lambda ev: self._runner.on_gui(lambda: self._on_text(ev)),
                on_status=lambda m: self._runner.on_gui(lambda: self.status.emit(m)),
                on_error=lambda m, _f: self._runner.on_gui(lambda: self.status.emit(m)),
                on_state=lambda st: self._runner.on_gui(lambda: self.indicator.set_state(st.value)),
            )
        except Exception as exc:  # noqa: BLE001
            self.record_btn.setChecked(False)
            QMessageBox.warning(self, "Microphone unavailable", str(exc))
            return
        self.record_btn.setText("Stop recording")
        self._runner.run_in_thread(self._pipeline.start, on_error=self._record_failed, name="practice-start")

    def _record_failed(self, exc: BaseException) -> None:
        self._pipeline = None
        self.record_btn.blockSignals(True)
        self.record_btn.setChecked(False)
        self.record_btn.blockSignals(False)
        self.record_btn.setText("🎤 Answer verbally")
        self.indicator.set_state("error")
        QMessageBox.warning(self, "Could not start recording", str(exc))

    def _stop_recording(self) -> None:
        self.record_btn.setText("🎤 Answer verbally")
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            self._runner.run_in_thread(pipeline.stop, lambda _r: self.indicator.set_state(PipelineState.IDLE.value), name="practice-stop")

    def _on_text(self, event: TranscriptEvent) -> None:
        if not event.is_final:
            return  # only commit final text into the editable answer
        base = self.answer.toPlainText().rstrip()
        self.answer.setPlainText(f"{base} {event.text}".strip())

    def stop(self) -> None:
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None

    # --- evaluation -------------------------------------------------------------------
    def _evaluate(self) -> None:
        question = self._current_question()
        answer = self.answer.toPlainText().strip()
        if not question or not answer:
            QMessageBox.information(self, "Nothing to evaluate", "Select a question and provide an answer first.")
            return
        if self.record_btn.isChecked():
            self.record_btn.setChecked(False)
        _pid, profile = self._repo.active()
        self.evaluate_btn.setEnabled(False)
        self.eval_spinner.start()

        def done(result) -> None:
            evaluation, _tin, _tout = result
            self.eval_spinner.stop()
            self.evaluate_btn.setEnabled(True)
            self._render(evaluation)
            self._persist(question, answer, evaluation)

        def fail(exc: BaseException) -> None:
            self.eval_spinner.stop()
            self.evaluate_btn.setEnabled(True)
            self._show_error(exc)

        self._runner.submit(self._coach.evaluate(profile, question, answer, self.role.text().strip()), done, fail)

    def _render(self, e: PracticeEvaluation) -> None:
        def bar(score: int, label: str) -> str:
            if label == "Technical accuracy" and score == 0:
                return f"<tr><td>{label}</td><td style='color:#9aa3b2'>n/a</td></tr>"
            color = "#3fb27f" if score >= 4 else "#e0a43a" if score == 3 else "#e5534b"
            return f"<tr><td>{label}</td><td style='color:{color}'><b>{'●' * score}{'○' * (5 - score)}</b> {score}/5</td></tr>"

        def ul(items: list[str]) -> str:
            return "<ul>" + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>" if items else "<p>-</p>"

        missing = (
            f"<p style='color:#e0a43a'><b>Add these personal details:</b> {_esc(', '.join(e.missing_personal_details))}</p>"
            if e.missing_personal_details else ""
        )
        self._improved = e.improved_answer
        self.copy_improved.setEnabled(bool(e.improved_answer))
        self.feedback.setText(
            "<table cellspacing=4>"
            + bar(e.overall, "Overall") + bar(e.relevance, "Relevance") + bar(e.clarity, "Clarity")
            + bar(e.technical_accuracy, "Technical accuracy") + bar(e.structure, "Structure")
            + "</table>"
            + f"<p><b>Strengths</b></p>{ul(e.strengths)}<p><b>Improve</b></p>{ul(e.improvements)}"
            + f"<p><b>Improved example answer</b></p><p>{_esc(e.improved_answer)}</p>{missing}"
        )

    def _persist(self, question: str, answer: str, e: PracticeEvaluation) -> None:
        if self._settings().privacy.retention == Retention.NONE:
            return
        if self._session_id is None:
            self._session_id = self._db.start_session("practice", None, self._settings().ai.model)
        self._db.add_qa(self._session_id, question, "practice", answer, e.model_dump_json())

    def _copy_improved(self) -> None:
        QGuiApplication.clipboard().setText(self._improved)
        self.status.emit("Improved answer copied")

    def _show_error(self, exc: BaseException) -> None:
        err = exc if isinstance(exc, AIError) else map_exception(exc)
        QMessageBox.warning(self, "Claude request failed", err.message)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


__all__ = ["PracticeTab"]
