"""
Matching rules: which candidate (if any) an incoming CV belongs to, and which pairs to flag.

1. Same passport -> attach automatically (flagged if the names clearly differ).
2. Different passports, same email or phone -> new candidate, flag the pair.
3. No passport on one or both sides, same email or phone and a similar name -> new candidate, flag the pair.
4. Name only -> never a match.

An email or phone seen with BUSY_CONTACT_NAMES or more different names is an agency contact
and never triggers a flag. A pair marked "Different people" is never flagged again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import Candidate, CvDocument, ReviewFlag
from app.talent.fields import similar_names

BUSY_CONTACT_NAMES = 3


@dataclass
class PossibleDuplicate:
    other_candidate_id: int
    reason: str


@dataclass
class Match:
    candidate_id: Optional[int] = None
    name_differs: bool = False
    duplicates: list[PossibleDuplicate] = field(default_factory=list)


def _candidate_ids_with(session: Session, column: str, value: str) -> set[int]:
    by_cv = select(CvDocument.candidate_id).where(getattr(CvDocument, column) == value)
    by_candidate = select(Candidate.id).where(getattr(Candidate, column) == value)
    return set(session.scalars(by_cv.union(by_candidate)))


def _distinct_names(names) -> int:
    """How many different people the names describe, treating similar names as one."""
    groups: list[str] = []
    for name in names:
        if name and not any(similar_names(name, seen) for seen in groups):
            groups.append(name)
    return len(groups)


def is_busy_contact(session: Session, column: str, value: str, new_name: Optional[str]) -> bool:
    names = list(session.scalars(
        select(CvDocument.extracted["full_name"].astext).where(getattr(CvDocument, column) == value)))
    names += list(session.scalars(select(Candidate.full_name).where(getattr(Candidate, column) == value)))
    return _distinct_names([*names, new_name]) >= BUSY_CONTACT_NAMES


def marked_different_people(session: Session, first: int, second: int) -> bool:
    pair = or_(and_(ReviewFlag.candidate_id == first, ReviewFlag.other_candidate_id == second),
               and_(ReviewFlag.candidate_id == second, ReviewFlag.other_candidate_id == first))
    return session.scalar(select(ReviewFlag.id).where(pair, ReviewFlag.resolution == "different_people")
                          .limit(1)) is not None


def _passports_of(session: Session, candidate_id: int) -> set[str]:
    by_cv = select(CvDocument.passport_norm).where(CvDocument.candidate_id == candidate_id)
    by_candidate = select(Candidate.passport_norm).where(Candidate.id == candidate_id)
    return {value for value in session.scalars(by_cv.union(by_candidate)) if value}


def find_match(session: Session, *, passport: Optional[str], email: Optional[str], phone: Optional[str],
               name: Optional[str]) -> Match:
    """Keys are already normalised (fields.passport_key, email_key, phone_key)."""
    if passport:
        same_passport = _candidate_ids_with(session, "passport_norm", passport)
        if same_passport:
            candidate = session.get(Candidate, min(same_passport))
            differs = bool(name and candidate.full_name) and not similar_names(name, candidate.full_name)
            return Match(candidate_id=candidate.id, name_differs=differs)

    return Match(duplicates=_as_duplicates(_contact_reasons(session, passport, email, phone, name)))


def _contact_reasons(session: Session, passport: Optional[str], email: Optional[str], phone: Optional[str],
                     name: Optional[str], exclude_id: Optional[int] = None) -> dict[int, list[str]]:
    """Rules 2 and 3: other candidates sharing an email or phone."""
    reasons: dict[int, list[str]] = {}
    for column, value, label in (("email_norm", email, "email"), ("phone_norm", phone, "phone")):
        if not value or is_busy_contact(session, column, value, name):
            continue
        for other_id in sorted(_candidate_ids_with(session, column, value) - {exclude_id}):
            other = session.get(Candidate, other_id)
            other_passports = _passports_of(session, other_id)
            if passport and other_passports and passport not in other_passports:
                reasons.setdefault(other_id, []).append(f"different passport numbers, same {label}")
            elif not (passport and passport in other_passports) and similar_names(name, other.full_name):
                reasons.setdefault(other_id, []).append(f"same {label} and a similar name, passport missing")
    return reasons


def _as_duplicates(reasons: dict[int, list[str]]) -> list[PossibleDuplicate]:
    return [PossibleDuplicate(other_id, "; ".join(dict.fromkeys(why))) for other_id, why in reasons.items()]


def duplicates_after_edit(session: Session, candidate: Candidate) -> list[PossibleDuplicate]:
    """A hand-edited passport, email or phone is checked against everyone else.

    An edit never merges people automatically: even the same passport only raises a flag.
    """
    reasons: dict[int, list[str]] = {}
    if candidate.passport_norm:
        for other_id in sorted(_candidate_ids_with(session, "passport_norm", candidate.passport_norm)
                               - {candidate.id}):
            reasons.setdefault(other_id, []).append("same passport number")
    own_passports = _passports_of(session, candidate.id)
    contact = _contact_reasons(session, candidate.passport_norm, candidate.email_norm, candidate.phone_norm,
                               candidate.full_name, exclude_id=candidate.id)
    for other_id, why in contact.items():
        # Their own older CVs may hold another passport, so only say "different" when none is shared.
        if not own_passports & _passports_of(session, other_id):
            reasons.setdefault(other_id, []).extend(why)
    return _as_duplicates(reasons)
