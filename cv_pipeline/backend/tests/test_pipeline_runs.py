"""Pipeline run history: what the Pipeline screen's last-run summary reads."""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mock_llm
from app.api.main import app
from app.api.tasks import tasks
from app.core.config import settings
from app.db.models import PipelineRun
from app.db.session import session_scope
from app.talent import runs
from conftest import pdf_bytes

mock_llm.start()
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean(clean_data, db):
    yield


def wait(task_id):
    tasks.get(task_id).future.result(timeout=60)
    return client.get(f"/api/tasks/{task_id}").json()


def stored():
    with session_scope() as session:
        return session.scalars(select(PipelineRun).order_by(PipelineRun.id)).all()


def test_an_extract_run_is_recorded_with_its_summary():
    path = settings.cv_folder / "acct" / "May-2026" / "a.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(pdf_bytes("a"))

    done = wait(client.post("/api/extract", json={}).json()["id"])

    assert done["status"] == "done"
    [run] = stored()
    assert (run.kind, run.status) == ("extract", "done") and run.finished_at
    assert run.summary["extracted"] == 1 and "placements" not in run.summary
    items = client.get("/api/pipeline/runs").json()["items"]
    assert items[0]["kind"] == "extract" and items[0]["summary"]["extracted"] == 1


def test_a_failed_run_is_recorded_as_an_error():
    def broken(progress):
        raise RuntimeError("mailbox unreachable")

    with pytest.raises(RuntimeError):
        runs.recorded("fetch", broken)(lambda *a: None)

    [run] = stored()
    assert run.status == "error" and run.summary == {"error": "mailbox unreachable"}


def test_summaries_drop_long_lists_and_cap_errors():
    summary = runs.summarise({"fetch": {"saved_files": ["a"] * 9, "mails_scanned": 3,
                                        "extraction_errors": {f"f{i}": "x" for i in range(60)}}})
    assert "saved_files" not in summary["fetch"] and summary["fetch"]["mails_scanned"] == 3
    assert len(summary["fetch"]["extraction_errors"]) == runs.MAX_ERRORS + 1


def test_runs_cut_off_by_a_restart_are_closed():
    release = threading.Event()
    task = tasks.submit("extract", "pipeline", runs.recorded("extract", lambda p: release.wait(10)))
    try:
        for _ in range(100):
            if stored():
                break
            threading.Event().wait(0.05)
        assert stored()[0].status == "running"
        assert runs.close_interrupted_runs() == 1
        assert stored()[0].summary == {"error": "The server stopped during this run"}
    finally:
        release.set()
        task.future.result(timeout=10)


def test_newest_runs_first():
    for kind in ("fetch", "extract", "s3-retry"):
        runs.recorded(kind, lambda p: {"ok": True})(lambda *a: None)
    kinds = [item["kind"] for item in client.get("/api/pipeline/runs", params={"limit": 2}).json()["items"]]
    assert kinds == ["s3-retry", "extract"]


def test_nothing_is_recorded_without_the_database(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "")
    assert runs.recorded("fetch", lambda p: {"ok": True})(lambda *a: None) == {"ok": True}
    assert client.get("/api/pipeline/runs").json() == {"items": []}
