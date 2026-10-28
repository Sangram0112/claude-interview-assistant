"""Privacy: data-flow disclosure, stored-data overview and deletion controls."""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app.config import Settings
from app.paths import data_dir
from app.security.secrets import SecretName, SecretStore
from app.storage.database import Database

DISCLOSURE = """
<h3>What this app does with your data</h3>
<ul>
<li><b>Audio</b> is captured only between <i>Start</i> and <i>Stop/Pause</i>, only from the sources you
enable, with a visible LISTENING indicator. Raw audio is never written to disk.</li>
<li><b>Transcription:</b> with <i>Local Whisper</i> (default) audio is transcribed on this PC. With
<i>Deepgram</i> audio is streamed to Deepgram while listening.</li>
<li><b>Claude API (Anthropic):</b> when a question is detected (or you type one, or use practice
mode), the question, a few recent transcript lines, and your active profile (resume text, job
description, skills, projects) are sent to Anthropic to generate a suggestion. Auto-filling a
profile sends the resume text.</li>
<li><b>Stored locally</b> in <code>{data_dir}</code>: settings, profiles, session statistics, and –
only if you enable retention – transcripts and answers. API keys are kept in Windows Credential
Manager.</li>
<li><b>Logs</b> record events and errors but not transcript text (unless you enable it), and API keys
are redacted.</li>
<li>No hidden recording, screen-capture evasion, credential access, or meeting-platform access
controls are used or bypassed.</li>
</ul>
<p>See the provider policies: anthropic.com/legal/privacy and deepgram.com/privacy.</p>
"""


class PrivacyTab(QWidget):
    data_deleted = Signal()

    def __init__(self, db: Database, secrets: SecretStore, settings: Callable[[], Settings], parent=None) -> None:
        super().__init__(parent)
        self._db = db
        self._secrets = secrets
        self._settings = settings
        layout = QVBoxLayout(self)
        text = QLabel(DISCLOSURE.format(data_dir=data_dir()))
        text.setWordWrap(True)
        layout.addWidget(text)
        self.counts = QLabel("")
        self.counts.setObjectName("Muted")
        layout.addWidget(self.counts)
        row = QHBoxLayout()
        open_btn = QPushButton("Open data folder")
        open_btn.clicked.connect(lambda: os.startfile(str(data_dir())))  # noqa: S606 - Windows only
        del_tx = QPushButton("Delete saved transcripts && answers")
        del_tx.setObjectName("Danger")
        del_tx.clicked.connect(self._delete_transcripts)
        del_all = QPushButton("Delete ALL local data && keys")
        del_all.setObjectName("Danger")
        del_all.clicked.connect(self._delete_all)
        row.addWidget(open_btn)
        row.addWidget(del_tx)
        row.addWidget(del_all)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        lines, qa = self._db.count_content()
        retention = self._settings().privacy.retention.value
        self.counts.setText(f"Saved transcript lines: {lines} · saved Q&A items: {qa} · retention setting: {retention}")

    def _delete_transcripts(self) -> None:
        if QMessageBox.question(self, "Delete transcripts", "Delete all saved transcripts and answers?") == QMessageBox.StandardButton.Yes:
            self._db.delete_all_content()
            self.refresh()
            self.data_deleted.emit()

    def _delete_all(self) -> None:
        if QMessageBox.question(
            self,
            "Delete all data",
            "Delete all profiles, transcripts, answers, session statistics and the API keys stored in "
            "Windows Credential Manager? This cannot be undone.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._db.delete_everything()
        for name in SecretName:
            self._secrets.delete(name)
        self.refresh()
        self.data_deleted.emit()
