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
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE)

# Older names some .env files use. boto3 only reads the AWS_ names, so copy them across.
LEGACY_NAMES = {"AWS_ACCESS_KEY_ID": "S3_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY": "S3_SECRET_ACCESS_KEY",
                "AWS_S3_BUCKET": "S3_BUCKET_NAME"}


def apply_legacy_names(environ) -> None:
    """Only when the AWS_ name is absent: a deliberately blank value stays blank."""
    for name, legacy in LEGACY_NAMES.items():
        if environ.get(name) is None and environ.get(legacy):
            environ[name] = environ[legacy]


apply_legacy_names(os.environ)


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
    fetch_until_date: str = field(default_factory=lambda: os.getenv("FETCH_UNTIL_DATE", "").strip())
    subject_keywords: list[str] = field(default_factory=lambda: _csv("SUBJECT_KEYWORDS"))
    from_filter: list[str] = field(default_factory=lambda: _csv("FROM_FILTER"))
    max_attachment_mb: float = field(default_factory=lambda: float(os.getenv("MAX_ATTACHMENT_MB", "15")))

    # Paths
    cv_folder: Path = field(default_factory=lambda: _path("CV_FOLDER", "data/cvs"))
    output_excel: Path = field(default_factory=lambda: _path("OUTPUT_EXCEL", "data/output/candidates.xlsx"))
    state_folder: Path = field(default_factory=lambda: _path("STATE_FOLDER", "data/state"))
    jobs_folder: Path = field(default_factory=lambda: _path("JOBS_FOLDER", "data/jobs"))

    # Talent database, e.g. postgresql+psycopg://talent:password@localhost:5432/talent
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "").strip())

    # Talent Pool login (same rules as the CRM)
    session_ttl_hours: int = field(default_factory=lambda: int(os.getenv("SESSION_TTL_HOURS", "12")))
    admin_session_minutes: int = field(default_factory=lambda: int(os.getenv("ADMIN_SESSION_MINUTES", "30")))
    admin_max_failed_attempts: int = field(default_factory=lambda: int(os.getenv("ADMIN_MAX_FAILED_ATTEMPTS", "5")))
    admin_lockout_minutes: int = field(default_factory=lambda: int(os.getenv("ADMIN_LOCKOUT_MINUTES", "15")))
    password_min_length: int = field(default_factory=lambda: int(os.getenv("PASSWORD_MIN_LENGTH", "6")))
    # Browsers only send Secure cookies over HTTPS. Set true whenever the API is served over HTTPS.
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").strip().lower() == "true")
    # Exact frontend origins allowed to call the API with the login cookie.
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip().rstrip("/") for o in os.getenv(
            "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()])

    # S3 (credentials come from boto3's normal chain, never from here)
    aws_s3_bucket: str = field(default_factory=lambda: os.getenv("AWS_S3_BUCKET", "").strip())
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "").strip())
    aws_s3_prefix: str = field(default_factory=lambda: os.getenv("AWS_S3_PREFIX", "cvs").strip().strip("/"))

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
