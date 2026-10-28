"""Reusable widgets: status chips, listening indicator, level meter, answer cards."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models import AnswerMode, AnswerSuggestion, ContextQuality, DetectedQuestion
from app.ui.theme import COLORS

MODE_LABELS = {
    AnswerMode.CONCISE: "Concise",
    AnswerMode.DETAILED: "Detailed",
    AnswerMode.STAR: "STAR",
    AnswerMode.TECHNICAL: "Technical",
    AnswerMode.BEGINNER: "Beginner-friendly",
}
CATEGORY_LABELS = {
    "intro": "Tell me about yourself",
    "behavioral": "Behavioral",
    "technical": "Technical",
    "project": "Project",
    "sql": "SQL",
    "python": "Python",
    "react_api": "React / API",
    "data_analyst": "Data analysis",
    "scenario": "Scenario",
    "career": "Career / strengths",
    "general": "General",
}


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Chip(QLabel):
    def __init__(self, text: str = "", level: str = "off", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.set_level(level)

    def set_level(self, level: str, text: str | None = None) -> None:
        if text is not None:
            self.setText(text)
        self.setProperty("level", level)
        repolish(self)


class ListeningIndicator(QLabel):
    """Always-visible capture indicator. Pulses red while audio is being captured."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on = False
        self._phase = False
        self._timer = QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._blink)
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        """state: idle | starting | listening | paused | error"""
        self._on = state == "listening"
        if self._on:
            self._timer.start()
        else:
            self._timer.stop()
        text, color = {
            "idle": ("○  Not listening", COLORS["muted"]),
            "starting": ("◌  Starting…", COLORS["warn"]),
            "listening": ("●  LISTENING", COLORS["rec"]),
            "paused": ("❚❚  Paused – not capturing", COLORS["warn"]),
            "stopping": ("◌  Stopping…", COLORS["warn"]),
            "error": ("✕  Capture error", COLORS["error"]),
        }.get(state, ("○  Not listening", COLORS["muted"]))
        self.setText(text)
        self._color = color
        self._apply(True)

    def _blink(self) -> None:
        self._phase = not self._phase
        self._apply(self._phase)

    def _apply(self, bright: bool) -> None:
        color = self._color if bright or not self._on else "#7a2a26"
        self.setStyleSheet(f"color: {color}; font-weight: 700; padding: 2px 6px;")


class LevelMeter(QProgressBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 100)
        self.setTextVisible(False)
        self.setFixedWidth(70)
        self.setFixedHeight(8)
        self._decay = QTimer(self)
        self._decay.setInterval(120)
        self._decay.timeout.connect(lambda: self.setValue(max(0, self.value() - 8)))
        self._decay.start()

    def push(self, rms: float) -> None:
        # Map RMS (0..~0.3 for speech) onto 0..100 with a perceptual curve.
        value = int(min(100, (rms / 0.15) ** 0.5 * 100))
        if value > self.value():
            self.setValue(value)


class Spinner(QLabel):
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text: str = "Generating…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._i = 0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._tick)
        self.setObjectName("Muted")
        self.hide()

    def start(self, text: str | None = None) -> None:
        if text:
            self._text = text
        self._timer.start()
        self._tick()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._i = (self._i + 1) % len(self._FRAMES)
        self.setText(f"{self._FRAMES[self._i]}  {self._text}")


class AnswerCard(QFrame):
    regenerate_requested = Signal(str, object)  # question id, AnswerMode
    copied = Signal()

    def __init__(self, question: DetectedQuestion, default_mode: AnswerMode, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.question = question
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.category = Chip(CATEGORY_LABELS.get(question.category.value, question.category.value))
        self.quality = Chip("Context: …", "off")
        self.quality.setToolTip(
            "How well your profile supports a personalised answer. Low means the answer relies on "
            "placeholders - add details in the Profile tab."
        )
        header.addWidget(self.category)
        header.addWidget(self.quality)
        header.addStretch(1)
        layout.addLayout(header)

        self.question_label = QLabel(question.text)
        self.question_label.setObjectName("Question")
        self.question_label.setWordWrap(True)
        self.question_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.question_label)
        if question.context:
            ctx = QLabel(f"Lead-in: {question.context}")
            ctx.setObjectName("Muted")
            ctx.setWordWrap(True)
            layout.addWidget(ctx)

        self.spinner = Spinner("Claude is drafting an answer…")
        layout.addWidget(self.spinner)

        self.answer = QLabel("")
        self.answer.setObjectName("Answer")
        self.answer.setWordWrap(True)
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.answer)

        self.points = QLabel("")
        self.points.setWordWrap(True)
        self.points.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.points)

        self.example = QLabel("")
        self.example.setWordWrap(True)
        self.example.setObjectName("Muted")
        layout.addWidget(self.example)

        self.personalize = QLabel("")
        self.personalize.setWordWrap(True)
        self.personalize.setObjectName("InfoBanner")
        self.personalize.hide()
        layout.addWidget(self.personalize)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setObjectName("Banner")
        self.error.hide()
        layout.addWidget(self.error)

        actions = QHBoxLayout()
        self.copy_btn = QPushButton("Copy answer")
        self.copy_btn.clicked.connect(self._copy)
        self.copy_btn.setEnabled(False)
        self.mode = QComboBox()
        for mode, label in MODE_LABELS.items():
            self.mode.addItem(label, mode)
        self.mode.setCurrentIndex(list(MODE_LABELS).index(default_mode))
        self.regen_btn = QPushButton("Regenerate")
        self.regen_btn.clicked.connect(lambda: self.regenerate_requested.emit(self.question.id, self.mode.currentData()))
        self.meta = QLabel("")
        self.meta.setObjectName("Muted")
        actions.addWidget(self.copy_btn)
        actions.addWidget(self.mode)
        actions.addWidget(self.regen_btn)
        actions.addStretch(1)
        actions.addWidget(self.meta)
        layout.addLayout(actions)

    def set_latest(self, latest: bool) -> None:
        self.setProperty("latest", "true" if latest else "false")
        repolish(self)

    def set_loading(self) -> None:
        self.error.hide()
        self.spinner.start()
        self.regen_btn.setEnabled(False)

    def show_suggestion(self, s: AnswerSuggestion, final: bool) -> None:
        self.answer.setText(s.spoken_answer)
        if s.key_points:
            items = "".join(f"<li>{_escape(p)}</li>" for p in s.key_points)
            self.points.setText(f"<b>Key points</b><ul style='margin:2px 0 0 -18px'>{items}</ul>")
        if s.resume_example:
            self.example.setText(f"From your profile: {s.resume_example}")
        if final:
            self.spinner.stop()
            self.regen_btn.setEnabled(True)
            self.copy_btn.setEnabled(bool(s.spoken_answer))
            level = {
                ContextQuality.HIGH: ("ok", "Context: high"),
                ContextQuality.MEDIUM: ("warn", "Context: medium"),
                ContextQuality.LOW: ("error", "Context: low"),
            }.get(s.context_quality, ("off", "Context: n/a"))
            self.quality.set_level(*level)
            if s.needs_personalization:
                self.personalize.setText("Needs personalisation: " + ", ".join(s.needs_personalization))
                self.personalize.show()
            else:
                self.personalize.hide()
            secs = s.latency_ms / 1000
            self.meta.setText(f"{secs:.1f}s · {s.output_tokens} tok")

    def show_error(self, message: str) -> None:
        self.spinner.stop()
        self.regen_btn.setEnabled(True)
        self.error.setText(message)
        self.error.show()

    def answer_text(self) -> str:
        return self.answer.text()

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.answer.text())
        self.copied.emit()


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
