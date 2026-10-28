import time

import pytest

from app.config import AnswerLength, Retention
from app.profiles.models import Experience, Project, UserProfile
from app.profiles.repository import ProfileRepository
from app.profiles.resume_import import ResumeImportError, clean_text, extract_resume_text
from app.profiles.text_format import (
    format_experience,
    format_projects,
    parse_experience,
    parse_list,
    parse_projects,
)


def test_profile_crud(db):
    repo = ProfileRepository(db)
    assert repo.active() == (None, UserProfile())
    pid = repo.save(UserProfile(name="Data roles", skills=["SQL", "Python"]))
    active_id, active = repo.active()
    assert active_id == pid and active.skills == ["SQL", "Python"]
    pid2 = repo.save(UserProfile(name="Web roles"))
    repo.set_active(pid2)
    assert repo.active()[0] == pid2
    repo.delete(pid2)
    assert repo.active()[0] == pid  # falls back to remaining profile
    repo.delete(pid)
    assert repo.list() == []


def test_retention_purge(db):
    sid = db.start_session("live", "zoom", "claude-opus-5-5")
    old = time.time() - 10 * 86400
    db.add_transcript_line(sid, "interviewer", "old line", ts=old)
    db.add_transcript_line(sid, "interviewer", "new line")
    db.add_qa(sid, "old q", "general", "a", ts=old)
    assert db.purge_expired(Retention.FOREVER) == 0
    assert db.purge_expired(Retention.DAYS_7) == 2
    assert db.count_content() == (1, 0)
    db.delete_all_content()
    assert db.count_content() == (0, 0)
    assert db.recent_sessions()[0].id == sid  # stats survive


def test_delete_everything(db):
    ProfileRepository(db).save(UserProfile(name="x"))
    sid = db.start_session("live", None, None)
    db.add_transcript_line(sid, "candidate", "hi")
    db.delete_everything()
    assert db.list_profiles() == [] and db.recent_sessions() == [] and db.count_content() == (0, 0)


def test_profile_prompt_marks_missing_sections():
    text = UserProfile(target_job_title="Data Analyst").to_prompt_text()
    assert "Data Analyst" in text
    assert "(not provided)" in text


def test_profile_prompt_includes_structured_data():
    p = UserProfile(
        projects=[Project(name="Sales Dashboard", technologies=["Power BI"], outcomes="used by 3 teams")],
        experience=[Experience(title="Intern", company="Acme", period="2024", highlights=["Automated reports"])],
    )
    text = p.to_prompt_text()
    assert "Sales Dashboard" in text and "Power BI" in text and "Automated reports" in text
    assert 0 < p.completeness() < 1


def test_resume_truncation_is_explicit():
    text = UserProfile(resume_text="x" * 30000).to_prompt_text(max_resume_chars=1000)
    assert "truncated" in text


def test_text_format_roundtrip():
    exp_text = "Data Analyst Intern | Acme | Jun 2024 - Aug 2024\n- Built dashboard\n- Wrote SQL\n\nTA | Uni | 2023"
    exp = parse_experience(exp_text)
    assert exp[0].company == "Acme" and exp[0].highlights == ["Built dashboard", "Wrote SQL"]
    assert exp[1].title == "TA"
    assert parse_experience(format_experience(exp)) == exp

    proj_text = "Recommender | Python, pandas | Solo\nContent-based recommender.\nOutcome: demoed to class"
    proj = parse_projects(proj_text)
    assert proj[0].technologies == ["Python", "pandas"] and proj[0].role == "Solo"
    assert proj[0].outcomes == "demoed to class"
    assert parse_projects(format_projects(proj)) == proj


def test_parse_list():
    assert parse_list("Python, SQL,\nReact", ",") == ["Python", "SQL", "React"]
    assert parse_list("- one\n- two\n\n") == ["one", "two"]


def test_import_txt(tmp_path):
    f = tmp_path / "resume.txt"
    f.write_text("Jane Doe\n\n\n\nSkills:   Python,\tSQL\nExperience with develop-\nment " + "x" * 300, encoding="utf-8")
    result = extract_resume_text(f)
    assert "Skills: Python, SQL" in result.text
    assert "development" in result.text
    assert "\n\n\n" not in result.text
    assert result.warnings == []


def _minimal_pdf(text: str) -> bytes:
    """Hand-built one-page PDF with a Helvetica text stream."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def test_import_pdf(tmp_path):
    f = tmp_path / "resume.pdf"
    f.write_bytes(_minimal_pdf("Jane Doe - Python and SQL developer"))
    result = extract_resume_text(f)
    assert "Python and SQL developer" in result.text
    assert result.pages == 1
    assert result.warnings  # short text triggers the scanned-PDF hint


def test_import_rejects_bad_files(tmp_path):
    bad = tmp_path / "resume.docx"
    bad.write_text("x")
    with pytest.raises(ResumeImportError):
        extract_resume_text(bad)
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("not a pdf")
    with pytest.raises(ResumeImportError):
        extract_resume_text(fake_pdf)


def test_clean_text():
    assert clean_text("a\r\nb\x00  c") == "a\nb c"


def test_answer_length_targets():
    assert AnswerLength.SHORT.target_words < AnswerLength.MEDIUM.target_words < AnswerLength.LONG.target_words
