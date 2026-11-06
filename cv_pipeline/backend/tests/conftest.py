"""
Runs before any test: points all data folders to a throw-away temp dir and sets
fake API keys, so tests never touch your real CVs, Excel, state or API credits.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cvpipe_test_"))
os.environ.update(
    CV_FOLDER=str(TMP / "cvs"),
    OUTPUT_EXCEL=str(TMP / "out" / "candidates.xlsx"),
    STATE_FOLDER=str(TMP / "state"),
    JOBS_FOLDER=str(TMP / "jobs"),
    JOBFIT_WORKERS="2",
    ANTHROPIC_API_KEY="test", GEMINI_API_KEY="test", LLM_PROVIDER="claude",
    IMAP_USER="hr@example.com", IMAP_PASSWORD="x", IMAP_HOST="imap.example.com",
    FETCH_SINCE_DATE="", SUBJECT_KEYWORDS="", FROM_FILTER="",
    DOCLING_OCR="false",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


import shutil  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def clean_data():
    """Empty every data folder before a test."""
    from app.core.config import settings
    for d in (settings.cv_folder, settings.state_folder, settings.output_excel.parent, settings.jobs_folder):
        shutil.rmtree(d, ignore_errors=True)
    settings.ensure_dirs()
    yield settings


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
