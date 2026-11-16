"""The one-time repair of blank mail details and stale register paths. Uses no LLM."""
import json

import pytest

import backfill_mail_details as backfill
import mock_llm
from app.core.config import settings
from app.storage.excel_store import load_candidates, upsert_rows

MAIL = {"from": "agent@example.com", "subject": "Welder CV", "mail_date": "2026-09-10T08:30:00+00:00"}


@pytest.fixture(autouse=True)
def clean(clean_data):
    yield


def write_manifest(by_hash):
    path = settings.state_folder / "attachments_manifest.json"
    path.write_text(json.dumps({"by_hash": by_hash}), encoding="utf-8")


def read_manifest():
    path = settings.state_folder / "attachments_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["by_hash"]


def write_processed(data):
    path = settings.state_folder / "processed_files.json"
    path.write_text(json.dumps(data), encoding="utf-8")


def row(file_hash, **values):
    return {"Full Name": "Name", "CV File": f"{file_hash}.pdf", "File Hash": file_hash, **values}


def test_fills_blanks_from_both_label_formats():
    upsert_rows([row("aaa"), row("bbb")])
    write_manifest({"PDF:aaa": MAIL, "bbb": {**MAIL, "subject": "Legacy"}})

    result = backfill.run()

    assert result["rows_filled"] == 2
    df = load_candidates().set_index("File Hash")
    assert df.loc["aaa", "Received From"] == "agent@example.com"
    assert df.loc["aaa", "Mail Date"] == "2026-09-10 08:30"
    assert df.loc["bbb", "Mail Subject"] == "Legacy"


def test_keeps_existing_values():
    upsert_rows([row("aaa", **{"Received From": "kept@example.com"})])
    write_manifest({"PDF:aaa": MAIL})

    backfill.run()

    saved = load_candidates().iloc[0]
    assert saved["Received From"] == "kept@example.com" and saved["Mail Subject"] == "Welder CV"


def test_reports_rows_with_no_mail_details():
    upsert_rows([row("aaa")])
    write_manifest({})
    result = backfill.run()
    assert result["rows_filled"] == 0 and result["rows_without_mail_details"] == 1


def test_dry_run_writes_nothing():
    upsert_rows([row("aaa")])
    write_manifest({"PDF:aaa": MAIL})
    before = settings.output_excel.read_bytes()

    result = backfill.run(dry_run=True)

    assert result["rows_filled"] == 1
    assert settings.output_excel.read_bytes() == before
    assert not list(settings.output_excel.parent.glob("*before_mail_backfill*"))


def test_keeps_a_copy_of_the_previous_excel():
    upsert_rows([row("aaa")])
    before = settings.output_excel.read_bytes()
    write_manifest({"PDF:aaa": MAIL})

    backfill.run()

    copy = settings.output_excel.with_name("candidates.before_mail_backfill.xlsx")
    assert copy.read_bytes() == before


def test_second_run_changes_nothing():
    upsert_rows([row("aaa")])
    write_manifest({"PDF:aaa": MAIL})
    backfill.run()
    assert backfill.run()["rows_filled"] == 0


def test_makes_no_llm_calls():
    mock_llm.SENT.clear()
    upsert_rows([row("aaa")])
    write_manifest({"PDF:aaa": MAIL})
    backfill.run()
    assert not mock_llm.SENT


def test_repairs_stale_register_paths():
    renamed = settings.cv_folder / "acct" / "May-2026" / "Welder_P1_Name.pdf"
    renamed.parent.mkdir(parents=True)
    renamed.write_bytes(b"x")
    write_manifest({
        "PDF:aaa": {"file": r"acct\May-2026\Subject line.pdf"},
        "PDF:gone": {"file": "old.pdf"},
        "PDF:failed": {"file": "failed.pdf"},
    })
    write_processed({
        "aaa": {"status": "ok", "path": str(renamed)},
        "gone": {"status": "ok", "path": str(settings.cv_folder / "missing.pdf")},
        "failed": {"status": "error", "file": "failed.pdf"},
    })

    result = backfill.run()

    assert result["manifest_paths_repaired"] == 1
    saved = read_manifest()
    assert (settings.cv_folder / saved["PDF:aaa"]["file"]) == renamed
    assert saved["PDF:gone"]["file"] == "old.pdf" and saved["PDF:failed"]["file"] == "failed.pdf"
