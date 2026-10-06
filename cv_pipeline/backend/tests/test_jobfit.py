"""Step 4: JD -> requirements -> suggested criteria -> shortlist -> job fit (LLM mocked)."""
import json

import pandas as pd

import mock_llm
from app.core.models import CriterionAssessment, JobFitLLM, JobRequirements
from app.jobfit.jobfit import ranked_results, recommendation, run_jobfit, score_candidate
from app.jobfit.requirements import prepare_job
from app.jobs.job_store import Job, create_job
from app.shortlist.shortlister import run_shortlist
from conftest import seed_candidates

mock_llm.start()
JD = "Python developer with Django and AWS, 3+ years backend. Kubernetes nice to have."


def test_score_math():
    req = JobRequirements.model_validate(mock_llm.REQUIREMENTS)   # 4 must (w=2) + 1 nice (w=1) = 9
    fit = JobFitLLM(assessments=[
        CriterionAssessment(criterion="Python", status="met", evidence=""),
        CriterionAssessment(criterion="django", status="partial", evidence=""),   # case-insensitive align
        CriterionAssessment(criterion="AWS", status="not_met", evidence=""),
        CriterionAssessment(criterion="Kubernetes", status="met", evidence=""),
        # experience requirement missing from LLM output -> counted as not_met
    ], overall_score=80, reasoning="r")
    s = score_candidate(fit, req)
    # (2*1 + 2*0.5 + 2*0 + 1*1 + 2*0) / 9 = 4/9 = 44.4
    assert s["criteria_score"] == 44.4
    assert s["fit_score"] == round(0.6 * 44.4 + 0.4 * 80)
    assert s["must_missing"] == ["AWS", "3+ years backend"]
    assert recommendation(85, 1) == "Good Fit" and recommendation(85, 0) == "Strong Fit"


def test_end_to_end(clean_data):
    seed_candidates([
        {"Full Name": "Full Match", "Skills": "Python, Django, AWS, Kubernetes", "Experience (Years)": 6},
        {"Full Name": "Half Match", "Skills": "Python, Flask", "Experience (Years)": 4},
        {"Full Name": "No Match", "Skills": "Photoshop, Illustrator", "Experience (Years)": 5},
    ])
    job = create_job("Python Developer", jd_text=JD)

    r = prepare_job(job.slug)                            # LLM structures the JD
    assert r["criteria_written"] and len(r["requirements"]["requirements"]) == 5   # 3 invented items dropped
    assert {d["criterion"] for d in r["dropped_not_in_jd"]} >= {"Docker", "Flask", "Strong communication skills"}
    assert r["requirements"]["location"] is None         # LLM said Bangalore, JD doesn't
    c = job.load_criteria()                              # criteria = JD values exactly
    assert c.min_experience == 3 and c.required_skills == ["Python", "Django", "AWS"] and not c.any_skills

    c.required_skills = ["Python"]                       # HR relaxes the rule
    job.save_criteria(c)
    assert prepare_job(job.slug)["criteria_written"] is False   # customised criteria are never overwritten

    assert run_shortlist(job.slug)["shortlisted"] == 2  # "No Match" filtered out by rules

    progress = []
    s = run_jobfit(job.slug, progress=lambda d, t, cur: progress.append((d, t)))
    assert s["scored_now"] == 2 and s["failed"] == 0 and progress[-1] == (2, 2)
    ranked = ranked_results(job.slug)
    assert [x["name"] for x in ranked] == ["Full Match", "Half Match"]
    assert ranked[0]["recommendation"] == "Strong Fit" and ranked[0]["must_missing"] == []
    assert "Django" in ranked[1]["must_missing"]

    xl = pd.read_excel(job.jobfit_xlsx, sheet_name=None)
    assert set(xl) == {"Ranking", "Criteria Detail", "Requirements"}
    assert list(xl["Ranking"]["Full Name"]) == ["Full Match", "Half Match"]
    assert len(xl["Criteria Detail"]) == 10              # 2 candidates x 5 requirements

    # re-run: everything cached, no LLM calls
    mock_llm.SENT.clear()
    s = run_jobfit(job.slug)
    assert s["scored_now"] == 0 and s["from_cache"] == 2 and not mock_llm.SENT

    # force with the other provider re-scores
    s = run_jobfit(job.slug, provider="gemini", force=True)
    assert s["scored_now"] == 2 and all(x["llm"] == "gemini" for x in ranked_results(job.slug))

    # editing the rubric invalidates the cache automatically
    req = job.load_requirements()
    req.requirements = req.requirements[:3]
    job.save_requirements(req)
    assert run_jobfit(job.slug)["scored_now"] == 2
    cache = json.loads(Job(job.slug).jobfit_json.read_text())
    assert all(len(v["assessments"]) == 3 for v in cache["results"].values())
