"""Local resume text extraction (PDF / TXT / MD). Nothing is uploaded here."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 15 * 1024 * 1024
SUPPORTED_SUFFIXES = {".pdf", ".txt", ".md"}


class ResumeImportError(Exception):
    """User-presentable import failure."""


@dataclass(slots=True)
class ExtractionResult:
    text: str
    pages: int = 0
    warnings: list[str] = field(default_factory=list)


def extract_resume_text(path: str | Path) -> ExtractionResult:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ResumeImportError(f"Unsupported file type '{suffix}'. Use PDF or TXT.")
    if not path.is_file():
        raise ResumeImportError("File not found.")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ResumeImportError("File is larger than 15 MB.")

    if suffix == ".pdf":
        result = _extract_pdf(path)
    else:
        result = ExtractionResult(text=_read_text(path))

    result.text = clean_text(result.text)
    if len(result.text) < 200:
        result.warnings.append(
            "Very little text was extracted. If this is a scanned PDF, paste the text manually "
            "or export the resume as a text-based PDF."
        )
    return result


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(path: Path) -> ExtractionResult:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover
        raise ResumeImportError("PDF support requires the 'pypdf' package.") from exc

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ResumeImportError("The PDF is password protected.") from exc
        texts: list[str] = []
        warnings: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                texts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - one bad page should not fail the import
                warnings.append(f"Page {index} could not be read.")
        return ExtractionResult(text="\n\n".join(texts), pages=len(reader.pages), warnings=warnings)
    except PdfReadError as exc:
        raise ResumeImportError("The file is not a readable PDF.") from exc


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Re-join words hyphenated across line breaks: "develop-\nment" -> "development"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return text.strip()
