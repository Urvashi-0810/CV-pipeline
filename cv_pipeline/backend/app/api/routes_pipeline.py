"""Endpoints for Steps 1-2: fetch mail, extract CVs, browse candidates, background tasks."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.api.schemas import ExtractRequest, FetchRequest, RunRequest
from app.api.tasks import LaneBusy, tasks
from app.core.config import CV_EXTENSIONS, settings
from app.core.state import JsonState
from app.storage.excel_store import load_candidates

router = APIRouter(prefix="/api", tags=["pipeline"])


def df_records(df) -> list[dict]:
    """DataFrame -> JSON-safe list of dicts (NaN -> null)."""
    return json.loads(df.to_json(orient="records", date_format="iso")) if len(df) else []


def _submit(kind: str, lane: str, fn, params: dict) -> dict:
    try:
        return tasks.submit(kind, lane, fn, params).to_dict()
    except LaneBusy as e:
        raise HTTPException(409, {"message": str(e), "task": e.task.to_dict()})


# ------------------------------------------------------------------ info
@router.get("/health")
def health():
    return {"ok": True}


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
    }


@router.get("/status")
def status():
    from app.extractor.cv_extractor import list_cv_files
    settings.ensure_dirs()
    mail = JsonState(settings.state_folder / "mail_state.json").data
    proc = JsonState(settings.state_folder / "processed_files.json").data
    failed = [{"file": v.get("file"), "error": v.get("error"), "at": v.get("at")}
              for v in proc.values() if v.get("status") == "error"]
    return {
        "mail": mail,
        "cv_files": len(list_cv_files(settings.cv_folder)),
        "extracted_ok": sum(1 for v in proc.values() if v.get("status") == "ok"),
        "failed": failed,
        "candidates_in_excel": len(load_candidates()),
    }


# ------------------------------------------------------------------ steps 1-2 (background)
@router.post("/fetch")
def fetch(body: FetchRequest = FetchRequest()):
    from app.fetcher.mail_fetcher import fetch_cvs
    return _submit("fetch", "pipeline", lambda p: fetch_cvs(reset=body.reset).as_dict(), body.model_dump())


@router.post("/extract")
def extract(body: ExtractRequest = ExtractRequest()):
    from app.extractor.cv_extractor import run_extraction
    return _submit("extract", "pipeline",
                   lambda p: run_extraction(provider=body.provider, force=body.force, limit=body.limit, progress=p).as_dict(),
                   body.model_dump())


@router.post("/run")
def run_all(body: RunRequest = RunRequest()):
    from app.extractor.cv_extractor import run_extraction
    from app.fetcher.mail_fetcher import fetch_cvs

    def job(p):
        p(0, 0, "fetching mail")
        f = fetch_cvs(reset=body.reset).as_dict()
        e = run_extraction(provider=body.provider, force=body.force, limit=body.limit, progress=p).as_dict()
        return {"fetch": f, "extract": e}
    return _submit("run", "pipeline", job, body.model_dump())


@router.get("/tasks")
def list_tasks():
    return [t.to_dict() for t in tasks.list()]


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    t = tasks.get(task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    return t.to_dict()


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


def _safe_cv_path(filename: str) -> Path:
    path = (settings.cv_folder / filename).resolve()
    if path.parent != settings.cv_folder.resolve():          # blocks ../../etc/passwd tricks
        raise HTTPException(400, "Invalid file name")
    if not path.exists():
        raise HTTPException(404, "CV not found")
    return path


@router.get("/cvs/{filename}")
def get_cv(filename: str):
    """Open/download the original CV (the UI can show PDFs inline)."""
    path = _safe_cv_path(filename)
    return FileResponse(path, filename=path.name, content_disposition_type="inline")


@router.post("/cvs/upload")
async def upload_cvs(files: list[UploadFile] = File(...)):
    """Manually add CVs (same as dropping files in data/cvs/). Run /api/extract afterwards."""
    from app.fetcher.mail_fetcher import _safe_filename, _unique_path
    settings.ensure_dirs()
    saved, skipped = [], []
    for f in files:
        name = _safe_filename(Path(f.filename or "upload").name)
        if Path(name).suffix.lower() not in CV_EXTENSIONS:
            skipped.append(f.filename)
            continue
        path = _unique_path(settings.cv_folder, name)
        path.write_bytes(await f.read())
        saved.append(path.name)
    return {"saved": saved, "skipped_unsupported": skipped}
