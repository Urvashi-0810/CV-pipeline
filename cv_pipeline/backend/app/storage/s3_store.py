"""
All S3 access for CV files.

Keys mirror the local folder layout: cvs/<mail-account>/<Month-Year>/<file>.pdf.
upload_existing_cvs_to_s3.py builds the same keys, so AWS_S3_PREFIX must stay "cvs"
or the historical CVs and the new ones end up under different paths.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import boto3
from botocore.config import Config

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger("s3")

URL_EXPIRY_SECONDS = 300
_CV_ROOT_MARKER = re.compile(r"(?:^|/)data/cvs/", re.IGNORECASE)


class S3NotConfigured(RuntimeError):
    """AWS_S3_BUCKET is missing from .env."""


def _client():
    """An S3 client that talks to the bucket's own region.

    Without an explicit regional endpoint, boto3 signs CV links for the region but points them at
    the global s3.amazonaws.com, which only accepts us-east-1 signatures, so every link fails with
    "the region 'ap-south-1' is wrong; expecting 'us-east-1'". Uploads hid this by following redirects.
    """
    region = settings.aws_region or None
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com" if region else None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def relative_cv_path(local_path: str | Path) -> Optional[str]:
    """Return a CV's path below data/cvs with forward slashes, or None if it isn't below it.

    Stored paths can be absolute and from another machine or install folder, so anything
    containing data/cvs is accepted as well as paths below the current CV_FOLDER.
    """
    text = str(local_path).replace("\\", "/")
    root = str(settings.cv_folder).replace("\\", "/").rstrip("/") + "/"
    if text.lower().startswith(root.lower()):
        return text[len(root):]
    match = _CV_ROOT_MARKER.search(text)
    if match:
        return text[match.end():]
    # Already relative, as the manifest stores it.
    return None if re.match(r"^(?:[A-Za-z]:)?/", text) else text


def key_for(relative_path: str) -> str:
    return "/".join(part for part in (settings.aws_s3_prefix, relative_path) if part)


def upload_cv_to_s3(path: Path) -> bool:
    """Upload a final, renamed PDF. Failures are logged and returned, never raised."""
    if not settings.aws_s3_bucket:
        log.error("AWS_S3_BUCKET is not configured.")
        return False

    try:
        key = key_for(path.relative_to(settings.cv_folder).as_posix())
        _client().upload_file(
            str(path),
            settings.aws_s3_bucket,
            key,
            ExtraArgs={"ContentType": "application/pdf"},
        )
        log.info("S3 upload successful: s3://%s/%s", settings.aws_s3_bucket, key)
        return True
    except Exception:
        log.exception("S3 upload failed for %s", path)
        return False


def list_cv_keys() -> set[str]:
    """Every key under the CV prefix. One listing is far cheaper than a HEAD per CV."""
    if not settings.aws_s3_bucket:
        raise S3NotConfigured("AWS_S3_BUCKET is not configured")

    prefix = f"{settings.aws_s3_prefix}/" if settings.aws_s3_prefix else ""
    pages = _client().get_paginator("list_objects_v2").paginate(Bucket=settings.aws_s3_bucket, Prefix=prefix)
    return {item["Key"] for page in pages for item in page.get("Contents", [])}


def delete_cv_keys(keys: list[str]) -> None:
    """Delete CV files from the bucket. Keys that don't exist are fine. Raises if any delete fails."""
    if not keys:
        return
    if not settings.aws_s3_bucket:
        raise S3NotConfigured("AWS_S3_BUCKET is not configured")
    client = _client()
    for start in range(0, len(keys), 1000):
        batch = [{"Key": key} for key in keys[start:start + 1000]]
        response = client.delete_objects(Bucket=settings.aws_s3_bucket, Delete={"Objects": batch, "Quiet": True})
        errors = response.get("Errors") or []
        if errors:
            raise RuntimeError(f"S3 refused to delete {len(errors)} file(s): {errors[0].get('Message')}")
    log.info("Deleted %d CV file(s) from S3", len(keys))


def presigned_cv_url(key: str, *, download: bool = False) -> str:
    """A short-lived link to one CV: opens inline, or downloads when download=True.

    The response type is forced to PDF because upload_existing_cvs_to_s3.py historically
    stored files with no ContentType, which browsers would download instead of showing.
    """
    if not settings.aws_s3_bucket:
        raise S3NotConfigured("AWS_S3_BUCKET is not configured")

    filename = key.rsplit("/", 1)[-1]
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "_")
    disposition = (
        f"{'attachment' if download else 'inline'}; "
        f"filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
    )
    return _client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.aws_s3_bucket,
            "Key": key,
            "ResponseContentType": "application/pdf",
            "ResponseContentDisposition": disposition,
        },
        ExpiresIn=URL_EXPIRY_SECONDS,
    )
