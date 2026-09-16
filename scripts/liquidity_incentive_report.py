"""LIQUIDITY INCENTIVE SHADOW — what the shadow market maker has observed, and what it would have earned.

WHY THIS EXISTS
---------------
docs/LIQUIDITY_INCENTIVE_THESIS.md (WS-020). The shadow collector quotes genuine two-sided resting
liquidity on every Kalshi liquidity-incentive market IN SIMULATION — no orders — under three quote
policies x five capital tiers x three fill models. This read prints, in order:

1. COLLECTOR — is the instrument alive and is the tape landing? Nothing below is trustworthy
   until this is healthy.
2. LANDSCAPE (Q1) — programs per day, advertised pool, median reward and Target Size.
3. HEADLINE (Q3/Q7) — "had this run at $X for the window": net per policy x tier, with the three
   fill models SIDE BY SIDE (never summed), and the share of net from the single largest outcome.
4. FILLS (Q4/Q5) — outcome mix, P(both | one), lag between legs, single-leg marks by horizon.
5. RANKING (Q2/Q6) — active programs by estimated net/day under the conservative model, with
   every component printed beside the state.

It is diagnostics and a ranking, not a trading signal: no state here submits anything.

Read-only, stdlib + psycopg:

    {"type": "script", "name": "liquidity_incentive_report"}
    {"type": "script", "name": "liquidity_incentive_report", "args": ["--days", "7", "--policy", "B_reward_efficient", "--tier", "250"]}
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

MODELS = ("optimistic", "conservative", "queue_aware")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _n(x, nd=4) -> str:
    return f"{float(x):.{nd}f}" if x is not None else "n/a"


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def collector(cur, hours: int) -> None:
    print("=" * 96)
    print("1. COLLECTOR")
    print("=" * 96)
    rows = _rows(cur, """
        SELECT kind, COUNT(*) FROM incentive_collector_events
        WHERE at >= now() - make_interval(hours => %s) GROUP BY kind ORDER BY kind""", (hours,))
    last = _rows(cur, "SELECT kind, at FROM incentive_collector_events ORDER BY at DESC LIMIT 1")
    print(f"  last event: {last[0][0]} at {last[0][1]:%Y-%m-%d %H:%M:%S}Z" if last else
          "  NO collector events — LIQUIDITY_INCENTIVE_SHADOW_ENABLED is probably false")
    for kind, n in rows:
        print(f"  {kind:<24}{n:>8}")
    cyc = _rows(cur, """
        SELECT started_at, programs_listed, liquidity_programs, volume_programs, new_terms, changed_terms,
               disappeared, total_period_reward_usd, errors
        FROM incentive_discovery_cycles ORDER BY id DESC LIMIT 1""")
    if cyc:
        (at, listed, liq, vol, new, chg, gone, pool, err) = cyc[0]
        print(f"  last discovery {at:%Y-%m-%d %H:%M}Z: listed={listed} liquidity={liq} volume={vol} "
              f"new={new} changed={chg} gone={gone} pool=${_n(pool, 2)} errors={err}")
    tape = _rows(cur, """
        SELECT (SELECT COUNT(*) FROM incentive_book_events WHERE received_at >= now() - make_interval(hours => %s)),
               (SELECT COUNT(*) FROM incentive_trade_events WHERE received_at >= now() - make_interval(hours => %s)),
               (SELECT COUNT(*) FROM incentive_shadow_quotes WHERE ended_at IS NULL),
               (SELECT COUNT(DISTINCT market_ticker) FROM incentive_market_snapshots
                 WHERE at >= now() - make_interval(hours => %s))""", (hours, hours, hours))
    b, t, open_pairs, mkts = tape[0]
    print(f"  tape ({hours}h): book events={b} trades={t}; open shadow pairs={open_pairs}; markets snapshotted={mkts}")


def landscape(cur, days: int) -> None:
    print()
    print("=" * 96)
    print(f"2. LANDSCAPE — liquidity programs by day (last {days} days)  [Q1]")
    print("=" * 96)
    rows = _rows(cur, """
        WITH d AS (SELECT generate_series((now() - make_interval(days => %s))::date, now()::date, '1 day')::date AS day)
        SELECT d.day,
               COUNT(p.id) AS programs,
               COALESCE(SUM(p.period_reward_usd / NULLIF(EXTRACT(EPOCH FROM (p.end_date - p.start_date)) / 86400.0, 0)), 0),
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY p.period_reward_usd / NULLIF(EXTRACT(EPOCH FROM (p.end_date - p.start_date)) / 86400.0, 0)),
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY p.target_size),
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (p.end_date - p.start_date)) / 3600.0)
        FROM d LEFT JOIN incentive_programs p
          ON p.incentive_type = 'liquidity'
         AND p.first_seen_at::date <= d.day
         AND (p.superseded_at IS NULL OR p.superseded_at::date >= d.day)
         AND (p.disappeared_at IS NULL OR p.disappeared_at::date >= d.day)
        GROUP BY d.day ORDER BY d.day""", (days,))
    print(f"  {'day':<12}{'programs':>10}{'reward/day $':>14}{'median $/day':>14}{'median target':>15}{'median hours':>14}")
    for day, n, pool, med, tgt, hrs in rows:
        print(f"  {day!s:<12}{n:>10}{_n(pool, 2):>14}{_n(med, 2):>14}{_n(tgt, 0):>15}{_n(hrs, 1):>14}")


def headline(cur, days: int) -> None:
    print()
    print("=" * 96)
    print(f"3. HEADLINE — had this run at each tier for the last {days} days  [Q3/Q7]")
    print("   net = est reward + paired P&L + single-leg MTM(5m, at bid) + settlement - fees; models side by side")
    print("=" * 96)
    span = _rows(cur, """
        SELECT EXTRACT(EPOCH FROM (now() - MIN(placed_at))) / 86400.0 FROM incentive_shadow_outcomes
        WHERE ended_at >= now() - make_interval(days => %s)""", (days,))[0][0]
    print(f"  observation span: {_n(span, 2)} days")
    rows = _rows(cur, """
        SELECT policy, capital_tier_usd, fill_model, COUNT(*), COUNT(DISTINCT market_ticker),
               SUM(est_reward_usd), SUM(paired_pnl_usd), SUM(fees_usd), SUM(single_leg_mtm_5m_usd),
               SUM(settlement_pnl_usd), SUM(net_before_settlement_usd), SUM(capital_hours),
               MAX(net_before_settlement_usd)
        FROM incentive_shadow_outcomes WHERE ended_at >= now() - make_interval(days => %s)
        GROUP BY policy, capital_tier_usd, fill_model
        ORDER BY policy, capital_tier_usd, fill_model""", (days,))
    print(f"  {'policy':<20}{'tier':>6}{'model':<14}{'n':>6}{'progs':>6}{'reward':>10}{'paired':>10}{'fees':>8}"
          f"{'sl_mtm':>10}{'settle':>10}{'net':>10}{'net/day':>10}{'net/cap-h':>12}{'largest%':>10}")
    for (pol, tier, model, n, progs, rew, paired, fees, mtm, settle, net, cap_h, best) in rows:
        per_day = float(net or 0) / float(span) if span else None
        per_cap = float(net or 0) / float(cap_h) if cap_h else None
        largest = (float(best) / float(net) * 100) if net and float(net) > 0 and best else None
        print(f"  {pol:<20}{tier:>6}  {model:<12}{n:>6}{progs:>6}{_n(rew):>10}{_n(paired):>10}{_n(fees):>8}"
              f"{_n(mtm):>10}{_n(settle):>10}{_n(net):>10}{_n(per_day):>10}{_n(per_cap, 6):>12}{_n(largest, 1):>10}")


def fills(cur, days: int) -> None:
    print()
    print("=" * 96)
    print(f"4. FILLS — outcome mix, P(both | one), lag, single-leg marks (last {days} days)  [Q4/Q5]")
    print("=" * 96)
    for model in MODELS:
        mix = _rows(cur, """
            SELECT outcome, COUNT(*) FROM incentive_shadow_outcomes
            WHERE fill_model = %s AND ended_at >= now() - make_interval(days => %s)
            GROUP BY outcome ORDER BY outcome""", (model, days))
        counts = dict(mix)
        both = counts.get("both_filled", 0)
        one = sum(counts.get(k, 0) for k in ("both_filled", "yes_only", "no_only", "partial_both", "partial_yes", "partial_no"))
        lag = _rows(cur, """
            SELECT AVG(seconds_between_legs), PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY seconds_between_legs)
            FROM incentive_shadow_outcomes
            WHERE fill_model = %s AND ended_at >= now() - make_interval(days => %s) AND seconds_between_legs IS NOT NULL""",
                    (model, days))[0]
        print(f"  {model:<13} mix: " + ", ".join(f"{k}={v}" for k, v in mix) +
              f" | P(both|one)={_n(both / one if one else None, 3)} n={one} | lag mean={_n(lag[0], 1)}s median={_n(lag[1], 1)}s")
    marks = _rows(cur, """
        SELECT fill_model, horizon_seconds, COUNT(*), AVG(pnl_at_bid_usd), MIN(pnl_at_bid_usd),
               AVG(pnl_at_mid_usd)
        FROM incentive_shadow_marks WHERE at >= now() - make_interval(days => %s)
        GROUP BY fill_model, horizon_seconds ORDER BY fill_model, horizon_seconds""", (days,))
    print(f"  {'model':<14}{'horizon s':>10}{'n':>7}{'mean@bid $':>12}{'worst@bid $':>12}{'mean@mid $':>12}")
    for (model, h, n, avg, worst, avg_mid) in marks:
        print(f"  {model:<14}{h:>10}{n:>7}{_n(avg):>12}{_n(worst):>12}{_n(avg_mid):>12}")
    depth = _rows(cur, """
        SELECT CASE WHEN LEAST(s.yes_depth_total, s.no_depth_total) < p.target_size THEN 'under_target'
                    WHEN LEAST(s.yes_depth_total, s.no_depth_total) < 3 * p.target_size THEN 'medium'
                    ELSE 'deep' END AS bucket,
               COUNT(*), AVG(o.single_leg_mtm_5m_usd), AVG(o.est_reward_usd)
        FROM incentive_shadow_outcomes o
        JOIN incentive_programs p ON p.id = o.program_row_id
        JOIN LATERAL (SELECT yes_depth_total, no_depth_total FROM incentive_market_snapshots ms
                      WHERE ms.market_ticker = o.market_ticker AND ms.at <= o.placed_at
                      ORDER BY ms.at DESC LIMIT 1) s ON TRUE
        WHERE o.fill_model = 'conservative' AND o.single_leg_side IS NOT NULL
          AND o.ended_at >= now() - make_interval(days => %s)
        GROUP BY 1 ORDER BY 1""", (days,))
    print("  single-leg outcomes by competing depth at placement (conservative)  [Q6]")
    for bucket, n, mtm, rew in depth:
        print(f"    {bucket:<14} n={n:<6} mean single-leg MTM={_n(mtm)}  mean est reward={_n(rew)}")


def ranking(cur, policy: str, tier: int, hours: int) -> None:
    print()
    print("=" * 96)
    print(f"5. RANKING — active programs, policy={policy} tier=${tier}, conservative outcomes over {hours}h  [Q2/Q6]")
    print("   read-only. Components printed beside the state; no state submits anything.")
    print("=" * 96)
    rows = _rows(cur, """
        WITH cur AS (
          SELECT p.* FROM incentive_programs p
          WHERE p.superseded_at IS NULL AND p.disappeared_at IS NULL AND p.incentive_type = 'liquidity'
            AND (p.end_date IS NULL OR p.end_date > now())),
        snap AS (
          SELECT DISTINCT ON (market_ticker) market_ticker, best_yes_bid, best_no_bid, yes_depth_total, no_depth_total,
                 est_yes_meets_target, est_no_meets_target, est_reference_price
          FROM incentive_market_snapshots ORDER BY market_ticker, id DESC),
        q AS (
          SELECT DISTINCT ON (market_ticker) market_ticker, yes_bid, no_bid, qty_per_side, capital_required_usd,
                 pair_edge_cents, est_yes_share, est_no_share, est_reward_per_hour_usd
          FROM incentive_shadow_quotes WHERE policy = %s AND capital_tier_usd = %s
          ORDER BY market_ticker, id DESC),
        o AS (
          SELECT market_ticker, COUNT(*) AS n, SUM(rest_seconds) / 86400.0 AS rest_days,
                 SUM(est_reward_usd) AS rew, SUM(paired_pnl_usd) AS paired, SUM(fees_usd) AS fees,
                 SUM(COALESCE(single_leg_mtm_5m_usd, 0)) + SUM(COALESCE(settlement_pnl_usd, 0)) AS single_leg
          FROM incentive_shadow_outcomes
          WHERE fill_model = 'conservative' AND policy = %s AND capital_tier_usd = %s
            AND ended_at >= now() - make_interval(hours => %s)
          GROUP BY market_ticker)
        SELECT c.market_ticker, c.period_reward_usd / NULLIF(EXTRACT(EPOCH FROM (c.end_date - c.start_date)) / 86400.0, 0),
               c.target_size, c.discount_factor_bps, s.best_yes_bid, s.best_no_bid, s.yes_depth_total, s.no_depth_total,
               s.est_yes_meets_target AND s.est_no_meets_target,
               q.yes_bid, q.no_bid, q.qty_per_side, q.capital_required_usd, q.pair_edge_cents,
               (COALESCE(q.est_yes_share, 0) + COALESCE(q.est_no_share, 0)) / 2.0, q.est_reward_per_hour_usd,
               o.n, o.rest_days, o.rew, o.paired, o.fees, o.single_leg
        FROM cur c LEFT JOIN snap s ON s.market_ticker = c.market_ticker
        LEFT JOIN q ON q.market_ticker = c.market_ticker
        LEFT JOIN o ON o.market_ticker = c.market_ticker
        ORDER BY c.period_reward_usd DESC NULLS LAST""", (policy, tier, policy, tier, hours))
    out = []
    for r in rows:
        (t, rpd, tgt, disc, yb, nb, yd, nd, both, qy, qn, qty, cap, edge, share, rph, n, rest_days, rew, paired, fees, sl) = r
        rest_days = float(rest_days) if rest_days else 0.0
        if rest_days > 0:
            reward_day = float(rew or 0) / rest_days
            paired_day = float(paired or 0) / rest_days
            fees_day = float(fees or 0) / rest_days
            sl_cost_day = max(0.0, -float(sl or 0) / rest_days)
        else:
            reward_day = float(rph or 0) * 24
            paired_day = fees_day = sl_cost_day = 0.0
        cap_f = float(cap or 0)
        cap_cost = cap_f * 0.0375 / 365.0
        net = reward_day + paired_day - sl_cost_day - fees_day - cap_cost
        if cap_f <= 0:
            state = "IGNORE"
        elif reward_day < 0.05 or net <= 0:
            state = "IGNORE"
        elif not both:
            state = "WATCH(target)"
        elif float(share or 0) < 0.02:
            state = "WATCH(share)"
        elif sl_cost_day > reward_day:
            state = "WATCH(adverse)"
        elif net >= 0.25 and (n or 0) >= 50:
            state = "POC_CANDIDATE"
        else:
            state = "SHADOW"
        out.append((net, t, rpd, tgt, disc, yb, nb, yd, nd, both, qy, qn, qty, cap_f, edge, share, reward_day,
                    paired_day, sl_cost_day, fees_day, n or 0, state))
    out.sort(key=lambda z: -z[0])
    print(f"  {'ticker':<34}{'rwd/d':>7}{'target':>7}{'disc':>6}{'ybid':>5}{'nbid':>5}{'ydep':>7}{'ndep':>7}{'both':>5}"
          f"{'quote':>12}{'cap$':>8}{'edge':>6}{'share':>7}{'rew/d':>8}{'pair/d':>8}{'sl/d':>8}{'fee/d':>7}{'net/d':>8}{'n':>5}  state")
    for (net, t, rpd, tgt, disc, yb, nb, yd, nd, both, qy, qn, qty, cap_f, edge, share, rd, pd, sl, fd, n, state) in out[:80]:
        quote = f"{qy}/{qn}x{int(qty)}" if qy is not None else "-"
        print(f"  {t[:34]:<34}{_n(rpd, 2):>7}{_n(tgt, 0):>7}{disc if disc is not None else '-':>6}{yb if yb is not None else '-':>5}"
              f"{nb if nb is not None else '-':>5}{_n(yd, 0):>7}{_n(nd, 0):>7}{('Y' if both else 'n') if both is not None else '-':>5}"
              f"{quote:>12}{_n(cap_f, 2):>8}{_n(edge, 1):>6}{_n(share, 3):>7}{_n(rd):>8}{_n(pd):>8}{_n(sl):>8}{_n(fd):>7}{_n(net):>8}{n:>5}  {state}")
    if len(out) > 80:
        print(f"  ... {len(out) - 80} more")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14, help="history / headline window (default 14)")
    ap.add_argument("--hours", type=int, default=72, help="ranking window (default 72)")
    ap.add_argument("--policy", default="A_break_even")
    ap.add_argument("--tier", type=int, default=100)
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            collector(cur, args.hours)
            landscape(cur, args.days)
            headline(cur, args.days)
            fills(cur, args.days)
            ranking(cur, args.policy, args.tier, args.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
