"""Step 3: rule evaluation + shortlist file."""
import pandas as pd

from app.core.models import ShortlistCriteria
from app.jobs.job_store import create_job
from app.shortlist.shortlister import evaluate, has_term, load_rejected, load_shortlist, run_shortlist
from conftest import seed_candidates

JD = "We need a Python developer with Django and AWS. 3+ years. Kubernetes is a plus. Location Pune."


def test_skill_word_matching():
    assert has_term("python, javascript, c++", "c++")
    assert not has_term("javascript", "java")            # Java != JavaScript
    assert not has_term("c++, c#", "c")                  # C != C++
    assert has_term("postgres, docker", "PostgreSQL")    # alias
    assert has_term("node.js, react", "nodejs")          # alias
    assert has_term("b.tech computer science", "B.Tech")


def row(**k):
    base = {"Full Name": "X", "Skills": "", "Experience (Years)": None, "Age": None, "Location": None,
            "Education": "", "Current/Last Job Title": "", "Languages": "", "Certifications": "", "Summary": ""}
    base.update(k)
    return pd.Series(base)


def test_rules_and_reasons():
    c = ShortlistCriteria(min_experience=3, max_age=35, required_skills=["Python"],
                          any_skills=["Django", "Flask", "FastAPI"], min_any_skills=1,
                          locations=["Pune", "Mumbai"], exclude_keywords=["intern"])
    ok = evaluate(row(Skills="Python, Django", **{"Experience (Years)": 5, "Age": 30, "Location": "Pune, MH"}), c)
    assert ok.passed and set(ok.matched_skills) == {"Python", "Django"}

    bad = evaluate(row(Skills="Java", **{"Experience (Years)": 1, "Age": 40, "Location": "Delhi"}), c)
    assert not bad.passed
    text = "; ".join(bad.reasons)
    for expected in ("experience 1y < min 3y", "age 40 > max 35", "missing required skills: Python",
                     "preferred skills", "location 'Delhi'"):
        assert expected in text, text

    # unknown age kept by default, unknown experience rejected by default
    assert evaluate(row(Skills="Python Flask", **{"Experience (Years)": 4, "Location": "Pune"}), c).passed
    assert "experience unknown" in evaluate(row(Skills="Python Flask", Location="Pune"), c).reasons


def test_run_shortlist_writes_ranked_file(clean_data):
    seed_candidates([
        {"Full Name": "A", "Skills": "Python, Django, AWS", "Experience (Years)": 5, "Location": "Pune"},
        {"Full Name": "B", "Skills": "Python, Django", "Experience (Years)": 8, "Location": "Pune"},
        {"Full Name": "C", "Skills": "Java, Spring", "Experience (Years)": 6, "Location": "Pune"},
        {"Full Name": "D", "Skills": "Python, Django, AWS", "Experience (Years)": 1, "Location": "Pune"},
    ])
    job = create_job("Python Dev", jd_text=JD)
    job.save_criteria(ShortlistCriteria(min_experience=2, any_skills=["Python", "Django", "AWS"], min_any_skills=2))
    s = run_shortlist(job.slug)
    assert s["total"] == 4 and s["shortlisted"] == 2
    sl = load_shortlist(job.slug)
    assert list(sl["Full Name"]) == ["A", "B"]           # A matched 3 skills, B 2
    assert list(sl["Rank"]) == [1, 2]
    rej = load_rejected(job.slug)
    assert set(rej["Full Name"]) == {"C", "D"} and rej["Reasons"].str.len().min() > 0

    job.save_criteria(ShortlistCriteria(any_skills=["Python"], max_candidates=1))
    s = run_shortlist(job.slug)
    assert s["shortlisted"] == 1 and s["cut_by_max_candidates"] == 2
