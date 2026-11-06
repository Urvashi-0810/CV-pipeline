"""
Pydantic models = the "contract" between our code and the LLM.

Instructor sends this schema to Claude/Gemini, and the LLM MUST return JSON
matching it. Instructor validates it and retries automatically if the LLM
returns something invalid. The `description=` texts are read by the LLM, so
they double as extraction instructions - edit them to change LLM behaviour.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Education(BaseModel):
    degree: Optional[str] = Field(None, description="Degree / qualification, e.g. 'B.Tech Computer Science', 'MBA', '12th (HSC)'")
    institution: Optional[str] = Field(None, description="College / university / school name")
    year: Optional[str] = Field(None, description="Passing year or range, e.g. '2019' or '2015-2019'")
    score: Optional[str] = Field(None, description="CGPA / percentage if mentioned")


class Candidate(BaseModel):
    full_name: Optional[str] = Field(None, description="Candidate's full name")
    email: Optional[str] = Field(None, description="Primary email address")
    phone: Optional[str] = Field(None, description="Primary phone number with country code if present")
    skills: list[str] = Field(default_factory=list, description="Technical and professional skills, each as a short item (e.g. 'Python', 'SQL', 'Team leadership'). Deduplicated.")
    passport_number: Optional[str] = Field(
            None,
            description="Passport number exactly as shown in the CV. Preserve letters, digits, and punctuation; null if not explicitly present. Do not substitute another identity number.",
        )
    total_experience_years: Optional[float] = Field(
        None,
        description=(
            "TOTAL professional work experience in years (decimal allowed, e.g. 3.5). "
            "Compute from job start/end dates if not stated explicitly; treat 'Present'/'Current' as today. "
            "Do not double-count overlapping jobs. Internships count only if nothing else. 0 for freshers. "
            "null only if impossible to determine."
        ),
    )
    education: list[Education] = Field(default_factory=list, description="Education history, highest/latest first")
    current_job_title: Optional[str] = Field(None, description="Current job title, or most recent one if not currently employed")
    current_company: Optional[str] = Field(None, description="Current or most recent employer")
    date_of_birth: Optional[str] = Field(None, description="Date of birth exactly as found, normalised to YYYY-MM-DD when the full date is known. null if not present")
    age: Optional[int] = Field(None, description="Age in years ONLY if explicitly written or computable from date of birth. Never guess from graduation year.")
    certifications: list[str] = Field(default_factory=list, description="Certifications / licences / courses with certificates")
    languages: list[str] = Field(default_factory=list, description="Spoken languages (not programming languages)")
    location: Optional[str] = Field(None, description="Current city, state/country")
    summary: Optional[str] = Field(None, description="2-3 sentence neutral professional summary of the candidate written by you")

    @field_validator("skills", "certifications", "languages")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        """Remove empty items and case-insensitive duplicates, keep original order."""
        seen, out = set(), []
        for item in v or []:
            item = (item or "").strip()
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
        return out

    @field_validator("age")
    @classmethod
    def _age_sane(cls, v):
        # Guard against nonsense like age=2019
        if v is not None and not (14 <= v <= 80):
            return None
        return v

    @field_validator("total_experience_years")
    @classmethod
    def _exp_sane(cls, v):
        if v is not None and (v < 0 or v > 60):
            return None
        return round(v, 1) if v is not None else v

    def fill_age_from_dob(self) -> None:
        """If the LLM gave a DOB but no age, compute it in Python (more reliable than LLM maths)."""
        if self.age is None and self.date_of_birth:
            try:
                dob = date.fromisoformat(self.date_of_birth[:10])
                t = date.today()
                self.age = t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))
            except ValueError:
                pass


# =====================================================================
#  STEP 3 - Shortlisting rules (pure Python/pandas, no LLM)
# =====================================================================
class ShortlistCriteria(BaseModel):
    """
    Rules applied to the Excel rows. Every rule is optional: leave it empty/null to ignore it.
    Stored per job in data/jobs/<slug>/criteria.json - edit that file (or the UI later).
    Text matching is case-insensitive; skills use whole-word matching so "Java" does NOT
    match "JavaScript" and "C" does NOT match "C++".
    """
    min_experience: Optional[float] = Field(None, description="Minimum total experience (years)")
    max_experience: Optional[float] = Field(None, description="Maximum total experience (years)")
    keep_unknown_experience: bool = Field(False, description="Keep CVs where experience could not be determined")

    min_age: Optional[int] = None
    max_age: Optional[int] = None
    keep_unknown_age: bool = Field(True, description="Most CVs have no age - keep them by default")

    required_skills: list[str] = Field(default_factory=list, description="ALL of these must be present")
    any_skills: list[str] = Field(default_factory=list, description="At least `min_any_skills` of these must be present")
    min_any_skills: int = 1

    locations: list[str] = Field(default_factory=list, description="Candidate location must contain ANY of these")
    keep_unknown_location: bool = True

    education_keywords: list[str] = Field(default_factory=list, description="Education must mention ANY of these (e.g. 'B.Tech', 'MBA')")
    job_title_keywords: list[str] = Field(default_factory=list, description="Current/last job title must contain ANY of these")
    languages: list[str] = Field(default_factory=list, description="ALL of these spoken languages required")
    exclude_keywords: list[str] = Field(default_factory=list, description="Reject if ANY of these appears anywhere in the row")

    max_candidates: Optional[int] = Field(None, description="Keep only the top N after ranking (None = all that pass)")


# =====================================================================
#  STEP 4a - Requirements extracted from the JD (the scoring rubric)
# =====================================================================
RequirementCategory = Literal["skill", "experience", "education", "certification", "language", "location", "other"]


class Requirement(BaseModel):
    criterion: str = Field(..., description="The requirement in the JD's OWN words, e.g. 'Python', '3+ years of backend development'. For skills: only the skill name exactly as written in the JD.")
    importance: Literal["must", "nice"] = Field(..., description="'must' = stated as required/mandatory/must or listed under requirements; 'nice' = stated as preferred/plus/good-to-have")
    category: RequirementCategory
    jd_quote: str = Field(..., description="The exact phrase copied character-for-character from the job description that states this requirement")


class JobRequirements(BaseModel):
    """Everything here must come from the job description text - nothing inferred or added."""
    job_title: str = Field(..., description="Job title exactly as written in the JD")
    seniority: Optional[str] = Field(None, description="Only if the JD states it (e.g. 'Senior'); else null")
    min_experience_years: Optional[float] = Field(None, description="Only if the JD states a number of years; else null")
    max_experience_years: Optional[float] = Field(None, description="Only if the JD states an upper limit; else null")
    location: Optional[str] = Field(None, description="Only if the JD states it; else null")
    requirements: list[Requirement] = Field(..., description="Every requirement explicitly written in the JD and nothing else. Split combined items ('Python/Django' -> two).")
    key_responsibilities: list[str] = Field(default_factory=list, description="Responsibilities as written in the JD (max 8)")

    def must(self, category: Optional[str] = None) -> list[str]:
        return [r.criterion for r in self.requirements if r.importance == "must" and (category is None or r.category == category)]

    def nice(self, category: Optional[str] = None) -> list[str]:
        return [r.criterion for r in self.requirements if r.importance == "nice" and (category is None or r.category == category)]


# =====================================================================
#  STEP 4b - Job fit for ONE candidate (what the LLM returns)
# =====================================================================
class CriterionAssessment(BaseModel):
    criterion: str = Field(..., description="Copy the requirement text exactly as given")
    status: Literal["met", "partial", "not_met"] = Field(
        ..., description="met = clear evidence in CV; partial = related/weaker evidence or less than required; not_met = no evidence"
    )
    evidence: str = Field(..., description="Short justification quoting/pointing to the CV (max ~25 words). 'No evidence in CV' if not_met.")


class JobFitLLM(BaseModel):
    assessments: list[CriterionAssessment] = Field(..., description="Exactly one assessment per requirement, same order as given")
    overall_score: int = Field(..., ge=0, le=100, description="Your holistic fit score 0-100 considering all requirements and responsibilities")
    strengths: list[str] = Field(default_factory=list, description="Top 3-5 strengths for THIS role")
    gaps: list[str] = Field(default_factory=list, description="Main gaps / risks for THIS role")
    reasoning: str = Field(..., description="3-5 sentence explanation of the fit, for a recruiter")
    interview_questions: list[str] = Field(default_factory=list, description="3 targeted interview questions to probe gaps")
