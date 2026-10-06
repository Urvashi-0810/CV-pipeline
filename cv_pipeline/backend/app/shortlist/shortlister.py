"""
STEP 3 - Rule-based shortlisting (no LLM, free and instant).

Reads data/output/candidates.xlsx, applies the job's criteria.json, and writes
data/jobs/<slug>/shortlist.xlsx with three sheets:
    Shortlisted  - passed every rule, ranked (most skills matched, then most experience)
    Rejected     - with a "Reasons" column saying exactly which rules failed
    Criteria     - the rules that were used (audit trail)

Why rules first and the LLM second? Rules are cheap and transparent; the LLM
job-fit (Step 4) costs money per CV, so we only send it the candidates that pass.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

import pandas as pd

from app.core.logger import get_logger
from app.core.models import ShortlistCriteria
from app.jobs.job_store import Job
from app.storage.excel_store import load_candidates, write_sheets

log = get_logger("shortlist")

# Common alternative spellings. Matching "postgresql" also accepts "postgres", etc.
# Add your own: every word in a group counts as the same skill.
SKILL_ALIASES: list[set[str]] = [
    {"javascript", "js", "ecmascript"},
    {"typescript", "ts"},
    {"kubernetes", "k8s"},
    {"postgresql", "postgres"},
    {"machine learning", "ml"},
    {"artificial intelligence", "ai"},
    {"natural language processing", "nlp"},
    {"react", "reactjs", "react.js"},
    {"node", "nodejs", "node.js"},
    {"amazon web services", "aws"},
    {"google cloud", "gcp", "google cloud platform"},
    {"microsoft azure", "azure"},
    {"ci/cd", "cicd", "continuous integration"},
    {"golang", "go"},
    {"c#", "csharp"},
    {"power bi", "powerbi"},
    {"ms excel", "excel", "microsoft excel"},
]


# ------------------------------------------------------------------ text helpers
def _s(v) -> str:
    """Excel cell -> clean lowercase string ('' for empty / NaN)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip().lower()


def _num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=2048)
def _pattern(term: str) -> re.Pattern:
    """Whole-word pattern that also respects + # . so 'c' != 'c++' and 'java' != 'javascript'."""
    return re.compile(r"(?<![\w+#])" + re.escape(term.lower().strip()) + r"(?![\w+#])")


def _variants(term: str) -> set[str]:
    t = term.lower().strip()
    for group in SKILL_ALIASES:
        if t in group:
            return group
    return {t}


def has_term(haystack: str, term: str) -> bool:
    return any(_pattern(v).search(haystack) for v in _variants(term))


# ------------------------------------------------------------------ evaluation
@dataclass
class RowResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)


def evaluate(row: pd.Series, c: ShortlistCriteria) -> RowResult:
    reasons: list[str] = []
    matched: list[str] = []

    # --- experience
    exp = _num(row.get("Experience (Years)"))
    if c.min_experience is not None or c.max_experience is not None:
        if exp is None:
            if not c.keep_unknown_experience:
                reasons.append("experience unknown")
        else:
            if c.min_experience is not None and exp < c.min_experience:
                reasons.append(f"experience {exp:g}y < min {c.min_experience:g}y")
            if c.max_experience is not None and exp > c.max_experience:
                reasons.append(f"experience {exp:g}y > max {c.max_experience:g}y")

    # --- age
    age = _num(row.get("Age"))
    if c.min_age is not None or c.max_age is not None:
        if age is None:
            if not c.keep_unknown_age:
                reasons.append("age unknown")
        else:
            if c.min_age is not None and age < c.min_age:
                reasons.append(f"age {age:g} < min {c.min_age}")
            if c.max_age is not None and age > c.max_age:
                reasons.append(f"age {age:g} > max {c.max_age}")

    # --- skills (searched in skills + certifications + job title + summary)
    skill_text = " | ".join(_s(row.get(k)) for k in ("Skills", "Certifications", "Current/Last Job Title", "Summary"))
    missing_req = [s for s in c.required_skills if not has_term(skill_text, s)]
    matched += [s for s in c.required_skills if s not in missing_req]
    if missing_req:
        reasons.append("missing required skills: " + ", ".join(missing_req))
    if c.any_skills:
        hits = [s for s in c.any_skills if has_term(skill_text, s)]
        matched += [h for h in hits if h not in matched]
        need = min(c.min_any_skills, len(c.any_skills))
        if len(hits) < need:
            reasons.append(f"only {len(hits)}/{need} of preferred skills ({', '.join(hits) or 'none'})")

    # --- location (substring, so 'pune' matches 'Pune, Maharashtra')
    if c.locations:
        loc = _s(row.get("Location"))
        if not loc:
            if not c.keep_unknown_location:
                reasons.append("location unknown")
        elif not any(l.lower().strip() in loc for l in c.locations):
            reasons.append(f"location '{row.get('Location')}' not in {c.locations}")

    # --- education / job title / languages
    if c.education_keywords and not any(has_term(_s(row.get("Education")), k) for k in c.education_keywords):
        reasons.append("education does not match " + str(c.education_keywords))
    if c.job_title_keywords and not any(k.lower() in _s(row.get("Current/Last Job Title")) for k in c.job_title_keywords):
        reasons.append("job title does not match " + str(c.job_title_keywords))
    langs = _s(row.get("Languages"))
    missing_lang = [l for l in c.languages if not has_term(langs, l)]
    if missing_lang:
        reasons.append("missing languages: " + ", ".join(missing_lang))

    # --- exclusions (anywhere in the row)
    if c.exclude_keywords:
        whole = " | ".join(_s(v) for v in row.values)
        hit = [k for k in c.exclude_keywords if has_term(whole, k)]
        if hit:
            reasons.append("excluded keyword: " + ", ".join(hit))

    return RowResult(passed=not reasons, reasons=reasons, matched_skills=matched)


# ------------------------------------------------------------------ main entry
def run_shortlist(slug: str, criteria: ShortlistCriteria | None = None) -> dict:
    job = Job(slug).require()
    c = criteria or job.load_criteria()
    df = load_candidates()
    if df.empty:
        raise ValueError("No candidates in Excel yet - run 'python main.py extract' first")

    results = [evaluate(row, c) for _, row in df.iterrows()]
    df = df.copy()
    df["Skills Matched"] = [len(r.matched_skills) for r in results]
    df["Matched Skill List"] = [", ".join(r.matched_skills) for r in results]
    df["Reasons"] = ["; ".join(r.reasons) for r in results]
    passed_mask = [r.passed for r in results]

    shortlisted = df[passed_mask].copy()
    rejected = df[[not p for p in passed_mask]].copy()

    shortlisted["_exp"] = pd.to_numeric(shortlisted["Experience (Years)"], errors="coerce").fillna(-1)
    shortlisted = shortlisted.sort_values(["Skills Matched", "_exp"], ascending=[False, False]).drop(columns="_exp")
    cut = 0
    if c.max_candidates is not None and len(shortlisted) > c.max_candidates:
        cut = len(shortlisted) - c.max_candidates
        extra = shortlisted.iloc[c.max_candidates:].copy()
        extra["Reasons"] = f"outside top {c.max_candidates}"
        rejected = pd.concat([rejected, extra])
        shortlisted = shortlisted.iloc[: c.max_candidates]
    shortlisted.insert(0, "Rank", range(1, len(shortlisted) + 1))
    shortlisted = shortlisted.drop(columns=["Reasons"])

    front = ["Full Name", "Reasons", "Experience (Years)", "Age", "Location", "Skills", "CV File", "File Hash"]
    rejected = rejected[[col for col in front if col in rejected] + [col for col in rejected.columns if col not in front]]

    crit_df = pd.DataFrame(
        [(k, ", ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v)))
         for k, v in c.model_dump().items()],
        columns=["Rule", "Value"],
    )
    write_sheets(job.shortlist_xlsx, {"Shortlisted": shortlisted, "Rejected": rejected, "Criteria": crit_df},
                 widths={"Reasons": 60, "Matched Skill List": 35, "Rule": 25, "Value": 60, "Rank": 6})

    summary = {
        "job": job.slug, "total": len(df), "shortlisted": len(shortlisted),
        "rejected": len(rejected), "cut_by_max_candidates": cut,
        "file": str(job.shortlist_xlsx), "at": datetime.now().isoformat(timespec="seconds"),
    }
    job.save_meta(last_shortlist=summary)
    log.info("Shortlist '%s': %d of %d candidates passed -> %s", job.slug, len(shortlisted), len(df), job.shortlist_xlsx.name)
    return summary


def load_shortlist(slug: str) -> pd.DataFrame:
    job = Job(slug).require()
    if not job.shortlist_xlsx.exists():
        raise ValueError(f"No shortlist for '{slug}' yet - run 'python main.py shortlist --job {slug}' first")
    return pd.read_excel(job.shortlist_xlsx, sheet_name="Shortlisted", dtype={"File Hash": str, "Phone": str})


def load_rejected(slug: str) -> pd.DataFrame:
    job = Job(slug).require()
    if not job.shortlist_xlsx.exists():
        return pd.DataFrame()
    return pd.read_excel(job.shortlist_xlsx, sheet_name="Rejected", dtype={"File Hash": str, "Phone": str})
