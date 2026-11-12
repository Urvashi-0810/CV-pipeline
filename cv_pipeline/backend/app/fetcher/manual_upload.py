"""
Manual CV upload: one upload is one person's CV, and may contain several files.

The files are merged into one PDF the same way the mail flow merges attachments, then go
through normal extraction: extract -> rename -> S3 -> Excel. The result is stored under
data/cvs/manual-upload/<Month-Year>/, so its S3 key is cvs/manual-upload/<Month-Year>/<file>.pdf.

Uploads are saved to a staging folder when received, so one that's waiting for a running
fetch or extract isn't lost if the server restarts.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

from app.core.config import settings
from app.core.logger import get_logger
from app.core.state import JsonState, file_sha256, manifest_entry
from app.extractor.cv_extractor import run_extraction
from app.fetcher.mail_fetcher import _merge_attachments_to_pdf, _unique_path, safe_filename

log = get_logger("upload")

MANUAL_FOLDER = "manual-upload"
MANUAL_SOURCE = "Manual upload"
_NAMES_FILE = "names.json"


def staging_root() -> Path:
    return settings.state_folder / "upload_staging"


def stage_upload(files: list[tuple[str, bytes]]) -> Path:
    """Save an upload's files, in order, and return its staging folder."""
    folder = staging_root() / uuid.uuid4().hex
    folder.mkdir(parents=True)
    for index, (_name, payload) in enumerate(files):
        (folder / f"{index:03d}.bin").write_bytes(payload)
    (folder / _NAMES_FILE).write_text(json.dumps([name for name, _ in files]), encoding="utf-8")
    return folder


def pending_uploads() -> list[Path]:
    """Uploads that were received but never finished, oldest first."""
    root = staging_root()
    if not root.exists():
        return []
    folders = [folder for folder in root.iterdir() if (folder / _NAMES_FILE).exists()]
    return sorted(folders, key=lambda folder: folder.stat().st_mtime)


def _load_stage(folder: Path) -> list[SimpleNamespace]:
    names = json.loads((folder / _NAMES_FILE).read_text(encoding="utf-8"))
    return [
        SimpleNamespace(filename=name, payload=(folder / f"{index:03d}.bin").read_bytes())
        for index, name in enumerate(names)
    ]


def _existing_file(entry: dict) -> Optional[Path]:
    """The file a manifest entry points to, if it's still on disk."""
    recorded = entry.get("file")
    if not recorded:
        return None
    path = Path(recorded)
    path = path if path.is_absolute() else settings.cv_folder / path
    return path if path.exists() else None


def process_upload(
    folder: Path,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> dict:
    """Run one staged upload. Returns status "added", "duplicate" or "failed"."""
    report = progress or (lambda *_: None)
    output_path = None
    registered = False
    try:
        attachments = _load_stage(folder)
        report(0, 2, "Merging files into one PDF")

        month_path = settings.cv_folder / MANUAL_FOLDER / datetime.now().strftime("%B-%Y")
        base_name = safe_filename(Path(attachments[0].filename).stem)[:100] or "upload"
        output_path = _unique_path(month_path, f"{base_name}.pdf")
        _merge_attachments_to_pdf(attachments, output_path)
        digest = file_sha256(output_path)

        processed = JsonState(settings.state_folder / "processed_files.json")
        manifest = JsonState(settings.state_folder / "attachments_manifest.json")
        by_hash = manifest.get("by_hash", {})
        record = processed.get(digest) or {}
        entry = manifest_entry(by_hash, digest)

        if record.get("status") == "ok":
            output_path.unlink(missing_ok=True)
            log.info("Upload is a duplicate of %s", record.get("file"))
            return {
                "status": "duplicate",
                "file_hash": digest,
                "file": record.get("file"),
                "name": record.get("name"),
            }

        existing = _existing_file(entry)
        if existing:
            # Already on file but not extracted yet (or its extraction failed): retry that copy.
            output_path.unlink(missing_ok=True)
            output_path = existing
            registered = True
        else:
            by_hash[f"PDF:{digest}"] = {
                "file": str(output_path.relative_to(settings.cv_folder)),
                "original_names": [attachment.filename for attachment in attachments],
                "from": MANUAL_SOURCE,
                "from_name": "",
                "subject": "",
                "mail_date": None,
                "source": "manual",
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                "attachment_count": len(attachments),
            }
            manifest.set("by_hash", by_hash)
            manifest.save()
            registered = True

        report(1, 2, "Extracting CV details")
        result = run_extraction(only_paths=[str(output_path)])
        report(2, 2, "done")

        record = JsonState(settings.state_folder / "processed_files.json").get(digest) or {}
        if record.get("status") != "ok":
            return {
                "status": "failed",
                "file_hash": digest,
                "file": record.get("file") or output_path.name,
                "error": record.get("error") or next(iter(result.errors.values()), "Extraction failed"),
            }
        placement = result.placements.get(digest) or {}
        return {
            "status": "added",
            "file_hash": digest,
            "file": record.get("file"),
            "name": record.get("name"),
            "s3_status": record.get("s3_status"),
            # Set when the talent database is configured: a passport match joins an existing person.
            "candidate_id": placement.get("candidate_id"),
            "attached_to_existing": bool(placement.get("attached")),
            "message": ("Added to existing candidate" if placement.get("attached")
                        else "Added as a new candidate" if placement else None),
        }
    except Exception:
        # A file that can't be converted will never succeed, so don't leave half a PDF behind.
        if output_path is not None and not registered:
            output_path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(folder, ignore_errors=True)
