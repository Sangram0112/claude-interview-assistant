"""Parse Claude's tagged answer format, including partial text while it streams."""

from __future__ import annotations

import re

from app.models import AnswerSuggestion, ContextQuality

_TAGS = ("answer", "key_points", "example", "context_quality", "needs_personalization")


def _section(text: str, tag: str, partial: bool) -> str:
    closed = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    if closed:
        return closed.group(1).strip()
    if partial:
        opened = re.search(rf"<{tag}>(.*)$", text, re.DOTALL)
        if opened:
            # Hide a half-received closing tag such as "</ans".
            return re.sub(r"<[^>]*$", "", opened.group(1)).strip()
    return ""


def parse_answer(text: str, question_id: str, partial: bool = False) -> AnswerSuggestion:
    suggestion = AnswerSuggestion(question_id=question_id, raw_text=text)
    answer = _section(text, "answer", partial)
    if not answer and not partial and not any(f"<{t}>" in text for t in _TAGS):
        answer = text.strip()  # model ignored the format: show the text as-is
    suggestion.spoken_answer = answer

    points = _section(text, "key_points", partial)
    suggestion.key_points = [
        re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
        for line in points.splitlines()
        if line.strip()
    ]
    example = _section(text, "example", partial)
    suggestion.resume_example = "" if example.lower().startswith("none in profile") else example

    quality = _section(text, "context_quality", partial).lower()
    for level in (ContextQuality.HIGH, ContextQuality.MEDIUM, ContextQuality.LOW):
        if quality.startswith(level.value):
            suggestion.context_quality = level
            break

    needs = _section(text, "needs_personalization", partial)
    if needs and needs.strip().lower().rstrip(".") != "none":
        suggestion.needs_personalization = [n.strip() for n in needs.split(",") if n.strip()]
    return suggestion
