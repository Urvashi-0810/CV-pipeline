"""The client's fields: experience and age arithmetic, the Excel layout, editing, and re-extraction."""
from datetime import date, datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mock_llm
from app.api.main import app
from app.api.tasks import tasks
from app.auth import service as auth_service
from app.auth.security import hash_password
from app.core.config import settings
from app.db.models import Candidate, User
from app.db.session import session_scope
from app.extractor import cv_extractor
from app.storage import s3_store
from app.storage.excel_store import load_candidates
from app.talent import experience as E
from app.talent import ingest
from app.talent.excel_export import CLIENT_COLUMNS, regenerate_excel
from conftest import pdf_bytes

mock_llm.start()
TODAY = date(2026, 11, 13)


def job(start, end, country="India", sector="construction", other=None):
    return {"country": country, "sector": sector, "other_sector": other, "start_date": start, "end_date": end}


# ---------- experience and age (no database) ----------

def test_months_count_inclusive_and_overlaps_once():
    work = [job("2018-03", "2021-06"), job("2020-01", "2022-02")]          # overlap Jan 2020 - Jun 2021
    assert E.experience_columns(work, TODAY)["Total Experience"] == "4 yrs"   # Mar 2018 - Feb 2022 = 48 months


def test_ongoing_jobs_end_this_month():
    work = [job("2025-12-01", "present")]
    assert E.experience_columns(work, TODAY)["Total Experience"] == "1 yr"    # Dec 2025 - Nov 2026


def test_a_year_on_its_own_covers_the_whole_year():
    assert E.experience_columns([job("2015", "2018")], TODAY)["Total Experience"] == "4 yrs"   # Jan 2015 - Dec 2018
    assert E.experience_columns([job("2018", "2018")], TODAY)["Total Experience"] == "1 yr"
    work = [job("2015", "2018"), job("2020-01", "2020-03")]
    assert E.experience_columns(work, TODAY)["Total Experience"] == "4 yrs 3 mths"
    assert E.experience_columns([job("2021", "present")], TODAY)["Total Experience"] == "5 yrs 11 mths"  # Jan 2021 - Nov 2026
    assert E.experience_columns([job("2016-06", "2018")], TODAY)["Total Experience"] == "2 yrs 7 mths"   # Jun 2016 - Dec 2018


def test_jobs_without_dates_are_not_counted():
    assert E.experience_columns([job(None, None)], TODAY)["Total Experience"] == E.NOT_FOUND
    assert E.experience_columns([job("2015", None), job("2020-01", "2020-03")], TODAY)["Total Experience"] == "3 mths"


def test_no_jobs_at_all_is_not_found():
    assert set(E.experience_columns([], TODAY).values()) == {E.NOT_FOUND}


def test_india_gulf_and_sectors():
    work = [
        job("2010-01", "2011-12", "India", "construction"),
        job("2012-01", "2014-06", "Dubai", "oil_gas"),
        job("2014-07", "2015-06", "KSA", "facility_management"),
        job("2015-07", "2016-06", "Iraq", "oil_gas"),
        job("2016-07", "2017-06", "Malaysia", "other", "manufacturing"),
        job("2017-07", "2017-12", "Oman", "other", "Power plant"),
    ]
    columns = E.experience_columns(work, TODAY)
    assert columns["Indian Experience"] == "2 yrs"
    assert columns["Gulf Experience"] == "5 yrs"                # Dubai, KSA, Iraq, Oman (not Malaysia)
    assert columns["Total Experience"] == "8 yrs"
    assert columns["Oil & Gas"] == "3 yrs 6 mths"
    assert columns["Construction"] == "2 yrs" and columns["Facility Management Experience"] == "1 yr"
    assert columns["Marine/Shipyard"] == E.NOT_APPLICABLE and columns["Healthcare"] == E.NOT_APPLICABLE
    assert columns["Other Relevant Sector Experience"] == "Manufacturing: 1 yr; Power plant: 6 mths"


def test_none_in_a_category_is_a_dash():
    columns = E.experience_columns([job("2020-01", "2020-12", "India", "healthcare")], TODAY)
    assert columns["Gulf Experience"] == "-" and columns["Other Relevant Sector Experience"] == "-"
    assert columns["Healthcare"] == "1 yr"


@pytest.mark.parametrize("born,stated,expected", [
    (date(1992, 3, 1), None, "34 yrs 8 mths"),
    (date(1992, 11, 14), None, "33 yrs 11 mths"),       # birthday tomorrow
    (date(2025, 11, 13), None, "1 yr"),
    (None, 40, "40 yrs"),
    (None, None, "Not found"),
])
def test_age_in_years_and_months(born, stated, expected):
    assert E.age_text(born, stated, TODAY) == expected


def test_dates_in_the_clients_format():
    assert E.dmy("1992-01-03") == "03-01-1992" and E.dmy("March 1992") == "March 1992" and E.dmy(None) is None


# ---------- the database, the Excel and the API ----------

@pytest.fixture
def placed(clean_data, db):
    def place(name="Rahul Sharma", file_hash="h1", **values):
        extracted = {"full_name": name, "work_history": [], **values}
        with session_scope() as session:
            return ingest.place_cv(session, {"file_hash": file_hash, "file_name": f"{file_hash}.pdf",
                                             "received_at": datetime(2026, 1, 5, tzinfo=timezone.utc),
                                             "extracted_at": datetime(2026, 1, 5, 9, tzinfo=timezone.utc)},
                                   extracted)
    return place


def test_the_excel_has_the_clients_layout(placed):
    placed(passport_number="P1234567", passport_type="ECR", passport_expiry="2031-05-20",
           date_of_birth="1992-01-03", gender="Male", nationality="Indian", phone="+91 98765 43210",
           education="ITI Welding - Govt ITI (2010); 10th", certifications=["AWS D1.1"], job_title="Welder",
           summary="Experienced welder.", work_history=[job("2015-01", "2019-12", "Qatar", "oil_gas")])
    placed("No Details", file_hash="h2")

    assert regenerate_excel() == 2
    sheet = pd.read_excel(settings.output_excel, sheet_name="Candidates", dtype=str)
    assert list(sheet.columns) == list(CLIENT_COLUMNS)
    assert list(sheet.columns[:23]) == [
        "SR NO", "CV Extraction Date", "Candidate Name", "Trade", "Passport No", "PP Type", "PassPort Expiry",
        "D-O-B", "Age", "Gender", "Nationality", "Qualification", "Certification", "Indian Experience",
        "Gulf Experience", "Total Experience", "Contact Number", "Facility Management Experience", "Oil & Gas",
        "Construction", "Marine/Shipyard", "Healthcare", "Other Relevant Sector Experience"]
    full, empty = sheet.iloc[0], sheet.iloc[1]
    assert (full["SR NO"], full["CV Extraction Date"], full["PP Type"], full["PassPort Expiry"], full["D-O-B"]) == \
           ("1", "05-01-2026", "ECR", "20-05-2031", "03-01-1992")
    assert full["Qualification"] == "ITI Welding - Govt ITI (2010)" and full["Certification"] == "AWS D1.1"
    assert (full["Gulf Experience"], full["Indian Experience"], full["Oil & Gas"]) == ("5 yrs", "-", "5 yrs")
    assert empty["Candidate Name"] == "No Details" and empty["Passport No"] == "Not found"
    assert empty["PP Type"] == "Not found" and empty["Gender"] == "Not found" and empty["Certification"] == "-"
    assert empty["Total Experience"] == "Not found" and empty["Age"] == "Not found"
    # Everything stored is still on "All fields", which the shortlist and job-fit steps read.
    assert list(load_candidates()["Full Name"]) == ["Rahul Sharma", "No Details"]


def test_extraction_fills_the_clients_fields(clean_data, db):
    path = settings.cv_folder / "acct" / "May-2026" / "a.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(pdf_bytes("a"))
    cv_extractor.run_extraction()
    with session_scope() as session:
        person = session.scalar(select(Candidate))
        assert (person.gender, person.nationality, person.passport_type, person.passport_expiry) == \
               ("Male", "Indian", "ECNR", "2031-05-20")
        assert [j["country"] for j in person.work_history] == ["India", "United Arab Emirates"]
        assert float(person.experience_years) == pytest.approx(E.total_years(person.work_history), abs=0.1)
    row = pd.read_excel(settings.output_excel, sheet_name="Candidates", dtype=str).iloc[0]
    assert row["Gulf Experience"] == "3 yrs 10 mths" and row["Oil & Gas"] == "3 yrs 10 mths"
    assert row["Other Relevant Sector Experience"].startswith("IT services: ")


@pytest.fixture
def client(clean_data, db):
    with session_scope() as session:
        session.add(User(username="urvashi", display_name="Urvashi", password_hash=hash_password("test@123")))
        auth_service.set_admin_password(session, "admin-pass")
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "urvashi", "password": "test@123"})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return client


def edit(client, candidate_id, values):
    version = client.get(f"/api/talent/candidates/{candidate_id}").json()["row_version"]
    return client.patch(f"/api/talent/candidates/{candidate_id}", json={"values": values},
                        headers={"If-Match": f'"{version}"'})


@pytest.mark.real_auth
def test_detail_and_list_show_the_clients_values(client, placed):
    placed(date_of_birth="1992-01-03", work_history=[job("2020-01", "present", "Kuwait", "marine_shipyard")])
    detail = client.get("/api/talent/candidates/1").json()
    assert detail["client"]["D-O-B"] == "03-01-1992" and detail["client"]["Marine/Shipyard"].endswith("mths")
    assert detail["client"]["Gulf Experience"] == detail["client"]["Total Experience"]
    item = client.get("/api/talent/candidates").json()["items"][0]
    assert item["total_experience"] == detail["client"]["Total Experience"] and item["age_text"].startswith("3")


@pytest.mark.real_auth
def test_editing_the_new_fields(client, placed):
    placed()
    assert edit(client, 1, {"gender": "Other"}).status_code == 422
    assert edit(client, 1, {"passport_type": "XYZ"}).status_code == 422
    assert edit(client, 1, {"passport_expiry": "31-02-2030"}).status_code == 422
    assert edit(client, 1, {"work_history": []}).status_code == 422
    ok = edit(client, 1, {"gender": "Female", "passport_type": "ECNR", "passport_expiry": "20-05-2031",
                          "nationality": "Nepali"})
    assert ok.status_code == 200
    values = client.get("/api/talent/candidates/1").json()["values"]
    assert (values["gender"], values["passport_expiry"], values["nationality"]) == ("Female", "2031-05-20", "Nepali")


@pytest.mark.real_auth
def test_a_hand_edited_experience_isnt_overwritten_by_the_calculation(client, placed):
    placed(work_history=[job("2020-01", "2020-12")])
    edit(client, 1, {"experience_years": "9"})
    edit(client, 1, {"location": "Pune"})                 # any later save recalculates the rest
    assert client.get("/api/talent/candidates/1").json()["values"]["experience_years"] == 9.0


def wait(client, task_id):
    tasks.get(task_id).future.result(timeout=60)
    return client.get(f"/api/tasks/{task_id}").json()


@pytest.mark.real_auth
def test_reextract_one_candidate_fills_the_new_fields(client, placed):
    placed(file_hash="old")
    with session_scope() as session:
        from app.db.models import CvDocument
        cv = session.scalar(select(CvDocument))
        cv.local_rel_path = "acct/May-2026/old.pdf"
    path = settings.cv_folder / "acct" / "May-2026" / "old.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(pdf_bytes("old"))

    task = client.post("/api/talent/candidates/1/reextract").json()
    assert task["controllable"]
    done = wait(client, task["id"])
    assert done["status"] == "done" and done["result"]["updated"] == 1
    detail = client.get("/api/talent/candidates/1").json()
    assert detail["values"]["gender"] == "Male" and detail["client"]["Gulf Experience"] == "3 yrs 10 mths"
    assert len(detail["cvs"]) == 1                                  # the same CV, not a new one


@pytest.mark.real_auth
def test_reextract_downloads_from_s3_when_the_file_isnt_here(client, placed, monkeypatch):
    placed(file_hash="old")
    with session_scope() as session:
        from app.db.models import CvDocument
        cv = session.scalar(select(CvDocument))
        cv.s3_key, cv.s3_status = "cvs/acct/old.pdf", "success"
    fetched = []

    def download(key, target):
        fetched.append(key)
        target.write_bytes(pdf_bytes("from s3"))
    monkeypatch.setattr(s3_store, "download_cv", download)

    done = wait(client, client.post("/api/talent/candidates/1/reextract").json()["id"])
    assert fetched == ["cvs/acct/old.pdf"] and done["result"]["updated"] == 1


@pytest.mark.real_auth
def test_reextract_for_a_filtered_list(client, placed):
    placed(file_hash="a")
    placed("Amit Verma", file_hash="b", work_history=[job("2020-01", "2020-12")])
    count = client.get("/api/talent/reextract/count", params={"missing_client_fields": True}).json()
    assert count == {"candidates": 1}
    done = wait(client, client.post("/api/talent/reextract", params={"missing_client_fields": True}).json()["id"])
    assert done["result"]["candidates"] == 1 and done["result"]["no_cv_file"] == 1     # no file for it in the test
    nothing = client.post("/api/talent/reextract", params={"search": "nobody-matches"})
    assert nothing.status_code == 400
