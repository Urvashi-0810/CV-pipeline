"""
The Talent Pool settings page: read and change the backend's .env, and test connections.

Secrets are never sent to the browser, only whether they're set. Most settings apply at once;
the database, folders, CORS and cookie settings are read at startup, so those wait for a restart.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, fields
from datetime import date
from typing import Any, Optional

from dotenv import dotenv_values
from sqlalchemy import make_url

from app.api.problems import ProblemError
from app.core import config
from app.core.config import LEGACY_NAMES, Settings, settings
from app.core.logger import get_logger

log = get_logger("settings")


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    label: str
    kind: str                      # text, secret, url, int, float, bool, choice, list, path, date
    default: str = ""
    help: str = ""
    choices: tuple = ()
    restart: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None


GROUPS = [
    ("llm", "AI model (LLM)", "Which AI reads the CVs, and its API keys."),
    ("mail", "Mailbox", "The mailbox CVs are fetched from."),
    ("s3", "S3 storage", "Where CV files are stored. Keys need Put, Get, List and Delete on the bucket."),
    ("folders", "Folders", "Where files are kept on this computer."),
    ("database", "Database", "The Talent Pool's Postgres database."),
    ("login", "Login and access", "Talent Pool sessions, the admin password lockout and which CRM addresses may connect."),
]

DEFS = [
    SettingDef("LLM_PROVIDER", "llm", "Provider in use", "choice", "claude", choices=("claude", "gemini")),
    SettingDef("ANTHROPIC_API_KEY", "llm", "Claude API key", "secret"),
    SettingDef("CLAUDE_MODEL", "llm", "Claude model", "text", "claude-sonnet-4-5"),
    SettingDef("GEMINI_API_KEY", "llm", "Gemini API key", "secret"),
    SettingDef("GEMINI_MODEL", "llm", "Gemini model", "text", "gemini-2.5-flash"),
    SettingDef("CV_PARSER", "llm", "How CVs reach the AI", "choice", "llm", choices=("llm", "docling"),
               help="llm sends the file itself (recommended). docling converts it to text first."),
    SettingDef("DOCLING_OCR", "llm", "Docling reads scanned pages (OCR)", "bool", "true",
               help="Only used when CVs reach the AI through docling."),

    SettingDef("IMAP_HOST", "mail", "Mail server", "text", "imap.gmail.com"),
    SettingDef("IMAP_PORT", "mail", "Port", "int", "993", minimum=1, maximum=65535),
    SettingDef("IMAP_USER", "mail", "Mailbox address", "text"),
    SettingDef("IMAP_PASSWORD", "mail", "Password (app password)", "secret"),
    SettingDef("IMAP_FOLDER", "mail", "Folder", "text", "INBOX"),
    SettingDef("FETCH_SINCE_DATE", "mail", "Fetch mail from", "date",
               help="Leave blank to fetch everything new. A range needs both dates."),
    SettingDef("FETCH_UNTIL_DATE", "mail", "Fetch mail until", "date"),
    SettingDef("SUBJECT_KEYWORDS", "mail", "Only subjects containing", "list",
               help="Comma separated. Blank means every subject."),
    SettingDef("FROM_FILTER", "mail", "Only from these senders", "list",
               help="Comma separated addresses or domains. Blank means every sender."),
    SettingDef("MAX_ATTACHMENT_MB", "mail", "Largest attachment (MB)", "float", "15", minimum=0.1, maximum=200),

    SettingDef("AWS_S3_BUCKET", "s3", "Bucket name", "text"),
    SettingDef("AWS_REGION", "s3", "Region", "text",
               help="The bucket's region, e.g. ap-south-1 for Mumbai. Links to CVs need it to be right."),
    SettingDef("AWS_S3_PREFIX", "s3", "Folder in the bucket", "text", "cvs",
               help="Must match upload_existing_cvs_to_s3.py, or old and new CVs end up in different places."),
    SettingDef("AWS_ACCESS_KEY_ID", "s3", "Access key ID", "secret"),
    SettingDef("AWS_SECRET_ACCESS_KEY", "s3", "Secret access key", "secret"),

    SettingDef("CV_FOLDER", "folders", "CV files", "path", "data/cvs", restart=True,
               help="Relative paths are inside the backend folder."),
    SettingDef("OUTPUT_EXCEL", "folders", "Candidate Excel", "path", "data/output/candidates.xlsx", restart=True),
    SettingDef("STATE_FOLDER", "folders", "Pipeline state", "path", "data/state", restart=True),
    SettingDef("JOBS_FOLDER", "folders", "Job files", "path", "data/jobs", restart=True),

    SettingDef("DATABASE_URL", "database", "Database connection", "url", restart=True,
               help="postgresql+psycopg://user:password@host:5432/talent"),
    SettingDef("TEST_DATABASE_URL", "database", "Test database (developers)", "url",
               help="Only for running the automated tests. Its name must end in _test."),

    SettingDef("SESSION_TTL_HOURS", "login", "Stay logged in for (hours)", "int", "12", minimum=1, maximum=720),
    SettingDef("ADMIN_SESSION_MINUTES", "login", "Admin stays unlocked for (minutes)", "int", "30",
               minimum=1, maximum=1440),
    SettingDef("ADMIN_MAX_FAILED_ATTEMPTS", "login", "Wrong admin passwords before lockout", "int", "5",
               minimum=1, maximum=100),
    SettingDef("ADMIN_LOCKOUT_MINUTES", "login", "Lockout length (minutes)", "int", "15", minimum=1, maximum=1440),
    SettingDef("PASSWORD_MIN_LENGTH", "login", "Shortest password allowed", "int", "6", minimum=4, maximum=128),
    SettingDef("COOKIE_SECURE", "login", "Served over HTTPS", "bool", "false", restart=True,
               help="Turn on only when the Talent Pool is served over HTTPS, or nobody can log in."),
    SettingDef("CORS_ORIGINS", "login", "CRM addresses allowed to connect", "list",
               "http://localhost:5173,http://127.0.0.1:5173", restart=True,
               help="Every address the CRM is opened from, exactly, e.g. http://192.168.1.20:5173"),
]
BY_KEY = {d.key: d for d in DEFS}
SECRET_KINDS = {"secret", "url"}

# Settings read once at startup: the running server keeps its old value until it restarts.
RESTART_ATTRS = {"database_url", "cv_folder", "output_excel", "state_folder", "jobs_folder", "cookie_secure",
                 "cors_origins"}

_write_lock = threading.Lock()


def env_file():
    return config.ENV_FILE


def _file_values() -> dict[str, str]:
    path = env_file()
    return {k: (v or "") for k, v in dotenv_values(path).items()} if path.exists() else {}


def _mask_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001
        return "(not a valid connection URL)"


def _secret_state(definition: SettingDef, stored: dict[str, str]) -> dict:
    value = stored.get(definition.key, "")
    alias = LEGACY_NAMES.get(definition.key)
    via = None
    if not value and alias and stored.get(alias):
        value, via = stored[alias], alias
    state = {"is_set": bool(value), "set_via": via}
    if value and definition.kind == "secret":
        state["hint"] = f"ends in {value[-4:]}" if len(value) >= 12 else "set"
    if value and definition.kind == "url":
        state["hint"] = _mask_url(value)
    return state


def _pending_restart(definition: SettingDef, stored: dict[str, str]) -> bool:
    """A restart setting whose saved value differs from the one this server started with."""
    if not definition.restart:
        return False
    saved = stored.get(definition.key, "") or definition.default
    running = os.environ.get(definition.key, "") or definition.default
    return saved.strip() != running.strip()


def describe() -> dict:
    stored = _file_values()
    groups = []
    for group_id, label, help_text in GROUPS:
        items = []
        for d in (d for d in DEFS if d.group == group_id):
            item = {"key": d.key, "label": d.label, "kind": d.kind, "help": d.help, "choices": list(d.choices),
                    "restart": d.restart, "default": d.default, "minimum": d.minimum, "maximum": d.maximum,
                    "pending_restart": _pending_restart(d, stored)}
            if d.kind in SECRET_KINDS:
                item |= _secret_state(d, stored)
            else:
                item["value"] = stored.get(d.key, d.default)
                item["is_default"] = d.key not in stored
            items.append(item)
        groups.append({"id": group_id, "label": label, "help": help_text, "settings": items})
    return {"env_file": str(env_file()), "groups": groups,
            "restart_pending": [d.key for d in DEFS if _pending_restart(d, stored)]}


# ---------- checking values ----------

def _clean(definition: SettingDef, raw: Any) -> str:
    """The value as it will be written, or raises ValueError with a message for the form."""
    value = "" if raw is None else str(raw).strip()
    if value == "":
        return ""
    kind = definition.kind
    if kind in ("int", "float"):
        try:
            number = int(value) if kind == "int" else float(value)
        except ValueError:
            raise ValueError("Enter a whole number." if kind == "int" else "Enter a number.")
        if definition.minimum is not None and number < definition.minimum:
            raise ValueError(f"At least {definition.minimum:g}.")
        if definition.maximum is not None and number > definition.maximum:
            raise ValueError(f"At most {definition.maximum:g}.")
        return str(number)
    if kind == "bool":
        if value.lower() not in ("true", "false"):
            raise ValueError("Choose yes or no.")
        return value.lower()
    if kind == "choice":
        if value not in definition.choices:
            raise ValueError(f"Choose one of: {', '.join(definition.choices)}.")
        return value
    if kind == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("Use the format YYYY-MM-DD.")
        return value
    if kind == "list":
        return ",".join(item.strip() for item in value.split(",") if item.strip())
    if kind == "url":
        try:
            url = make_url(value)
        except Exception:  # noqa: BLE001
            raise ValueError("Not a valid connection URL.")
        if url.drivername != "postgresql+psycopg":
            raise ValueError("Must start with postgresql+psycopg://")
        if definition.key == "TEST_DATABASE_URL" and not (url.database or "").endswith("_test"):
            raise ValueError("The test database's name must end in _test.")
        return value
    if "\n" in value or "\r" in value:
        raise ValueError("Must be on one line.")
    return value


def _check_together(values: dict[str, str]) -> list[dict]:
    """Rules that involve two settings."""
    errors = []
    since, until = values.get("FETCH_SINCE_DATE", ""), values.get("FETCH_UNTIL_DATE", "")
    if since and until and since > until:
        errors.append({"field": "FETCH_UNTIL_DATE", "message": "Must be on or after the from date."})
    origins = values.get("CORS_ORIGINS", "")
    for origin in (o for o in origins.split(",") if o):
        if not re.fullmatch(r"https?://[^/\s]+", origin):
            errors.append({"field": "CORS_ORIGINS",
                           "message": f"{origin} must look like http://host:port, with no path or slash at the end."})
            break
    return errors


# ---------- writing .env ----------

def _quote(value: str) -> str:
    """Quote a value so python-dotenv reads back exactly the same text."""
    if value == "" or re.fullmatch(r"[^\s#'\"\\]+", value):
        return value
    if "'" not in value:
        return f"'{value}'"                      # single quotes: taken literally, backslashes included
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write(changes: dict[str, Optional[str]]) -> None:
    """Update lines in place, keeping comments and order. None removes the setting."""
    path = env_file()
    raw = path.read_bytes().decode("utf-8") if path.exists() else ""
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    done = set()
    out = []
    for line in lines:
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in changes:
            if key in done:
                continue                             # a duplicate line would override ours: drop it
            done.add(key)
            if changes[key] is not None:
                out.append(f"{key}={_quote(changes[key])}")
            continue
        out.append(line)
    added = [k for k, v in changes.items() if k not in done and v is not None]
    if added:
        out += ["", "# ---------- set from the Talent Pool settings page ----------"]
        out += [f"{k}={_quote(changes[k])}" for k in added]
    text = newline.join(out) + newline
    tmp = path.with_name(path.name + ".saving")
    tmp.write_bytes(text.encode("utf-8"))
    tmp.replace(path)


def _apply_to_running_server(changed: dict[str, Optional[str]]) -> None:
    for key, value in changed.items():
        if BY_KEY[key].restart:
            continue
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    fresh = Settings()
    for item in fields(Settings):
        if item.name not in RESTART_ATTRS:
            setattr(settings, item.name, getattr(fresh, item.name))


def save(values: dict[str, Any], clear: list[str], *, username: str) -> dict:
    """Validate and save. A blank non-secret goes back to its default; a blank secret is left as it is."""
    errors, changes = [], {}
    for key in [*values, *clear]:
        if key not in BY_KEY:
            errors.append({"field": key, "message": "Not a setting that can be changed here."})
    if errors:
        raise ProblemError(422, "validation-error", "Some settings are invalid", "Fix the listed settings.",
                           errors=errors)

    for key, raw in values.items():
        definition = BY_KEY[key]
        try:
            value = _clean(definition, raw)
        except ValueError as e:
            errors.append({"field": key, "message": str(e)})
            continue
        if definition.kind in SECRET_KINDS and value == "":
            continue                                 # an empty secret box means "keep it"
        changes[key] = value or None
    for key in clear:
        changes[key] = None
        if LEGACY_NAMES.get(key):
            changes[LEGACY_NAMES[key]] = None        # clearing must also clear the older name

    stored = _file_values()
    merged = {d.key: stored.get(d.key, d.default) for d in DEFS} | {k: v or "" for k, v in changes.items()}
    errors += _check_together(merged)
    if errors:
        raise ProblemError(422, "validation-error", "Some settings are invalid", "Fix the listed settings.",
                           errors=errors)
    changes = {k: v for k, v in changes.items() if (stored.get(k) or None) != v}
    if not changes:
        return {"saved": [], "restart_required": []}

    with _write_lock:
        _write(changes)
        _apply_to_running_server({k: v for k, v in changes.items() if k in BY_KEY})
    saved = [k for k in changes if k in BY_KEY]
    log.info("Settings changed by %s: %s", username, ", ".join(saved))   # never the values
    return {"saved": saved, "restart_required": [k for k in saved if BY_KEY[k].restart]}
