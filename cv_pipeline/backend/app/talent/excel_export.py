"""
candidates.xlsx regenerated from Postgres: one row per CV, every row showing its candidate's
current values (manual edits included). CV File, mail details and dates stay per CV.

The rebuild refuses to run while the Excel still holds CVs that aren't in Postgres (before the
historical backfill), so it can never drop rows. Callers then fall back to adding rows.

request_refresh() coalesces many changes into one rebuild a few seconds later, for edits.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

import pandas as pd
from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.models import Candidate, CvDocument
from app.db.session import session_scope
from app.storage.excel_store import COLUMNS, KEY, SHEET, load_candidates, write_sheets
from app.talent.fields import current_age

log = get_logger("excel_export")

S3_LABELS = {"success": "Success", "failed": "Failed", "pending": "Pending", "missing": "Missing"}
REFRESH_DELAY_SECONDS = 5


class ExcelNotMigrated(RuntimeError):
    """The Excel holds CVs that aren't in Postgres yet: run backfill_talent.py first."""


def _when(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else None


def _row(candidate: Candidate, cv: CvDocument) -> dict:
    return {
        "Full Name": candidate.full_name, "Email": candidate.email, "Phone": candidate.phone,
        "Passport Number": candidate.passport_number, "Skills": ", ".join(candidate.skills or []),
        "Experience (Years)": float(candidate.experience_years) if candidate.experience_years is not None else None,
        "Education": candidate.education, "Current/Last Job Title": candidate.job_title,
        "Current/Last Company": candidate.company, "Age": current_age(candidate.birth_date, candidate.age),
        "Date of Birth": candidate.date_of_birth,
        "Certifications": ", ".join(candidate.certifications or []),
        "Languages": ", ".join(candidate.languages or []), "Location": candidate.location,
        "Summary": candidate.summary, "CV File": cv.file_name, "Received From": cv.mail_from,
        "Mail Subject": cv.mail_subject, "Mail Date": _when(cv.mail_date),
        "Extraction Method": cv.extraction_method, "LLM": cv.llm, "Extracted At": _when(cv.extracted_at),
        "Uploaded to S3": S3_LABELS.get(cv.s3_status, cv.s3_status), KEY: cv.file_hash,
    }


def regenerate_excel() -> int:
    """Rewrite candidates.xlsx from Postgres. Returns the number of rows written."""
    with session_scope() as session:
        rows = [_row(candidate, cv) for cv, candidate in session.execute(
            select(CvDocument, Candidate).join(Candidate, Candidate.id == CvDocument.candidate_id)
            .order_by(CvDocument.received_at, CvDocument.id))]

    in_excel = {str(value) for value in load_candidates()[KEY].dropna()} if settings.output_excel.exists() else set()
    not_loaded = in_excel - {row[KEY] for row in rows}
    if not_loaded:
        raise ExcelNotMigrated(f"{len(not_loaded)} CV(s) in {settings.output_excel.name} aren't in Postgres yet; "
                               "run backfill_talent.py")

    write_sheets(settings.output_excel, {SHEET: pd.DataFrame(rows, columns=list(COLUMNS))}, widths=COLUMNS)
    return len(rows)


_lock = threading.Lock()
_run_lock = threading.Lock()
_pending = False


def _refresh_now() -> None:
    global _pending
    with _lock:
        _pending = False
    with _run_lock:
        try:
            regenerate_excel()
        except Exception:  # noqa: BLE001 - a failed rebuild must not break the request that asked for it
            log.exception("Could not regenerate %s", settings.output_excel.name)


def request_refresh(delay: float = REFRESH_DELAY_SECONDS) -> None:
    """Rebuild the Excel soon. Many requests within the delay give one rebuild."""
    global _pending
    with _lock:
        if _pending:
            return
        _pending = True
    timer = threading.Timer(delay, _refresh_now)
    timer.daemon = True
    timer.start()
