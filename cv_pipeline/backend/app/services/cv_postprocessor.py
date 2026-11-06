"""Run extraction and trade_passport_name renaming on fetched PDFs.

Call process_pdf_files(result.saved_files, extract_one, on_candidate=...) from the
existing module that already defines/imports extract_one. The callback can write
candidate data to your existing Excel writer.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Callable, Iterable, Any
from app.core.file_naming import rename_candidate_pdf

log = logging.getLogger("cv_postprocessor")


def process_pdf_files(
    paths: Iterable[str | Path],
    extract_one: Callable[..., tuple[Any, str, str]],
    *,
    provider: str | None = None,
    on_candidate: Callable[[Any, Path, str, str], None] | None = None,
) -> list[tuple[Any, Path]]:
    """Extract each PDF, rename it, and optionally pass data to Excel/database writer.

    on_candidate receives (candidate, final_pdf_path, extraction_method, extracted_text).
    A failed CV is logged and does not stop processing the remaining files.
    """
    processed: list[tuple[Any, Path]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or path.suffix.lower() != ".pdf":
            continue
        try:
            candidate, method, text = extract_one(path, provider=provider)
            final_path = rename_candidate_pdf(path, candidate)
            if on_candidate:
                on_candidate(candidate, final_path, method, text)
            log.info("Extracted and renamed CV: %s", final_path)
            processed.append((candidate, final_path))
        except Exception:
            log.exception("Extraction/naming failed for CV: %s", path)
    return processed


def process_pdf_directory(
    root: str | Path,
    extract_one: Callable[..., tuple[Any, str, str]],
    *,
    provider: str | None = None,
    on_candidate: Callable[[Any, Path, str, str], None] | None = None,
) -> list[tuple[Any, Path]]:
    """Process existing PDFs recursively; already named files may be processed again."""
    root = Path(root)
    return process_pdf_files(root.rglob("*.pdf"), extract_one, provider=provider, on_candidate=on_candidate)
