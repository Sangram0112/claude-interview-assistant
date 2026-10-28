"""Profile editor: resume import, job description, structured experience, delete."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ai.client import ClaudeService
from app.ai.errors import AIError, map_exception
from app.ai.profile_extract import extract_profile, merge_into
from app.config import AnswerLength
from app.profiles.models import UserProfile
from app.profiles.repository import ProfileRepository
from app.profiles.resume_import import ResumeImportError, extract_resume_text
from app.profiles.text_format import format_experience, format_projects, parse_experience, parse_list, parse_projects
from app.ui.async_runner import AsyncRunner


def _text_edit(placeholder: str, height: int = 110) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setPlaceholderText(placeholder)
    edit.setMinimumHeight(height)
    return edit


class ExtractionPreviewDialog(QDialog):
    """Lets the user review and correct extracted resume text before it is used."""

    def __init__(self, text: str, warnings: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review extracted resume text")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        info = QLabel("Check the text below and fix any extraction errors. It is stored only on this PC.")
        info.setWordWrap(True)
        layout.addWidget(info)
        for warning in warnings:
            w = QLabel(warning)
            w.setObjectName("InfoBanner")
            w.setWordWrap(True)
            layout.addWidget(w)
        self.edit = QPlainTextEdit(text)
        layout.addWidget(self.edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.edit.toPlainText().strip()


class ProfileTab(QWidget):
    profile_changed = Signal()
    status = Signal(str)

    def __init__(self, repo: ProfileRepository, claude: ClaudeService, runner: AsyncRunner, parent=None) -> None:
        super().__init__(parent)
        self._repo = repo
        self._claude = claude
        self._runner = runner
        self._profile_id: int | None = None

        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        self.selector = QComboBox()
        self.selector.currentIndexChanged.connect(self._on_select)
        new_btn = QPushButton("New profile")
        new_btn.clicked.connect(self._new_profile)
        self.delete_btn = QPushButton("Delete profile")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete_profile)
        self.save_btn = QPushButton("Save profile")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.save)
        top.addWidget(QLabel("Active profile:"))
        top.addWidget(self.selector, 1)
        top.addWidget(new_btn)
        top.addWidget(self.delete_btn)
        top.addWidget(self.save_btn)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self.name = QLineEdit()
        self.candidate_name = QLineEdit()
        self.target_title = QLineEdit()
        self.target_title.setPlaceholderText("e.g. Junior Data Analyst")
        form.addRow("Profile name", self.name)
        form.addRow("Your name", self.candidate_name)
        form.addRow("Target job title", self.target_title)

        self.job_description = _text_edit("Paste the job description here.", 140)
        form.addRow("Job description", self.job_description)

        resume_row = QHBoxLayout()
        import_btn = QPushButton("Import resume (PDF / TXT)…")
        import_btn.clicked.connect(self._import_resume)
        self.autofill_btn = QPushButton("Auto-fill fields from resume with Claude…")
        self.autofill_btn.clicked.connect(self._autofill)
        resume_row.addWidget(import_btn)
        resume_row.addWidget(self.autofill_btn)
        resume_row.addStretch(1)
        resume_box = QWidget()
        resume_box.setLayout(resume_row)
        resume_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Resume", resume_box)
        self.resume_text = _text_edit("Resume text (imported or pasted). Extracted locally; edit freely.", 180)
        form.addRow("", self.resume_text)

        self.summary = _text_edit("2-3 sentence professional summary.", 70)
        form.addRow("Summary", self.summary)
        self.skills = _text_edit("Comma-separated: Python, SQL, React, Power BI …", 60)
        form.addRow("Skills && stack", self.skills)
        self.education = _text_edit("One per line: B.Tech Computer Science, XYZ University, 2025", 60)
        form.addRow("Education", self.education)
        self.experience = _text_edit(
            "Title | Company | Period\n- highlight\n- highlight\n\n(blank line between jobs)", 130
        )
        form.addRow("Work experience", self.experience)
        self.projects = _text_edit(
            "Project name | Tech, Tech | Your role\nShort description\nOutcome: result you actually achieved\n\n(blank line between projects)",
            130,
        )
        form.addRow("Projects", self.projects)
        self.achievements = _text_edit("One per line. Only real, verifiable achievements.", 70)
        form.addRow("Key achievements", self.achievements)
        self.length = QComboBox()
        for label, value in (("Short (~30 s)", AnswerLength.SHORT), ("Medium (~60 s)", AnswerLength.MEDIUM), ("Long (~90 s)", AnswerLength.LONG)):
            self.length.addItem(label, value)
        form.addRow("Preferred answer length", self.length)
        self.style = QLineEdit()
        form.addRow("Speaking style", self.style)

        self.reload()

    # --- data <-> widgets --------------------------------------------------------------
    def reload(self) -> None:
        self.selector.blockSignals(True)
        self.selector.clear()
        profiles = self._repo.list()
        active_index = 0
        for i, (pid, name, active) in enumerate(profiles):
            self.selector.addItem(name, pid)
            if active:
                active_index = i
        self.selector.blockSignals(False)
        if profiles:
            self.selector.setCurrentIndex(active_index)
            self._load(self.selector.currentData())
        else:
            self._profile_id = None
            self._fill(UserProfile())
        self.delete_btn.setEnabled(bool(profiles))

    def _load(self, profile_id: int | None) -> None:
        self._profile_id = profile_id
        profile = self._repo.get(profile_id) if profile_id is not None else None
        self._fill(profile or UserProfile())

    def _fill(self, p: UserProfile) -> None:
        self.name.setText(p.name)
        self.candidate_name.setText(p.candidate_name)
        self.target_title.setText(p.target_job_title)
        self.job_description.setPlainText(p.job_description)
        self.resume_text.setPlainText(p.resume_text)
        self.summary.setPlainText(p.summary)
        self.skills.setPlainText(", ".join(p.skills))
        self.education.setPlainText("\n".join(p.education))
        self.experience.setPlainText(format_experience(p.experience))
        self.projects.setPlainText(format_projects(p.projects))
        self.achievements.setPlainText("\n".join(p.achievements))
        self.length.setCurrentIndex(max(0, self.length.findData(p.preferred_answer_length)))
        self.style.setText(p.speaking_style)

    def current_profile(self) -> UserProfile:
        return UserProfile(
            name=self.name.text().strip() or "My profile",
            candidate_name=self.candidate_name.text().strip(),
            target_job_title=self.target_title.text().strip(),
            job_description=self.job_description.toPlainText().strip(),
            resume_text=self.resume_text.toPlainText().strip(),
            summary=self.summary.toPlainText().strip(),
            skills=parse_list(self.skills.toPlainText(), ","),
            education=parse_list(self.education.toPlainText()),
            experience=parse_experience(self.experience.toPlainText()),
            projects=parse_projects(self.projects.toPlainText()),
            achievements=parse_list(self.achievements.toPlainText()),
            preferred_answer_length=self.length.currentData(),
            speaking_style=self.style.text().strip() or UserProfile().speaking_style,
        )

    # --- actions ----------------------------------------------------------------------
    def save(self) -> None:
        profile = self.current_profile()
        self._profile_id = self._repo.save(profile, self._profile_id)
        self._repo.set_active(self._profile_id)
        self.reload()
        self.status.emit("Profile saved")
        self.profile_changed.emit()

    def _on_select(self, _index: int) -> None:
        pid = self.selector.currentData()
        if pid is not None:
            self._repo.set_active(pid)
            self._load(pid)
            self.profile_changed.emit()

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Profile name (e.g. 'Data Analyst roles'):")
        if not ok or not name.strip():
            return
        pid = self._repo.save(UserProfile(name=name.strip()))
        self._repo.set_active(pid)
        self.reload()
        self.profile_changed.emit()

    def _delete_profile(self) -> None:
        if self._profile_id is None:
            return
        answer = QMessageBox.question(
            self, "Delete profile", "Permanently delete this profile, including its resume text, from this PC?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._repo.delete(self._profile_id)
        self.reload()
        self.status.emit("Profile deleted")
        self.profile_changed.emit()

    def _import_resume(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import resume", "", "Resume (*.pdf *.txt *.md)")
        if not path:
            return
        try:
            result = extract_resume_text(path)
        except ResumeImportError as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        dialog = ExtractionPreviewDialog(result.text, result.warnings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.resume_text.setPlainText(dialog.text())
            self.status.emit("Resume imported – review it, then Save profile")

    def _autofill(self) -> None:
        text = self.resume_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "No resume text", "Import or paste your resume text first.")
            return
        answer = QMessageBox.question(
            self,
            "Send resume to Claude?",
            "This sends your resume text to Anthropic's Claude API to extract skills, experience and "
            "projects. Empty fields are filled; fields you already completed are kept.\n\nContinue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.autofill_btn.setEnabled(False)
        self.autofill_btn.setText("Extracting…")

        def done(result) -> None:
            extracted, _tin, _tout = result
            self._fill(merge_into(self.current_profile(), extracted))
            self._reset_autofill()
            self.status.emit("Fields filled from resume – review them, then Save profile")

        def fail(exc: BaseException) -> None:
            self._reset_autofill()
            err = exc if isinstance(exc, AIError) else map_exception(exc)
            QMessageBox.warning(self, "Auto-fill failed", err.message)

        self._runner.submit(extract_profile(self._claude, text), done, fail)

    def _reset_autofill(self) -> None:
        self.autofill_btn.setEnabled(True)
        self.autofill_btn.setText("Auto-fill fields from resume with Claude…")
