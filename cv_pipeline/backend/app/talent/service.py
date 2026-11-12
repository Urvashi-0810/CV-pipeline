"""
Talent Pool operations behind the API: list, detail, edit, review flags, merge, delete, S3 retry.

Edits and flag actions change Postgres and ask for a coalesced Excel rebuild. Delete also
removes the person's files from S3, the local disk, the JSON state and the job files.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pandas as pd
from sqlalchemy import Integer, and_, cast, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.problems import ProblemError
from app.core.config import settings
from app.core.control import RunCancelled, checkpoint
from app.core.logger import get_logger
from app.core.state import JsonState
from app.db.models import Candidate, CvDocument, EditHistory, ReviewFlag, User
from app.db.session import session_scope
from app.storage import s3_store
from app.storage.excel_store import KEY, write_sheets
from app.talent import fields as F
from app.talent.excel_export import request_refresh
from app.talent.ingest import _flag_duplicate, refresh_candidate, refresh_needs_review
from app.talent.matching import duplicates_after_edit

log = get_logger("talent")

MANUAL_SOURCE = "manual"
MAX_PAGE = 200
MAX_TEXT_LENGTH = 5000
CONTACT_FIELDS = {"passport_number", "email", "phone"}
SKILL_SEPARATOR = "\x1f"

# Age worked out from the date of birth when it's known, else the age from the CV.
AGE = func.coalesce(cast(func.date_part("year", func.age(func.current_date(), Candidate.birth_date)), Integer),
                    Candidate.age)
SORTS = {
    "name": func.lower(Candidate.full_name), "trade": func.lower(Candidate.job_title),
    "passport": Candidate.passport_number, "experience": Candidate.experience_years, "age": AGE,
    "location": func.lower(Candidate.location), "received": Candidate.received_at,
}
SUGGEST = {"trade": Candidate.job_title, "location": Candidate.location}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _like(text: str) -> str:
    escaped = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _number(value: Any) -> Any:
    return F.json_safe(value)


def _get(session: Session, candidate_id: int, *, lock: bool = False) -> Candidate:
    candidate = session.get(Candidate, candidate_id, with_for_update=lock)
    if candidate is None:
        raise ProblemError(404, "candidate-not-found", "Candidate not found",
                           "They may have been merged into another candidate or deleted.")
    return candidate


# ---------- list ----------

def list_candidates(session: Session, *, search: str = "", trade: str = "", location: str = "", skills: str = "",
                    min_experience: Optional[float] = None, max_experience: Optional[float] = None,
                    min_age: Optional[int] = None, max_age: Optional[int] = None,
                    received_from: Optional[date] = None, received_to: Optional[date] = None,
                    source: str = "", needs_review: Optional[bool] = None, include_unknown: bool = True,
                    sort: str = "received", order: str = "desc", limit: int = 50, offset: int = 0) -> dict:
    conditions = []
    if search.strip():
        conditions.append(Candidate.search_text.ilike(_like(search)))
    if trade.strip():
        conditions.append(Candidate.job_title.ilike(_like(trade)))
    if location.strip():
        conditions.append(Candidate.location.ilike(_like(location)))
    for skill in (s for s in skills.split(",") if s.strip()):
        # Every listed skill must appear. The separator stops a term matching across two skills.
        conditions.append(func.array_to_string(Candidate.skills, SKILL_SEPARATOR).ilike(_like(skill)))
    for column, low, high in ((Candidate.experience_years, min_experience, max_experience), (AGE, min_age, max_age)):
        if low is None and high is None:
            continue
        in_range = and_(*(c for c in (column >= low if low is not None else None,
                                      column <= high if high is not None else None) if c is not None))
        # Unknown values are kept by default, as decided for the Experience and Age filters.
        conditions.append(or_(in_range, column.is_(None)) if include_unknown else in_range)
    if received_from:
        conditions.append(Candidate.received_at >= received_from)
    if received_to:
        conditions.append(Candidate.received_at < received_to + timedelta(days=1))
    if source:
        match = (CvDocument.source_type == MANUAL_SOURCE) if source == MANUAL_SOURCE else \
            (CvDocument.mail_account == source)
        conditions.append(exists().where(CvDocument.candidate_id == Candidate.id, match))
    if needs_review is not None:
        conditions.append(Candidate.needs_review.is_(needs_review))

    total = session.scalar(select(func.count()).select_from(Candidate).where(*conditions))
    sort_column = SORTS.get(sort, SORTS["received"])
    ordering = sort_column.asc().nulls_last() if order == "asc" else sort_column.desc().nulls_last()
    cv_count = select(func.count()).where(CvDocument.candidate_id == Candidate.id).scalar_subquery()
    latest = CvDocument.__table__.alias("latest")
    rows = session.execute(
        select(Candidate, AGE.label("current_age"), cv_count.label("cv_count"), latest.c.s3_status)
        .outerjoin(latest, latest.c.id == Candidate.latest_cv_id)
        .where(*conditions).order_by(ordering, Candidate.id.desc())
        .limit(max(1, min(limit, MAX_PAGE))).offset(max(0, offset)))
    items = [{
        "id": c.id, "full_name": c.full_name, "job_title": c.job_title, "passport_number": c.passport_number,
        "experience_years": _number(c.experience_years), "age": age, "location": c.location,
        "received_at": c.received_at, "needs_review": c.needs_review, "cv_count": count,
        "latest_cv_id": c.latest_cv_id, "latest_cv_viewable": status == "success",
    } for c, age, count, status in rows]
    return {"total": total, "items": items}


def filter_options(session: Session) -> dict:
    mailboxes = session.execute(
        select(CvDocument.mail_account, func.count(func.distinct(CvDocument.candidate_id)))
        .where(CvDocument.mail_account.is_not(None)).group_by(CvDocument.mail_account)
        .order_by(CvDocument.mail_account))
    manual = session.scalar(select(func.count(func.distinct(CvDocument.candidate_id)))
                            .where(CvDocument.source_type == MANUAL_SOURCE))
    sources = [{"value": account, "label": account, "candidates": count} for account, count in mailboxes]
    if manual:
        sources.append({"value": MANUAL_SOURCE, "label": "Manual upload", "candidates": manual})
    return {"sources": sources}


def suggest(session: Session, field: str, text: str, limit: int = 10) -> list[dict]:
    """Values for a type-to-search filter, most common first."""
    if field == "skills":
        skill = func.unnest(Candidate.skills).label("value")
        inner = select(skill).subquery()
        query = (select(inner.c.value, func.count().label("n")).where(inner.c.value.ilike(_like(text)))
                 .group_by(inner.c.value))
    elif field in SUGGEST:
        column = SUGGEST[field]
        query = select(column.label("value"), func.count().label("n")).where(column.ilike(_like(text))) \
            .group_by(column)
    else:
        raise ProblemError(400, "unknown-field", "Suggestions are available for trade, location and skills")
    rows = session.execute(query.order_by(func.count().desc()).limit(limit))
    return [{"value": value, "candidates": count} for value, count in rows]


# ---------- detail ----------

def _cv_out(cv: CvDocument, latest_id: Optional[int]) -> dict:
    return {
        "id": cv.id, "file_name": cv.file_name, "received_at": cv.received_at, "source_type": cv.source_type,
        "mail_account": cv.mail_account, "mail_from": cv.mail_from, "mail_from_name": cv.mail_from_name,
        "mail_subject": cv.mail_subject, "s3_status": cv.s3_status, "viewable": cv.s3_status == "success",
        "is_latest": cv.id == latest_id, "extraction_method": cv.extraction_method, "llm": cv.llm,
        "extracted_at": cv.extracted_at,
    }


def _flag_out(session: Session, flag: ReviewFlag, candidate_id: int) -> dict:
    other_id = flag.other_candidate_id if flag.candidate_id == candidate_id else flag.candidate_id
    other = session.get(Candidate, other_id) if other_id and other_id != candidate_id else None
    return {
        "id": flag.id, "type": flag.type, "status": flag.status, "reason": flag.reason, "message": flag.message,
        "fields": flag.fields, "created_at": flag.created_at,
        "other_candidate": {"id": other.id, "full_name": other.full_name, "passport_number": other.passport_number,
                            "email": other.email, "phone": other.phone, "job_title": other.job_title}
        if other else None,
    }


def candidate_detail(session: Session, candidate_id: int) -> dict:
    c = _get(session, candidate_id)
    cvs = session.scalars(select(CvDocument).where(CvDocument.candidate_id == c.id)
                          .order_by(CvDocument.received_at.desc(), CvDocument.id.desc()))
    flags = session.scalars(select(ReviewFlag).where(
        ReviewFlag.status == "open",
        or_(ReviewFlag.candidate_id == c.id, ReviewFlag.other_candidate_id == c.id)).order_by(ReviewFlag.id))
    values = {name: getattr(c, name) for name in F.FIELDS}
    values["experience_years"] = _number(values["experience_years"])
    return {
        "id": c.id, "values": values, "age": F.current_age(c.birth_date, c.age),
        "age_from_birth_date": c.birth_date is not None, "manual_fields": sorted(c.manual_fields or {}),
        "needs_review": c.needs_review, "received_at": c.received_at, "row_version": c.row_version,
        "created_at": c.created_at, "updated_at": c.updated_at,
        "cvs": [_cv_out(cv, c.latest_cv_id) for cv in cvs],
        "flags": [_flag_out(session, flag, c.id) for flag in flags],
    }


def edit_history(session: Session, candidate_id: int) -> list[dict]:
    _get(session, candidate_id)
    rows = session.execute(
        select(EditHistory, User.display_name).outerjoin(User, User.id == EditHistory.changed_by_user_id)
        .where(EditHistory.candidate_id == candidate_id)
        .order_by(EditHistory.created_at.desc(), EditHistory.id.desc()))
    return [{"id": h.id, "field": h.field, "old_value": h.old_value, "new_value": h.new_value, "kind": h.kind,
             "changed_by": name, "cv_document_id": h.cv_document_id, "created_at": h.created_at}
            for h, name in rows]


# ---------- edit ----------

def _clean_edit(name: str, value: Any) -> Any:
    if name not in F.FIELDS:
        raise ProblemError(422, "validation-error", "Some fields are invalid", "Fix the listed fields and try again.",
                           errors=[{"field": name, "message": "This field can't be edited."}])
    if isinstance(value, str) and len(value) > MAX_TEXT_LENGTH:
        raise ProblemError(422, "validation-error", "Some fields are invalid", "Fix the listed fields and try again.",
                           errors=[{"field": name, "message": f"At most {MAX_TEXT_LENGTH} characters."}])
    cleaned = F.to_column(name, value)
    if name in F.NUMBER_FIELDS and cleaned is None and not F.is_blank(value):
        raise ProblemError(422, "validation-error", "Some fields are invalid", "Fix the listed fields and try again.",
                           errors=[{"field": name, "message": "Enter a number."}])
    return cleaned


def edit_candidate(session: Session, candidate_id: int, values: dict, *, user_id: int,
                   expected_version: Optional[int]) -> dict:
    """Manual edits. Each changed field is recorded and kept against later CVs that don't change it."""
    if expected_version is None:
        raise ProblemError(428, "if-match-required", "Missing version",
                           "Send the candidate's row_version in the If-Match header.")
    cleaned = {name: _clean_edit(name, value) for name, value in values.items()}
    c = _get(session, candidate_id, lock=True)
    if c.row_version != expected_version:
        raise ProblemError(412, "candidate-changed", "Someone else changed this candidate",
                           "Reload to see the latest values, then make your change again.")

    now = _now()
    manual = dict(c.manual_fields or {})
    changed = []
    for name, value in cleaned.items():
        current = getattr(c, name)
        if value == current or (F.is_blank(value) and F.is_blank(current)):
            continue
        session.add(EditHistory(candidate_id=c.id, field=name, kind="manual_edit", changed_by_user_id=user_id,
                                old_value=F.display(current), new_value=F.display(value)))
        setattr(c, name, value)
        manual[name] = now.isoformat()
        changed.append(name)
    if not changed:
        return {"changed": [], "row_version": c.row_version, "flags_raised": 0}

    c.manual_fields = manual
    c.row_version += 1
    refresh_candidate(c)
    session.flush()
    flags = 0
    if CONTACT_FIELDS & set(changed):
        # An edited passport, email or phone re-runs matching. It only ever raises flags.
        for duplicate in duplicates_after_edit(session, c):
            if _flag_duplicate(session, c.id, duplicate.other_candidate_id, f"after an edit: {duplicate.reason}"):
                flags += 1
                refresh_needs_review(session, duplicate.other_candidate_id)
    refresh_needs_review(session, c.id)
    return {"changed": changed, "row_version": c.row_version, "flags_raised": flags}


# ---------- review flags ----------

def _open_flag(session: Session, flag_id: int, *types: str) -> ReviewFlag:
    flag = session.get(ReviewFlag, flag_id, with_for_update=True)
    if flag is None:
        raise ProblemError(404, "flag-not-found", "Review item not found")
    if flag.status != "open":
        raise ProblemError(409, "flag-resolved", "This review item was already resolved")
    if types and flag.type not in types:
        raise ProblemError(409, "wrong-flag-type", "That action doesn't apply to this review item")
    return flag


def _resolve(flag: ReviewFlag, resolution: str, user_id: int) -> None:
    flag.status, flag.resolution = "resolved", resolution
    flag.resolved_by_user_id, flag.resolved_at = user_id, _now()


def mark_different_people(session: Session, flag_id: int, *, user_id: int) -> dict:
    flag = _open_flag(session, flag_id, "possible_duplicate")
    _resolve(flag, "different_people", user_id)
    for candidate_id in (flag.candidate_id, flag.other_candidate_id):
        refresh_needs_review(session, candidate_id)
    return {"resolved": flag.id}


def accept_flag(session: Session, flag_id: int, *, user_id: int) -> dict:
    """Close a flag without changing anything: the new CV's values, or the passport match, are fine."""
    flag = _open_flag(session, flag_id, "edit_replaced", "passport_name_mismatch")
    _resolve(flag, "accepted", user_id)
    refresh_needs_review(session, flag.candidate_id)
    return {"resolved": flag.id}


def keep_my_edit(session: Session, flag_id: int, field: str, *, user_id: int) -> dict:
    """Put back a manual value that a newer CV replaced."""
    flag = _open_flag(session, flag_id, "edit_replaced")
    items = [dict(item) for item in flag.fields]
    item = next((i for i in items if i["field"] == field and not i.get("kept")), None)
    if item is None:
        raise ProblemError(409, "nothing-to-keep", "That field has no replaced edit to keep")
    c = _get(session, flag.candidate_id, lock=True)
    restored = F.to_column(field, item["manual_value"])
    session.add(EditHistory(candidate_id=c.id, field=field, kind="keep_my_edit", changed_by_user_id=user_id,
                            old_value=F.display(getattr(c, field)), new_value=F.display(restored),
                            cv_document_id=item.get("cv_document_id")))
    setattr(c, field, restored)
    c.manual_fields = {**(c.manual_fields or {}), field: _now().isoformat()}
    c.row_version += 1
    refresh_candidate(c)
    item["kept"] = True
    flag.fields = items
    if all(i.get("kept") for i in items):
        _resolve(flag, "kept_edit", user_id)
    refresh_needs_review(session, c.id)
    return {"kept": field, "row_version": c.row_version, "flag_resolved": flag.status == "resolved"}


def merge(session: Session, flag_id: int, *, user_id: int) -> dict:
    """Same person: all CVs move to the older candidate record and the other is removed.

    Values come from the newest CV across both; manual edits from both are kept, and when both
    edited a field the more recent edit wins.
    """
    flag = _open_flag(session, flag_id, "possible_duplicate")
    first_id, second_id = sorted((flag.candidate_id, flag.other_candidate_id))
    keep, gone = _get(session, first_id, lock=True), _get(session, second_id, lock=True)

    edits: dict[str, tuple[str, Any]] = {}
    for person in (keep, gone):
        for name, when in (person.manual_fields or {}).items():
            if name not in edits or when > edits[name][0]:
                edits[name] = (when, getattr(person, name))

    session.execute(update(CvDocument).where(CvDocument.candidate_id == gone.id).values(candidate_id=keep.id))
    session.execute(update(EditHistory).where(EditHistory.candidate_id == gone.id).values(candidate_id=keep.id))
    _resolve(flag, "merged", user_id)
    flag.other_candidate_id = None              # kept as a record of the merge after the other one is removed
    flag.candidate_id = keep.id
    flag.fields = [{"merged_candidate_id": gone.id, "merged_name": gone.full_name}]
    session.flush()
    _move_flags(session, gone.id, keep.id, user_id)

    cvs = session.scalars(select(CvDocument).where(CvDocument.candidate_id == keep.id)
                          .order_by(CvDocument.received_at, CvDocument.id)).all()
    values: dict[str, Any] = {}
    for cv in cvs:                                # oldest first, so the newest non-blank value wins
        for name in F.FIELDS:
            value = F.to_column(name, (cv.extracted or {}).get(name))
            if not F.is_blank(value):
                values[name] = value
    for name, (_, value) in edits.items():
        values[name] = value
    for name in F.FIELDS:
        setattr(keep, name, values.get(name, [] if name in F.LIST_FIELDS else None))
    keep.manual_fields = {name: when for name, (when, _) in edits.items()}
    if cvs:
        keep.latest_cv_id, keep.received_at = cvs[-1].id, cvs[-1].received_at
    keep.row_version += 1
    refresh_candidate(keep)
    session.delete(gone)
    session.flush()
    refresh_needs_review(session, keep.id)
    return {"candidate_id": keep.id, "merged_candidate_id": gone.id, "cvs": len(cvs)}


def _move_flags(session: Session, old_id: int, new_id: int, user_id: int) -> None:
    """Point the removed candidate's other flags at the merged one, closing any that no longer make sense."""
    for flag in session.scalars(select(ReviewFlag).where(
            or_(ReviewFlag.candidate_id == old_id, ReviewFlag.other_candidate_id == old_id))).all():
        first = new_id if flag.candidate_id == old_id else flag.candidate_id
        second = new_id if flag.other_candidate_id == old_id else flag.other_candidate_id
        if second is not None and first == second:
            if flag.status == "open":
                _resolve(flag, "merged", user_id)
            first, second = new_id, None            # a flag between the two merged people
        elif flag.status == "open" and second is not None:
            duplicate = session.scalar(select(ReviewFlag.id).where(
                ReviewFlag.id != flag.id, ReviewFlag.status == "open", ReviewFlag.type == flag.type,
                or_(and_(ReviewFlag.candidate_id == first, ReviewFlag.other_candidate_id == second),
                    and_(ReviewFlag.candidate_id == second, ReviewFlag.other_candidate_id == first))))
            if duplicate:
                _resolve(flag, "merged", user_id)
        flag.candidate_id, flag.other_candidate_id = first, second
        session.flush()
        for candidate_id in (first, second):
            if candidate_id and candidate_id != old_id:
                refresh_needs_review(session, candidate_id)


# ---------- delete ----------

def _remove_from_state(hashes: set[str]) -> None:
    processed = JsonState(settings.state_folder / "processed_files.json")
    for file_hash in hashes:
        processed.data.pop(file_hash, None)
    processed.save()
    manifest = JsonState(settings.state_folder / "attachments_manifest.json")
    by_hash = manifest.get("by_hash", {})
    for file_hash in hashes:
        by_hash.pop(f"PDF:{file_hash}", None)
        by_hash.pop(file_hash, None)
    manifest.set("by_hash", by_hash)
    manifest.save()
    for file_hash in hashes:
        (settings.state_folder / "text_cache" / f"{file_hash}.md").unlink(missing_ok=True)


def _remove_from_jobs(hashes: set[str]) -> int:
    """Drop the person from every job's shortlist and job-fit files. Returns the files changed."""
    import json
    changed = 0
    for job_dir in (p for p in settings.jobs_folder.glob("*") if p.is_dir()):
        for name in ("shortlist.xlsx", "jobfit.xlsx"):
            path = job_dir / name
            if not path.exists():
                continue
            sheets = pd.read_excel(path, sheet_name=None, dtype={KEY: str, "Phone": str})
            kept = {sheet: df[~df[KEY].astype(str).isin(hashes)] if KEY in df.columns else df
                    for sheet, df in sheets.items()}
            if any(len(kept[s]) != len(sheets[s]) for s in sheets):
                write_sheets(path, kept)
                changed += 1
        results_path = job_dir / "jobfit_results.json"
        if results_path.exists():
            cache = json.loads(results_path.read_text(encoding="utf-8"))
            results = cache.get("results", {})
            if hashes & set(results):
                cache["results"] = {h: r for h, r in results.items() if h not in hashes}
                results_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                changed += 1
    return changed


def delete_candidate(candidate_id: int) -> dict:
    """Remove a person everywhere. Runs while holding the pipeline lane (see the route)."""
    with session_scope() as session:
        c = _get(session, candidate_id, lock=True)
        cvs = session.scalars(select(CvDocument).where(CvDocument.candidate_id == c.id)).all()
        hashes = {cv.file_hash for cv in cvs}
        keys = [cv.s3_key for cv in cvs if cv.s3_key]
        local = [settings.cv_folder / cv.local_rel_path for cv in cvs if cv.local_rel_path]
        name = c.full_name
        others = {f.candidate_id if f.other_candidate_id == c.id else f.other_candidate_id
                  for f in session.scalars(select(ReviewFlag).where(
                      or_(ReviewFlag.candidate_id == c.id, ReviewFlag.other_candidate_id == c.id)))}
        session.delete(c)
        session.flush()
        # S3 first: if it fails the transaction rolls back and nothing is lost.
        try:
            s3_store.delete_cv_keys(keys)
        except s3_store.S3NotConfigured:
            raise
        except Exception as e:  # noqa: BLE001 - any S3 failure stops the delete
            log.error("S3 delete failed for candidate %s: %s", candidate_id, e)
            raise ProblemError(502, "s3-delete-failed", "The CV files couldn't be deleted from S3",
                               "Nothing was deleted. Try again in a moment.")
        for other_id in others - {None, c.id}:
            refresh_needs_review(session, other_id)

    removed_files = 0
    for path in local:
        if path.exists():
            path.unlink()
            removed_files += 1
    _remove_from_state(hashes)
    jobs_changed = _remove_from_jobs(hashes)
    request_refresh()
    log.info("Deleted candidate %s (%s): %d CV(s)", candidate_id, name, len(hashes))
    return {"deleted": candidate_id, "name": name, "cvs": len(hashes), "s3_files": len(keys),
            "local_files": removed_files, "job_files_changed": jobs_changed}


# ---------- S3 retry ----------

def retry_failed_uploads(progress: Callable[[int, int, str], None]) -> dict:
    """Upload every CV whose S3 upload failed or whose file was missing from the bucket."""
    with session_scope() as session:
        todo = [(cv.id, cv.file_hash, cv.local_rel_path, cv.file_name) for cv in session.scalars(
            select(CvDocument).where(CvDocument.s3_status.in_(("failed", "missing", "pending"))))]
    result = {"tried": len(todo), "uploaded": 0, "failed": 0, "no_local_file": 0, "cancelled": False}
    processed = JsonState(settings.state_folder / "processed_files.json")
    for done, (cv_id, file_hash, relative_path, file_name) in enumerate(todo):
        try:
            checkpoint()
        except RunCancelled:
            result["cancelled"] = True
            break
        progress(done, len(todo), file_name)
        path = settings.cv_folder / relative_path if relative_path else None
        if path is None or not path.exists():
            result["no_local_file"] += 1
            continue
        ok = s3_store.upload_cv_to_s3(path)
        result["uploaded" if ok else "failed"] += 1
        with session_scope() as session:
            cv = session.get(CvDocument, cv_id)
            cv.s3_status = "success" if ok else "failed"
            cv.s3_key = s3_store.key_for(relative_path)
        if file_hash in processed.data:
            processed.data[file_hash]["s3_status"] = "Success" if ok else "Failed"
    processed.save()
    progress(len(todo), len(todo), "done")
    if result["uploaded"]:
        request_refresh(delay=0)
    return result


def counts(session: Session) -> dict:
    by_status = dict(session.execute(select(CvDocument.s3_status, func.count()).group_by(CvDocument.s3_status)).all())
    return {
        "candidates": session.scalar(select(func.count()).select_from(Candidate)),
        "cv_documents": sum(by_status.values()),
        "needs_review": session.scalar(select(func.count()).where(Candidate.needs_review.is_(True))),
        "s3_failed": by_status.get("failed", 0) + by_status.get("pending", 0),
        "s3_missing": by_status.get("missing", 0),
    }
