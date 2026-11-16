"""
One-time repair of existing data. Makes no LLM calls. Safe to run more than once.

    python backfill_mail_details.py --dry-run     # report what would change, write nothing
    python backfill_mail_details.py               # apply

It does two things:
  1. Fills blank Received From / Mail Subject / Mail Date cells in candidates.xlsx from
     attachments_manifest.json, reading both label formats. Existing values are kept.
     The previous Excel is saved next to it as candidates.before_mail_backfill.xlsx.
  2. Points manifest entries at the renamed file, using the path in processed_files.json.
     Without this, CVs renamed before the fix still aren't found by the duplicate check.

Close candidates.xlsx in Excel before applying.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pandas as pd

from app.core.config import settings
from app.core.state import JsonState, manifest_entry
from app.storage.excel_store import KEY, _write, format_mail_date, load_candidates
from app.storage.s3_store import relative_cv_path


def _is_blank(value) -> bool:
    return pd.isna(value) or not str(value).strip()


def backfill_excel(by_hash: dict, dry_run: bool = False) -> dict:
    df = load_candidates()
    result = {"rows": len(df), "rows_filled": 0, "rows_without_mail_details": 0}
    if df.empty or KEY not in df.columns:
        return result

    columns = {"Received From": "from", "Mail Subject": "subject", "Mail Date": "mail_date"}
    for column in columns:
        df[column] = df[column].astype(object) if column in df.columns else None

    for index, row in df.iterrows():
        blanks = [column for column in columns if _is_blank(row[column])]
        if not blanks:
            continue
        entry = manifest_entry(by_hash, str(row[KEY]))
        filled = False
        for column in blanks:
            value = entry.get(columns[column])
            if column == "Mail Date":
                value = format_mail_date(value)
            if value:
                df.at[index, column] = value
                filled = True
        if filled:
            result["rows_filled"] += 1
        elif not entry:
            result["rows_without_mail_details"] += 1

    if result["rows_filled"] and not dry_run:
        path = settings.output_excel
        shutil.copy2(path, path.with_name(f"{path.stem}.before_mail_backfill{path.suffix}"))
        _write(df, path)
    return result


def repair_manifest_paths(manifest: JsonState, processed: dict, dry_run: bool = False) -> dict:
    by_hash = manifest.get("by_hash", {})
    repaired = 0
    for digest, record in processed.items():
        entry = manifest_entry(by_hash, digest)
        path = record.get("path")
        if not entry or not path or record.get("status") != "ok":
            continue
        relative_path = relative_cv_path(path)
        if not relative_path or not (settings.cv_folder / relative_path).exists():
            continue
        current = str(entry.get("file", "")).replace("\\", "/")
        if current != relative_path:
            entry["file"] = str(Path(relative_path))
            repaired += 1
    if repaired and not dry_run:
        manifest.save()
    return {"manifest_paths_repaired": repaired}


def run(dry_run: bool = False) -> dict:
    manifest = JsonState(settings.state_folder / "attachments_manifest.json")
    processed = JsonState(settings.state_folder / "processed_files.json").data
    return {
        "dry_run": dry_run,
        **backfill_excel(manifest.get("by_hash", {}), dry_run),
        **repair_manifest_paths(manifest, processed, dry_run),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    print(json.dumps(run(parser.parse_args().dry_run), indent=2))
