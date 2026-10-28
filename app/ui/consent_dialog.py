"""Shown before every live session: what will be captured, where it goes, and consent."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from app.config import Retention, Settings
from app.platforms.registry import PLATFORMS
from app.transcription.factory import provider_display_name


def disclosure_html(settings: Settings, platform_id: str) -> str:
    sources = []
    if settings.audio.capture_system_audio:
        sources.append("<b>System audio</b> (what you hear: other participants)")
    if settings.audio.capture_microphone:
        sources.append("<b>Your microphone</b>")
    provider, external = provider_display_name(settings)
    stt = (
        f"Audio is sent to <b>{provider}</b> for transcription while listening."
        if external
        else f"Audio is transcribed <b>on this computer</b> ({provider}); it is not uploaded."
    )
    retention = (
        "Transcripts and answers are <b>not saved</b> to disk."
        if settings.privacy.retention == Retention.NONE
        else f"Transcripts and answers are saved locally for <b>{settings.privacy.retention.value}</b>."
    )
    platform = PLATFORMS.get(platform_id)
    return f"""
    <p><b>Audio to capture:</b> {', '.join(sources) or 'none selected'}</p>
    <p>{stt} Raw audio is never saved.</p>
    <p>When a question is detected, its text, recent transcript lines, and your profile
    (resume, job description) are sent to <b>Anthropic's Claude API</b> to draft an answer.</p>
    <p>{retention}</p>
    <p><b>Platform:</b> {platform.name if platform else platform_id} – captured through Windows
    system audio; no meeting-platform API or account access is used.</p>
    <p style="color:#e0a43a">Recording or transcribing a conversation can require the consent of
    everyone in it, and many employers and interview processes do not allow AI assistance.
    Only continue if you have the necessary permission and doing so complies with the law,
    the meeting platform's terms, and the interviewer's or organisation's rules.</p>
    """


class ConsentDialog(QDialog):
    def __init__(self, settings: Settings, platform_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Before you start listening")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        text = QLabel(disclosure_html(settings, platform_id))
        text.setWordWrap(True)
        layout.addWidget(text)
        self.confirm = QCheckBox(
            "I have permission to capture and transcribe this conversation, and I understand what "
            "data is sent to Anthropic and the transcription provider."
        )
        self.confirm.setStyleSheet("font-weight:600")
        layout.addWidget(self.confirm)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start_btn = buttons.addButton("Start listening", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_btn.setObjectName("Primary")
        self.start_btn.setEnabled(False)
        self.confirm.toggled.connect(self.start_btn.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
