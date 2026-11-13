"""
The mailboxes CVs are fetched from. Managed by the Talent Pool admin; any user picks one for a fetch.

App passwords are encrypted with MAILBOX_SECRET_KEY (created in .env the first time it's needed).
Losing that key means re-entering the passwords, so it's never shown or changed from the screens.
The mailbox in .env is copied into this list once, so existing setups keep working.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import date
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.problems import ProblemError
from app.core.config import settings
from app.core.logger import get_logger
from app.core.state import JsonState
from app.db.models import AppSettings, Mailbox
from app.fetcher.mail_fetcher import MailSource, mailbox_state_key

log = get_logger("mailboxes")

KEY_NAME = "MAILBOX_SECRET_KEY"
DEFAULT_HOST = "imap.zoho.in"
TIMEOUT_SECONDS = 20
_key_lock = threading.Lock()


# ---------- encryption ----------

def _fernet() -> Fernet:
    with _key_lock:
        key = os.environ.get(KEY_NAME, "").strip()
        if not key:
            from app.talent.settings_admin import _write
            key = Fernet.generate_key().decode()
            _write({KEY_NAME: key})
            os.environ[KEY_NAME] = key
            log.info("Created %s in .env to encrypt mailbox passwords", KEY_NAME)
        return Fernet(key.encode())


def _encrypt(password: str) -> str:
    return _fernet().encrypt(password.encode()).decode()


def _decrypt(mailbox: Mailbox) -> str:
    try:
        return _fernet().decrypt(mailbox.password_encrypted.encode()).decode()
    except InvalidToken:
        raise ProblemError(409, "mailbox-password-unreadable", f"The password for {mailbox.name} can't be read",
                           f"{KEY_NAME} in .env has changed. Edit the mailbox and enter its app password again.")


# ---------- listing ----------

def _last_fetches(mailbox: Mailbox) -> dict:
    """When each folder of this mailbox was last fetched (from the fetcher's own state)."""
    state = JsonState(settings.state_folder / "mail_state.json").data
    prefix = mailbox_state_key(mailbox.email, mailbox.host, "")
    return {key[len(prefix):]: value.get("last_run") for key, value in state.items()
            if key.startswith(prefix) and value.get("last_run")}


def describe(mailbox: Mailbox) -> dict:
    """A mailbox for the screens. Never the password."""
    return {"id": mailbox.id, "name": mailbox.name, "email": mailbox.email, "host": mailbox.host,
            "port": mailbox.port, "default_folder": mailbox.default_folder, "last_fetch": _last_fetches(mailbox),
            "created_at": mailbox.created_at, "updated_at": mailbox.updated_at}


def list_mailboxes(session: Session) -> list[dict]:
    return [describe(m) for m in session.scalars(select(Mailbox).order_by(func.lower(Mailbox.name)))]


def get(session: Session, mailbox_id: int) -> Mailbox:
    mailbox = session.get(Mailbox, mailbox_id)
    if mailbox is None:
        raise ProblemError(404, "mailbox-not-found", "Mailbox not found", "It may have been removed.")
    return mailbox


# ---------- changes (Talent Pool admin) ----------

def _invalid(field: str, message: str) -> ProblemError:
    return ProblemError(422, "validation-error", "Some fields are invalid", "Fix the listed fields and try again.",
                        errors=[{"field": field, "message": message}])


def _clean(values: dict, *, creating: bool) -> dict:
    out = {}
    for key in ("name", "email", "host", "default_folder"):
        if key in values and values[key] is not None:
            out[key] = str(values[key]).strip()
    if "email" in out and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", out["email"]):
        raise _invalid("email", "Enter the mailbox's email address.")
    if "name" in out and not out["name"]:
        raise _invalid("name", "Give the mailbox a name.")
    if "host" in out and not re.fullmatch(r"[A-Za-z0-9.-]+", out["host"] or ""):
        raise _invalid("host", "Enter the mail server, e.g. imap.zoho.in")
    if "default_folder" in out and not out["default_folder"]:
        raise _invalid("default_folder", "Enter a folder, e.g. INBOX")
    if values.get("port") not in (None, ""):
        try:
            port = int(values["port"])
        except (TypeError, ValueError):
            raise _invalid("port", "Enter a port number, e.g. 993")
        if not 1 <= port <= 65535:
            raise _invalid("port", "Enter a port number, e.g. 993")
        out["port"] = port
    password = (values.get("password") or "").strip()
    if creating and not password:
        raise _invalid("password", "Enter the mailbox's app password.")
    if password:
        out["password_encrypted"] = _encrypt(password)
    return out


def _taken(email: str) -> ProblemError:
    return ProblemError(409, "mailbox-exists", "That mailbox is already in the list", f"{email} is already connected.")


def create(session: Session, values: dict) -> Mailbox:
    cleaned = _clean(values, creating=True)
    cleaned.setdefault("host", DEFAULT_HOST)
    cleaned.setdefault("name", cleaned["email"])
    mailbox = Mailbox(**cleaned)
    session.add(mailbox)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise _taken(cleaned["email"])
    return mailbox


def update(session: Session, mailbox_id: int, values: dict) -> Mailbox:
    mailbox = get(session, mailbox_id)
    for key, value in _clean(values, creating=False).items():   # a blank password keeps the current one
        setattr(mailbox, key, value)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise _taken(values.get("email", ""))
    return mailbox


def remove(session: Session, mailbox_id: int) -> None:
    """Only the connection goes. Its candidates, CVs and history stay."""
    session.delete(get(session, mailbox_id))


# ---------- connecting ----------

def _login(mailbox: Mailbox):
    from imap_tools import MailBox
    return MailBox(mailbox.host, port=mailbox.port, timeout=TIMEOUT_SECONDS).login(mailbox.email, _decrypt(mailbox))


def folders(session: Session, mailbox_id: int) -> list[str]:
    mailbox = get(session, mailbox_id)
    try:
        with _login(mailbox) as connection:
            return [f.name for f in connection.folder.list()]
    except ProblemError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ProblemError(502, "mailbox-unreachable", f"Couldn't open {mailbox.name}", str(e)[:300])


def test(session: Session, mailbox_id: int) -> dict:
    mailbox = get(session, mailbox_id)
    try:
        with _login(mailbox) as connection:
            names = [f.name for f in connection.folder.list()]
    except ProblemError as e:
        return {"ok": False, "message": e.detail or e.title}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {str(e)[:300]}"}
    if mailbox.default_folder not in names:
        return {"ok": False, "message": f"Logged in, but there's no folder called \"{mailbox.default_folder}\".",
                "folders": names[:100]}
    return {"ok": True, "message": f"Logged in as {mailbox.email}. {len(names)} folders.", "folders": names[:100]}


def source_for(session: Session, mailbox_id: int, folder: Optional[str], since: Optional[date],
               until: Optional[date]) -> tuple[MailSource, dict]:
    """What a fetch reads, plus a description of it for the run's summary."""
    mailbox = get(session, mailbox_id)
    folder = (folder or "").strip() or mailbox.default_folder
    source = MailSource(mailbox.host, mailbox.port, mailbox.email, _decrypt(mailbox), folder,
                        since.isoformat() if since else "", until.isoformat() if until else "")
    about = {"mailbox_id": mailbox.id, "mailbox": mailbox.name, "mailbox_email": mailbox.email, "folder": folder,
             "mode": "range" if source.date_range else "new",
             "since": source.since or None, "until": source.until or None}
    return source, about


# ---------- the .env mailbox, copied in once ----------

def import_env_mailbox() -> Optional[int]:
    """Copy the .env mailbox into the list the first time this runs. Never again after that."""
    if not settings.database_url:
        return None
    from app.db.session import session_scope
    try:
        with session_scope() as session:
            row = session.scalar(select(AppSettings).where(AppSettings.id.is_(True)).with_for_update())
            if row is None:
                row = AppSettings(id=True)
                session.add(row)
            if row.mailboxes_imported:
                return None
            row.mailboxes_imported = True
            if not (settings.imap_user and settings.imap_password):
                return None
            exists = session.scalar(select(Mailbox.id).where(func.lower(Mailbox.email) == settings.imap_user.lower()))
            if exists:
                return None
            mailbox = Mailbox(name=settings.imap_user, email=settings.imap_user, host=settings.imap_host,
                              port=settings.imap_port, default_folder=settings.imap_folder or "INBOX",
                              password_encrypted=_encrypt(settings.imap_password))
            session.add(mailbox)
            session.flush()
            log.info("Copied the .env mailbox %s into the mailbox list", settings.imap_user)
            return mailbox.id
    except Exception:  # noqa: BLE001 - must never stop the server starting
        log.exception("Could not copy the .env mailbox into the mailbox list")
        return None
