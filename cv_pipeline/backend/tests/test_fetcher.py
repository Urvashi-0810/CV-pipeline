"""
Tests the mail fetcher against a FAKE mailbox (no real IMAP server needed):
  - first run downloads CV attachments only (not logos / txt files)
  - second run with no new mail downloads nothing (UID tracking works)
  - new mail -> only that mail is processed
  - same CV re-sent in another mail -> duplicate skipped (hash dedupe)
  - UIDVALIDITY change -> rescan, but still no duplicates
"""
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.fetcher import mail_fetcher


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
        self.folder = SimpleNamespace(status=lambda *a, **k: {"UIDVALIDITY": FakeMailbox.uidvalidity, "MESSAGES": 0})

    def login(self, *a, **k):
        return self

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
    for f in settings.state_folder.glob("*.json"):
        f.unlink()
    settings.cv_folder.mkdir(parents=True, exist_ok=True)
    for f in settings.cv_folder.iterdir():
        f.unlink()
    yield


BIG_IMG = b"\x89PNG" + b"0" * 60_000


def test_fetch_tracking_and_dedupe():
    FakeMailbox.messages = {
        1: mail(1, [att("John CV.pdf", b"%PDF john"), att("logo.png", b"\x89PNG small", "inline")]),
        2: mail(2, []),                                             # no attachment
        3: mail(3, [att("notes.txt", b"hello"), att("Jane.docx", b"PK jane")]),
        4: mail(4, [att("scan.png", BIG_IMG)]),                      # real image CV
    }
    r = mail_fetcher.fetch_cvs()
    assert r.attachments_saved == 3 and r.mails_scanned == 4 and r.last_uid == 4
    names = sorted(p.name for p in settings.cv_folder.iterdir())
    assert any("John CV.pdf" in n for n in names) and not any("logo" in n or "notes" in n for n in names)

    # run again, nothing new
    r = mail_fetcher.fetch_cvs()
    assert r.mails_scanned == 0 and r.attachments_saved == 0

    # one new mail + the same John CV re-sent
    FakeMailbox.messages[5] = mail(5, [att("Raj.pdf", b"%PDF raj")])
    FakeMailbox.messages[6] = mail(6, [att("john_resend.pdf", b"%PDF john")])
    r = mail_fetcher.fetch_cvs()
    assert r.mails_scanned == 2 and r.attachments_saved == 1 and r.duplicates_skipped == 1 and r.last_uid == 6


def test_uidvalidity_change_rescans_without_duplicates():
    FakeMailbox.messages = {1: mail(1, [att("a.pdf", b"%PDF a")])}
    assert mail_fetcher.fetch_cvs().attachments_saved == 1
    FakeMailbox.uidvalidity = 999                 # server reset the folder
    r = mail_fetcher.fetch_cvs()
    assert r.mails_scanned == 1 and r.attachments_saved == 0 and r.duplicates_skipped == 1


def test_subject_filter(monkeypatch):
    monkeypatch.setattr(settings, "subject_keywords", ["resume", "application"])
    FakeMailbox.messages = {
        1: mail(1, [att("a.pdf", b"%PDF a")], subject="Invoice March"),
        2: mail(2, [att("b.pdf", b"%PDF b")], subject="Job Application - Python dev"),
    }
    assert mail_fetcher.fetch_cvs().attachments_saved == 1
