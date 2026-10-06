"""
STEP 4b - Job fit: shortlisted CV + JD -> LLM -> score, matched/missing criteria, reasoning.

For each shortlisted candidate:
  1. Get the full CV text (cached Markdown from Step 2; re-converted if missing;
     vision fallback for scans).
  2. Send JD + the numbered requirements (rubric) + candidate profile + CV text.
     Instructor returns a validated JobFitLLM: one met/partial/not_met assessment per
     requirement with evidence, a holistic score, strengths, gaps, reasoning, questions.
  3. Python computes the final score - transparent and reproducible:

        Criteria Score = weighted % of requirements met
                         (must-have weight 2, nice-to-have weight 1;
                          met = 1, partial = 0.5, not met = 0)
        Fit Score      = 0.6 x Criteria Score + 0.4 x LLM holistic score

     Recommendation from Fit Score: >=80 Strong Fit, >=65 Good Fit, >=45 Partial Fit,
     else Not a Fit.  A candidate missing any must-have can't be "Strong Fit".

Results are cached in jobfit_results.json per CV hash; re-running only scores new
shortlisted candidates (unless --force, or the requirements changed).
Candidates are scored in parallel threads (JOBFIT_WORKERS) - LLM calls are I/O-bound.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Optional

import pandas as pd

from app.core.config import settings
from app.core.logger import get_logger
from app.core.models import JobFitLLM, JobRequirements
from app.extractor import converter
from app.jobfit.requirements import generate_requirements
from app.jobs.job_store import Job
from app.llm.client import get_llm
from app.shortlist.shortlister import load_shortlist
from app.storage.excel_store import write_sheets

log = get_logger("jobfit")

WEIGHT = {"must": 2.0, "nice": 1.0}
VALUE = {"met": 1.0, "partial": 0.5, "not_met": 0.0}
CRITERIA_SHARE = 0.6           # Fit Score = 60% rubric + 40% LLM holistic

ProgressFn = Callable[[int, int, str], None]

SYSTEM = """You are a senior technical recruiter evaluating ONE candidate against ONE job.
Be objective and evidence-based: use ONLY the job description and the CV provided - no outside
assumptions about the role or the candidate. Only credit what the CV actually shows.
- Assess EVERY numbered requirement, in the same order, copying its text exactly into `criterion`.
- 'met' = clear evidence; 'partial' = related/transferable skill or less than required level/years;
  'not_met' = no evidence. Absence from the CV counts as not_met.
- Experience requirements: compare against the candidate's actual relevant years.
- overall_score: holistic 0-100 for this specific role (responsibilities, seniority, domain), not just keyword overlap.
- Do not consider age, gender, religion, ethnicity, marital status or other protected attributes."""


def recommendation(score: float, must_missing: int) -> str:
    if score >= 80 and must_missing == 0:
        return "Strong Fit"
    if score >= 65:
        return "Good Fit"
    if score >= 45:
        return "Partial Fit"
    return "Not a Fit"


def _norm(s: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in s).split())


def score_candidate(fit: JobFitLLM, req: JobRequirements) -> dict:
    """Align the LLM's assessments to our rubric and compute the deterministic scores."""
    by_name = {_norm(a.criterion): a for a in fit.assessments}
    rows, total_w, got = [], 0.0, 0.0
    for i, r in enumerate(req.requirements):
        a = by_name.get(_norm(r.criterion))
        if a is None and i < len(fit.assessments) and len(fit.assessments) == len(req.requirements):
            a = fit.assessments[i]                         # same order, slightly reworded
        status = a.status if a else "not_met"
        evidence = a.evidence if a else "Not assessed by LLM"
        w = WEIGHT[r.importance]
        total_w += w
        got += w * VALUE[status]
        rows.append({"criterion": r.criterion, "importance": r.importance, "category": r.category,
                     "status": status, "evidence": evidence})
    criteria_score = round(100 * got / total_w, 1) if total_w else 0.0
    fit_score = round(CRITERIA_SHARE * criteria_score + (1 - CRITERIA_SHARE) * fit.overall_score)
    must_missing = [x["criterion"] for x in rows if x["importance"] == "must" and x["status"] == "not_met"]
    return {
        "fit_score": fit_score,
        "criteria_score": criteria_score,
        "llm_score": fit.overall_score,
        "recommendation": recommendation(fit_score, len(must_missing)),
        "must_missing": must_missing,
        "matched": [x["criterion"] for x in rows if x["status"] == "met"],
        "partial": [x["criterion"] for x in rows if x["status"] == "partial"],
        "missing": [x["criterion"] for x in rows if x["status"] == "not_met"],
        "assessments": rows,
        "strengths": fit.strengths,
        "gaps": fit.gaps,
        "reasoning": fit.reasoning,
        "interview_questions": fit.interview_questions,
    }


# ------------------------------------------------------------------ CV text
def _cv_text_and_file(row: pd.Series):
    """
    Returns (cv_text, file_or_None) for the job-fit prompt - same rules as extraction:
    cached text from Step 2 if we have it, otherwise CV_PARSER decides
    (CV_PARSER=llm: PDF/image CVs are attached so the LLM reads the original file).
    """
    file_hash = str(row.get("File Hash", ""))
    cache = settings.state_folder / "text_cache" / f"{file_hash}.md"
    if cache.exists():
        text = cache.read_text(encoding="utf-8")
        if text.strip():
            return text, None
    path = settings.cv_folder / str(row.get("CV File", ""))
    if not path.exists():
        raise FileNotFoundError(f"CV file not found: {path.name}")
    text, file, _ = converter.prepare_for_llm(path)
    return text, file


def _profile(row: pd.Series) -> str:
    keys = ["Full Name", "Experience (Years)", "Current/Last Job Title", "Current/Last Company",
            "Skills", "Education", "Certifications", "Languages", "Location"]
    lines = []
    for k in keys:
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and math.isnan(v)) and str(v).strip():
            lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _build_prompt(job: Job, req: JobRequirements, row: pd.Series, cv_text: str) -> str:
    rubric = "\n".join(f"{i}. [{r.importance.upper()}] ({r.category}) {r.criterion}"
                       for i, r in enumerate(req.requirements, start=1))
    return f"""=== JOB: {req.job_title} ===
{job.jd_text[:15000]}

=== REQUIREMENTS TO ASSESS (in this order) ===
{rubric}

=== CANDIDATE PROFILE (auto-extracted, may be incomplete) ===
{_profile(row)}

=== FULL CV ===
{cv_text[:40000] if cv_text else '(the CV is the attached document)'}"""


# ------------------------------------------------------------------ main entry
def _rubric_hash(req: JobRequirements) -> str:
    return hashlib.sha256(req.model_dump_json().encode()).hexdigest()[:16]


def run_jobfit(slug: str, provider: Optional[str] = None, force: bool = False,
               workers: Optional[int] = None, limit: Optional[int] = None,
               progress: Optional[ProgressFn] = None) -> dict:
    job = Job(slug).require()
    req = generate_requirements(slug, provider)            # cached after first time
    shortlist = load_shortlist(slug)
    if shortlist.empty:
        raise ValueError("Shortlist is empty - relax the criteria and run shortlist again")

    provider_name = (provider or settings.llm_provider).lower()
    llm = get_llm(provider_name)
    rhash = _rubric_hash(req)

    cache = json.loads(job.jobfit_json.read_text(encoding="utf-8")) if job.jobfit_json.exists() else {}
    if cache.get("rubric_hash") != rhash or force:
        cache = {"rubric_hash": rhash, "results": {}}
    results: dict = cache["results"]
    lock = threading.Lock()

    todo = [row for _, row in shortlist.iterrows() if str(row["File Hash"]) not in results]
    if limit is not None:
        todo = todo[:limit]
    skipped = len(shortlist) - len(todo)
    log.info("Job fit '%s': %d to score, %d cached, rubric of %d requirements, LLM=%s",
             slug, len(todo), skipped, len(req.requirements), provider_name)

    errors: dict[str, str] = {}
    done = 0

    def work(row: pd.Series) -> tuple[str, dict]:
        t0 = time.time()
        cv_text, file = _cv_text_and_file(row)
        fit = llm.extract(JobFitLLM, SYSTEM, text=_build_prompt(job, req, row, cv_text), file=file)
        scored = score_candidate(fit, req)
        scored.update({
            "name": None if pd.isna(row.get("Full Name")) else row.get("Full Name"),
            "cv_file": row.get("CV File"), "llm": provider_name,
            "scored_at": datetime.now().isoformat(timespec="seconds"),
            "seconds": round(time.time() - t0, 1),
        })
        return str(row["File Hash"]), scored

    def save():
        job.jobfit_json.write_text(json.dumps(cache, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=max(1, workers or settings.jobfit_workers)) as pool:
        futures = {pool.submit(work, row): row for row in todo}
        for fut in as_completed(futures):
            row = futures[fut]
            label = row.get("Full Name") if not pd.isna(row.get("Full Name")) else row.get("CV File")
            try:
                h, scored = fut.result()
                with lock:
                    results[h] = scored
                    save()                                   # progress survives a crash
                log.info("  %-28s fit=%3d  %s", label, scored["fit_score"], scored["recommendation"])
            except Exception as e:  # noqa: BLE001
                errors[str(row.get("CV File"))] = str(e)
                log.error("  FAIL %s: %s", label, e)
            done += 1
            if progress:
                progress(done, len(todo), str(label))

    save()
    out = write_jobfit_excel(job, req, shortlist, results)
    summary = {"job": slug, "shortlisted": len(shortlist), "scored_now": len(todo) - len(errors),
               "from_cache": skipped, "failed": len(errors), "errors": errors,
               "file": str(out), "at": datetime.now().isoformat(timespec="seconds")}
    job.save_meta(last_jobfit={k: v for k, v in summary.items() if k != "errors"})
    return summary


def ranked_results(slug: str) -> list[dict]:
    """Results for the current shortlist, best first (used by the API)."""
    job = Job(slug).require()
    if not job.jobfit_json.exists():
        return []
    results = json.loads(job.jobfit_json.read_text(encoding="utf-8")).get("results", {})
    try:
        current = set(load_shortlist(slug)["File Hash"].astype(str))
    except ValueError:
        current = set(results)
    out = [dict(v, file_hash=h) for h, v in results.items() if h in current]
    return sorted(out, key=lambda r: (-r["fit_score"], -r["criteria_score"]))


def write_jobfit_excel(job: Job, req: JobRequirements, shortlist: pd.DataFrame, results: dict):
    by_hash = shortlist.set_index(shortlist["File Hash"].astype(str))
    ranking, detail = [], []
    for h, r in results.items():
        if h not in by_hash.index:
            continue                                         # no longer shortlisted
        row = by_hash.loc[h]
        ranking.append({
            "Full Name": row.get("Full Name"), "Fit Score": r["fit_score"], "Recommendation": r["recommendation"],
            "Criteria Score": r["criteria_score"], "LLM Score": r["llm_score"],
            "Must-haves Missing": ", ".join(r["must_missing"]),
            "Matched": ", ".join(r["matched"]), "Partial": ", ".join(r["partial"]), "Missing": ", ".join(r["missing"]),
            "Strengths": "; ".join(r["strengths"]), "Gaps": "; ".join(r["gaps"]), "Reasoning": r["reasoning"],
            "Interview Questions": "\n".join(f"{i}. {q}" for i, q in enumerate(r["interview_questions"], 1)),
            "Email": row.get("Email"), "Phone": row.get("Phone"),
            "Experience (Years)": row.get("Experience (Years)"), "Location": row.get("Location"),
            "Current/Last Job Title": row.get("Current/Last Job Title"),
            "CV File": row.get("CV File"), "LLM": r.get("llm"), "Scored At": r.get("scored_at"), "File Hash": h,
        })
        for a in r["assessments"]:
            detail.append({"Full Name": row.get("Full Name"), "Requirement": a["criterion"],
                           "Importance": a["importance"], "Category": a["category"],
                           "Status": a["status"], "Evidence": a["evidence"], "File Hash": h})

    rank_df = pd.DataFrame(ranking)
    if not rank_df.empty:
        rank_df = rank_df.sort_values(["Fit Score", "Criteria Score"], ascending=False)
    rank_df.insert(0, "Rank", range(1, len(rank_df) + 1))
    req_df = pd.DataFrame([r.model_dump() for r in req.requirements])
    write_sheets(job.jobfit_xlsx, {"Ranking": rank_df, "Criteria Detail": pd.DataFrame(detail), "Requirements": req_df},
                 widths={"Rank": 6, "Fit Score": 9, "Recommendation": 14, "Criteria Score": 9, "LLM Score": 9,
                         "Must-haves Missing": 30, "Matched": 40, "Partial": 30, "Missing": 30, "Strengths": 50,
                         "Gaps": 50, "Reasoning": 70, "Interview Questions": 60, "Requirement": 40,
                         "Evidence": 70, "criterion": 40})
    log.info("Job fit written -> %s", job.jobfit_xlsx)
    return job.jobfit_xlsx
