"""Request bodies for the API (FastAPI validates them and shows them in /docs)."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Provider = Optional[Literal["claude", "gemini"]]


class FetchRequest(BaseModel):
    reset: bool = False
    # Which mailbox and folder to read. Without a mailbox, the one in .env is used.
    mailbox_id: Optional[int] = None
    folder: Optional[str] = None
    # Both dates (inclusive) = a date-range run; neither = mail new since the last fetch.
    since: Optional[date] = None
    until: Optional[date] = None


class ExtractRequest(BaseModel):
    provider: Provider = None
    force: bool = False
    limit: Optional[int] = Field(None, ge=1)


class RunRequest(ExtractRequest, FetchRequest):
    pass


class JdUpdate(BaseModel):
    jd_text: str


class RequirementsRequest(BaseModel):
    provider: Provider = None
    force: bool = Field(False, description="Regenerate even if requirements.json exists")
    overwrite_criteria: bool = Field(False, description="Replace criteria.json with the suggestion")


class JobFitRequest(BaseModel):
    provider: Provider = None
    force: bool = False
    workers: Optional[int] = Field(None, ge=1, le=16)
    limit: Optional[int] = Field(None, ge=1)
