"""Shared, Qt-free domain types used across audio, transcription, AI and UI layers."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class Speaker(str, Enum):
    """Who produced a piece of audio. Derived from the capture source, not diarization."""

    INTERVIEWER = "interviewer"  # system/loopback audio (the other meeting participants)
    CANDIDATE = "candidate"  # the user's microphone


class QuestionCategory(str, Enum):
    INTRO = "intro"
    BEHAVIORAL = "behavioral"
    TECHNICAL = "technical"
    PROJECT = "project"
    SQL = "sql"
    PYTHON = "python"
    REACT_API = "react_api"
    DATA_ANALYST = "data_analyst"
    SCENARIO = "scenario"
    CAREER = "career"  # strengths, weaknesses, goals
    GENERAL = "general"


class AnswerMode(str, Enum):
    CONCISE = "concise"
    DETAILED = "detailed"
    STAR = "star"
    TECHNICAL = "technical"
    BEGINNER = "beginner"


class ContextQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class TranscriptEvent:
    """Output of a transcription provider.

    ``is_final`` False means an interim hypothesis that may be replaced by a later
    event with the same ``utterance_id``.
    """

    speaker: Speaker
    text: str
    is_final: bool
    utterance_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class DetectedQuestion:
    text: str
    category: QuestionCategory
    speaker: Speaker
    context: str = ""  # preceding sentence(s), e.g. "I see you built X."
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)


@dataclass(slots=True)
class AnswerSuggestion:
    question_id: str
    spoken_answer: str = ""
    key_points: list[str] = field(default_factory=list)
    resume_example: str = ""
    context_quality: ContextQuality = ContextQuality.UNKNOWN
    needs_personalization: list[str] = field(default_factory=list)
    raw_text: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int = 0


class PipelineState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    LISTENING = "listening"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"
