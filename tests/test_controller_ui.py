"""Controller + UI integration tests with fake audio, STT and Claude (offscreen Qt)."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.errors import AIError, AIErrorKind
from app.config import Retention, Settings
from app.controller import SessionController
from app.models import AnswerSuggestion, ContextQuality, PipelineState, Speaker, TranscriptEvent
from app.security.secrets import SecretStore
from tests.test_audio_transcription import FakeProvider, FakeSource

pytest.importorskip("pytestqt")


class FakeClaude:
    def __init__(self, fail: AIError | None = None, delay: float = 0.0):
        self.fail = fail
        self.delay = delay
        self.requests = []

    def invalidate(self):
        pass

    async def stream_answer(self, question, profile, mode, length, recent, on_partial=None):
        self.requests.append((question.text, mode, list(recent)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        if on_partial:
            on_partial(AnswerSuggestion(question_id=question.id, spoken_answer="Partial"))
        return AnswerSuggestion(
            question_id=question.id,
            spoken_answer=f"Answer to: {question.text}",
            key_points=["one", "two"],
            context_quality=ContextQuality.HIGH,
            model="claude-opus-5-5",
            input_tokens=100,
            output_tokens=50,
        )


@pytest.fixture
def runner(qapp):
    from app.ui.async_runner import AsyncRunner

    r = AsyncRunner()
    yield r
    r.shutdown()


def make_controller(db, runner, claude=None, settings=None):
    source, provider = FakeSource(Speaker.INTERVIEWER), FakeProvider()
    settings = settings or Settings()
    controller = SessionController(
        settings, db, SecretStore(), claude or FakeClaude(), runner,
        sources_factory=lambda s: [source], provider_factory=lambda s, sec: provider,
    )
    return controller, source, provider


def test_manual_question_generates_answer(qtbot, db, runner):
    controller, *_ = make_controller(db, runner)
    with qtbot.waitSignal(controller.answer_ready, timeout=5000) as blocker:
        controller.ask_manual("What is a SQL join?")
    assert blocker.args[0].spoken_answer == "Answer to: What is a SQL join?"
    assert controller.stats.answers == 1 and controller.stats.estimated_cost_usd > 0


def test_live_session_detects_dedups_and_respects_retention(qtbot, db, runner):
    claude = FakeClaude()
    controller, source, provider = make_controller(db, runner, claude)
    with qtbot.waitSignal(controller.state_changed, timeout=5000, check_params_cb=lambda s: s == PipelineState.LISTENING):
        controller.start_session("zoom")
    assert source.is_running

    with qtbot.waitSignal(controller.answer_ready, timeout=5000):
        provider.on_event(TranscriptEvent(Speaker.INTERVIEWER, "Welcome. Can you tell me about yourself?", True, "u1"))
    # Same question again: suppressed
    provider.on_event(TranscriptEvent(Speaker.INTERVIEWER, "So tell me about yourself.", True, "u2"))
    qtbot.wait(200)
    assert len(claude.requests) == 1
    assert claude.requests[0][2] == []  # the question line itself is not duplicated as context
    assert controller.stats.questions == 1

    with qtbot.waitSignal(controller.state_changed, timeout=5000, check_params_cb=lambda s: s == PipelineState.IDLE):
        controller.stop_session()
    assert not source.is_running and provider.stopped == 1
    assert db.count_content() == (0, 0)  # retention NONE: nothing stored
    session = db.recent_sessions()[0]
    assert session.ended_at is not None and session.questions == 1


def test_retention_enabled_stores_content(qtbot, db, runner):
    settings = Settings()
    settings.privacy.retention = Retention.DAYS_7
    controller, source, provider = make_controller(db, runner, settings=settings)
    with qtbot.waitSignal(controller.state_changed, timeout=5000, check_params_cb=lambda s: s == PipelineState.LISTENING):
        controller.start_session("teams")
    qtbot.waitUntil(lambda: controller._session_id is not None, timeout=3000)
    with qtbot.waitSignal(controller.answer_ready, timeout=5000):
        provider.on_event(TranscriptEvent(Speaker.INTERVIEWER, "Why do you want this role?", True, "u1"))
    assert db.count_content() == (1, 1)
    controller.shutdown()


def test_pause_resume_and_failure_paths(qtbot, db, runner):
    claude = FakeClaude(fail=AIError(AIErrorKind.AUTH, "Claude rejected the API key."))
    controller, source, _provider = make_controller(db, runner, claude)
    with qtbot.waitSignal(controller.state_changed, timeout=5000, check_params_cb=lambda s: s == PipelineState.LISTENING):
        controller.start_session("meet")
    with qtbot.waitSignal(controller.state_changed, timeout=5000, check_params_cb=lambda s: s == PipelineState.PAUSED):
        controller.toggle_pause()
    assert not source.is_running
    with qtbot.waitSignal(controller.state_changed, timeout=5000, check_params_cb=lambda s: s == PipelineState.LISTENING):
        controller.toggle_pause()
    with qtbot.waitSignal(controller.answer_failed, timeout=5000) as blocker:
        controller.ask_manual("What are your strengths?")
    assert "API key" in blocker.args[1]
    controller.shutdown()
    assert not source.is_running


def test_start_failure_reports_error(qtbot, db, runner):
    controller = SessionController(
        Settings(), db, SecretStore(), FakeClaude(), runner,
        sources_factory=lambda s: [FakeSource(fail=True)], provider_factory=lambda s, sec: FakeProvider(),
    )
    with qtbot.waitSignal(controller.error, timeout=5000) as blocker:
        controller.start_session("zoom")
    assert "device missing" in blocker.args[0]
    assert controller.state == PipelineState.ERROR


def test_regenerate_cancels_previous(qtbot, db, runner):
    claude = FakeClaude(delay=0.3)
    controller, *_ = make_controller(db, runner, claude)
    controller.settings.detection.auto_generate = False
    q = controller.ask_manual("Explain REST APIs")
    from app.models import AnswerMode

    controller.generate(q.id, AnswerMode.TECHNICAL)
    controller.generate(q.id, AnswerMode.BEGINNER)
    with qtbot.waitSignal(controller.answer_ready, timeout=5000):
        pass
    qtbot.wait(400)
    assert controller.stats.answers == 1  # first request was cancelled
    assert [r[1] for r in claude.requests][-1] == AnswerMode.BEGINNER


def test_main_window_smoke(qtbot, db, runner):
    from app.ui.main_window import MainWindow

    controller, *_ = make_controller(db, runner)
    window = MainWindow(controller)
    qtbot.addWidget(window)
    window.show()
    assert window.start_btn.isEnabled() and not window.stop_btn.isEnabled()
    window.manual.setText("Tell me about a time you led a team")
    with qtbot.waitSignal(controller.answer_ready, timeout=5000):
        window._ask_manual()
    qtbot.waitUntil(lambda: len(window._cards) == 1, timeout=2000)
    card = next(iter(window._cards.values()))
    qtbot.waitUntil(lambda: card.copy_btn.isEnabled(), timeout=2000)
    assert card.answer_text().startswith("Answer to:")
    assert card.mode.currentText() == "STAR"  # behavioural question defaults to STAR
    card.copy_btn.click()
    from PySide6.QtGui import QGuiApplication

    assert QGuiApplication.clipboard().text() == card.answer_text()
    window._set_compact(True)
    assert not window.left_panel.isVisible()
    window._set_compact(False)
    window._on_hotkey("toggle_window")
    window.close()


def test_settings_profile_practice_widgets_construct(qtbot, db, runner):
    from app.ai.client import ClaudeService
    from app.ai.practice import PracticeCoach
    from app.profiles.repository import ProfileRepository
    from app.ui.practice_tab import PracticeTab
    from app.ui.profile_tab import ProfileTab
    from app.ui.settings_dialog import SettingsDialog

    settings = Settings()
    claude = ClaudeService(lambda: settings.ai, SecretStore())
    dialog = SettingsDialog(settings, SecretStore(), claude, runner)
    qtbot.addWidget(dialog)
    out = dialog.result_settings()
    assert out.ai.model == settings.ai.model

    repo = ProfileRepository(db)
    tab = ProfileTab(repo, claude, runner)
    qtbot.addWidget(tab)
    tab.target_title.setText("Data Analyst")
    tab.skills.setPlainText("SQL, Python")
    tab.projects.setPlainText("Dashboard | Power BI | Solo\nWeekly sales KPIs")
    tab.save()
    _pid, saved = repo.active()
    assert saved.target_job_title == "Data Analyst" and saved.skills == ["SQL", "Python"]
    assert saved.projects[0].technologies == ["Power BI"]

    practice = PracticeTab(lambda: settings, repo, PracticeCoach(claude), runner, db, SecretStore(), lambda: False)
    qtbot.addWidget(practice)
    practice.custom_q.setText("Why this company?")
    practice._add_custom()
    assert practice._current_question() == "Why this company?"
