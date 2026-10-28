"""Prompt construction.

Layout (for prompt caching, stable content first):
  system[0]  fixed instructions             - identical for every request
  system[1]  candidate profile + job desc   - stable for a session  (cache breakpoint)
  messages   recent conversation + question - changes every request
"""

from __future__ import annotations

from collections.abc import Sequence

from app.config import AnswerLength
from app.models import AnswerMode, DetectedQuestion, QuestionCategory
from app.profiles.models import UserProfile

ANSWER_SYSTEM_PROMPT = """\
You help a job candidate during a live interview or meeting by drafting answers they can \
say in their own voice. The candidate reads your draft quickly on a small screen while \
listening, so it must be easy to scan and sound natural when spoken.

Ground every answer in the candidate profile provided below. This is the most important \
rule: never invent or embellish degrees, employers, job titles, dates, projects, metrics, \
achievements, certifications or technical skills. Use only facts present in the profile. \
When the question needs a personal fact the profile lacks, write a natural placeholder in \
square brackets, such as [your team size] or [a specific example from your internship], \
and list what is missing in <needs_personalization>. A short honest answer with \
placeholders is far more useful to the candidate than a polished answer they cannot defend \
in follow-up questions.

For general technical questions (concepts, SQL, Python, React, APIs, data analysis), give a \
correct, precise explanation. Connect it to the candidate's own experience only where the \
profile genuinely supports that. If a question is ambiguous or the transcript looks garbled, \
answer the most likely interpretation and note the assumption briefly in the key points.

Write in first person as the candidate, in the speaking style given in the profile. Use \
spoken language: short sentences, no markdown headings, no bullet points inside the spoken \
answer, no emojis.

Respond with exactly these tagged sections and nothing else:
<answer>The spoken answer.</answer>
<key_points>3 to 5 short lines, one per line, each starting with "- ".</key_points>
<example>One relevant example taken from the profile (project, job or achievement) and how \
to mention it, or "None in profile" if nothing fits.</example>
<context_quality>high, medium or low: how well the profile supports a personalised answer \
to this question.</context_quality>
<needs_personalization>Comma-separated list of missing personal details, or "none".</needs_personalization>
"""

MODE_INSTRUCTIONS: dict[AnswerMode, str] = {
    AnswerMode.CONCISE: "Keep the answer tight and direct; lead with the main point.",
    AnswerMode.DETAILED: "Give a fuller answer with one supporting detail per main point. Allow up to 1.5x the target length.",
    AnswerMode.STAR: (
        "Structure the answer with the STAR method - Situation, Task, Action, Result - as "
        "flowing speech (no labels). Use a real situation from the profile; if none fits, use "
        "bracketed placeholders for the situation and result rather than inventing one."
    ),
    AnswerMode.TECHNICAL: (
        "Answer as a precise technical explanation: definition, how it works, a short example, "
        "and a trade-off or pitfall. Mention where the candidate used it only if the profile shows it."
    ),
    AnswerMode.BEGINNER: (
        "Explain in beginner-friendly terms with a simple analogy, while staying technically correct."
    ),
}

CATEGORY_HINTS: dict[QuestionCategory, str] = {
    QuestionCategory.INTRO: "Use a present-past-future structure: current role/studies, relevant background, why this role.",
    QuestionCategory.BEHAVIORAL: "Behavioural question: use STAR and a real example from the profile.",
    QuestionCategory.PROJECT: "Project question: prioritise the candidate's actual project details (goal, their role, tech, outcome).",
    QuestionCategory.CAREER: "Career question: be honest and specific; for weaknesses pick a real, non-critical one and show improvement steps.",
    QuestionCategory.SCENARIO: "Scenario question: explain the approach step by step, including how to clarify requirements and verify results.",
    QuestionCategory.SQL: "SQL question: if a query helps, describe it briefly in words; keep any query short enough to say aloud.",
    QuestionCategory.PYTHON: "Python question: give a correct explanation with a tiny example described in words.",
    QuestionCategory.REACT_API: "React/API question: be precise about concepts (state, effects, rendering, HTTP methods, status codes, auth).",
    QuestionCategory.DATA_ANALYST: "Data analysis question: mention the analysis process (question, data, cleaning, method, insight, communication).",
    QuestionCategory.TECHNICAL: "Technical question: accurate definition, how it works, and a trade-off.",
    QuestionCategory.GENERAL: "",
}

_SECONDS_PER_LENGTH = {AnswerLength.SHORT: 30, AnswerLength.MEDIUM: 60, AnswerLength.LONG: 90}


def default_mode_for(category: QuestionCategory, fallback: AnswerMode) -> AnswerMode:
    if fallback != AnswerMode.CONCISE:
        return fallback
    if category == QuestionCategory.BEHAVIORAL:
        return AnswerMode.STAR
    return fallback


def build_profile_block(profile: UserProfile) -> str:
    if profile.is_empty():
        header = (
            "The candidate has not provided a resume or profile yet. Treat every personal fact "
            "as missing and use bracketed placeholders.\n\n"
        )
    else:
        header = ""
    return f"<candidate_profile>\n{header}{profile.to_prompt_text()}\n</candidate_profile>"


def build_system(profile: UserProfile) -> list[dict]:
    return [
        {"type": "text", "text": ANSWER_SYSTEM_PROMPT},
        {
            "type": "text",
            "text": build_profile_block(profile),
            # Profile is stable across a session: cache everything up to here.
            "cache_control": {"type": "ephemeral"},
        },
    ]


def build_answer_user_message(
    question: DetectedQuestion,
    mode: AnswerMode,
    length: AnswerLength,
    recent_transcript: Sequence[tuple[str, str]] = (),
) -> str:
    """``recent_transcript`` is a list of (speaker label, text) lines, oldest first."""
    words = length.target_words
    if mode == AnswerMode.DETAILED:
        words = int(words * 1.5)
    lines = []
    if recent_transcript:
        convo = "\n".join(f"{who}: {text}" for who, text in recent_transcript)
        lines.append(f"<recent_conversation>\n{convo}\n</recent_conversation>")
    if question.context:
        lines.append(f"<interviewer_lead_in>{question.context}</interviewer_lead_in>")
    lines.append(f"<question category=\"{question.category.value}\">{question.text}</question>")
    guidance = [
        f"Target length: about {words} words (roughly {_SECONDS_PER_LENGTH[length]} seconds spoken).",
        MODE_INSTRUCTIONS[mode],
    ]
    hint = CATEGORY_HINTS.get(question.category, "")
    if hint:
        guidance.append(hint)
    lines.append("<instructions>\n" + "\n".join(guidance) + "\n</instructions>")
    return "\n\n".join(lines)
