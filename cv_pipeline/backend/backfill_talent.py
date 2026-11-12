"""
One-time load of the historical CVs into the talent database. Makes no LLM calls.

    python backfill_talent.py --dry-run     # report what would happen, write nothing to Postgres
    python backfill_talent.py               # load, then rebuild candidates.xlsx from Postgres

Reads candidates.xlsx, processed_files.json and attachments_manifest.json, and lists the S3
bucket once to confirm each CV. Rows are loaded oldest first and keyed on File Hash, so it's
safe to run again after a stop: rows already loaded are skipped. Each CV goes through the
matching rules, so the same person's CVs end up under one candidate.

After a real load the old Excel is kept as candidates.before_talent_backfill.xlsx and
candidates.xlsx is rebuilt from Postgres. Close it in Excel first.

A CV with no file in the bucket is still loaded, marked s3_status = missing, so no candidate
data is lost. Those rows are also listed in talent_backfill_missing.csv next to the Excel.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.state import JsonState, manifest_entry
from app.db.models import CvDocument
from app.db.session import session_scope
from app.fetcher.manual_upload import MANUAL_SOURCE
from app.storage.excel_store import KEY, SHEET
from app.storage.s3_store import key_for, list_cv_keys, relative_cv_path
from app.talent import fields as F
from app.talent import ingest
from app.talent.excel_export import ExcelNotMigrated, regenerate_excel

BATCH_SIZE = 500
NOT_IN_BUCKET = "not in bucket"
NO_PATH = "no local path recorded"

TEXT_FIELDS = {
    "full_name": "Full Name", "email": "Email", "phone": "Phone", "passport_number": "Passport Number",
    "job_title": "Current/Last Job Title", "company": "Current/Last Company",
    "date_of_birth": "Date of Birth", "location": "Location", "summary": "Summary", "education": "Education",
}
LIST_FIELDS = {"skills": "Skills", "certifications": "Certifications", "languages": "Languages"}


def _text(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    return str(value).strip() or None


def _date(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(_text(value) or "")
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _read_rows() -> list[dict]:
    """Every value as text: Excel would otherwise turn passport numbers into floats."""
    if not settings.output_excel.exists():
        raise FileNotFoundError(f"{settings.output_excel} not found")
    df = pd.read_excel(settings.output_excel, sheet_name=SHEET, dtype=str)
    if KEY not in df.columns:
        raise ValueError(f"{settings.output_excel.name} has no {KEY} column")
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict("records")


def build_record(row: dict, processed: dict, by_hash: dict, bucket_keys: set[str], loaded_at: datetime) -> dict:
    """Turn one Excel row and its state entries into values for the two tables."""
    file_hash = _text(row[KEY])
    state = processed.get(file_hash) or {}
    mail = manifest_entry(by_hash, file_hash)

    recorded_path = state.get("path") or mail.get("file")
    relative_path = relative_cv_path(recorded_path) if recorded_path else None
    s3_key = key_for(relative_path) if relative_path else None
    if not s3_key:
        missing_reason = NO_PATH
    else:
        missing_reason = None if s3_key in bucket_keys else NOT_IN_BUCKET

    mail_from = mail.get("from") or _text(row.get("Received From"))
    manual = mail.get("source") == "manual" or mail_from == MANUAL_SOURCE
    mail_date = _date(mail.get("mail_date")) or _date(row.get("Mail Date"))
    extracted_at = _date(row.get("Extracted At")) or _date(state.get("at"))
    has_date = bool(mail_date or extracted_at)
    path_parts = relative_path.split("/") if relative_path else []

    fields = {name: _text(row.get(column)) for name, column in TEXT_FIELDS.items()}
    fields |= {name: F.to_column(name, row.get(column)) for name, column in LIST_FIELDS.items()}
    values = {
        **fields,
        "experience_years": F.to_column("experience_years", row.get("Experience (Years)")),
        "age": F.to_column("age", row.get("Age")),
    }
    return {
        "candidate": values,
        "document": {
            "file_hash": file_hash,
            "file_name": _text(row.get("CV File")) or state.get("file") or file_hash,
            "local_rel_path": relative_path,
            "s3_key": s3_key,
            "s3_status": "missing" if missing_reason else "success",
            "source_type": "manual" if manual else "mail",
            "mail_account": path_parts[0] if len(path_parts) > 1 and not manual else None,
            "mail_from": mail_from,
            "mail_from_name": mail.get("from_name") or None,
            "mail_subject": mail.get("subject") or _text(row.get("Mail Subject")),
            "mail_date": mail_date,
            "received_at": mail_date or extracted_at or loaded_at,
            "extracted": {**fields, "experience_years": _text(row.get("Experience (Years)")),
                          "age": _text(row.get("Age"))},
            "extraction_method": _text(row.get("Extraction Method")) or state.get("method"),
            "llm": _text(row.get("LLM")) or state.get("llm"),
            "extracted_at": extracted_at,
        },
        "missing_reason": missing_reason,
        "has_date": has_date,
    }


def place_cv(session, record: dict) -> dict:
    """Match one CV to a candidate and store it (the same rules as new CVs)."""
    document = {key: value for key, value in record["document"].items() if key != "extracted"}
    return asdict(ingest.place_cv(session, document, record["document"]["extracted"]))


def write_missing_report(records: list[dict]) -> Path:
    path = settings.output_excel.parent / "talent_backfill_missing.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as report:
        writer = csv.writer(report)
        writer.writerow([KEY, "CV File", "Full Name", "Reason", "S3 Key"])
        for record in records:
            document = record["document"]
            writer.writerow([document["file_hash"], document["file_name"], record["candidate"]["full_name"],
                             record["missing_reason"], document["s3_key"]])
    return path


def run(dry_run: bool = False) -> dict:
    processed = JsonState(settings.state_folder / "processed_files.json").data
    by_hash = JsonState(settings.state_folder / "attachments_manifest.json").get("by_hash", {})
    rows = _read_rows()
    bucket_keys = list_cv_keys()
    loaded_at = datetime.now(timezone.utc)

    with session_scope() as session:
        already_loaded = set(session.scalars(select(CvDocument.file_hash)))

    unique = {_text(row[KEY]): row for row in rows if _text(row[KEY])}
    records = [build_record(row, processed, by_hash, bucket_keys, loaded_at)
               for file_hash, row in unique.items() if file_hash not in already_loaded]
    records.sort(key=lambda record: record["document"]["received_at"])

    missing = [record for record in records if record["missing_reason"]]
    result = {
        "rows": len(rows),
        "rows_without_hash": sum(1 for row in rows if not _text(row[KEY])),
        "duplicate_rows": sum(1 for row in rows if _text(row[KEY])) - len(unique),
        "already_loaded": len(unique) - len(records),
        "to_load": len(records),
        "loaded": 0,
        "attached_to_existing": 0,
        "flags_raised": 0,
        "s3_found": len(records) - len(missing),
        "s3_not_in_bucket": sum(1 for record in missing if record["missing_reason"] == NOT_IN_BUCKET),
        "s3_no_path": sum(1 for record in missing if record["missing_reason"] == NO_PATH),
        "without_passport": sum(1 for record in records if not record["candidate"]["passport_number"]),
        "without_date": sum(1 for record in records if not record["has_date"]),
        "failed": {},
        "missing_report": str(write_missing_report(missing)),
    }
    if dry_run:
        return result

    for start in range(0, len(records), BATCH_SIZE):
        with session_scope() as session:
            for record in records[start:start + BATCH_SIZE]:
                try:
                    with session.begin_nested():
                        placement = place_cv(session, record)
                    result["loaded"] += 1
                    result["attached_to_existing"] += int(placement["attached"])
                    result["flags_raised"] += placement["flags_raised"]
                except SQLAlchemyError as e:
                    result["failed"][record["document"]["file_hash"]] = str(e)[:300]
    result["excel"] = rebuild_excel()
    return result


def rebuild_excel() -> str:
    """Keep the old Excel, then rebuild it from Postgres. Never runs if rows would be lost."""
    backup = settings.output_excel.with_name("candidates.before_talent_backfill.xlsx")
    if not backup.exists():
        shutil.copy2(settings.output_excel, backup)
    try:
        return f"rebuilt from Postgres with {regenerate_excel()} rows; previous copy kept as {backup.name}"
    except ExcelNotMigrated as e:
        return f"not rebuilt: {e}. Fix the failed rows and run again"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report only")
    args = parser.parse_args()
    print(json.dumps(run(dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
