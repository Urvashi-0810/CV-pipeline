"""
FastAPI application - the HTTP layer the React frontend talks to.

Run:   python main.py serve          (or: uvicorn app.api.main:app --port 8100 --reload)
Docs:  http://localhost:8100/docs    (log in first with POST /api/auth/login to try the other endpoints)

Every route needs a Talent Pool login except logging in itself and /api/health.
The API contains no business logic: endpoints call the same functions the CLI uses.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.problems import register_problem_handlers
from app.api.routes_auth import router as auth_router
from app.api.routes_jobs import router as jobs_router
from app.api.routes_pipeline import resume_pending_uploads
from app.api.routes_pipeline import router as pipeline_router
from app.api.routes_settings import router as settings_router
from app.api.routes_talent import router as talent_router
from app.auth.deps import require_login
from app.core.config import settings
from app.talent.runs import close_interrupted_runs

# Routes anyone can reach. Everything else is behind require_login (checked by tests/test_auth.py).
PUBLIC_PATHS = {"/api/health", "/api/auth/login"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_dirs()          # make sure data folders exist before serving
    close_interrupted_runs()        # runs cut off by a restart show as stopped, not running forever
    resume_pending_uploads()        # uploads that were waiting when the server stopped
    yield


app = FastAPI(
    lifespan=lifespan,
    title="CV Pipeline API",
    version="1.0",
    description="Talent Pool: fetch CVs from mail → extract with LLM → candidates",
)
register_problem_handlers(app)

# The CRM frontend calls this API directly from the browser, sending the login cookie.
# Credentials need exact origins: set CORS_ORIGINS to every address the CRM is opened from.
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition", "ETag"])


@app.get("/api/health", tags=["pipeline"])
def health():
    return {"ok": True}


logged_in = [Depends(require_login)]
app.include_router(auth_router)                       # each route checks the login itself, except login
app.include_router(pipeline_router, dependencies=logged_in)
app.include_router(jobs_router, dependencies=logged_in)
app.include_router(talent_router, dependencies=logged_in)
app.include_router(settings_router, dependencies=logged_in)     # also needs Talent Pool admin
