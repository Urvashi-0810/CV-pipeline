"""Talent Pool API: the candidate list, detail, edits, review flags, delete and S3 retry.

Every route needs a Talent Pool login (added in main.py). Delete also needs Talent Pool admin.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel

from app.api.problems import ProblemError
from app.api.tasks import LaneBusy, tasks
from app.auth.deps import AdminUser, CurrentUser, DbSession
from app.db.models import CvDocument
from app.storage.s3_store import URL_EXPIRY_SECONDS, S3NotConfigured, presigned_cv_url
from app.talent import service
from app.talent.excel_export import request_refresh
from app.talent.runs import recorded

router = APIRouter(prefix="/api/talent", tags=["talent pool"])


class CandidateEdit(BaseModel):
    values: dict[str, Any]


class KeepEdit(BaseModel):
    field: str


def _version(if_match: Optional[str]) -> Optional[int]:
    if if_match is None:
        return None
    try:
        return int(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError:
        raise ProblemError(400, "bad-if-match", "If-Match must be the candidate's row_version")


def _busy(e: LaneBusy) -> ProblemError:
    return ProblemError(409, "pipeline-busy", "A pipeline run is in progress",
                        "Wait for it to finish, then try again.", task=e.task.to_dict())


@router.get("/candidates")
def list_candidates(
    db: DbSession,
    search: str = "", trade: str = "", location: str = "",
    skills: str = Query("", description="comma separated; every skill must be present"),
    min_experience: Optional[float] = None, max_experience: Optional[float] = None,
    min_age: Optional[int] = None, max_age: Optional[int] = None,
    received_from: Optional[date] = None, received_to: Optional[date] = None,
    source: str = Query("", description='a mailbox, or "manual" for manual uploads'),
    needs_review: Optional[bool] = None,
    include_unknown: bool = Query(True, description="keep candidates with unknown experience or age"),
    sort: Literal["name", "trade", "passport", "experience", "age", "location", "received"] = "received",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=service.MAX_PAGE), offset: int = Query(0, ge=0),
):
    return service.list_candidates(
        db, search=search, trade=trade, location=location, skills=skills, min_experience=min_experience,
        max_experience=max_experience, min_age=min_age, max_age=max_age, received_from=received_from,
        received_to=received_to, source=source, needs_review=needs_review, include_unknown=include_unknown,
        sort=sort, order=order, limit=limit, offset=offset)


@router.get("/filter-options")
def filter_options(db: DbSession):
    return service.filter_options(db)


@router.get("/suggest")
def suggest(db: DbSession, field: Literal["trade", "location", "skills"], q: str = Query(..., min_length=1)):
    return {"items": service.suggest(db, field, q)}


@router.get("/candidates/{candidate_id}")
def candidate_detail(candidate_id: int, db: DbSession, response: Response):
    detail = service.candidate_detail(db, candidate_id)
    response.headers["ETag"] = f'"{detail["row_version"]}"'
    return detail


@router.get("/candidates/{candidate_id}/history")
def candidate_history(candidate_id: int, db: DbSession):
    return {"items": service.edit_history(db, candidate_id)}


@router.patch("/candidates/{candidate_id}")
def edit_candidate(candidate_id: int, body: CandidateEdit, current: CurrentUser, db: DbSession, response: Response,
                   if_match: Optional[str] = Header(None)):
    result = service.edit_candidate(db, candidate_id, body.values, user_id=current.user.id,
                                    expected_version=_version(if_match))
    db.commit()
    if result["changed"]:
        request_refresh()
    response.headers["ETag"] = f'"{result["row_version"]}"'
    return result


@router.delete("/candidates/{candidate_id}")
def delete_candidate(candidate_id: int, _: AdminUser):
    """Talent Pool admin only. Refused while a pipeline run is active, and holds off new runs meanwhile."""
    try:
        return tasks.run_exclusive("delete", "pipeline", lambda: service.delete_candidate(candidate_id))
    except LaneBusy as e:
        raise _busy(e)
    except S3NotConfigured as e:
        raise ProblemError(503, "s3-not-configured", "S3 is not set up", str(e))


@router.get("/cvs/{cv_id}/url")
def cv_url(cv_id: int, db: DbSession, download: bool = False):
    """A short-lived S3 link to one CV version: opens inline, or downloads with ?download=true."""
    cv = db.get(CvDocument, cv_id)
    if cv is None:
        raise ProblemError(404, "cv-not-found", "CV not found")
    if cv.s3_status != "success" or not cv.s3_key:
        raise ProblemError(409, "cv-not-in-s3", "This CV isn't in S3",
                           "Its upload failed or the file is missing. Use Retry failed S3 uploads on the Pipeline screen.")
    try:
        url = presigned_cv_url(cv.s3_key, download=download)
    except S3NotConfigured as e:
        raise ProblemError(503, "s3-not-configured", "S3 is not set up", str(e))
    return {"url": url, "expires_in": URL_EXPIRY_SECONDS}


@router.post("/flags/{flag_id}/merge")
def merge(flag_id: int, current: CurrentUser, db: DbSession):
    result = service.merge(db, flag_id, user_id=current.user.id)
    db.commit()
    request_refresh()
    return result


@router.post("/flags/{flag_id}/different-people")
def different_people(flag_id: int, current: CurrentUser, db: DbSession):
    result = service.mark_different_people(db, flag_id, user_id=current.user.id)
    db.commit()
    return result


@router.post("/flags/{flag_id}/keep-edit")
def keep_edit(flag_id: int, body: KeepEdit, current: CurrentUser, db: DbSession):
    result = service.keep_my_edit(db, flag_id, body.field, user_id=current.user.id)
    db.commit()
    request_refresh()
    return result


@router.post("/flags/{flag_id}/accept")
def accept(flag_id: int, current: CurrentUser, db: DbSession):
    result = service.accept_flag(db, flag_id, user_id=current.user.id)
    db.commit()
    return result


@router.post("/s3/retry")
def retry_s3():
    """Re-upload every CV whose S3 upload failed. A background task in the pipeline lane."""
    try:
        return tasks.submit("s3-retry", "pipeline", recorded("s3-retry", service.retry_failed_uploads),
                            controllable=True).to_dict()
    except LaneBusy as e:
        raise _busy(e)
