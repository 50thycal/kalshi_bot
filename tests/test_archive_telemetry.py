"""Integrity checks for the offline archive format; no production DB or bucket access."""

import csv
import gzip
import io
import json
from datetime import datetime, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from scripts.archive_telemetry import file_sha256, upload, utc_day, verify


def test_verify_csv_with_embedded_newline_and_detect_corruption(tmp_path):
    data = tmp_path / "execution_book_events_2026-09-17.csv.gz"
    manifest = tmp_path / "execution_book_events_2026-09-17.manifest.json"
    with gzip.open(data, "wt", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["id", "raw_json"])
        writer.writerow([1, '{"message":"line one\nline two"}'])
        writer.writerow([2, "{}"])
    record = {
        "format": "postgres-copy-csv-gzip-v1",
        "file": data.name,
        "columns": ["id", "raw_json"],
        "row_count": 2,
        "sha256": file_sha256(data),
    }
    manifest.write_text(json.dumps(record), encoding="utf-8")
    assert verify(data, manifest)["row_count"] == 2

    record["row_count"] = 3
    manifest.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="row count mismatch"):
        verify(data, manifest)

    record["row_count"] = 2
    manifest.write_text(json.dumps(record), encoding="utf-8")
    with data.open("ab") as out:
        out.write(b"corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify(data, manifest)


def test_utc_day_requires_completed_day():
    today = datetime.now(timezone.utc).date().isoformat()
    with pytest.raises(ValueError, match="completed UTC days"):
        utc_day(today)
    start, end = utc_day("2026-09-17")
    assert (end - start).total_seconds() == 86400


def test_upload_reads_back_bytes_and_refuses_conflict(tmp_path, monkeypatch):
    data = tmp_path / "incentive_book_events_2026-09-17.csv.gz"
    manifest_path = tmp_path / "incentive_book_events_2026-09-17.manifest.json"
    with gzip.open(data, "wt", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["id"])
        writer.writerow([1])
    manifest = {
        "format": "postgres-copy-csv-gzip-v1",
        "table": "incentive_book_events",
        "utc_start": "2026-09-17T00:00:00+00:00",
        "utc_end_exclusive": "2026-09-18T00:00:00+00:00",
        "file": data.name,
        "columns": ["id"],
        "row_count": 1,
        "sha256": file_sha256(data),
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    for key, value in {
        "ARCHIVE_S3_ENDPOINT": "https://example.invalid",
        "ARCHIVE_S3_BUCKET": "research-archive",
        "ARCHIVE_S3_ACCESS_KEY_ID": "test-key",
        "ARCHIVE_S3_SECRET_ACCESS_KEY": "test-secret",
        "ARCHIVE_S3_REGION": "auto",
    }.items():
        monkeypatch.setenv(key, value)

    class FakeS3:
        objects = {}

        def head_object(self, *, Bucket, Key):
            if Key not in self.objects:
                raise ClientError({"Error": {"Code": "404"},
                                   "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
            return {}

        def upload_file(self, path, bucket, key):
            with open(path, "rb") as src:
                self.objects[key] = src.read()

        def get_object(self, *, Bucket, Key):
            data = self.objects[Key]
            return {"Body": StreamingBody(io.BytesIO(data), len(data))}

    fake = FakeS3()
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: fake)
    result = upload(data, manifest_path)
    assert result["row_count"] == 1
    assert len(fake.objects) == 2
    assert upload(data, manifest_path) == result  # idempotent verified readback
    fake.objects[result["data_key"]] = b"different bytes"
    with pytest.raises(ValueError, match="readback checksum mismatch"):
        upload(data, manifest_path)
