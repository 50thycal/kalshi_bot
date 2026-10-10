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


# --- Whole-table archive (WS-024 D3), end to end on real Postgres ---------------------------

def _fake_bucket(monkeypatch):
    for key, value in {
        "ARCHIVE_S3_ENDPOINT": "https://example.invalid",
        "ARCHIVE_S3_BUCKET": "research-archive",
        "ARCHIVE_S3_ACCESS_KEY_ID": "test-key",
        "ARCHIVE_S3_SECRET_ACCESS_KEY": "test-secret",
        "ARCHIVE_S3_REGION": "auto",
    }.items():
        monkeypatch.setenv(key, value)

    class FakeS3:
        def __init__(self):
            self.objects = {}

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
    return fake


@pytest.fixture
def archive_databases(monkeypatch):
    """A throwaway source database (standing in for production) and an isolated restore
    database, both created on the CI Postgres service and dropped afterwards."""
    import os
    import uuid

    psycopg = pytest.importorskip("psycopg")
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    admin_url = os.environ.get("XOS_TEST_POSTGRES_URL")
    if not admin_url:
        pytest.skip("XOS_TEST_POSTGRES_URL not set (CI provides a Postgres service)")
    suffix = uuid.uuid4().hex[:10]
    names = {"source": f"archive_src_{suffix}", "restore": f"archive_rst_{suffix}"}
    with psycopg.connect(admin_url, autocommit=True) as admin:
        for name in names.values():
            admin.execute(f'CREATE DATABASE "{name}"')
    base = conninfo_to_dict(admin_url)
    urls = {role: make_conninfo(**{**base, "dbname": name}) for role, name in names.items()}
    monkeypatch.setenv("DATABASE_URL_RO", urls["source"])
    monkeypatch.setenv("RESTORE_DATABASE_URL", urls["restore"])
    with psycopg.connect(urls["source"], autocommit=True) as conn:
        for table, clock in (("incentive_shadow_events", "at"),
                             ("incentive_book_events", "received_at")):
            conn.execute(
                f"CREATE TABLE {table} (id bigserial PRIMARY KEY, market_ticker varchar(128) "
                f"NOT NULL, {clock} timestamptz NOT NULL, count numeric(18,2), "
                "detail_json jsonb, note text)"
            )
        conn.execute(
            "INSERT INTO incentive_shadow_events (id, market_ticker, at, count, detail_json, note) "
            "SELECT g * 2, 'KX-' || g, now() - interval '5 days' + g * interval '1 second', "
            "CASE WHEN g % 7 = 0 THEN NULL ELSE g / 3.0 END, "
            "jsonb_build_object('n', g, 'text', E'line one\\nline \"two\", three'), "
            "CASE WHEN g % 5 = 0 THEN NULL ELSE E'quote \" comma , newline\\n' || g END "
            "FROM generate_series(1, 2500) g"
        )
        conn.execute(
            "INSERT INTO incentive_book_events (market_ticker, received_at) "
            "VALUES ('KX-LIVE', now())"
        )
    yield urls
    with psycopg.connect(admin_url, autocommit=True) as admin:
        for name in names.values():
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_archive_table_exports_restores_and_completes_once(archive_databases, monkeypatch,
                                                            tmp_path):
    import psycopg

    from scripts.archive_telemetry import archive_table

    fake = _fake_bucket(monkeypatch)
    work = tmp_path / "work"
    done = archive_table("incentive_shadow_events", 1000, work, frozen_hours=24,
                         restore_test=True, pause_seconds=0)
    # ids 2..5000 step 2 -> ranges [2,1002) [1002,2002) ... five ranges, 2,500 rows.
    assert done["snapshot_count"] == 2500
    assert [c["row_count"] for c in done["chunks"]] == [500] * 5
    assert done["restore_tested"] is True
    complete_key = "telemetry/v1/incentive_shadow_events/ids/COMPLETE.json"
    assert json.loads(fake.objects[complete_key])["snapshot_count"] == 2500
    assert len(fake.objects) == 11  # five data + five manifests + COMPLETE
    assert not any(work.iterdir())  # nothing left on the job's disk
    with psycopg.connect(archive_databases["restore"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        ).fetchone()[0] == 0
    # A rerun reads COMPLETE.json and does nothing else.
    before = dict(fake.objects)
    assert archive_table("incentive_shadow_events", 1000, work, frozen_hours=24,
                         restore_test=True, pause_seconds=0)["completed_at"] == done["completed_at"]
    assert fake.objects == before


def test_archive_table_resumes_and_refuses_a_corrupted_range(archive_databases, monkeypatch,
                                                             tmp_path):
    from scripts.archive_telemetry import archive_table, export_ids, upload

    fake = _fake_bucket(monkeypatch)
    work = tmp_path / "work"
    export_ids("incentive_shadow_events", 2, 1002, work)
    stem = work / "incentive_shadow_events_ids_000000000002_000000001002"
    stored = upload(stem.with_suffix(".csv.gz"), stem.with_suffix(".manifest.json"))
    fake.objects[stored["data_key"]] = b"not the archived bytes"
    with pytest.raises(ValueError, match="does not match its manifest"):
        archive_table("incentive_shadow_events", 1000, tmp_path / "again", frozen_hours=24,
                      restore_test=False, pause_seconds=0)
    assert not any(key.endswith("COMPLETE.json") for key in fake.objects)


def test_archive_table_refuses_a_table_still_being_written(archive_databases, monkeypatch,
                                                           tmp_path):
    from scripts.archive_telemetry import archive_table

    _fake_bucket(monkeypatch)
    with pytest.raises(ValueError, match="not frozen"):
        archive_table("incentive_book_events", 1000, tmp_path, frozen_hours=24,
                      restore_test=False, pause_seconds=0)
    with pytest.raises(ValueError, match="frozen tables"):
        archive_table("execution_book_events", 1000, tmp_path, frozen_hours=24,
                      restore_test=False, pause_seconds=0)


def test_restore_check_catches_a_difference_and_refuses_production(archive_databases,
                                                                   monkeypatch, tmp_path):
    import psycopg

    from scripts.archive_telemetry import export_ids, restore_check

    manifest = export_ids("incentive_shadow_events", 2, 1002, tmp_path)
    data = tmp_path / manifest["file"]
    assert restore_check(data, manifest, 1000)["rows"] == 500
    with psycopg.connect(archive_databases["source"], autocommit=True) as conn:
        conn.execute("UPDATE incentive_shadow_events SET note = 'changed' WHERE id = 500")
    with pytest.raises(ValueError, match="differ from production at ids \\[500\\]"):
        restore_check(data, manifest, 1000)
    monkeypatch.setenv("RESTORE_DATABASE_URL", archive_databases["source"])
    with pytest.raises(ValueError, match="isolated database"):
        restore_check(data, manifest, 10)
