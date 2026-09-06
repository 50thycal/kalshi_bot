"""Every SQL string in the ops scripts must survive psycopg's placeholder parser.

`scripts/db_query.py` and the analysis scripts call `cur.execute(sql, params)`
with a params tuple, so psycopg scans the WHOLE query string — SQL comments
included — for placeholders. A lone `%` (e.g. a `LIKE 'x%'` wildcard, or a `%`
inside an explanatory comment) raises ProgrammingError and aborts the run.

This bit twice in one afternoon: first a `LIKE p.value || '%'` in the coverage
section of `experiment_os_status`, then the comment added to explain the fix.
Both times the failure surfaced only in production, because these scripts run on
a GitHub Actions runner against the real database and nothing exercised their
SQL locally. This test does, offline, using psycopg's own parser.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

# Scripts whose SQL is asserted here. Ops scripts run unattended against
# production; add new ones as they are written.
CHECKED = ["experiment_os_status.py", "ops_doctor.py", "mmsell_series_pnl.py",
           "series_registry_review.py"]


def _sql_literals(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every string constant in the module that looks like a SQL statement."""
    tree = ast.parse(path.read_text())
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            head = node.value.lstrip().upper()
            if head.startswith(("SELECT", "WITH", "TABLE", "EXPLAIN")):
                out.append((node.lineno, node.value))
    return out


@pytest.mark.parametrize("script", CHECKED)
def test_ops_sql_survives_psycopg_placeholder_parsing(script):
    from psycopg._queries import _split_query

    path = SCRIPTS / script
    literals = _sql_literals(path)
    assert literals, f"no SQL found in {script} — did the extraction break?"
    for lineno, sql in literals:
        try:
            _split_query(sql.encode(), "utf-8")
        except Exception as exc:  # noqa: BLE001 — the parser's own error is the message
            pytest.fail(
                f"{script}:{lineno} SQL is not psycopg-safe: {exc}\n"
                "A literal percent must be DOUBLED — in the SQL and in any "
                "comment inside the same string.\n"
                f"{sql.strip()[:400]}"
            )
