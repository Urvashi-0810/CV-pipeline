"""Login, admin unlock and user management. Ported from the CRM's auth, admin and users services."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.problems import ProblemError
from app.auth.security import hash_password, hash_token, new_token, password_needs_rehash, verify_password
from app.core.config import settings
from app.db.models import AppSettings, AuthSession, User

USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{3,50}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _invalid(field: str, message: str) -> ProblemError:
    return ProblemError(422, "validation-error", "Some fields are invalid", "Fix the listed fields and try again.",
                        errors=[{"field": field, "message": message}])


def check_password_strength(password: str, field: str = "password") -> None:
    if len(password) < settings.password_min_length:
        raise _invalid(field, f"Must be at least {settings.password_min_length} characters.")


def check_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise _invalid("username", "3-50 characters: letters, digits, dot, underscore or hyphen.")
    return username


# ---------- login ----------

def login(db: Session, username: str, password: str) -> tuple[User, str, str]:
    """Check credentials and start a session. Returns (user, session token, CSRF token)."""
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))

    # Always check a hash, even for unknown users, so the timing reveals nothing.
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        raise ProblemError(401, "invalid-credentials", "Wrong username or password",
                           "Check your username and password. Ask the Talent Pool admin if your account was deactivated.")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    now = _now()
    token, csrf = new_token(), new_token()
    db.add(AuthSession(token_hash=hash_token(token), user_id=user.id, csrf_token=csrf,
                       expires_at=now + timedelta(hours=settings.session_ttl_hours)))
    user.last_login_at = now
    # Housekeeping: drop this user's expired sessions.
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.expires_at <= now))
    db.commit()
    return user, token, csrf


def logout(db: Session, session: AuthSession) -> None:
    db.delete(session)
    db.commit()


# ---------- admin unlock ----------

def get_app_settings(db: Session, *, for_update: bool = False) -> AppSettings | None:
    query = select(AppSettings).where(AppSettings.id.is_(True))
    return db.scalar(query.with_for_update() if for_update else query)


def admin_password_configured(db: Session) -> bool:
    row = get_app_settings(db)
    return bool(row and row.admin_password_hash)


def unlock_admin(db: Session, session: AuthSession, user: User, password: str) -> datetime:
    """Check the admin password and unlock admin for this login session."""
    now = _now()
    if user.admin_locked_until and user.admin_locked_until > now:
        seconds = math.ceil((user.admin_locked_until - now).total_seconds())
        raise ProblemError(429, "admin-locked", "Too many wrong admin passwords",
                           f"Try again in {math.ceil(seconds / 60)} minute(s).", headers={"Retry-After": str(seconds)})

    app_settings = get_app_settings(db)
    if not app_settings or not app_settings.admin_password_hash:
        raise ProblemError(503, "admin-password-not-set", "The Talent Pool admin password has not been set up",
                           "Run bootstrap_auth.py on the server to set it.")

    if not verify_password(app_settings.admin_password_hash, password):
        # Counted per user, not per session, so logging in again doesn't reset the count.
        user.admin_failed_attempts += 1
        if user.admin_failed_attempts >= settings.admin_max_failed_attempts:
            user.admin_locked_until = now + timedelta(minutes=settings.admin_lockout_minutes)
            user.admin_failed_attempts = 0
        db.commit()
        raise ProblemError(401, "admin-password-wrong", "Wrong admin password")

    user.admin_failed_attempts = 0
    user.admin_locked_until = None
    session.admin_until = now + timedelta(minutes=settings.admin_session_minutes)
    db.commit()
    return session.admin_until


def lock_admin(db: Session, session: AuthSession) -> None:
    session.admin_until = None
    db.commit()


def change_admin_password(db: Session, *, user_id: int, current: str, new: str) -> None:
    app_settings = get_app_settings(db, for_update=True)
    if not app_settings or not verify_password(app_settings.admin_password_hash, current):
        raise ProblemError(401, "admin-password-wrong", "Current admin password is wrong")
    check_password_strength(new, "new_password")
    app_settings.admin_password_hash = hash_password(new)
    app_settings.updated_by = user_id
    db.commit()


def set_admin_password(db: Session, password: str) -> None:
    """For the bootstrap script: no current password needed."""
    app_settings = get_app_settings(db, for_update=True)
    if app_settings is None:
        app_settings = AppSettings(id=True)
        db.add(app_settings)
    app_settings.admin_password_hash = hash_password(password)
    db.commit()


# ---------- users ----------

def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(func.lower(User.display_name))))


def create_user(db: Session, *, username: str, display_name: str, password: str) -> User:
    username = check_username(username)
    display_name = display_name.strip()
    if not display_name or len(display_name) > 100:
        raise _invalid("display_name", "Enter a name of up to 100 characters.")
    check_password_strength(password, "initial_password")
    taken = ProblemError(409, "username-taken", "Username already in use",
                         f'Choose a different username than "{username}".')
    if db.scalar(select(User.id).where(func.lower(User.username) == username.lower())):
        raise taken
    user = User(username=username, display_name=display_name, password_hash=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # Two requests at the same moment: the unique index stops the second.
        db.rollback()
        raise taken
    return user


def get_user_or_404(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ProblemError(404, "user-not-found", "User not found")
    return user


def set_user_active(db: Session, *, acting_user_id: int, user_id: int, is_active: bool) -> User:
    user = get_user_or_404(db, user_id)
    if not is_active and user.id == acting_user_id:
        raise ProblemError(409, "cannot-deactivate-self", "You cannot deactivate your own account",
                           "Ask another user with the admin password to do it.")
    if is_active != user.is_active:
        user.is_active = is_active
        if not is_active:
            # Logged out everywhere, immediately.
            db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return user


def change_own_password(db: Session, *, user: User, current_session_id: int, current_password: str,
                        new_password: str) -> None:
    """A user changes their own password. Their other browsers are logged out; this one stays in."""
    if not verify_password(user.password_hash, current_password):
        raise ProblemError(401, "password-wrong", "Your current password is wrong")
    check_password_strength(new_password, "new_password")
    user.password_hash = hash_password(new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.id != current_session_id))
    db.commit()


def reset_password(db: Session, *, user_id: int, new_password: str) -> User:
    """The admin sets a new password. The user's sessions end, so only the new password works."""
    user = get_user_or_404(db, user_id)
    check_password_strength(new_password, "new_password")
    user.password_hash = hash_password(new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.commit()
    return user
