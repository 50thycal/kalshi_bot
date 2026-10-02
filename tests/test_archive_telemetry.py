"""Integrity checks for the offline archive format; no production DB or bucket access."""

import csv
import gzip
import json
from datetime import datetime, timezone

import pytest

from scripts.archive_telemetry import file_sha256, utc_day, verify


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
