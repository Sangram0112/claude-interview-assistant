"""The candidate profile used to personalise answers."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.config import AnswerLength


class Project(BaseModel):
    name: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    role: str = ""
    outcomes: str = ""  # only results the user actually reported


class Experience(BaseModel):
    title: str = ""
    company: str = ""
    period: str = ""
    highlights: list[str] = Field(default_factory=list)


class UserProfile(BaseModel):
    name: str = "My profile"
    candidate_name: str = ""
    target_job_title: str = ""
    job_description: str = ""
    resume_text: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    preferred_answer_length: AnswerLength = AnswerLength.MEDIUM
    speaking_style: str = "Professional, warm and confident; plain language."

    def is_empty(self) -> bool:
        return not any(
            [
                self.resume_text.strip(),
                self.summary.strip(),
                self.skills,
                self.projects,
                self.experience,
                self.achievements,
            ]
        )

    def completeness(self) -> float:
        """0..1 score of how much personal context is available (drives the quality badge)."""
        checks = [
            bool(self.resume_text.strip() or self.summary.strip()),
            bool(self.target_job_title.strip()),
            bool(self.job_description.strip()),
            bool(self.skills),
            bool(self.projects),
            bool(self.experience),
            bool(self.achievements),
        ]
        return sum(checks) / len(checks)

    def to_prompt_text(self, max_resume_chars: int = 24000) -> str:
        """Render the profile as plain text for the model. Empty sections are labelled as such
        so the model knows the information is missing rather than inventing it."""

        def section(title: str, body: str) -> str:
            return f"## {title}\n{body.strip() if body.strip() else '(not provided)'}\n"

        projects = "\n".join(
            f"- {p.name}: {p.description}"
            + (f" | Role: {p.role}" if p.role else "")
            + (f" | Tech: {', '.join(p.technologies)}" if p.technologies else "")
            + (f" | Outcomes (user-reported): {p.outcomes}" if p.outcomes else "")
            for p in self.projects
        )
        experience = "\n".join(
            f"- {e.title} at {e.company} ({e.period})"
            + "".join(f"\n    * {h}" for h in e.highlights)
            for e in self.experience
        )
        resume = self.resume_text.strip()
        if len(resume) > max_resume_chars:
            # Structured fields carry the important facts; mark the cut explicitly.
            resume = resume[:max_resume_chars] + "\n[... resume text truncated for length ...]"

        parts = [
            section("Candidate name", self.candidate_name),
            section("Target job title", self.target_job_title),
            section("Professional summary", self.summary),
            section("Skills and technical stack", ", ".join(self.skills)),
            section("Education", "\n".join(f"- {e}" for e in self.education)),
            section("Work experience", experience),
            section("Projects (academic or professional)", projects),
            section("Key achievements (user-reported)", "\n".join(f"- {a}" for a in self.achievements)),
            section("Preferred speaking style", self.speaking_style),
            section("Job description", self.job_description),
            section("Full resume text", resume),
        ]
        return "\n".join(parts)
