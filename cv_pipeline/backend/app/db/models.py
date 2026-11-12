"""
Tables in the talent database. Postgres is the source of truth for candidates.

candidates holds one row per person with their current values. cv_documents holds one row
per CV received, including what was extracted from it. Passport matching uses
cv_documents.passport_norm, because a merged candidate can hold two passport numbers and a
later CV with either must still attach.

The *_norm columns are filled by the matching step.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMPTY_OBJECT = text("'{}'::jsonb")
EMPTY_LIST = text("'[]'::jsonb")
EMPTY_ARRAY = text("'{}'::text[]")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


def _id() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(), primary_key=True)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _one_of(column: str, *values: str) -> str:
    return f"{column} in ({', '.join(repr(value) for value in values)})"


def _trigram_index(table: str, column: str) -> Index:
    return Index(f"ix_{table}_{column}_trgm", column, postgresql_using="gin",
                 postgresql_ops={column: "gin_trgm_ops"})


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = _id()
    username: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Users are deactivated, never deleted: edit history points at them.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Counted per user rather than per session, so logging in again doesn't reset the count.
    admin_failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    admin_locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("uq_users_username_lower", func.lower(username), unique=True),)


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = _id()
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    admin_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default=text("true"))
    admin_password_hash: Mapped[Optional[str]] = mapped_column(Text)
    updated_by: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (CheckConstraint("id", name="single_row"),)


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[int] = _id()
    full_name: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    phone: Mapped[Optional[str]] = mapped_column(Text)
    passport_number: Mapped[Optional[str]] = mapped_column(Text)
    job_title: Mapped[Optional[str]] = mapped_column(Text)
    company: Mapped[Optional[str]] = mapped_column(Text)
    date_of_birth: Mapped[Optional[str]] = mapped_column(Text)
    # date_of_birth as a real date when it's a full one, so the age can be worked out when read.
    birth_date: Mapped[Optional[date]] = mapped_column(Date)
    location: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    education: Mapped[Optional[str]] = mapped_column(Text)
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY)
    certifications: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY)
    languages: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=EMPTY_ARRAY)
    experience_years: Mapped[Optional[Decimal]] = mapped_column(Numeric(4, 1))
    age: Mapped[Optional[int]] = mapped_column(SmallInteger)

    email_norm: Mapped[Optional[str]] = mapped_column(Text)
    phone_norm: Mapped[Optional[str]] = mapped_column(Text)
    passport_norm: Mapped[Optional[str]] = mapped_column(Text)

    # The CV the current values came from. Circular with cv_documents, so it's added afterwards.
    latest_cv_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("cv_documents.id", ondelete="SET NULL", use_alter=True)
    )
    received_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    manual_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=EMPTY_OBJECT)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    search_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        _trigram_index("candidates", "search_text"),
        _trigram_index("candidates", "job_title"),
        _trigram_index("candidates", "location"),
        Index("ix_candidates_skills", "skills", postgresql_using="gin"),
        Index("ix_candidates_full_name", "full_name"),
        Index("ix_candidates_experience_years", "experience_years"),
        Index("ix_candidates_age", "age"),
        Index("ix_candidates_received_at", "received_at"),
        Index("ix_candidates_email_norm", "email_norm"),
        Index("ix_candidates_phone_norm", "phone_norm"),
    )


class CvDocument(Base):
    __tablename__ = "cv_documents"

    id: Mapped[int] = _id()
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    local_rel_path: Mapped[Optional[str]] = mapped_column(Text)
    s3_key: Mapped[Optional[str]] = mapped_column(Text)
    # missing = no file found for this CV in the bucket. The candidate data is still kept.
    s3_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="mail")
    mail_account: Mapped[Optional[str]] = mapped_column(Text)
    mail_from: Mapped[Optional[str]] = mapped_column(Text)
    mail_from_name: Mapped[Optional[str]] = mapped_column(Text)
    mail_subject: Mapped[Optional[str]] = mapped_column(Text)
    mail_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    extracted: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=EMPTY_OBJECT)
    passport_norm: Mapped[Optional[str]] = mapped_column(Text, index=True)
    email_norm: Mapped[Optional[str]] = mapped_column(Text, index=True)
    phone_norm: Mapped[Optional[str]] = mapped_column(Text, index=True)
    extraction_method: Mapped[Optional[str]] = mapped_column(Text)
    llm: Mapped[Optional[str]] = mapped_column(Text)
    extracted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(_one_of("s3_status", "success", "failed", "pending", "missing"), name="s3_status"),
        CheckConstraint(_one_of("source_type", "mail", "manual"), name="source_type"),
        Index("ix_cv_documents_s3_status", "s3_status"),
    )


class EditHistory(Base):
    __tablename__ = "edit_history"

    id: Mapped[int] = _id()
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    cv_document_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("cv_documents.id", ondelete="SET NULL"), index=True
    )
    # Null for changes made by the pipeline.
    changed_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(_one_of("kind", "manual_edit", "replaced_by_cv", "keep_my_edit"), name="kind"),
    )


class ReviewFlag(Base):
    __tablename__ = "review_flags"

    id: Mapped[int] = _id()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    other_candidate_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text)
    message: Mapped[Optional[str]] = mapped_column(Text)
    # [{field, manual_value, new_value, cv_document_id, kept}] for edit_replaced flags
    fields: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=EMPTY_LIST)
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    resolved_by_user_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(_one_of("type", "possible_duplicate", "edit_replaced", "passport_name_mismatch"),
                        name="type"),
        CheckConstraint(_one_of("status", "open", "resolved"), name="status"),
        CheckConstraint(
            f"resolution is null or {_one_of('resolution', 'merged', 'different_people', 'kept_edit', 'accepted')}",
            name="resolution",
        ),
        CheckConstraint("other_candidate_id is distinct from candidate_id", name="two_candidates"),
        # The same pair can't be flagged twice while open, whichever way round it's stored.
        Index(
            "uq_review_flags_open_pair",
            "type",
            func.least(candidate_id, other_candidate_id),
            func.greatest(candidate_id, other_candidate_id),
            unique=True,
            postgresql_where=text("status = 'open' AND other_candidate_id IS NOT NULL"),
        ),
        Index("ix_review_flags_status", "status"),
    )


class PipelineRun(Base):
    """Task state lives in memory, so the last run summary is kept here to survive a restart."""
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = _id()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=EMPTY_OBJECT)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_one_of("status", "running", "done", "cancelled", "error"), name="status"),
        Index("ix_pipeline_runs_kind_started_at", "kind", "started_at"),
    )


def build_search_text(candidate: Candidate) -> str:
    """Everything the Talent Pool search box looks through, as one indexed column."""
    values = (candidate.full_name, candidate.email, candidate.phone, candidate.passport_number,
              candidate.job_title, candidate.company, candidate.location, *(candidate.skills or []))
    return " ".join(value for value in values if value)
