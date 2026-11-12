"""Manual upload: merge -> extract -> rename -> S3 -> Excel, duplicates, and waiting for a run."""
import json
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

import mock_llm
from app.api.main import app
from app.api.routes_pipeline import resume_pending_uploads
from app.api.tasks import WAITING_MESSAGE, tasks
from app.core.config import settings
from app.extractor import cv_extractor
from app.fetcher import manual_upload
from app.storage.excel_store import load_candidates
from conftest import pdf_bytes

mock_llm.start()
client = TestClient(app)
PDF = "application/pdf"


@pytest.fixture(autouse=True)
def clean(clean_data):
    yield


def wait(task_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in ("done", "error"):
            return task
        time.sleep(0.05)
    raise TimeoutError(task_id)


def upload(*files):
    return client.post("/api/cvs/upload", files=[("files", file) for file in files])


def stored_pdfs():
    return list(settings.cv_folder.rglob("*.pdf"))


def manifest():
    path = settings.state_folder / "attachments_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["by_hash"]


def test_several_files_become_one_cv_and_run_the_full_pipeline(monkeypatch):
    uploaded = []
    monkeypatch.setattr(cv_extractor, "upload_cv_to_s3", lambda path: uploaded.append(path) or True)

    r = upload(("cv.pdf", pdf_bytes("CV", pages=2), PDF), ("passport.pdf", pdf_bytes("Passport"), PDF),
               ("notes.txt", b"x", "text/plain"))
    assert r.status_code == 200 and r.json()["skipped_unsupported"] == ["notes.txt"]

    done = wait(r.json()["id"])
    assert done["status"] == "done", done["error"]
    assert done["result"]["status"] == "added" and done["result"]["name"] == "Rahul Sharma"

    files = stored_pdfs()
    assert len(files) == 1 and uploaded == files
    assert files[0].name == "Senior_Software_Engineer_P1234567_Rahul_Sharma.pdf"
    assert files[0].relative_to(settings.cv_folder).parts[0] == "manual-upload"
    assert len(PdfReader(str(files[0])).pages) == 3

    row = load_candidates().iloc[0]
    assert row["Received From"] == "Manual upload" and row["Uploaded to S3"] == "Success"
    entry = next(iter(manifest().values()))
    assert entry["source"] == "manual" and entry["original_names"] == ["cv.pdf", "passport.pdf"]
    assert not manual_upload.pending_uploads()


def test_identical_upload_is_reported_not_processed_again():
    first = wait(upload(("cv.pdf", pdf_bytes("CV"), PDF)).json()["id"])
    mock_llm.SENT.clear()

    second = wait(upload(("again.pdf", pdf_bytes("CV"), PDF)).json()["id"])

    result = second["result"]
    assert result["status"] == "duplicate" and result["name"] == "Rahul Sharma"
    assert result["file_hash"] == first["result"]["file_hash"]
    assert not mock_llm.SENT
    assert len(stored_pdfs()) == 1 and len(load_candidates()) == 1


def test_failed_extraction_is_reported_and_uploading_again_retries_it(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(cv_extractor, "extract_one", lambda *a: (_ for _ in ()).throw(RuntimeError("LLM down")))
        failed = wait(upload(("cv.pdf", pdf_bytes("CV"), PDF)).json()["id"])
    assert failed["status"] == "done"
    assert failed["result"]["status"] == "failed" and "LLM down" in failed["result"]["error"]
    assert len(stored_pdfs()) == 1

    retried = wait(upload(("cv.pdf", pdf_bytes("CV"), PDF)).json()["id"])
    assert retried["result"]["status"] == "added"
    assert len(stored_pdfs()) == 1 and len(load_candidates()) == 1


def test_unreadable_file_fails_cleanly():
    done = wait(upload(("cv.pdf", b"not a pdf", PDF)).json()["id"])
    assert done["status"] == "error"
    assert not stored_pdfs() and not manual_upload.pending_uploads()


def test_upload_is_rejected_when_nothing_is_usable(monkeypatch):
    assert upload(("notes.txt", b"x", "text/plain")).status_code == 400
    assert upload(("cv.pdf", b"", PDF)).status_code == 400
    monkeypatch.setattr(settings, "max_attachment_mb", 0.001)
    assert upload(("cv.pdf", b"x" * 5000, PDF)).status_code == 413


def test_upload_waits_for_a_running_task_and_blocks_new_runs():
    release = threading.Event()
    running = tasks.submit("extract", "pipeline", lambda p: release.wait(10))
    try:
        task = upload(("cv.pdf", pdf_bytes("CV"), PDF)).json()
        assert task["status"] == "queued" and task["current"] == WAITING_MESSAGE
        assert client.post("/api/extract", json={}).status_code == 409
        assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "queued"
    finally:
        release.set()
    assert wait(running.id)["status"] == "done"
    assert wait(task["id"])["result"]["status"] == "added"


def test_uploads_run_one_at_a_time_in_order():
    release = threading.Event()
    tasks.submit("extract", "pipeline", lambda p: release.wait(10))
    first = upload(("a.pdf", pdf_bytes("A"), PDF)).json()
    second = upload(("b.pdf", pdf_bytes("B"), PDF)).json()
    release.set()
    assert wait(first["id"])["status"] == "done" and wait(second["id"])["status"] == "done"
    assert len(stored_pdfs()) == 2


def test_upload_left_waiting_by_a_restart_is_processed_on_startup():
    manual_upload.stage_upload([("cv.pdf", pdf_bytes("CV"))])
    resumed = resume_pending_uploads()
    assert len(resumed) == 1

    done = wait(resumed[0].id)
    assert done["result"]["status"] == "added"
    assert not manual_upload.pending_uploads()


def _query(url):
    return parse_qs(urlparse(url).query)


def test_cv_url_opens_inline_or_downloads_from_s3(monkeypatch):
    monkeypatch.setattr(cv_extractor, "upload_cv_to_s3", lambda path: True)
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    file_hash = wait(upload(("cv.pdf", pdf_bytes("CV"), PDF)).json()["id"])["result"]["file_hash"]

    body = client.get(f"/api/cvs/{file_hash}/url").json()
    assert "/cvs/manual-upload/" in urlparse(body["url"]).path
    assert body["expires_in"] <= 900
    query = _query(body["url"])
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["response-content-type"] == [PDF]
    assert query["response-content-disposition"][0].startswith("inline;")

    download = client.get(f"/api/cvs/{file_hash}/url", params={"download": True}).json()
    assert _query(download["url"])["response-content-disposition"][0].startswith("attachment;")


def test_cv_url_errors(monkeypatch):
    assert client.get("/api/cvs/unknown/url").status_code == 404

    file_hash = wait(upload(("cv.pdf", pdf_bytes("CV"), PDF)).json()["id"])["result"]["file_hash"]
    assert client.get(f"/api/cvs/{file_hash}/url").status_code == 409     # no bucket, so its upload failed

    state = settings.state_folder / "processed_files.json"
    data = json.loads(state.read_text(encoding="utf-8"))
    data[file_hash]["s3_status"] = "Success"
    state.write_text(json.dumps(data), encoding="utf-8")
    assert client.get(f"/api/cvs/{file_hash}/url").status_code == 503     # bucket not configured
