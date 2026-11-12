"""
Candidate fields and the rules for comparing them. Pure functions, no database.

"Extracted" values are what one CV said, kept as plain JSON in cv_documents.extracted.
"Column" values are the same values cleaned for the candidates table.
The comparison rules are written out in the requirements doc ("How the rules compare values").
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

TEXT_FIELDS = ("full_name", "email", "phone", "passport_number", "job_title", "company",
               "date_of_birth", "location", "summary", "education")
LIST_FIELDS = ("skills", "certifications", "languages")
NUMBER_FIELDS = ("experience_years", "age")
FIELDS = TEXT_FIELDS + LIST_FIELDS + NUMBER_FIELDS

# The LLM rewrites the summary every time, so a new summary never replaces a manual edit.
NEVER_REPLACES_AN_EDIT = {"summary"}

PASSPORT_PLACEHOLDERS = {
    "NA", "NIL", "NONE", "NULL", "UNKNOWN", "NOTMENTIONED", "NOTAVAILABLE", "NOTPROVIDED", "NOTGIVEN",
    "NOTAPPLICABLE", "APPLIED", "APPLIEDFOR", "PENDING", "NO", "NOPASSPORT", "XXXXXX", "TBA", "TBD",
}
MIN_PASSPORT_LENGTH = 6
MIN_PHONE_DIGITS = 7


# ---------- cleaning values ----------

def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and value != value:     # NaN from pandas
        return None
    text = str(value).strip()
    return text or None


def _list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = (_text(value) or "").split(",")
    seen, out = set(), []
    for item in items:
        item = (_text(item) or "")
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _experience(value: Any) -> Optional[Decimal]:
    try:
        number = Decimal(_text(value) or "x")
    except InvalidOperation:
        return None
    return number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if 0 <= number < 1000 else None


def _age(value: Any) -> Optional[int]:
    try:
        number = float(_text(value) or "x")
    except ValueError:
        return None
    return int(number) if 0 <= number < 150 else None


def to_column(field: str, value: Any) -> Any:
    """One extracted value, cleaned for the candidates table."""
    if field in LIST_FIELDS:
        return _list(value)
    if field == "experience_years":
        return _experience(value)
    if field == "age":
        return _age(value)
    return _text(value)


def columns(extracted: dict) -> dict:
    return {field: to_column(field, extracted.get(field)) for field in FIELDS}


def is_blank(value: Any) -> bool:
    return value is None or value == [] or (isinstance(value, str) and not value.strip())


def display(value: Any) -> Optional[str]:
    """A value as text, for edit history and review flags."""
    if is_blank(value):
        return None
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def birth_date(value: Any) -> Optional[date]:
    """A full YYYY-MM-DD date of birth, or None (partial or impossible dates are left as text)."""
    try:
        return date.fromisoformat((_text(value) or "")[:10])
    except ValueError:
        return None


def current_age(born: Optional[date], stored_age: Optional[int], today: Optional[date] = None) -> Optional[int]:
    """Age from the date of birth when it's known, so it never goes stale; else the age from the CV."""
    if born is None:
        return stored_age
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# ---------- "did this field change?" ----------

def _comparable(field: str, value: Any) -> Any:
    value = to_column(field, value)
    if field in LIST_FIELDS:
        return frozenset(" ".join(item.lower().split()) for item in value)
    if isinstance(value, str):
        return " ".join(value.lower().split())
    return value


def same_value(field: str, first: Any, second: Any) -> bool:
    """Ignores case, spacing and the order of list items."""
    return _comparable(field, first) == _comparable(field, second)


# ---------- matching keys ----------

def passport_key(value: Any) -> Optional[str]:
    """Spaces and dashes removed, uppercased. Placeholders and short values count as no passport."""
    text = _text(value)
    if not text:
        return None
    key = re.sub(r"[\s\-]+", "", text).upper()
    letters_and_digits = re.sub(r"[^A-Z0-9]", "", key)
    if (len(letters_and_digits) < MIN_PASSPORT_LENGTH or letters_and_digits in PASSPORT_PLACEHOLDERS
            or set(letters_and_digits) == {"0"}):
        return None
    return key


def email_key(value: Any) -> Optional[str]:
    text = re.sub(r"\s+", "", _text(value) or "").lower()
    return text if "@" in text else None


def phone_key(value: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", _text(value) or "")
    return digits[-10:] if len(digits) >= MIN_PHONE_DIGITS else None


def name_words(value: Any) -> frozenset[str]:
    return frozenset(re.sub(r"[^\w]+", " ", (_text(value) or "").lower().replace("_", " ")).split())


def similar_names(first: Any, second: Any) -> bool:
    """Every word of the shorter name is in the longer one, and the shorter has 2+ words (or they're equal)."""
    a, b = name_words(first), name_words(second)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 2 and shorter <= longer
