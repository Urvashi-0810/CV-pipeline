from __future__ import annotations
import re
from pypdf.errors import DependencyError
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from imap_tools import AND, MailBox
from imap_tools import U as UidRange
import imaplib
import time
import shutil
import subprocess
import tempfile
import re
from pathlib import Path

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

def safe_filename(value: str) -> str:
    """Convert a candidate field into a Windows-safe filename part."""
    value = str(value or "").strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip(" ._")


def rename_merged_pdf(pdf_path: str, candidate: dict) -> str:
    pdf_path = Path(pdf_path)

    position = safe_filename(candidate.get("current_job_title"))
    passport = safe_filename(candidate.get("passport_number"))
    name = safe_filename(candidate.get("full_name"))

    if not all([position, passport, name]):
        print(
            f"WARNING: Cannot rename {pdf_path.name}; "
            "position, passport, or name is missing."
        )
        return str(pdf_path)

    new_name = f"{position}_{passport}_{name}.pdf"
    new_path = pdf_path.with_name(new_name)

    if new_path.exists() and new_path != pdf_path:
        print(f"WARNING: Target file already exists: {new_path}")
        return str(pdf_path)

    if new_path != pdf_path:
        pdf_path.rename(new_path)
        print(f"Renamed merged PDF: {pdf_path.name} -> {new_path.name}")

    return str(new_path)


def _safe_folder_name(name: str) -> str:

    """
    Convert a string into a Windows-safe folder name.
    Example:
        candidate@gmail.com
        Java Developer - candidate@gmail.com - UID5629
    """
    if not name:
        return "unknown"
    name = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", str(name))
    name = re.sub(r"\s+", " ", name).strip(" ._")
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
    subject = re.sub(r"^(re|fw|fwd)\s*:\s*", "", subject, flags=re.IGNORECASE,).strip()
    
    # Remove Windows-invalid characters
    subject = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_", subject,)

    # Normalize spaces
    subject = re.sub(r"\s+", " ", subject,)

    # Normalize repeated underscores

    subject = re.sub(r"_+", "_", subject,)

    # Remove invalid ending characters
    subject = subject.strip(" ._")

    if not subject:
        return "No Subject"

    # Shorten long subject
    if len(subject) > max_length:
        subject = (subject[:max_length].rstrip(" ._-"))
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
    subject = re.sub(r"^(re|fw|fwd)\s*:\s*","", subject, flags=re.IGNORECASE,).strip()

    # Remove Windows-invalid characters
    subject = re.sub(r'[<>:"/\\|?*\r\n\t]+', "_",subject,)

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
    # John Smith \<john@gmail.com>
    match = re.search(r"<([^<>@\s]+@[^<>@\s]+)>",sender,)
    if match:
        sender = match.group(1)
    sender = re.sub(r'[<>:"/\\|?*\r\n\t]+',"_", sender,)
    return sender.strip(" ._") or "unknown_sender"


def _unique_path(folder: Path, filename: str) -> Path:
    """
    Return a unique file path.
    Example:
        Resume.pdf
        Resume_1.pdf
        Resume_2.pdf
    """

    folder.mkdir(parents=True,exist_ok=True,)
    path = folder / filename
    counter = 1
    while path.exists():
        path = (folder/ f"{Path(filename).stem}_{counter}{Path(filename).suffix}")
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
        subject = (msg.subject or "").lower()

        if not any(
            keyword.lower() in subject
            for keyword in settings.subject_keywords
        ):
            return False
    if settings.from_filter:
        sender = (msg.from_ or "").lower()

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
    extension = Path(filename).suffix.lower()
    if extension not in CV_EXTENSIONS:
        return False


    # Maximum attachment size
    if att.size > (settings.max_attachment_mb* 1024* 1024):
        log.warning(
            "Skipping %s: larger than %s MB",
            filename,
            settings.max_attachment_mb,
        )
        return False

    # Ignore small/inline images
    if extension in IMAGE_EXTENSIONS:
        disposition = (att.content_disposition or "").lower()
        if disposition == "inline":
            return False
        if att.size < MIN_IMAGE_BYTES:
            return False
    return True


# ============================================================
# MailFetcher
# ============================================================

def find_libreoffice() -> str | None:
    """Find LibreOffice on PATH or in common Windows installation folders."""
    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if executable:
        return executable

    for candidate in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _convert_attachment_to_pdf(att, work_dir: Path, index: int) -> Path:
    """Convert one supported attachment to a temporary PDF without downsampling images."""
    filename = att.filename or f"attachment_{index}"
    extension = Path(filename).suffix.lower()
    payload = att.payload
    if not payload:
        raise ValueError(f"Attachment is empty: {filename}")

    # Prefix with index so two attachments with the same name cannot overwrite each other.
    safe_stem = safe_filename(Path(filename).stem)[:100] or f"attachment_{index}"
    source_path = work_dir / f"{index:03d}_{safe_stem}{extension}"
    source_path.write_bytes(payload)

    if extension == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(source_path), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise ValueError("PDF is password protected")
            except Exception as exc:
                raise ValueError(f"Encrypted PDF cannot be merged: {filename}") from exc
        if not reader.pages:
            raise ValueError(f"PDF contains no pages: {filename}")
        return source_path

    if extension in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        from PIL import Image, ImageOps
        pdf_path = work_dir / f"converted_{index:03d}.pdf"
        with Image.open(source_path) as original:
            # Preserve source pixels and apply orientation metadata; no downsampling.
            image = ImageOps.exif_transpose(original)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            image.save(pdf_path, "PDF", resolution=150.0)
        return pdf_path

    
    if extension in {".doc", ".docx", ".odt", ".rtf"}:
        # First try Microsoft Word for formats it commonly supports.
        # If Word fails, fall back to LibreOffice.
        converted = work_dir / f"{source_path.stem}.pdf"
        word_error = None

        if extension in {".doc", ".docx", ".rtf", ".odt"}:
            try:
                return _convert_with_word(source_path, converted)
            except Exception as exc:
                word_error = exc
                log.warning(
                    "Word conversion failed for %s; trying LibreOffice. Error: %s",
                    filename,
                    exc,
                )

        office = find_libreoffice()
        if not office:
            raise RuntimeError(
                f"Word and LibreOffice conversion unavailable for {filename}. "
                f"Word error: {word_error}"
            )

        profile_dir = work_dir / f"lo_profile_{index}"
        profile_dir.mkdir(parents=True, exist_ok=True)

        try:
            completed = subprocess.run(
                [
                    office,
                    f"-env:UserInstallation={profile_dir.as_uri()}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(work_dir),
                    str(source_path),
                ],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )

            if (
                completed.returncode == 0
                and converted.exists()
                and converted.stat().st_size > 0
            ):
                log.info("LibreOffice conversion successful: %s", converted)
                return converted

            details = (
                completed.stderr or completed.stdout or "No details returned"
            ).strip()

            raise RuntimeError(
                f"LibreOffice conversion failed for {filename}: "
                f"returncode={completed.returncode}; details={details}"
            ) from word_error

        except Exception:
            converted.unlink(missing_ok=True)
            raise

    raise ValueError(
        f"Unsupported attachment type for PDF conversion: {filename} ({extension or 'no extension'})"
    )


def _compress_pdf_lossless(pdf_path: Path) -> None:
    """Apply lossless PDF stream/object compression, retaining original images and pixels.

    This does not downsample or JPEG-recompress images. Scanned PDFs may not become
    much smaller because embedded scans are preserved byte-for-byte where possible.
    If the result is larger, the original is retained.
    """
    from pypdf import PdfReader, PdfWriter

    pdf_path = Path(pdf_path)
    temporary_path = pdf_path.with_name(pdf_path.stem + ".compressed.tmp.pdf")
    original_size = pdf_path.stat().st_size
    try:
        reader = PdfReader(str(pdf_path), strict=False)
        if reader.is_encrypted:
            raise ValueError(f"Cannot compress encrypted PDF: {pdf_path.name}")
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)

        for page in writer.pages:
            # Flate-compress page content streams; this is lossless.
            try:
                page.compress_content_streams()
            except Exception as exc:
                log.warning("Could not compress a page stream in %s: %s", pdf_path.name, exc)

        # Deduplicate identical indirect objects when supported by the installed pypdf.
        deduplicate = getattr(writer, "compress_identical_objects", None)
        if callable(deduplicate):
            try:
                deduplicate(remove_duplicates=True,remove_unreferenced=True,)
            except DependencyError as exc:
                log.warning(
                    "Skipping PDF object deduplication because a decoder "
                    "dependency is missing: %s",exc,)
        with temporary_path.open("wb") as handle:
            writer.write(handle)

        compressed_size = temporary_path.stat().st_size
        if compressed_size < original_size:
            temporary_path.replace(pdf_path)
            log.info(
                "Lossless PDF compression: %s -> %s bytes (%.1f%% smaller)",
                original_size, compressed_size,
                (1 - compressed_size / original_size) * 100,
            )
        else:
            temporary_path.unlink(missing_ok=True)
            log.info(
                "Lossless compression did not reduce %s (%s -> %s bytes); original retained",
                pdf_path.name, original_size, compressed_size,
            )
    finally:
        temporary_path.unlink(missing_ok=True)


def _merge_attachments_to_pdf(attachments, output_path: Path) -> None:
    """Convert each attachment to PDF, merge in email order, then losslessly compress."""
    from pypdf import PdfReader, PdfWriter

    if not attachments:
        raise ValueError("No attachments supplied for PDF creation")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cv_merge_") as temp_name:
        work_dir = Path(temp_name)
        converted_paths = [
            _convert_attachment_to_pdf(att, work_dir, index)
            for index, att in enumerate(attachments, start=1)
        ]

        writer = PdfWriter()
        page_count = 0
        for pdf_path in converted_paths:
            reader = PdfReader(str(pdf_path), strict=False)
            if reader.is_encrypted:
                try:
                    if not reader.decrypt(""):
                        raise ValueError("PDF requires a password")
                except Exception as exc:
                    raise ValueError(f"Encrypted PDF cannot be merged: {pdf_path.name}") from exc
            if len(reader.pages) == 0:
                raise ValueError(f"PDF contains no pages: {pdf_path.name}")
            writer.append(reader)
            page_count += len(reader.pages)

        if page_count == 0:
            raise ValueError("No pages were available to merge")

        temporary_output = output_path.with_name(output_path.stem + ".merging.tmp.pdf")
        try:
            with temporary_output.open("wb") as handle:
                writer.write(handle)
            temporary_output.replace(output_path)
        finally:
            temporary_output.unlink(missing_ok=True)

    # Compress only after every attachment has been converted/merged.
    try:
        _compress_pdf_lossless(output_path)
    except DependencyError as exc:
        log.warning(
            "PDF compression skipped for %s: %s. "
            "Keeping the merged PDF if it is intact.",
            output_path,
            exc,
        )


def _convert_with_word(source_path: Path, output_pdf: Path) -> Path:
    """Convert a Word document to PDF using Microsoft Word COM."""
    import logging
    import pythoncom
    import win32com.client

    log = logging.getLogger("fetcher")
    word = None
    doc = None

    try:
        pythoncom.CoInitialize()

        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0

        log.info("Microsoft Word conversion started: %s", source_path.name)

        # OpenAndRepair=True asks Word to recover damaged documents.
        doc = word.Documents.Open(
            FileName=str(source_path.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            OpenAndRepair=True,
            NoEncodingDialog=True,
            Visible=False,
        )

        # 17 is Word's PDF format.
        doc.SaveAs2(
            FileName=str(output_pdf.resolve()),
            FileFormat=17,
            AddToRecentFiles=False,
        )

        if not output_pdf.exists() or output_pdf.stat().st_size == 0:
            raise RuntimeError("Word did not create a valid PDF output file.")

        log.info("Microsoft Word conversion successful: %s", output_pdf)
        return output_pdf

    except Exception:
        output_pdf.unlink(missing_ok=True)
        log.exception("Microsoft Word conversion failed: %s", source_path.name)
        raise

    finally:
        if doc is not None:
            try:
                doc.Close(SaveChanges=False)
            except Exception:
                log.warning("Could not close Word document: %s", source_path.name)

        if word is not None:
            try:
                word.Quit()
            except Exception:
                log.warning("Could not close Microsoft Word cleanly.")

        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

class MailFetcher:
    def __init__(self):
        settings.ensure_dirs()
        self.state = JsonState(settings.state_folder/ "mail_state.json")
        self.manifest = JsonState(settings.state_folder/ "attachments_manifest.json")
        
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

        mb = MailBox(settings.imap_host,port=settings.imap_port,)
        mb.login(settings.imap_user, settings.imap_password, initial_folder=settings.imap_folder,)
        log.info("IMAP state after login: %s",getattr(mb.client,"state","UNKNOWN",),)  

        # Explicitly select the mailbox
        mb.folder.set(settings.imap_folder)
        log.info("IMAP connection established and folder selected: %s",settings.imap_folder,)
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
            log.warning("Could not logout IMAP connection: %s", exc,)


    # ========================================================
    # Public method
    # ========================================================

    def run(self,reset: bool = False,) -> FetchResult:
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
                    "IMAP connection is already in LOGOUT state after connection.")
            if state != "SELECTED":
                mb.folder.set(settings.imap_folder)

            # Get UIDVALIDITY from the SELECT response.
            # Do not issue an additional STATUS command to Zoho.
            response = mb.client.response("UIDVALIDITY")

            if not response or len(response) < 2 or not response[1]:
                raise RuntimeError("Could not obtain UIDVALIDITY from IMAP SELECT response.")
            uidvalidity = int(response[1][0])
            log.info("IMAP UIDVALIDITY: %s", uidvalidity)
            saved = self.state.get(self.key,{},)
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
                # Track PDFs created during this batch only.
                saved_files_before = len(result.saved_files)
                batch_success = False

                try:
                    if mb is None:
                        raise RuntimeError(
                            "IMAP connection is unavailable. Stopping fetch."
                        )

                    state = getattr(mb.client, "state", "UNKNOWN")

                    if state == "LOGOUT":
                        raise RuntimeError(
                            "IMAP connection is in LOGOUT state. Stopping fetch."
                        )

                    if state != "SELECTED":
                        mb.folder.set(settings.imap_folder)

                    batch_last_uid = result.last_uid

                    for msg in mb.fetch(
                        AND(uid=[str(uid) for uid in batch]),
                        mark_seen=False,
                        bulk=False,
                    ):
                        try:
                            self._process_message(msg, result)
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
                except Exception as exc:
                    log.exception(
                        "Batch %d-%d failed. No automatic reconnect or retry: %s",
                        batch_start,
                        batch_end,
                        exc,
                    )

                    # Do not continue making IMAP requests after a connection failure.
                    self._close_mailbox(mb)
                    mb = None

                if not batch_success:
                    log.error(
                        "Stopping the fetch because batch %d-%d failed.",
                        batch_start,
                        batch_end,
                    )
                    break

                # Save progress after a successfully fetched batch.
                self._save_state(uidvalidity, result)

                log.info(
                    "Fetch progress saved. Last UID=%d",
                    result.last_uid,
                )

                # Extract only files created during this batch.
                newly_saved_files = list(
                    dict.fromkeys(result.saved_files[saved_files_before:])
                )

                if newly_saved_files:
                    try:
                        from app.extractor.cv_extractor import run_extraction

                        extraction_result = run_extraction(
                            provider=settings.llm_provider,
                            only_paths=newly_saved_files,
                        )

                        log.info(
                            "Batch extraction completed: found=%s, extracted=%s, "
                            "skipped=%s, failed=%s",
                            getattr(extraction_result, "found", "unknown"),
                            getattr(extraction_result, "extracted", "unknown"),
                            getattr(
                                extraction_result,
                                "skipped_already_done",
                                "unknown",
                            ),
                            getattr(extraction_result, "failed", "unknown"),
                        )

                        for filename, error in getattr(
                            extraction_result, "errors", {}
                        ).items():
                            log.error(
                                "Extraction failed for %s: %s",
                                filename,
                                error,
                            )

                    except Exception:
                        log.exception(
                            "Extraction failed for batch %d-%d.",
                            batch_start,
                            batch_end,
                        )

                else:
                    log.info(
                        "No new PDFs created in batch %d-%d; skipping extraction.",
                        batch_start,
                        batch_end,
                    )

                # Persist state after extraction.
                self._save_state(uidvalidity, result)

                log.info(
                    "Batch %d-%d processing finished.",
                    batch_start,
                    batch_end,
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
                log.info(
						"Fetch progress saved. Last UID=%d",
						result.last_uid,
					)
                # Extract and upload only PDFs created during this batch.
                newly_saved_files = list(dict.fromkeys(result.saved_files[saved_files_before:]))
                if newly_saved_files:
                    log.info(
						"Starting extraction for %d PDF(s) from batch %d-%d.",
						len(newly_saved_files),
						batch_start,
						batch_end,
					)
                    try:
						# Local import avoids a module-level circular import.
                        from app.extractor.cv_extractor import run_extraction
                        extraction_result = run_extraction(
							provider=settings.llm_provider,
							only_paths=newly_saved_files,
						)
                        log.info(
						"Batch extraction completed: found=%s, "
						"extracted=%s, skipped=%s, failed=%s",
						getattr(extraction_result, "found", "unknown"),
						getattr(extraction_result, "extracted", "unknown"),
						getattr(
							extraction_result,
							"skipped_already_done",
							"unknown",
						),
						getattr(extraction_result, "failed", "unknown"),
					)
                        errors = getattr(extraction_result, "errors", {})
                        if errors:
                            for filename, error in errors.items():
                                log.error(
                                    "Extraction failed for %s: %s",
                                    filename,
                                    error,
                                )
                    except Exception:
                        log.exception(
								"Extraction failed for batch %d-%d. "
								"Email fetching will continue with the next batch.",
								batch_start,
								batch_end,
							)
                else:
                    log.info(
						"No new PDF files created in batch %d-%d; "
						"skipping extraction.",
						batch_start,
						batch_end,
					)
                    
            # Persist any manifest/state changes made during extraction.
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
            state = getattr(mb.client,"state","UNKNOWN",)
            if state == "LOGOUT":
                return
            mb.logout()
        except Exception as exc:
            log.warning("Could not close IMAP connection safely: %s",exc,)

    def _build_criteria(self, last_uid: int,):

        # ----------------------------------------------------
        # DATE RANGE MODE
        # ----------------------------------------------------

        if (settings.fetch_since_date and settings.fetch_until_date):
            since = datetime.strptime(
                settings.fetch_since_date,
                "%Y-%m-%d",
            ).date()
            until = datetime.strptime(settings.fetch_until_date, "%Y-%m-%d",).date()
            log.info("Downloading emails from %s to %s",since,until,)
            return AND(date_gte=since, date_lt=until,)

        # ----------------------------------------------------
        # NORMAL INCREMENTAL MODE
        # ----------------------------------------------------

        if last_uid > 0:
            return AND(uid=UidRange(last_uid + 1, "*",))

        # ----------------------------------------------------
        # FIRST RUN WITH ONLY START DATE
        # ----------------------------------------------------

        if settings.fetch_since_date:
            since = datetime.strptime(settings.fetch_since_date, "%Y-%m-%d",).date()
            return AND(date_gte=since,)

        # ----------------------------------------------------
        # NO FILTER
        # ----------------------------------------------------

        return AND(all=True)

    # ========================================================
    # Process one email
    # ========================================================

    def _process_message(self, msg, result: FetchResult):
        """Save each matching email as one compressed PDF in its month folder."""
        if not msg.attachments or not _mail_matches_filters(msg):
            return

        cv_attachments = []
        for att in msg.attachments:
            try:
                if _is_cv_attachment(att):
                    cv_attachments.append(att)
            except Exception as exc:
                log.warning(
                    "Could not inspect attachment %s: %s",
                    getattr(att, "filename", "unknown"), exc,
                )

        if not cv_attachments:
            return

        email_date = msg.date or datetime.now()
        month_folder = email_date.strftime("%B-%Y")
        email_account = _safe_folder_name(settings.imap_user)
        month_path = settings.cv_folder / email_account / month_folder
        month_path.mkdir(parents=True, exist_ok=True)

        hashes = self.manifest.get("by_hash", {})
        subject = msg.subject or f"Email_UID_{msg.uid}"
        base_name = safe_filename(_safe_subject(subject, 100))
        output_path = _unique_path(month_path, f"{base_name}.pdf")

        try:
            # One attachment is converted too, so the downstream extractor always receives PDF.
            _merge_attachments_to_pdf(cv_attachments, output_path)
            digest = file_sha256(output_path.read_bytes())
            manifest_key = f"PDF:{digest}"

            if manifest_key in hashes:
                existing_file = hashes[manifest_key].get("file", "")
                existing_path = Path(existing_file) if existing_file else None
                if existing_path and not existing_path.is_absolute():
                    existing_path = settings.cv_folder / existing_path
                if existing_path and existing_path.exists():
                    output_path.unlink(missing_ok=True)
                    result.duplicates_skipped += 1
                    log.info("Duplicate PDF skipped: %s (same content as %s)", output_path.name, existing_path)
                    return
                log.warning("Removing stale PDF manifest entry: %s", existing_file)
                hashes.pop(manifest_key, None)

            try:
                relative_path = str(output_path.relative_to(settings.cv_folder))
            except ValueError:
                relative_path = str(output_path)

            hashes[manifest_key] = {
                "file": relative_path,
                "original_names": [a.filename or "attachment" for a in cv_attachments],
                "from": msg.from_ or "",
                "from_name": msg.from_values.name if msg.from_values else "",
                "subject": msg.subject or "",
                "mail_date": msg.date.isoformat() if msg.date else None,
                "uid": int(msg.uid),
                "folder": settings.imap_folder,
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                "attachment_count": len(cv_attachments),
            }
            result.attachments_saved += 1
            result.saved_files.append(str(output_path))
            log.info(
                "Created %s from %d attachment(s): %s",
                "merged PDF" if len(cv_attachments) > 1 else "PDF",
                len(cv_attachments), output_path,
            )
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            log.exception(
                "Failed converting/merging attachments for email UID %s. "
                "No combined PDF was saved: %s", msg.uid, exc,
            )
        finally:
            self.manifest.set("by_hash", hashes)

    def _save_state(self,uidvalidity: int,result: FetchResult,):
        # ----------------------------------------------------
        # Save manifest first.
        # ----------------------------------------------------
        self.manifest.save()
        self.state.set(
            self.key,
            {
                "uidvalidity": uidvalidity,
                "last_uid": result.last_uid,
                "last_run": (datetime.now().isoformat(timespec="seconds")),
                "total_cvs_in_manifest": len(self.manifest.get("by_hash",{},)),
            },
        )
        self.state.save()

def fetch_cvs(
    reset: bool = False,
) -> FetchResult:
    """
    Convenience function used by main.py / FastAPI.
    """
    return MailFetcher().run(reset=reset)


def list_cv_files(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*")
        if p.is_file()
        and p.suffix.lower() in CV_EXTENSIONS
    )