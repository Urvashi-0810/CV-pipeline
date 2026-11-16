"""Talent Pool login, CSRF, admin unlock and user management, against the real database."""
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import bootstrap_auth
from app.api.main import PUBLIC_PATHS, app
from app.auth import service
from app.auth.security import hash_password, session_cookie_name
from app.core.config import settings
from app.db.models import AppSettings, AuthSession, User
from app.db.session import session_scope

pytestmark = pytest.mark.real_auth
PASSWORD = "test@123"
ADMIN_PASSWORD = "admin-pass"


@pytest.fixture(autouse=True)
def clean(db):
    yield


def add_user(username="urvashi", password=PASSWORD, **values):
    with session_scope() as session:
        user = User(username=username, display_name=username.title(), password_hash=hash_password(password), **values)
        session.add(user)
        session.flush()
        return user.id


def set_admin_password(password=ADMIN_PASSWORD):
    with session_scope() as session:
        service.set_admin_password(session, password)


def logged_in(username="urvashi", password=PASSWORD):
    """A client holding a session. Its CSRF token is sent on every request."""
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def admin_client():
    add_user()
    set_admin_password()
    client = logged_in()
    assert client.post("/api/admin/session", json={"admin_password": ADMIN_PASSWORD}).status_code == 204
    return client


def problem(response):
    return response.json()["type"].removeprefix("urn:talent:problem:")


# ---------- every route needs a login ----------

def _routes():
    # From the OpenAPI schema: FastAPI keeps included routers nested, so app.routes doesn't list them.
    for path, operations in app.openapi()["paths"].items():
        if path not in PUBLIC_PATHS:
            for method in operations:
                yield method.upper(), re.sub(r"\{[^}]+\}", "1", path)


@pytest.mark.parametrize("method,path", sorted(_routes()))
def test_every_route_refuses_anonymous_callers(method, path):
    response = TestClient(app).request(method, path)
    assert response.status_code == 401, response.text
    assert problem(response) == "not-authenticated"


def test_route_list_covers_the_pipeline_and_jobs_routes():
    paths = {path for _, path in _routes()}
    assert {"/api/candidates", "/api/cvs/upload", "/api/fetch", "/api/jobs", "/api/admin/users"} <= paths


def test_health_stays_open():
    assert TestClient(app).get("/api/health").json() == {"ok": True}


def test_a_logged_in_user_reaches_pipeline_routes(clean_data):
    add_user()
    assert logged_in().get("/api/candidates").status_code == 200


# ---------- login ----------

def test_login_sets_an_http_only_session_cookie():
    add_user()
    response = TestClient(app).post("/api/auth/login", json={"username": "URVASHI", "password": PASSWORD})

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("talent_session=") and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert response.json()["user"]["username"] == "urvashi" and response.json()["csrf_token"]
    with session_scope() as session:
        assert session.scalar(select(User.last_login_at)) is not None


def test_the_session_token_is_stored_only_as_a_hash():
    add_user()
    client = logged_in()
    with session_scope() as session:
        stored = session.scalar(select(AuthSession.token_hash))
    assert stored != client.cookies.get("talent_session") and len(stored) == 64


@pytest.mark.parametrize("username,password", [("urvashi", "wrong"), ("nobody", PASSWORD)])
def test_wrong_login_gives_one_message(username, password):
    add_user()
    response = TestClient(app).post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401 and problem(response) == "invalid-credentials"


def test_a_deactivated_user_cannot_log_in():
    add_user(is_active=False)
    response = TestClient(app).post("/api/auth/login", json={"username": "urvashi", "password": PASSWORD})
    assert response.status_code == 401


def test_me_returns_the_user_and_csrf_token():
    add_user()
    client = logged_in()
    me = client.get("/api/me").json()
    assert me["username"] == "urvashi" and me["admin_active"] is False
    assert me["csrf_token"] == client.headers["X-CSRF-Token"]


def test_an_expired_session_is_refused_and_removed():
    add_user()
    client = logged_in()
    with session_scope() as session:
        session.execute(update(AuthSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))

    assert client.get("/api/me").status_code == 401
    with session_scope() as session:
        assert session.scalar(select(AuthSession)) is None


def test_logout_ends_the_session():
    add_user()
    client = logged_in()
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_writes_need_the_csrf_token():
    add_user()
    client = logged_in()
    del client.headers["X-CSRF-Token"]
    response = client.post("/api/auth/logout")
    assert response.status_code == 403 and problem(response) == "csrf-token-invalid"
    client.headers["X-CSRF-Token"] = "forged"
    assert client.post("/api/fetch", json={}).status_code == 403


def test_the_cookie_has_its_own_name_and_needs_https_when_secure(monkeypatch):
    assert session_cookie_name() == "talent_session"
    monkeypatch.setattr(settings, "cookie_secure", True)
    add_user()
    response = TestClient(app).post("/api/auth/login", json={"username": "urvashi", "password": PASSWORD})
    assert response.headers["set-cookie"].startswith("__Host-talent_session=")
    assert "Secure" in response.headers["set-cookie"]


# ---------- admin unlock ----------

def test_admin_routes_need_the_admin_password():
    add_user()
    set_admin_password()
    client = logged_in()

    response = client.get("/api/admin/users")
    assert response.status_code == 403 and problem(response) == "admin-session-required"
    assert client.get("/api/admin/session").json() == {"active": False, "expires_at": None,
                                                        "password_configured": True}


def test_the_admin_password_unlocks_admin_for_30_minutes():
    client = admin_client()

    status = client.get("/api/admin/session").json()
    until = datetime.fromisoformat(status["expires_at"])
    assert status["active"] and timedelta(minutes=29) < until - datetime.now(timezone.utc) <= timedelta(minutes=30)
    assert client.get("/api/me").json()["admin_active"] is True
    assert client.get("/api/admin/users").status_code == 200


def test_admin_unlock_belongs_to_one_session():
    admin_client()
    other = logged_in()
    assert other.get("/api/admin/users").status_code == 403


def test_admin_expires():
    client = admin_client()
    with session_scope() as session:
        session.execute(update(AuthSession).values(admin_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert client.get("/api/admin/users").status_code == 403


def test_locking_admin_ends_it():
    client = admin_client()
    assert client.delete("/api/admin/session").status_code == 204
    assert client.get("/api/admin/users").status_code == 403


def test_wrong_admin_passwords_lock_the_user_out_even_after_logging_in_again():
    add_user()
    set_admin_password()
    for _ in range(settings.admin_max_failed_attempts):
        response = logged_in().post("/api/admin/session", json={"admin_password": "wrong"})
        assert response.status_code == 401 and problem(response) == "admin-password-wrong"

    locked = logged_in().post("/api/admin/session", json={"admin_password": ADMIN_PASSWORD})
    assert locked.status_code == 429 and problem(locked) == "admin-locked"
    assert int(locked.headers["Retry-After"]) > 0


def test_a_correct_admin_password_resets_the_count():
    add_user()
    set_admin_password()
    client = logged_in()
    for _ in range(settings.admin_max_failed_attempts - 1):
        client.post("/api/admin/session", json={"admin_password": "wrong"})
    assert client.post("/api/admin/session", json={"admin_password": ADMIN_PASSWORD}).status_code == 204
    with session_scope() as session:
        assert session.scalar(select(User.admin_failed_attempts)) == 0


def test_unlock_explains_when_no_admin_password_is_set():
    add_user()
    response = logged_in().post("/api/admin/session", json={"admin_password": "anything"})
    assert response.status_code == 503 and problem(response) == "admin-password-not-set"


def test_changing_the_admin_password():
    client = admin_client()
    wrong = client.put("/api/admin/password", json={"current_password": "nope", "new_password": "brand-new"})
    assert wrong.status_code == 401

    short = client.put("/api/admin/password", json={"current_password": ADMIN_PASSWORD, "new_password": "x"})
    assert short.status_code == 422 and short.json()["errors"][0]["field"] == "new_password"

    assert client.put("/api/admin/password",
                      json={"current_password": ADMIN_PASSWORD, "new_password": "brand-new"}).status_code == 204
    assert logged_in().post("/api/admin/session", json={"admin_password": ADMIN_PASSWORD}).status_code == 401
    assert logged_in().post("/api/admin/session", json={"admin_password": "brand-new"}).status_code == 204


# ---------- users ----------

def test_the_admin_adds_a_user_who_can_then_log_in():
    client = admin_client()
    response = client.post("/api/admin/users",
                           json={"username": "priya", "display_name": "Priya", "initial_password": "secret1"})

    assert response.status_code == 201 and response.json()["is_active"] is True
    assert "password" not in str(response.json())
    assert logged_in("priya", "secret1").get("/api/me").json()["display_name"] == "Priya"
    names = [user["username"] for user in client.get("/api/admin/users").json()["items"]]
    assert names == ["priya", "urvashi"]


@pytest.mark.parametrize("body,field", [
    ({"username": "ab", "display_name": "A", "initial_password": "secret1"}, "username"),
    ({"username": "has space", "display_name": "A", "initial_password": "secret1"}, "username"),
    ({"username": "priya", "display_name": " ", "initial_password": "secret1"}, "display_name"),
    ({"username": "priya", "display_name": "A", "initial_password": "short"}, "initial_password"),
])
def test_new_users_are_checked(body, field):
    response = admin_client().post("/api/admin/users", json=body)
    assert response.status_code == 422 and response.json()["errors"][0]["field"] == field


def test_usernames_are_unique_ignoring_case():
    response = admin_client().post("/api/admin/users",
                                   json={"username": "URVASHI", "display_name": "U", "initial_password": "secret1"})
    assert response.status_code == 409 and problem(response) == "username-taken"


def test_deactivating_a_user_logs_them_out_everywhere():
    client = admin_client()
    priya_id = add_user("priya")
    priya = logged_in("priya")

    assert client.patch(f"/api/admin/users/{priya_id}", json={"is_active": False}).json()["is_active"] is False
    with session_scope() as session:
        assert session.scalar(select(AuthSession).where(AuthSession.user_id == priya_id)) is None
    assert priya.get("/api/me").status_code == 401
    assert client.patch(f"/api/admin/users/{priya_id}", json={"is_active": True}).status_code == 200
    assert logged_in("priya").get("/api/me").status_code == 200


def test_the_admin_cannot_deactivate_themselves():
    client = admin_client()
    my_id = client.get("/api/me").json()["id"]
    response = client.patch(f"/api/admin/users/{my_id}", json={"is_active": False})
    assert response.status_code == 409 and problem(response) == "cannot-deactivate-self"


def test_resetting_a_password_logs_the_user_out_and_only_the_new_one_works():
    client = admin_client()
    priya_id = add_user("priya")
    priya = logged_in("priya")

    assert client.put(f"/api/admin/users/{priya_id}/password", json={"new_password": "fresh-pass"}).status_code == 204
    assert priya.get("/api/me").status_code == 401
    old = TestClient(app).post("/api/auth/login", json={"username": "priya", "password": PASSWORD})
    assert old.status_code == 401
    logged_in("priya", "fresh-pass")


def test_a_user_changes_their_own_password_and_other_browsers_are_logged_out():
    add_user()
    browser = logged_in()
    other_browser = logged_in()

    response = browser.put("/api/me/password", json={"current_password": PASSWORD, "new_password": "my-new-pass"})

    assert response.status_code == 204
    assert browser.get("/api/me").status_code == 200
    assert other_browser.get("/api/me").status_code == 401
    old = TestClient(app).post("/api/auth/login", json={"username": "urvashi", "password": PASSWORD})
    assert old.status_code == 401
    logged_in("urvashi", "my-new-pass")


def test_changing_your_own_password_checks_the_current_one_and_length():
    add_user()
    client = logged_in()
    wrong = client.put("/api/me/password", json={"current_password": "nope", "new_password": "my-new-pass"})
    assert wrong.status_code == 401 and problem(wrong) == "password-wrong"
    short = client.put("/api/me/password", json={"current_password": PASSWORD, "new_password": "x"})
    assert short.status_code == 422 and short.json()["errors"][0]["field"] == "new_password"
    logged_in("urvashi", PASSWORD)


def test_changing_your_own_password_needs_no_admin_unlock():
    add_user()
    set_admin_password()
    assert logged_in().put("/api/me/password",
                           json={"current_password": PASSWORD, "new_password": "my-new-pass"}).status_code == 204


def test_unknown_user_is_a_404():
    assert admin_client().put("/api/admin/users/999/password", json={"new_password": "fresh-pass"}).status_code == 404


# ---------- the CRM frontend calling from another port ----------

def test_cors_allows_the_crm_frontend_with_credentials():
    response = TestClient(app).options("/api/me", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cors_refuses_other_origins():
    response = TestClient(app).get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


# ---------- setup ----------

def test_bootstrap_creates_the_first_user_and_admin_password(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_USERNAME", "Urvashi")
    monkeypatch.setenv("BOOTSTRAP_DISPLAY_NAME", "Urvashi")
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", PASSWORD)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)

    assert bootstrap_auth.run() == ["created user Urvashi", "set the admin password"]
    assert bootstrap_auth.run() == ["users already exist, none created", "admin password already set, unchanged"]

    client = logged_in("urvashi", PASSWORD)
    assert client.post("/api/admin/session", json={"admin_password": PASSWORD}).status_code == 204


def test_bootstrap_can_reset_a_forgotten_admin_password(monkeypatch):
    add_user()
    set_admin_password()
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "replacement")

    assert bootstrap_auth.run(reset_admin_password=True)[1] == "set the admin password"
    with session_scope() as session:
        assert session.scalar(select(AppSettings.admin_password_hash))
    assert logged_in().post("/api/admin/session", json={"admin_password": "replacement"}).status_code == 204


def test_bootstrap_refuses_a_short_password(monkeypatch):
    monkeypatch.setenv("BOOTSTRAP_USERNAME", "urvashi")
    monkeypatch.setenv("BOOTSTRAP_DISPLAY_NAME", "U")
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", "x")
    with pytest.raises(SystemExit):
        bootstrap_auth.run()
    with session_scope() as session:
        assert session.scalar(select(User)) is None


def test_no_database_gives_a_clear_503(monkeypatch):
    from app.db.session import get_engine
    get_engine.cache_clear()
    monkeypatch.setattr(settings, "database_url", "")
    try:
        response = TestClient(app).post("/api/auth/login", json={"username": "u", "password": "p"})
        assert response.status_code == 503 and problem(response) == "database-not-configured"
    finally:
        get_engine.cache_clear()
