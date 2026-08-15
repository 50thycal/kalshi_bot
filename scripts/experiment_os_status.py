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

    # --- 5) legacy coverage -----------------------------------------------------------
    # Every strategy tag that has ever traded, resolved against the imported
    # classification: concrete deployment-arm tags, plus each experiment's declared
    # covered_tags / covered_tag_prefixes (docs_json). An UNMAPPED tag traded real
    # (paper or live) volume but resolves to no experiment — the migration is not
    # done while this list is non-empty.
    print("\n=== 5) LEGACY COVERAGE (paper_trades/live_orders tags vs imports) ===")
    if not total:
        print("    no experiments imported yet — every tag is unmapped by definition.")
    else:
        rows = _rows(cur, """
            WITH tags AS (
                SELECT DISTINCT strategy AS tag FROM paper_trades WHERE strategy IS NOT NULL
                UNION
                SELECT DISTINCT strategy FROM live_orders WHERE strategy IS NOT NULL
            ),
            covered AS (
                SELECT DISTINCT strategy_tag AS tag
                FROM experiment_deployment_arms WHERE strategy_tag IS NOT NULL
            )
            SELECT t.tag FROM tags t
            WHERE t.tag NOT IN (SELECT tag FROM covered)
              AND NOT EXISTS (
                    SELECT 1 FROM experiments e
                    WHERE e.docs_json IS NOT NULL
                      AND (e.docs_json::jsonb -> 'covered_tags') ? t.tag)
              AND NOT EXISTS (
                    SELECT 1 FROM experiments e,
                         jsonb_array_elements_text(
                             COALESCE(e.docs_json::jsonb -> 'covered_tag_prefixes',
                                      '[]'::jsonb)) p
                    WHERE t.tag LIKE p.value || '%')
            ORDER BY 1
        """)
        ntags = _rows(cur, """
            SELECT count(*) FROM (
                SELECT DISTINCT strategy FROM paper_trades WHERE strategy IS NOT NULL
                UNION
                SELECT DISTINCT strategy FROM live_orders WHERE strategy IS NOT NULL
            ) t
        """)[0][0]
        if not rows:
            print(f"    all {ntags} traded tags map to an experiment — coverage complete.")
        else:
            print(f"    {len(rows)} of {ntags} traded tags are UNMAPPED:")
            for (tag,) in rows:
                print(f"      - {tag}")
            print("    (classify them in the manifest, or record HISTORICAL_UNTRACKED;")
            print("     nothing is auto-stubbed. Evo tags never appear here — evo trades")
            print("     live in evo_* tables under evo lineage.)")

    # --- 6) scoreboard ----------------------------------------------------------------
    # Per active experiment: universal paper metrics per arm over the CURRENT epoch's
    # window (epoch start floor — the clean evidence floor; individual gates may carry
    # tighter recorded floors), and each gate's latest recorded verdict. Settled =
    # every terminal-with-P&L status; filtering 'settled' alone would silently drop
    # stop-closed trades (the recorded mmsellA1-A3 reading error).
    print("\n=== 6) SCOREBOARD (current-epoch evidence + latest gate verdicts) ===")
    rows = _rows(cur, """
        WITH cur AS (
            SELECT e.id AS exp_id, e.key, v.id AS ver_id, ep.id AS epoch_id,
                   ep.epoch_number, ep.started_at,
                   COALESCE(ep.ended_at, now()) AS window_end
            FROM experiments e
            JOIN experiment_versions v ON v.experiment_id = e.id
                 AND v.version = (SELECT max(version) FROM experiment_versions
                                  WHERE experiment_id = e.id)
            JOIN experiment_epochs ep ON ep.version_id = v.id
                 AND ep.epoch_number = (SELECT max(epoch_number) FROM experiment_epochs
                                        WHERE version_id = v.id)
            WHERE e.state NOT IN ('RETIRED')
        )
        SELECT cur.key, cur.epoch_number, a.arm_key, da.strategy_tag,
               count(pt.id) FILTER (WHERE pt.status IN
                   ('settled','closed_sl','closed_tp','closed_timeout')) AS settled,
               ROUND(COALESCE(sum(pt.pnl) FILTER (WHERE pt.status IN
                   ('settled','closed_sl','closed_tp','closed_timeout')), 0)::numeric
                   * 100 / NULLIF(count(pt.id) FILTER (WHERE pt.status IN
                   ('settled','closed_sl','closed_tp','closed_timeout')), 0), 2)
                   AS cents_per_trade,
               ROUND(100.0 * count(pt.id) FILTER (WHERE pt.pnl > 0 AND pt.status IN
                   ('settled','closed_sl','closed_tp','closed_timeout'))
                   / NULLIF(count(pt.id) FILTER (WHERE pt.status IN
                   ('settled','closed_sl','closed_tp','closed_timeout')), 0), 1)
                   AS win_pct,
               count(pt.id) FILTER (WHERE pt.status = 'open') AS open_n
        FROM cur
        JOIN experiment_epochs ep ON ep.id = cur.epoch_id
        JOIN experiment_deployments d ON d.epoch_id = ep.id AND d.kind = 'paper'
        JOIN experiment_deployment_arms da ON da.deployment_id = d.id
        JOIN experiment_arms a ON a.id = da.arm_id
        LEFT JOIN paper_trades pt ON pt.strategy = da.strategy_tag
             AND pt.created_at >= cur.started_at AND pt.created_at < cur.window_end
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 3
    """)
    if not rows:
        print("    no active experiments with paper deployments.")
    else:
        _table(["experiment", "epoch", "arm", "tag", "settled", "c/trade", "win%", "open"],
               rows)
    rows = _rows(cur, """
        SELECT e.key, g.gate_key, r.verdict, r.computed_at::timestamp(0), r.computed_by
        FROM experiment_gates g
        JOIN experiment_versions v ON v.id = g.version_id
        JOIN experiments e ON e.id = v.experiment_id
        JOIN LATERAL (
            SELECT verdict, computed_at, computed_by
            FROM experiment_gate_results r
            WHERE r.gate_id = g.id
            ORDER BY r.computed_at DESC, r.id DESC LIMIT 1
        ) r ON true
        WHERE e.state NOT IN ('RETIRED')
        ORDER BY 1, 2
    """)
    if not rows:
        print("    no gate results recorded yet (evaluate via the CLI or PR 3 evaluator).")
    else:
        print("\n    latest gate verdicts:")
        _table(["experiment", "gate", "verdict", "computed_at", "by"], rows)


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
