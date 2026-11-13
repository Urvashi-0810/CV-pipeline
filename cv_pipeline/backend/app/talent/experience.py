"""
The client's experience and age columns, calculated by code from the work history the AI lists.

- Experience is counted in whole months, from the start month to the end month inclusive.
  "present" ends in the current month. Overlapping jobs are merged, so no month counts twice.
- A year on its own runs from January (start) to December (end): "2015 - 2018" is 4 years.
- A job with no dates isn't counted.
- "Not found": the CV should say it but doesn't (no dated jobs at all, no date of birth...).
- "-": doesn't apply (the candidate has dated jobs, but none in India, the Gulf, or that sector).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Optional

NOT_FOUND = "Not found"
NOT_APPLICABLE = "-"

# Countries on the Persian Gulf, with the names CVs use for them.
GULF = {
    "united arab emirates", "uae", "u.a.e", "u.a.e.", "dubai", "abu dhabi", "sharjah", "ajman", "fujairah",
    "ras al khaimah", "umm al quwain", "saudi arabia", "ksa", "k.s.a", "kingdom of saudi arabia", "saudi",
    "qatar", "kuwait", "oman", "sultanate of oman", "bahrain", "kingdom of bahrain", "iraq", "iran",
}
INDIA = {"india", "bharat"}

SECTOR_COLUMNS = {
    "facility_management": "Facility Management Experience",
    "oil_gas": "Oil & Gas",
    "construction": "Construction",
    "marine_shipyard": "Marine/Shipyard",
    "healthcare": "Healthcare",
}
OTHER_COLUMN = "Other Relevant Sector Experience"
COLUMNS = ["Indian Experience", "Gulf Experience", "Total Experience", *SECTOR_COLUMNS.values(), OTHER_COLUMN]


def _month(value: Any, today: date, *, is_end: bool) -> Optional[int]:
    """A date as a month number (year*12 + month), or None when there's no usable date.

    A year on its own covers the whole year: January as a start, December as an end,
    so "2015 - 2018" counts as 4 years.
    """
    text = str(value or "").strip().lower()
    if text in ("present", "current", "till date", "till now", "ongoing", "now"):
        return today.year * 12 + today.month - 1
    match = re.match(r"^(\d{4})-(\d{1,2})", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        return year * 12 + month - 1 if 1 <= month <= 12 else None
    match = re.fullmatch(r"(\d{4})", text)
    if match:
        return int(match.group(1)) * 12 + (11 if is_end else 0)
    return None


def _span(job: dict, today: date) -> Optional[tuple[int, int]]:
    start = _month(job.get("start_date"), today, is_end=False)
    end = _month(job.get("end_date"), today, is_end=True)
    if start is None or end is None or end < start:
        return None
    return start, min(end, today.year * 12 + today.month - 1)


def months_worked(spans: Iterable[tuple[int, int]]) -> int:
    """Months covered by any of the spans, each month counted once."""
    total, last = 0, None
    for start, end in sorted(spans):
        if last is not None and start <= last:
            start = last + 1
        if end >= start:
            total += end - start + 1
        last = end if last is None else max(last, end)
    return total


def duration(months: int) -> str:
    years, rest = divmod(months, 12)
    parts = []
    if years:
        parts.append(f"{years} yr{'s' if years != 1 else ''}")
    if rest or not years:
        parts.append(f"{rest} mth{'s' if rest != 1 else ''}")
    return " ".join(parts)


def _country(job: dict) -> str:
    return re.sub(r"\s+", " ", str(job.get("country") or "").strip().lower())


def experience_columns(work_history: list[dict], today: Optional[date] = None) -> dict[str, str]:
    """Every experience column for one candidate, as the client wants them written."""
    today = today or date.today()
    dated = [(job, span) for job in (work_history or []) if (span := _span(job, today))]
    if not dated:
        return {column: NOT_FOUND for column in COLUMNS}

    def total(predicate) -> str:
        spans = [span for job, span in dated if predicate(job)]
        return duration(months_worked(spans)) if spans else NOT_APPLICABLE

    out = {
        "Indian Experience": total(lambda job: _country(job) in INDIA),
        "Gulf Experience": total(lambda job: _country(job) in GULF),
        "Total Experience": total(lambda job: True),
    }
    for sector, column in SECTOR_COLUMNS.items():
        out[column] = total(lambda job, s=sector: job.get("sector") == s)

    others: dict[str, list] = {}
    for job, span in dated:
        if job.get("sector") not in SECTOR_COLUMNS:
            name = str(job.get("other_sector") or "").strip() or "Other"
            others.setdefault(name.title() if name.islower() else name, []).append(span)
    out[OTHER_COLUMN] = "; ".join(f"{name}: {duration(months_worked(spans))}"
                                  for name, spans in sorted(others.items())) or NOT_APPLICABLE
    return out


def total_years(work_history: list[dict], today: Optional[date] = None) -> Optional[float]:
    """Total experience as a number, for sorting and the experience filter."""
    today = today or date.today()
    spans = [span for job in (work_history or []) if (span := _span(job, today))]
    return round(months_worked(spans) / 12, 1) if spans else None


def age_text(born: Optional[date], stated_age: Optional[int], today: Optional[date] = None) -> str:
    """Age as of today in years and months, from the date of birth; else the age the CV states."""
    today = today or date.today()
    if born is None:
        return f"{stated_age} yrs" if stated_age else NOT_FOUND
    months = (today.year - born.year) * 12 + today.month - born.month - (1 if today.day < born.day else 0)
    return duration(max(months, 0))


def dmy(value: Any) -> Optional[str]:
    """YYYY-MM-DD as DD-MM-YYYY (the client's format). Anything else is returned as it is."""
    text = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else (text or None)
