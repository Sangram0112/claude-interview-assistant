"""Settings: API keys (stored in Windows Credential Manager), model, audio, privacy, UI."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.ai.client import ClaudeService
from app.ai.errors import AIError, map_exception
from app.config import (
    SUPPORTED_MODELS,
    AnswerLength,
    Effort,
    Retention,
    Settings,
    TranscriptionProviderId,
)
from app.models import AnswerMode
from app.security.secrets import SecretName, SecretSource, SecretStore, mask_secret
from app.ui.async_runner import AsyncRunner
from app.ui.widgets import MODE_LABELS

log = logging.getLogger(__name__)

WHISPER_MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "large-v3", "distil-large-v3"]


def _combo(items: list[tuple[str, object]], current: object) -> QComboBox:
    box = QComboBox()
    for label, data in items:
        box.addItem(label, data)
    idx = box.findData(current)
    box.setCurrentIndex(max(0, idx))
    return box


class SecretField(QWidget):
    """Masked key entry. The stored key is never displayed; only a short hint is shown."""

    def __init__(self, secrets: SecretStore, name: SecretName, placeholder: str) -> None:
        super().__init__()
        self._secrets, self._name = secrets, name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(placeholder)
        self.save_btn = QPushButton("Save key")
        self.delete_btn = QPushButton("Remove")
        self.delete_btn.setObjectName("Danger")
        row.addWidget(self.edit, 1)
        row.addWidget(self.save_btn)
        row.addWidget(self.delete_btn)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.save_btn.clicked.connect(self._save)
        self.delete_btn.clicked.connect(self._delete)
        self.refresh()

    def refresh(self) -> None:
        value, source = self._secrets.lookup(self._name)
        if source == SecretSource.MISSING:
            self.status.setText("Not configured.")
        else:
            self.status.setText(f"Configured ({mask_secret(value)}) – from {source.value}.")
        self.delete_btn.setEnabled(source == SecretSource.KEYRING)

    def _save(self) -> None:
        value = self.edit.text().strip()
        if not value:
            return
        try:
            self._secrets.set(self._name, value)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not save key", f"The key could not be stored: {type(exc).__name__}")
            return
        self.edit.clear()
        self.refresh()

    def _delete(self) -> None:
        self._secrets.delete(self._name)
        self.refresh()


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, secrets: SecretStore, claude: ClaudeService, runner: AsyncRunner, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self._settings = settings.model_copy(deep=True)
        self._secrets = secrets
        self._claude = claude
        self._runner = runner

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._ai_tab(), "Claude API")
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._stt_tab(), "Transcription")
        tabs.addTab(self._detection_tab(), "Detection")
        tabs.addTab(self._privacy_tab(), "Privacy")
        tabs.addTab(self._ui_tab(), "Interface")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # --- tabs ------------------------------------------------------------------------
    def _ai_tab(self) -> QWidget:
        ai = self._settings.ai
        w = QWidget()
        form = QFormLayout(w)
        self.anthropic_key = SecretField(self._secrets, SecretName.ANTHROPIC_API_KEY, "sk-ant-… (from console.anthropic.com)")
        form.addRow("Anthropic API key", self.anthropic_key)
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("Test connection")
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result, 1)
        form.addRow("", self._wrap(test_row))
        self.test_btn.clicked.connect(self._test_connection)

        self.model = QComboBox()
        self.model.setEditable(True)
        for model_id, label in SUPPORTED_MODELS.items():
            self.model.addItem(f"{label}  [{model_id}]", model_id)
        idx = self.model.findData(ai.model)
        if idx >= 0:
            self.model.setCurrentIndex(idx)
        else:
            self.model.setEditText(ai.model)
        form.addRow("Model", self.model)

        self.effort = _combo([("Low (fastest)", Effort.LOW), ("Medium", Effort.MEDIUM), ("High (most thorough)", Effort.HIGH)], ai.effort)
        self.effort.setToolTip(
            "Controls how much Claude reasons before answering. Replaces 'temperature', which the "
            "Claude API no longer accepts (API version 2026-10-21)."
        )
        form.addRow("Reasoning effort", self.effort)
        self.length = _combo([("Short (~30 s)", AnswerLength.SHORT), ("Medium (~60 s)", AnswerLength.MEDIUM), ("Long (~90 s)", AnswerLength.LONG)], ai.answer_length)
        form.addRow("Default answer length", self.length)
        self.mode = _combo([(label, mode) for mode, label in MODE_LABELS.items()], ai.default_mode)
        form.addRow("Default answer mode", self.mode)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(1024, 64000)
        self.max_tokens.setSingleStep(1000)
        self.max_tokens.setValue(ai.max_tokens)
        form.addRow("Max tokens", self.max_tokens)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(5, 600)
        self.timeout.setValue(ai.request_timeout_s)
        self.timeout.setSuffix(" s")
        form.addRow("Request timeout", self.timeout)
        self.retries = QSpinBox()
        self.retries.setRange(0, 6)
        self.retries.setValue(ai.max_retries)
        form.addRow("Automatic retries", self.retries)
        self.context_turns = QSpinBox()
        self.context_turns.setRange(0, 30)
        self.context_turns.setValue(ai.recent_context_turns)
        form.addRow("Recent transcript lines sent", self.context_turns)
        self.fallback = QCheckBox("Retry safety-declined requests on Anthropic's recommended fallback model")
        self.fallback.setChecked(ai.refusal_fallback)
        form.addRow("Refusal fallback", self.fallback)
        return w

    def _audio_tab(self) -> QWidget:
        a = self._settings.audio
        w = QWidget()
        form = QFormLayout(w)
        self.cap_sys = QCheckBox("Capture system audio (other participants)")
        self.cap_sys.setChecked(a.capture_system_audio)
        self.cap_mic = QCheckBox("Capture my microphone")
        self.cap_mic.setChecked(a.capture_microphone)
        form.addRow(self.cap_sys)
        form.addRow(self.cap_mic)
        self.loop_dev = QComboBox()
        self.mic_dev = QComboBox()
        form.addRow("System-audio device", self.loop_dev)
        form.addRow("Microphone", self.mic_dev)
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._load_devices)
        form.addRow("", refresh)
        self.device_error = QLabel("")
        self.device_error.setObjectName("Muted")
        self.device_error.setWordWrap(True)
        form.addRow("", self.device_error)
        self.silence = QSpinBox()
        self.silence.setRange(200, 3000)
        self.silence.setSingleStep(50)
        self.silence.setSuffix(" ms")
        self.silence.setValue(a.silence_ms)
        form.addRow("End-of-sentence silence", self.silence)
        self.max_utt = QDoubleSpinBox()
        self.max_utt.setRange(3, 60)
        self.max_utt.setSuffix(" s")
        self.max_utt.setValue(a.max_utterance_s)
        form.addRow("Max chunk length", self.max_utt)
        self.min_rms = QDoubleSpinBox()
        self.min_rms.setDecimals(3)
        self.min_rms.setRange(0.0, 0.5)
        self.min_rms.setSingleStep(0.001)
        self.min_rms.setValue(a.min_rms)
        self.min_rms.setToolTip("Minimum loudness treated as speech. Raise it in noisy rooms.")
        form.addRow("Speech threshold", self.min_rms)
        self._load_devices()
        return w

    def _load_devices(self) -> None:
        a = self._settings.audio
        self.loop_dev.clear()
        self.mic_dev.clear()
        self.loop_dev.addItem("Default output device", None)
        self.mic_dev.addItem("Windows default microphone", None)
        try:
            from app.audio.pyaudio_sources import list_input_devices, list_loopback_devices

            for d in list_loopback_devices():
                self.loop_dev.addItem(d.name + (" (default)" if d.is_default else ""), d.index)
            for d in list_input_devices():
                self.mic_dev.addItem(d.name + (" (default)" if d.is_default else ""), d.index)
            self.device_error.setText("")
        except Exception as exc:  # noqa: BLE001
            self.device_error.setText(f"Could not list audio devices: {exc}")
        for box, current in ((self.loop_dev, a.loopback_device), (self.mic_dev, a.microphone_device)):
            idx = box.findData(current)
            box.setCurrentIndex(max(0, idx))

    def _stt_tab(self) -> QWidget:
        t = self._settings.transcription
        w = QWidget()
        form = QFormLayout(w)
        self.provider = _combo(
            [("Local Whisper – offline, audio stays on this PC", TranscriptionProviderId.LOCAL_WHISPER),
             ("Deepgram – cloud streaming (needs API key)", TranscriptionProviderId.DEEPGRAM)],
            t.provider,
        )
        form.addRow("Provider", self.provider)
        self.whisper_model = QComboBox()
        self.whisper_model.setEditable(True)
        self.whisper_model.addItems(WHISPER_MODELS)
        self.whisper_model.setCurrentText(t.whisper_model)
        self.whisper_model.setToolTip("tiny.en/base.en run well on most CPUs. Larger models are more accurate but slower.")
        form.addRow("Whisper model", self.whisper_model)
        self.whisper_device = _combo([("CPU", "cpu"), ("NVIDIA GPU (CUDA)", "cuda")], t.whisper_device)
        form.addRow("Whisper device", self.whisper_device)
        self.interim = QCheckBox("Show live (interim) text while someone is speaking")
        self.interim.setChecked(t.interim_results)
        form.addRow(self.interim)
        self.deepgram_key = SecretField(self._secrets, SecretName.DEEPGRAM_API_KEY, "Deepgram API key")
        form.addRow("Deepgram API key", self.deepgram_key)
        self.deepgram_model = QLineEdit(t.deepgram_model)
        form.addRow("Deepgram model", self.deepgram_model)
        return w

    def _detection_tab(self) -> QWidget:
        d = self._settings.detection
        w = QWidget()
        form = QFormLayout(w)
        self.det_int = QCheckBox("Detect questions from system audio (interviewer)")
        self.det_int.setChecked(d.detect_from_interviewer)
        self.det_cand = QCheckBox("Detect questions from my microphone")
        self.det_cand.setChecked(d.detect_from_candidate)
        self.auto_gen = QCheckBox("Generate an answer automatically for each new question")
        self.auto_gen.setChecked(d.auto_generate)
        form.addRow(self.det_int)
        form.addRow(self.det_cand)
        form.addRow(self.auto_gen)
        self.min_words = QSpinBox()
        self.min_words.setRange(1, 20)
        self.min_words.setValue(d.min_words)
        form.addRow("Minimum words in a question", self.min_words)
        self.dup = QDoubleSpinBox()
        self.dup.setRange(0.5, 1.0)
        self.dup.setSingleStep(0.02)
        self.dup.setValue(d.duplicate_threshold)
        form.addRow("Duplicate similarity threshold", self.dup)
        return w

    def _privacy_tab(self) -> QWidget:
        p = self._settings.privacy
        w = QWidget()
        form = QFormLayout(w)
        self.retention = _combo(
            [("Don't save transcripts or answers", Retention.NONE), ("Keep for 1 day", Retention.DAYS_1),
             ("Keep for 7 days", Retention.DAYS_7), ("Keep for 30 days", Retention.DAYS_30),
             ("Keep until I delete them", Retention.FOREVER)],
            p.retention,
        )
        form.addRow("Transcript retention", self.retention)
        self.log_tx = QCheckBox("Include transcript text in debug logs (not recommended)")
        self.log_tx.setChecked(p.log_transcripts)
        form.addRow(self.log_tx)
        note = QLabel("Raw audio is never saved. API keys are stored in Windows Credential Manager, never in files.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def _ui_tab(self) -> QWidget:
        u = self._settings.ui
        w = QWidget()
        form = QFormLayout(w)
        self.on_top = QCheckBox("Keep window on top")
        self.on_top.setChecked(u.always_on_top)
        self.hotkeys = QCheckBox("Enable global shortcuts (Ctrl+Alt+P / R / H / C)")
        self.hotkeys.setChecked(u.global_hotkeys)
        self.font_scale = QDoubleSpinBox()
        self.font_scale.setRange(0.7, 2.0)
        self.font_scale.setSingleStep(0.1)
        self.font_scale.setValue(u.font_scale)
        form.addRow(self.on_top)
        form.addRow(self.hotkeys)
        form.addRow("Text size", self.font_scale)
        return w

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    # --- actions ----------------------------------------------------------------------
    def _test_connection(self) -> None:
        self._claude.invalidate()
        self.test_btn.setEnabled(False)
        self.test_result.setText("Checking…")
        self.test_result.setStyleSheet("")
        model = self._model_id()

        def ok(name: str) -> None:
            self.test_btn.setEnabled(True)
            self.test_result.setStyleSheet("color:#3fb27f")
            self.test_result.setText(f"Connected. Model available: {name}")

        def fail(exc: BaseException) -> None:
            self.test_btn.setEnabled(True)
            err = exc if isinstance(exc, AIError) else map_exception(exc)
            self.test_result.setStyleSheet("color:#e5534b")
            self.test_result.setText(err.message)

        self._runner.submit(self._claude.check_connection(model), ok, fail)

    def _model_id(self) -> str:
        text = self.model.currentText().strip()
        idx = self.model.findText(text)
        if idx >= 0 and self.model.itemData(idx):
            return str(self.model.itemData(idx))
        return text or self._settings.ai.model

    def result_settings(self) -> Settings:
        s = self._settings
        s.ai.model = self._model_id()
        s.ai.effort = self.effort.currentData()
        s.ai.answer_length = self.length.currentData()
        s.ai.default_mode = AnswerMode(self.mode.currentData())
        s.ai.max_tokens = self.max_tokens.value()
        s.ai.request_timeout_s = self.timeout.value()
        s.ai.max_retries = self.retries.value()
        s.ai.recent_context_turns = self.context_turns.value()
        s.ai.refusal_fallback = self.fallback.isChecked()
        s.audio.capture_system_audio = self.cap_sys.isChecked()
        s.audio.capture_microphone = self.cap_mic.isChecked()
        s.audio.loopback_device = self.loop_dev.currentData()
        s.audio.microphone_device = self.mic_dev.currentData()
        s.audio.silence_ms = self.silence.value()
        s.audio.max_utterance_s = self.max_utt.value()
        s.audio.min_rms = self.min_rms.value()
        s.transcription.provider = self.provider.currentData()
        s.transcription.whisper_model = self.whisper_model.currentText().strip() or "base.en"
        s.transcription.whisper_device = self.whisper_device.currentData()
        s.transcription.whisper_compute_type = "float16" if s.transcription.whisper_device == "cuda" else "int8"
        s.transcription.interim_results = self.interim.isChecked()
        s.transcription.deepgram_model = self.deepgram_model.text().strip() or "nova-3"
        s.detection.detect_from_interviewer = self.det_int.isChecked()
        s.detection.detect_from_candidate = self.det_cand.isChecked()
        s.detection.auto_generate = self.auto_gen.isChecked()
        s.detection.min_words = self.min_words.value()
        s.detection.duplicate_threshold = self.dup.value()
        s.privacy.retention = self.retention.currentData()
        s.privacy.log_transcripts = self.log_tx.isChecked()
        s.ui.always_on_top = self.on_top.isChecked()
        s.ui.global_hotkeys = self.hotkeys.isChecked()
        s.ui.font_scale = self.font_scale.value()
        return s


__all__ = ["SettingsDialog"]
