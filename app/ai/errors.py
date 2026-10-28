"""Map Anthropic SDK exceptions to user-presentable errors (never containing secrets)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import anthropic

from app.security.redaction import redact


class AIErrorKind(str, Enum):
    NO_CREDENTIALS = "no_credentials"
    AUTH = "auth"
    PERMISSION = "permission"
    BILLING = "billing"
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    TIMEOUT = "timeout"
    NETWORK = "network"
    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    REFUSAL = "refusal"
    SERVER = "server"
    UNKNOWN = "unknown"


@dataclass
class AIError(Exception):
    kind: AIErrorKind
    message: str
    retryable: bool = False
    retry_after_s: float | None = None
    request_id: str | None = None

    def __str__(self) -> str:
        return self.message


def _request_id(exc: Exception) -> str | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        return headers.get("request-id")
    return None


def map_exception(exc: BaseException) -> AIError:
    """Most-specific first, mirroring the SDK's exception hierarchy."""
    if isinstance(exc, AIError):
        return exc
    credentials_error = getattr(anthropic, "CredentialsError", None)
    if (credentials_error and isinstance(exc, credentials_error)) or (
        isinstance(exc, TypeError) and "authentication method" in str(exc)
    ):
        # Raised by the SDK before sending when no credential source is available.
        return AIError(
            AIErrorKind.NO_CREDENTIALS,
            "No Claude API key is configured. Open Settings > Claude API and paste your "
            "Anthropic API key (from console.anthropic.com).",
        )
    rid = _request_id(exc) if isinstance(exc, Exception) else None
    detail = redact(getattr(exc, "message", "") or str(exc))[:300]

    if isinstance(exc, anthropic.AuthenticationError):
        return AIError(
            AIErrorKind.AUTH,
            "Claude rejected the API key. Open Settings > API keys and enter a valid Anthropic API key.",
            request_id=rid,
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return AIError(
            AIErrorKind.PERMISSION,
            "This API key is not allowed to use the selected model. Choose another model in Settings "
            "or check the key's workspace permissions in the Anthropic Console.",
            request_id=rid,
        )
    if isinstance(exc, anthropic.NotFoundError):
        return AIError(
            AIErrorKind.NOT_FOUND,
            "The selected Claude model was not found. Pick a different model in Settings.",
            request_id=rid,
        )
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = None
        try:
            retry_after = float(exc.response.headers.get("retry-after", ""))
        except (ValueError, AttributeError):
            pass
        return AIError(
            AIErrorKind.RATE_LIMIT,
            "Claude rate limit reached. The request will be retried shortly"
            + (f" (about {int(retry_after)}s)." if retry_after else "."),
            retryable=True,
            retry_after_s=retry_after,
            request_id=rid,
        )
    if isinstance(exc, anthropic.BadRequestError):
        lower = detail.lower()
        if "credit balance" in lower or "billing" in lower or "purchase credits" in lower:
            return AIError(
                AIErrorKind.BILLING,
                "Your Anthropic account has no available credit. Add credits or a payment method "
                "under Plans & Billing in the Anthropic Console, then try again.",
                request_id=rid,
            )
        return AIError(AIErrorKind.BAD_REQUEST, f"Claude could not process the request: {detail}", request_id=rid)
    if isinstance(exc, anthropic.APITimeoutError):
        return AIError(AIErrorKind.TIMEOUT, "Claude took too long to respond. Try again.", retryable=True)
    if isinstance(exc, anthropic.APIConnectionError):
        return AIError(
            AIErrorKind.NETWORK,
            "Could not reach the Claude API. Check your internet connection, VPN or proxy.",
            retryable=True,
        )
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status == 402:
            return AIError(AIErrorKind.BILLING, "Billing is unavailable for this Anthropic account.", request_id=rid)
        if status == 529:
            return AIError(AIErrorKind.OVERLOADED, "Claude is temporarily overloaded. Retrying…", retryable=True, request_id=rid)
        if status >= 500:
            return AIError(AIErrorKind.SERVER, f"Claude API server error ({status}). Try again shortly.", retryable=True, request_id=rid)
        return AIError(AIErrorKind.UNKNOWN, f"Claude API error ({status}): {detail}", request_id=rid)
    return AIError(AIErrorKind.UNKNOWN, f"Unexpected error: {type(exc).__name__}")
