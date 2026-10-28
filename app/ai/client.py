"""Async Claude API service.

* Credentials: key from :class:`SecretStore` (env var or Windows Credential Manager); if none
  is stored, the SDK's own credential chain is tried (e.g. an ``ant auth login`` profile).
* Retries: the SDK retries connection errors, 408/409/429/5xx with exponential backoff.
* Streaming: answers stream so the first words appear quickly.
* Refusal fallback: on models that support it, the API's server-side ``fallbacks: "default"``
  re-runs a safety-declined request on Anthropic's recommended fallback model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from app.ai.answer_parser import parse_answer
from app.ai.errors import AIError, AIErrorKind, map_exception
from app.ai.prompts import build_answer_user_message, build_system
from app.config import AISettings, AnswerLength
from app.logging_setup import transcript_preview
from app.models import AnswerMode, AnswerSuggestion, DetectedQuestion
from app.profiles.models import UserProfile
from app.security.secrets import SecretName, SecretStore

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Models that accept the server-side `fallbacks: "default"` refusal fallback.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeService:
    def __init__(self, settings: Callable[[], AISettings], secrets: SecretStore) -> None:
        self._settings = settings
        self._secrets = secrets
        self._client: anthropic.AsyncAnthropic | None = None
        self._client_key: tuple | None = None

    # --- client management -----------------------------------------------------------
    def has_explicit_key(self) -> bool:
        return self._secrets.get(SecretName.ANTHROPIC_API_KEY) is not None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        settings = self._settings()
        api_key = self._secrets.get(SecretName.ANTHROPIC_API_KEY)
        cache_key = (api_key, settings.request_timeout_s, settings.max_retries)
        if self._client is None or self._client_key != cache_key:
            kwargs: dict = {"timeout": settings.request_timeout_s, "max_retries": settings.max_retries}
            if api_key:
                kwargs["api_key"] = api_key
            # Without a key the SDK falls back to its own credential chain (e.g. an
            # `ant auth login` profile); if that is empty too, the request raises and
            # map_exception turns it into a NO_CREDENTIALS error.
            try:
                self._client = anthropic.AsyncAnthropic(**kwargs)
            except Exception as exc:  # noqa: BLE001 - e.g. CredentialsError for a broken profile
                raise map_exception(exc) from exc
            self._client_key = cache_key
        return self._client

    def invalidate(self) -> None:
        """Call after the API key or network settings change."""
        self._client = None
        self._client_key = None

    def _fallback_kwargs(self, model: str) -> dict:
        if self._settings().refusal_fallback and model in _FALLBACK_MODELS:
            return {"betas": [_FALLBACK_BETA], "fallbacks": "default"}
        return {}

    # --- answer generation -----------------------------------------------------------
    async def stream_answer(
        self,
        question: DetectedQuestion,
        profile: UserProfile,
        mode: AnswerMode,
        length: AnswerLength,
        recent_transcript: Sequence[tuple[str, str]] = (),
        on_partial: Callable[[AnswerSuggestion], None] | None = None,
    ) -> AnswerSuggestion:
        settings = self._settings()
        client = self._get_client()
        model = settings.model
        system = build_system(profile)
        user = build_answer_user_message(question, mode, length, recent_transcript)
        log.info("Requesting answer (%s, mode=%s, question=%s)", model, mode.value, transcript_preview(question.text))

        started = time.monotonic()
        text = ""
        try:
            async with client.beta.messages.stream(
                model=model,
                max_tokens=settings.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": settings.effort.value},
                **self._fallback_kwargs(model),
            ) as stream:
                async for delta in stream.text_stream:
                    text += delta
                    if on_partial is not None:
                        on_partial(parse_answer(text, question.id, partial=True))
                final = await stream.get_final_message()
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a user-facing error
            raise map_exception(exc) from exc

        if final.stop_reason == "refusal":
            raise AIError(
                AIErrorKind.REFUSAL,
                "Claude declined to answer this question. Try rephrasing it or regenerate.",
            )
        suggestion = parse_answer(text, question.id, partial=False)
        if final.stop_reason == "max_tokens" and not suggestion.spoken_answer:
            raise AIError(AIErrorKind.BAD_REQUEST, "The answer was cut off. Increase max tokens in Settings.")
        usage = final.usage
        suggestion.model = getattr(final, "model", model)
        suggestion.input_tokens = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        suggestion.cache_read_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0
        suggestion.output_tokens = usage.output_tokens or 0
        suggestion.latency_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "Answer ready in %d ms (in=%d cached=%d out=%d)",
            suggestion.latency_ms, suggestion.input_tokens, suggestion.cache_read_tokens, suggestion.output_tokens,
        )
        return suggestion

    # --- structured outputs ----------------------------------------------------------
    async def parse(self, system: str, user: str, output_format: type[T], max_tokens: int = 16000) -> tuple[T, int, int]:
        """Return (parsed object, input tokens, output tokens)."""
        settings = self._settings()
        client = self._get_client()
        try:
            response = await client.messages.parse(
                model=settings.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=output_format,
            )
        except Exception as exc:  # noqa: BLE001
            raise map_exception(exc) from exc
        if response.stop_reason == "refusal":
            raise AIError(AIErrorKind.REFUSAL, "Claude declined this request.")
        parsed = response.parsed_output
        if parsed is None:
            raise AIError(AIErrorKind.BAD_REQUEST, "Claude returned an incomplete response. Try again.")
        return parsed, response.usage.input_tokens or 0, response.usage.output_tokens or 0

    async def check_connection(self, model: str | None = None) -> str:
        """Verify credentials and model access without spending tokens."""
        model = model or self._settings().model
        client = self._get_client()
        try:
            info = await client.models.retrieve(model)
        except Exception as exc:  # noqa: BLE001
            raise map_exception(exc) from exc
        return getattr(info, "display_name", None) or model
