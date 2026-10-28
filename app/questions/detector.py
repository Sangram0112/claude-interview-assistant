"""Turns final transcript utterances into interview questions.

Heuristic and local (no API call), so it adds no latency or cost. It recognises explicit
questions ("...?"), interrogative openers ("How would you...") and imperative interview
prompts ("Tell me about yourself", "Walk me through...").
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

from app.models import DetectedQuestion, QuestionCategory, Speaker, TranscriptEvent
from app.questions.dedup import QuestionDeduplicator

_SENTENCE_SPLIT = re.compile(r"(?<=[.?!])\s+(?=[A-Z0-9\"'])")

_INTERROGATIVE_OPENERS = (
    "what", "why", "how", "when", "where", "who", "whom", "which", "whose",
    "can you", "could you", "would you", "will you", "should you",
    "do you", "did you", "does", "have you", "has", "had you",
    "are you", "were you", "is there", "is it", "are there", "was there",
    "if you", "suppose", "imagine", "let's say", "say you",
)
_IMPERATIVE_PROMPTS = (
    "tell me", "tell us", "describe", "explain", "walk me through", "walk us through",
    "give me an example", "give us an example", "share an example", "share a time",
    "talk about", "talk me through", "take me through", "elaborate", "introduce yourself",
    "i'd like to hear", "i would like to hear", "i'd love to hear", "help me understand",
    "write a query", "write a function", "how about", "what about",
)
_LEADING_FILLERS = re.compile(
    r"^(?:(?:so|okay|ok|alright|all right|right|and|well|um+|uh+|great|cool|now|next|then|sure|"
    r"perfect|nice|thanks|thank you|good)[,.!]?\s+)+",
    re.IGNORECASE,
)

_CATEGORY_KEYWORDS: list[tuple[QuestionCategory, tuple[str, ...]]] = [
    (QuestionCategory.INTRO, ("about yourself", "introduce yourself", "your background", "walk me through your resume", "walk me through your background")),
    (QuestionCategory.CAREER, ("strength", "weakness", "five years", "5 years", "career goal", "why do you want", "why should we hire", "why this company", "why our company", "where do you see yourself", "salary expectation", "why are you leaving", "motivat*")),
    (QuestionCategory.SQL, ("sql", "query", "queries", "join", "group by", "database", "index", "normaliz*", "window function", "primary key", "foreign key")),
    (QuestionCategory.PYTHON, ("python", "pandas", "list comprehension", "decorator", "generator", "django", "flask", "numpy", "dictionary", "gil")),
    (QuestionCategory.REACT_API, ("react", "hook", "usestate", "useeffect", "component", "redux", "rest api", "api", "endpoint", "graphql", "http", "frontend", "front-end", "javascript", "typescript")),
    (QuestionCategory.DATA_ANALYST, ("dashboard", "excel", "tableau", "power bi", "kpi", "metric", "a/b test", "statistic*", "data cleaning", "visualiz*", "analys*", "insight")),
    (QuestionCategory.BEHAVIORAL, ("a time when", "a time you", "tell me about a time", "conflict", "disagree", "challenge you faced", "difficult*", "mistake", "failure", "failed", "deadline", "teamwork", "leadership", "led a", "pressure", "feedback", "proud")),
    (QuestionCategory.PROJECT, ("project", "you built", "you worked on", "your role in", "architecture of", "on your resume", "internship", "capstone", "thesis")),
    (QuestionCategory.SCENARIO, ("what would you do", "how would you handle", "how would you approach", "suppose", "imagine", "let's say", "if you were", "scenario", "hypothetical")),
    (QuestionCategory.TECHNICAL, ("difference between", "how does", "explain", "algorithm", "complexity", "design a", "system design", "scalab*", "scaling", "cache", "thread", "memory", "oop", "object-oriented", "data structure", "cloud", "docker", "git", "testing")),
]


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Whole-word match (plural allowed); a trailing '*' marks a stem matched as a prefix.

    Word boundaries matter: "joining" must not match the SQL keyword "join".
    """
    if keyword.endswith("*"):
        return re.compile(rf"\b{re.escape(keyword[:-1])}")
    return re.compile(rf"\b{re.escape(keyword)}(?:s|es)?\b")


_CATEGORY_PATTERNS = [(cat, [_keyword_pattern(k) for k in kws]) for cat, kws in _CATEGORY_KEYWORDS]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def strip_fillers(sentence: str) -> str:
    return _LEADING_FILLERS.sub("", sentence).strip()


def is_question(sentence: str, min_words: int = 4) -> bool:
    core = strip_fillers(sentence)
    words = core.split()
    if len(words) < min_words:
        return False
    lower = core.lower()
    if lower.endswith("?"):
        return True
    if any(lower.startswith(p) for p in _IMPERATIVE_PROMPTS):
        return True
    # Whisper sometimes drops the question mark on interrogatives.
    if any(lower.startswith(p + " ") for p in _INTERROGATIVE_OPENERS):
        # Exclude statements like "What I mean is..." / "How we do it is..."
        return not re.match(r"^(what|how|when|where|why|who)\s+(i|we|they|he|she)\s+(mean|meant|do|did|said)\b", lower)
    return False


def classify(question: str) -> QuestionCategory:
    lower = question.lower()
    for category, patterns in _CATEGORY_PATTERNS:
        if any(p.search(lower) for p in patterns):
            return category
    return QuestionCategory.GENERAL


def _classify_with_context(text: str, context: str) -> QuestionCategory:
    category = classify(text)
    if category == QuestionCategory.GENERAL and context:
        category = classify(f"{context} {text}")
    return category


@dataclass
class QuestionDetector:
    """Stateful detector fed with *final* transcript events."""

    deduplicator: QuestionDeduplicator = field(default_factory=QuestionDeduplicator)
    speakers: frozenset[Speaker] = frozenset({Speaker.INTERVIEWER})
    min_words: int = 4
    context_window_s: float = 20.0
    _recent: dict[Speaker, deque[tuple[float, str]]] = field(default_factory=dict)

    def process(self, event: TranscriptEvent) -> list[DetectedQuestion]:
        if not event.is_final or event.speaker not in self.speakers:
            return []
        now = event.timestamp or time.time()
        recent = self._recent.setdefault(event.speaker, deque(maxlen=4))
        found: list[DetectedQuestion] = []
        for sentence in split_sentences(event.text):
            if is_question(sentence, self.min_words):
                text = strip_fillers(sentence)
                text = text[0].upper() + text[1:] if text else text
                if self.deduplicator.is_duplicate(text, now):
                    recent.append((now, sentence))
                    continue
                context = " ".join(s for ts, s in recent if now - ts <= self.context_window_s)
                question = DetectedQuestion(
                    text=text,
                    # The question decides; the lead-in only breaks ties for vague questions.
                    category=_classify_with_context(text, context),
                    speaker=event.speaker,
                    context=context,
                    timestamp=now,
                )
                self.deduplicator.remember(text, now)
                found.append(question)
            recent.append((now, sentence))
        return found

    def reset(self) -> None:
        self._recent.clear()
        self.deduplicator.clear()
