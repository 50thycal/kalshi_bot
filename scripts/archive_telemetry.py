"""Export one UTC day of raw telemetry using a SELECT-only Postgres credential.

This tool deliberately has no database write, deletion, bucket upload, or scheduling path.
The resulting CSV is PostgreSQL COPY format, suitable for a restore test against an
isolated database with the matching schema. Do not use the trading worker's DATABASE_URL.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

TABLE_CLOCKS = {
    "incentive_book_events": "received_at",
    "execution_book_events": "received_at",
    "incentive_market_snapshots": "at",
}


@contextmanager
def private_file(path: Path):
    """Create a new archive file with private permissions even under a loose umask."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        yield stream


def utc_day(value: str) -> tuple[datetime, datetime]:
    start = datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    if start >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0):
        raise ValueError("Only completed UTC days may be exported")
    return start, start + timedelta(days=1)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(data_path: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["format"] != "postgres-copy-csv-gzip-v1":
        raise ValueError("Unsupported archive format")
    if data_path.name != manifest["file"]:
        raise ValueError("Archive filename does not match manifest")
    if file_sha256(data_path) != manifest["sha256"]:
        raise ValueError("Archive checksum mismatch")
    with gzip.open(data_path, "rt", encoding="utf-8", newline="") as src:
        rows = csv.reader(src)
        if next(rows, None) != manifest["columns"]:
            raise ValueError("Archive columns do not match manifest")
        actual = sum(1 for _ in rows)
    if actual != manifest["row_count"]:
        raise ValueError(f"Archive row count mismatch: {actual} != {manifest['row_count']}")
    return manifest


def export(table: str, day: str, output_dir: Path) -> dict:
    if table not in TABLE_CLOCKS:
        raise ValueError("Table is not in the raw-telemetry export allowlist")
    start, end = utc_day(day)
    url = os.environ.get("DATABASE_URL_RO", "")
    if not url:
        raise ValueError("DATABASE_URL_RO is required")
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    import psycopg
    from psycopg import IsolationLevel, sql

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = f"{table}_{day}"
    data_path = output_dir / f"{stem}.csv.gz"
    manifest_path = output_dir / f"{stem}.manifest.json"
    if data_path.exists() or manifest_path.exists():
        raise FileExistsError("Archive already exists; verify it instead of overwriting")
    temp_path = output_dir / f".{stem}.{os.getpid()}.part"
    options = (
        "-c default_transaction_read_only=on -c statement_timeout=300000 "
        "-c lock_timeout=2000 -c idle_in_transaction_session_timeout=60000"
    )
    try:
        with psycopg.connect(url, options=options, connect_timeout=15) as conn:
            conn.read_only = True
            conn.isolation_level = IsolationLevel.REPEATABLE_READ
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT attname, format_type(atttypid, atttypmod) "
                    "FROM pg_attribute WHERE attrelid = %s::regclass "
                    "AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
                    (f"public.{table}",),
                )
                schema = cur.fetchall()
                if not schema or schema[0][0] != "id":
                    raise ValueError("Expected an id-first table schema")
                columns = [name for name, _type in schema]
                clock = sql.Identifier(TABLE_CLOCKS[table])
                relation = sql.Identifier("public", table)
                bounds = sql.SQL(" WHERE {} >= %s AND {} < %s").format(clock, clock)
                cur.execute(
                    sql.SQL("SELECT count(*), min(id), max(id) FROM {}{}").format(
                        relation, bounds
                    ),
                    (start, end),
                )
                row_count, min_id, max_id = cur.fetchone()
                statement = sql.SQL(
                    "COPY (SELECT {} FROM {}{} ORDER BY id) TO STDOUT "
                    "WITH (FORMAT csv, HEADER true)"
                ).format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)), relation, bounds
                )
                with private_file(temp_path) as raw:
                    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                        with cur.copy(statement, (start, end)) as copy:
                            for block in copy:
                                compressed.write(block)
                # The count and COPY used the same repeatable-read snapshot.
        manifest = {
            "format": "postgres-copy-csv-gzip-v1",
            "table": table,
            "utc_start": start.isoformat(),
            "utc_end_exclusive": end.isoformat(),
            "file": data_path.name,
            "columns": columns,
            "types": [type_name for _name, type_name in schema],
            "row_count": row_count,
            "min_id": min_id,
            "max_id": max_id,
            "sha256": file_sha256(temp_path),
            "compressed_bytes": temp_path.stat().st_size,
        }
        # Verify contents before making either artifact visible as final.
        temporary_manifest = output_dir / f".{stem}.{os.getpid()}.json.part"
        with private_file(temporary_manifest) as raw:
            raw.write((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
        try:
            # The final filename is part of the manifest; verification reads the temp data.
            with gzip.open(temp_path, "rt", encoding="utf-8", newline="") as src:
                rows = csv.reader(src)
                if next(rows, None) != columns or sum(1 for _ in rows) != row_count:
                    raise ValueError("COPY output does not match the database snapshot")
            temp_path.replace(data_path)
            temporary_manifest.replace(manifest_path)
        finally:
            temporary_manifest.unlink(missing_ok=True)
        return manifest
    finally:
        temp_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export_cmd = commands.add_parser("export")
    export_cmd.add_argument("--table", required=True, choices=sorted(TABLE_CLOCKS))
    export_cmd.add_argument("--day", required=True, help="Completed UTC day, YYYY-MM-DD")
    export_cmd.add_argument("--output-dir", type=Path, required=True)
    verify_cmd = commands.add_parser("verify")
    verify_cmd.add_argument("data_path", type=Path)
    verify_cmd.add_argument("manifest_path", type=Path)
    args = parser.parse_args()
    result = (
        export(args.table, args.day, args.output_dir)
        if args.command == "export" else verify(args.data_path, args.manifest_path)
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
