"""S3 keys and upload behaviour, including the hardened historical upload script."""
import pytest
from boto3.exceptions import S3UploadFailedError

import mock_llm
import upload_existing_cvs_to_s3 as script
from app.core.config import settings
from app.extractor import cv_extractor
from app.storage import s3_store
from app.storage.excel_store import load_candidates
from conftest import pdf_bytes

mock_llm.start()


@pytest.mark.parametrize("stored, expected", [
    (r"C:\Users\HP\Documents\shield_global\CV-pipeline\cv_pipeline\backend\data\cvs\hr@x.com\May-2026\a.pdf",
     "hr@x.com/May-2026/a.pdf"),
    ("/srv/app/data/cvs/hr@x.com/May-2026/a.pdf", "hr@x.com/May-2026/a.pdf"),
    (r"hr@x.com\May-2026\a.pdf", "hr@x.com/May-2026/a.pdf"),
    ("a.pdf", "a.pdf"),
    (r"C:\somewhere\else\a.pdf", None),
])
def test_relative_cv_path(stored, expected):
    assert s3_store.relative_cv_path(stored) == expected


def test_cv_links_use_the_buckets_own_region(monkeypatch):
    """A link signed for ap-south-1 but sent to the global s3.amazonaws.com is refused by S3."""
    from urllib.parse import parse_qs, urlparse
    monkeypatch.setattr(settings, "aws_s3_bucket", "cv-bucket")
    monkeypatch.setattr(settings, "aws_region", "ap-south-1")
    url = urlparse(s3_store.presigned_cv_url("cvs/acct/May-2026/a.pdf"))
    assert url.netloc == "cv-bucket.s3.ap-south-1.amazonaws.com"
    assert "/ap-south-1/s3/aws4_request" in parse_qs(url.query)["X-Amz-Credential"][0]


def test_relative_cv_path_under_the_configured_folder():
    assert s3_store.relative_cv_path(settings.cv_folder / "acct" / "a.pdf") == "acct/a.pdf"


def test_key_uses_the_shared_prefix():
    assert s3_store.key_for("hr@x.com/May-2026/a.pdf") == "cvs/hr@x.com/May-2026/a.pdf"
    assert s3_store.key_for("hr@x.com/May-2026/a.pdf") == f"{script.S3_PREFIX}/hr@x.com/May-2026/a.pdf"


class FakeBucket:
    """The parts of the boto3 S3 client that uploads use."""

    def __init__(self, existing=None, fail=()):
        self.existing = existing or {}
        self.fail = fail
        self.calls = []

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        return [{"Contents": [{"Key": key, "Size": size} for key, size in self.existing.items()]}]

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        if self.fail is True or key in self.fail:
            raise S3UploadFailedError("upload failed")
        self.calls.append((key, ExtraArgs))


def test_upload_uses_the_cv_folder_path_as_the_key(clean_data, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(s3_store, "_client", lambda: bucket)
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    path = settings.cv_folder / "hr@x.com" / "May-2026" / "a.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")

    assert s3_store.upload_cv_to_s3(path) is True
    assert bucket.calls == [("cvs/hr@x.com/May-2026/a.pdf", {"ContentType": "application/pdf"})]


def test_s3_failure_keeps_the_cv_and_records_it(clean_data, monkeypatch):
    monkeypatch.setattr(s3_store, "_client", lambda: FakeBucket(fail=True))
    monkeypatch.setattr(settings, "aws_s3_bucket", "test-bucket")
    (settings.cv_folder / "cv.pdf").write_bytes(pdf_bytes("CV"))

    result = cv_extractor.run_extraction()

    assert result.extracted == 1 and result.failed == 0
    assert load_candidates().iloc[0]["Uploaded to S3"] == "Failed"


@pytest.fixture
def historical(tmp_path, monkeypatch):
    """Points the script at a temp folder and a fake bucket. The script file isn't changed."""
    root = tmp_path / "cvs"
    (root / "acct" / "May-2026").mkdir(parents=True)
    monkeypatch.setattr(script, "LOCAL_ROOT", root)
    monkeypatch.setattr(script, "FAILURES_FILE", tmp_path / "failures.txt")

    def use(bucket):
        monkeypatch.setattr(script, "s3", bucket)
        return bucket
    use.root = root
    use.failures = tmp_path / "failures.txt"
    return use


def add(root, name, data=b"pdf"):
    path = root / "acct" / "May-2026" / name
    path.write_bytes(data)
    return f"cvs/acct/May-2026/{name}"


def test_script_skips_files_already_uploaded(historical):
    done = add(historical.root, "done.pdf")
    changed = add(historical.root, "changed.pdf", b"longer content")
    new = add(historical.root, "new.pdf")
    bucket = historical(FakeBucket(existing={done: 3, changed: 3}))

    assert script.upload_existing_cvs() is True
    assert sorted(key for key, _ in bucket.calls) == sorted([changed, new])


def test_script_sets_the_content_type(historical):
    add(historical.root, "cv.pdf")
    bucket = historical(FakeBucket())
    script.upload_existing_cvs()
    assert bucket.calls[0][1] == {"ContentType": "application/pdf"}


def test_script_carries_on_after_a_failed_upload_and_lists_it(historical):
    bad = add(historical.root, "a_bad.pdf")
    good = add(historical.root, "b_good.pdf")
    bucket = historical(FakeBucket(fail=[bad]))

    assert script.upload_existing_cvs() is False
    assert [key for key, _ in bucket.calls] == [good]
    assert "a_bad.pdf" in historical.failures.read_text(encoding="utf-8")


def test_script_re_run_retries_failures_and_clears_the_list(historical):
    add(historical.root, "cv.pdf")
    historical(FakeBucket(fail=True))
    assert script.upload_existing_cvs() is False

    historical(FakeBucket())
    assert script.upload_existing_cvs() is True
    assert not historical.failures.exists()


def test_script_stops_when_everything_is_failing(historical, monkeypatch):
    monkeypatch.setattr(script, "MAX_CONSECUTIVE_FAILURES", 3)
    for index in range(10):
        add(historical.root, f"{index}.pdf")
    historical(FakeBucket(fail=True))

    assert script.upload_existing_cvs() is False
    assert len(historical.failures.read_text(encoding="utf-8").splitlines()) == 3
