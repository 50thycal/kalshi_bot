"""Read-only ad-hoc SQL runner for the bot's Postgres database.

Built to run inside the `DB Query (read-only)` GitHub Actions workflow so that
the data can be inspected on demand without exposing the database to anything
other than a scoped, read-only role. It is deliberately self-contained (only
needs `psycopg`) so the workflow can install a single dependency.

Three layers of read-only enforcement, in order of importance:
  1. The DATABASE_URL_RO secret should point at a Postgres role that only has
     SELECT (see scripts/sql/create_readonly_role.sql). This is the real guard.
  2. The session is opened with `default_transaction_read_only=on`, so the
     server rejects any write even if the role somehow could perform one.
  3. A lightweight lexical check rejects obvious write/DDL statements and
     multiple statements before anything is sent to the server.

Usage:
    DATABASE_URL_RO=postgresql://... SQL="select count(*) from signals" \
        python scripts/db_query.py
    # or
    python scripts/db_query.py "select * from bot_runs order by id desc limit 5"
"""

from __future__ import annotations

import os
import sys

# Statements that must never reach the database. The read-only role/transaction
# already block these server-side; this just fails fast with a clear message.
_FORBIDDEN = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "comment", "copy", "merge", "call", "do",
    "vacuum", "reindex", "refresh", "set ", "reset ",
)

MAX_ROWS_DEFAULT = 200
CELL_WIDTH = 200  # truncate wide cells (e.g. JSONB) so logs stay readable
STATEMENT_TIMEOUT_MS = 30_000


def _to_libpq_url(url: str) -> str:
    """Strip the SQLAlchemy `+driver` suffix so plain libpq/psycopg accepts it."""
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _resolve_sql() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1]
    env_sql = os.environ.get("SQL", "").strip()
    if env_sql:
        return env_sql
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _validate(sql: str) -> str:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SystemExit("No SQL provided (pass an argument, set SQL, or pipe stdin).")
    if ";" in sql:
        raise SystemExit("Only a single statement is allowed (found ';').")
    lowered = sql.lower()
    # Allow a leading WITH ... only if the body is a SELECT; otherwise require SELECT.
    head = lowered.lstrip("(")
    if not (head.startswith("select") or head.startswith("with") or head.startswith("table") or head.startswith("explain")):
        raise SystemExit("Only read-only SELECT/WITH/TABLE/EXPLAIN queries are allowed.")
    for kw in _FORBIDDEN:
        if kw in lowered:
            raise SystemExit(f"Rejected: statement contains a write/DDL keyword ('{kw.strip()}').")
    return sql


def _print_table(columns: list[str], rows: list[tuple]) -> None:
    def cell(v: object) -> str:
        s = "" if v is None else str(v)
        s = s.replace("\n", " ").replace("\t", " ")
        return s if len(s) <= CELL_WIDTH else s[: CELL_WIDTH - 1] + "…"

    str_rows = [[cell(v) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))
    sep = "  "
    print(sep.join(c.ljust(widths[i]) for i, c in enumerate(columns)))
    print(sep.join("-" * widths[i] for i in range(len(columns))))
    for row in str_rows:
        print(sep.join(v.ljust(widths[i]) for i, v in enumerate(row)))


def main() -> int:
    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    sql = _validate(_resolve_sql())
    try:
        max_rows = int(os.environ.get("MAX_ROWS", MAX_ROWS_DEFAULT))
    except ValueError:
        max_rows = MAX_ROWS_DEFAULT

    import psycopg  # deferred so the read-only guards run before the driver is needed

    # Server-side read-only + timeouts, independent of the role's grants.
    options = (
        f"-c default_transaction_read_only=on "
        f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
        f"-c idle_in_transaction_session_timeout=60000"
    )
    with psycopg.connect(url, options=options, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description is None:
                print("(no result set)")
                return 0
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            rows = rows[:max_rows]
            _print_table(columns, rows)
            print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''}"
                  + (f"; output capped at {max_rows} — refine the query for more)" if truncated else ")"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
