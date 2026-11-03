"""LIMM VOLUME CENSUS — what Kalshi's non-liquidity incentive programmes pay, and for what.

Pre-registered in docs/LIMM_VOLUME_CENSUS.md (2026-11-03, study 7). A census: it inventories
and nominates; it authorizes nothing. Read-only: DATABASE_URL_RO. Stdlib + psycopg.

Usage: {"type":"script","name":"limm_volume_census","args":[],"id":"rl-s7-census-<date>"}
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- pre-registered constants ----------------------------------------------------------------
HISTORY_SINCE = "2026-09-01T00:00:00+00:00"
FOLLOW_UP_TOTAL_PER_DAY = 10.0
FOLLOW_UP_SERIES_PER_DAY = 2.0
OVERLAP_DAYS = 30
LIQUIDITY = "liquidity"

TYPE_SQL = """
SELECT coalesce(incentive_type, '?') AS itype,
       count(*) FILTER (WHERE end_date > now() AND disappeared_at IS NULL
                          AND last_seen_at > now() - interval '1 day') AS active,
       count(*) FILTER (WHERE start_date >= %(since)s) AS since_sep,
       coalesce(sum(period_reward_usd) FILTER (WHERE start_date >= %(since)s), 0) AS pool_since,
       min(first_seen_at) AS first_seen, max(last_seen_at) AS last_seen
  FROM incentive_programs WHERE superseded_at IS NULL GROUP BY 1 ORDER BY 1
"""

ACTIVE_SQL = """
SELECT coalesce(incentive_type, '?'), coalesce(series_ticker, split_part(market_ticker,'-',1)),
       market_ticker, period_reward_usd, start_date, end_date, incentive_description,
       (extra_params_json->>'max_reward_per_account')
  FROM incentive_programs
 WHERE superseded_at IS NULL AND disappeared_at IS NULL AND end_date > now()
   AND last_seen_at > now() - interval '1 day' AND coalesce(incentive_type,'') <> 'liquidity'
"""

OVERLAP_SQL = """
SELECT count(DISTINCT t.market_ticker) FROM (
  SELECT market_ticker FROM paper_trades WHERE created_at >= now() - make_interval(days => %(d)s)
  UNION
  SELECT market_ticker FROM live_orders WHERE created_at >= now() - make_interval(days => %(d)s)
) t WHERE t.market_ticker = ANY(%(tickers)s)
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_volume_census.py)


def pool_per_day(pool_usd, start, end) -> float | None:
    if pool_usd is None or start is None or end is None or end <= start:
        return None
    return float(pool_usd) / ((end - start).total_seconds() / 86400.0)


def cap_usd(raw) -> float | None:
    """max_reward_per_account is centi-cents (1/10,000 USD)."""
    try:
        return int(raw) / 10_000.0
    except (TypeError, ValueError):
        return None


def verdict(ever_non_liquidity: int, total_per_day: float, best_series_per_day: float) -> str:
    if ever_non_liquidity == 0:
        return "INSTRUMENT GAP"
    if total_per_day >= FOLLOW_UP_TOTAL_PER_DAY and best_series_per_day >= FOLLOW_UP_SERIES_PER_DAY:
        return "FOLLOW-UP"
    return "NO-LEAD"


# ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("LIMM volume census — non-liquidity incentive programmes "
          "(pre-registered docs/LIMM_VOLUME_CENSUS.md)\n")
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute(TYPE_SQL, {"since": HISTORY_SINCE})
        types_ = cur.fetchall()
        cur.execute(ACTIVE_SQL)
        active = cur.fetchall()
        tickers = sorted({r[2] for r in active})
        overlap = 0
        if tickers:
            cur.execute(OVERLAP_SQL, {"d": OVERLAP_DAYS, "tickers": tickers})
            overlap = int(cur.fetchone()[0] or 0)

    print("== by type (current terms)")
    print(f"  {'type':22s} {'active':>7} {'since 09-01':>11} {'pool since $':>12} "
          f"{'first seen':>11} {'last seen':>11}")
    ever_non_liq = 0
    for itype, act, since, pool, first, last in types_:
        if itype != LIQUIDITY:
            ever_non_liq += int(since or 0) + int(act or 0)
        print(f"  {itype[:22]:22s} {act:7d} {since:11d} {float(pool):12.2f} "
              f"{str(first)[:10]:>11} {str(last)[:10]:>11}")

    by_series = defaultdict(lambda: {"n": 0, "per_day": 0.0, "types": set(), "desc": set(),
                                     "caps": set()})
    for itype, series, _tk, pool, start, end, desc, cap in active:
        s = by_series[series]
        s["n"] += 1
        s["per_day"] += pool_per_day(pool, start, end) or 0.0
        s["types"].add(itype)
        if desc and len(s["desc"]) < 2:
            s["desc"].add(desc[:80])
        c = cap_usd(cap)
        if c is not None:
            s["caps"].add(round(c, 2))
    total = sum(s["per_day"] for s in by_series.values())
    best = max((s["per_day"] for s in by_series.values()), default=0.0)

    print(f"\n== active non-liquidity programmes by series ({len(active)} programmes, "
          f"{len(by_series)} series, ${total:.2f}/day in all)")
    print(f"  {'series':24s} {'progs':>5} {'$/day':>8} {'cap/acct $':>10}  types | description")
    for name, s in sorted(by_series.items(), key=lambda kv: -kv[1]["per_day"])[:25]:
        caps = ",".join(f"{c:g}" for c in sorted(s["caps"])) or "-"
        print(f"  {name[:24]:24s} {s['n']:5d} {s['per_day']:8.2f} {caps:>10}  "
              f"{'+'.join(sorted(s['types']))} | {' / '.join(sorted(s['desc']))}")
    print(f"\n== overlap: {overlap} of {len(tickers)} active non-liquidity markets were entered by "
          f"one of our books in the last {OVERLAP_DAYS} days")

    v = verdict(ever_non_liq, total, best)
    print("\n== VERDICT (a census; a nomination authorizes nothing)")
    print(f"  {v}  (total ${total:.2f}/day; best series ${best:.2f}/day)")
    if overlap:
        print("  NOTE: our books already trade volume-programme markets; they may earn rewards "
              "nothing attributes (supports the reward-attribution candidate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
