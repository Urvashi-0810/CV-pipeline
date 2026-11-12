"""
One-time upload of the existing CV folder to S3.

Safe to run again: files already in S3 at the same size are skipped, so a re-run after a
failure or interruption only sends what's missing. A failed file doesn't stop the run.
Failures are listed at the end and saved to s3_upload_failures.txt beside this script.
"""
from pathlib import Path
import mimetypes
import sys

import boto3
from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError

# Existing local CV directory
LOCAL_ROOT = Path(
    r"C:\Users\HP\Documents\shield_global"
    r"\CV-pipeline\cv_pipeline\backend\data\cvs"
)

# Replace with your actual S3 bucket name
BUCKET_NAME = "shield-global-cvs-bucket-521554525205-ap-south-1-an"

# Keep the existing folder structure under this prefix
# Must match AWS_S3_PREFIX in .env, or the S3 keys won't match the extractor's.
S3_PREFIX = "cvs"

# First run: True prints the files without uploading.
# After verifying the output, change it to False.
DRY_RUN = False

# Stop if this many uploads fail in a row: the network or credentials are likely down.
# Running the script again carries on from where it stopped.
MAX_CONSECUTIVE_FAILURES = 25

FAILURES_FILE = Path(__file__).with_name("s3_upload_failures.txt")

s3 = boto3.client("s3")


def existing_s3_files():
    """Return {key: size} for everything already under the prefix."""
    existing = {}
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=f"{S3_PREFIX}/"):
        for item in page.get("Contents", []):
            existing[item["Key"]] = item["Size"]
    return existing


def upload_args(file_path):
    """Store the real content type, so browsers can show PDFs inline."""
    content_type = mimetypes.guess_type(file_path.name)[0]
    return {"ContentType": content_type} if content_type else None


def upload_existing_cvs():
    if not LOCAL_ROOT.exists():
        raise FileNotFoundError(
            f"Local CV folder not found: {LOCAL_ROOT}"
        )

    files = [
        path for path in LOCAL_ROOT.rglob("*")
        if path.is_file()
    ]

    print(f"Local folder: {LOCAL_ROOT}")
    print(f"Files found: {len(files)}")

    # Stops here, before uploading, if the bucket or credentials are wrong.
    existing = existing_s3_files()
    print(f"Already in S3: {len(existing)}")

    uploaded = 0
    skipped = 0
    failures = []
    consecutive_failures = 0
    stopped_early = False

    for file_path in files:
        relative_path = file_path.relative_to(
            LOCAL_ROOT
        ).as_posix()

        s3_key = f"{S3_PREFIX}/{relative_path}"

        try:
            if existing.get(s3_key) == file_path.stat().st_size:
                skipped += 1
                continue

            if DRY_RUN:
                print(f"[DRY RUN] {file_path} -> {s3_key}")
                continue

            s3.upload_file(
                str(file_path),
                BUCKET_NAME,
                s3_key,
                ExtraArgs=upload_args(file_path)
            )

            uploaded += 1
            consecutive_failures = 0
            print(f"[SUCCESS] s3://{BUCKET_NAME}/{s3_key}")

        except (Boto3Error, BotoCoreError, ClientError, OSError) as exc:
            failures.append((file_path, exc))
            consecutive_failures += 1
            print(f"[FAILED] {file_path}: {exc}")

            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                stopped_early = True
                print(
                    f"\nStopping: {consecutive_failures} uploads failed in a row. "
                    "Check the network and AWS credentials, then run this again."
                )
                break

    print("\nUpload summary")
    print(f"Total local files: {len(files)}")
    print(f"Uploaded: {uploaded}")
    print(f"Skipped (already in S3): {skipped}")
    print(f"Failed: {len(failures)}")
    print(f"Stopped early: {stopped_early}")
    print(f"Dry run: {DRY_RUN}")

    if failures:
        FAILURES_FILE.write_text(
            "\n".join(f"{path}\t{exc}" for path, exc in failures),
            encoding="utf-8",
        )
        print(f"Failed files listed in: {FAILURES_FILE}")
        print("Run this script again to retry them.")
    else:
        FAILURES_FILE.unlink(missing_ok=True)

    return not failures


if __name__ == "__main__":
    sys.exit(0 if upload_existing_cvs() else 1)
