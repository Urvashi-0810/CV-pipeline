"""FastAPI dependencies: the current login, the CSRF check and the admin unlock. Ported from the CRM's app/deps.py."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.problems import ProblemError
from app.auth.security import hash_token, session_cookie_name
from app.db.models import AuthSession, User
from app.db.session import get_db

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DbSession = Annotated[Session, Depends(get_db)]


@dataclass
class CurrentLogin:
    session: AuthSession
    user: User

    @property
    def admin_active(self) -> bool:
        until = self.session.admin_until
        return until is not None and until > datetime.now(timezone.utc)


def _not_logged_in() -> ProblemError:
    return ProblemError(401, "not-authenticated", "Not logged in", "Please log in to the Talent Pool again.")


def get_current_login(request: Request, db: DbSession) -> CurrentLogin:
    token = request.cookies.get(session_cookie_name())
    if not token:
        raise _not_logged_in()

    row = db.execute(
        select(AuthSession, User).join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_token(token))
    ).one_or_none()
    if row is None:
        raise _not_logged_in()

    login_session, user = row
    if login_session.expires_at <= datetime.now(timezone.utc) or not user.is_active:
        db.delete(login_session)
        db.commit()
        raise _not_logged_in()

    # CSRF protection for anything that changes data.
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(sent, login_session.csrf_token):
            raise ProblemError(403, "csrf-token-invalid", "Security token missing or invalid",
                               "Reload the page and try again.")

    return CurrentLogin(session=login_session, user=user)


CurrentUser = Annotated[CurrentLogin, Depends(get_current_login)]


def require_login(current: CurrentUser) -> CurrentLogin:
    """Added to every router, so no route can be left open by accident."""
    return current


def require_admin(current: CurrentUser) -> CurrentLogin:
    if not current.admin_active:
        raise ProblemError(403, "admin-session-required", "Talent Pool admin access required",
                           "Enter the Talent Pool admin password to continue.")
    return current


AdminUser = Annotated[CurrentLogin, Depends(require_admin)]
