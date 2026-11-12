"""The Talent Pool API: list and filters, detail, edits, review flags, merge, delete, CV links, S3 retry."""
import json
import threading
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import app
from app.api.tasks import tasks
from app.auth import service as auth_service
from app.auth.security import hash_password
from app.core.config import settings
from app.db.models import Candidate, CvDocument, EditHistory, ReviewFlag, User
from app.db.session import session_scope
from app.storage import s3_store
from app.storage.excel_store import KEY, write_sheets
from app.talent import excel_export, ingest, service

pytestmark = pytest.mark.real_auth
T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
_count = 0


@pytest.fixture(autouse=True)
def setup(clean_data, db, monkeypatch):
    refreshes = []
    monkeypatch.setattr(service, "request_refresh", lambda *a, **k: refreshes.append(1))
    monkeypatch.setattr("app.api.routes_talent.request_refresh", lambda *a, **k: refreshes.append(1))
    with session_scope() as session:
        session.add(User(username="urvashi", display_name="Urvashi", password_hash=hash_password("test@123")))
        auth_service.set_admin_password(session, "admin-pass")
    yield refreshes


@pytest.fixture
def client():
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "urvashi", "password": "test@123"})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return client


@pytest.fixture
def admin(client):
    assert client.post("/api/admin/session", json={"admin_password": "admin-pass"}).status_code == 204
    return client


def person(name="Rahul Sharma", when=T0, account="jobs@acct.com", source="mail", s3="success", file_hash=None,
           **values):
    """Place one CV and return (candidate id, cv id)."""
    global _count
    _count += 1
    file_hash = file_hash or f"hash{_count}"
    relative = f"{account if source == 'mail' else 'manual-upload'}/May-2026/{file_hash}.pdf"
    document = {"file_hash": file_hash, "file_name": f"{file_hash}.pdf", "received_at": when,
                "source_type": source, "mail_account": account if source == "mail" else None,
                "local_rel_path": relative, "s3_key": f"cvs/{relative}", "s3_status": s3}
    with session_scope() as session:
        placed = ingest.place_cv(session, document, {"full_name": name, **values})
    return placed.candidate_id, placed.cv_document_id


def ids(response):
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def get_flags(**where):
    with session_scope() as session:
        query = select(ReviewFlag).order_by(ReviewFlag.id)
        for name, value in where.items():
            query = query.where(getattr(ReviewFlag, name) == value)
        return session.scalars(query).all()


# ---------- list ----------

def test_list_columns_and_newest_first(client):
    old, _ = person("Old One", when=T0, job_title="Welder", passport_number="P1111111", experience_years=7.5,
                    location="Pune")
    new, _ = person("New One", when=T0 + timedelta(days=5))

    body = client.get("/api/talent/candidates").json()
    assert body["total"] == 2 and [i["id"] for i in body["items"]] == [new, old]
    item = body["items"][1]
    assert (item["full_name"], item["job_title"], item["passport_number"], item["experience_years"],
            item["location"], item["cv_count"], item["latest_cv_viewable"]) == \
           ("Old One", "Welder", "P1111111", 7.5, "Pune", 1, True)


def test_free_text_search_covers_the_searchable_fields(client):
    rahul, _ = person(passport_number="P1234567", skills=["Rigging"])
    person("Amit Verma")
    assert ids(client.get("/api/talent/candidates", params={"search": "p1234"})) == [rahul]
    assert ids(client.get("/api/talent/candidates", params={"search": "rigg"})) == [rahul]
    assert ids(client.get("/api/talent/candidates", params={"search": "100%"})) == []


def test_trade_location_and_skills_are_type_to_search(client):
    welder, _ = person(job_title="Pipe Welder", location="Navi Mumbai", skills=["MIG Welding", "TIG"])
    person("Amit Verma", job_title="Electrician", location="Pune", skills=["Wiring", "MIG Welding"])
    assert ids(client.get("/api/talent/candidates", params={"trade": "weld"})) == [welder]
    assert ids(client.get("/api/talent/candidates", params={"location": "mumbai"})) == [welder]
    assert ids(client.get("/api/talent/candidates", params={"skills": "mig, tig"})) == [welder]
    # A term can't match across two skills ("Welding" + "TIG" -> "ng ti" isn't a skill).
    assert ids(client.get("/api/talent/candidates", params={"skills": "ng ti"})) == []


def test_experience_range_keeps_unknown_values_unless_asked_not_to(client):
    seven, _ = person("Seven", experience_years=7)
    person("Two", experience_years=2)
    unknown, _ = person("Unknown")
    found = ids(client.get("/api/talent/candidates", params={"min_experience": 5, "max_experience": 10}))
    assert sorted(found) == sorted([seven, unknown])
    strict = client.get("/api/talent/candidates",
                        params={"min_experience": 5, "max_experience": 10, "include_unknown": False})
    assert ids(strict) == [seven]


def test_age_is_worked_out_from_the_date_of_birth(client):
    born = date.today().replace(year=date.today().year - 30) - timedelta(days=1)
    by_birth, _ = person("By Birth", date_of_birth=born.isoformat(), age=55)
    by_cv, _ = person("By CV", age=40)
    items = {i["id"]: i for i in client.get("/api/talent/candidates").json()["items"]}
    assert items[by_birth]["age"] == 30 and items[by_cv]["age"] == 40
    assert ids(client.get("/api/talent/candidates",
                          params={"min_age": 25, "max_age": 35, "include_unknown": False})) == [by_birth]
    assert ids(client.get("/api/talent/candidates", params={"sort": "age", "order": "asc"})) == [by_birth, by_cv]


def test_received_date_source_and_needs_review_filters(client):
    early, _ = person("Early", when=datetime(2025, 3, 1, 10, tzinfo=timezone.utc), account="a@x.com")
    manual, _ = person("Manual", when=datetime(2025, 3, 20, tzinfo=timezone.utc), source="manual")
    flagged, _ = person("Flag Me", passport_number="P7777777", email="f@x.com")
    person("Flag Me", passport_number="Z8888888", email="f@x.com")

    march = client.get("/api/talent/candidates", params={"received_from": "2025-03-01", "received_to": "2025-03-01"})
    assert ids(march) == [early]
    assert ids(client.get("/api/talent/candidates", params={"source": "manual"})) == [manual]
    assert ids(client.get("/api/talent/candidates", params={"source": "a@x.com"})) == [early]
    assert len(ids(client.get("/api/talent/candidates", params={"needs_review": True}))) == 2
    assert flagged in ids(client.get("/api/talent/candidates", params={"needs_review": True}))


def test_sorting_and_paging(client):
    for name in ("Charlie Brown", "alice Smith", "Bob Jones"):
        person(name)
    names = [i["full_name"] for i in client.get("/api/talent/candidates",
                                                params={"sort": "name", "order": "asc"}).json()["items"]]
    assert names == ["alice Smith", "Bob Jones", "Charlie Brown"]
    page = client.get("/api/talent/candidates", params={"sort": "name", "order": "asc", "limit": 1, "offset": 1})
    assert page.json()["total"] == 3 and [i["full_name"] for i in page.json()["items"]] == ["Bob Jones"]
    assert client.get("/api/talent/candidates", params={"limit": 1000}).status_code == 422


def test_filter_options_and_suggestions(client):
    person(account="a@x.com", job_title="Pipe Welder", skills=["MIG Welding"])
    person("Amit Verma", account="a@x.com", job_title="Pipe Welder")
    person("Manual One", source="manual", job_title="Welder")
    sources = client.get("/api/talent/filter-options").json()["sources"]
    assert sources == [{"value": "a@x.com", "label": "a@x.com", "candidates": 2},
                       {"value": "manual", "label": "Manual upload", "candidates": 1}]
    trades = client.get("/api/talent/suggest", params={"field": "trade", "q": "weld"}).json()["items"]
    assert trades == [{"value": "Pipe Welder", "candidates": 2}, {"value": "Welder", "candidates": 1}]
    skills = client.get("/api/talent/suggest", params={"field": "skills", "q": "mig"}).json()["items"]
    assert skills == [{"value": "MIG Welding", "candidates": 1}]


# ---------- detail and edits ----------

def test_detail_lists_every_cv_and_open_flags(client):
    rahul, first_cv = person(passport_number="P1234567", email="r@x.com", when=T0)
    _, second_cv = person(passport_number="P1234567", when=T0 + timedelta(days=1))
    other, _ = person("Rahul Sharma", passport_number="Z9999999", email="r@x.com")

    response = client.get(f"/api/talent/candidates/{rahul}")
    detail = response.json()
    assert response.headers["ETag"] == f'"{detail["row_version"]}"'
    assert [cv["id"] for cv in detail["cvs"]] == [second_cv, first_cv] and detail["cvs"][0]["is_latest"]
    [flag] = detail["flags"]
    assert flag["type"] == "possible_duplicate" and flag["other_candidate"]["id"] == other
    assert client.get("/api/talent/candidates/999999").status_code == 404


def edit(client, candidate_id, values, version=None):
    version = version or client.get(f"/api/talent/candidates/{candidate_id}").json()["row_version"]
    return client.patch(f"/api/talent/candidates/{candidate_id}", json={"values": values},
                        headers={"If-Match": f'"{version}"'})


def test_an_edit_is_saved_recorded_and_refreshes_the_excel(client, setup):
    rahul, _ = person(location="Pune", skills=["Welding"])
    response = edit(client, rahul, {"location": "Dubai", "skills": "Welding, TIG", "experience_years": "8.25"})

    assert response.status_code == 200 and response.json()["changed"] == ["location", "skills", "experience_years"]
    detail = client.get(f"/api/talent/candidates/{rahul}").json()
    assert detail["values"]["location"] == "Dubai" and detail["values"]["skills"] == ["Welding", "TIG"]
    assert detail["values"]["experience_years"] == 8.3
    assert detail["manual_fields"] == ["experience_years", "location", "skills"]
    history = client.get(f"/api/talent/candidates/{rahul}/history").json()["items"]
    location = next(h for h in history if h["field"] == "location")
    assert (location["old_value"], location["new_value"], location["changed_by"], location["kind"]) == \
           ("Pune", "Dubai", "Urvashi", "manual_edit")
    assert setup


def test_edits_need_the_current_version(client):
    rahul, _ = person()
    no_version = client.patch(f"/api/talent/candidates/{rahul}", json={"values": {"location": "Pune"}})
    assert no_version.status_code == 428
    assert edit(client, rahul, {"location": "Pune"}).status_code == 200
    stale = edit(client, rahul, {"location": "Goa"}, version=1)
    assert stale.status_code == 412 and stale.json()["type"].endswith("candidate-changed")


@pytest.mark.parametrize("values,field", [({"experience_years": "lots"}, "experience_years"),
                                          ({"row_version": 5}, "row_version"),
                                          ({"summary": "x" * 6000}, "summary")])
def test_bad_edits_are_refused(client, values, field):
    rahul, _ = person()
    response = edit(client, rahul, values)
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == field


def test_an_unchanged_value_is_not_an_edit(client):
    rahul, _ = person(location="Pune")
    assert edit(client, rahul, {"location": "Pune"}).json()["changed"] == []
    assert client.get(f"/api/talent/candidates/{rahul}/history").json()["items"] == []


def test_editing_a_passport_to_someone_elses_flags_but_never_merges(client):
    rahul, _ = person(passport_number="P1234567")
    amit, _ = person("Amit Verma", passport_number="Z7654321")
    assert edit(client, amit, {"passport_number": "p-123 4567"}).json()["flags_raised"] == 1
    [flag] = get_flags(status="open")
    assert {flag.candidate_id, flag.other_candidate_id} == {rahul, amit} and "same passport" in flag.reason
    with session_scope() as session:
        assert session.get(Candidate, amit) is not None


def test_editing_a_contact_runs_the_contact_rules(client):
    person(passport_number="P1234567", phone="+91 98765 43210")
    amit, _ = person("Rahul Sharma", passport_number="Z7654321")
    assert edit(client, amit, {"phone": "098765 43210"}).json()["flags_raised"] == 1


# ---------- review flags ----------

def duplicate_pair():
    first, _ = person(passport_number="P1234567", email="r@x.com", when=T0, location="Pune")
    second, _ = person("Sharma Rahul", passport_number="Z7654321", email="r@x.com", when=T0 + timedelta(days=3),
                       location="Dubai", company="ACME")
    [flag] = get_flags(status="open")
    return first, second, flag.id


def test_different_people_closes_the_flag_for_good(client):
    first, second, flag_id = duplicate_pair()
    assert client.post(f"/api/talent/flags/{flag_id}/different-people").status_code == 200
    [flag] = get_flags()
    assert (flag.status, flag.resolution) == ("resolved", "different_people") and flag.resolved_by_user_id
    for candidate_id in (first, second):
        assert not client.get(f"/api/talent/candidates/{candidate_id}").json()["needs_review"]
    assert client.post(f"/api/talent/flags/{flag_id}/different-people").status_code == 409


def test_merge_moves_every_cv_and_takes_the_newest_values(client):
    first, second, flag_id = duplicate_pair()
    response = client.post(f"/api/talent/flags/{flag_id}/merge")

    assert response.status_code == 200 and response.json() == {"candidate_id": first, "merged_candidate_id": second,
                                                               "cvs": 2}
    detail = client.get(f"/api/talent/candidates/{first}").json()
    assert len(detail["cvs"]) == 2 and detail["values"]["location"] == "Dubai"
    assert detail["values"]["company"] == "ACME" and not detail["needs_review"]
    assert client.get(f"/api/talent/candidates/{second}").status_code == 404
    [flag] = get_flags()
    assert flag.resolution == "merged" and flag.fields[0]["merged_candidate_id"] == second


def test_merge_keeps_manual_edits_from_both_and_the_latest_edit_wins(client):
    first, second, flag_id = duplicate_pair()
    edit(client, first, {"job_title": "Rigger", "location": "Pune City"})
    edit(client, second, {"location": "Abu Dhabi"})                    # later edit of the same field
    history_before = len(client.get(f"/api/talent/candidates/{first}/history").json()["items"])

    client.post(f"/api/talent/flags/{flag_id}/merge")

    detail = client.get(f"/api/talent/candidates/{first}").json()
    assert detail["values"]["job_title"] == "Rigger" and detail["values"]["location"] == "Abu Dhabi"
    assert detail["manual_fields"] == ["job_title", "location"]
    assert len(client.get(f"/api/talent/candidates/{first}/history").json()["items"]) == history_before + 1


def test_merge_repoints_the_removed_candidates_other_flags(client):
    first, second, flag_id = duplicate_pair()
    third, _ = person("Rahul Sharma", passport_number="Q5555555", email="r@x.com")
    assert len(get_flags(status="open")) == 3          # third vs first and third vs second

    client.post(f"/api/talent/flags/{flag_id}/merge")

    open_flags = get_flags(status="open")
    assert len(open_flags) == 1                        # the two flags against the same person became one
    assert {open_flags[0].candidate_id, open_flags[0].other_candidate_id} == {first, third}


def test_merge_keeps_the_removed_candidates_own_open_review_items(client):
    first, second, flag_id = duplicate_pair()
    edit(client, second, {"company": "My Edit"})
    person("Sharma Rahul", passport_number="Z7654321", company="Newer Co", when=T0 + timedelta(days=9))
    replaced = get_flags(type="edit_replaced")
    assert len(replaced) == 1 and replaced[0].candidate_id == second

    client.post(f"/api/talent/flags/{flag_id}/merge")

    [moved] = get_flags(type="edit_replaced")
    assert moved.status == "open" and moved.candidate_id == first
    detail = client.get(f"/api/talent/candidates/{first}").json()
    assert detail["needs_review"] and [f["type"] for f in detail["flags"]] == ["edit_replaced"]


def test_keep_my_edit_restores_the_value(client):
    rahul, _ = person(passport_number="P1234567", location="Pune", when=T0)
    edit(client, rahul, {"location": "Pune City"})
    person(passport_number="P1234567", location="Dubai", when=T0 + timedelta(days=1))
    [flag] = get_flags(status="open")
    assert client.get(f"/api/talent/candidates/{rahul}").json()["values"]["location"] == "Dubai"

    response = client.post(f"/api/talent/flags/{flag.id}/keep-edit", json={"field": "location"})

    assert response.status_code == 200 and response.json()["flag_resolved"]
    detail = client.get(f"/api/talent/candidates/{rahul}").json()
    assert detail["values"]["location"] == "Pune City" and not detail["needs_review"]
    kinds = [h["kind"] for h in client.get(f"/api/talent/candidates/{rahul}/history").json()["items"]]
    assert kinds == ["keep_my_edit", "replaced_by_cv", "manual_edit"]
    again = client.post(f"/api/talent/flags/{flag.id}/keep-edit", json={"field": "location"})
    assert again.status_code == 409


def test_accepting_a_flag_closes_it_without_changes(client):
    rahul, _ = person(passport_number="P1234567")
    person("Amit Verma", passport_number="P1234567", when=T0 + timedelta(days=1))
    [flag] = get_flags(status="open")
    assert flag.type == "passport_name_mismatch"
    assert client.post(f"/api/talent/flags/{flag.id}/accept").status_code == 200
    assert not client.get(f"/api/talent/candidates/{rahul}").json()["needs_review"]
    assert client.post(f"/api/talent/flags/{flag.id}/merge").status_code == 409


# ---------- CV links ----------

def test_cv_links_for_every_version(client, monkeypatch):
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    _, good = person()
    _, failed = person("Amit Verma", s3="failed")
    body = client.get(f"/api/talent/cvs/{good}/url", params={"download": True}).json()
    assert "/cvs/jobs%40acct.com/May-2026/" in body["url"] or "/cvs/jobs@acct.com/May-2026/" in body["url"]
    assert "attachment" in body["url"] and body["expires_in"] == 300
    assert client.get(f"/api/talent/cvs/{failed}/url").status_code == 409
    assert client.get("/api/talent/cvs/999999/url").status_code == 404


# ---------- delete ----------

def files_for(cv_hash, account="jobs@acct.com"):
    path = settings.cv_folder / account / "May-2026" / f"{cv_hash}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF")
    return path


def test_delete_needs_talent_pool_admin(client):
    rahul, _ = person()
    response = client.delete(f"/api/talent/candidates/{rahul}")
    assert response.status_code == 403 and response.json()["type"].endswith("admin-session-required")


def test_delete_removes_the_person_everywhere(admin, monkeypatch, setup):
    deleted_keys = []
    monkeypatch.setattr(s3_store, "delete_cv_keys", lambda keys: deleted_keys.extend(keys))
    rahul, _ = person(passport_number="P1234567", file_hash="aaa")
    person(passport_number="P1234567", file_hash="bbb", when=T0 + timedelta(days=1))
    keep, _ = person("Amit Verma", file_hash="ccc")
    local = [files_for(h) for h in ("aaa", "bbb", "ccc")]
    (settings.state_folder / "text_cache").mkdir(parents=True, exist_ok=True)
    (settings.state_folder / "text_cache" / "aaa.md").write_text("cv", encoding="utf-8")
    (settings.state_folder / "processed_files.json").write_text(json.dumps(
        {"aaa": {"status": "ok"}, "bbb": {"status": "ok"}, "ccc": {"status": "ok"}}), encoding="utf-8")
    (settings.state_folder / "attachments_manifest.json").write_text(json.dumps(
        {"by_hash": {"PDF:aaa": {}, "bbb": {}, "PDF:ccc": {}}}), encoding="utf-8")
    job = settings.jobs_folder / "welder"
    job.mkdir(parents=True)
    write_sheets(job / "shortlist.xlsx", {"Shortlisted": pd.DataFrame({KEY: ["aaa", "ccc"], "Full Name": ["R", "A"]}),
                                          "Criteria": pd.DataFrame({"Rule": ["x"]})})
    (job / "jobfit_results.json").write_text(json.dumps({"rubric_hash": "r", "results": {"bbb": {}, "ccc": {}}}),
                                             encoding="utf-8")

    response = admin.delete(f"/api/talent/candidates/{rahul}")

    assert response.status_code == 200, response.text
    assert response.json() | {} == {"deleted": rahul, "name": "Rahul Sharma", "cvs": 2, "s3_files": 2,
                                    "local_files": 2, "job_files_changed": 2}
    assert sorted(deleted_keys) == ["cvs/jobs@acct.com/May-2026/aaa.pdf", "cvs/jobs@acct.com/May-2026/bbb.pdf"]
    assert [p.exists() for p in local] == [False, False, True]
    assert not (settings.state_folder / "text_cache" / "aaa.md").exists()
    state = json.loads((settings.state_folder / "processed_files.json").read_text(encoding="utf-8"))
    manifest = json.loads((settings.state_folder / "attachments_manifest.json").read_text(encoding="utf-8"))
    assert list(state) == ["ccc"] and list(manifest["by_hash"]) == ["PDF:ccc"]
    shortlist = pd.read_excel(job / "shortlist.xlsx", sheet_name=None, dtype=str)
    assert list(shortlist["Shortlisted"][KEY]) == ["ccc"] and list(shortlist["Criteria"]["Rule"]) == ["x"]
    assert list(json.loads((job / "jobfit_results.json").read_text(encoding="utf-8"))["results"]) == ["ccc"]
    with session_scope() as session:
        assert [c.id for c in session.scalars(select(Candidate))] == [keep]
        assert len(session.scalars(select(CvDocument)).all()) == 1
    assert setup


def test_a_failed_s3_delete_deletes_nothing(admin, monkeypatch):
    monkeypatch.setattr(s3_store, "delete_cv_keys",
                        lambda keys: (_ for _ in ()).throw(RuntimeError("S3 refused")))
    rahul, _ = person(file_hash="aaa")
    path = files_for("aaa")
    response = admin.delete(f"/api/talent/candidates/{rahul}")
    assert response.status_code == 502 and response.json()["type"].endswith("s3-delete-failed")
    assert path.exists()
    with session_scope() as session:
        assert session.get(Candidate, rahul) is not None


def test_delete_is_refused_while_a_run_is_active(admin, monkeypatch):
    monkeypatch.setattr(s3_store, "delete_cv_keys", lambda keys: None)
    rahul, _ = person()
    release = threading.Event()
    running = tasks.submit("extract", "pipeline", lambda p: release.wait(10))
    try:
        response = admin.delete(f"/api/talent/candidates/{rahul}")
        assert response.status_code == 409 and response.json()["type"].endswith("pipeline-busy")
    finally:
        release.set()
    running.future.result(timeout=10)
    assert admin.delete(f"/api/talent/candidates/{rahul}").status_code == 200


# ---------- S3 retry and status ----------

def test_retrying_failed_s3_uploads(client, monkeypatch):
    uploaded = []
    monkeypatch.setattr(s3_store, "upload_cv_to_s3", lambda path: uploaded.append(path.name) or True)
    _, failed = person(s3="failed", file_hash="aaa")
    person("Amit Verma", s3="missing", file_hash="bbb")             # no local file
    person("Suresh Patil", file_hash="ccc")                          # already fine
    files_for("aaa")
    (settings.state_folder / "processed_files.json").write_text(json.dumps({"aaa": {"s3_status": "Failed"}}),
                                                              encoding="utf-8")

    task = client.post("/api/talent/s3/retry").json()
    tasks.get(task["id"]).future.result(timeout=30)
    done = client.get(f"/api/tasks/{task['id']}").json()

    assert done["status"] == "done" and done["result"] == {"tried": 2, "uploaded": 1, "failed": 0,
                                                           "no_local_file": 1, "cancelled": False}
    assert done["controllable"]
    assert uploaded == ["aaa.pdf"]
    with session_scope() as session:
        assert session.get(CvDocument, failed).s3_status == "success"
    state = json.loads((settings.state_folder / "processed_files.json").read_text(encoding="utf-8"))
    assert state["aaa"]["s3_status"] == "Success"


def test_status_includes_the_talent_counts(client):
    person(passport_number="P1234567", email="r@x.com", s3="failed")
    person("Rahul Sharma", passport_number="Z7654321", email="r@x.com", s3="missing")
    talent = client.get("/api/status").json()["talent"]
    assert talent == {"candidates": 2, "cv_documents": 2, "needs_review": 2, "s3_failed": 1, "s3_missing": 1}


def test_excel_shows_the_age_from_the_date_of_birth():
    born = date.today().replace(year=date.today().year - 30) - timedelta(days=1)
    person(date_of_birth=born.isoformat(), age=55)
    excel_export.regenerate_excel()
    assert pd.read_excel(settings.output_excel, dtype=str)["Age"][0] == "30"
