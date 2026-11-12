"""
Runs before any test: points all data folders to a throw-away temp dir and sets
fake API keys, so tests never touch your real CVs, Excel, state or API credits.
"""
import os
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values  # noqa: E402
from sqlalchemy import make_url  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="cvpipe_test_"))
TEST_DATABASE_URL = (os.getenv("TEST_DATABASE_URL")
                     or dotenv_values(BACKEND / ".env").get("TEST_DATABASE_URL") or "").strip()
os.environ.update(
    # Never the real database. Tests without the db fixture run Excel-only, as before Postgres.
    DATABASE_URL="",
    CV_FOLDER=str(TMP / "cvs"),
    OUTPUT_EXCEL=str(TMP / "out" / "candidates.xlsx"),
    STATE_FOLDER=str(TMP / "state"),
    JOBS_FOLDER=str(TMP / "jobs"),
    JOBFIT_WORKERS="2",
    ANTHROPIC_API_KEY="test", GEMINI_API_KEY="test", LLM_PROVIDER="claude",
    IMAP_USER="hr@example.com", IMAP_PASSWORD="x", IMAP_HOST="imap.example.com",
    FETCH_SINCE_DATE="", SUBJECT_KEYWORDS="", FROM_FILTER="",
    DOCLING_OCR="false",
    AWS_S3_BUCKET="", AWS_REGION="ap-south-1", AWS_S3_PREFIX="cvs",
    AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
    # Older names a real .env may use: blank, so they can never point the tests at a real bucket.
    S3_BUCKET_NAME="", S3_ACCESS_KEY_ID="", S3_SECRET_ACCESS_KEY="",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


import shutil  # noqa: E402
from contextlib import contextmanager  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _logged_in(request):
    """API tests run as a logged-in user, so they need no database.

    Tests marked real_auth go through the real login instead (tests/test_auth.py).
    """
    main = sys.modules.get("app.api.main")
    if main is None or request.node.get_closest_marker("real_auth"):
        yield
        return
    from app.auth.deps import require_login
    main.app.dependency_overrides[require_login] = lambda: None
    yield
    main.app.dependency_overrides.pop(require_login, None)


@pytest.fixture
def clean_data():
    """Empty every data folder before a test."""
    from app.core.config import settings
    for d in (settings.cv_folder, settings.state_folder, settings.output_excel.parent, settings.jobs_folder):
        shutil.rmtree(d, ignore_errors=True)
    settings.ensure_dirs()
    yield settings


def alembic_config():
    from alembic.config import Config
    return Config(str(BACKEND / "alembic.ini"))


@contextmanager
def using_database(url: str):
    """Point the app at a database for the duration of a block."""
    from app.core.config import settings
    from app.db.session import get_engine
    previous = settings.database_url
    settings.database_url = url
    get_engine.cache_clear()
    try:
        yield
    finally:
        if url:
            get_engine().dispose()
        settings.database_url = previous
        get_engine.cache_clear()


@pytest.fixture(scope="session")
def migrated_db():
    """Rebuild the test database from the migrations. Tests needing it skip if none is set."""
    from alembic import command
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    # These tests wipe tables, so a typo must not be able to point them at real data.
    if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
        pytest.exit("TEST_DATABASE_URL must name a database ending in _test", returncode=2)
    with using_database(TEST_DATABASE_URL):
        command.downgrade(alembic_config(), "base")
        command.upgrade(alembic_config(), "head")


@pytest.fixture
def db(migrated_db):
    """An empty talent database, used by the app for this test only."""
    from sqlalchemy import text

    from app.db.models import Base
    from app.db.session import get_engine
    with using_database(TEST_DATABASE_URL):
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        with get_engine().begin() as connection:
            connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        yield


def pdf_bytes(text: str, pages: int = 1) -> bytes:
    """A real PDF. The same text always gives the same bytes."""
    import io

    from reportlab.pdfgen import canvas
    buffer = io.BytesIO()
    document = canvas.Canvas(buffer, invariant=True)
    for _ in range(pages):
        document.drawString(30, 800, text)
        document.showPage()
    document.save()
    return buffer.getvalue()


def seed_candidates(rows: list[dict]) -> None:
    """Put candidates straight into the Excel + text cache (skips mail/LLM extraction)."""
    from app.core.config import settings
    from app.storage.excel_store import upsert_rows
    cache = settings.state_folder / "text_cache"
    cache.mkdir(parents=True, exist_ok=True)
    full = []
    for i, r in enumerate(rows):
        h = r.get("File Hash", f"hash{i}")
        cv_text = r.pop("_cv_text", None) or (
            f"{r.get('Full Name')} CV. Skills: {r.get('Skills', '')}. "
            f"Experience {r.get('Experience (Years)')} years in backend development. " * 3)
        (cache / f"{h}.md").write_text(cv_text, encoding="utf-8")
        full.append({"CV File": f"cv{i}.pdf", **r, "File Hash": h})
    upsert_rows(full)
