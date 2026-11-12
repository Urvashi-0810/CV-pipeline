"""The historical load into Postgres: S3 keys, mail details, missing files, re-runs. Uses no LLM."""
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import select

import backfill_talent as backfill
import mock_llm
from app.core.config import settings
from app.db.models import Candidate, CvDocument
from app.db.session import session_scope
from app.storage import s3_store
from app.storage.excel_store import COLUMNS, KEY, SHEET, upsert_rows
from app.storage.s3_store import S3NotConfigured

BUCKET: set = set()
CLIENT_CVS = r"C:\Users\Client\pipeline\data\cvs"


@pytest.fixture(autouse=True)
def clean(clean_data, db, monkeypatch):
    BUCKET.clear()
    monkeypatch.setattr(backfill, "list_cv_keys", lambda: set(BUCKET))
    yield


def write_state(processed=None, manifest=None):
    (settings.state_folder / "processed_files.json").write_text(json.dumps(processed or {}), encoding="utf-8")
    (settings.state_folder / "attachments_manifest.json").write_text(
        json.dumps({"by_hash": manifest or {}}), encoding="utf-8")


def row(file_hash, **values):
    return {"Full Name": "Rahul Sharma", "CV File": f"{file_hash}.pdf", KEY: file_hash, **values}


def client_path(relative):
    return CLIENT_CVS + "\\" + relative.replace("/", "\\")


def add_cv(file_hash, relative="acct/May-2026/cv.pdf", in_bucket=True, **values):
    """A row, its processed entry, and optionally its S3 file."""
    upsert_rows([row(file_hash, **values)])
    path = settings.state_folder / "processed_files.json"
    processed = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    processed[file_hash] = {"status": "ok", "path": client_path(relative)}
    path.write_text(json.dumps(processed), encoding="utf-8")
    if in_bucket:
        BUCKET.add(f"cvs/{relative}")


def documents():
    with session_scope() as session:
        return session.scalars(select(CvDocument).order_by(CvDocument.id)).all()


def candidates():
    with session_scope() as session:
        return session.scalars(select(Candidate).order_by(Candidate.id)).all()


def report_rows():
    path = settings.output_excel.parent / "talent_backfill_missing.csv"
    with path.open(encoding="utf-8-sig", newline="") as report:
        return list(csv.DictReader(report))


def test_rebuilds_the_s3_key_from_the_clients_full_path():
    add_cv("aaa", "acct@mail.com/May-2026/Welder_P1_Rahul.pdf")
    write_state(json.loads((settings.state_folder / "processed_files.json").read_text(encoding="utf-8")))

    result = backfill.run()

    cv = documents()[0]
    assert result["loaded"] == 1 and result["failed"] == {}
    assert cv.s3_key == "cvs/acct@mail.com/May-2026/Welder_P1_Rahul.pdf"
    assert cv.local_rel_path == "acct@mail.com/May-2026/Welder_P1_Rahul.pdf"
    assert cv.s3_status == "success" and cv.mail_account == "acct@mail.com"


def test_a_cv_not_in_the_bucket_is_loaded_marked_and_reported():
    add_cv("aaa", in_bucket=False, **{"Full Name": "No File", "Email": "x@example.com"})

    result = backfill.run()

    cv = documents()[0]
    assert result["loaded"] == 1 and result["s3_not_in_bucket"] == 1 and result["s3_found"] == 0
    assert cv.s3_status == "missing" and cv.s3_key == "cvs/acct/May-2026/cv.pdf"
    assert candidates()[0].email == "x@example.com"
    assert report_rows() == [{KEY: "aaa", "CV File": "aaa.pdf", "Full Name": "No File",
                              "Reason": "not in bucket", "S3 Key": "cvs/acct/May-2026/cv.pdf"}]


def test_a_row_with_no_recorded_path_is_loaded_marked_and_reported():
    upsert_rows([row("aaa")])
    write_state()

    result = backfill.run()

    cv = documents()[0]
    assert result["s3_no_path"] == 1
    assert cv.s3_status == "missing" and cv.s3_key is None and cv.local_rel_path is None
    assert report_rows()[0]["Reason"] == "no local path recorded"


def test_a_path_outside_the_cv_folder_counts_as_no_path():
    upsert_rows([row("aaa")])
    write_state({"aaa": {"path": r"C:\Temp\cv.pdf"}})
    assert backfill.run()["s3_no_path"] == 1


def test_uses_the_register_path_when_processed_has_none():
    upsert_rows([row("aaa")])
    write_state({"aaa": {"status": "ok", "file": "cv.pdf"}}, {"PDF:aaa": {"file": r"acct\May-2026\cv.pdf"}})
    BUCKET.add("cvs/acct/May-2026/cv.pdf")

    backfill.run()

    assert documents()[0].s3_status == "success"


@pytest.mark.parametrize("label", ["PDF:aaa", "aaa"])
def test_reads_mail_details_under_both_labels(label):
    upsert_rows([row("aaa")])
    write_state(manifest={label: {"from": "agent@example.com", "from_name": "Agent", "subject": "Welder CV",
                                  "mail_date": "2026-05-02T08:30:00+00:00"}})

    backfill.run()

    cv = documents()[0]
    assert (cv.mail_from, cv.mail_from_name, cv.mail_subject) == ("agent@example.com", "Agent", "Welder CV")
    assert cv.received_at.isoformat().startswith("2026-05-02") and cv.source_type == "mail"


def test_falls_back_to_the_excel_mail_details():
    upsert_rows([row("aaa", **{"Received From": "excel@example.com", "Mail Subject": "From Excel",
                               "Mail Date": "2026-04-01 10:15"})])
    write_state()

    backfill.run()

    cv = documents()[0]
    assert cv.mail_from == "excel@example.com" and cv.mail_subject == "From Excel"
    assert cv.mail_date == datetime(2026, 4, 1, 10, 15, tzinfo=timezone.utc)


def test_manual_uploads_are_recognised():
    upsert_rows([row("aaa")])
    write_state({"aaa": {"path": client_path("manual-upload/May-2026/cv.pdf")}},
                {"PDF:aaa": {"from": "Manual upload", "source": "manual"}})

    backfill.run()

    cv = documents()[0]
    assert cv.source_type == "manual" and cv.mail_account is None


def test_maps_excel_columns_to_candidate_fields():
    upsert_rows([row("aaa", **{
        "Email": "r@example.com", "Phone": "+91 98765 43210", "Passport Number": "P1234567",
        "Skills": "Welding, MIG, ", "Certifications": "AWS D1.1", "Languages": "Hindi, English",
        "Experience (Years)": 7.5, "Age": 34, "Current/Last Job Title": "Welder",
        "Current/Last Company": "ACME", "Location": "Pune", "Education": "ITI", "Summary": "Skilled.",
        "Date of Birth": "1992-01-01", "Extraction Method": "llm-file", "LLM": "gemini",
        "Extracted At": "2026-05-03 09:00",
    })])
    write_state()

    backfill.run()

    candidate, cv = candidates()[0], documents()[0]
    assert candidate.skills == ["Welding", "MIG"] and candidate.languages == ["Hindi", "English"]
    assert candidate.experience_years == Decimal("7.5") and candidate.age == 34
    assert candidate.job_title == "Welder" and candidate.company == "ACME"
    assert candidate.phone == "+91 98765 43210" and candidate.date_of_birth == "1992-01-01"
    assert "P1234567" in candidate.search_text and "Welding" in candidate.search_text
    assert candidate.latest_cv_id == cv.id and candidate.received_at == cv.received_at
    assert cv.extraction_method == "llm-file" and cv.llm == "gemini"
    assert cv.extracted["passport_number"] == "P1234567" and cv.extracted["skills"] == ["Welding", "MIG"]


def test_numeric_looking_passports_keep_their_exact_text():
    df = pd.DataFrame([row("aaa", **{"Passport Number": 1234567}), row("bbb")]).reindex(columns=list(COLUMNS))
    settings.output_excel.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(settings.output_excel, sheet_name=SHEET, index=False)
    write_state()

    backfill.run()

    assert candidates()[0].passport_number == "1234567"


def test_unusable_numbers_become_blank_but_stay_in_the_extracted_values():
    upsert_rows([row("aaa", **{"Experience (Years)": "about 5", "Age": "99999"})])
    write_state()

    result = backfill.run()

    candidate = candidates()[0]
    assert result["failed"] == {}
    assert candidate.experience_years is None and candidate.age is None
    assert documents()[0].extracted["experience_years"] == "about 5"


def test_a_file_without_a_passport_column_still_loads():
    df = pd.DataFrame([row("aaa")])
    settings.output_excel.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(settings.output_excel, sheet_name=SHEET, index=False)
    write_state()

    result = backfill.run()

    assert result["loaded"] == 1 and result["without_passport"] == 1


def test_loads_oldest_first():
    upsert_rows([row("new", **{"Mail Date": "2026-06-01 10:00"}), row("old", **{"Mail Date": "2024-01-01 10:00"})])
    write_state()

    backfill.run()

    assert [cv.file_hash for cv in documents()] == ["old", "new"]


def test_a_row_with_no_date_uses_the_load_time_and_is_counted():
    upsert_rows([row("aaa")])
    write_state()

    result = backfill.run()

    assert result["without_date"] == 1 and documents()[0].received_at is not None


def test_second_run_loads_nothing_new():
    add_cv("aaa")
    first = backfill.run()
    second = backfill.run()

    assert first["loaded"] == 1 and second["loaded"] == 0 and second["already_loaded"] == 1
    assert len(documents()) == 1 and len(candidates()) == 1


def test_a_run_picks_up_where_it_stopped():
    add_cv("aaa", "acct/May-2026/a.pdf")
    backfill.run()
    add_cv("bbb", "acct/May-2026/b.pdf")

    result = backfill.run()

    assert result["loaded"] == 1 and len(documents()) == 2


def test_dry_run_writes_nothing_to_postgres_but_still_reports():
    add_cv("aaa", in_bucket=False)

    result = backfill.run(dry_run=True)

    assert result["to_load"] == 1 and result["loaded"] == 0 and result["s3_not_in_bucket"] == 1
    assert not documents() and not candidates()
    assert len(report_rows()) == 1


def test_rows_without_a_hash_are_counted_not_loaded():
    df = pd.DataFrame([row("aaa"), {"Full Name": "No Hash"}]).reindex(columns=list(COLUMNS))
    settings.output_excel.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(settings.output_excel, sheet_name=SHEET, index=False)
    write_state()

    result = backfill.run()

    assert result["rows_without_hash"] == 1 and len(documents()) == 1


def test_one_bad_row_does_not_stop_the_load(monkeypatch):
    from sqlalchemy.exc import IntegrityError
    upsert_rows([row("bad", **{"Mail Date": "2024-01-01 10:00"}), row("good", **{"Mail Date": "2025-01-01 10:00"})])
    write_state()
    place = backfill.place_cv

    def failing(session, record):
        if record["document"]["file_hash"] == "bad":
            session.add(Candidate())
            session.flush()
            raise IntegrityError("insert", {}, Exception("refused"))
        return place(session, record)
    monkeypatch.setattr(backfill, "place_cv", failing)

    result = backfill.run()

    assert result["loaded"] == 1 and list(result["failed"]) == ["bad"]
    assert [cv.file_hash for cv in documents()] == ["good"] and len(candidates()) == 1


def test_stops_rather_than_marking_everything_missing_if_the_bucket_cant_be_read(monkeypatch):
    def unavailable():
        raise S3NotConfigured("AWS_S3_BUCKET is not configured")
    monkeypatch.setattr(backfill, "list_cv_keys", unavailable)
    add_cv("aaa")

    with pytest.raises(S3NotConfigured):
        backfill.run()
    assert not documents()


def test_stops_clearly_when_there_is_no_excel():
    with pytest.raises(FileNotFoundError):
        backfill.run()


def test_makes_no_llm_calls():
    mock_llm.SENT.clear()
    add_cv("aaa")
    backfill.run()
    assert not mock_llm.SENT


def test_the_same_persons_cvs_are_grouped_by_passport():
    upsert_rows([row("old", **{"Passport Number": "P1234567", "Mail Date": "2024-01-01 10:00", "Phone": "111"}),
                 row("new", **{"Passport Number": "p-123 4567", "Mail Date": "2025-01-01 10:00",
                               "Phone": "+91 98765 43210"}),
                 row("other", **{"Full Name": "Someone Else", "Passport Number": "Z7654321"})])
    write_state()

    result = backfill.run()

    assert result["loaded"] == 3 and result["attached_to_existing"] == 1
    people = candidates()
    assert len(people) == 2
    rahul = next(c for c in people if c.full_name == "Rahul Sharma")
    assert rahul.phone == "+91 98765 43210"                     # the newer CV's values
    assert {cv.candidate_id for cv in documents() if cv.file_hash in ("old", "new")} == {rahul.id}


def test_a_real_load_rebuilds_the_excel_and_keeps_the_old_one():
    upsert_rows([row("aaa", **{"Passport Number": "P1234567", "Mail Date": "2024-01-01 10:00"}),
                 row("bbb", **{"Passport Number": "P1234567", "Mail Date": "2025-01-01 10:00",
                               "Location": "Pune"})])
    before = settings.output_excel.read_bytes()
    write_state()

    result = backfill.run()

    assert result["excel"].startswith("rebuilt from Postgres with 2 rows")
    backup = settings.output_excel.with_name("candidates.before_talent_backfill.xlsx")
    assert backup.read_bytes() == before
    rebuilt = pd.read_excel(settings.output_excel, sheet_name=SHEET, dtype=str)
    assert list(rebuilt[KEY]) == ["aaa", "bbb"] and set(rebuilt["Location"]) == {"Pune"}


def test_the_excel_is_not_rebuilt_while_rows_failed_to_load(monkeypatch):
    upsert_rows([row("aaa")])
    write_state()
    before = settings.output_excel.read_bytes()
    monkeypatch.setattr(backfill, "place_cv", lambda session, record: (_ for _ in ()).throw(
        __import__("sqlalchemy").exc.SQLAlchemyError("refused")))

    result = backfill.run()

    assert result["excel"].startswith("not rebuilt") and settings.output_excel.read_bytes() == before


def test_list_cv_keys_reads_every_page(monkeypatch):
    calls = []

    class Paginator:
        def paginate(self, **kwargs):
            calls.append(kwargs)
            return [{"Contents": [{"Key": "cvs/a.pdf"}]}, {"Contents": [{"Key": "cvs/b.pdf"}]}, {}]

    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    monkeypatch.setattr(s3_store, "_client", lambda: SimpleNamespace(get_paginator=lambda name: Paginator()))

    assert s3_store.list_cv_keys() == {"cvs/a.pdf", "cvs/b.pdf"}
    assert calls == [{"Bucket": "test-bucket", "Prefix": "cvs/"}]


def test_list_cv_keys_needs_a_bucket():
    with pytest.raises(S3NotConfigured):
        s3_store.list_cv_keys()
