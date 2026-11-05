"""Candidate PDF naming helpers: trade_passport_name.pdf."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional


def _filename_part(value: Optional[str], fallback: str) -> str:
    value = str(value or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip(" _. ")
    return value or fallback


def candidate_pdf_filename(candidate) -> str:
    trade = _filename_part(getattr(candidate, "current_job_title", None), "UnknownTrade")
    passport = _filename_part(getattr(candidate, "passport_number", None), "UnknownPassport")
    name = _filename_part(getattr(candidate, "full_name", None), "UnknownName")
    return f"{trade}_{passport}_{name}.pdf"


def rename_candidate_pdf(path: Path, candidate) -> Path:
    """Rename PDF without mutating candidate fields; avoid overwriting existing files."""
    path = Path(path)
    if path.suffix.lower() != ".pdf" or not path.exists():
        return path
    base = candidate_pdf_filename(candidate)
    destination = path.with_name(base)
    counter = 2
    while destination.exists() and destination.resolve() != path.resolve():
        destination = path.with_name(f"{Path(base).stem}_{counter}.pdf")
        counter += 1
    if destination.resolve() != path.resolve():
        path.rename(destination)
    return destination
