"""
"Test connection" buttons on the settings page. Each returns {"ok", "message", "details"}, never raises,
and never puts a secret in its message. They test the saved settings.
"""
from __future__ import annotations

from typing import Callable

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger("connection_tests")
TIMEOUT_SECONDS = 20
S3_TEST_FILE = ".talent-pool-connection-test"


def _result(ok: bool, message: str, **details) -> dict:
    return {"ok": ok, "message": message, "details": details}


def _guard(name: str, check: Callable[[], dict]) -> dict:
    try:
        return check()
    except Exception as e:  # noqa: BLE001 - every failure becomes a readable message
        log.warning("%s connection test failed: %s", name, e)
        return _result(False, f"{type(e).__name__}: {str(e)[:300]}")


def test_mail() -> dict:
    def check():
        from imap_tools import MailBox
        if not (settings.imap_user and settings.imap_password):
            return _result(False, "Enter the mailbox address and password first.")
        with MailBox(settings.imap_host, port=settings.imap_port, timeout=TIMEOUT_SECONDS).login(
                settings.imap_user, settings.imap_password) as mailbox:
            folders = [f.name for f in mailbox.folder.list()]
        if settings.imap_folder not in folders:
            return _result(False, f"Logged in, but there is no folder called \"{settings.imap_folder}\".",
                           folders=folders[:50])
        return _result(True, f"Logged in as {settings.imap_user}. Folder \"{settings.imap_folder}\" found.",
                       folders=folders[:50])
    return _guard("Mail", check)


def test_s3() -> dict:
    def check():
        from botocore.exceptions import ClientError

        from app.storage import s3_store
        if not settings.aws_s3_bucket:
            return _result(False, "Enter the bucket name first.")
        client = s3_store._client()
        key = s3_store.key_for(S3_TEST_FILE)
        steps = []
        try:
            client.head_bucket(Bucket=settings.aws_s3_bucket)
            steps.append("bucket found")
            client.list_objects_v2(Bucket=settings.aws_s3_bucket, Prefix=f"{settings.aws_s3_prefix}/", MaxKeys=1)
            steps.append("list")
            client.put_object(Bucket=settings.aws_s3_bucket, Key=key, Body=b"ok", ContentType="text/plain")
            steps.append("upload")
            client.delete_object(Bucket=settings.aws_s3_bucket, Key=key)
            steps.append("delete")
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            step = ["finding the bucket", "listing files", "uploading", "deleting"][len(steps)]
            hint = {"403": "the keys aren't allowed to do this", "AccessDenied": "the keys aren't allowed to do this",
                    "404": "the bucket doesn't exist in this region", "NoSuchBucket": "the bucket doesn't exist",
                    "InvalidAccessKeyId": "the access key ID is wrong",
                    "SignatureDoesNotMatch": "the secret access key is wrong"}.get(code, code or "refused")
            return _result(False, f"Failed while {step}: {hint}.", passed=steps)
        return _result(True, f"Bucket \"{settings.aws_s3_bucket}\" works: list, upload and delete all allowed.",
                       passed=steps)
    return _guard("S3", check)


def test_database() -> dict:
    """Tests the DATABASE_URL saved in .env, which may be waiting for a restart."""
    def check():
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine, text

        from app.core.config import BASE_DIR
        from app.talent.settings_admin import _file_values
        url = _file_values().get("DATABASE_URL") or settings.database_url
        if not url:
            return _result(False, "Enter the database connection first.")
        engine = create_engine(url, connect_args={"connect_timeout": TIMEOUT_SECONDS})
        try:
            with engine.connect() as connection:
                version = connection.execute(text("SHOW server_version")).scalar()
                has_table = connection.execute(text("SELECT to_regclass('alembic_version')")).scalar()
                current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar() \
                    if has_table else None
        finally:
            engine.dispose()
        head = ScriptDirectory.from_config(Config(str(BASE_DIR / "alembic.ini"))).get_current_head()
        if current != head:
            return _result(False, f"Connected (Postgres {version}), but the tables aren't up to date "
                                  f"({current or 'none'} instead of {head}). Run: alembic upgrade head",
                           postgres=version, tables=current, latest=head)
        return _result(True, f"Connected to Postgres {version}. Tables are up to date.", postgres=version,
                       tables=current)
    return _guard("Database", check)


def test_llm() -> dict:
    def check():
        from pydantic import BaseModel

        from app.llm.client import get_llm
        provider = settings.llm_provider

        class Ping(BaseModel):
            answer: str

        answer = get_llm(provider).extract(Ping, "Reply in the schema.", text="Say 'pong'.").answer
        model = settings.gemini_model if provider == "gemini" else settings.claude_model
        return _result(True, f"{provider} ({model}) answered.", answer=answer[:50])
    return _guard("LLM", check)


TESTS = {"mail": test_mail, "s3": test_s3, "database": test_database, "llm": test_llm}
