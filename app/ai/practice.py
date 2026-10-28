"""Interview practice: Claude asks questions and evaluates the user's answers."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.client import ClaudeService
from app.ai.prompts import build_profile_block
from app.profiles.models import UserProfile


class PracticeQuestion(BaseModel):
    question: str
    category: str = Field(description="intro, behavioral, technical, project, sql, python, react_api, data_analyst, scenario or career")
    what_a_strong_answer_covers: str


class PracticeQuestionSet(BaseModel):
    questions: list[PracticeQuestion]


class PracticeEvaluation(BaseModel):
    relevance: int = Field(description="1-5")
    clarity: int = Field(description="1-5")
    technical_accuracy: int = Field(description="1-5; 0 if not a technical question")
    structure: int = Field(description="1-5")
    overall: int = Field(description="1-5")
    strengths: list[str]
    improvements: list[str]
    improved_answer: str
    missing_personal_details: list[str]

    def clamp(self) -> PracticeEvaluation:
        for name in ("relevance", "clarity", "structure", "overall"):
            setattr(self, name, max(1, min(5, int(getattr(self, name)))))
        self.technical_accuracy = max(0, min(5, int(self.technical_accuracy)))
        return self


_QUESTIONS_SYSTEM = """\
You are an experienced interviewer preparing a realistic mock interview. Write questions a \
real interviewer for the given role would ask, mixing categories as requested. Where the \
candidate profile mentions specific projects or jobs, ask about them by name. Do not assume \
facts that are not in the profile."""

_EVAL_SYSTEM = """\
You are a supportive but honest interview coach. Evaluate the candidate's answer to the \
question for relevance, clarity, technical accuracy (0 when the question is not technical) \
and structure, each on a 1-5 scale, plus an overall score. Give specific, actionable \
feedback. Then write an improved example answer in the candidate's voice that uses only \
facts from the candidate's answer and profile; never invent employers, degrees, metrics or \
projects - use bracketed placeholders such as [result you achieved] where a detail is \
missing, and list those details in missing_personal_details. Keep the improved answer \
speakable in 30-90 seconds. For behavioural questions use STAR."""


class PracticeCoach:
    def __init__(self, service: ClaudeService) -> None:
        self._service = service

    async def generate_questions(
        self, profile: UserProfile, role: str, count: int = 5, focus: str = "mixed"
    ) -> tuple[list[PracticeQuestion], int, int]:
        user = (
            f"{build_profile_block(profile)}\n\n"
            f"Target role: {role or profile.target_job_title or 'not specified'}\n"
            f"Focus: {focus}\n"
            f"Write {count} distinct interview questions in the order an interviewer would ask them."
        )
        result, tin, tout = await self._service.parse(_QUESTIONS_SYSTEM, user, PracticeQuestionSet)
        return result.questions[:count], tin, tout

    async def evaluate(
        self, profile: UserProfile, question: str, answer: str, role: str = ""
    ) -> tuple[PracticeEvaluation, int, int]:
        user = (
            f"{build_profile_block(profile)}\n\n"
            f"Target role: {role or profile.target_job_title or 'not specified'}\n\n"
            f"<question>{question}</question>\n\n"
            f"<candidate_answer>{answer}</candidate_answer>"
        )
        result, tin, tout = await self._service.parse(_EVAL_SYSTEM, user, PracticeEvaluation)
        return result.clamp(), tin, tout
