"""Experiment OS status — read-only inspection of the experiment lifecycle tables.

The ops-channel window into the Experiment OS foundation (docs/EXPERIMENT_OS_FOUNDATION.md,
spec docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md): every experiment with its lifecycle state,
recent audited transitions, the platform component/revision registry with the active
snapshot, and unresolved integrity events. Until the legacy importer PR lands the tables
are expected to be empty — this script says so explicitly rather than printing nothing.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/experiment_os_status.py
    # or:  {"type": "script", "name": "experiment_os_status"}
    #      {"type": "script", "name": "experiment_os_status", "args": ["--transitions", "40"]}
"""

from __future__ import annotations

import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def _table(headers, rows):
    cells = [[("-" if c is None else str(c)) for c in r] for r in rows]
    widths = [
        max(len(str(h)), *(len(r[i]) for r in cells)) if cells else len(str(h))
        for i, h in enumerate(headers)
    ]
    print("    " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths, strict=False)))
    for r in cells:
        print("    " + "  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)))


def report(cur, n_transitions: int) -> None:
    print("=== EXPERIMENT OS STATUS (foundation — enforcement mode OFF) ===\n")

    present = _rows(cur, "SELECT to_regclass('public.experiments') IS NOT NULL")[0][0]
    if not present:
        print("  experiment OS tables do not exist yet — the foundation migration has not")
        print("  been applied to this database.")
        return

    # --- 1) experiments ---------------------------------------------------------------
    total = _rows(cur, "SELECT count(*) FROM experiments")[0][0]
    print(f"=== 1) EXPERIMENTS ({total}) ===")
    if not total:
        print("    none recorded. Expected until the legacy importer PR — no runtime path")
        print("    writes these tables in the foundation release.")
    else:
        rows = _rows(cur, """
            SELECT state, count(*) FROM experiments GROUP BY 1 ORDER BY 2 DESC, 1
        """)
        print("    by state: " + "  ".join(f"{s}={c}" for s, c in rows))
        rows = _rows(cur, """
            SELECT e.key, e.state, COALESCE(e.origin, '-'), COALESCE(e.family, '-'),
                   COALESCE('v' || v.maxv::text, '-'),
                   COALESCE(e.legacy_class, 'native'), COALESCE(e.migration_integrity, '-')
            FROM experiments e
            LEFT JOIN (SELECT experiment_id, max(version) AS maxv
                       FROM experiment_versions GROUP BY 1) v ON v.experiment_id = e.id
            ORDER BY e.key
        """)
        _table(["key", "state", "origin", "family", "ver", "class", "integrity"], rows)

    # --- 2) recent transitions --------------------------------------------------------
    print(f"\n=== 2) RECENT TRANSITIONS (last {n_transitions}) ===")
    rows = _rows(cur, """
        SELECT t.occurred_at::timestamp(0), e.key,
               COALESCE(t.from_state, '(created)'), t.to_state, t.actor,
               COALESCE(t.approved_by, '-'), COALESCE(left(t.reason, 60), '-')
        FROM experiment_state_transitions t
        JOIN experiments e ON e.id = t.experiment_id
        ORDER BY t.occurred_at DESC, t.id DESC
        LIMIT %s
    """, (n_transitions,))
    if not rows:
        print("    none recorded.")
    else:
        _table(["occurred_at", "experiment", "from", "to", "actor", "approved", "reason"],
               rows[::-1])

    # --- 3) platform registry ---------------------------------------------------------
    print("\n=== 3) PLATFORM REGISTRY ===")
    rows = _rows(cur, """
        SELECT c.key,
               COALESCE(a.version, '(none active)'),
               a.activated_at::timestamp(0),
               (SELECT count(*) FROM platform_revisions r WHERE r.component_id = c.id)
        FROM platform_components c
        LEFT JOIN platform_revisions a
               ON a.component_id = c.id AND a.status = 'active'
        ORDER BY c.key
    """)
    if not rows:
        print("    no components registered. Expected until the legacy importer PR seeds the")
        print("    baseline (components + revisions + the first complete snapshot).")
    else:
        _table(["component", "active revision", "activated_at", "revisions"], rows)
        snaps = _rows(cur, """
            SELECT count(*), max(created_at)::timestamp(0) FROM platform_snapshots
        """)[0]
        print(f"    snapshots recorded: {snaps[0]}   newest: {snaps[1]}")

    # --- 4) integrity -----------------------------------------------------------------
    print("\n=== 4) UNRESOLVED INTEGRITY EVENTS ===")
    rows = _rows(cur, """
        SELECT i.detected_at::timestamp(0), e.key, i.kind, i.severity,
               COALESCE(left(i.description, 70), '-')
        FROM experiment_integrity_events i
        JOIN experiments e ON e.id = i.experiment_id
        WHERE i.resolved_at IS NULL
        ORDER BY i.detected_at
    """)
    if not rows:
        print("    none — all clear.")
    else:
        _table(["detected_at", "experiment", "kind", "severity", "description"], rows)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    n_transitions = 25
    if "--transitions" in argv:
        i = argv.index("--transitions")
        if i + 1 < len(argv):
            n_transitions = int(argv[i + 1])

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, n_transitions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
