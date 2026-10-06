"""
Central configuration.

Everything is read from the .env file once, here, and exposed as a single
`settings` object. Every other module does `from app.core.config import settings`
instead of reading os.environ itself -> one place to look when something is misconfigured.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# backend/ folder (this file is backend/app/core/config.py)
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


def _csv(name: str) -> list[str]:
    raw = os.getenv(name, "") or ""
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default) or default)
    return p if p.is_absolute() else BASE_DIR / p


@dataclass
class Settings:
    # LLM
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "claude").lower())
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    claude_model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

    # How resumes reach the LLM: "llm" (send the file itself) or "docling" (convert to text first)
    cv_parser: str = field(default_factory=lambda: os.getenv("CV_PARSER", "llm").strip().lower())

    # Mail
    imap_host: str = field(default_factory=lambda: os.getenv("IMAP_HOST", "imap.gmail.com"))
    imap_port: int = field(default_factory=lambda: int(os.getenv("IMAP_PORT", "993")))
    imap_user: str = field(default_factory=lambda: os.getenv("IMAP_USER", ""))
    imap_password: str = field(default_factory=lambda: os.getenv("IMAP_PASSWORD", ""))
    imap_folder: str = field(default_factory=lambda: os.getenv("IMAP_FOLDER", "INBOX"))
    fetch_since_date: str = field(default_factory=lambda: os.getenv("FETCH_SINCE_DATE", "").strip())
    subject_keywords: list[str] = field(default_factory=lambda: _csv("SUBJECT_KEYWORDS"))
    from_filter: list[str] = field(default_factory=lambda: _csv("FROM_FILTER"))
    max_attachment_mb: float = field(default_factory=lambda: float(os.getenv("MAX_ATTACHMENT_MB", "15")))

    # Paths
    cv_folder: Path = field(default_factory=lambda: _path("CV_FOLDER", "data/cvs"))
    output_excel: Path = field(default_factory=lambda: _path("OUTPUT_EXCEL", "data/output/candidates.xlsx"))
    state_folder: Path = field(default_factory=lambda: _path("STATE_FOLDER", "data/state"))
    jobs_folder: Path = field(default_factory=lambda: _path("JOBS_FOLDER", "data/jobs"))

    # Job-fit
    jobfit_workers: int = field(default_factory=lambda: int(os.getenv("JOBFIT_WORKERS", "3")))

    def ensure_dirs(self) -> None:
        self.cv_folder.mkdir(parents=True, exist_ok=True)
        self.output_excel.parent.mkdir(parents=True, exist_ok=True)
        self.state_folder.mkdir(parents=True, exist_ok=True)
        self.jobs_folder.mkdir(parents=True, exist_ok=True)


settings = Settings()

# File types we treat as CVs
CV_EXTENSIONS = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
