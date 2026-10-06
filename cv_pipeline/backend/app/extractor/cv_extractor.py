"""
STEP 2 - Process every CV in the CV folder:  file -> text -> LLM -> Candidate -> Excel.

For each file:
  1. Compute its SHA-256 hash. If data/state/processed_files.json says that hash
     was already extracted successfully -> skip (so re-running is cheap and safe).
  2+3. The LLM parses the resume (converter.prepare_for_llm decides what it receives):
        CV_PARSER=llm (default): PDF/image -> the original file is sent to the LLM   ("llm-file")
                                 DOCX      -> its raw text is sent                     ("docx-text")
        CV_PARSER=docling:       Docling Markdown is sent; scans fall back to the file ("docling")
  4. Validated Candidate -> one row in Excel (upsert by hash).
  5. Mark the hash as processed. Failures are recorded and retried next run.

Any text that was sent is cached in data/state/text_cache/<hash>.md so the job-fit step can
reuse it; for PDFs/images in llm mode the job-fit step attaches the original file again.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from app.core.config import CV_EXTENSIONS, settings
from app.core.logger import get_logger
from app.core.models import Candidate
from app.core.state import JsonState, file_sha256
from app.extractor import converter
from app.extractor.prompts import cv_extraction_system_prompt
from app.llm.client import get_llm
from app.storage.excel_store import candidate_to_row, load_candidates, upsert_rows

log = get_logger("extractor")

MAX_TEXT_CHARS = 60_000   # safety cap: ~15k tokens is far more than any real CV


@dataclass
class ExtractResult:
    found: int = 0
    extracted: int = 0
    skipped_already_done: int = 0
    failed: int = 0
    total_rows_in_excel: int = 0
    errors: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def text_cache_dir() -> Path:
    d = settings.state_folder / "text_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_cv_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in CV_EXTENSIONS)


def extract_one(path: Path, provider: Optional[str] = None) -> tuple[Candidate, str, str]:
    """Extract one CV. Returns (candidate, method, text_sent). Raises on failure."""
    llm = get_llm(provider)
    system = cv_extraction_system_prompt()

    # 1) decide what the LLM gets: the file itself (PDF/image) or text (DOCX / Docling)
    text, file, method = converter.prepare_for_llm(path)

    # 2) the LLM parses the resume and fills the Candidate schema
    candidate = llm.extract(
        Candidate, system,
        text=f"CV CONTENT:\n\n{text[:MAX_TEXT_CHARS]}" if text.strip() else None,
        file=file,
    )
    candidate.fill_age_from_dob()
    return candidate, method, text


def run_extraction(provider: Optional[str] = None, force: bool = False,
                   only_file: Optional[str] = None, limit: Optional[int] = None,
                   progress: Optional[Callable[[int, int, str], None]] = None) -> ExtractResult:
    """
    provider : "claude" / "gemini" / None (= .env default)
    force    : re-extract files that were already processed
    only_file: process just this file name (debugging)
    limit    : stop after N extractions (testing / cost control)
    progress : optional callback(done, total, current_file) - used by the API to show progress
    """
    settings.ensure_dirs()
    processed = JsonState(settings.state_folder / "processed_files.json")
    manifest = JsonState(settings.state_folder / "attachments_manifest.json").get("by_hash", {})
    provider_name = (provider or settings.llm_provider).lower()

    files = list_cv_files(settings.cv_folder)
    if only_file:
        files = [f for f in files if f.name == only_file]
    res = ExtractResult(found=len(files))
    log.info("Found %d CV file(s) in %s", len(files), settings.cv_folder)

    for i, path in enumerate(files, start=1):
        if progress:
            progress(i - 1, len(files), path.name)
        if limit is not None and res.extracted >= limit:
            break
        digest = file_sha256(path)
        prev = processed.get(digest)
        if prev and prev.get("status") == "ok" and not force:
            res.skipped_already_done += 1
            continue

        log.info("Extracting %s ...", path.name)
        t0 = time.time()
        try:
            candidate, method, text = extract_one(path, provider_name)
            if text:
                (text_cache_dir() / f"{digest}.md").write_text(text, encoding="utf-8")

            row = candidate_to_row(
                candidate, file_name=path.name, file_hash=digest,
                mail_meta=manifest.get(digest, {}), method=method, provider=provider_name,
            )
            res.total_rows_in_excel = upsert_rows([row])   # saved immediately -> crash-safe

            processed.set(digest, {
                "status": "ok", "file": path.name, "method": method, "llm": provider_name,
                "name": candidate.full_name, "at": datetime.now().isoformat(timespec="seconds"),
            })
            res.extracted += 1
            log.info("  OK  %-30s exp=%s  (%s, %.1fs)", candidate.full_name,
                     candidate.total_experience_years, method, time.time() - t0)
        except Exception as e:  # noqa: BLE001 - one bad CV must not stop the batch
            res.failed += 1
            res.errors[path.name] = str(e)
            processed.set(digest, {
                "status": "error", "file": path.name, "error": str(e)[:500],
                "at": datetime.now().isoformat(timespec="seconds"),
            })
            log.error("  FAIL %s: %s", path.name, e)
            log.debug(traceback.format_exc())
        finally:
            processed.save()

    if progress:
        progress(len(files), len(files), "done")
    res.total_rows_in_excel = len(load_candidates())
    log.info("Extraction done: %s", {k: v for k, v in res.as_dict().items() if k != "errors"})
    return res
