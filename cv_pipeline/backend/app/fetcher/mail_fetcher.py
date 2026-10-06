"""
STEP 1 - Download CV attachments from a mailbox (Zoho Mail or Gmail) via IMAP.

How "remember where I stopped" works
------------------------------------
Every email in an IMAP folder has a UID (unique id) that only ever increases.
After processing, we store the highest UID we have seen in
data/state/mail_state.json. Next run we only ask the server for UIDs greater
than that -> old mails are never downloaded or even looked at again.

The folder also has a UIDVALIDITY number. If the server ever resets UIDs
(rare - e.g. folder deleted/recreated) UIDVALIDITY changes, and we start
from scratch. Even then nothing is downloaded twice, because every saved
file's SHA-256 hash is recorded in data/state/attachments_manifest.json and
duplicates are skipped.

The manifest also remembers who sent each CV, the subject and the date -
the extractor copies those into Excel.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from imap_tools import AND, MailBox, MailMessage
from imap_tools import U as UidRange

from app.core.config import CV_EXTENSIONS, IMAGE_EXTENSIONS, settings
from app.core.logger import get_logger
from app.core.state import JsonState, file_sha256

log = get_logger("fetcher")

BATCH_SIZE = 50                 # how many mails to download per IMAP request
MIN_IMAGE_BYTES = 40 * 1024     # images smaller than this are logos/signatures, not CVs


@dataclass
class FetchResult:
    mails_scanned: int = 0
    attachments_saved: int = 0
    duplicates_skipped: int = 0
    last_uid: int = 0
    saved_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# ---------------------------------------------------------------- helpers
def _safe_filename(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", name).strip(" ._")
    return name[:150] or "attachment"


def _unique_path(folder: Path, filename: str) -> Path:
    p = folder / filename
    i = 1
    while p.exists():
        p = folder / f"{Path(filename).stem}_{i}{Path(filename).suffix}"
        i += 1
    return p


def _mail_matches_filters(msg: MailMessage) -> bool:
    """Optional SUBJECT_KEYWORDS / FROM_FILTER from .env."""
    if settings.subject_keywords:
        subj = (msg.subject or "").lower()
        if not any(k in subj for k in settings.subject_keywords):
            return False
    if settings.from_filter:
        sender = (msg.from_ or "").lower()
        if not any(f in sender for f in settings.from_filter):
            return False
    return True


def _is_cv_attachment(att) -> bool:
    ext = Path(att.filename or "").suffix.lower()
    if ext not in CV_EXTENSIONS:
        return False
    if att.size > settings.max_attachment_mb * 1024 * 1024:
        log.warning("Skipping %s: larger than %s MB", att.filename, settings.max_attachment_mb)
        return False
    if ext in IMAGE_EXTENSIONS:
        # Inline images (signature logos, banners) are not CVs
        if (att.content_disposition or "").lower() == "inline" or att.size < MIN_IMAGE_BYTES:
            return False
    return True


# ---------------------------------------------------------------- main class
class MailFetcher:
    def __init__(self):
        settings.ensure_dirs()
        self.state = JsonState(settings.state_folder / "mail_state.json")
        self.manifest = JsonState(settings.state_folder / "attachments_manifest.json")
        # unique key per mailbox + folder, so you can switch accounts/folders safely
        self.key = f"{settings.imap_user}|{settings.imap_host}|{settings.imap_folder}"

    # -- public -----------------------------------------------------------
    def run(self, reset: bool = False) -> FetchResult:
        if not settings.imap_user or not settings.imap_password:
            raise RuntimeError("IMAP_USER / IMAP_PASSWORD missing in .env")

        result = FetchResult()
        log.info("Connecting to %s as %s (folder: %s)", settings.imap_host, settings.imap_user, settings.imap_folder)

        with MailBox(settings.imap_host, port=settings.imap_port).login(
            settings.imap_user, settings.imap_password, initial_folder=settings.imap_folder
        ) as mb:
            status = mb.folder.status(settings.imap_folder, ["UIDVALIDITY", "MESSAGES"])
            uidvalidity = int(status["UIDVALIDITY"])

            saved = self.state.get(self.key, {})
            if reset or saved.get("uidvalidity") != uidvalidity:
                if saved and not reset:
                    log.warning("UIDVALIDITY changed -> folder was reset on the server, rescanning (duplicates still skipped)")
                last_uid = 0
            else:
                last_uid = int(saved.get("last_uid", 0))
            result.last_uid = last_uid

            criteria = self._build_criteria(last_uid)
            log.info("Last processed UID = %s  ->  search: %s", last_uid, criteria)

            # 1) ask only for UIDs (cheap), 2) download full mails in batches
            uids = sorted(int(u) for u in mb.uids(criteria))
            uids = [u for u in uids if u > last_uid]   # 'N:*' always returns the newest mail; drop it if already seen
            log.info("%d new mail(s) to scan", len(uids))

            for i in range(0, len(uids), BATCH_SIZE):
                batch = uids[i : i + BATCH_SIZE]
                # mark_seen=False -> we never change read/unread status in your inbox
                for msg in mb.fetch(AND(uid=[str(u) for u in batch]), mark_seen=False, bulk=True):
                    self._process_message(msg, result)
                    result.mails_scanned += 1
                    result.last_uid = max(result.last_uid, int(msg.uid))
                # checkpoint after every batch -> a crash loses at most one batch of progress
                self._save_state(uidvalidity, result)

            self._save_state(uidvalidity, result)

        log.info(
            "Done. scanned=%d saved=%d duplicates=%d last_uid=%d",
            result.mails_scanned, result.attachments_saved, result.duplicates_skipped, result.last_uid,
        )
        return result

    # -- internals ----------------------------------------------------------
    def _build_criteria(self, last_uid: int):
        if last_uid > 0:
            return AND(uid=UidRange(last_uid + 1, "*"))
        if settings.fetch_since_date:
            since = datetime.strptime(settings.fetch_since_date, "%Y-%m-%d").date()
            return AND(date_gte=since)
        return AND(all=True)

    def _process_message(self, msg: MailMessage, result: FetchResult) -> None:
        if not msg.attachments or not _mail_matches_filters(msg):
            return

        hashes: dict = self.manifest.get("by_hash", {})
        for att in msg.attachments:
            if not _is_cv_attachment(att):
                continue
            payload = att.payload
            digest = file_sha256(payload)
            if digest in hashes:
                result.duplicates_skipped += 1
                log.info("Duplicate skipped: %s (same as %s)", att.filename, hashes[digest]["file"])
                continue

            day = (msg.date.date() if msg.date else date.today()).isoformat()
            fname = _safe_filename(f"{day}_uid{msg.uid}_{att.filename}")
            path = _unique_path(settings.cv_folder, fname)
            path.write_bytes(payload)

            hashes[digest] = {
                "file": path.name,
                "original_name": att.filename,
                "from": msg.from_,
                "from_name": msg.from_values.name if msg.from_values else "",
                "subject": msg.subject,
                "mail_date": msg.date.isoformat() if msg.date else None,
                "uid": int(msg.uid),
                "folder": settings.imap_folder,
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            }
            result.attachments_saved += 1
            result.saved_files.append(path.name)
            log.info("Saved %s  (from %s)", path.name, msg.from_)

        self.manifest.set("by_hash", hashes)

    def _save_state(self, uidvalidity: int, result: FetchResult) -> None:
        # Save the manifest FIRST: if we crash between the two writes, the worst case is
        # re-scanning a few mails whose files are then recognised as duplicates.
        self.manifest.save()
        self.state.set(self.key, {
            "uidvalidity": uidvalidity,
            "last_uid": result.last_uid,
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "total_cvs_in_manifest": len(self.manifest.get("by_hash", {})),
        })
        self.state.save()


def fetch_cvs(reset: bool = False) -> FetchResult:
    """Convenience function (the future FastAPI endpoint will call this)."""
    return MailFetcher().run(reset=reset)
