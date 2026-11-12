"""
Tests the mail fetcher against a FAKE mailbox (no real IMAP server needed):
  - first run downloads CV attachments only (not logos / txt files)
  - second run with no new mail downloads nothing (UID tracking works)
  - new mail -> only that mail is processed
  - same CV re-sent in another mail -> duplicate skipped (hash dedupe)
  - UIDVALIDITY change -> rescan, but still no duplicates
  - mail details reach the Excel, and the register follows the file after its rename

Fetching runs extraction after each batch, so these run with the LLM mocked.
"""
import io
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import mock_llm
from app.core.config import settings
from app.fetcher import mail_fetcher
from app.storage.excel_store import load_candidates

mock_llm.start()


def pdf(text):
    """A real one-page PDF: the fetcher validates and merges attachments."""
    from reportlab.pdfgen import canvas
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, invariant=True)
    page.drawString(30, 800, text)
    page.save()
    return buffer.getvalue()


def png():
    """A real image over the 40 KB minimum."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.frombytes("RGB", (300, 300), os.urandom(300 * 300 * 3)).save(buffer, "PNG")
    return buffer.getvalue()


def att(name, data, disposition="attachment"):
    return SimpleNamespace(filename=name, payload=data, size=len(data), content_disposition=disposition)


def mail(uid, attachments, subject="Application", sender="cand@example.com"):
    return SimpleNamespace(
        uid=str(uid), subject=subject, from_=sender, from_values=SimpleNamespace(name="Cand"),
        date=datetime(2026, 9, 10, tzinfo=timezone.utc), attachments=attachments,
    )


class FakeMailbox:
    """Implements just the parts of imap_tools.MailBox our fetcher uses."""
    messages: dict = {}
    uidvalidity = 100

    def __init__(self, *a, **k):
        self.folder = SimpleNamespace(set=lambda *a, **k: None)
        self.client = SimpleNamespace(
            state="SELECTED",
            response=lambda name: ("OK", [str(FakeMailbox.uidvalidity).encode()]),
        )

    def login(self, *a, **k):
        return self

    def logout(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def uids(self, criteria):
        s = str(criteria)
        m = re.search(r"UID (\d+):\*", s)
        all_uids = sorted(self.messages)
        if m:
            start = int(m.group(1))
            hits = [u for u in all_uids if u >= start]
            return [str(u) for u in (hits or all_uids[-1:])]   # real IMAP returns newest mail for N:* if nothing new
        return [str(u) for u in all_uids]

    def fetch(self, criteria, **k):
        wanted = [int(x) for x in re.search(r"UID ([\d,]+)", str(criteria)).group(1).split(",")]
        return [self.messages[u] for u in wanted]


@pytest.fixture(autouse=True)
def fake_mailbox(monkeypatch):
    monkeypatch.setattr(mail_fetcher, "MailBox", FakeMailbox)
    FakeMailbox.messages = {}
    FakeMailbox.uidvalidity = 100
    for folder in (settings.cv_folder, settings.state_folder, settings.output_excel.parent):
        shutil.rmtree(folder, ignore_errors=True)
    settings.ensure_dirs()
    yield


def manifest():
    path = settings.state_folder / "attachments_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["by_hash"]


def test_fetch_tracking_and_dedupe():
    john = pdf("John CV")
    FakeMailbox.messages = {
        1: mail(1, [att("John CV.pdf", john), att("logo.png", b"\x89PNG small", "inline")]),
        2: mail(2, []),                                             # no attachment
        3: mail(3, [att("notes.txt", b"hello"), att("Jane.pdf", pdf("Jane CV"))]),
        4: mail(4, [att("scan.png", png())]),                        # real image CV
    }
    r = mail_fetcher.fetch_cvs()
    assert r.attachments_saved == 3 and r.mails_scanned == 4 and r.last_uid == 4
    originals = sorted(name for entry in manifest().values() for name in entry["original_names"])
    assert originals == ["Jane.pdf", "John CV.pdf", "scan.png"]

    # run again, nothing new
    r = mail_fetcher.fetch_cvs()
    assert r.mails_scanned == 0 and r.attachments_saved == 0

    # one new mail + the same John CV re-sent
    FakeMailbox.messages[5] = mail(5, [att("Raj.pdf", pdf("Raj CV"))])
    FakeMailbox.messages[6] = mail(6, [att("john_resend.pdf", john)])
    r = mail_fetcher.fetch_cvs()
    assert r.mails_scanned == 2 and r.attachments_saved == 1 and r.duplicates_skipped == 1 and r.last_uid == 6


def test_register_follows_the_renamed_file():
    FakeMailbox.messages = {1: mail(1, [att("John CV.pdf", pdf("John CV"))])}
    mail_fetcher.fetch_cvs()

    entry = next(iter(manifest().values()))
    assert Path(entry["file"]).name == "Senior_Software_Engineer_P1234567_Rahul_Sharma.pdf"
    assert (settings.cv_folder / entry["file"]).exists()
    assert len(list(settings.cv_folder.rglob("*.pdf"))) == 1


def test_resent_cv_in_a_later_batch_of_the_same_run_is_a_duplicate(monkeypatch):
    """The fetcher must not lose the extractor's path update when it saves the manifest."""
    monkeypatch.setattr(mail_fetcher, "BATCH_SIZE", 1)
    john = pdf("John CV")
    FakeMailbox.messages = {
        1: mail(1, [att("John CV.pdf", john)], subject="Application"),
        2: mail(2, [att("john_resend.pdf", john)], subject="Fwd Application"),
    }
    r = mail_fetcher.fetch_cvs()
    assert r.attachments_saved == 1 and r.duplicates_skipped == 1
    assert len(list(settings.cv_folder.rglob("*.pdf"))) == 1


def test_mail_details_reach_the_excel():
    FakeMailbox.messages = {1: mail(1, [att("John CV.pdf", pdf("John CV"))], subject="Welder CV")}
    mail_fetcher.fetch_cvs()

    row = load_candidates().iloc[0]
    assert row["Received From"] == "cand@example.com"
    assert row["Mail Subject"] == "Welder CV"
    assert row["Mail Date"] == "2026-09-10 00:00"


def test_progress_counts_mails_and_the_result_adds_up_extraction(monkeypatch):
    monkeypatch.setattr(mail_fetcher, "BATCH_SIZE", 2)
    FakeMailbox.messages = {
        1: mail(1, [att("a.pdf", pdf("a"))], subject="A"),
        2: mail(2, []),
        3: mail(3, [att("b.pdf", pdf("b"))], subject="B"),
    }
    seen = []

    r = mail_fetcher.fetch_cvs(progress=lambda done, total, current: seen.append((done, total, current)))

    assert seen[0] == (0, 3, "Scanning mail")
    assert [s[:2] for s in seen if s[2].startswith("Scanning mail ")] == [(1, 3), (2, 3), (3, 3)]
    assert any(current.startswith("Extracting CVs from mails 1-2") for _, _, current in seen)
    assert all(total == 3 for _, total, _ in seen)
    assert (r.mails_to_scan, r.cvs_extracted, r.cvs_failed, r.stopped_early) == (3, 2, 0, None)


def test_a_failed_batch_is_reported(monkeypatch):
    FakeMailbox.messages = {1: mail(1, [att("a.pdf", pdf("a"))])}
    monkeypatch.setattr(FakeMailbox, "fetch", lambda self, *a, **k: (_ for _ in ()).throw(OSError("connection reset")))
    r = mail_fetcher.fetch_cvs()
    assert r.stopped_early == "Mail batch 1-1 failed: connection reset" and r.mails_scanned == 0


def test_uidvalidity_change_rescans_without_duplicates():
    FakeMailbox.messages = {1: mail(1, [att("a.pdf", pdf("a"))])}
    assert mail_fetcher.fetch_cvs().attachments_saved == 1
    FakeMailbox.uidvalidity = 999                 # server reset the folder
    r = mail_fetcher.fetch_cvs()
    assert r.mails_scanned == 1 and r.attachments_saved == 0 and r.duplicates_skipped == 1


def test_subject_filter(monkeypatch):
    monkeypatch.setattr(settings, "subject_keywords", ["resume", "application"])
    FakeMailbox.messages = {
        1: mail(1, [att("a.pdf", pdf("a"))], subject="Invoice March"),
        2: mail(2, [att("b.pdf", pdf("b"))], subject="Job Application - Python dev"),
    }
    assert mail_fetcher.fetch_cvs().attachments_saved == 1
