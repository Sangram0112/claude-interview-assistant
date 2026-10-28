"""Plain-text editing format for structured profile sections.

Experience (blank line between entries)::

    Data Analyst Intern | Acme Corp | Jun 2024 - Aug 2024
    - Built a weekly sales dashboard in Power BI

Projects (blank line between entries)::

    Movie Recommender | Python, pandas | Solo developer
    Content-based recommender for 5,000 movies.
    Outcome: Presented at the department showcase
"""

from __future__ import annotations

import re

from app.profiles.models import Experience, Project


def _blocks(text: str) -> list[list[str]]:
    blocks, current = [], []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip():
            current.append(line.rstrip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _bullet(line: str) -> str | None:
    m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$", line)
    return m.group(1).strip() if m else None


def parse_experience(text: str) -> list[Experience]:
    items = []
    for block in _blocks(text):
        head = [p.strip() for p in block[0].split("|")]
        highlights = []
        for line in block[1:]:
            highlights.append(_bullet(line) or line.strip())
        items.append(
            Experience(
                title=head[0] if head else "",
                company=head[1] if len(head) > 1 else "",
                period=head[2] if len(head) > 2 else "",
                highlights=[h for h in highlights if h],
            )
        )
    return items


def format_experience(items: list[Experience]) -> str:
    out = []
    for e in items:
        lines = [" | ".join(p for p in (e.title, e.company, e.period))]
        lines += [f"- {h}" for h in e.highlights]
        out.append("\n".join(lines))
    return "\n\n".join(out)


def parse_projects(text: str) -> list[Project]:
    items = []
    for block in _blocks(text):
        head = [p.strip() for p in block[0].split("|")]
        description, outcomes = [], []
        for line in block[1:]:
            m = re.match(r"^\s*(?:outcomes?|results?)\s*:\s*(.*)$", line, re.IGNORECASE)
            if m:
                outcomes.append(m.group(1).strip())
            else:
                description.append(_bullet(line) or line.strip())
        tech = [t.strip() for t in head[1].split(",") if t.strip()] if len(head) > 1 else []
        items.append(
            Project(
                name=head[0] if head else "",
                technologies=tech,
                role=head[2] if len(head) > 2 else "",
                description=" ".join(d for d in description if d),
                outcomes="; ".join(o for o in outcomes if o),
            )
        )
    return items


def format_projects(items: list[Project]) -> str:
    out = []
    for p in items:
        lines = [" | ".join((p.name, ", ".join(p.technologies), p.role)).rstrip(" |")]
        if p.description:
            lines.append(p.description)
        if p.outcomes:
            lines.append(f"Outcome: {p.outcomes}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def parse_list(text: str, separator: str = "\n") -> list[str]:
    parts = text.split(separator) if separator != "," else re.split(r"[,\n]", text)
    cleaned = []
    for part in parts:
        value = (_bullet(part) or part).strip()
        if value:
            cleaned.append(value)
    return cleaned
