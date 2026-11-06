
from pathlib import Path
import boto3
from botocore.exceptions import ClientError

# Existing local CV directory
LOCAL_ROOT = Path(
    r"C:\Users\HP\Documents\shield_global"
    r"\CV-pipeline\cv_pipeline\backend\data\cvs"
)

# Replace with your actual S3 bucket name
BUCKET_NAME = "shield-global-cvs-bucket-521554525205-ap-south-1-an"

# Keep the existing folder structure under this prefix
S3_PREFIX = "cvs"

# First run: True prints the files without uploading.
# After verifying the output, change it to False.
DRY_RUN = False

s3 = boto3.client("s3")


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

    uploaded = 0
    failed = 0

    for file_path in files:
        relative_path = file_path.relative_to(
            LOCAL_ROOT
        ).as_posix()

        s3_key = f"{S3_PREFIX}/{relative_path}"

        try:
            if DRY_RUN:
                print(f"[DRY RUN] {file_path} -> {s3_key}")
                continue

            s3.upload_file(
                str(file_path),
                BUCKET_NAME,
                s3_key
            )

            uploaded += 1
            print(f"[SUCCESS] s3://{BUCKET_NAME}/{s3_key}")

        except (ClientError, OSError) as exc:
            failed += 1
            print(f"[FAILED] {file_path}: {exc}")

    print("\nUpload summary")
    print(f"Total local files: {len(files)}")
    print(f"Uploaded: {uploaded}")
    print(f"Failed: {failed}")
    print(f"Dry run: {DRY_RUN}")


if __name__ == "__main__":
    upload_existing_cvs()
