"""Placing CVs in Postgres: the matching rules, new CVs vs manual edits, the Excel, and the pipeline wiring."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mock_llm
from app.core.config import settings
from app.db.models import Candidate, CvDocument, EditHistory, ReviewFlag
from app.db.session import session_scope
from app.extractor import cv_extractor
from app.storage.excel_store import KEY, SHEET, load_candidates, upsert_rows
from app.talent import ingest
from app.talent.excel_export import ExcelNotMigrated, regenerate_excel
from conftest import pdf_bytes

mock_llm.start()
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
_count = 0


@pytest.fixture(autouse=True)
def clean(clean_data, db):
    yield


def cv(name="Rahul Sharma", passport=None, email=None, phone=None, when=T0, file_hash=None, **values):
    """Place one CV and return its Placement."""
    global _count
    _count += 1
    extracted = {"full_name": name, "passport_number": passport, "email": email, "phone": phone, **values}
    document = {"file_hash": file_hash or f"h{_count}", "file_name": f"cv{_count}.pdf", "received_at": when}
    with session_scope() as session:
        return ingest.place_cv(session, document, extracted)


def candidate(candidate_id):
    with session_scope() as session:
        return session.get(Candidate, candidate_id)


def flags(**where):
    with session_scope() as session:
        query = select(ReviewFlag).order_by(ReviewFlag.id)
        for name, value in where.items():
            query = query.where(getattr(ReviewFlag, name) == value)
        return session.scalars(query).all()


def edit(candidate_id, **values):
    """A manual edit, as the edit API (next step) will record it."""
    with session_scope() as session:
        person = session.get(Candidate, candidate_id)
        manual = dict(person.manual_fields)
        for name, value in values.items():
            setattr(person, name, value)
            manual[name] = datetime.now(timezone.utc).isoformat()
        person.manual_fields = manual


# ---------- rule 1: passport ----------

def test_a_new_cv_creates_a_candidate():
    placed = cv(passport="P1234567", skills=["Welding"], experience_years=7.5)
    person = candidate(placed.candidate_id)
    assert not placed.attached and person.full_name == "Rahul Sharma" and person.skills == ["Welding"]
    assert person.passport_norm == "P1234567" and person.latest_cv_id == placed.cv_document_id
    assert "Welding" in person.search_text and not flags()


def test_same_passport_attaches_automatically():
    first = cv(passport="P1234567")
    second = cv(name="SHARMA Rahul", passport="p-123 4567", when=T0 + timedelta(days=1))
    assert second.attached and second.candidate_id == first.candidate_id
    assert candidate(first.candidate_id).latest_cv_id == second.cv_document_id and not flags()


def test_same_passport_but_a_different_name_attaches_and_flags():
    first = cv(passport="P1234567")
    second = cv(name="Amit Verma", passport="P1234567", when=T0 + timedelta(days=1))
    assert second.attached and second.candidate_id == first.candidate_id
    [flag] = flags()
    assert flag.type == "passport_name_mismatch" and "Amit Verma" in flag.message
    assert candidate(first.candidate_id).needs_review


def test_a_merged_candidate_with_two_passports_matches_either():
    first = cv(passport="P1234567")
    with session_scope() as session:            # a second passport on one of their CVs
        session.add(CvDocument(candidate_id=first.candidate_id, file_hash="x", file_name="x.pdf",
                               received_at=T0, passport_norm="Z7654321"))
    assert cv(passport="Z7654321").candidate_id == first.candidate_id


def test_placeholder_passports_never_match():
    first = cv(passport="N/A")
    second = cv(name="Someone Else", passport="N/A")
    assert second.candidate_id != first.candidate_id and not flags()


# ---------- rules 2-4 ----------

def test_different_passports_same_email_flags_both():
    first = cv(passport="P1234567", email="r@example.com")
    second = cv(name="Rahul S", passport="Z7654321", email="R@Example.com")
    assert second.candidate_id != first.candidate_id
    [flag] = flags()
    assert flag.type == "possible_duplicate" and "different passport numbers, same email" in flag.reason
    assert {flag.candidate_id, flag.other_candidate_id} == {first.candidate_id, second.candidate_id}
    assert candidate(first.candidate_id).needs_review and candidate(second.candidate_id).needs_review


def test_one_passport_missing_same_phone_and_similar_name_flags():
    first = cv(passport="P1234567", phone="+91 98765 43210")
    cv(name="Sharma Rahul Kumar", phone="098765 43210")
    [flag] = flags()
    assert "same phone and a similar name" in flag.reason and flag.other_candidate_id == first.candidate_id


def test_no_passport_on_either_side_with_a_similar_name_flags():
    cv(email="r@example.com")
    cv(name="rahul  sharma", email="r@example.com")
    assert len(flags()) == 1


def test_shared_contact_with_a_different_name_and_no_passport_is_not_flagged():
    cv(email="r@example.com")
    cv(name="Amit Verma", email="r@example.com")
    assert not flags()


def test_name_alone_never_matches():
    first = cv()
    second = cv()
    assert second.candidate_id != first.candidate_id and not flags()


def test_an_agency_contact_shared_by_three_people_never_flags():
    cv(name="Rahul Sharma", passport="P1111111", email="jobs@agency.com")
    cv(name="Amit Verma", passport="P2222222", email="jobs@agency.com")      # 2 names: still flags
    assert len(flags()) == 1
    cv(name="Suresh Patil", passport="P3333333", email="jobs@agency.com")   # 3rd name: agency contact
    assert len(flags()) == 1


def test_a_pair_marked_different_people_is_never_flagged_again():
    first = cv(passport="P1234567", email="r@example.com")
    second = cv(passport="Z7654321", email="r@example.com")
    with session_scope() as session:
        flag = session.scalar(select(ReviewFlag))
        flag.status, flag.resolution = "resolved", "different_people"
    later = cv(name="Rahul Sharma", passport="Q5555555", email="r@example.com", when=T0 + timedelta(days=1))
    # A new person with the same email: flagged against both, except... the pair already decided stays quiet.
    assert len(flags(status="open")) == 2
    assert not any({f.candidate_id, f.other_candidate_id} == {first.candidate_id, second.candidate_id}
                   for f in flags(status="open"))
    assert all(later.candidate_id in (f.candidate_id, f.other_candidate_id) for f in flags(status="open"))


def test_a_cv_joining_a_candidate_does_not_reopen_a_decided_pair():
    first = cv(passport="P1234567", email="r@example.com")
    second = cv(passport="Z7654321", email="r@example.com")
    with session_scope() as session:
        flag = session.scalar(select(ReviewFlag))
        flag.status, flag.resolution = "resolved", "different_people"
    attached = cv(passport="Z7654321", email="r@example.com", when=T0 + timedelta(days=1))
    assert attached.candidate_id == second.candidate_id and not flags(status="open")


def test_flagging_refuses_a_pair_already_marked_different_people():
    first = cv(passport="P1234567", email="r@example.com")
    second = cv(passport="Z7654321", email="r@example.com")
    with session_scope() as session:
        flag = session.scalar(select(ReviewFlag))
        flag.status, flag.resolution = "resolved", "different_people"
    with session_scope() as session:
        assert ingest._flag_duplicate(session, second.candidate_id, first.candidate_id, "again") is False
    assert not flags(status="open")


def test_an_open_flag_is_not_repeated():
    cv(passport="P1234567", email="r@example.com")
    cv(passport="Z7654321", email="r@example.com")
    cv(passport="Z7654321", email="r@example.com", when=T0 + timedelta(days=1))   # same pair again
    assert len(flags()) == 1


# ---------- newer CVs vs manual edits ----------

def test_untouched_fields_take_the_newer_cvs_values():
    first = cv(passport="P1234567", location="Pune", job_title="Welder")
    cv(passport="P1234567", location="Mumbai", job_title="Senior Welder", when=T0 + timedelta(days=1))
    person = candidate(first.candidate_id)
    assert (person.location, person.job_title) == ("Mumbai", "Senior Welder") and not flags()


def test_a_blank_in_the_new_cv_keeps_the_current_value():
    first = cv(passport="P1234567", phone="+91 98765 43210")
    cv(passport="P1234567", phone=None, when=T0 + timedelta(days=1))
    assert candidate(first.candidate_id).phone == "+91 98765 43210"


def test_an_edit_stays_when_the_new_cv_did_not_change_that_field():
    first = cv(passport="P1234567", location="Pune", skills=["Welding", "MIG"])
    edit(first.candidate_id, location="Pune City", skills=["Welding", "MIG", "TIG"])
    cv(passport="P1234567", location=" pune ", skills=["mig", "Welding"], when=T0 + timedelta(days=1))
    person = candidate(first.candidate_id)
    assert person.location == "Pune City" and person.skills == ["Welding", "MIG", "TIG"]
    assert not flags() and set(person.manual_fields) == {"location", "skills"}


def test_a_changed_field_replaces_the_edit_records_it_and_flags():
    first = cv(passport="P1234567", location="Pune", job_title="Welder")
    edit(first.candidate_id, location="Pune City", job_title="Lead Welder")
    second = cv(passport="P1234567", location="Dubai", job_title="Welder", when=T0 + timedelta(days=1))

    person = candidate(first.candidate_id)
    assert person.location == "Dubai" and person.job_title == "Lead Welder"
    assert set(person.manual_fields) == {"job_title"} and person.needs_review
    [flag] = flags()
    assert flag.type == "edit_replaced" and flag.message == "New CV received: 1 manually edited field was replaced."
    assert flag.fields == [{"field": "location", "manual_value": "Pune City", "new_value": "Dubai",
                            "cv_document_id": second.cv_document_id, "kept": False}]
    with session_scope() as session:
        history = session.scalar(select(EditHistory))
        assert (history.kind, history.old_value, history.new_value) == ("replaced_by_cv", "Pune City", "Dubai")


def test_summary_never_replaces_an_edit_but_updates_when_untouched():
    first = cv(passport="P1234567", summary="Old summary")
    cv(passport="P1234567", summary="Rewritten summary", when=T0 + timedelta(days=1))
    assert candidate(first.candidate_id).summary == "Rewritten summary"
    edit(first.candidate_id, summary="My summary")
    cv(passport="P1234567", summary="Another rewrite", when=T0 + timedelta(days=2))
    assert candidate(first.candidate_id).summary == "My summary" and not flags()


def test_an_older_cv_arriving_late_only_fills_blanks():
    first = cv(passport="P1234567", location="Mumbai", when=T0)
    late = cv(passport="P1234567", location="Pune", company="ACME", when=T0 - timedelta(days=300))
    person = candidate(first.candidate_id)
    assert late.attached and person.location == "Mumbai" and person.company == "ACME"
    assert person.latest_cv_id == first.cv_document_id and person.received_at == T0


def test_reextracting_the_latest_cv_applies_the_rules():
    first = cv(passport="P1234567", location="Pune", file_hash="same")
    again = cv(passport="P1234567", location="Mumbai", file_hash="same")
    assert again.reextracted and again.candidate_id == first.candidate_id
    assert candidate(first.candidate_id).location == "Mumbai"
    with session_scope() as session:
        assert len(session.scalars(select(CvDocument)).all()) == 1


def test_reextracting_an_older_cv_does_not_change_the_candidate():
    old = cv(passport="P1234567", location="Pune", file_hash="old", when=T0)
    cv(passport="P1234567", location="Mumbai", when=T0 + timedelta(days=1))
    cv(passport="P1234567", location="Delhi", file_hash="old", when=T0)
    assert candidate(old.candidate_id).location == "Mumbai"


# ---------- Excel from Postgres ----------

def test_excel_has_one_row_per_cv_with_the_persons_current_values():
    first = cv(passport="P1234567", location="Pune", skills=["Welding"], experience_years=7.5)
    cv(passport="P1234567", location="Dubai", when=T0 + timedelta(days=1))
    edit(first.candidate_id, job_title="Lead Welder")

    assert regenerate_excel() == 2
    df = pd.read_excel(settings.output_excel, sheet_name=SHEET, dtype=str)
    assert list(df["Location"]) == ["Dubai", "Dubai"] and list(df["Current/Last Job Title"]) == ["Lead Welder"] * 2
    assert list(df["CV File"]) != [df["CV File"][0]] * 2 and df["Skills"][0] == "Welding"


def test_excel_is_never_rebuilt_if_it_would_lose_rows():
    upsert_rows([{"Full Name": "Historic", "CV File": "old.pdf", KEY: "not-in-postgres"}])
    cv()
    with pytest.raises(ExcelNotMigrated):
        regenerate_excel()
    assert list(load_candidates()[KEY]) == ["not-in-postgres"]


# ---------- the pipeline writes to Postgres ----------

def put_cv(name):
    path = settings.cv_folder / "acct" / "May-2026" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes(name))
    return path


def test_extraction_stores_matches_and_rebuilds_the_excel():
    put_cv("a.pdf")
    put_cv("b.pdf")                                   # the mocked LLM gives both the same passport

    result = cv_extractor.run_extraction()

    assert result.extracted == 2 and len(result.placements) == 2
    assert sorted(p["attached"] for p in result.placements.values()) == [False, True]
    with session_scope() as session:
        people = session.scalars(select(Candidate)).all()
        documents = session.scalars(select(CvDocument)).all()
    assert len(people) == 1 and people[0].passport_norm == "P1234567"
    assert {d.mail_account for d in documents} == {"acct"} and all(d.s3_status == "failed" for d in documents)
    assert all(d.extracted["full_name"] == "Rahul Sharma" for d in documents)
    assert len(load_candidates()) == 2


def test_before_the_backfill_new_rows_are_added_to_the_old_excel():
    upsert_rows([{"Full Name": "Historic", "CV File": "old.pdf", KEY: "not-in-postgres"}])
    put_cv("a.pdf")

    result = cv_extractor.run_extraction()

    assert "Rows added to the existing Excel" in result.excel_note
    assert len(load_candidates()) == 2


def test_a_database_failure_leaves_the_cv_to_be_retried(monkeypatch):
    put_cv("a.pdf")
    monkeypatch.setattr(ingest, "place_cv", lambda *a: (_ for _ in ()).throw(RuntimeError("db down")))
    result = cv_extractor.run_extraction()
    assert result.failed == 1 and "db down" in next(iter(result.errors.values()))
    monkeypatch.undo()
    assert cv_extractor.run_extraction().extracted == 1


def test_manual_upload_reports_a_passport_match():
    from app.api.main import app
    client = TestClient(app)
    client.app.dependency_overrides.clear()
    from app.auth.deps import require_login
    app.dependency_overrides[require_login] = lambda: None

    def upload(text):
        task = client.post("/api/cvs/upload", files=[("files", ("cv.pdf", pdf_bytes(text), "application/pdf"))]).json()
        end = datetime.now() + timedelta(seconds=30)
        while datetime.now() < end:
            done = client.get(f"/api/tasks/{task['id']}").json()
            if done["status"] in ("done", "error"):
                return done
        raise TimeoutError

    first = upload("first")["result"]
    second = upload("second")["result"]
    assert first["message"] == "Added as a new candidate" and not first["attached_to_existing"]
    assert second["message"] == "Added to existing candidate" and second["candidate_id"] == first["candidate_id"]
    with session_scope() as session:
        assert {d.source_type for d in session.scalars(select(CvDocument))} == {"manual"}
