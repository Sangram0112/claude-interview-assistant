"""Main window: session controls, live transcript, question/answer cards, and tabs."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app import APP_ID, APP_NAME
from app.ai.practice import PracticeCoach
from app.config import CONSENT_VERSION, Settings
from app.controller import SPEAKER_LABELS, SessionController, SessionStatsView
from app.logging_setup import set_transcript_logging
from app.models import AnswerSuggestion, DetectedQuestion, PipelineState, Speaker, TranscriptEvent
from app.platforms.registry import PLATFORMS, detect_platform_running
from app.security.secrets import SecretName, SecretSource
from app.ui.consent_dialog import ConsentDialog
from app.ui.hotkeys import HOTKEYS, GlobalHotkeys
from app.ui.practice_tab import PracticeTab
from app.ui.privacy_tab import PrivacyTab
from app.ui.profile_tab import ProfileTab
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import stylesheet
from app.ui.widgets import AnswerCard, Chip, LevelMeter, ListeningIndicator

log = logging.getLogger(__name__)

MAX_CARDS = 40


class MainWindow(QMainWindow):
    def __init__(self, controller: SessionController) -> None:
        super().__init__()
        self.c = controller
        self._cards: dict[str, AnswerCard] = {}
        self._interim: dict[Speaker, str] = {}
        self._hotkeys = GlobalHotkeys()
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._hotkeys)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(self.FULL_MIN_WIDTH, 360)
        self.resize(1100, 760)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(6)
        root.addWidget(self._build_header())

        self.banner = QLabel("")
        self.banner.setObjectName("Banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.banner.mousePressEvent = lambda _e: self.banner.hide()  # click to dismiss
        root.addWidget(self.banner)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_live_tab(), "Live copilot")
        coach = PracticeCoach(self.c.claude)
        self.practice = PracticeTab(
            lambda: self.c.settings, self.c.profiles, coach, self.c.runner, self.c.db, self.c.secrets, lambda: self.c.is_active
        )
        self.practice.status.connect(self._status)
        self.tabs.addTab(self.practice, "Practice")
        self.profile_tab = ProfileTab(self.c.profiles, self.c.claude, self.c.runner)
        self.profile_tab.status.connect(self._status)
        self.profile_tab.profile_changed.connect(self._refresh_profile_hint)
        self.tabs.addTab(self.profile_tab, "Profile")
        self.privacy_tab = PrivacyTab(self.c.db, self.c.secrets, lambda: self.c.settings)
        self.privacy_tab.data_deleted.connect(self._on_data_deleted)
        self.tabs.addTab(self.privacy_tab, "Privacy")
        self.tabs.addTab(self._build_setup_tab(), "Platform setup")
        self.tabs.currentChanged.connect(lambda _i: self.privacy_tab.refresh())
        root.addWidget(self.tabs, 1)

        self._wire_controller()
        self._install_shortcuts()
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        self.apply_settings(self.c.settings, initial=True)
        self._restore_geometry()
        self._on_state(PipelineState.IDLE)
        self._refresh_profile_hint()
        self._refresh_key_chip()

    # --- layout -----------------------------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Panel")
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 6, 10, 6)
        self.platform = QComboBox()
        self.platform.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.platform.setMinimumContentsLength(12)
        for pid, info in PLATFORMS.items():
            self.platform.addItem(info.name, pid)
        self.platform.currentIndexChanged.connect(self._platform_changed)
        self.start_btn = QPushButton("▶  Start session")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self._start)
        self.pause_btn = QPushButton("❚❚  Pause")
        self.pause_btn.clicked.connect(self.c.toggle_pause)
        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(self.c.stop_session)
        self.indicator = ListeningIndicator()

        self.sys_chip = Chip("System audio", "off")
        self.sys_meter = LevelMeter()
        self.mic_chip = Chip("Mic", "off")
        self.mic_meter = LevelMeter()
        self.claude_chip = Chip("Claude", "off")
        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("Muted")

        self.ontop_btn = QToolButton()
        self.ontop_btn.setText("📌")
        self.ontop_btn.setToolTip("Keep window on top")
        self.ontop_btn.setCheckable(True)
        self.ontop_btn.toggled.connect(self._set_on_top)
        self.compact_btn = QToolButton()
        self.compact_btn.setText("▣")
        self.compact_btn.setToolTip("Compact view (answers only)")
        self.compact_btn.setCheckable(True)
        self.compact_btn.toggled.connect(self._set_compact)
        settings_btn = QToolButton()
        settings_btn.setText("⚙")
        settings_btn.setToolTip("Settings")
        settings_btn.clicked.connect(self._open_settings)

        # Row 1: session controls + listening indicator (always visible, never squeezed).
        # Row 2: device/API status (hidden in compact view).
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        row.addLayout(outer)
        controls = QHBoxLayout()
        for w in (self.platform, self.start_btn, self.pause_btn, self.stop_btn):
            controls.addWidget(w)
        self.indicator.setMinimumWidth(self.indicator.fontMetrics().horizontalAdvance("❚❚  Paused – not capturing") + 16)
        self.indicator.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        controls.addWidget(self.indicator)
        controls.addStretch(1)
        for w in (self.timer_label, self.ontop_btn, self.compact_btn, settings_btn):
            controls.addWidget(w)
        outer.addLayout(controls)

        self.status_row = QWidget()
        self.status_row.setObjectName("StatusRow")
        status = QHBoxLayout(self.status_row)
        status.setContentsMargins(0, 0, 0, 0)
        for w in (self.sys_chip, self.sys_meter, self.mic_chip, self.mic_meter, self.claude_chip):
            status.addWidget(w)
        status.addStretch(1)
        outer.addWidget(self.status_row)

        self._full_only = [self.platform, self.status_row]
        self._button_text = {
            self.start_btn: ("▶  Start session", "▶"),
            self.stop_btn: ("■  Stop", "■"),
        }
        return header

    def _build_live_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 6, 4, 4)
        self.profile_hint = QLabel("")
        self.profile_hint.setObjectName("InfoBanner")
        self.profile_hint.setWordWrap(True)
        self.profile_hint.hide()
        layout.addWidget(self.profile_hint)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        left = QFrame()
        left.setObjectName("Panel")
        ll = QVBoxLayout(left)
        title = QLabel("Live transcript")
        title.setObjectName("Heading")
        ll.addWidget(title)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setMaximumBlockCount(2000)
        self.transcript.setPlaceholderText("Transcript appears here after you start a session.")
        ll.addWidget(self.transcript, 1)
        self.interim_label = QLabel("")
        self.interim_label.setObjectName("Muted")
        self.interim_label.setWordWrap(True)
        ll.addWidget(self.interim_label)
        ask_row = QHBoxLayout()
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("Type a question to get a suggestion (works without audio)…")
        self.manual.returnPressed.connect(self._ask_manual)
        ask_btn = QPushButton("Ask")
        ask_btn.clicked.connect(self._ask_manual)
        ask_row.addWidget(self.manual, 1)
        ask_row.addWidget(ask_btn)
        ll.addLayout(ask_row)
        self.left_panel = left
        self.split.addWidget(left)

        right = QFrame()
        right.setObjectName("Panel")
        rl = QVBoxLayout(right)
        top = QHBoxLayout()
        qt = QLabel("Questions & suggested answers")
        qt.setObjectName("Heading")
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_cards)
        top.addWidget(qt)
        top.addStretch(1)
        top.addWidget(clear_btn)
        rl.addLayout(top)
        self.cards_area = QScrollArea()
        self.cards_area.setWidgetResizable(True)
        holder = QWidget()
        holder.setObjectName("CardsHolder")
        self.cards_layout = QVBoxLayout(holder)
        self.cards_layout.setContentsMargins(0, 0, 4, 0)
        self.cards_layout.setSpacing(8)
        self.empty_label = QLabel(
            "Detected interview questions appear here as cards with a suggested spoken answer, "
            "key points and an example from your profile.\n\nShortcuts: "
            + " · ".join(f"{label} {action.replace('_', ' ')}" for action, _m, _k, label in HOTKEYS.values())
        )
        self.empty_label.setObjectName("Muted")
        self.empty_label.setWordWrap(True)
        self.cards_layout.addWidget(self.empty_label)
        self.cards_layout.addStretch(1)
        self.cards_area.setWidget(holder)
        rl.addWidget(self.cards_area, 1)
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("Muted")
        rl.addWidget(self.stats_label)
        self.split.addWidget(right)
        self.split.setSizes([400, 700])
        layout.addWidget(self.split, 1)
        return tab

    def _build_setup_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.setup_text = QLabel("")
        self.setup_text.setWordWrap(True)
        self.setup_text.setTextFormat(Qt.TextFormat.RichText)
        self.setup_text.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.setup_text, 1)
        return tab

    # --- wiring -----------------------------------------------------------------------
    def _wire_controller(self) -> None:
        c = self.c
        c.state_changed.connect(self._on_state)
        c.transcript.connect(self._on_transcript)
        c.question_detected.connect(self._on_question)
        c.answer_started.connect(lambda qid: self._cards.get(qid) and self._cards[qid].set_loading())
        c.answer_partial.connect(lambda s: self._on_answer(s, final=False))
        c.answer_ready.connect(lambda s: self._on_answer(s, final=True))
        c.answer_failed.connect(self._on_answer_failed)
        c.status.connect(self._status)
        c.error.connect(self._show_error)
        c.level.connect(self._on_level)
        c.stats_changed.connect(self._on_stats)
        self._hotkeys.triggered.connect(self._on_hotkey)

    def _install_shortcuts(self) -> None:
        # In-window fallbacks for when global hotkeys are disabled or taken.
        for action, _mods, _vk, label in HOTKEYS.values():
            shortcut = QShortcut(QKeySequence(label), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda a=action: self._on_hotkey(a))
        quit_action = QAction(self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    # --- session control ----------------------------------------------------------------
    def _start(self) -> None:
        s = self.c.settings
        if not (s.audio.capture_microphone or s.audio.capture_system_audio):
            self._show_error("Enable at least one audio source in Settings > Audio.")
            return
        dialog = ConsentDialog(s, self.platform.currentData(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        s.privacy.consent_acknowledged_version = CONSENT_VERSION
        s.save()
        self.banner.hide()
        self.transcript.appendPlainText(f"— Session started {time.strftime('%H:%M:%S')} —")
        self.c.start_session(self.platform.currentData())

    def _on_state(self, state: PipelineState) -> None:
        active = state in (PipelineState.STARTING, PipelineState.LISTENING, PipelineState.PAUSED)
        self.start_btn.setEnabled(state in (PipelineState.IDLE, PipelineState.ERROR))
        self.stop_btn.setEnabled(active)
        self.pause_btn.setEnabled(state in (PipelineState.LISTENING, PipelineState.PAUSED))
        self._update_pause_text()
        self.platform.setEnabled(not active)
        self.indicator.set_state(state.value)
        listening = state == PipelineState.LISTENING
        self.setWindowTitle(f"● LISTENING – {APP_NAME}" if listening else (f"Paused – {APP_NAME}" if state == PipelineState.PAUSED else APP_NAME))
        s = self.c.settings.audio
        for chip, enabled, label in ((self.sys_chip, s.capture_system_audio, "System audio"), (self.mic_chip, s.capture_microphone, "Mic")):
            if not enabled:
                chip.set_level("off", f"{label}: off")
            elif listening:
                chip.set_level("ok", f"{label}: live")
            elif state == PipelineState.PAUSED:
                chip.set_level("warn", f"{label}: paused")
            else:
                chip.set_level("off", f"{label}: ready")
        if state == PipelineState.IDLE:
            self._interim.clear()
            self.interim_label.setText("")

    def _on_transcript(self, event: TranscriptEvent) -> None:
        if event.is_final:
            self._interim.pop(event.speaker, None)
            self.transcript.appendPlainText(f"{SPEAKER_LABELS[event.speaker]}: {event.text}")
        else:
            self._interim[event.speaker] = event.text
        self.interim_label.setText("\n".join(f"{SPEAKER_LABELS[s]} (live): {t}" for s, t in self._interim.items()))

    def _on_level(self, speaker: Speaker, level: float) -> None:
        (self.sys_meter if speaker == Speaker.INTERVIEWER else self.mic_meter).push(level)

    def _ask_manual(self) -> None:
        text = self.manual.text().strip()
        if text:
            self.manual.clear()
            self.c.ask_manual(text)

    # --- cards ------------------------------------------------------------------------
    def _on_question(self, question: DetectedQuestion) -> None:
        self.empty_label.hide()
        from app.ai.prompts import default_mode_for

        card = AnswerCard(question, default_mode_for(question.category, self.c.settings.ai.default_mode))
        card.regenerate_requested.connect(lambda qid, mode: self.c.generate(qid, mode))
        card.copied.connect(lambda: self._status("Answer copied to clipboard"))
        for other in self._cards.values():
            other.set_latest(False)
        card.set_latest(True)
        self._cards[question.id] = card
        self.cards_layout.insertWidget(0, card)
        self.cards_area.verticalScrollBar().setValue(0)
        while len(self._cards) > MAX_CARDS:
            oldest_id = next(iter(self._cards))
            self._cards.pop(oldest_id).deleteLater()

    def _on_answer(self, suggestion: AnswerSuggestion, final: bool) -> None:
        card = self._cards.get(suggestion.question_id)
        if card is not None:
            card.show_suggestion(suggestion, final)

    def _on_answer_failed(self, question_id: str, message: str) -> None:
        card = self._cards.get(question_id)
        if card is not None:
            card.show_error(message)
        self._refresh_key_chip(error=message)

    def _clear_cards(self) -> None:
        for card in self._cards.values():
            card.deleteLater()
        self._cards.clear()
        self.empty_label.show()

    def _latest_card(self) -> AnswerCard | None:
        qid = self.c.latest_question_id()
        return self._cards.get(qid) if qid else None

    # --- hotkeys ----------------------------------------------------------------------
    def _on_hotkey(self, action: str) -> None:
        if action == "toggle_pause":
            self.c.toggle_pause()
        elif action == "regenerate":
            card = self._latest_card()
            mode = card.mode.currentData() if card else None
            self.c.regenerate_latest(mode)
        elif action == "toggle_window":
            # Minimise instead of hiding so the taskbar entry (with its LISTENING title) stays visible.
            if self.isMinimized() or not self.isActiveWindow():
                self.showNormal()
                self.raise_()
                self.activateWindow()
            else:
                self.showMinimized()
        elif action == "copy_latest":
            card = self._latest_card()
            if card and card.answer_text():
                QGuiApplication.clipboard().setText(card.answer_text())
                self._status("Latest answer copied")

    # --- settings & view ----------------------------------------------------------------
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.c.settings, self.c.secrets, self.c.claude, self.c.runner, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self.c.claude.invalidate()  # keys may have changed even if cancelled
        if accepted:
            new = dialog.result_settings()
            new.save()
            self.c.apply_settings(new)
            self.apply_settings(new)
            if self.c.is_active:
                self._status("Audio/transcription changes apply to the next session")
        self._refresh_key_chip()

    def apply_settings(self, s: Settings, initial: bool = False) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(s.ui.font_scale))
        set_transcript_logging(s.privacy.log_transcripts)
        if initial:
            idx = self.platform.findData(s.ui.platform)
            self.platform.setCurrentIndex(max(0, idx))
            self._platform_changed()
            self.compact_btn.setChecked(s.ui.compact)
        self.ontop_btn.blockSignals(True)
        self.ontop_btn.setChecked(s.ui.always_on_top)
        self.ontop_btn.blockSignals(False)
        self._set_on_top(s.ui.always_on_top, persist=False)
        if s.ui.global_hotkeys:
            self._hotkeys.register(int(self.winId()))
            if self._hotkeys.failed:
                self._status("Some shortcuts are used by another app: " + ", ".join(self._hotkeys.failed))
        else:
            self._hotkeys.unregister()

    def _set_on_top(self, on: bool, persist: bool = True) -> None:
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
        if persist:
            self.c.settings.ui.always_on_top = on
            self.c.settings.save()

    _NARROW_PX = 900
    FULL_MIN_WIDTH = 760
    COMPACT_MIN_WIDTH = 380

    def _apply_density(self) -> None:
        """Short, icon-only session buttons in compact view or narrow windows, so the
        listening indicator always has room to be read."""
        short = getattr(self, "_compact_view", False) or self.width() < self._NARROW_PX
        if short == getattr(self, "_compact", None):
            return
        self._compact = short
        for btn, (full, label) in self._button_text.items():
            btn.setText(label if short else full)
            btn.setToolTip(full.split("  ", 1)[1] if short else "")
        self._update_pause_text()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if hasattr(self, "_button_text"):
            self._apply_density()

    def _update_pause_text(self) -> None:
        paused = self.c.state == PipelineState.PAUSED
        if getattr(self, "_compact", False):
            self.pause_btn.setText("▶" if paused else "❚❚")
            self.pause_btn.setToolTip("Resume" if paused else "Pause")
        else:
            self.pause_btn.setText("▶  Resume" if paused else "❚❚  Pause")
            self.pause_btn.setToolTip("")

    def _set_compact(self, on: bool) -> None:
        self.left_panel.setVisible(not on)
        self.tabs.tabBar().setVisible(not on)
        for w in self._full_only:
            w.setVisible(not on)
        self._compact_view = on
        self._apply_density()
        self.setMinimumWidth(self.COMPACT_MIN_WIDTH if on else self.FULL_MIN_WIDTH)
        if on:
            self.tabs.setCurrentIndex(0)
            self.resize(min(self.width(), 520), self.height())
        elif self.width() < self.FULL_MIN_WIDTH:
            self.resize(self.FULL_MIN_WIDTH, self.height())
        self.c.settings.ui.compact = on
        self.c.settings.save()

    def _platform_changed(self, *_args) -> None:
        pid = self.platform.currentData()
        info = PLATFORMS.get(pid)
        if info is None:
            return
        self.c.settings.ui.platform = pid
        running = detect_platform_running(pid)
        running_text = {True: "detected running", False: "not detected", None: "n/a"}[running]
        tips = "".join(f"<li>{t}</li>" for t in info.setup_tips)
        self.setup_text.setText(
            f"<h3>{info.name}</h3><p><b>Audio capture:</b> {info.capture.value}<br>"
            f"<b>App process:</b> {running_text}</p><ol>{tips}</ol>"
            f"<p><b>Native platform API:</b> {info.native_api.value}. {info.native_api_notes}</p>"
            "<p>If transcripts stay empty: check that the meeting audio plays through the device "
            "selected in Settings &gt; Audio, and that the System audio meter moves when others speak.</p>"
        )

    def _refresh_profile_hint(self) -> None:
        _pid, profile = self.c.profiles.active()
        if profile.is_empty():
            self.profile_hint.setText(
                "No resume or profile yet – answers will use placeholders instead of your real experience. "
                "Add your details in the Profile tab."
            )
            self.profile_hint.show()
        elif profile.completeness() < 0.5:
            self.profile_hint.setText("Your profile is incomplete; add projects, experience and the job description for better answers.")
            self.profile_hint.show()
        else:
            self.profile_hint.hide()

    def _refresh_key_chip(self, error: str | None = None) -> None:
        _value, source = self.c.secrets.lookup(SecretName.ANTHROPIC_API_KEY)
        if error and ("API key" in error or "credit" in error or "Billing" in error):
            self.claude_chip.set_level("error", "Claude: check key/billing")
            self.claude_chip.setToolTip(error)
        elif source == SecretSource.MISSING:
            self.claude_chip.set_level("warn", "Claude: no key")
            self.claude_chip.setToolTip("Add your Anthropic API key in Settings > Claude API.")
        else:
            self.claude_chip.set_level("ok", "Claude: ready")
            self.claude_chip.setToolTip(f"API key from {source.value}. Model: {self.c.settings.ai.model}")

    # --- misc -------------------------------------------------------------------------
    def _on_stats(self, stats: SessionStatsView) -> None:
        self.stats_label.setText(
            f"Questions {stats.questions} · answers {stats.answers} · tokens in {stats.input_tokens:,} "
            f"(+{stats.cache_read_tokens:,} cached) / out {stats.output_tokens:,} · est. ${stats.estimated_cost_usd:.3f}"
            + (f" · last {stats.last_latency_ms / 1000:.1f}s" if stats.last_latency_ms else "")
        )

    def _tick(self) -> None:
        started = self.c.stats.started_at
        if started and self.c.is_active:
            secs = int(time.time() - started)
            self.timer_label.setText(f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}" if secs >= 3600 else f"{secs // 60:02d}:{secs % 60:02d}")

    def _status(self, message: str) -> None:
        self.statusBar().showMessage(message, 6000)

    def _show_error(self, message: str) -> None:
        self.banner.setText(f"⚠  {message}   (click to dismiss)")
        self.banner.show()
        log.warning("UI error shown: %s", message[:200])
        self._refresh_key_chip(error=message)

    def _on_data_deleted(self) -> None:
        self.profile_tab.reload()
        self._refresh_profile_hint()
        self._refresh_key_chip()

    def _restore_geometry(self) -> None:
        geometry = QSettings(APP_ID, "window").value("geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        QSettings(APP_ID, "window").setValue("geometry", self.saveGeometry())
        self._hotkeys.unregister()
        self.practice.stop()
        self.c.shutdown()
        self.c.settings.save()
        super().closeEvent(event)


__all__ = ["MainWindow"]
