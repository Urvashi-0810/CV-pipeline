"""Endpoints for Steps 3-4: jobs (JD), requirements, criteria, shortlist, job fit."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.routes_pipeline import _submit, df_records
from app.api.schemas import JdUpdate, JobFitRequest, RequirementsRequest
from app.core.models import JobRequirements, ShortlistCriteria
from app.jobs.job_store import Job, create_job, delete_job, list_jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job(slug: str) -> Job:
    job = Job(slug)
    if not job.exists():
        raise HTTPException(404, f"Job '{slug}' not found")
    return job


def _bad_request(fn, *a, **k):
    """Turn our ValueErrors (bad input / missing step) into HTTP 400 with the message."""
    try:
        return fn(*a, **k)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ jobs CRUD
@router.get("")
def jobs():
    return list_jobs()


@router.post("")
async def new_job(title: str = Form(...), slug: Optional[str] = Form(None),
                  jd_text: Optional[str] = Form(None), jd_file: Optional[UploadFile] = File(None)):
    """Create a job from pasted JD text OR an uploaded JD file (PDF/DOCX/TXT)."""
    if not jd_text and not jd_file:
        raise HTTPException(400, "Give jd_text or jd_file")
    if jd_file:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / Path(jd_file.filename or "jd.txt").name
            p.write_bytes(await jd_file.read())
            job = _bad_request(create_job, title, slug=slug, jd_file=p)
    else:
        job = _bad_request(create_job, title, slug=slug, jd_text=jd_text)
    return job.summary()


@router.get("/{slug}")
def get_job(slug: str):
    job = _job(slug)
    req = job.load_requirements()
    return {**job.summary(), "jd_text": job.jd_text if job.jd_path.exists() else "",
            "requirements": req.model_dump() if req else None, "criteria": job.load_criteria().model_dump()}


@router.put("/{slug}/jd")
def update_jd(slug: str, body: JdUpdate):
    job = _job(slug)
    _bad_request(job.set_jd_text, body.jd_text)
    return {"ok": True, "note": "requirements were reset - regenerate them"}


@router.delete("/{slug}")
def remove_job(slug: str):
    _job(slug)
    delete_job(slug)
    return {"deleted": slug}


# ------------------------------------------------------------------ requirements (rubric)
@router.post("/{slug}/requirements")
def make_requirements(slug: str, body: RequirementsRequest = RequirementsRequest()):
    """LLM reads the JD -> requirements.json (+ suggested criteria.json if still empty). ~10-30 s."""
    from app.jobfit.requirements import prepare_job
    _job(slug)
    try:
        return prepare_job(slug, body.provider, body.force, body.overwrite_criteria)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.put("/{slug}/requirements")
def edit_requirements(slug: str, req: JobRequirements):
    """Let HR edit the rubric (add/remove items, change must/nice). Clears cached job-fit scores."""
    job = _job(slug)
    job.save_requirements(req)
    return req


# ------------------------------------------------------------------ criteria + shortlist (Step 3)
@router.get("/{slug}/criteria")
def get_criteria(slug: str):
    return _job(slug).load_criteria()


@router.put("/{slug}/criteria")
def put_criteria(slug: str, c: ShortlistCriteria):
    _job(slug).save_criteria(c)
    return c


@router.post("/{slug}/shortlist")
def shortlist(slug: str, c: Optional[ShortlistCriteria] = None):
    """Run the rules (instant). If a criteria body is sent it is saved first."""
    from app.shortlist.shortlister import run_shortlist
    job = _job(slug)
    if c is not None:
        job.save_criteria(c)
    summary = _bad_request(run_shortlist, slug)
    return {"summary": summary, **get_shortlist(slug)}


@router.get("/{slug}/shortlist")
def get_shortlist(slug: str):
    from app.shortlist.shortlister import load_rejected, load_shortlist
    _job(slug)
    try:
        return {"shortlisted": df_records(load_shortlist(slug)), "rejected": df_records(load_rejected(slug))}
    except ValueError:
        return {"shortlisted": [], "rejected": []}


# ------------------------------------------------------------------ job fit (Step 4, background)
@router.post("/{slug}/jobfit")
def jobfit(slug: str, body: JobFitRequest = JobFitRequest()):
    from app.jobfit.jobfit import run_jobfit
    job = _job(slug)
    if not job.shortlist_xlsx.exists():
        raise HTTPException(400, "Run the shortlist first")
    return _submit("jobfit", f"job:{slug}",
                   lambda p: run_jobfit(slug, provider=body.provider, force=body.force,
                                        workers=body.workers, limit=body.limit, progress=p),
                   {"job": slug, **body.model_dump()})


@router.get("/{slug}/jobfit")
def get_jobfit(slug: str):
    from app.jobfit.jobfit import ranked_results
    _job(slug)
    return {"results": ranked_results(slug)}


# ------------------------------------------------------------------ downloads
@router.get("/{slug}/download/{kind}")
def download(slug: str, kind: Literal["shortlist", "jobfit"]):
    job = _job(slug)
    path = job.shortlist_xlsx if kind == "shortlist" else job.jobfit_xlsx
    if not path.exists():
        raise HTTPException(404, f"No {kind} file yet")
    return FileResponse(path, filename=f"{slug}_{path.name}")
