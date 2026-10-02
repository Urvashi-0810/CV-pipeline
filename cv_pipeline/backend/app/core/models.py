"""
Pydantic models = the "contract" between our code and the LLM.

Instructor sends this schema to Claude/Gemini, and the LLM MUST return JSON
matching it. Instructor validates it and retries automatically if the LLM
returns something invalid. The `description=` texts are read by the LLM, so
they double as extraction instructions - edit them to change LLM behaviour.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

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
