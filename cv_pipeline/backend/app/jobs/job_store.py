"""
A "job" = one opening you are hiring for. Everything about it lives in one folder:

    data/jobs/<slug>/
        job.json            title, created date, original JD file name
        jd.md               job description text (converted from PDF/DOCX if you gave a file)
        requirements.json   STEP 4a: requirements the LLM extracted from the JD (the scoring rubric)
        criteria.json       STEP 3:  shortlisting rules (you edit this; can be auto-suggested from the JD)
        shortlist.xlsx      STEP 3 output
        jobfit.xlsx         STEP 4 output (ranking)
        jobfit_results.json STEP 4 results as JSON (used by the API / cache)

Slug = short folder-safe id, e.g. "python-dev-pune".
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.models import JobRequirements, ShortlistCriteria


def jobs_root() -> Path:
    settings.jobs_folder.mkdir(parents=True, exist_ok=True)
    return settings.jobs_folder


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60] or "job"


class Job:
    def __init__(self, slug: str):
        self.slug = slugify(slug)
        self.dir = jobs_root() / self.slug

    # ---- paths
    @property
    def meta_path(self) -> Path: return self.dir / "job.json"
    @property
    def jd_path(self) -> Path: return self.dir / "jd.md"
    @property
    def requirements_path(self) -> Path: return self.dir / "requirements.json"
    @property
    def criteria_path(self) -> Path: return self.dir / "criteria.json"
    @property
    def shortlist_xlsx(self) -> Path: return self.dir / "shortlist.xlsx"
    @property
    def jobfit_xlsx(self) -> Path: return self.dir / "jobfit.xlsx"
    @property
    def jobfit_json(self) -> Path: return self.dir / "jobfit_results.json"

    def exists(self) -> bool:
        return self.meta_path.exists()

    def require(self) -> "Job":
        if not self.exists():
            raise ValueError(f"Job '{self.slug}' not found. Create it first (python main.py job create ...)")
        return self

    # ---- meta
    @property
    def meta(self) -> dict:
        return json.loads(self.meta_path.read_text(encoding="utf-8")) if self.meta_path.exists() else {}

    def save_meta(self, **updates) -> None:
        m = self.meta
        m.update(updates)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- JD
    @property
    def jd_text(self) -> str:
        if not self.jd_path.exists():
            raise ValueError(f"Job '{self.slug}' has no job description yet")
        return self.jd_path.read_text(encoding="utf-8")

    def set_jd_text(self, text: str) -> None:
        if len(text.strip()) < 30:
            raise ValueError("Job description is empty or too short")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jd_path.write_text(text.strip(), encoding="utf-8")
        self.save_meta(jd_updated_at=_now())
        # JD changed -> old rubric is no longer valid
        self.requirements_path.unlink(missing_ok=True)

    def set_jd_file(self, file: Path) -> None:
        """.txt/.md/.pdf/.docx -> plain text. We keep the JD as text so every requirement can be
        checked against it word-for-word (see app/jobfit/requirements.py)."""
        from app.extractor.converter import extract_text
        text = extract_text(file)
        self.set_jd_text(text)
        self.save_meta(jd_source_file=file.name)

    # ---- requirements (rubric)
    def load_requirements(self) -> Optional[JobRequirements]:
        if not self.requirements_path.exists():
            return None
        return JobRequirements.model_validate_json(self.requirements_path.read_text(encoding="utf-8"))

    def save_requirements(self, req: JobRequirements) -> None:
        self.requirements_path.write_text(req.model_dump_json(indent=2), encoding="utf-8")

    # ---- criteria
    def load_criteria(self) -> ShortlistCriteria:
        if not self.criteria_path.exists():
            return ShortlistCriteria()
        return ShortlistCriteria.model_validate_json(self.criteria_path.read_text(encoding="utf-8"))

    def save_criteria(self, c: ShortlistCriteria) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.criteria_path.write_text(c.model_dump_json(indent=2), encoding="utf-8")

    def summary(self) -> dict:
        m = self.meta
        return {
            "slug": self.slug,
            "title": m.get("title", self.slug),
            "created_at": m.get("created_at"),
            "has_jd": self.jd_path.exists(),
            "has_requirements": self.requirements_path.exists(),
            "has_criteria": self.criteria_path.exists(),
            "has_shortlist": self.shortlist_xlsx.exists(),
            "has_jobfit": self.jobfit_xlsx.exists(),
            "last_shortlist": m.get("last_shortlist"),
            "last_jobfit": m.get("last_jobfit"),
        }


def create_job(title: str, slug: Optional[str] = None, jd_text: Optional[str] = None,
               jd_file: Optional[Path] = None) -> Job:
    job = Job(slug or title)
    if job.exists():
        raise ValueError(f"Job '{job.slug}' already exists")
    job.dir.mkdir(parents=True, exist_ok=True)
    job.save_meta(slug=job.slug, title=title, created_at=_now())
    try:
        if jd_file:
            job.set_jd_file(Path(jd_file))
        elif jd_text:
            job.set_jd_text(jd_text)
    except Exception:
        shutil.rmtree(job.dir, ignore_errors=True)
        raise
    if not job.criteria_path.exists():
        job.save_criteria(ShortlistCriteria())
    return job


def list_jobs() -> list[dict]:
    return [Job(p.name).summary() for p in sorted(jobs_root().iterdir()) if (p / "job.json").exists()]


def delete_job(slug: str) -> None:
    job = Job(slug).require()
    shutil.rmtree(job.dir)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
