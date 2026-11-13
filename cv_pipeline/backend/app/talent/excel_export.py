"""
candidates.xlsx regenerated from Postgres: one row per CV, every row showing its candidate's
current values (manual edits included). CV File, mail details and dates stay per CV.

The rebuild refuses to run while the Excel still holds CVs that aren't in Postgres (before the
historical backfill), so it can never drop rows. Callers then fall back to adding rows.

request_refresh() coalesces many changes into one rebuild a few seconds later, for edits.
"""
from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Optional

import pandas as pd
from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.models import Candidate, CvDocument
from app.db.session import session_scope
from app.storage.excel_store import ALL_FIELDS_SHEET, COLUMNS, KEY, SHEET, load_candidates, write_sheets
from app.talent.experience import (
    NOT_APPLICABLE,
    NOT_FOUND,
    OTHER_COLUMN,
    SECTOR_COLUMNS,
    age_text,
    dmy,
    experience_columns,
)
from app.talent.fields import current_age

log = get_logger("excel_export")

S3_LABELS = {"success": "Success", "failed": "Failed", "pending": "Pending", "missing": "Missing"}
REFRESH_DELAY_SECONDS = 5


class ExcelNotMigrated(RuntimeError):
    """The Excel holds CVs that aren't in Postgres yet: run backfill_talent.py first."""


def _when(value: Optional[datetime]) -> Optional[str]:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else None


# The client's layout, in their order and spelling ("Date" renamed to say which date it is),
# then the summary, then the columns that trace each row back to its CV.
CLIENT_COLUMNS: dict[str, int] = {
    "SR NO": 7, "CV Extraction Date": 13, "Candidate Name": 26, "Trade": 24, "Passport No": 14, "PP Type": 9,
    "PassPort Expiry": 13, "D-O-B": 12, "Age": 14, "Gender": 9, "Nationality": 13, "Qualification": 34,
    "Certification": 30, "Indian Experience": 15, "Gulf Experience": 15, "Total Experience": 15,
    "Contact Number": 18, "Facility Management Experience": 16, "Oil & Gas": 14, "Construction": 14,
    "Marine/Shipyard": 14, "Healthcare": 14, "Other Relevant Sector Experience": 34, "Summary": 60,
    "CV File": 40, "Received From": 30, "Mail Subject": 30, "Mail Date": 17, "Uploaded to S3": 14, KEY: 20,
}
CLIENT_SHEET = SHEET                 # "Candidates": the sheet the client reads
# ALL_FIELDS_SHEET ("All fields"): every stored field, which the shortlist and job-fit steps read


def _found(value) -> str:
    return value if value not in (None, "", []) else NOT_FOUND


def _qualification(education: Optional[str]) -> Optional[str]:
    """The highest qualification: education is stored highest first, '; ' between entries."""
    return (education or "").split("; ")[0].strip() or None


def _client_row(number: int, candidate: Candidate, cv: CvDocument, today) -> dict:
    experience = experience_columns(candidate.work_history, today)
    return {
        "SR NO": number,
        "CV Extraction Date": cv.extracted_at.astimezone().strftime("%d-%m-%Y") if cv.extracted_at else NOT_FOUND,
        "Candidate Name": _found(candidate.full_name),
        "Trade": _found(candidate.job_title),
        "Passport No": _found(candidate.passport_number),
        "PP Type": _found(candidate.passport_type),
        "PassPort Expiry": _found(dmy(candidate.passport_expiry)),
        "D-O-B": _found(dmy(candidate.date_of_birth)),
        "Age": age_text(candidate.birth_date, candidate.age, today),
        "Gender": _found(candidate.gender),
        "Nationality": _found(candidate.nationality),
        "Qualification": _found(_qualification(candidate.education)),
        "Certification": ", ".join(candidate.certifications) if candidate.certifications else NOT_APPLICABLE,
        **{column: experience[column] for column in ("Indian Experience", "Gulf Experience", "Total Experience")},
        "Contact Number": _found(candidate.phone),
        **{column: experience[column] for column in (*SECTOR_COLUMNS.values(), OTHER_COLUMN)},
        "Summary": _found(candidate.summary),
        "CV File": cv.file_name,
        "Received From": cv.mail_from,
        "Mail Subject": cv.mail_subject,
        "Mail Date": cv.mail_date.astimezone().strftime("%d-%m-%Y %H:%M") if cv.mail_date else None,
        "Uploaded to S3": S3_LABELS.get(cv.s3_status, cv.s3_status),
        KEY: cv.file_hash,
    }


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
    today = date.today()
    with session_scope() as session:
        pairs = session.execute(
            select(CvDocument, Candidate).join(Candidate, Candidate.id == CvDocument.candidate_id)
            .order_by(CvDocument.received_at, CvDocument.id)).all()
        client_rows = [_client_row(n, candidate, cv, today) for n, (cv, candidate) in enumerate(pairs, start=1)]
        rows = [_row(candidate, cv) for cv, candidate in pairs]

    in_excel = {str(value) for value in load_candidates()[KEY].dropna()} if settings.output_excel.exists() else set()
    not_loaded = in_excel - {row[KEY] for row in rows}
    if not_loaded:
        raise ExcelNotMigrated(f"{len(not_loaded)} CV(s) in {settings.output_excel.name} aren't in Postgres yet; "
                               "run backfill_talent.py")

    write_sheets(settings.output_excel, {
        CLIENT_SHEET: pd.DataFrame(client_rows, columns=list(CLIENT_COLUMNS)),
        ALL_FIELDS_SHEET: pd.DataFrame(rows, columns=list(COLUMNS)),
    }, widths={**COLUMNS, **CLIENT_COLUMNS})
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
