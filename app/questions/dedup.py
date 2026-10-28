"""Suppress repeated questions (re-asked, rephrased slightly, or re-transcribed)."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

_FILLERS = {
    "um", "uh", "so", "okay", "ok", "like", "just", "please", "actually", "basically",
    "maybe", "kind", "of", "sort", "the", "a", "an", "you", "know", "can", "could",
    "would", "me", "us", "your", "yourself", "to",
}
_KEEP_SHORT = {"why", "how", "sql", "api", "ui", "ux", "ai", "ml"}


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> set[str]:
    return {t for t in normalize(text).split() if (t not in _FILLERS and len(t) > 2) or t in _KEEP_SHORT}


def similarity(a: str, b: str) -> float:
    """Max of token-set Jaccard and character-sequence ratio over normalised text."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = content_tokens(a), content_tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    return max(jaccard, ratio)


@dataclass
class QuestionDeduplicator:
    threshold: float = 0.82
    window_s: float = 600.0
    max_items: int = 200
    _history: deque[tuple[float, str]] = field(default_factory=deque)

    def is_duplicate(self, text: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        self._expire(now)
        return any(similarity(text, previous) >= self.threshold for _, previous in self._history)

    def remember(self, text: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self._history.append((now, text))
        while len(self._history) > self.max_items:
            self._history.popleft()

    def check_and_remember(self, text: str, now: float | None = None) -> bool:
        """Return True if ``text`` is new (and remember it); False if duplicate."""
        if self.is_duplicate(text, now):
            return False
        self.remember(text, now)
        return True

    def clear(self) -> None:
        self._history.clear()

    def _expire(self, now: float) -> None:
        while self._history and now - self._history[0][0] > self.window_s:
            self._history.popleft()
