"""
FastAPI application - the HTTP layer the React frontend will talk to.

Run:   python main.py serve          (or: uvicorn app.api.main:app --reload)
Docs:  http://localhost:8000/docs    (interactive - you can try every endpoint there)

The API contains no business logic: every endpoint just calls the same functions
the CLI uses (fetch_cvs, run_extraction, run_shortlist, run_jobfit ...).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_jobs import router as jobs_router
from app.api.routes_pipeline import router as pipeline_router
from app.core.config import settings

@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_dirs()          # make sure data folders exist before serving
    yield


app = FastAPI(
    lifespan=lifespan,
    title="CV Pipeline API",
    version="1.0",
    description="Fetch CVs from mail → extract with LLM → shortlist → JD job fit",
)

# React dev servers (Vite :5173, CRA :3000). Override with CORS_ORIGINS=comma,separated
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins if o.strip()],
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(pipeline_router)
app.include_router(jobs_router)

