"""Archive raw telemetry from production Postgres to the private research bucket.

Two export shapes, both PostgreSQL COPY CSV + gzip with a manifest:

- `export` / `upload`: one completed UTC day of an allowlisted table.
- `archive-tables`: a whole FROZEN table (writer off; WS-024 D3) in primary-key id ranges.
  Each range is exported, verified, uploaded and read back; with `--restore-test` it is then
  downloaded from the bucket, loaded into an ISOLATED database and compared with production.
  A table's `COMPLETE.json` is written last and only when every range is accounted for.

Production is only ever read: sessions are read-only, repeatable-read transactions. This tool
has no production write, deletion, or scheduling path; the only writes are to the bucket and to
the isolated restore database, which must not be the production database.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import time as clock_time
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

TABLE_CLOCKS = {
    "incentive_book_events": "received_at",
    "execution_book_events": "received_at",
    "incentive_market_snapshots": "at",
    "incentive_shadow_events": "at",
}
# Whole-table archive is allowed only for tapes whose writers are switched off: both
# default off since 2026-10-07 (config `liquidity_incentive_book_events_max_per_minute`,
# `liquidity_incentive_persist_shadow_events`). `archive-tables` re-checks the newest row.
FROZEN_TABLES = ("incentive_book_events", "incentive_shadow_events")
DAY_FORMAT = "postgres-copy-csv-gzip-v1"
IDS_FORMAT = "postgres-copy-csv-gzip-ids-v1"
TABLE_FORMAT = "telemetry-table-archive-v1"
READ_ONLY_SESSION = (
    "-c default_transaction_read_only=on -c statement_timeout=300000 "
    "-c lock_timeout=2000 -c idle_in_transaction_session_timeout=60000 -c TimeZone=UTC"
)
_TYPE_NAME = re.compile(r"^[a-z][a-z0-9 _(),]*$")


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
    if manifest["format"] not in (DAY_FORMAT, IDS_FORMAT):
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


def database_url(name: str) -> str:
    url = os.environ.get(name, "")
    if not url:
        raise ValueError(f"{name} is required")
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


@contextmanager
def source_connection():
    """A read-only, repeatable-read session on production; nothing here can write."""
    import psycopg
    from psycopg import IsolationLevel

    with psycopg.connect(
        database_url("DATABASE_URL_RO"), options=READ_ONLY_SESSION, connect_timeout=15
    ) as conn:
        conn.read_only = True
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        yield conn


def table_schema(cur, table: str) -> list[tuple[str, str]]:
    cur.execute(
        "SELECT attname, format_type(atttypid, atttypmod) "
        "FROM pg_attribute WHERE attrelid = %s::regclass "
        "AND attnum > 0 AND NOT attisdropped ORDER BY attnum",
        (f"public.{table}",),
    )
    schema = cur.fetchall()
    if not schema or schema[0][0] != "id":
        raise ValueError("Expected an id-first table schema")
    return schema


def copy_out(cur, table, schema, bounds, params, output_dir: Path, stem: str, fields) -> dict:
    """COPY one bounded slice to a private gzip file, count it in the same snapshot, verify
    the file against that count, and only then make the data and manifest visible."""
    from psycopg import sql

    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_path = output_dir / f"{stem}.csv.gz"
    manifest_path = output_dir / f"{stem}.manifest.json"
    if data_path.exists() or manifest_path.exists():
        raise FileExistsError("Archive already exists; verify it instead of overwriting")
    temp_path = output_dir / f".{stem}.{os.getpid()}.part"
    columns = [name for name, _type in schema]
    relation = sql.Identifier("public", table)
    try:
        cur.execute(
            sql.SQL("SELECT count(*), min(id), max(id) FROM {}{}").format(relation, bounds),
            params,
        )
        row_count, min_id, max_id = cur.fetchone()
        statement = sql.SQL(
            "COPY (SELECT {} FROM {}{} ORDER BY id) TO STDOUT WITH (FORMAT csv, HEADER true)"
        ).format(sql.SQL(", ").join(map(sql.Identifier, columns)), relation, bounds)
        with private_file(temp_path) as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                with cur.copy(statement, params) as copy:
                    for block in copy:
                        compressed.write(block)
        # The count and COPY used the same repeatable-read snapshot.
        manifest = {
            **fields,
            "table": table,
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


def export(table: str, day: str, output_dir: Path) -> dict:
    if table not in TABLE_CLOCKS:
        raise ValueError("Table is not in the raw-telemetry export allowlist")
    start, end = utc_day(day)
    from psycopg import sql

    clock = sql.Identifier(TABLE_CLOCKS[table])
    bounds = sql.SQL(" WHERE {} >= %s AND {} < %s").format(clock, clock)
    fields = {
        "format": DAY_FORMAT,
        "utc_start": start.isoformat(),
        "utc_end_exclusive": end.isoformat(),
    }
    with source_connection() as conn, conn.cursor() as cur:
        schema = table_schema(cur, table)
        return copy_out(cur, table, schema, bounds, (start, end), output_dir,
                        f"{table}_{day}", fields)


def export_ids(table: str, id_start: int, id_end: int, output_dir: Path) -> dict:
    """Export ids [id_start, id_end) of a frozen table; a primary-key range scan."""
    if table not in FROZEN_TABLES:
        raise ValueError("Whole-table export is limited to frozen tables")
    if not 0 <= id_start < id_end:
        raise ValueError("Invalid id range")
    from psycopg import sql

    bounds = sql.SQL(" WHERE id >= %s AND id < %s")
    fields = {"format": IDS_FORMAT, "id_start": id_start, "id_end_exclusive": id_end}
    with source_connection() as conn, conn.cursor() as cur:
        schema = table_schema(cur, table)
        return copy_out(cur, table, schema, bounds, (id_start, id_end), output_dir,
                        ids_stem(table, id_start, id_end), fields)


def ids_stem(table: str, id_start: int, id_end: int) -> str:
    return f"{table}_ids_{id_start:012d}_{id_end:012d}"


def object_prefix(manifest: dict) -> str:
    """Bucket prefix for a validated manifest; rejects anything but the two known shapes."""
    table = manifest.get("table")
    if manifest.get("format") == IDS_FORMAT:
        if table not in FROZEN_TABLES:
            raise ValueError("Whole-table archive is limited to frozen tables")
        id_start, id_end = manifest["id_start"], manifest["id_end_exclusive"]
        if not (isinstance(id_start, int) and isinstance(id_end, int) and 0 <= id_start < id_end):
            raise ValueError("Manifest must cover a valid id range")
        if manifest["file"] != ids_stem(table, id_start, id_end) + ".csv.gz":
            raise ValueError("Archive filename does not match its id range")
        return f"telemetry/v1/{table}/ids/{id_start:012d}-{id_end:012d}/"
    if table not in TABLE_CLOCKS:
        raise ValueError("Table is not in the raw-telemetry export allowlist")
    start, end = utc_day(manifest["utc_start"][:10])
    if (manifest["utc_start"], manifest["utc_end_exclusive"]) != (
        start.isoformat(), end.isoformat()
    ):
        raise ValueError("Manifest must cover one complete UTC day")
    return f"telemetry/v1/{table}/{start.date().isoformat()}/"


class Bucket:
    """The private archive bucket. Keys are written once and always read back."""

    def __init__(self):
        variables = (
            "ARCHIVE_S3_ENDPOINT", "ARCHIVE_S3_BUCKET", "ARCHIVE_S3_ACCESS_KEY_ID",
            "ARCHIVE_S3_SECRET_ACCESS_KEY", "ARCHIVE_S3_REGION",
        )
        values = {name: os.environ.get(name, "") for name in variables}
        if not all(values.values()):
            raise ValueError(
                "Archive bucket endpoint, bucket, region and credentials are required"
            )
        style = os.environ.get("ARCHIVE_S3_ADDRESSING_STYLE", "virtual")
        if style not in {"virtual", "path"}:
            raise ValueError("Invalid archive bucket addressing style")

        import boto3
        from botocore.config import Config
        from botocore.exceptions import ClientError

        self._client_error = ClientError
        self.client = boto3.client(
            "s3", endpoint_url=values["ARCHIVE_S3_ENDPOINT"],
            region_name=values["ARCHIVE_S3_REGION"],
            aws_access_key_id=values["ARCHIVE_S3_ACCESS_KEY_ID"],
            aws_secret_access_key=values["ARCHIVE_S3_SECRET_ACCESS_KEY"],
            config=Config(s3={"addressing_style": style}),
        )
        self.name = values["ARCHIVE_S3_BUCKET"]

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.name, Key=key)
        except self._client_error as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return False
            raise
        return True

    def read(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.name, Key=key)["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def sha256(self, key: str) -> str:
        digest = hashlib.sha256()
        body = self.client.get_object(Bucket=self.name, Key=key)["Body"]
        try:
            for block in body.iter_chunks(chunk_size=1024 * 1024):
                digest.update(block)
        finally:
            body.close()
        return digest.hexdigest()

    def download(self, key: str, path: Path) -> None:
        body = self.client.get_object(Bucket=self.name, Key=key)["Body"]
        try:
            with private_file(path) as out:
                for block in body.iter_chunks(chunk_size=1024 * 1024):
                    out.write(block)
        finally:
            body.close()

    def put_once(self, path: Path, key: str) -> None:
        """Upload unless present, then require the stored bytes to equal the local file."""
        if not self.exists(key):
            self.client.upload_file(str(path), self.name, key)
        if self.sha256(key) != file_sha256(path):
            raise ValueError(f"Archive bucket readback checksum mismatch for {key}")


def upload(data_path: Path, manifest_path: Path) -> dict:
    """Upload validated files to the private archive and read the bytes back.

    The manifest is uploaded last, so its presence denotes a verified pair.
    Existing keys are read back and checked, never overwritten.
    """
    manifest = verify(data_path, manifest_path)
    prefix = object_prefix(manifest)
    bucket = Bucket()
    data_key = prefix + data_path.name
    manifest_key = prefix + manifest_path.name
    bucket.put_once(data_path, data_key)
    bucket.put_once(manifest_path, manifest_key)
    return {"bucket": bucket.name, "data_key": data_key, "manifest_key": manifest_key,
            "row_count": manifest["row_count"], "sha256": manifest["sha256"]}


def log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, default=str), flush=True)


def _same_database(url_a: str, url_b: str) -> bool:
    from psycopg.conninfo import conninfo_to_dict

    a, b = conninfo_to_dict(url_a), conninfo_to_dict(url_b)
    keys = ("host", "port", "dbname")
    return all(str(a.get(k) or "") == str(b.get(k) or "") for k in keys)


@contextmanager
def restore_connection():
    """The isolated restore database. It must not be production, and must not hold the
    bot's schema, so a mis-pointed variable fails closed before anything is written."""
    import psycopg

    url = database_url("RESTORE_DATABASE_URL")
    if _same_database(url, database_url("DATABASE_URL_RO")):
        raise ValueError("RESTORE_DATABASE_URL must be an isolated database, not production")
    with psycopg.connect(url, options="-c TimeZone=UTC", connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            if cur.fetchone()[0]:
                raise ValueError("RESTORE_DATABASE_URL holds the bot's schema; refusing")
        yield conn


def _text_rows(cur, table: str, columns: list[str], ids: list[int]) -> dict:
    from psycopg import sql

    cur.execute(
        sql.SQL("SELECT id, {} FROM {} WHERE id = ANY(%s)").format(
            sql.SQL(", ").join(sql.SQL("{}::text").format(sql.Identifier(c)) for c in columns),
            sql.Identifier("public", table),
        ),
        (ids,),
    )
    return {row[0]: row[1:] for row in cur.fetchall()}


def restore_check(data_path: Path, manifest: dict, sample_rows: int) -> dict:
    """Load one archived range into the isolated database with COPY FROM STDIN, then compare
    count, id bounds and sampled rows (every column as text) against production."""
    from psycopg import sql

    table, columns, types = manifest["table"], manifest["columns"], manifest["types"]
    if any(not _TYPE_NAME.match(t) for t in types):
        raise ValueError("Unexpected column type in manifest")
    relation = sql.Identifier("public", table)
    with restore_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(relation))
            cur.execute(sql.SQL("CREATE TABLE {} ({})").format(
                relation,
                sql.SQL(", ").join(
                    sql.SQL("{} {}").format(sql.Identifier(c), sql.SQL(t))
                    for c, t in zip(columns, types, strict=True)
                ),
            ))
            statement = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true)").format(
                relation, sql.SQL(", ").join(map(sql.Identifier, columns))
            )
            with cur.copy(statement) as copy, gzip.open(data_path, "rb") as src:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    copy.write(block)
            cur.execute(sql.SQL("SELECT count(*), min(id), max(id) FROM {}").format(relation))
            restored = cur.fetchone()
            expected = (manifest["row_count"], manifest["min_id"], manifest["max_id"])
            if tuple(restored) != expected:
                raise ValueError(f"Restore mismatch: {tuple(restored)} != {expected}")
            cur.execute(
                sql.SQL("SELECT id FROM {} ORDER BY random() LIMIT %s").format(relation),
                (sample_rows,),
            )
            ids = sorted({row[0] for row in cur.fetchall()}
                         | {i for i in expected[1:] if i is not None})
            archived = _text_rows(cur, table, columns, ids)
            # Leave nothing behind: the restore database only ever holds one range at a time.
            cur.execute(sql.SQL("DROP TABLE {}").format(relation))
        conn.commit()
    with source_connection() as conn, conn.cursor() as cur:
        production = _text_rows(cur, table, columns, ids)
    if archived != production:
        differing = sorted(i for i in ids if archived.get(i) != production.get(i))
        raise ValueError(f"Restored rows differ from production at ids {differing[:10]}")
    return {"rows": restored[0], "sampled": len(ids)}


def archive_table(table: str, chunk_rows: int, work_dir: Path, frozen_hours: float,
                  restore_test: bool, sample_rows: int = 25, pause_seconds: float = 1.0) -> dict:
    """Archive one frozen table in id ranges; resumable, and COMPLETE.json is written last."""
    if table not in FROZEN_TABLES:
        raise ValueError("Whole-table archive is limited to frozen tables")
    if chunk_rows < 1000:
        raise ValueError("chunk_rows must be at least 1000")
    bucket = Bucket()
    base = f"telemetry/v1/{table}/ids/"
    complete_key = base + "COMPLETE.json"
    if bucket.exists(complete_key):
        done = json.loads(bucket.read(complete_key))
        log("table_already_complete", table=table, rows=done["snapshot_count"])
        return done

    from psycopg import sql

    with source_connection() as conn, conn.cursor() as cur:
        clock = sql.Identifier(TABLE_CLOCKS[table])
        relation = sql.Identifier("public", table)
        cur.execute(sql.SQL("SELECT {} FROM {} ORDER BY id DESC LIMIT 1").format(clock, relation))
        newest = (cur.fetchone() or [None])[0]
        if newest is None:
            raise ValueError("Table is empty; nothing to archive")
        age_hours = (datetime.now(timezone.utc) - newest).total_seconds() / 3600
        if age_hours < frozen_hours:
            raise ValueError(f"{table} was written {age_hours:.1f}h ago; not frozen")
        # One full count, in the same snapshot as the id bounds.
        cur.execute("SET LOCAL statement_timeout = '30min'")
        cur.execute(sql.SQL("SELECT count(*), min(id), max(id) FROM {}").format(relation))
        count, min_id, max_id = cur.fetchone()
    log("snapshot", table=table, rows=count, min_id=min_id, max_id=max_id, newest=newest)

    work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    chunks = []
    for id_start in range(min_id, max_id + 1, chunk_rows):
        id_end = min(id_start + chunk_rows, max_id + 1)
        stem = ids_stem(table, id_start, id_end)
        prefix = f"{base}{id_start:012d}-{id_end:012d}/"
        data_key, manifest_key = prefix + stem + ".csv.gz", prefix + stem + ".manifest.json"
        data_path = work_dir / f"{stem}.csv.gz"
        manifest_path = work_dir / f"{stem}.manifest.json"
        if bucket.exists(manifest_key):
            # A manifest is uploaded only after its data was read back; check again anyway.
            manifest = json.loads(bucket.read(manifest_key))
            if (manifest["id_start"], manifest["id_end_exclusive"]) != (id_start, id_end):
                raise ValueError(f"Stored manifest does not cover {stem}")
            if bucket.sha256(data_key) != manifest["sha256"]:
                raise ValueError(f"Stored data does not match its manifest: {stem}")
            status = "resumed"
        else:
            for stale in (data_path, manifest_path):
                stale.unlink(missing_ok=True)
            manifest = export_ids(table, id_start, id_end, work_dir)
            upload(data_path, manifest_path)
            status = "uploaded"
        data_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        restored = None
        if restore_test:
            bucket.download(data_key, data_path)
            try:
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                verify(data_path, manifest_path)  # the bucket copy, not the local export
                restored = restore_check(data_path, manifest, sample_rows)
            finally:
                data_path.unlink(missing_ok=True)
                manifest_path.unlink(missing_ok=True)
        chunks.append({"data_key": data_key, "manifest_key": manifest_key,
                       "id_start": id_start, "id_end_exclusive": id_end,
                       "row_count": manifest["row_count"], "sha256": manifest["sha256"],
                       "compressed_bytes": manifest["compressed_bytes"]})
        log("chunk", table=table, status=status, id_start=id_start, id_end=id_end,
            rows=manifest["row_count"], bytes=manifest["compressed_bytes"], restored=restored)
        clock_time.sleep(pause_seconds)

    archived_rows = sum(c["row_count"] for c in chunks)
    if archived_rows != count:
        raise ValueError(f"Archived {archived_rows} rows but the snapshot had {count}")
    complete = {
        "format": TABLE_FORMAT, "table": table, "snapshot_count": count,
        "min_id": min_id, "max_id": max_id, "newest_row_at": newest.isoformat(),
        "chunk_rows": chunk_rows, "chunks": chunks, "restore_tested": restore_test,
        "restore_sample_rows": sample_rows if restore_test else 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "compressed_bytes": sum(c["compressed_bytes"] for c in chunks),
    }
    complete_path = work_dir / f"{table}.COMPLETE.json"
    complete_path.unlink(missing_ok=True)
    with private_file(complete_path) as out:
        out.write((json.dumps(complete, indent=2) + "\n").encode("utf-8"))
    try:
        bucket.put_once(complete_path, complete_key)
    finally:
        complete_path.unlink(missing_ok=True)
    log("table_complete", table=table, rows=count, chunks=len(chunks),
        compressed_bytes=complete["compressed_bytes"], restore_tested=restore_test)
    return complete


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
    upload_cmd = commands.add_parser("upload")
    upload_cmd.add_argument("data_path", type=Path)
    upload_cmd.add_argument("manifest_path", type=Path)
    tables_cmd = commands.add_parser("archive-tables")
    tables_cmd.add_argument("--table", action="append", required=True, choices=FROZEN_TABLES)
    tables_cmd.add_argument("--chunk-rows", type=int, default=500_000)
    tables_cmd.add_argument("--work-dir", type=Path, required=True)
    tables_cmd.add_argument("--frozen-hours", type=float, default=72.0)
    tables_cmd.add_argument("--restore-test", action="store_true")
    tables_cmd.add_argument("--sample-rows", type=int, default=25)
    args = parser.parse_args()
    if args.command == "export":
        result = export(args.table, args.day, args.output_dir)
    elif args.command == "verify":
        result = verify(args.data_path, args.manifest_path)
    elif args.command == "upload":
        result = upload(args.data_path, args.manifest_path)
    else:
        try:
            result = {
                table: {k: v for k, v in archive_table(
                    table, args.chunk_rows, args.work_dir, args.frozen_hours,
                    args.restore_test, args.sample_rows,
                ).items() if k != "chunks"}
                for table in args.table
            }
        except Exception as error:  # one clear terminal line for the job log
            log("archive_failed", error=f"{type(error).__name__}: {error}")
            return 1
    print(json.dumps(result, indent=2, default=str), file=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
