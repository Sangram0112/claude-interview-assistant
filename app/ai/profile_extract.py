"""Optional: turn pasted/extracted resume text into structured profile fields with Claude.

This sends the resume text to Anthropic, so the UI asks for confirmation first. Users can
always fill in the profile manually instead.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.ai.client import ClaudeService
from app.profiles.models import Experience, Project, UserProfile


class ExtractedProfile(BaseModel):
    candidate_name: str
    summary: str
    skills: list[str]
    education: list[str]
    experience: list[Experience]
    projects: list[Project]
    achievements: list[str]


_SYSTEM = """\
Extract structured information from the resume text. Copy facts exactly as stated; do not \
infer, embellish or add anything that is not written in the resume. Leave a field empty (or \
an empty list) when the resume does not contain it. For achievements and project outcomes, \
include only results the resume explicitly states, keeping any numbers exactly as written. \
The summary is 2-3 sentences built only from resume facts."""


async def extract_profile(service: ClaudeService, resume_text: str) -> tuple[ExtractedProfile, int, int]:
    user = f"<resume>\n{resume_text}\n</resume>"
    return await service.parse(_SYSTEM, user, ExtractedProfile)


def merge_into(profile: UserProfile, extracted: ExtractedProfile, overwrite: bool = False) -> UserProfile:
    """Fill empty profile fields from the extraction (or replace them when ``overwrite``)."""
    updated = profile.model_copy(deep=True)
    for field in ("candidate_name", "summary"):
        value = getattr(extracted, field)
        if value and (overwrite or not getattr(updated, field)):
            setattr(updated, field, value)
    for field in ("skills", "education", "experience", "projects", "achievements"):
        value = getattr(extracted, field)
        if value and (overwrite or not getattr(updated, field)):
            setattr(updated, field, list(value))
    return updated
