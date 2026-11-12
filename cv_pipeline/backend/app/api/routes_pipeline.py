"""Endpoints for Steps 1-2: fetch mail, extract CVs, browse candidates, background tasks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.schemas import ExtractRequest, FetchRequest, RunRequest
from app.api.tasks import LaneBusy, Task, TaskNotControllable, tasks
from app.core.config import CV_EXTENSIONS, settings
from app.core.state import JsonState, manifest_entry
from app.storage.excel_store import load_candidates
from app.talent.runs import recorded
from app.storage.s3_store import (
    URL_EXPIRY_SECONDS,
    S3NotConfigured,
    key_for,
    presigned_cv_url,
    relative_cv_path,
)

router = APIRouter(prefix="/api", tags=["pipeline"])


def df_records(df) -> list[dict]:
    """DataFrame -> JSON-safe list of dicts (NaN -> null)."""
    return json.loads(df.to_json(orient="records", date_format="iso")) if len(df) else []


def _submit(kind: str, lane: str, fn, params: dict, controllable: bool = False) -> dict:
    try:
        return tasks.submit(kind, lane, fn, params, controllable=controllable).to_dict()
    except LaneBusy as e:
        raise HTTPException(409, {"message": str(e), "task": e.task.to_dict()})


# ------------------------------------------------------------------ info
@router.get("/config")
def config():
    """Non-secret settings, so the UI can show what is configured."""
    return {
        "llm_provider": settings.llm_provider,
        "providers_configured": {"claude": bool(settings.anthropic_api_key), "gemini": bool(settings.gemini_api_key)},
        "claude_model": settings.claude_model,
        "gemini_model": settings.gemini_model,
        "imap": {"host": settings.imap_host, "user": settings.imap_user, "folder": settings.imap_folder,
                 "configured": bool(settings.imap_user and settings.imap_password)},
        "s3": {"bucket": settings.aws_s3_bucket, "region": settings.aws_region,
               "configured": bool(settings.aws_s3_bucket)},
        "database": {"configured": bool(settings.database_url)},
    }


@router.get("/status")
def status():
    from app.extractor.cv_extractor import list_cv_files
    settings.ensure_dirs()
    mail = JsonState(settings.state_folder / "mail_state.json").data
    proc = JsonState(settings.state_folder / "processed_files.json").data
    failed = [{"file": v.get("file"), "error": v.get("error"), "at": v.get("at")}
              for v in proc.values() if v.get("status") == "error"]
    status = {
        "mail": mail,
        "cv_files": len(list_cv_files(settings.cv_folder)),
        "extracted_ok": sum(1 for v in proc.values() if v.get("status") == "ok"),
        "failed": failed,
        "candidates_in_excel": len(load_candidates()),
        "talent": None,
    }
    if settings.database_url:
        # Candidates in Postgres, CVs, flags waiting for review, S3 uploads failed or missing
        from app.db.session import session_scope
        from app.talent.service import counts
        with session_scope() as session:
            status["talent"] = counts(session)
    return status


# ------------------------------------------------------------------ steps 1-2 (background)
@router.post("/fetch")
def fetch(body: FetchRequest = FetchRequest()):
    """Fetch new mail; each batch's CVs are extracted as it goes. Progress counts mails."""
    from app.fetcher.mail_fetcher import fetch_cvs
    return _submit("fetch", "pipeline",
                   recorded("fetch", lambda p: fetch_cvs(reset=body.reset, progress=p).as_dict()), body.model_dump(),
                   controllable=True)


@router.post("/extract")
def extract(body: ExtractRequest = ExtractRequest()):
    from app.extractor.cv_extractor import run_extraction
    return _submit("extract", "pipeline",
                   recorded("extract", lambda p: run_extraction(provider=body.provider, force=body.force,
                                                                limit=body.limit, progress=p).as_dict()),
                   body.model_dump(), controllable=True)


@router.post("/run")
def run_all(body: RunRequest = RunRequest()):
    from app.extractor.cv_extractor import run_extraction
    from app.fetcher.mail_fetcher import fetch_cvs

    def job(p):
        f = fetch_cvs(reset=body.reset, progress=p).as_dict()
        if f["cancelled"]:
            return {"fetch": f, "extract": None, "cancelled": True}
        # Then any CV files on disk not extracted yet (e.g. ones that failed before)
        e = run_extraction(provider=body.provider, force=body.force, limit=body.limit, progress=p).as_dict()
        return {"fetch": f, "extract": e}
    return _submit("run", "pipeline", recorded("run", job), body.model_dump(), controllable=True)


@router.get("/pipeline/runs")
def pipeline_runs(limit: int = Query(10, ge=1, le=50)):
    """Recent runs, newest first, with their summaries. Empty without the talent database."""
    from app.talent.runs import recent_runs
    return {"items": recent_runs(limit) if settings.database_url else []}


@router.get("/tasks")
def list_tasks():
    return [t.to_dict() for t in tasks.list()]


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    t = tasks.get(task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    return t.to_dict()


@router.post("/tasks/{task_id}/{action}")
def control_task(task_id: str, action: Literal["pause", "resume", "cancel"]):
    """Pause, resume or cancel a fetch, extract, run or S3 retry.

    They take effect at the next safe point: after the current CV, or between batches of mail.
    """
    try:
        return getattr(tasks, action)(task_id).to_dict()
    except KeyError:
        raise HTTPException(404, "Task not found")
    except TaskNotControllable as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------------------------ candidates & CV files
@router.get("/candidates")
def candidates(search: str = Query("", description="filter on any column"), limit: int = 1000):
    df = load_candidates()
    if search:
        mask = df.astype(str).apply(lambda col: col.str.contains(search, case=False, na=False)).any(axis=1)
        df = df[mask]
    return {"total": len(df), "rows": df_records(df.head(limit))}


@router.get("/candidates/download")
def download_candidates():
    if not settings.output_excel.exists():
        raise HTTPException(404, "No Excel yet - run extraction first")
    return FileResponse(settings.output_excel, filename=settings.output_excel.name)


def _cv_s3_key(file_hash: str) -> str:
    """Work out a CV's S3 key from the local records, without touching the disk or S3."""
    processed = JsonState(settings.state_folder / "processed_files.json").get(file_hash) or {}
    by_hash = JsonState(settings.state_folder / "attachments_manifest.json").get("by_hash", {})
    if processed.get("status") != "ok":
        raise HTTPException(404, "CV not found")
    if processed.get("s3_status") == "Failed":
        raise HTTPException(409, "This CV's S3 upload failed, so it can't be opened yet")
    recorded = processed.get("path") or manifest_entry(by_hash, file_hash).get("file")
    relative_path = relative_cv_path(recorded) if recorded else None
    if not relative_path:
        raise HTTPException(404, "No S3 location is recorded for this CV")
    return key_for(relative_path)


@router.get("/cvs/{file_hash}/url")
def get_cv_url(file_hash: str, download: bool = False):
    """A short-lived S3 link to a CV: opens inline, or downloads with ?download=true."""
    try:
        url = presigned_cv_url(_cv_s3_key(file_hash), download=download)
    except S3NotConfigured as e:
        raise HTTPException(503, str(e))
    return {"url": url, "expires_in": URL_EXPIRY_SECONDS}


def resume_pending_uploads() -> list[Task]:
    """Queue uploads left waiting by a restart. Called once at startup."""
    from app.fetcher.manual_upload import pending_uploads, process_upload
    return [
        tasks.submit("upload", "pipeline", lambda p, f=folder: process_upload(f, p),
                     {"resumed": True}, queue=True)
        for folder in pending_uploads()
    ]


@router.post("/cvs/upload")
async def upload_cv(files: list[UploadFile] = File(...)):
    """Add one person's CV. Several files are merged into one PDF, then fully processed.

    Returns a task to poll. If a run is active, the upload waits for it to finish.
    """
    from app.fetcher.manual_upload import process_upload, stage_upload

    settings.ensure_dirs()
    limit = int(settings.max_attachment_mb * 1024 * 1024)
    accepted, skipped = [], []
    for f in files:
        name = Path(f.filename or "upload").name
        if Path(name).suffix.lower() not in CV_EXTENSIONS:
            skipped.append(f.filename)
            continue
        payload = await f.read()
        if len(payload) > limit:
            raise HTTPException(413, f"{name} is larger than {settings.max_attachment_mb:g} MB")
        if not payload:
            raise HTTPException(400, f"{name} is empty")
        accepted.append((name, payload))
    if not accepted:
        raise HTTPException(400, "No supported CV files in this upload")

    folder = stage_upload(accepted)
    task = tasks.submit("upload", "pipeline", lambda p: process_upload(folder, p),
                        {"files": [name for name, _ in accepted]}, queue=True)
    return {**task.to_dict(), "skipped_unsupported": skipped}
