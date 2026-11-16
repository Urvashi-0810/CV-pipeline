"""Connection to the talent database. Connects on first use, so code that needs no database runs without one."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


class DatabaseNotConfigured(RuntimeError):
    """DATABASE_URL is missing from .env."""


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    if not settings.database_url:
        raise DatabaseNotConfigured("DATABASE_URL is not configured")
    return create_engine(settings.database_url, pool_pre_ping=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request. Routes commit explicitly."""
    session = sessionmaker(get_engine(), expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commits on success, rolls back on any error."""
    with sessionmaker(get_engine(), expire_on_commit=False).begin() as session:
        yield session
