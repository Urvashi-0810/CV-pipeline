"""The Talent Pool settings page: admin only, secrets never sent, .env edited safely, connection tests."""
import os
from dataclasses import fields
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from dotenv import dotenv_values
from fastapi.testclient import TestClient

import mock_llm
from app.api.main import app
from app.auth import service as auth_service
from app.auth.security import hash_password
from app.core import config
from app.core.config import Settings, settings
from app.db.models import User
from app.db.session import session_scope
from app.storage import s3_store
from app.talent import connection_tests, settings_admin
from conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.real_auth
mock_llm.start()
SECRET = "sk-ant-super-secret-value-9876"


@pytest.fixture(autouse=True)
def isolated(db, tmp_path, monkeypatch):
    """A throwaway .env, and the running settings and environment put back afterwards."""
    env = tmp_path / ".env"
    env.write_bytes(("# LLM\r\nLLM_PROVIDER=gemini\r\nANTHROPIC_API_KEY=" + SECRET + "\r\n\r\n"
                     "# Mail\r\nIMAP_HOST=imap.zoho.in\r\nIMAP_FOLDER=INBOX\r\n").encode())
    monkeypatch.setattr(config, "ENV_FILE", env)
    saved_environ = dict(os.environ)
    saved_settings = {f.name: getattr(settings, f.name) for f in fields(Settings)}
    with session_scope() as session:
        session.add(User(username="urvashi", display_name="Urvashi", password_hash=hash_password("test@123")))
        auth_service.set_admin_password(session, "admin-pass")
    yield env
    os.environ.clear()
    os.environ.update(saved_environ)
    for name, value in saved_settings.items():
        setattr(settings, name, value)


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


def setting(body, key):
    return next(s for g in body["groups"] for s in g["settings"] if s["key"] == key)


def save(admin, values=None, clear=None):
    return admin.put("/api/admin/settings", json={"values": values or {}, "clear": clear or []})


# ---------- access and secrets ----------

def test_only_the_talent_pool_admin_can_see_settings(client):
    response = client.get("/api/admin/settings")
    assert response.status_code == 403 and response.json()["type"].endswith("admin-session-required")
    assert client.put("/api/admin/settings", json={"values": {}}).status_code == 403
    assert client.post("/api/admin/settings/test/mail").status_code == 403


def test_secrets_are_never_sent(admin, isolated):
    with isolated.open("ab") as f:
        f.write(b"DATABASE_URL=postgresql+psycopg://talent:db-password-123@localhost:5432/talent\r\n")
    response = admin.get("/api/admin/settings")
    assert SECRET not in response.text and "db-password-123" not in response.text
    claude_key = setting(response.json(), "ANTHROPIC_API_KEY")
    assert claude_key["is_set"] and claude_key["hint"] == "ends in 9876" and "value" not in claude_key
    assert "***" in setting(response.json(), "DATABASE_URL")["hint"]
    assert not setting(response.json(), "GEMINI_API_KEY")["is_set"]


def test_values_and_defaults(admin):
    body = admin.get("/api/admin/settings").json()
    assert [g["id"] for g in body["groups"]] == ["llm", "mail", "s3", "folders", "database", "login"]
    assert setting(body, "IMAP_HOST")["value"] == "imap.zoho.in" and not setting(body, "IMAP_HOST")["is_default"]
    port = setting(body, "IMAP_PORT")
    assert port["value"] == "993" and port["is_default"]


def test_the_defaults_match_the_app():
    """The page's defaults must be the ones the app uses when a setting is missing."""
    names = {d.key: d for d in settings_admin.DEFS}
    for key in [k for k in names if k in os.environ]:
        os.environ.pop(key)
    os.environ.update(CV_FOLDER="data/cvs", OUTPUT_EXCEL="data/output/candidates.xlsx", STATE_FOLDER="data/state",
                      JOBS_FOLDER="data/jobs")
    fresh = Settings()
    assert fresh.llm_provider == names["LLM_PROVIDER"].default
    assert fresh.claude_model == names["CLAUDE_MODEL"].default and fresh.gemini_model == names["GEMINI_MODEL"].default
    assert fresh.imap_port == int(names["IMAP_PORT"].default) and fresh.imap_folder == names["IMAP_FOLDER"].default
    assert fresh.max_attachment_mb == float(names["MAX_ATTACHMENT_MB"].default)
    assert fresh.aws_region == names["AWS_REGION"].default and fresh.aws_s3_prefix == names["AWS_S3_PREFIX"].default
    assert fresh.cv_parser == names["CV_PARSER"].default and fresh.imap_host == names["IMAP_HOST"].default
    assert fresh.session_ttl_hours == int(names["SESSION_TTL_HOURS"].default)
    assert fresh.admin_session_minutes == int(names["ADMIN_SESSION_MINUTES"].default)
    assert fresh.password_min_length == int(names["PASSWORD_MIN_LENGTH"].default)
    assert fresh.cookie_secure is False and ",".join(fresh.cors_origins) == names["CORS_ORIGINS"].default


# ---------- saving ----------

def test_saving_edits_lines_in_place_and_applies_at_once(admin, isolated):
    response = save(admin, {"IMAP_HOST": "imap.gmail.com", "IMAP_PORT": "143", "CLAUDE_MODEL": "claude-x"})

    assert response.status_code == 200 and response.json()["restart_required"] == []
    text = isolated.read_bytes().decode()
    assert "\r\n" in text and "\n" not in text.replace("\r\n", "")          # line endings kept
    assert text.startswith("# LLM\r\nLLM_PROVIDER=gemini\r\n")               # comments and order kept
    assert "IMAP_HOST=imap.gmail.com\r\nIMAP_FOLDER=INBOX" in text
    assert text.index("IMAP_PORT=143") > text.index("set from the Talent Pool settings page")
    assert settings.imap_host == "imap.gmail.com" and settings.imap_port == 143 and settings.claude_model == "claude-x"


def test_settings_read_at_startup_wait_for_a_restart(admin, isolated):
    before = settings.cv_folder
    response = save(admin, {"CV_FOLDER": "D:/elsewhere/cvs"})
    assert response.json()["restart_required"] == ["CV_FOLDER"]
    assert settings.cv_folder == before
    body = admin.get("/api/admin/settings").json()
    # (The test run's own temporary folders also differ from this throwaway .env, so only CV_FOLDER is checked.)
    assert "CV_FOLDER" in body["restart_pending"] and setting(body, "CV_FOLDER")["pending_restart"]
    assert "DATABASE_URL" not in body["restart_pending"] and not setting(body, "IMAP_HOST")["pending_restart"]


@pytest.mark.parametrize("value", [r"C:\Users\HP\My CVs", "has # hash", "it's quoted", 'say "hi"', "a\\b c's \"d\""])
def test_awkward_values_read_back_exactly(admin, isolated, value):
    assert save(admin, {"IMAP_FOLDER": value}).status_code == 200
    assert dotenv_values(isolated)["IMAP_FOLDER"] == value


def test_a_blank_secret_keeps_it_and_clear_removes_it(admin, isolated):
    save(admin, {"ANTHROPIC_API_KEY": ""})
    assert dotenv_values(isolated)["ANTHROPIC_API_KEY"] == SECRET
    save(admin, {"ANTHROPIC_API_KEY": "new-secret-value-1234"})
    assert dotenv_values(isolated)["ANTHROPIC_API_KEY"] == "new-secret-value-1234"
    assert settings.anthropic_api_key == "new-secret-value-1234"
    save(admin, clear=["ANTHROPIC_API_KEY"])
    assert "ANTHROPIC_API_KEY" not in dotenv_values(isolated) and settings.anthropic_api_key == ""


def test_a_blank_setting_goes_back_to_its_default(admin, isolated):
    save(admin, {"IMAP_FOLDER": ""})
    assert "IMAP_FOLDER" not in dotenv_values(isolated) and settings.imap_folder == "INBOX"


def test_aws_keys_are_set_in_the_environment_for_s3(admin):
    save(admin, {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE123456", "AWS_S3_BUCKET": "my-bucket"})
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE123456" and settings.aws_s3_bucket == "my-bucket"


def test_older_names_fill_only_a_missing_aws_name():
    blank = {"S3_BUCKET_NAME": "old-name-bucket", "AWS_S3_BUCKET": ""}
    config.apply_legacy_names(blank)
    assert blank["AWS_S3_BUCKET"] == ""                          # blank on purpose stays blank
    absent = {"S3_BUCKET_NAME": "old-name-bucket", "S3_ACCESS_KEY_ID": "AKIAOLD"}
    config.apply_legacy_names(absent)
    assert absent["AWS_S3_BUCKET"] == "old-name-bucket" and absent["AWS_ACCESS_KEY_ID"] == "AKIAOLD"


def test_older_s3_key_names_are_recognised_and_cleared_together(admin, isolated):
    with isolated.open("ab") as f:
        f.write(b"S3_ACCESS_KEY_ID=AKIAOLDNAME0000001\r\n")
    key = setting(admin.get("/api/admin/settings").json(), "AWS_ACCESS_KEY_ID")
    assert key["is_set"] and key["set_via"] == "S3_ACCESS_KEY_ID"
    save(admin, clear=["AWS_ACCESS_KEY_ID"])
    assert "S3_ACCESS_KEY_ID" not in dotenv_values(isolated)


@pytest.mark.parametrize("values,field", [
    ({"IMAP_PORT": "99999"}, "IMAP_PORT"),
    ({"IMAP_PORT": "abc"}, "IMAP_PORT"),
    ({"LLM_PROVIDER": "openai"}, "LLM_PROVIDER"),
    ({"COOKIE_SECURE": "maybe"}, "COOKIE_SECURE"),
    ({"FETCH_SINCE_DATE": "10/09/2026"}, "FETCH_SINCE_DATE"),
    ({"FETCH_SINCE_DATE": "2026-09-10", "FETCH_UNTIL_DATE": "2026-01-01"}, "FETCH_UNTIL_DATE"),
    ({"DATABASE_URL": "mysql://x@y/z"}, "DATABASE_URL"),
    ({"TEST_DATABASE_URL": "postgresql+psycopg://u:p@h/talent"}, "TEST_DATABASE_URL"),
    ({"CORS_ORIGINS": "http://localhost:5173/"}, "CORS_ORIGINS"),
    ({"IMAP_FOLDER": "two\nlines"}, "IMAP_FOLDER"),
    ({"NOT_A_SETTING": "x"}, "NOT_A_SETTING"),
])
def test_bad_values_are_refused_and_nothing_is_written(admin, isolated, values, field):
    before = isolated.read_bytes()
    response = save(admin, values)
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == field
    assert isolated.read_bytes() == before


def test_lists_are_tidied(admin, isolated):
    save(admin, {"SUBJECT_KEYWORDS": " resume , CV,, application "})
    assert dotenv_values(isolated)["SUBJECT_KEYWORDS"] == "resume,CV,application"
    assert settings.subject_keywords == ["resume", "cv", "application"]


# ---------- connection tests ----------

class FakeMailBox:
    folders = ["INBOX", "Jobs"]

    def __init__(self, host, port=993, timeout=None):
        self.folder = SimpleNamespace(list=lambda: [SimpleNamespace(name=n) for n in FakeMailBox.folders])

    def login(self, user, password):
        if password != "right":
            raise RuntimeError("AUTHENTICATIONFAILED")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_mail_connection(admin, monkeypatch):
    import imap_tools
    monkeypatch.setattr(imap_tools, "MailBox", FakeMailBox)
    save(admin, {"IMAP_USER": "hr@example.com", "IMAP_PASSWORD": "right", "IMAP_FOLDER": "Jobs"})
    assert admin.post("/api/admin/settings/test/mail").json()["ok"]
    save(admin, {"IMAP_FOLDER": "Missing"})
    result = admin.post("/api/admin/settings/test/mail").json()
    assert not result["ok"] and "no folder called" in result["message"]
    save(admin, {"IMAP_PASSWORD": "wrong"})
    failed = admin.post("/api/admin/settings/test/mail").json()
    assert not failed["ok"] and "AUTHENTICATIONFAILED" in failed["message"] and "wrong" not in failed["message"]


def test_s3_connection(admin, monkeypatch):
    calls = []

    class Client:
        def __init__(self, refuse=None):
            self.refuse = refuse

        def __getattr__(self, name):
            def call(**kwargs):
                calls.append(name)
                if name == self.refuse:
                    raise ClientError({"Error": {"Code": "AccessDenied"}}, name)
            return call

    save(admin, {"AWS_S3_BUCKET": "cv-bucket"})
    monkeypatch.setattr(s3_store, "_client", lambda: Client())
    result = admin.post("/api/admin/settings/test/s3").json()
    assert result["ok"] and calls == ["head_bucket", "list_objects_v2", "put_object", "delete_object"]
    monkeypatch.setattr(s3_store, "_client", lambda: Client(refuse="put_object"))
    refused = admin.post("/api/admin/settings/test/s3").json()
    assert not refused["ok"] and refused["message"] == "Failed while uploading: the keys aren't allowed to do this."


def test_database_connection(admin, isolated):
    save(admin, {"DATABASE_URL": TEST_DATABASE_URL})
    result = admin.post("/api/admin/settings/test/database").json()
    assert result["ok"] and "up to date" in result["message"]


def test_a_wrong_database_password_fails_without_showing_it(admin):
    from sqlalchemy import make_url
    url = make_url(TEST_DATABASE_URL).set(password="definitely-wrong-pw")
    save(admin, {"DATABASE_URL": url.render_as_string(hide_password=False)})
    result = admin.post("/api/admin/settings/test/database").json()
    assert not result["ok"] and "definitely-wrong-pw" not in str(result)


def test_llm_connection(admin, monkeypatch):
    result = admin.post("/api/admin/settings/test/llm").json()
    assert result["ok"] and result["details"]["answer"] == "pong"
    import app.llm.client as llm_client
    monkeypatch.setattr(llm_client, "get_llm", lambda provider: (_ for _ in ()).throw(RuntimeError("API key not valid")))
    failed = admin.post("/api/admin/settings/test/llm").json()
    assert not failed["ok"] and "API key not valid" in failed["message"]


def test_unknown_test_target(admin):
    assert admin.post("/api/admin/settings/test/ftp").status_code == 422
