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
    ANTHROPIC_API_KEY="test", GEMINI_API_KEY="test", LLM_PROVIDER="claude",
    IMAP_USER="hr@example.com", IMAP_PASSWORD="x", IMAP_HOST="imap.example.com",
    FETCH_SINCE_DATE="", SUBJECT_KEYWORDS="", FROM_FILTER="",
    DOCLING_OCR="false",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
