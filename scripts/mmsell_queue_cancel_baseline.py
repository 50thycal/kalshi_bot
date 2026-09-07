"""QUEUE-AWARE CANCELLATION — the read-only baseline and shadow report.

WHAT THIS ANSWERS (docs/MMSELL_QUEUE_AWARE_CANCEL.md)
------------------------------------------------------
1. COVERAGE      — what fraction of resting live orders return a queue position at all.
2. SURVIVAL      — P(fill before the 4h timeout | still resting at age A, current depth D),
                   the table the frozen rule reads. Counts, not just percentages, because a
                   thin cell is not evidence.
3. OUTCOMES      — where resting time goes: filled / timeout / exchange-cancelled, with the
                   capital-hours each consumed.
4. FILL TIMING   — realized c/contract by age-at-fill x depth: are the late fills the losers
                   (the hypothesis) or the winners (what the 2026-09-07 baseline found)?
5. CAPACITY      — did the open-position cap bind (gate:open_cap refusals per day)? A freed
                   slot is worth something only on the days this says yes.
6. RULE REPLAY   — apply the FROZEN rule (live/queue_cancel.FROZEN_RULE) to the historical
                   ticks: which orders it would have cancelled, how many of those later
                   filled anyway, and what those fills earned. Retrospective, on data the
                   rule's own table was fitted to — a sanity check, NOT the shadow gate.
7. SHADOW        — the live shadow's own decision rows, when LIVE_QUEUE_CANCEL_MODE=shadow
                   has been running: coverage, would-cancels, later fills, forgone P&L,
                   cap-bound share. This is what the pre-registered gates read.

Read-only, stdlib + psycopg. Never writes, never cancels.

    {"type": "script", "name": "mmsell_queue_cancel_baseline"}
    {"type": "script", "name": "mmsell_queue_cancel_baseline", "args": ["--hours", "336"]}
"""

from __future__ import annotations

import argparse
import os
import sys

from kalshi_bot.live.queue_cancel import (
    AGE_CHECKPOINTS_MIN,
    DEPTH_BUCKETS,
    FROZEN_RULE,
    QueueCancelRule,
)

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

LIVE_TAG_PREFIXES = ("Cmmsell10", "Dmmsell10", "Emmsell10", "Fmmsell10", "Gmmsell10", "Hmmsell10")


# ---------------------------------------------------------------- pure helpers (tested)


def qualifying_cells(rule: QueueCancelRule = FROZEN_RULE) -> set[tuple[int, str]]:
    """The (age checkpoint, depth bucket) cells at which the rule opens the gate."""
    out = set()
    for (cp, bucket), (n, filled) in rule.survival.items():
        if cp * 60 < rule.min_age_seconds or n < rule.min_cell_n or n <= 0:
            continue
        if 100.0 * filled / n <= rule.max_fill_probability_pct:
            out.add((cp, bucket))
    return out


def bucket_case_sql(column: str) -> str:
    """SQL CASE mapping a contracts-ahead column onto the rule's depth buckets."""
    parts = []
    for name, upper in DEPTH_BUCKETS:
        if upper is None:
            parts.append(f"ELSE '{name}'")
        else:
            parts.append(f"WHEN {column} <= {upper} THEN '{name}'")
    return f"CASE WHEN {column} IS NULL THEN 'no_tel' " + " ".join(parts) + " END"


def checkpoint_case_sql(age_min_expr: str) -> str:
    """SQL CASE mapping an age-in-minutes expression onto the LARGEST reached checkpoint."""
    parts = [f"WHEN {age_min_expr} >= {cp} THEN {cp}" for cp in sorted(AGE_CHECKPOINTS_MIN, reverse=True)]
    return "CASE " + " ".join(parts) + " ELSE NULL END"


def qualifying_cells_sql(cells: set[tuple[int, str]], cp_col: str, bucket_col: str) -> str:
    """A boolean SQL expression true when (checkpoint, bucket) is a qualifying cell."""
    if not cells:
        return "FALSE"
    return "(" + " OR ".join(
        f"({cp_col} = {cp} AND {bucket_col} = '{bucket}')" for cp, bucket in sorted(cells)
    ) + ")"


def _tag_filter(col: str = "strategy") -> str:
    return "(" + " OR ".join(f"{col} LIKE '{p}%'" for p in LIVE_TAG_PREFIXES) + ")"


def _pct(x) -> str:
    return f"{x * 100:.1f}%" if x is not None else "n/a"


def _num(x, nd=1) -> str:
    return f"{float(x):.{nd}f}" if x is not None else "n/a"


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# ---------------------------------------------------------------- sections


def _coverage(cur, hours: int) -> bool:
    cur.execute(
        "WITH o AS (SELECT kalshi_order_id FROM live_orders"
        f" WHERE {_tag_filter()} AND kalshi_order_id IS NOT NULL"
        f"   AND created_at >= now() - interval '{int(hours)} hours' AND status <> 'pending')"
        " SELECT count(*) AS orders,"
        "        count(*) FILTER (WHERE EXISTS (SELECT 1 FROM live_order_queue_ticks t"
        "                  WHERE t.kalshi_order_id = o.kalshi_order_id)) AS with_any_tick,"
        "        count(*) FILTER (WHERE EXISTS (SELECT 1 FROM live_order_queue_ticks t"
        "                  WHERE t.kalshi_order_id = o.kalshi_order_id"
        "                    AND t.queue_position IS NOT NULL)) AS with_rank,"
        "        (SELECT count(*) FROM live_order_queue_ticks"
        f"          WHERE captured_at >= now() - interval '{int(hours)} hours') AS samples,"
        "        (SELECT count(*) FROM live_order_queue_ticks"
        f"          WHERE captured_at >= now() - interval '{int(hours)} hours'"
        "            AND queue_position IS NULL) AS null_samples"
        " FROM o"
    )
    orders, any_tick, with_rank, samples, null_samples = cur.fetchone()
    print(f"=== 1. COVERAGE (last {hours}h, live mmsell10 canary tags) ===")
    if not orders:
        print("  no live orders in window — nothing below can be read.")
        return False
    print(f"  orders {orders}   with >=1 queue sample {any_tick} ({_pct(any_tick / orders)})"
          f"   with a readable rank {with_rank} ({_pct(with_rank / orders)})")
    print(f"  samples {samples}   unreadable {null_samples}"
          f" ({_pct(null_samples / samples) if samples else 'n/a'})")
    print("  (an order with no sample at all usually filled inside its first cycle — see")
    print("   the `no_tel` rows in section 4; that is fast fills, not a sampler fault)")
    return with_rank > 0


def _survival(cur, hours: int) -> None:
    print("\n=== 2. SURVIVAL — P(fill before timeout | resting at age A, depth D now) ===")
    cps = ",".join(str(c) for c in AGE_CHECKPOINTS_MIN)
    cur.execute(
        "WITH o AS (SELECT kalshi_order_id, created_at, status FROM live_orders"
        f" WHERE {_tag_filter()} AND kalshi_order_id IS NOT NULL"
        f"   AND created_at >= now() - interval '{int(hours)} hours'"
        "   AND status IN ('filled','canceled')),"
        f" cp AS (SELECT unnest(ARRAY[{cps}]) AS age_min),"
        " tk AS (SELECT t.kalshi_order_id, t.contracts_ahead,"
        "               EXTRACT(EPOCH FROM (t.captured_at - o.created_at))/60.0 AS age"
        "        FROM live_order_queue_ticks t JOIN o USING (kalshi_order_id)"
        "        WHERE t.queue_position IS NOT NULL),"
        " at_cp AS (SELECT DISTINCT ON (o.kalshi_order_id, cp.age_min)"
        "                  o.kalshi_order_id, cp.age_min, o.status, tk.contracts_ahead"
        "           FROM o CROSS JOIN cp JOIN tk USING (kalshi_order_id)"
        "           WHERE tk.age BETWEEN cp.age_min - 3 AND cp.age_min + 3"
        "           ORDER BY o.kalshi_order_id, cp.age_min, abs(tk.age - cp.age_min))"
        f" SELECT age_min, {bucket_case_sql('contracts_ahead')} AS bucket, count(*) AS n,"
        "        count(*) FILTER (WHERE status='filled') AS filled_later"
        " FROM at_cp GROUP BY 1,2 ORDER BY 1,2"
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no orders have both queue samples and a terminal status yet)")
        return
    cells = qualifying_cells()
    print(f"  {'age':>4} {'depth':>8} {'resting':>8} {'fill later':>11} {'P(fill)':>8}  rule")
    for age, bucket, n, filled in rows:
        p = filled / n if n else None
        mark = "CANCEL" if (age, bucket) in cells else ("thin" if n < FROZEN_RULE.min_cell_n else "")
        print(f"  {age:>4} {bucket:>8} {n:>8} {filled:>11} {_pct(p):>8}  {mark}")
    print(f"  (rule {FROZEN_RULE.rule_version}: cancel where P(fill) <= "
          f"{FROZEN_RULE.max_fill_probability_pct}% with n >= {FROZEN_RULE.min_cell_n} and age >= "
          f"{FROZEN_RULE.min_age_seconds // 60} min. The frozen table is the 2026-09-07 read;")
    print("   a re-run here shows drift, and drift is a new Version, never a quiet re-tune.)")


def _outcomes(cur, hours: int) -> None:
    print("\n=== 3. OUTCOMES — where resting time and capital go ===")
    cur.execute(
        "WITH o AS (SELECT o.kalshi_order_id, o.strategy, o.created_at, o.status,"
        "                  o.limit_price, o.quantity, o.cancel_reason FROM live_orders o"
        f" WHERE {_tag_filter('o.strategy')} AND o.kalshi_order_id IS NOT NULL"
        f"   AND o.created_at >= now() - interval '{int(hours)} hours'),"
        " t AS (SELECT kalshi_order_id, max(captured_at) AS last_at"
        "       FROM live_order_queue_ticks GROUP BY 1),"
        " j AS (SELECT o.*, EXTRACT(EPOCH FROM (COALESCE(t.last_at, o.created_at) - o.created_at))"
        "              /3600.0 AS rest_h, o.quantity * o.limit_price / 100.0 AS cap_usd"
        "       FROM o LEFT JOIN t USING (kalshi_order_id))"
        " SELECT status, COALESCE(left(cancel_reason, 20), '') AS reason, count(*),"
        "        round(sum(cap_usd)::numeric, 2), round(avg(rest_h)::numeric, 2),"
        "        round(sum(cap_usd * rest_h)::numeric, 2)"
        " FROM j GROUP BY 1, 2 ORDER BY 1, 2"
    )
    rows = cur.fetchall()
    print(f"  {'status':>9} {'reason':>20} {'n':>5} {'capital $':>10} {'avg rest h':>11} {'$-hours':>9}")
    for status, reason, n, cap, rest, caph in rows:
        print(f"  {status:>9} {reason:>20} {n:>5} {_num(cap, 2):>10} {_num(rest, 2):>11} {_num(caph, 2):>9}")
    print("  (an empty reason on `canceled` is an EXCHANGE-side cancel — typically market close;")
    print("   `timeout` is our 4h rule. At a $1 clip the $-hours are small in dollars: the")
    print("   scarce thing is the SLOT the order holds, and only while section 5 says the cap binds.)")


def _fill_timing(cur, hours: int) -> None:
    print("\n=== 4. FILL TIMING — realized c/contract by age-at-fill x depth at last sample ===")
    cur.execute(
        "WITH o AS (SELECT o.kalshi_order_id, o.created_at, o.quantity, o.market_ticker"
        f" FROM live_orders o WHERE {_tag_filter('o.strategy')} AND o.kalshi_order_id IS NOT NULL"
        f"   AND o.created_at >= now() - interval '{int(hours)} hours' AND o.status = 'filled'),"
        " contested AS (SELECT market_ticker FROM live_orders GROUP BY 1"
        "               HAVING count(DISTINCT strategy) > 1),"
        " t AS (SELECT DISTINCT ON (kalshi_order_id) kalshi_order_id, captured_at AS last_at,"
        "              contracts_ahead AS last_ahead FROM live_order_queue_ticks"
        "       WHERE queue_position IS NOT NULL ORDER BY kalshi_order_id, captured_at DESC),"
        " p AS (SELECT DISTINCT ON (market_ticker) market_ticker, quantity, realized_pnl"
        "       FROM positions ORDER BY market_ticker, captured_at DESC),"
        " j AS (SELECT o.*, t.last_ahead,"
        "              EXTRACT(EPOCH FROM (COALESCE(t.last_at, o.created_at) - o.created_at))/60.0 AS age_min,"
        "              p.quantity AS pos_qty, p.realized_pnl,"
        "              (o.market_ticker IN (SELECT market_ticker FROM contested)) AS contested"
        "       FROM o LEFT JOIN t USING (kalshi_order_id)"
        "       LEFT JOIN p ON p.market_ticker = o.market_ticker)"
        " SELECT CASE WHEN age_min < 5 THEN 'a<5' WHEN age_min < 30 THEN 'a5_30'"
        "             WHEN age_min < 90 THEN 'a30_90' WHEN age_min < 180 THEN 'a90_180'"
        "             ELSE 'a180+' END AS age_at_fill,"
        f"        {bucket_case_sql('last_ahead')} AS depth, count(*) AS fills,"
        "        count(*) FILTER (WHERE NOT contested AND pos_qty = 0 AND realized_pnl IS NOT NULL) AS settled,"
        "        round((100 * sum(realized_pnl) FILTER (WHERE NOT contested AND pos_qty = 0)"
        "              / NULLIF(sum(quantity) FILTER (WHERE NOT contested AND pos_qty = 0), 0))::numeric, 2),"
        "        count(*) FILTER (WHERE NOT contested AND pos_qty = 0 AND realized_pnl > 0),"
        "        count(*) FILTER (WHERE NOT contested AND pos_qty = 0 AND realized_pnl < 0)"
        " FROM j GROUP BY 1, 2 ORDER BY 1, 2"
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no filled orders in window)")
        return
    print(f"  {'age@fill':>9} {'depth':>8} {'fills':>6} {'settled':>8} {'c/ct':>8} {'wins':>5} {'losses':>7}")
    for age, depth, fills, settled, cpc, wins, losses in rows:
        print(f"  {age:>9} {depth:>8} {fills:>6} {settled:>8} {_num(cpc, 2):>8} {wins:>5} {losses:>7}")
    print("  (2026-09-07 baseline: every fill after 90 min was a winner (~+7c/ct, 0 losses of 29);")
    print("   the losers are the fast fills. A rule that cancels late-resting orders forgoes the")
    print("   book's best fills — the shadow's `forgone c/would-cancel` measures exactly that cost.)")


def _capacity(cur, hours: int) -> None:
    print("\n=== 5. CAPACITY — does the open-position cap bind? (gate:open_cap refusals/day) ===")
    cur.execute(
        "SELECT recorded_at::date::text AS day, live_tag,"
        "       count(*) FILTER (WHERE live_outcome = 'gate:open_cap') AS open_cap,"
        "       count(*) FILTER (WHERE live_outcome = 'placed') AS placed,"
        "       count(*) FILTER (WHERE live_outcome LIKE 'gate:%' AND live_outcome <> 'gate:open_cap'"
        "                        AND live_outcome <> 'gate:dedup') AS other_gates"
        " FROM live_paper_parity_events"
        f" WHERE recorded_at >= now() - interval '{int(hours)} hours' AND {_tag_filter('live_tag')}"
        " GROUP BY 1, 2 ORDER BY 1, 2"
    )
    rows = cur.fetchall()
    if not rows:
        print("  (no parity tape in window)")
        return
    print(f"  {'day':>10} {'tag':>10} {'open_cap':>9} {'placed':>7} {'other gates':>12}")
    for day, tag, oc, placed, other in rows:
        flag = "  BINDING" if oc and placed and oc >= placed * 0.5 else ""
        print(f"  {day:>10} {tag:>10} {oc:>9} {placed:>7} {other:>12}{flag}")
    print("  (BINDING = refusals at the cap are at least half of what was placed. A released")
    print("   slot has value on those days and none on the others.)")


def _rule_replay(cur, hours: int) -> None:
    print(f"\n=== 6. RULE REPLAY — {FROZEN_RULE.rule_version} applied to the historical ticks ===")
    cells = qualifying_cells()
    cp_sql = checkpoint_case_sql("age_min")
    bucket_sql = bucket_case_sql("contracts_ahead")
    cur.execute(
        "WITH o AS (SELECT o.kalshi_order_id, o.created_at, o.status, o.quantity, o.limit_price,"
        "                  o.market_ticker FROM live_orders o"
        f" WHERE {_tag_filter('o.strategy')} AND o.kalshi_order_id IS NOT NULL"
        f"   AND o.created_at >= now() - interval '{int(hours)} hours'"
        "   AND o.status IN ('filled','canceled')),"
        " tk AS (SELECT t.kalshi_order_id, t.captured_at, t.contracts_ahead,"
        "               EXTRACT(EPOCH FROM (t.captured_at - o.created_at))/60.0 AS age_min"
        "        FROM live_order_queue_ticks t JOIN o USING (kalshi_order_id)"
        "        WHERE t.queue_position IS NOT NULL),"
        f" scored AS (SELECT kalshi_order_id, captured_at, {cp_sql} AS cp, {bucket_sql} AS bucket FROM tk),"
        " first_hit AS (SELECT kalshi_order_id, min(captured_at) AS hit_at FROM scored"
        f"               WHERE {qualifying_cells_sql(cells, 'cp', 'bucket')} GROUP BY 1),"
        " last_tick AS (SELECT kalshi_order_id, max(captured_at) AS last_at FROM tk GROUP BY 1),"
        " contested AS (SELECT market_ticker FROM live_orders GROUP BY 1 HAVING count(DISTINCT strategy) > 1),"
        " p AS (SELECT DISTINCT ON (market_ticker) market_ticker, quantity, realized_pnl"
        "       FROM positions ORDER BY market_ticker, captured_at DESC)"
        " SELECT count(*) AS orders,"
        "        count(f.hit_at) AS would_cancel,"
        "        count(f.hit_at) FILTER (WHERE o.status = 'filled') AS later_filled,"
        "        round(sum(o.realized) FILTER (WHERE f.hit_at IS NOT NULL)::numeric, 2) AS forgone_usd,"
        "        round(sum(o.quantity * o.limit_price / 100.0"
        "                  * EXTRACT(EPOCH FROM (l.last_at - f.hit_at)) / 3600.0)"
        "              FILTER (WHERE f.hit_at IS NOT NULL)::numeric, 2) AS released_usd_hours"
        " FROM (SELECT o.*, CASE WHEN o.status = 'filled' AND o.market_ticker NOT IN"
        "                        (SELECT market_ticker FROM contested) AND p.quantity = 0"
        "                   THEN p.realized_pnl ELSE 0 END AS realized"
        "       FROM o LEFT JOIN p ON p.market_ticker = o.market_ticker) o"
        " LEFT JOIN first_hit f USING (kalshi_order_id)"
        " LEFT JOIN last_tick l USING (kalshi_order_id)"
    )
    orders, would, later, forgone, released = cur.fetchone()
    print(f"  qualifying cells: {sorted(cells)}")
    print(f"  terminal orders {orders}   would-cancel {would}"
          f"   later filled anyway {later} ({_pct(later / would) if would else 'n/a'})")
    print(f"  forgone realized ${_num(forgone, 2)} over {would} would-cancels"
          f" = {_num(100 * float(forgone or 0) / would if would else None, 2)} c/would-cancel")
    print(f"  resting $-hours the rule would have cut: {_num(released, 2)}")
    print("  (IN-SAMPLE: the rule's table was fitted on these same orders, so this can only")
    print("   look as good as the table. The pre-registered gates read the SHADOW (section 7).)")


def _shadow(cur, hours: int) -> None:
    print(f"\n=== 7. SHADOW — live_order_queue_decisions (last {hours}h) ===")
    cur.execute(
        "SELECT count(*), count(*) FILTER (WHERE telemetry_status = 'observed'),"
        "       count(DISTINCT kalshi_order_id) FILTER (WHERE decision = 'queue_cancel'),"
        "       count(*) FILTER (WHERE acted), count(DISTINCT rule_version), min(mode), max(mode),"
        "       count(*) FILTER (WHERE experiment_deployment_arm_id IS NULL)"
        " FROM live_order_queue_decisions"
        f" WHERE decided_at >= now() - interval '{int(hours)} hours'"
    )
    rows, observed, would, acted, rules, mode_lo, mode_hi, unstamped = cur.fetchone()
    if not rows:
        print("  no decision rows. Expected while LIVE_QUEUE_CANCEL_MODE=off (the default) or")
        print("  while nothing is resting. Set the mode to `shadow` to start the instrument.")
        return
    print(f"  rows {rows}   telemetry observed {observed} ({_pct(observed / rows)})"
          f"   would-cancel orders {would}   cancels actually sent {acted}")
    print(f"  mode {mode_lo}{'/' + mode_hi if mode_hi != mode_lo else ''}   rule versions {rules}"
          f"   rows without Experiment OS lineage {unstamped}")
    if unstamped:
        print("  !! unstamped rows: the shadow ran before the experiment was registered (or its")
        print("     epoch is closed). Those rows are telemetry only; no gate can read them.")
    cur.execute(
        "WITH first_hit AS (SELECT kalshi_order_id, min(decided_at) AS hit_at"
        "                   FROM live_order_queue_decisions"
        f"                  WHERE decided_at >= now() - interval '{int(hours)} hours'"
        "                    AND decision = 'queue_cancel' GROUP BY 1),"
        " d AS (SELECT d.kalshi_order_id, d.cap_bound, d.limit_price, d.quantity FROM"
        "       live_order_queue_decisions d JOIN first_hit f"
        "         ON f.kalshi_order_id = d.kalshi_order_id AND f.hit_at = d.decided_at),"
        " contested AS (SELECT market_ticker FROM live_orders GROUP BY 1 HAVING count(DISTINCT strategy) > 1),"
        " p AS (SELECT DISTINCT ON (market_ticker) market_ticker, quantity, realized_pnl"
        "       FROM positions ORDER BY market_ticker, captured_at DESC)"
        " SELECT count(*), count(*) FILTER (WHERE d.cap_bound), count(*) FILTER (WHERE d.cap_bound IS NULL),"
        "        count(*) FILTER (WHERE o.status = 'filled'),"
        "        round(sum(CASE WHEN o.status = 'filled' AND p.quantity = 0"
        "                        AND o.market_ticker NOT IN (SELECT market_ticker FROM contested)"
        "                       THEN p.realized_pnl ELSE 0 END)::numeric, 2)"
        " FROM d JOIN live_orders o ON o.kalshi_order_id = d.kalshi_order_id"
        " LEFT JOIN p ON p.market_ticker = o.market_ticker"
    )
    n, bound, unknown, later, forgone = cur.fetchone()
    if n:
        print(f"  would-cancel orders {n}: cap bound at decision {bound} ({_pct(bound / n)}),"
              f" cap unknown {unknown}, later filled {later} ({_pct(later / n)}),"
              f" forgone ${_num(forgone, 2)} = {_num(100 * float(forgone or 0) / n, 2)} c/would-cancel")
    print("  (These are the numbers the pre-registered gates read, via the canonical")
    print("   evaluator: `xos evaluate-gates` / `xos show mmsell10-queue-aware-cancel`.)")


def report(cur, hours: int) -> None:
    if not _coverage(cur, hours):
        return
    _survival(cur, hours)
    _outcomes(cur, hours)
    _fill_timing(cur, hours)
    _capacity(cur, hours)
    _rule_replay(cur, hours)
    _shadow(cur, hours)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=336, help="lookback window (default 14 days)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, args.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
