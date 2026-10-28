"""Claude service tests with a fake SDK client (no network, no tokens spent)."""

import asyncio
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.ai.answer_parser import parse_answer
from app.ai.client import ClaudeService
from app.ai.errors import AIError, AIErrorKind, map_exception
from app.ai.practice import PracticeCoach, PracticeEvaluation
from app.ai.profile_extract import ExtractedProfile, merge_into
from app.ai.prompts import ANSWER_SYSTEM_PROMPT, build_answer_user_message, build_system, default_mode_for
from app.config import AISettings, AnswerLength, Effort
from app.models import AnswerMode, ContextQuality, DetectedQuestion, QuestionCategory, Speaker
from app.profiles.models import Experience, UserProfile
from app.security.secrets import SecretStore

TAGGED = (
    "<answer>I'm a final-year CS student who built a sales dashboard [team size].</answer>"
    "<key_points>- Lead with current role\n- Mention dashboard\n- Tie to the job</key_points>"
    "<example>Sales dashboard project at Acme</example>"
    "<context_quality>medium</context_quality>"
    "<needs_personalization>team size, dashboard users</needs_personalization>"
)


def question(text="Tell me about yourself", category=QuestionCategory.INTRO, context=""):
    return DetectedQuestion(text=text, category=category, speaker=Speaker.INTERVIEWER, context=context)


# --- prompts & parsing --------------------------------------------------------------
def test_system_prompt_forbids_fabrication_and_caches_profile():
    system = build_system(UserProfile(skills=["SQL"]))
    assert "never invent" in ANSWER_SYSTEM_PROMPT.lower()
    assert system[0]["text"] == ANSWER_SYSTEM_PROMPT  # stable prefix
    assert system[1]["cache_control"] == {"type": "ephemeral"}
    assert "SQL" in system[1]["text"]


def test_empty_profile_instructs_placeholders():
    assert "has not provided a resume" in build_system(UserProfile())[1]["text"]


def test_user_message_contents():
    msg = build_answer_user_message(
        question("How did you test it?", QuestionCategory.PROJECT, context="I see you built an API."),
        AnswerMode.STAR,
        AnswerLength.SHORT,
        [("Interviewer", "Welcome"), ("You", "Thanks")],
    )
    assert "How did you test it?" in msg and "I see you built an API." in msg
    assert "Interviewer: Welcome" in msg and "STAR" in msg and "80 words" in msg


def test_default_mode_for_behavioral_is_star():
    assert default_mode_for(QuestionCategory.BEHAVIORAL, AnswerMode.CONCISE) == AnswerMode.STAR
    assert default_mode_for(QuestionCategory.BEHAVIORAL, AnswerMode.TECHNICAL) == AnswerMode.TECHNICAL
    assert default_mode_for(QuestionCategory.SQL, AnswerMode.CONCISE) == AnswerMode.CONCISE


def test_parse_complete_answer():
    s = parse_answer(TAGGED, "q1")
    assert s.spoken_answer.startswith("I'm a final-year")
    assert s.key_points == ["Lead with current role", "Mention dashboard", "Tie to the job"]
    assert s.resume_example == "Sales dashboard project at Acme"
    assert s.context_quality == ContextQuality.MEDIUM
    assert s.needs_personalization == ["team size", "dashboard users"]


def test_parse_partial_stream_hides_half_tags():
    s = parse_answer("<answer>I have two years of exp</an", "q1", partial=True)
    assert s.spoken_answer == "I have two years of exp"
    assert s.key_points == []


def test_parse_untagged_fallback_and_none_values():
    assert parse_answer("Just text", "q").spoken_answer == "Just text"
    s = parse_answer("<answer>A</answer><example>None in profile</example><needs_personalization>none</needs_personalization>", "q")
    assert s.resume_example == "" and s.needs_personalization == []


# --- fake SDK ------------------------------------------------------------------------
class FakeStream:
    def __init__(self, chunks, stop_reason="end_turn"):
        self.chunks, self.stop_reason = chunks, stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def gen():
            for c in self.chunks:
                yield c

        return gen()

    async def get_final_message(self):
        usage = SimpleNamespace(input_tokens=1200, output_tokens=150, cache_read_input_tokens=900, cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason=self.stop_reason, usage=usage, model="claude-opus-5-5")


class FakeClient:
    def __init__(self, stream=None, parse_result=None, raise_exc=None):
        self.calls = []
        self._stream = stream
        self._parse_result = parse_result
        self._raise = raise_exc
        outer = self

        class Beta:
            class messages:  # noqa: N801
                @staticmethod
                def stream(**kwargs):
                    outer.calls.append(kwargs)
                    if outer._raise:
                        raise outer._raise
                    return outer._stream

        class Messages:
            @staticmethod
            async def parse(**kwargs):
                outer.calls.append(kwargs)
                if outer._raise:
                    raise outer._raise
                return SimpleNamespace(
                    stop_reason="end_turn",
                    parsed_output=outer._parse_result,
                    usage=SimpleNamespace(input_tokens=10, output_tokens=20),
                )

        self.beta = Beta()
        self.messages = Messages()


def make_service(client, **ai):
    settings = AISettings(**ai)
    service = ClaudeService(lambda: settings, SecretStore())
    service._get_client = lambda: client
    return service


def test_stream_answer_request_shape_and_usage():
    chunks = [TAGGED[i : i + 17] for i in range(0, len(TAGGED), 17)]
    client = FakeClient(stream=FakeStream(chunks))
    service = make_service(client, effort=Effort.LOW)
    partials = []
    result = asyncio.run(
        service.stream_answer(question(), UserProfile(), AnswerMode.CONCISE, AnswerLength.MEDIUM, [], partials.append)
    )
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"] == {"effort": "low"}
    for removed in ("temperature", "top_p", "top_k"):  # rejected by API version 2026-10-21
        assert removed not in call
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert "thinking" not in call  # adaptive thinking is the default on Opus 5.5
    assert partials and partials[-1].spoken_answer
    assert result.spoken_answer.startswith("I'm a final-year")
    assert (result.input_tokens, result.cache_read_tokens, result.output_tokens) == (1200, 900, 150)


def test_no_fallback_for_haiku_or_when_disabled():
    for kwargs in ({"model": "claude-haiku-5-5"}, {"refusal_fallback": False}):
        client = FakeClient(stream=FakeStream([TAGGED]))
        asyncio.run(make_service(client, **kwargs).stream_answer(question(), UserProfile(), AnswerMode.CONCISE, AnswerLength.SHORT))
        assert "betas" not in client.calls[0] and "fallbacks" not in client.calls[0]


def test_refusal_becomes_ai_error():
    client = FakeClient(stream=FakeStream(["<answer>"], stop_reason="refusal"))
    with pytest.raises(AIError) as info:
        asyncio.run(make_service(client).stream_answer(question(), UserProfile(), AnswerMode.CONCISE, AnswerLength.SHORT))
    assert info.value.kind == AIErrorKind.REFUSAL


def _resp(status, headers=None):
    return httpx2.Response(status, headers=headers or {}, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


@pytest.mark.parametrize(
    "exc,kind,retryable",
    [
        (anthropic.AuthenticationError("bad key sk-ant-api03-SECRETSECRET", response=_resp(401), body=None), AIErrorKind.AUTH, False),
        (anthropic.PermissionDeniedError("no", response=_resp(403), body=None), AIErrorKind.PERMISSION, False),
        (anthropic.NotFoundError("model", response=_resp(404), body=None), AIErrorKind.NOT_FOUND, False),
        (anthropic.RateLimitError("slow", response=_resp(429, {"retry-after": "12"}), body=None), AIErrorKind.RATE_LIMIT, True),
        (anthropic.BadRequestError("Your credit balance is too low", response=_resp(400), body=None), AIErrorKind.BILLING, False),
        (anthropic.InternalServerError("boom", response=_resp(500), body=None), AIErrorKind.SERVER, True),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), AIErrorKind.NETWORK, True),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")), AIErrorKind.TIMEOUT, True),
    ],
)
def test_error_mapping(exc, kind, retryable):
    err = map_exception(exc)
    assert err.kind == kind and err.retryable == retryable
    assert "sk-ant" not in err.message


def test_rate_limit_retry_after_parsed():
    err = map_exception(anthropic.RateLimitError("slow", response=_resp(429, {"retry-after": "12"}), body=None))
    assert err.retry_after_s == 12.0


def test_stream_errors_are_mapped():
    client = FakeClient(raise_exc=anthropic.AuthenticationError("bad", response=_resp(401), body=None))
    with pytest.raises(AIError) as info:
        asyncio.run(make_service(client).stream_answer(question(), UserProfile(), AnswerMode.CONCISE, AnswerLength.SHORT))
    assert info.value.kind == AIErrorKind.AUTH


def test_missing_credentials_message(monkeypatch):
    """With no key anywhere, the real SDK raises before sending; we surface a setup hint."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_CONFIG_DIR", "/nonexistent-cia-test")
    service = ClaudeService(lambda: AISettings(max_retries=0), SecretStore())
    service._secrets.get = lambda name: None
    with pytest.raises(AIError) as info:
        asyncio.run(service.check_connection())
    assert info.value.kind == AIErrorKind.NO_CREDENTIALS
    assert "Settings" in info.value.message


# --- practice & profile extraction ------------------------------------------------------
def test_practice_evaluation_is_clamped():
    evaluation = PracticeEvaluation(
        relevance=9, clarity=0, technical_accuracy=-1, structure=3, overall=4,
        strengths=["clear"], improvements=["add result"], improved_answer="...", missing_personal_details=[],
    )
    client = FakeClient(parse_result=evaluation)
    result, tin, tout = asyncio.run(PracticeCoach(make_service(client)).evaluate(UserProfile(), "Q?", "A."))
    assert (result.relevance, result.clarity, result.technical_accuracy) == (5, 1, 0)
    assert client.calls[0]["output_format"] is PracticeEvaluation
    assert (tin, tout) == (10, 20)


def test_merge_extracted_profile_keeps_user_fields():
    existing = UserProfile(summary="My own summary", skills=[])
    extracted = ExtractedProfile(
        candidate_name="Jane", summary="Extracted", skills=["SQL"], education=[],
        experience=[Experience(title="Intern")], projects=[], achievements=[],
    )
    merged = merge_into(existing, extracted)
    assert merged.summary == "My own summary" and merged.skills == ["SQL"] and merged.candidate_name == "Jane"
    assert merge_into(existing, extracted, overwrite=True).summary == "Extracted"
