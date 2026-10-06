"""
Tiny JSON-file "database" used to remember progress between runs.

Two state files live in data/state/:
  mail_state.json       -> per mailbox+folder: last processed IMAP UID (+ UIDVALIDITY)
  processed_files.json  -> per CV file hash: was it already extracted into Excel?

Writes are atomic (write temp file, then rename) so a crash mid-write
never leaves a corrupted state file.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonState:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Keep a copy of the broken file and start fresh
                path.rename(path.with_suffix(".corrupt.json"))
                self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, self.path)


def file_sha256(path_or_bytes: Path | bytes) -> str:
    """Content hash -> the same CV sent twice (even with another name) is detected."""
    h = hashlib.sha256()
    if isinstance(path_or_bytes, (bytes, bytearray)):
        h.update(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()
