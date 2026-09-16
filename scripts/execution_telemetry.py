"""EXECUTION TELEMETRY — is the queue/fill record complete, and what does one order look like?

WHY THIS EXISTS
---------------
docs/MMSELL_QUEUE_FILL_TELEMETRY.md (WS-019). Every live MMSELL resting order is meant to be one
traceable observation: decision context, queue samples on a fine cadence, the order book and
public trades around it, our fill with the exchange timestamp, and what the market did after.
This read answers, in order:

1. COVERAGE — is the record actually landing? Nothing below is trustworthy until this is high.
   A collector that silently stopped (disconnected, throttled, rate-limited, thread dead) is
   the failure this section exists to expose.
2. FUNNEL — orders placed -> resting -> partial / full fill / cancel / still open.
3. QUEUE AT REST — the distribution of contracts ahead at the first sample, by book.
4. FILL RECONCILIATION — WebSocket fills vs REST fills, both directions.
5. `--order <kalshi_order_id>` — one order's whole trace: context, ticks, fills, trades at our
   price, order-status events, lifecycle events.

It is diagnostics, not a model. No fill probability or EV appears here on purpose.

Read-only, stdlib + psycopg:

    {"type": "script", "name": "execution_telemetry"}
    {"type": "script", "name": "execution_telemetry", "args": ["--hours", "24"]}
    {"type": "script", "name": "execution_telemetry", "args": ["--order", "<kalshi_order_id>"]}
"""

from __future__ import annotations

import argparse
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


def _pct(num, den) -> str:
    return f"{num / den * 100:.1f}%" if den else "n/a"


def _n(x, nd=1) -> str:
    return f"{float(x):.{nd}f}" if x is not None else "n/a"


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


# ---------------------------------------------------------------- sections


def coverage(cur, hours: int) -> bool:
    w = f"now() - interval '{int(hours)} hours'"
    print(f"=== COVERAGE (last {hours}h) — read this first ===")
    started, stopped, disabled = _one(cur, (
        "SELECT count(*) FILTER (WHERE kind='thread_started'),"
        "       count(*) FILTER (WHERE kind='thread_stopped'),"
        "       count(*) FILTER (WHERE kind='disabled')"
        f" FROM execution_collector_events WHERE at >= {w}"))
    last = _one(cur, "SELECT kind, at::text FROM execution_collector_events"
                     " ORDER BY at DESC LIMIT 1")
    if not started and not last:
        print("  no collector events at all in the window.")
        print("  Expected while: EXECUTION_TELEMETRY_ENABLED=false, or the worker has not")
        print("  redeployed since the merge. If the live worker IS running with the flag on,")
        print("  this is a FAULT — check the worker log for 'execution telemetry'.")
        return False
    print(f"  thread started {started}  stopped {stopped}  disabled {disabled}"
          f"   last event: {last[0]} at {last[1]}" if last else "")
    conn, disc, gaps, snaps, thr, pf, rl, unp, loop = _one(cur, (
        "SELECT count(*) FILTER (WHERE kind='connected'),"
        "       count(*) FILTER (WHERE kind='disconnected'),"
        "       count(*) FILTER (WHERE kind='seq_gap'),"
        "       count(*) FILTER (WHERE kind='snapshot_requested'),"
        "       count(*) FILTER (WHERE kind='throttled'),"
        "       count(*) FILTER (WHERE kind='poll_failed'),"
        "       count(*) FILTER (WHERE kind='rate_limited'),"
        "       count(*) FILTER (WHERE kind='unparsed'),"
        "       count(*) FILTER (WHERE kind='loop_error')"
        f" FROM execution_collector_events WHERE at >= {w}"))
    print(f"  connections {conn}  disconnects {disc}  seq gaps {gaps} (snapshots re-requested"
          f" {snaps})  throttled {thr}  poll failures {pf}  429s {rl}  unparsed {unp}"
          f"  loop errors {loop}")
    if disc and conn and disc >= conn:
        print("  !! as many disconnects as connects — the stream is not staying up.")
    if rl:
        print("  !! rate-limited polls: lower EXECUTION_QUEUE_MAX_POLLS_PER_MINUTE or raise the")
        print("     interval. Trading shares this budget.")

    orders, with_ctx, with_rest, with_any, with_term = _one(cur, (
        "SELECT count(*),"
        "       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM execution_order_context c"
        "                                      WHERE c.live_order_id = o.id)),"
        "       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM live_order_queue_ticks t"
        "                                      WHERE t.live_order_id = o.id"
        "                                        AND t.trigger = 'at_rest')),"
        "       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM live_order_queue_ticks t"
        "                                      WHERE t.live_order_id = o.id)),"
        "       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM live_order_queue_ticks t"
        "                                      WHERE t.live_order_id = o.id"
        "                                        AND t.trigger = 'terminal'))"
        " FROM live_orders o"
        f" WHERE o.created_at >= {w} AND o.kalshi_order_id IS NOT NULL"))
    print(f"\n  live orders with a Kalshi id: {orders}")
    print(f"    decision context row      {with_ctx:>6}  ({_pct(with_ctx, orders)})")
    print(f"    queue tick at rest        {with_rest:>6}  ({_pct(with_rest, orders)})")
    print(f"    any queue tick            {with_any:>6}  ({_pct(with_any, orders)})")
    print(f"    terminal tick attempt     {with_term:>6}  ({_pct(with_term, orders)})")
    ticks, readable, med = _one(cur, (
        "SELECT count(*), count(*) FILTER (WHERE contracts_ahead IS NOT NULL),"
        "       percentile_disc(0.5) WITHIN GROUP (ORDER BY n)"
        " FROM (SELECT t.contracts_ahead, count(*) OVER (PARTITION BY t.kalshi_order_id) AS n"
        f"       FROM live_order_queue_ticks t WHERE t.captured_at >= {w}) x"))
    print(f"  queue ticks {ticks}  readable {_pct(readable, ticks)}  median ticks/order {med}")
    cur.execute("SELECT coalesce(trigger,'(none)'), count(*) FROM live_order_queue_ticks"
                f" WHERE captured_at >= {w} GROUP BY 1 ORDER BY 2 DESC")
    print("  by trigger: " + ", ".join(f"{k} {v}" for k, v in cur.fetchall()))
    be, bm, snapshots = _one(cur, (
        "SELECT count(*), count(DISTINCT market_ticker), count(*) FILTER (WHERE kind='snapshot')"
        f" FROM execution_book_events WHERE received_at >= {w}"))
    te, tm = _one(cur, "SELECT count(*), count(DISTINCT market_ticker)"
                       f" FROM execution_trade_events WHERE received_at >= {w}")
    fe, oe, me = _one(cur, (
        f"SELECT (SELECT count(*) FROM execution_fill_events WHERE received_at >= {w}),"
        f"       (SELECT count(*) FROM execution_order_events WHERE received_at >= {w}),"
        f"       (SELECT count(*) FROM execution_market_events WHERE received_at >= {w})"))
    print(f"  book events {be} ({snapshots} snapshots) over {bm} markets;"
          f" trades {te} over {tm} markets; WS fills {fe}; order events {oe};"
          f" lifecycle {me}")
    # Orders placed BEFORE the collector's first start in the window cannot have a context row
    # (the hook did not exist when they were sent); only orders after it can indict the hook.
    since_start = _one(cur, (
        "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM execution_order_context c"
        "                                                WHERE c.live_order_id = o.id))"
        " FROM live_orders o"
        " WHERE o.kalshi_order_id IS NOT NULL"
        "   AND o.created_at >= (SELECT min(at) FROM execution_collector_events"
        f"                       WHERE kind = 'thread_started' AND at >= {w})"))
    after_n, after_ctx = (since_start or (0, 0))
    print(f"  orders placed since the collector first started: {after_n}"
          f"  with context row {after_ctx} ({_pct(after_ctx, after_n)})")
    if after_n and not after_ctx:
        print("  !! orders placed after the collector started have no decision context rows —")
        print("     the executor hook is not writing. Check the worker log for 'decision context'.")
    elif orders and not with_ctx:
        print("  (no context rows yet: every order in the window predates the collector's start)")
    if orders and not with_any:
        print("  !! orders exist but no queue ticks — the collector's tracked set is empty,")
        print("     or the thread is not running.")
    return bool(orders)


def funnel(cur, hours: int) -> None:
    w = f"now() - interval '{int(hours)} hours'"
    print(f"\n=== FUNNEL (orders created in the last {hours}h) ===")
    cur.execute(
        "SELECT o.strategy, count(*),"
        "       count(*) FILTER (WHERE o.status='filled'),"
        "       count(*) FILTER (WHERE o.status='canceled'),"
        "       count(*) FILTER (WHERE o.status IN ('resting','partial','submitted')),"
        "       count(*) FILTER (WHERE o.status IN ('rejected','error','not_landed')),"
        "       count(*) FILTER (WHERE (SELECT count(*) FROM execution_fill_events f"
        "                               WHERE f.kalshi_order_id = o.kalshi_order_id) > 1)"
        f" FROM live_orders o WHERE o.created_at >= {w}"
        " GROUP BY 1 ORDER BY 1")
    rows = cur.fetchall()
    if not rows:
        print("  (no orders)")
        return
    print(f"  {'book':12s} {'placed':>7} {'filled':>7} {'cancel':>7} {'open':>6}"
          f" {'refused':>8} {'multi-fill':>10}")
    for strat, n, filled, canceled, opn, refused, multi in rows:
        print(f"  {str(strat)[:12]:12s} {n:>7} {filled:>7} {canceled:>7} {opn:>6}"
              f" {refused:>8} {multi:>10}")
    print("  (`refused` = never reached the book: rejected/error/not_landed. Never pooled with")
    print("   `cancel` — an operational refusal and an execution miss are different facts.)")


def queue_at_rest(cur, hours: int) -> None:
    w = f"now() - interval '{int(hours)} hours'"
    print(f"\n=== QUEUE AT REST — contracts ahead at the first sample (last {hours}h) ===")
    cur.execute(
        "WITH first AS ("
        "  SELECT DISTINCT ON (kalshi_order_id) kalshi_order_id, strategy, contracts_ahead"
        f"  FROM live_order_queue_ticks WHERE captured_at >= {w} AND contracts_ahead IS NOT NULL"
        "  ORDER BY kalshi_order_id, captured_at)"
        " SELECT strategy, count(*),"
        "        percentile_disc(0.25) WITHIN GROUP (ORDER BY contracts_ahead),"
        "        percentile_disc(0.5) WITHIN GROUP (ORDER BY contracts_ahead),"
        "        percentile_disc(0.75) WITHIN GROUP (ORDER BY contracts_ahead),"
        "        percentile_disc(0.9) WITHIN GROUP (ORDER BY contracts_ahead),"
        "        count(*) FILTER (WHERE contracts_ahead = 0)"
        " FROM first GROUP BY 1 ORDER BY 1")
    rows = cur.fetchall()
    if not rows:
        print("  (no readable first samples yet)")
        return
    print(f"  {'book':12s} {'orders':>7} {'p25':>7} {'p50':>7} {'p75':>7} {'p90':>7} {'front':>7}")
    for strat, n, p25, p50, p75, p90, front in rows:
        print(f"  {str(strat)[:12]:12s} {n:>7} {_n(p25,0):>7} {_n(p50,0):>7} {_n(p75,0):>7}"
              f" {_n(p90,0):>7} {_pct(front, n):>7}")
    print("  (Continuous distribution on purpose — no buckets are frozen here. Phase 2 fits")
    print("   the curves; this only shows the data is arriving and where the mass sits.)")


def fill_reconciliation(cur, hours: int) -> None:
    w = f"now() - interval '{int(hours)} hours'"
    print(f"\n=== FILL RECONCILIATION — WebSocket vs REST (last {hours}h) ===")
    ws, ws_matched = _one(cur, (
        "SELECT count(*), count(*) FILTER (WHERE rest_fill_id IS NOT NULL)"
        f" FROM execution_fill_events WHERE received_at >= {w}"))
    rest, rest_with_ws = _one(cur, (
        "SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM execution_fill_events e"
        "                                                WHERE e.trade_id = f.kalshi_fill_id))"
        f" FROM fills f WHERE f.filled_at >= {w}"))
    print(f"  WS fills {ws}, matched to REST {ws_matched} ({_pct(ws_matched, ws)})")
    print(f"  REST fills {rest}, with a WS event {rest_with_ws} ({_pct(rest_with_ws, rest)})")
    lag = _one(cur, (
        "SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY d),"
        "       percentile_disc(0.9) WITHIN GROUP (ORDER BY d)"
        " FROM (SELECT extract(epoch FROM (f.filled_at - to_timestamp(e.ts_ms/1000.0))) AS d"
        "       FROM execution_fill_events e JOIN fills f ON f.id = e.rest_fill_id"
        f"       WHERE e.received_at >= {w}) x"))
    if lag and lag[0] is not None:
        print(f"  REST `filled_at` lags the exchange fill by p50 {_n(lag[0],0)}s /"
              f" p90 {_n(lag[1],0)}s — which is why the WS stream exists.")
    if ws and ws_matched < ws:
        print("  (unmatched WS fills are normal for up to one reconcile cycle; persistent ones")
        print("   mean the REST poll and the stream disagree — inspect by trade_id)")


def order_trace(cur, koid: str) -> None:
    print(f"=== ORDER {koid} ===")
    row = _one(cur, (
        "SELECT o.id, o.strategy, o.market_ticker, o.event_ticker, o.side, o.limit_price,"
        "       o.quantity, o.status, o.cancel_reason, o.created_at::text"
        " FROM live_orders o WHERE o.kalshi_order_id = %s"), (koid,))
    if row is None:
        print("  not found in live_orders")
        return
    oid, strat, ticker, event, side, px, qty, status, reason, created = row
    print(f"  {strat} {ticker} ({event}) {side} @ {px}c x {qty}  status={status}"
          f" reason={reason}  created {created}")
    ctx = _one(cur, (
        "SELECT decided_at::text, acked_at::text, ack_ts_ms, cancel_requested_at::text,"
        "       cancel_confirmed_at::text, terminal_reason, twin_tag, candidate_mid,"
        "       best_yes_bid, best_yes_ask, best_no_bid, spread, depth_at_best_ask,"
        "       hours_to_close, band_lo, band_hi, max_yes, review_tier, regime, market_type,"
        "       market_mode, open_positions_for_tag, open_position_cap, hot_entry, offset_cents"
        " FROM execution_order_context WHERE live_order_id = %s ORDER BY id DESC LIMIT 1"),
        (oid,))
    print("\n  DECISION CONTEXT")
    if ctx is None:
        print("    (none — order predates the telemetry deploy, or the hook failed)")
    else:
        (dec, ack, ack_ms, creq, cconf, term, twin, mid, ybid, yask, nbid, spread, dask, htc,
         lo, hi, maxyes, tier, regime, mtype, mode, opn, cap, hot, off) = ctx
        print(f"    decided {dec}  acked {ack} (exchange ts_ms {ack_ms})")
        print(f"    cancel requested {creq}  confirmed {cconf}  terminal reason {term}")
        print(f"    twin {twin}  mid {_n(mid)}  yes {ybid}/{yask}  no-bid {nbid}  spread {spread}"
              f"  depth@ask {dask}")
        print(f"    htc {_n(htc,2)}  band {lo}-{hi} maxyes {maxyes}  tier {tier}  regime {regime}"
              f"  type {mtype}/{mode}  open {opn}/{cap}  hot {hot}  offset {off}")
    print("\n  QUEUE SAMPLES (captured_at, trigger, contracts_ahead, remaining, qty@our px,"
          " better, trades since)")
    cur.execute(
        "SELECT captured_at::text, trigger, contracts_ahead, remaining_count,"
        "       features_json->>'qty_at_our_price', features_json->>'qty_better',"
        "       features_json->>'trades_since_placement', features_json->>'book_valid'"
        " FROM live_order_queue_ticks WHERE kalshi_order_id = %s ORDER BY captured_at",
        (koid,))
    ticks = cur.fetchall()
    if not ticks:
        print("    (no samples)")
    for t in ticks[:200]:
        print(f"    {t[0]}  {str(t[1] or ''):12s} ahead={t[2] if t[2] is not None else 'NULL':>6}"
              f" rem={t[3] if t[3] is not None else '?':>5} at_px={t[4] or '?':>7}"
              f" better={t[5] or '?':>7} trades={t[6] or '?':>4} book={t[7] or '?'}")
    if len(ticks) > 200:
        print(f"    ... {len(ticks) - 200} more")
    print("\n  FILLS (WS)")
    cur.execute(
        "SELECT to_timestamp(ts_ms/1000.0)::text, count_fp, yes_price_cents, fee_cost, is_taker,"
        "       rest_fill_id FROM execution_fill_events WHERE kalshi_order_id = %s ORDER BY ts_ms",
        (koid,))
    fills = cur.fetchall()
    if not fills:
        print("    (none)")
    for f in fills:
        print(f"    {f[0]}  count {f[1]}  yes {f[2]}c  fee {f[3]}  taker={f[4]}"
              f"  rest_fill_id={f[5] if f[5] is not None else 'UNMATCHED'}")
    print("\n  ORDER STATUS EVENTS (WS)")
    cur.execute(
        "SELECT received_at::text, status, fill_count_fp, remaining_count_fp"
        " FROM execution_order_events WHERE kalshi_order_id = %s ORDER BY received_at", (koid,))
    for e in cur.fetchall():
        print(f"    {e[0]}  {e[1]}  filled {e[2]}  remaining {e[3]}")
    our_yes = (100 - px) if side == "no" and px is not None else px
    print(f"\n  PUBLIC TRADES AT OUR PRICE (yes {our_yes}c) while tracked")
    cur.execute(
        "SELECT to_timestamp(ts_ms/1000.0)::text, count_fp, taker_outcome_side, is_block_trade"
        " FROM execution_trade_events WHERE market_ticker = %s AND yes_price_cents = %s"
        "   AND received_at >= (SELECT created_at FROM live_orders WHERE id = %s)"
        " ORDER BY ts_ms LIMIT 100", (ticker, our_yes, oid))
    trades = cur.fetchall()
    if not trades:
        print("    (none)")
    for t in trades:
        print(f"    {t[0]}  {t[1]} taker={t[2]} block={t[3]}")
    be = _one(cur, "SELECT count(*), min(received_at)::text, max(received_at)::text"
                   " FROM execution_book_events WHERE market_ticker = %s", (ticker,))
    print(f"\n  BOOK EVENTS for {ticker}: {be[0]} rows {be[1]} -> {be[2]}")
    cur.execute("SELECT received_at::text, event_type, is_deactivated FROM execution_market_events"
                " WHERE market_ticker = %s ORDER BY received_at", (ticker,))
    life = cur.fetchall()
    if life:
        print("  LIFECYCLE: " + "; ".join(f"{r[0]} {r[1]} deact={r[2]}" for r in life))


def report(cur, hours: int, order: str | None) -> None:
    if order:
        order_trace(cur, order)
        return
    if not coverage(cur, hours):
        return
    funnel(cur, hours)
    queue_at_rest(cur, hours)
    fill_reconciliation(cur, hours)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=72, help="lookback window (default 3 days)")
    ap.add_argument("--order", default=None, help="one Kalshi order id: print its full trace")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            report(cur, args.hours, args.order)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
