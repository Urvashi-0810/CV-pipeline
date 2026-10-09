from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from imap_tools import AND, MailBox
from imap_tools import U as UidRange

import imaplib
import time

from app.core.config import (
    CV_EXTENSIONS,
    IMAGE_EXTENSIONS,
    settings,
)

from app.core.logger import get_logger
from app.core.state import JsonState, file_sha256


log = get_logger("fetcher")


# ============================================================
# Configuration
# ============================================================

BATCH_SIZE = 50
MAX_IMAP_RETRIES = 5
IMAP_RETRY_DELAY = 10

# Ignore very small images because they are usually
# email signatures, logos, banners, etc.
MIN_IMAGE_BYTES = 40 * 1024


# ============================================================
# Result object
# ============================================================

@dataclass
class FetchResult:

    mails_scanned: int = 0
    attachments_saved: int = 0
    duplicates_skipped: int = 0
    last_uid: int = 0
    saved_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


# ============================================================
# Filename helpers
# ============================================================

def _safe_filename(name: str) -> str:
    """
    Remove Windows-invalid characters from a filename.
    """

    if not name:
        return "attachment"

    name = re.sub(
        r'[<>:"/\\|?*\r\n\t]+',
        "_",
        str(name)
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip(" ._")

    return name[:150] or "attachment"


def _safe_folder_name(name: str) -> str:
    """
    Convert a string into a Windows-safe folder name.

    Example:
        candidate@gmail.com
        Java Developer - candidate@gmail.com - UID5629
    """

    if not name:
        return "unknown"

    name = re.sub(
        r'[<>:"/\\|?*\r\n\t]+',
        "_",
        str(name)
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip(" ._")

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }

    if name.upper() in reserved:
        name = "_" + name

    return name[:150] or "unknown"

def _safe_subject(
    subject: str,
    max_length: int = 70,
) -> str:
    """
    Convert email subject into a short,
    Windows-safe folder name.
    """

    if not subject:
        return "No Subject"

    subject = str(subject).strip()

    # Remove RE:, FW:, FWD:
    subject = re.sub(
        r"^(re|fw|fwd)\s*:\s*",
        "",
        subject,
        flags=re.IGNORECASE,
    ).strip()

    # Remove Windows-invalid characters
    subject = re.sub(
        r'[<>:"/\\|?*\r\n\t]+',
        "_",
        subject,
    )

    # Normalize spaces
    subject = re.sub(
        r"\s+",
        " ",
        subject,
    )

    # Normalize repeated underscores
    subject = re.sub(
        r"_+",
        "_",
        subject,
    )

    # Remove invalid ending characters
    subject = subject.strip(
        " ._"
    )

    if not subject:
        return "No Subject"

    # Shorten long subject
    if len(subject) > max_length:

        subject = (
            subject[:max_length]
            .rstrip(" ._-")
        )

        subject += "..."

    return subject or "No Subject"

    """
    Convert email subject into a short, safe Windows folder name.

    Examples:
        "RE: Urgent Requirement for Senior Java Developer"
            -> "Urgent Requirement for Senior Java Developer"

        Very long subjects are truncated to max_length.

    Args:
        subject: Original email subject
        max_length: Maximum subject length

    Returns:
        Sanitized and shortened subject.
    """

    if not subject:
        return "No Subject"

    # Convert to string
    subject = str(subject).strip()

    # Remove common email prefixes
    subject = re.sub(
        r"^(re|fw|fwd)\s*:\s*",
        "",
        subject,
        flags=re.IGNORECASE,
    ).strip()

    # Remove Windows-invalid characters
    subject = re.sub(
        r'[<>:"/\\|?*\r\n\t]+',
        "_",
        subject,
    )

    # Replace multiple spaces/underscores
    subject = re.sub(r"\s+", " ", subject)
    subject = re.sub(r"_+", "_", subject)

    # Remove unwanted characters from beginning/end
    subject = subject.strip(" ._")

    if not subject:
        return "No Subject"

    # Shorten the subject
    if len(subject) > max_length:
        subject = subject[:max_length].rstrip(" ._-")

        # Add indication that it was shortened
        subject = subject + "..."

    return subject or "No Subject"


def _safe_sender_email(sender: str | None) -> str:
    """
    Extract and sanitize sender email address.
    """

    if not sender:
        return "unknown_sender"

    sender = sender.strip()

    # Example:
    # John Smith <john@gmail.com>
    match = re.search(
        r"<([^<>@\s]+@[^<>@\s]+)>",
        sender,
    )

    if match:
        sender = match.group(1)

    sender = re.sub(
        r'[<>:"/\\|?*\r\n\t]+',
        "_",
        sender,
    )

    return sender.strip(" ._") or "unknown_sender"


def _unique_path(folder: Path, filename: str) -> Path:
    """
    Return a unique file path.

    Example:
        Resume.pdf
        Resume_1.pdf
        Resume_2.pdf
    """

    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    path = folder / filename
    counter = 1

    while path.exists():

        path = (
            folder
            / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
        )

        counter += 1

    return path


# ============================================================
# Mail filters
# ============================================================

def _mail_matches_filters(msg) -> bool:
    """
    Apply optional SUBJECT_KEYWORDS and FROM_FILTER.
    """

    if settings.subject_keywords:

        subject = (
            msg.subject or ""
        ).lower()

        if not any(
            keyword.lower() in subject
            for keyword in settings.subject_keywords
        ):
            return False

    if settings.from_filter:

        sender = (
            msg.from_ or ""
        ).lower()

        if not any(
            value.lower() in sender
            for value in settings.from_filter
        ):
            return False

    return True


# ============================================================
# Attachment filters
# ============================================================

def _is_cv_attachment(att) -> bool:
    """
    Return True if attachment looks like a CV.
    """

    filename = att.filename or ""

    extension = Path(
        filename
    ).suffix.lower()

    if extension not in CV_EXTENSIONS:
        return False

    # Maximum attachment size
    if att.size > (
        settings.max_attachment_mb
        * 1024
        * 1024
    ):

        log.warning(
            "Skipping %s: larger than %s MB",
            filename,
            settings.max_attachment_mb,
        )

        return False

    # Ignore small/inline images
    if extension in IMAGE_EXTENSIONS:

        disposition = (
            att.content_disposition or ""
        ).lower()

        if disposition == "inline":
            return False

        if att.size < MIN_IMAGE_BYTES:
            return False

    return True


# ============================================================
# MailFetcher
# ============================================================
class MailFetcher:

    def __init__(self):

        settings.ensure_dirs()

        self.state = JsonState(
            settings.state_folder
            / "mail_state.json"
        )

        self.manifest = JsonState(
            settings.state_folder
            / "attachments_manifest.json"
        )

        # Unique key for mailbox + folder
        self.key = (
            f"{settings.imap_user}|"
            f"{settings.imap_host}|"
            f"{settings.imap_folder}"
        )

    # ========================================================
    # IMAP connection
    # ========================================================

    def _connect_mailbox(self):
        """
        Create a fresh IMAP connection to Zoho.
        """

        log.info(
        "Connecting to %s as %s (folder: %s)",
        settings.imap_host,
        settings.imap_user,
        settings.imap_folder,
        )

        mb = MailBox(
            settings.imap_host,
            port=settings.imap_port,
        )

        mb.login(
            settings.imap_user,
            settings.imap_password,
            initial_folder=settings.imap_folder,
        )

        log.info(
            "IMAP state after login: %s",
            getattr(
                mb.client,
                "state",
                "UNKNOWN",
            ),
        )  

        # Explicitly select the mailbox
        mb.folder.set(
            settings.imap_folder
        )

        log.info(
            "IMAP connection established and folder selected: %s",
            settings.imap_folder,
        )

        return mb

    # ========================================================
    # Close IMAP connection safely
    # ========================================================


    def _safe_logout(self, mb):

        if mb is None:
            return

        try:
            mb.logout()

        except Exception as exc:

            log.warning(
                "Could not logout IMAP connection: %s",
                exc,
            )
        

    # ========================================================
    # Public method
    # ========================================================

    def run(
        self,
        reset: bool = False,
    ) -> FetchResult:

        if not settings.imap_user or not settings.imap_password:
            raise RuntimeError("IMAP_USER / IMAP_PASSWORD missing in .env")

        result = FetchResult()
        mb = None

        try:
            mb = self._connect_mailbox()

            state = getattr(mb.client, "state", "UNKNOWN")
            log.info("IMAP state after connection: %s", state)

            if state == "LOGOUT":
                raise RuntimeError(
                    "IMAP connection is already in LOGOUT state after connection."
                )

            if state != "SELECTED":
                mb.folder.set(settings.imap_folder)

            # Get UIDVALIDITY from the SELECT response.
            # Do not issue an additional STATUS command to Zoho.
            response = mb.client.response("UIDVALIDITY")

            if not response or len(response) < 2 or not response[1]:
                raise RuntimeError(
                    "Could not obtain UIDVALIDITY from IMAP SELECT response."
                )

            uidvalidity = int(response[1][0])

            log.info("IMAP UIDVALIDITY: %s", uidvalidity)

            saved = self.state.get(
                self.key,
                {},
            )

            if reset or saved.get("uidvalidity") != uidvalidity:
                if saved and not reset:
                    log.warning(
                        "UIDVALIDITY changed. Starting from UID 0."
                    )
                last_uid = 0
            else:
                last_uid = int(saved.get("last_uid", 0))

            result.last_uid = last_uid
            log.info("Starting from UID: %d", last_uid)

            criteria = self._build_criteria(last_uid)
            log.info("Search criteria: %s", criteria)

            uids = sorted(int(uid) for uid in mb.uids(criteria))

            date_range_mode = (
                bool(settings.fetch_since_date)
                and bool(settings.fetch_until_date)
            )

            if not date_range_mode:
                uids = [uid for uid in uids if uid > last_uid]

            log.info("%d mail(s) to scan", len(uids))

            if not uids:
                log.info("No new matching emails found.")
                self._save_state(uidvalidity, result)
                return result

            for i in range(0, len(uids), BATCH_SIZE):

                batch = uids[i:i + BATCH_SIZE]
                batch_start = i + 1
                batch_end = min(i + BATCH_SIZE, len(uids))

                log.info(
                    "Processing batch %d-%d of %d",
                    batch_start,
                    batch_end,
                    len(uids),
                )

                batch_success = False

                for attempt in range(1, MAX_IMAP_RETRIES + 1):

                    try:
                        if mb is None:
                            mb = self._connect_mailbox()

                        state = getattr(
                            mb.client,
                            "state",
                            "UNKNOWN",
                        )

                        if state == "LOGOUT":
                            raise imaplib.IMAP4.abort(
                                "IMAP connection is in LOGOUT state"
                            )

                        if state != "SELECTED":
                            mb.folder.set(settings.imap_folder)

                        batch_last_uid = result.last_uid

                        for msg in mb.fetch(
                            AND(
                                uid=[str(uid) for uid in batch]
                            ),
                            mark_seen=False,
                            bulk=False,
                        ):

                            try:
                                self._process_message(
                                    msg,
                                    result,
                                )

                            except Exception as exc:
                                log.exception(
                                    "Failed processing email UID %s: %s",
                                    msg.uid,
                                    exc,
                                )

                            result.mails_scanned += 1
                            batch_last_uid = max(
                                batch_last_uid,
                                int(msg.uid),
                            )

                        result.last_uid = batch_last_uid
                        batch_success = True

                        log.info(
                            "Batch %d-%d completed successfully.",
                            batch_start,
                            batch_end,
                        )

                        break

                    except imaplib.IMAP4.abort as exc:

                        log.error(
                            "IMAP connection aborted while processing "
                            "batch %d-%d. Attempt %d/%d. Error: %s",
                            batch_start,
                            batch_end,
                            attempt,
                            MAX_IMAP_RETRIES,
                            exc,
                        )

                        self._close_mailbox(mb)
                        mb = None

                        if attempt < MAX_IMAP_RETRIES:
                            log.info(
                                "Waiting %d seconds before reconnecting...",
                                IMAP_RETRY_DELAY,
                            )
                            time.sleep(IMAP_RETRY_DELAY)

                            try:
                                mb = self._connect_mailbox()
                                log.info(
                                    "IMAP reconnect successful. State=%s.",
                                    getattr(
                                        mb.client,
                                        "state",
                                        "UNKNOWN",
                                    ),
                                )

                            except Exception as reconnect_exc:
                                log.exception(
                                    "IMAP reconnect failed on attempt "
                                    "%d/%d: %s",
                                    attempt,
                                    MAX_IMAP_RETRIES,
                                    reconnect_exc,
                                )
                                mb = None

                                if attempt < MAX_IMAP_RETRIES:
                                    time.sleep(IMAP_RETRY_DELAY)

                        else:
                            log.error(
                                "Batch %d-%d failed after %d attempts.",
                                batch_start,
                                batch_end,
                                MAX_IMAP_RETRIES,
                            )

                    except Exception as exc:

                        log.exception(
                            "Unexpected error while processing "
                            "batch %d-%d: %s",
                            batch_start,
                            batch_end,
                            exc,
                        )

                        break

                self._save_state(
                    uidvalidity,
                    result,
                )

                if not batch_success:
                    log.warning(
                        "Batch %d-%d was not completed successfully. "
                        "Continuing with next batch.",
                        batch_start,
                        batch_end,
                    )
                else:
                    log.info(
                        "Progress saved after batch %d-%d. Last UID=%d",
                        batch_start,
                        batch_end,
                        result.last_uid,
                    )

            self._save_state(
                uidvalidity,
                result,
            )

        finally:

            if mb is not None:
                log.info("Closing IMAP connection.")
                self._close_mailbox(mb)

        log.info(
            "Done. scanned=%d saved=%d duplicates=%d last_uid=%d",
            result.mails_scanned,
            result.attachments_saved,
            result.duplicates_skipped,
            result.last_uid,
        )

        return result


    def _close_mailbox(self, mb):
        """
        Safely close an IMAP connection.
        """
        if mb is None:
            return

        try:
            state = getattr(
                mb.client,
                "state",
                "UNKNOWN",
            )

            if state == "LOGOUT":
                return

            mb.logout()

        except Exception as exc:
            log.warning(
                "Could not close IMAP connection safely: %s",
                exc,
            )

    def _build_criteria(
        self,
        last_uid: int,
    ):

        # ----------------------------------------------------
        # DATE RANGE MODE
        # ----------------------------------------------------

        if (
            settings.fetch_since_date
            and settings.fetch_until_date
        ):

            since = datetime.strptime(
                settings.fetch_since_date,
                "%Y-%m-%d",
            ).date()

            until = datetime.strptime(
                settings.fetch_until_date,
                "%Y-%m-%d",
            ).date()

            log.info(
                "Downloading emails from %s to %s",
                since,
                until,
            )

            return AND(
                date_gte=since,
                date_lt=until,
            )

        # ----------------------------------------------------
        # NORMAL INCREMENTAL MODE
        # ----------------------------------------------------

        if last_uid > 0:

            return AND(
                uid=UidRange(
                    last_uid + 1,
                    "*",
                )
            )

        # ----------------------------------------------------
        # FIRST RUN WITH ONLY START DATE
        # ----------------------------------------------------

        if settings.fetch_since_date:

            since = datetime.strptime(
                settings.fetch_since_date,
                "%Y-%m-%d",
            ).date()

            return AND(
                date_gte=since,
            )

        # ----------------------------------------------------
        # NO FILTER
        # ----------------------------------------------------

        return AND(
            all=True
        )

    # ========================================================
    # Process one email
    # ========================================================

    def _process_message(
        self,
        msg,
        result: FetchResult,
    ):

        # ----------------------------------------------------
        # Basic checks
        # ----------------------------------------------------

        if not msg.attachments:
            return

        if not _mail_matches_filters(msg):
            return

        # ----------------------------------------------------
        # Find CV attachments
        # ----------------------------------------------------

        cv_attachments = []

        for att in msg.attachments:

            try:

                if _is_cv_attachment(att):
                    cv_attachments.append(att)

            except Exception as exc:

                log.warning(
                    "Could not inspect attachment %s: %s",
                    getattr(
                        att,
                        "filename",
                        "unknown",
                    ),
                    exc,
                )

        if not cv_attachments:
            return

        # ====================================================
        # EMAIL DATE
        # ====================================================

        if msg.date:

            email_date = msg.date

        else:

            email_date = datetime.now()

        month_folder = email_date.strftime(
            "%B-%Y"
        )

        # ====================================================
        # MAIN EMAIL ACCOUNT FOLDER
        # ====================================================

        email_account = _safe_folder_name(
            settings.imap_user
        )

        email_account_path = (
            settings.cv_folder
            / email_account
        )

        email_account_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ====================================================
        # MONTH FOLDER
        # ====================================================

        month_path = (
            email_account_path
            / month_folder
        )

        month_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ====================================================
        # DESTINATION FOLDER
        # ====================================================

        if len(cv_attachments) == 1:

            # ------------------------------------------------
            # ONE ATTACHMENT
            #
            # Save directly into:
            #
            # IMAP_USER/
            #     Month/
            #         resume.pdf
            # ------------------------------------------------

            destination_folder = month_path

            log.info(
                "Email UID %s has 1 CV attachment. "
                "Saving directly to month folder: %s",
                msg.uid,
                destination_folder,
            )

        else:

            # ------------------------------------------------
            # MULTIPLE ATTACHMENTS
            #
            # Save into:
            #
            # IMAP_USER/
            #     Month/
            #         Subject - sender@email.com/
            #             file1.pdf
            #             file2.pdf
            # ------------------------------------------------

            subject = _safe_subject(
                msg.subject or "No Subject",
                max_length=70,
            )

            sender_email = _safe_sender_email(
                msg.from_
            )

            folder_name = (
                f"{subject} - {sender_email}"
            )

            folder_name = _safe_folder_name(
                folder_name
            )

            # Final protection against excessively long
            # Windows folder names.

            folder_name = (
                folder_name[:120]
                .rstrip(" ._")
            )

            if not folder_name:
                folder_name = "No Subject"

            destination_folder = (
                month_path / folder_name
            )

            destination_folder.mkdir(
                parents=True,
                exist_ok=True,
            )

            log.info(
                "Email UID %s has %d CV attachments. "
                "Saving to subject/sender folder: %s",
                msg.uid,
                len(cv_attachments),
                destination_folder,
            )

        # ====================================================
        # Load manifest
        # ====================================================

        hashes = self.manifest.get(
            "by_hash",
            {},
        )

        # ====================================================
        # Process attachments
        # ====================================================

        for att in cv_attachments:

            try:

                filename = (
                    att.filename
                    or "attachment"
                )

                payload = att.payload

                if not payload:

                    log.warning(
                        "Empty attachment skipped: %s",
                        filename,
                    )

                    continue

                # --------------------------------------------
                # Calculate SHA-256
                # --------------------------------------------

                digest = file_sha256(
                    payload
                )

                # --------------------------------------------
                # Duplicate content check
                # --------------------------------------------

                if digest in hashes:

                    existing_file = (
                        hashes[digest].get(
                            "file",
                            "",
                        )
                    )

                    # Stored path is relative to cv_folder

                    existing_path = Path(
                        existing_file
                    )

                    if not existing_path.is_absolute():

                        existing_path = (
                            settings.cv_folder
                            / existing_path
                        )

                    # ----------------------------------------
                    # Physical file still exists
                    # ----------------------------------------

                    if existing_path.exists():

                        result.duplicates_skipped += 1

                        log.info(
                            "Duplicate skipped: %s "
                            "(same content as %s)",
                            filename,
                            existing_path,
                        )

                        continue

                    # ----------------------------------------
                    # Manifest entry is stale
                    # ----------------------------------------

                    else:

                        log.warning(
                            "Manifest contains stale entry "
                            "for %s. File does not exist: %s. "
                            "Downloading again.",
                            filename,
                            existing_path,
                        )

                        del hashes[digest]

                # --------------------------------------------
                # Safe filename
                # --------------------------------------------

                safe_name = _safe_filename(
                    filename
                )

                # --------------------------------------------
                # Avoid overwriting another file with same
                # filename but different content
                # --------------------------------------------

                path = _unique_path(
                    destination_folder,
                    safe_name,
                )

                # --------------------------------------------
                # Save attachment
                # --------------------------------------------

                path.write_bytes(
                    payload
                )

                # --------------------------------------------
                # Relative path for manifest
                # --------------------------------------------

                try:

                    relative_path = str(
                        path.relative_to(
                            settings.cv_folder
                        )
                    )

                except ValueError:

                    relative_path = str(
                        path
                    )

                # --------------------------------------------
                # Save manifest information
                # --------------------------------------------

                hashes[digest] = {

                    "file": relative_path,

                    "original_name": filename,

                    "from": msg.from_,

                    "from_name": (
                        msg.from_values.name
                        if msg.from_values
                        else ""
                    ),

                    "subject": (
                        msg.subject
                        or ""
                    ),

                    "mail_date": (
                        msg.date.isoformat()
                        if msg.date
                        else None
                    ),

                    "uid": int(
                        msg.uid
                    ),

                    "folder": (
                        settings.imap_folder
                    ),

                    "downloaded_at": (
                        datetime.now().isoformat(
                            timespec="seconds"
                        )
                    ),
                }

                result.attachments_saved += 1

                result.saved_files.append(
                    str(path)
                )

                log.info(
                    "Saved CV: %s",
                    path,
                )

            except Exception as exc:

                log.exception(
                    "Failed downloading attachment %s "
                    "from email UID %s: %s",
                    getattr(
                        att,
                        "filename",
                        "unknown",
                    ),
                    msg.uid,
                    exc,
                )

                continue

        # ====================================================
        # Save manifest after processing email
        # ====================================================

        self.manifest.set(
            "by_hash",
            hashes,
        )

    # ========================================================
    # Save state
    # ========================================================

    def _save_state(
        self,
        uidvalidity: int,
        result: FetchResult,
    ):

        # ----------------------------------------------------
        # Save manifest first.
        # ----------------------------------------------------

        self.manifest.save()

        self.state.set(
            self.key,
            {
                "uidvalidity": uidvalidity,

                "last_uid": result.last_uid,

                "last_run": (
                    datetime.now().isoformat(
                        timespec="seconds"
                    )
                ),

                "total_cvs_in_manifest": len(
                    self.manifest.get(
                        "by_hash",
                        {},
                    )
                ),
            },
        )

        self.state.save()

def fetch_cvs(
    reset: bool = False,
) -> FetchResult:
    """
    Convenience function used by main.py / FastAPI.
    """
    return MailFetcher().run(
        reset=reset
    )     