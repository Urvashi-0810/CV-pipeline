"""The talent schema: migrations, the rules the database itself enforces, and search indexes."""
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.models import AppSettings, Base, Candidate, CvDocument, EditHistory, ReviewFlag, User, build_search_text
from app.db.session import DatabaseNotConfigured, get_engine, session_scope
from conftest import alembic_config

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean(db):
    yield


def add_candidate(session, **values):
    candidate = Candidate(**values)
    session.add(candidate)
    session.flush()
    return candidate


def add_cv(session, candidate, file_hash="hash"):
    cv = CvDocument(candidate_id=candidate.id, file_hash=file_hash, file_name=f"{file_hash}.pdf", received_at=NOW)
    session.add(cv)
    session.flush()
    return cv


def add_flag(session, first, second, **values):
    flag = ReviewFlag(type="possible_duplicate", candidate_id=first.id, other_candidate_id=second.id, **values)
    session.add(flag)
    session.flush()
    return flag


def test_migrations_create_every_table_and_match_the_models():
    assert set(Base.metadata.tables) <= set(inspect(get_engine()).get_table_names())
    with get_engine().connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_migrations_can_be_undone_and_applied_again():
    command.downgrade(alembic_config(), "base")
    assert set(inspect(get_engine()).get_table_names()) <= {"alembic_version"}
    command.upgrade(alembic_config(), "head")
    assert "candidates" in inspect(get_engine()).get_table_names()


def test_defaults_apply():
    with session_scope() as session:
        candidate = add_candidate(session, full_name="Rahul")
        session.refresh(candidate)
        assert candidate.skills == [] and candidate.manual_fields == {}
        assert candidate.needs_review is False and candidate.row_version == 1


def test_a_cv_hash_is_stored_only_once():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            candidate = add_candidate(session)
            add_cv(session, candidate, "same")
            add_cv(session, candidate, "same")


@pytest.mark.parametrize("values", [{"s3_status": "lost"}, {"source_type": "fax"}])
def test_cv_columns_refuse_unknown_values(values):
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            candidate = add_candidate(session)
            session.add(CvDocument(candidate_id=candidate.id, file_hash="h", file_name="f", received_at=NOW, **values))


def test_deleting_a_candidate_removes_their_cvs_history_and_flags():
    with session_scope() as session:
        kept = add_candidate(session)
        deleted = add_candidate(session)
        add_cv(session, deleted)
        session.add(EditHistory(candidate_id=deleted.id, field="phone", kind="manual_edit"))
        add_flag(session, kept, deleted)
        deleted_id = deleted.id

    with session_scope() as session:
        session.delete(session.get(Candidate, deleted_id))

    with session_scope() as session:
        assert session.scalar(select(CvDocument)) is None
        assert session.scalar(select(EditHistory)) is None
        assert session.scalar(select(ReviewFlag)) is None
        assert len(session.scalars(select(Candidate)).all()) == 1


def test_deleting_the_latest_cv_keeps_the_candidate():
    with session_scope() as session:
        candidate = add_candidate(session)
        cv = add_cv(session, candidate)
        candidate.latest_cv_id = cv.id
        session.flush()
        candidate_id = candidate.id

    with session_scope() as session:
        session.delete(session.scalar(select(CvDocument)))

    with session_scope() as session:
        assert session.get(Candidate, candidate_id).latest_cv_id is None


def test_a_pair_has_one_open_flag_whichever_way_round():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            first, second = add_candidate(session), add_candidate(session)
            add_flag(session, first, second)
            add_flag(session, second, first)


def test_a_closed_pair_can_be_flagged_again():
    with session_scope() as session:
        first, second = add_candidate(session), add_candidate(session)
        add_flag(session, first, second, status="resolved", resolution="different_people")
        add_flag(session, first, second)


def test_several_open_flags_without_another_candidate_are_allowed():
    with session_scope() as session:
        candidate = add_candidate(session)
        session.add_all([ReviewFlag(type="edit_replaced", candidate_id=candidate.id) for _ in range(2)])


def test_a_flag_needs_two_different_candidates():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            candidate = add_candidate(session)
            add_flag(session, candidate, candidate)


def test_usernames_ignore_case():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(User(username="Priya", display_name="P", password_hash="x"))
            session.add(User(username="priya", display_name="P", password_hash="x"))


def test_a_user_with_history_cannot_be_deleted():
    with session_scope() as session:
        user = User(username="priya", display_name="P", password_hash="x")
        session.add(user)
        session.flush()
        session.add(EditHistory(candidate_id=add_candidate(session).id, field="phone", kind="manual_edit",
                                changed_by_user_id=user.id))
        user_id = user.id

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.delete(session.get(User, user_id))


def test_app_settings_holds_one_row():
    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(AppSettings(id=True))
            session.flush()
            session.add(AppSettings(id=False))


def test_contains_search_uses_the_trigram_index():
    with get_engine().begin() as connection:
        connection.execute(text("SET LOCAL enable_seqscan = off"))
        plan = connection.execute(text(
            "EXPLAIN SELECT id FROM candidates WHERE search_text ILIKE '%welder%'")).scalars().all()
    assert "ix_candidates_search_text_trgm" in " ".join(plan)


def test_search_text_holds_the_searchable_fields():
    candidate = Candidate(full_name="Rahul Sharma", passport_number="P123", job_title=None, skills=["Welding", "MIG"])
    assert build_search_text(candidate) == "Rahul Sharma P123 Welding MIG"


def test_clear_error_when_no_database_is_configured(monkeypatch):
    get_engine.cache_clear()
    monkeypatch.setattr(settings, "database_url", "")
    try:
        with pytest.raises(DatabaseNotConfigured):
            get_engine()
    finally:
        get_engine.cache_clear()
