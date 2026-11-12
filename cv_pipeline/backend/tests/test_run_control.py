"""Pause, resume and cancel for fetch, extract and run, at safe points only."""
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import mock_llm
from app.api.main import app
from app.api.tasks import ACTIVE, tasks
from app.core.config import settings
from app.core.control import RunCancelled, RunControl
from app.core.state import JsonState
from app.db.models import PipelineRun
from app.db.session import session_scope
from app.extractor import cv_extractor
from app.fetcher import mail_fetcher
from conftest import pdf_bytes
from test_fetcher import FakeMailbox, att, mail, pdf

mock_llm.start()
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean(clean_data, db):
    yield


def wait_for(task_id, *statuses, timeout=30):
    end = time.time() + timeout
    task = {}
    while time.time() < end:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in statuses:
            return task
        time.sleep(0.02)
    raise TimeoutError(f"{task_id} never reached {statuses}: {task.get('status')}")


def put_cvs(count):
    for i in range(count):
        path = settings.cv_folder / "acct" / "May-2026" / f"cv{i}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf_bytes(f"cv {i}"))


def start(path):
    response = client.post(path, json={})
    assert response.status_code == 200, response.text
    return response.json()


class Gate:
    """Each CV's extraction waits here, so a test can act while a CV is in progress."""

    def __init__(self):
        self.started, self.go = threading.Semaphore(0), threading.Semaphore(0)

    def wait_started(self):
        assert self.started.acquire(timeout=20), "no CV started"

    def release(self):
        self.go.release()

    def step(self):
        self.wait_started()
        self.release()


@pytest.fixture
def gate(monkeypatch):
    gate = Gate()
    real = cv_extractor.extract_one

    def extract_one(path, provider):
        gate.started.release()
        assert gate.go.acquire(timeout=20)
        return real(path, provider)

    monkeypatch.setattr(cv_extractor, "extract_one", extract_one)
    yield gate
    # Whatever happened, leave no run holding the pipeline for the next test.
    for _ in range(100):
        gate.go.release()
    for task in tasks.list():
        if task.status in ACTIVE:
            if getattr(task, "control", None):
                task.control.cancel()
            task.future.result(timeout=30)


def test_the_control_itself():
    control = RunControl()
    control.checkpoint()                                  # nothing requested: carries on
    control.pause()
    resumer = threading.Timer(0.2, control.resume)
    resumer.start()
    began = time.time()
    control.checkpoint()                                  # waits until resumed
    assert time.time() - began >= 0.15 and control.was_paused() and not control.was_paused()
    control.cancel()
    with pytest.raises(RunCancelled):
        control.checkpoint()


def test_cancel_lets_the_current_cv_finish_then_stops(gate):
    put_cvs(3)
    task = start("/api/extract")
    assert task["controllable"]
    gate.step()                                           # CV 1 done
    gate.wait_started()                                   # CV 2 in progress
    assert client.post(f"/api/tasks/{task['id']}/cancel").json()["cancel_requested"]
    gate.release()                                        # CV 2 finishes; CV 3 never starts

    done = wait_for(task["id"], "cancelled", "done", "error")
    assert done["status"] == "cancelled"
    assert done["result"]["extracted"] == 2 and done["result"]["cancelled"]
    processed = JsonState(settings.state_folder / "processed_files.json").data
    assert sum(1 for v in processed.values() if v["status"] == "ok") == 2
    with session_scope() as session:
        assert session.scalar(select(PipelineRun.status)) == "cancelled"


def test_the_next_extract_picks_up_where_cancel_stopped(gate):
    put_cvs(2)
    task = start("/api/extract")
    gate.wait_started()
    client.post(f"/api/tasks/{task['id']}/cancel")
    gate.release()
    wait_for(task["id"], "cancelled")

    again = start("/api/extract")
    gate.step()
    finished = wait_for(again["id"], "done")
    assert finished["result"]["extracted"] == 1 and finished["result"]["skipped_already_done"] == 1


def test_pause_waits_after_the_current_cv_and_resume_finishes(gate):
    put_cvs(2)
    task = start("/api/extract")
    gate.wait_started()
    assert client.post(f"/api/tasks/{task['id']}/pause").json()["pause_requested"]
    gate.release()
    paused = wait_for(task["id"], "paused")
    assert paused["pause_requested"]

    assert client.post("/api/fetch", json={}).status_code == 409   # a paused run still holds the pipeline

    resumed = client.post(f"/api/tasks/{task['id']}/resume").json()
    assert not resumed["pause_requested"]
    gate.step()
    finished = wait_for(task["id"], "done")
    assert finished["result"]["extracted"] == 2 and not finished["pause_requested"]


def test_cancel_while_paused_stops_at_once(gate):
    put_cvs(2)
    task = start("/api/extract")
    gate.wait_started()
    client.post(f"/api/tasks/{task['id']}/pause")
    gate.release()
    wait_for(task["id"], "paused")
    client.post(f"/api/tasks/{task['id']}/cancel")
    assert wait_for(task["id"], "cancelled")["result"]["extracted"] == 1


def test_only_running_pipeline_runs_can_be_controlled():
    assert client.post("/api/tasks/nope/pause").status_code == 404
    upload = client.post("/api/cvs/upload", files=[("files", ("cv.pdf", pdf_bytes("x"), "application/pdf"))]).json()
    tasks.get(upload["id"]).future.result(timeout=30)
    assert client.post(f"/api/tasks/{upload['id']}/cancel").status_code == 409
    assert client.post(f"/api/tasks/{upload['id']}/stop").status_code == 422


# ---------- fetch ----------

@pytest.fixture
def mailbox(monkeypatch):
    connects = []

    class CountingMailbox(FakeMailbox):
        def login(self, *a, **k):
            connects.append(1)
            return self

    monkeypatch.setattr(mail_fetcher, "MailBox", CountingMailbox)
    monkeypatch.setattr(mail_fetcher, "BATCH_SIZE", 1)
    CountingMailbox.messages = {i: mail(i, [att(f"cv{i}.pdf", pdf(f"cv {i}"))], subject=f"CV {i}") for i in (1, 2, 3)}
    CountingMailbox.uidvalidity = 100
    return connects


def test_cancel_a_fetch_saves_progress_and_the_next_fetch_carries_on(mailbox, gate):
    task = start("/api/fetch")
    gate.wait_started()                                   # mail 1's CV is being extracted
    client.post(f"/api/tasks/{task['id']}/cancel")
    gate.release()
    done = wait_for(task["id"], "cancelled")
    assert done["result"]["cancelled"] and done["result"]["mails_scanned"] == 1
    state = JsonState(settings.state_folder / "mail_state.json").data
    assert next(iter(state.values()))["last_uid"] == 1

    again = start("/api/fetch")
    gate.step()
    gate.step()
    finished = wait_for(again["id"], "done")
    assert finished["result"]["mails_scanned"] == 2 and finished["result"]["attachments_saved"] == 2


def test_a_paused_fetch_reconnects_to_the_mailbox_when_resumed(mailbox, gate):
    task = start("/api/fetch")
    gate.wait_started()
    client.post(f"/api/tasks/{task['id']}/pause")
    gate.release()
    wait_for(task["id"], "paused")
    connects_while_paused = len(mailbox)
    client.post(f"/api/tasks/{task['id']}/resume")
    gate.step()
    gate.step()
    finished = wait_for(task["id"], "done")
    assert finished["result"]["mails_scanned"] == 3
    assert len(mailbox) == connects_while_paused + 1


def test_cancelling_run_both_skips_the_extract_step(mailbox, gate):
    task = start("/api/run")
    gate.wait_started()
    client.post(f"/api/tasks/{task['id']}/cancel")
    gate.release()
    done = wait_for(task["id"], "cancelled")
    assert done["result"]["extract"] is None and done["result"]["fetch"]["cancelled"]
