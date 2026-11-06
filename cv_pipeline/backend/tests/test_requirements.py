"""JD grounding: requirements must come from the JD text only."""
from app.core.models import JobRequirements, Requirement
from app.jobfit.requirements import _norm, ground_requirements, quote_in_jd, suggest_criteria, term_in_jd

JD = """Senior Backend Engineer - Pune
Requirements:
• 4+ years of experience in backend development
• Strong Python and FastAPI
• Experience with PostgreSQL / Redis
Nice to have: exposure to Kubernetes."""


def R(criterion, quote, category="skill", importance="must"):
    return Requirement(criterion=criterion, jd_quote=quote, category=category, importance=importance)


def test_matching_helpers():
    jd = _norm(JD)
    assert quote_in_jd("4+ years of experience in backend development", jd)
    assert quote_in_jd("Strong   Python and\nFastAPI", jd)                       # whitespace differences ok
    assert not quote_in_jd("5+ years with Django", jd)
    assert term_in_jd("PostgreSQL", jd) and term_in_jd("redis", jd)
    assert not term_in_jd("Postgres", jd)                                         # synonyms are NOT accepted
    assert not term_in_jd("Java", _norm("JavaScript developer"))


def test_ground_requirements_drops_invented_items():
    raw = JobRequirements(
        job_title="Senior Backend Engineer", seniority="Senior", min_experience_years=4,
        max_experience_years=8,                                   # not in JD
        location="Mumbai",                                        # not in JD
        requirements=[
            R("Python", "Strong Python and FastAPI"),
            R("FastAPI", "Strong Python and FastAPI"),
            R("PostgreSQL", "Experience with PostgreSQL / Redis"),
            R("Redis", "Experience with PostgreSQL / Redis"),
            R("Kubernetes", "exposure to Kubernetes", importance="nice"),
            R("4+ years of backend development", "4+ years of experience in backend development", "experience"),
            R("Docker", "Experience with Docker"),                  # invented skill
            R("Django", "Strong Python and FastAPI"),              # real quote, but skill not in JD
            R("Python", "Strong Python and FastAPI"),              # duplicate
            R("Excellent communication skills", "Requirements:", "other"),   # wording not from JD
        ],
    )
    clean, dropped = ground_requirements(raw, JD)
    assert [r.criterion for r in clean.requirements] == [
        "Python", "FastAPI", "PostgreSQL", "Redis", "Kubernetes", "4+ years of backend development"]
    reasons = {d["criterion"]: d["reason"] for d in dropped}
    assert reasons["Docker"] == "quote not found in JD"
    assert reasons["Django"] == "skill name not written in JD"
    assert reasons["Python"] == "duplicate"
    assert reasons["Excellent communication skills"] == "wording not from JD"
    assert clean.min_experience_years == 4 and clean.max_experience_years is None
    assert clean.location is None and clean.seniority == "Senior"

    c = suggest_criteria(clean)                                     # straight from the JD, nothing added
    assert c.min_experience == 4 and c.max_experience is None
    assert c.required_skills == ["Python", "FastAPI", "PostgreSQL", "Redis"] and c.any_skills == []
