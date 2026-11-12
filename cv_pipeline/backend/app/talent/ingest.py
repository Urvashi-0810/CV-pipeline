"""
Store one extracted CV in Postgres: match it to a candidate, update that candidate's values,
and raise review flags. Used by the extractor, manual upload and the historical backfill.

Newer = later received_at (mail date, or upload time for manual uploads). An older CV arriving
late is attached but only fills blank fields. A blank value never clears the current one.
A manual edit stays unless the new CV's value differs from the previous CV's value for that field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Candidate, CvDocument, EditHistory, ReviewFlag, build_search_text
from app.talent import fields as F
from app.talent.matching import find_match, marked_different_people


@dataclass
class Placement:
    candidate_id: int
    cv_document_id: int
    attached: bool                       # joined an existing candidate
    reextracted: bool = False            # the same file was extracted again
    replaced_edits: list[str] = field(default_factory=list)
    flags_raised: int = 0


def cv_keys(extracted: dict) -> dict:
    return {"passport_norm": F.passport_key(extracted.get("passport_number")),
            "email_norm": F.email_key(extracted.get("email")),
            "phone_norm": F.phone_key(extracted.get("phone"))}


def refresh_candidate(candidate: Candidate) -> None:
    """Recompute the matching keys, birth date and search text after any change of values."""
    candidate.birth_date = F.birth_date(candidate.date_of_birth)
    candidate.passport_norm = F.passport_key(candidate.passport_number)
    candidate.email_norm = F.email_key(candidate.email)
    candidate.phone_norm = F.phone_key(candidate.phone)
    candidate.search_text = build_search_text(candidate)


def refresh_needs_review(session: Session, candidate_id: int) -> None:
    session.flush()
    open_flag = session.scalar(select(ReviewFlag.id).where(
        ReviewFlag.status == "open",
        or_(ReviewFlag.candidate_id == candidate_id, ReviewFlag.other_candidate_id == candidate_id)).limit(1))
    session.get(Candidate, candidate_id).needs_review = open_flag is not None


def apply_cv(session: Session, candidate: Candidate, previous: dict, new: dict, cv: CvDocument,
             newest: bool) -> list[str]:
    """Copy a CV's values onto its candidate. Returns the manually edited fields it replaced."""
    manual = dict(candidate.manual_fields or {})
    replaced = []
    for name in F.FIELDS:
        new_value = F.to_column(name, new.get(name))
        if F.is_blank(new_value):
            continue                                     # a blank never clears a value
        current = getattr(candidate, name)
        if not newest:
            if F.is_blank(current):
                setattr(candidate, name, new_value)      # a late older CV only fills gaps
            continue
        if name in manual:
            if name in F.NEVER_REPLACES_AN_EDIT or F.same_value(name, previous.get(name), new.get(name)):
                continue                                 # the CV didn't change this field: the edit stays
            session.add(EditHistory(candidate_id=candidate.id, field=name, kind="replaced_by_cv",
                                    old_value=F.display(current), new_value=F.display(new_value),
                                    cv_document_id=cv.id))
            replaced.append({"field": name, "manual_value": F.json_safe(current) if not isinstance(current, list)
                             else current, "new_value": F.json_safe(new_value), "cv_document_id": cv.id,
                             "kept": False})
            del manual[name]
        setattr(candidate, name, new_value)

    candidate.manual_fields = manual
    if replaced:
        count = len(replaced)
        session.add(ReviewFlag(
            type="edit_replaced", candidate_id=candidate.id, fields=replaced,
            reason="new CV changed manually edited fields",
            message=f"New CV received: {count} manually edited field{'s were' if count > 1 else ' was'} replaced."))
    return [item["field"] for item in replaced]


def _flag_duplicate(session: Session, first: int, second: int, reason: str) -> bool:
    if marked_different_people(session, first, second):
        return False
    pair = or_((ReviewFlag.candidate_id == first) & (ReviewFlag.other_candidate_id == second),
               (ReviewFlag.candidate_id == second) & (ReviewFlag.other_candidate_id == first))
    if session.scalar(select(ReviewFlag.id).where(pair, ReviewFlag.status == "open",
                                                  ReviewFlag.type == "possible_duplicate").limit(1)):
        return False
    session.add(ReviewFlag(type="possible_duplicate", candidate_id=first, other_candidate_id=second,
                           reason=reason, message=f"Possible duplicate: {reason}."))
    return True


def _reextract(session: Session, cv: CvDocument, document: dict, extracted: dict) -> Placement:
    """The same file extracted again: update that CV; the candidate changes only if it's their newest CV."""
    previous = dict(cv.extracted or {})
    for name in ("extraction_method", "llm", "extracted_at", "s3_key", "s3_status", "local_rel_path", "file_name"):
        if document.get(name) is not None:
            setattr(cv, name, document[name])
    cv.extracted = extracted
    for name, value in cv_keys(extracted).items():
        setattr(cv, name, value)
    session.flush()

    candidate = session.get(Candidate, cv.candidate_id)
    replaced = []
    if candidate.latest_cv_id == cv.id:
        replaced = apply_cv(session, candidate, previous, extracted, cv, newest=True)
        refresh_candidate(candidate)
    refresh_needs_review(session, candidate.id)
    return Placement(candidate.id, cv.id, attached=True, reextracted=True, replaced_edits=replaced,
                     flags_raised=1 if replaced else 0)


def place_cv(session: Session, document: dict, extracted: dict) -> Placement:
    """document: the cv_documents columns (file_hash, file_name, received_at, mail details...)."""
    existing = session.scalar(select(CvDocument).where(CvDocument.file_hash == document["file_hash"]))
    if existing:
        return _reextract(session, existing, document, extracted)

    keys = cv_keys(extracted)
    name = F.to_column("full_name", extracted.get("full_name"))
    match = find_match(session, passport=keys["passport_norm"], email=keys["email_norm"],
                       phone=keys["phone_norm"], name=name)
    received_at = document.get("received_at") or datetime.now(timezone.utc)
    flags = 0

    if match.candidate_id:
        candidate = session.get(Candidate, match.candidate_id, with_for_update=True)
        latest = session.get(CvDocument, candidate.latest_cv_id) if candidate.latest_cv_id else None
        cv = CvDocument(candidate_id=candidate.id, **{**document, "received_at": received_at},
                        extracted=extracted, **keys)
        session.add(cv)
        session.flush()
        newest = candidate.received_at is None or received_at >= candidate.received_at
        replaced = apply_cv(session, candidate, (latest.extracted if latest else {}) or {}, extracted, cv, newest)
        flags += 1 if replaced else 0
        if newest:
            candidate.latest_cv_id, candidate.received_at = cv.id, received_at
        if match.name_differs:
            session.add(ReviewFlag(
                type="passport_name_mismatch", candidate_id=candidate.id,
                reason="same passport number, different name",
                message=f"CV {cv.file_name} was attached by passport number, but its name "
                        f"\"{name}\" differs from \"{candidate.full_name}\".",
                fields=[{"cv_document_id": cv.id, "cv_name": name, "candidate_name": candidate.full_name}]))
            flags += 1
        refresh_candidate(candidate)
        refresh_needs_review(session, candidate.id)
        return Placement(candidate.id, cv.id, attached=True, replaced_edits=replaced, flags_raised=flags)

    candidate = Candidate(**F.columns(extracted), received_at=received_at)
    refresh_candidate(candidate)
    session.add(candidate)
    session.flush()
    cv = CvDocument(candidate_id=candidate.id, **{**document, "received_at": received_at},
                    extracted=extracted, **keys)
    session.add(cv)
    session.flush()
    candidate.latest_cv_id = cv.id

    for duplicate in match.duplicates:
        if _flag_duplicate(session, candidate.id, duplicate.other_candidate_id, duplicate.reason):
            flags += 1
            refresh_needs_review(session, duplicate.other_candidate_id)
    refresh_needs_review(session, candidate.id)
    return Placement(candidate.id, cv.id, attached=False, flags_raised=flags)


def extracted_from_model(candidate, education: Optional[str]) -> dict:
    """An extracted Candidate (app.core.models) as the JSON kept in cv_documents.extracted."""
    return {
        "full_name": candidate.full_name, "email": candidate.email, "phone": candidate.phone,
        "passport_number": candidate.passport_number, "job_title": candidate.current_job_title,
        "company": candidate.current_company, "date_of_birth": candidate.date_of_birth,
        "location": candidate.location, "summary": candidate.summary, "education": education,
        "skills": list(candidate.skills), "certifications": list(candidate.certifications),
        "languages": list(candidate.languages), "experience_years": candidate.total_experience_years,
        "age": candidate.age,
    }
