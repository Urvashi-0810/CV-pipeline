"""
STEP 4a - Job description -> structured requirements (the scoring rubric).

STRICTLY GROUNDED IN THE JD. The LLM is only used to *read and structure* the JD
text you provided - it must not add skills, synonyms, "related" technologies or
industry knowledge. This is enforced twice:

  1. The prompt tells the LLM to copy, not infer, and to return for every
     requirement the exact `jd_quote` it came from.
  2. Python then checks every item against the JD text (`ground_requirements`):
       * the jd_quote must actually appear in the JD
       * the requirement's words must appear in the JD (skills: the exact skill name)
       * experience years / location / seniority must appear in the JD, else cleared
     Anything that fails is DROPPED and listed in job.json -> "requirements_dropped"
     (and printed), so you can see exactly what the LLM tried to add.

Nothing is fetched from the internet or any other source - only the JD text.

Done once per job, cached in data/jobs/<slug>/requirements.json. You can edit that
file by hand; every candidate is graded against it in Step 4.

`suggest_criteria()` turns the requirements into a starting criteria.json for Step 3,
again using only the JD's own values (no tolerances or extra keywords added).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from app.core.logger import get_logger
from app.core.models import JobRequirements, ShortlistCriteria
from app.jobs.job_store import Job
from app.llm.client import get_llm

log = get_logger("requirements")

SYSTEM = """You are a precise information extractor. You will receive ONE job description (JD).
Structure ONLY what is written in it.

STRICT RULES - the output is checked word-for-word against the JD and anything not found is discarded:
- Use ONLY information explicitly written in the JD. Do NOT add skills, tools, synonyms, related
  technologies, certifications or qualifications that the JD does not literally mention.
  (Example: if the JD says "Django", do not add "Python" unless "Python" is written too.)
- Use the JD's own wording. For category='skill', the criterion is ONLY the skill/tool name exactly
  as spelled in the JD. Split combined items ('Python/Django' -> 'Python' and 'Django').
- jd_quote: copy the exact phrase from the JD (character-for-character) that states the requirement.
- importance='must' if the JD presents it as required/mandatory/must/essential or lists it under
  requirements/qualifications without saying optional; 'nice' only if the JD says preferred/plus/
  good to have/bonus/desirable.
- Experience years, location and seniority: fill ONLY if the JD states them, otherwise null.
- If the JD has few requirements, return few. Never pad the list."""

STOPWORDS = {"and", "or", "the", "a", "an", "of", "in", "on", "for", "with", "to", "at", "as", "is", "be",
             "are", "by", "from", "years", "year", "experience", "least", "minimum", "plus", "strong",
             "good", "knowledge", "working", "hands", "proficiency", "proficient", "ability", "skills"}


# ------------------------------------------------------------------ grounding helpers
def _norm(text: str) -> str:
    """lowercase, unify punctuation/bullets/line-breaks, keep + # . / for tech names."""
    text = text.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9+#./' -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_jd(quote: str, jd_norm: str) -> bool:
    q = _norm(quote).strip(" .")
    if len(q) < 2:
        return False
    if q in jd_norm:
        return True
    # tolerate tiny differences (a dropped bullet/character) - 90% must match contiguously
    m = SequenceMatcher(None, jd_norm, q, autojunk=False).find_longest_match(0, len(jd_norm), 0, len(q))
    return m.size >= 0.9 * len(q)


def term_in_jd(term: str, jd_norm: str) -> bool:
    """Whole-word match, so 'Java' is not found inside 'JavaScript'."""
    t = _norm(term).strip(" .")
    return bool(t) and re.search(r"(?<![\w+#])" + re.escape(t) + r"(?![\w+#])", jd_norm) is not None


def words_in_jd(text: str, jd_norm: str, min_share: float = 0.8) -> bool:
    words = [w.strip("./") for w in _norm(text).split()]
    words = [w for w in words if w and w not in STOPWORDS and not w.isdigit() and len(w) > 1]
    if not words:
        return True
    found = sum(1 for w in words if term_in_jd(w, jd_norm))
    return found / len(words) >= min_share


def number_in_jd(value: Optional[float], jd_norm: str) -> bool:
    if value is None:
        return True
    n = int(value) if float(value).is_integer() else value
    return re.search(rf"(?<![\d.]){re.escape(str(n))}(?![\d])", jd_norm) is not None


def ground_requirements(req: JobRequirements, jd_text: str) -> tuple[JobRequirements, list[dict]]:
    """Keep only what is verifiably in the JD. Returns (cleaned requirements, dropped items)."""
    jd = _norm(jd_text)
    kept, dropped, seen = [], [], set()

    for r in req.requirements:
        reason = None
        if not quote_in_jd(r.jd_quote, jd):
            reason = "quote not found in JD"
        elif r.category == "skill" and not term_in_jd(r.criterion, jd):
            reason = "skill name not written in JD"
        elif r.category != "skill" and not words_in_jd(r.criterion, jd):
            reason = "wording not from JD"
        elif _norm(r.criterion) in seen:
            reason = "duplicate"
        if reason:
            dropped.append({"criterion": r.criterion, "category": r.category, "reason": reason, "jd_quote": r.jd_quote})
        else:
            seen.add(_norm(r.criterion))
            kept.append(r)

    clean = req.model_copy(update={"requirements": kept})
    for field in ("min_experience_years", "max_experience_years"):
        if not number_in_jd(getattr(clean, field), jd):
            dropped.append({"criterion": f"{field}={getattr(clean, field)}", "category": "experience",
                            "reason": "number not in JD"})
            clean = clean.model_copy(update={field: None})
    for field in ("location", "seniority"):
        v = getattr(clean, field)
        if v and not words_in_jd(v, jd, min_share=1.0):
            dropped.append({"criterion": f"{field}={v}", "category": field, "reason": "not in JD"})
            clean = clean.model_copy(update={field: None})
    return clean, dropped


# ------------------------------------------------------------------ main functions
def generate_requirements(slug: str, provider: Optional[str] = None, force: bool = False) -> JobRequirements:
    job = Job(slug).require()
    if not force:
        existing = job.load_requirements()
        if existing:
            log.info("Using cached requirements for '%s' (use --force to regenerate)", slug)
            return existing
    log.info("Reading requirements from the JD of '%s' (JD text only) ...", slug)
    jd_text = job.jd_text
    raw = get_llm(provider).extract(JobRequirements, SYSTEM, text=f"JOB DESCRIPTION:\n\n{jd_text[:30000]}")
    req, dropped = ground_requirements(raw, jd_text)
    if not req.requirements:
        raise ValueError("No requirements could be matched to the JD text - is the JD complete? "
                         "You can also write requirements.json by hand.")
    job.save_requirements(req)
    job.save_meta(requirements_dropped=dropped)
    for d in dropped:
        log.warning("  dropped (not in JD): %s  [%s]", d["criterion"], d["reason"])
    log.info("  %d requirements kept (%d must-have), %d dropped", len(req.requirements), len(req.must()), len(dropped))
    return req


def suggest_criteria(req: JobRequirements) -> ShortlistCriteria:
    """
    Shortlist rules taken straight from the JD - nothing added:
      * experience = the JD's own min/max years (no tolerance)
      * required_skills = the JD's must-have skills
    Nice-to-have skills are not used for filtering; they are scored in Step 4.
    If this is too strict for your CV pool, edit criteria.json (e.g. move skills to any_skills).
    """
    return ShortlistCriteria(
        min_experience=req.min_experience_years,
        max_experience=req.max_experience_years,
        keep_unknown_experience=True,
        required_skills=req.must("skill"),
    )


def criteria_is_default(job: Job) -> bool:
    return job.load_criteria() == ShortlistCriteria()


def prepare_job(slug: str, provider: Optional[str] = None, force: bool = False,
                overwrite_criteria: bool = False) -> dict:
    """Generate requirements and (if criteria.json is still empty) the JD-based criteria.json."""
    job = Job(slug).require()
    req = generate_requirements(slug, provider, force)
    wrote = False
    if overwrite_criteria or criteria_is_default(job):
        job.save_criteria(suggest_criteria(req))
        wrote = True
    return {"requirements": req.model_dump(), "dropped_not_in_jd": job.meta.get("requirements_dropped", []),
            "criteria_written": wrote, "criteria": job.load_criteria().model_dump()}
