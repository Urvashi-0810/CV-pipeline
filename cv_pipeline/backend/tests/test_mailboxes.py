"""Several mailboxes: managing them, encrypted passwords, and fetching one folder for new mail or a date range."""
import os
import threading
from types import SimpleNamespace

import imap_tools
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mock_llm
from app.api.main import app
from app.api.tasks import ACTIVE, tasks
from app.auth import service as auth_service
from app.auth.security import hash_password
from app.core.config import settings
from app.core.state import JsonState
from app.db.models import AppSettings, Candidate, Mailbox, User
from app.db.session import session_scope
from app.extractor import cv_extractor
from app.fetcher import mail_fetcher
from app.talent import mailboxes
from test_fetcher import FakeMailbox, att, mail, pdf

mock_llm.start()
PASSWORD = "zoho-app-password-123"


@pytest.fixture(autouse=True)
def clean(clean_data, db):
    yield


@pytest.fixture
def admin():
    with session_scope() as session:
        session.add(User(username="urvashi", display_name="Urvashi", password_hash=hash_password("test@123")))
        auth_service.set_admin_password(session, "admin-pass")
    client = TestClient(app)
    login = client.post("/api/auth/login", json={"username": "urvashi", "password": "test@123"})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    client.post("/api/admin/session", json={"admin_password": "admin-pass"})
    return client


def add(client, **values):
    body = {"name": "Recruitment", "email": "cv@shieldglobal.in", "password": PASSWORD, **values}
    return client.post("/api/mailboxes", json=body)


# ---------- managing mailboxes ----------

@pytest.mark.real_auth
def test_adding_a_mailbox_stores_the_password_encrypted(admin):
    response = add(admin)
    assert response.status_code == 201
    body = response.json()
    assert (body["host"], body["port"], body["default_folder"]) == ("imap.zoho.in", 993, "INBOX")
    assert PASSWORD not in response.text and "password" not in body
    with session_scope() as session:
        stored = session.scalar(select(Mailbox))
        assert PASSWORD not in stored.password_encrypted and mailboxes._decrypt(stored) == PASSWORD
    listed = admin.get("/api/mailboxes").json()["items"]
    assert [m["email"] for m in listed] == ["cv@shieldglobal.in"] and PASSWORD not in str(listed)


@pytest.mark.real_auth
def test_only_the_talent_pool_admin_changes_mailboxes(admin):
    add(admin)
    plain = TestClient(app)
    login = plain.post("/api/auth/login", json={"username": "urvashi", "password": "test@123"})
    plain.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    assert plain.get("/api/mailboxes").status_code == 200                       # listing is for everyone
    assert add(plain, email="other@shieldglobal.in").status_code == 403
    assert plain.patch("/api/mailboxes/1", json={"name": "x"}).status_code == 403
    assert plain.delete("/api/mailboxes/1").status_code == 403
    assert plain.post("/api/mailboxes/1/test").status_code == 403


@pytest.mark.real_auth
@pytest.mark.parametrize("values,field", [
    ({"email": "not-an-email"}, "email"), ({"password": ""}, "password"), ({"port": 99999}, "port"),
    ({"host": "bad host!"}, "host"), ({"name": " "}, "name"),
])
def test_bad_mailboxes_are_refused(admin, values, field):
    response = add(admin, **values)
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == field


@pytest.mark.real_auth
def test_the_same_mailbox_cant_be_added_twice(admin):
    add(admin)
    assert add(admin, email="CV@ShieldGlobal.in", name="Again").status_code == 409


@pytest.mark.real_auth
def test_editing_keeps_the_password_unless_a_new_one_is_given(admin):
    add(admin)
    assert admin.patch("/api/mailboxes/1", json={"name": "Jobs", "password": "", "default_folder": "CVs"}).status_code == 200
    with session_scope() as session:
        stored = session.get(Mailbox, 1)
        assert (stored.name, stored.default_folder, mailboxes._decrypt(stored)) == ("Jobs", "CVs", PASSWORD)
    admin.patch("/api/mailboxes/1", json={"password": "new-password-456"})
    with session_scope() as session:
        assert mailboxes._decrypt(session.get(Mailbox, 1)) == "new-password-456"


@pytest.mark.real_auth
def test_removing_a_mailbox_keeps_its_candidates(admin):
    add(admin)
    with session_scope() as session:
        session.add(Candidate(full_name="From that mailbox"))
    assert admin.delete("/api/mailboxes/1").status_code == 204
    assert admin.get("/api/mailboxes").json()["items"] == []
    with session_scope() as session:
        assert session.scalar(select(Candidate.full_name)) == "From that mailbox"


@pytest.mark.real_auth
def test_a_changed_key_asks_for_the_password_again(admin, monkeypatch):
    add(admin)
    from cryptography.fernet import Fernet
    monkeypatch.setenv("MAILBOX_SECRET_KEY", Fernet.generate_key().decode())
    response = admin.get("/api/mailboxes/1/folders")
    assert response.status_code == 409 and "enter its app password again" in response.json()["detail"]


class LoginBox:
    folders = ["INBOX", "CVs 2024", "Spam"]

    def __init__(self, host, port=993, timeout=None):
        self.folder = SimpleNamespace(list=lambda: [SimpleNamespace(name=n) for n in LoginBox.folders])

    def login(self, user, password):
        if password != PASSWORD:
            raise RuntimeError("AUTHENTICATIONFAILED")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.mark.real_auth
def test_testing_a_mailbox_and_listing_its_folders(admin, monkeypatch):
    monkeypatch.setattr(imap_tools, "MailBox", LoginBox)
    add(admin)
    assert admin.post("/api/mailboxes/1/test").json()["ok"]
    assert admin.get("/api/mailboxes/1/folders").json()["items"] == ["INBOX", "CVs 2024", "Spam"]
    admin.patch("/api/mailboxes/1", json={"default_folder": "Missing"})
    assert "no folder called" in admin.post("/api/mailboxes/1/test").json()["message"]
    admin.patch("/api/mailboxes/1", json={"password": "wrong-password"})
    failed = admin.post("/api/mailboxes/1/test").json()
    assert not failed["ok"] and "AUTHENTICATIONFAILED" in failed["message"] and "wrong-password" not in str(failed)


def test_the_env_mailbox_is_copied_in_once(monkeypatch):
    monkeypatch.setattr(settings, "imap_user", "old@shieldglobal.in")
    monkeypatch.setattr(settings, "imap_password", PASSWORD)
    first = mailboxes.import_env_mailbox()
    assert first is not None and mailboxes.import_env_mailbox() is None
    with session_scope() as session:
        session.delete(session.get(Mailbox, first))
    assert mailboxes.import_env_mailbox() is None                  # removing it doesn't bring it back
    with session_scope() as session:
        assert session.scalar(select(Mailbox)) is None and session.scalar(select(AppSettings.mailboxes_imported))


# ---------- fetching one mailbox folder ----------

class RecordingBox(FakeMailbox):
    logins, folders_set, searches = [], [], []

    def __init__(self, host, port=993, **k):
        super().__init__()
        self.folder = SimpleNamespace(set=lambda name: RecordingBox.folders_set.append(name))
        self.host = host

    def login(self, user, password, initial_folder=None):
        RecordingBox.logins.append((self.host, user, password, initial_folder))
        return self

    def uids(self, criteria):
        RecordingBox.searches.append(str(criteria))
        return super().uids(criteria)


@pytest.fixture
def mailbox_with_mail(monkeypatch):
    monkeypatch.setattr(mail_fetcher, "MailBox", RecordingBox)
    RecordingBox.logins, RecordingBox.folders_set, RecordingBox.searches = [], [], []
    RecordingBox.messages = {1: mail(1, [att("a.pdf", pdf("a"))], subject="CV A"),
                             2: mail(2, [att("b.pdf", pdf("b"))], subject="CV B")}
    RecordingBox.uidvalidity = 100
    with session_scope() as session:
        mailbox = mailboxes.create(session, {"name": "Recruitment", "email": "cv@shieldglobal.in",
                                             "password": PASSWORD})
        return mailbox.id


def run_fetch(client, **body):
    response = client.post("/api/fetch", json=body)
    assert response.status_code == 200, response.text
    tasks.get(response.json()["id"]).future.result(timeout=60)
    return client.get(f"/api/tasks/{response.json()['id']}").json()


def test_a_fetch_reads_the_chosen_mailbox_and_folder(mailbox_with_mail):
    client = TestClient(app)
    done = run_fetch(client, mailbox_id=mailbox_with_mail, folder="CVs 2024")
    assert done["status"] == "done" and done["result"]["attachments_saved"] == 2
    assert RecordingBox.logins[0][:3] == ("imap.zoho.in", "cv@shieldglobal.in", PASSWORD)
    assert "CVs 2024" in RecordingBox.folders_set or RecordingBox.logins[0][3] == "CVs 2024"
    assert (done["result"]["mailbox"], done["result"]["folder"], done["result"]["mode"]) == \
           ("Recruitment", "CVs 2024", "new")
    state = JsonState(settings.state_folder / "mail_state.json").data
    assert state["cv@shieldglobal.in|imap.zoho.in|CVs 2024"]["last_uid"] == 2
    assert {p.parts[-3] for p in settings.cv_folder.rglob("*.pdf")} == {"cv@shieldglobal.in"}


def test_a_date_range_includes_the_to_date_and_leaves_the_marker(mailbox_with_mail):
    client = TestClient(app)
    run_fetch(client, mailbox_id=mailbox_with_mail)                          # normal fetch: marker at 2
    RecordingBox.messages[3] = mail(3, [att("c.pdf", pdf("c"))], subject="CV C")

    done = run_fetch(client, mailbox_id=mailbox_with_mail, since="2024-01-01", until="2024-03-31")
    assert done["result"]["mode"] == "range" and done["result"]["until"] == "2024-03-31"
    search = RecordingBox.searches[-1]
    assert "SINCE 1-Jan-2024" in search and "BEFORE 1-Apr-2024" in search     # 31-03 included
    state = JsonState(settings.state_folder / "mail_state.json").data["cv@shieldglobal.in|imap.zoho.in|INBOX"]
    assert state["last_uid"] == 2 and state["last_range"] == "2024-01-01 to 2024-03-31"

    again = run_fetch(client, mailbox_id=mailbox_with_mail)                  # new mail still found afterwards
    assert again["result"]["mails_scanned"] == 1 and again["result"]["last_uid"] == 3


@pytest.mark.parametrize("body,message", [
    ({"since": "2024-01-01"}, "both a from date and a to date"),
    ({"since": "2024-05-01", "until": "2024-01-01"}, "on or before the to date"),
    ({}, "Choose which mailbox"),
])
def test_bad_fetch_requests_are_refused(mailbox_with_mail, body, message):
    if "since" in body:
        body["mailbox_id"] = mailbox_with_mail
    response = TestClient(app).post("/api/fetch", json=body)
    assert response.status_code == 422 and message in str(response.json())


def test_a_second_fetch_waits_and_a_waiting_one_can_be_cancelled(mailbox_with_mail, monkeypatch):
    started, go = threading.Semaphore(0), threading.Semaphore(0)
    real = cv_extractor.extract_one

    def gated(path, provider):
        started.release()
        assert go.acquire(timeout=20)
        return real(path, provider)
    monkeypatch.setattr(cv_extractor, "extract_one", gated)
    client = TestClient(app)
    try:
        first = client.post("/api/fetch", json={"mailbox_id": mailbox_with_mail}).json()
        assert started.acquire(timeout=20)
        second = client.post("/api/fetch", json={"mailbox_id": mailbox_with_mail, "folder": "CVs 2024"}).json()
        third = client.post("/api/fetch", json={"mailbox_id": mailbox_with_mail, "folder": "Spam"}).json()
        assert second["status"] == "queued" and third["status"] == "queued"
        assert client.post(f"/api/tasks/{third['id']}/cancel").status_code == 200
    finally:
        for _ in range(50):
            go.release()
    for task_id in (first["id"], second["id"], third["id"]):
        tasks.get(task_id).future.result(timeout=60)
    assert client.get(f"/api/tasks/{first['id']}").json()["status"] == "done"
    assert client.get(f"/api/tasks/{second['id']}").json()["status"] == "done"
    skipped = client.get(f"/api/tasks/{third['id']}").json()
    assert skipped["status"] == "cancelled" and skipped["result"]["never_started"]
    assert all(t.status not in ACTIVE for t in tasks.list())
