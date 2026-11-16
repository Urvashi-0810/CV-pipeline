"""Error responses in the CRM's format (application/problem+json), so the frontend handles both alike.

Only the login and admin routes use these. The older pipeline routes keep their {"detail": ...} bodies.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db.session import DatabaseNotConfigured

PROBLEM_PREFIX = "urn:talent:problem:"


class ProblemError(Exception):
    def __init__(self, status: int, slug: str, title: str, detail: str | None = None,
                 headers: dict[str, str] | None = None, **extensions: Any) -> None:
        super().__init__(detail or title)
        self.status, self.slug, self.title, self.detail = status, slug, title, detail
        self.headers = headers
        self.extensions = extensions


def problem_response(status: int, slug: str, title: str, detail: str | None = None,
                     headers: dict[str, str] | None = None, **extensions: Any) -> JSONResponse:
    body = {"type": PROBLEM_PREFIX + slug, "title": title, "status": status}
    if detail:
        body["detail"] = detail
    return JSONResponse(body | extensions, status_code=status, headers=headers,
                        media_type="application/problem+json")


def register_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(_: Request, e: ProblemError):
        return problem_response(e.status, e.slug, e.title, e.detail, e.headers, **e.extensions)

    @app.exception_handler(DatabaseNotConfigured)
    async def _no_database(_: Request, e: DatabaseNotConfigured):
        return problem_response(503, "database-not-configured", "The talent database is not set up",
                                "Set DATABASE_URL in the backend .env (see README).")
