"""Password hashing, session tokens and the session cookie. Ported from the CRM's app/security.py."""
from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Response

from app.core.config import settings

_hasher = PasswordHasher()

# A real hash of a random value, so an unknown username takes as long to check as a known one.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_cookie_name() -> str:
    # Its own name: cookies aren't separated by port, so it must not collide with crm_session.
    # The __Host- prefix needs HTTPS, so it's only used when the cookie is Secure.
    return "__Host-talent_session" if settings.cookie_secure else "talent_session"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=session_cookie_name(), value=token, max_age=settings.session_ttl_hours * 3600,
        path="/", secure=settings.cookie_secure, httponly=True, samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=session_cookie_name(), path="/", secure=settings.cookie_secure, httponly=True, samesite="lax",
    )
