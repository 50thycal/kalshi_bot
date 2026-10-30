"""LIMM PROGRAMME OUTCOMES — which incentive programmes pay, net of how their cheap side settles.

Pre-registered in docs/LIMM_PROGRAMME_OUTCOMES.md (2026-10-30, catalog research study 3). Every
constant marked "pre-registered" is frozen there; changing one is a new probe.

One snapshot per market per clock hour from `incentive_market_snapshots`, joined to its own
programme row. A qualifying hour (valid book, both sides meet Target Size, programme active,
liquidity programme, >= 48 h before close) with a cheap side (best bid 1-10c, the cheaper if
both) is priced at the live rule: rest at that side's best bid, $20 at >= 4c else $10, at most
500 contracts. Reward per hour = pool per hour x qty / (field + qty) / 2 (the dashboard
estimator, which matched Kalshi's payouts on 2026-10-16). Hours where that share exceeds 0.5 are
the thin-book regime that inflated the placement model; they are excluded from reward and counted.

Settlement: per market, the side cheap in most qualifying hours, its mean best bid p, and Kalshi's
result. G = 100*[cheap side won] - p cents per contract: what a lone fill held to settlement
earns. Event-clustered intervals; labels per series as pre-registered.

No lookahead: prices and field scores come from the snapshot; the result only scores.
Read-only: DATABASE_URL_RO (read-only transaction) plus public Kalshi REST. Stdlib + psycopg.

Usage: {"type":"script","name":"limm_programme_outcomes","args":[],"id":"rl-s3-run1-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- pre-registered constants ----------------------------------------------------------------
REWARD_SINCE = "2026-10-16T00:00:00+00:00"
SETTLE_SINCE = "2026-09-17T00:00:00+00:00"
MAX_PRICE, MIN_PRICE = 10, 1
SIZE_UP_PRICE, SMALL_DOLLARS, LARGE_DOLLARS, MAX_QTY = 4, 10, 20, 500
STEP_ASIDE_HOURS = 48.0
THIN_SHARE = 0.5
MIN_MARKET_DAYS, MIN_SETTLED, MIN_EVENTS = 20, 100, 30
REWARD_BAR_USD = 0.50
G_FLOOR_CENTS = -1.0
Z = 1.96
LIVE_SERIES_EXTRA = frozenset({"KXYTVIEWSHIGH"})

SNAP_SQL = """
WITH s AS (
  SELECT DISTINCT ON (market_ticker, date_trunc('hour', at))
         market_ticker, date_trunc('hour', at) AS hr, program_row_id, best_yes_bid, best_no_bid,
         est_yes_score_total, est_no_score_total, est_yes_meets_target, est_no_meets_target
    FROM incentive_market_snapshots
   WHERE at >= %(since)s AND book_valid
   ORDER BY market_ticker, date_trunc('hour', at), at DESC
)
SELECT s.market_ticker, s.hr, s.best_yes_bid, s.best_no_bid, s.est_yes_score_total,
       s.est_no_score_total, s.est_yes_meets_target, s.est_no_meets_target,
       p.series_ticker, p.event_ticker, p.incentive_type, p.period_reward_usd, p.start_date,
       p.end_date, p.close_time
  FROM s JOIN incentive_programs p ON p.id = s.program_row_id
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_programme_outcomes.py)


def _aware(x):
    if x is None:
        return None
    if isinstance(x, str):
        try:
            x = dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
        except ValueError:
            return None
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def cheap_side(best_yes_bid, best_no_bid) -> tuple[str, int] | None:
    """('yes'|'no', price) for the side whose best bid is 1-10c, the cheaper if both; yes on ties."""
    cands = [(p, s) for s, p in (("yes", best_yes_bid), ("no", best_no_bid))
             if p is not None and MIN_PRICE <= p <= MAX_PRICE]
    if not cands:
        return None
    p, s = min(cands, key=lambda c: (c[0], c[1] != "yes"))
    return s, int(p)


def live_qty(price: int) -> int:
    dollars = LARGE_DOLLARS if price >= SIZE_UP_PRICE else SMALL_DOLLARS
    return min(MAX_QTY, int(dollars * 100 // price))


def hourly_reward(period_reward_usd, start, end, qty: int, field) -> tuple[float, float] | None:
    """(reward USD for one hour, our share) at the dashboard estimator; None when unreadable."""
    start, end = _aware(start), _aware(end)
    if period_reward_usd is None or start is None or end is None or end <= start:
        return None
    field = float(field or 0.0)
    if field < 0:
        return None
    share = qty / (field + qty) if (field + qty) > 0 else 0.0
    pool_per_hour = float(period_reward_usd) / ((end - start).total_seconds() / 3600.0)
    return pool_per_hour * share / 2.0, share


def qualifies(row: dict) -> bool:
    hr, start, end, close = (_aware(row.get(k)) for k in ("hr", "start", "end", "close"))
    if not (row.get("yes_meets") and row.get("no_meets")):
        return False
    if "liquid" not in (row.get("itype") or "").lower():
        return False
    if hr is None or start is None or end is None or not (start <= hr < end):
        return False
    if close is not None and (close - hr).total_seconds() / 3600.0 < STEP_ASIDE_HOURS:
        return False
    return True


def in_live_universe(series: str) -> bool:
    s = (series or "").upper()
    return "MENTION" in s or s in LIVE_SERIES_EXTRA


def cluster_mean(vals: list[tuple[str, float]]) -> dict | None:
    """Mean with an event-clustered standard error."""
    n = len(vals)
    if not n:
        return None
    mean = sum(v for _, v in vals) / n
    res: dict[str, float] = defaultdict(float)
    for g, v in vals:
        res[g] += v - mean
    k = len(res)
    se = math.sqrt(k / (k - 1) * sum(x * x for x in res.values())) / n if k > 1 else float("nan")
    return {"n": n, "events": k, "mean": mean, "se": se}


def label(market_days: int, reward_md: float | None, g: dict | None) -> str:
    if market_days < MIN_MARKET_DAYS or reward_md is None:
        return "THIN"
    settle_ok = g is not None and g["n"] >= MIN_SETTLED and g["events"] >= MIN_EVENTS
    ucb = None if g is None or math.isnan(g["se"]) else g["mean"] + Z * g["se"]
    pays = reward_md >= REWARD_BAR_USD
    if pays and settle_ok and g["mean"] >= G_FLOOR_CENTS:
        return "ADD-CANDIDATE"
    if pays and settle_ok and ucb is not None and ucb < 0:
        return "COSTLY-REWARD"
    if pays and not settle_ok:
        return "REWARD-ONLY"
    if not pays and settle_ok and ucb is not None and ucb < 0:
        return "AVOID"
    return "NO-CALL"


def label_reward(market_days: int, reward_md: float | None) -> str:
    """v2 (reward-only) label, docs/LIMM_PROGRAMME_OUTCOMES.md §8."""
    if market_days < MIN_MARKET_DAYS or reward_md is None:
        return "THIN"
    return "REWARD-LEAD" if reward_md >= REWARD_BAR_USD else "NO-LEAD"


def market_cheap_side(hours: list[tuple[str, int]]) -> tuple[str, float] | None:
    """Majority cheap side over a market's qualifying hours and that side's mean price."""
    if not hours:
        return None
    side = Counter(s for s, _ in hours).most_common(1)[0][0]
    ps = [p for s, p in hours if s == side]
    return side, sum(ps) / len(ps)


# ---------------------------------------------------------------------------------------------


def _f(x, spec="{:+.2f}"):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-print-md", type=int, default=MIN_MARKET_DAYS)
    ap.add_argument("--reward-since", default=REWARD_SINCE)
    ap.add_argument("--reward-until", default=None)
    ap.add_argument("--reward-only", action="store_true",
                    help="v2 (§8): rank on reward alone; settlement is neither fetched nor used")
    args = ap.parse_args(argv)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url
    from mmsell_selection_audit import _result, fetch_markets

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("LIMM programme outcomes — reward per market-day vs cheap-side settlement "
          "(pre-registered docs/LIMM_PROGRAMME_OUTCOMES.md)\n")
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        cur.execute(SNAP_SQL, {"since": SETTLE_SINCE})
        raw = cur.fetchall()
    reward_since = _aware(args.reward_since)
    reward_until = _aware(args.reward_until) if args.reward_until else None
    print(f"hourly snapshots since {SETTLE_SINCE}: {len(raw)}")

    rew = defaultdict(lambda: {"usd": 0.0, "days": set(), "thin_h": 0, "h": 0, "event": None})
    settle_hours: dict[str, list[tuple[str, int]]] = defaultdict(list)
    meta: dict[str, tuple[str, str]] = {}
    nq = 0
    for (tk, hr, yb, nb, ys, ns, ym, nm, series, event, itype, pool, start, end, close) in raw:
        row = {"hr": hr, "start": start, "end": end, "close": close, "yes_meets": ym,
               "no_meets": nm, "itype": itype}
        if not qualifies(row):
            continue
        cs = cheap_side(yb, nb)
        if cs is None:
            continue
        nq += 1
        series = (series or tk.split("-", 1)[0]).upper()
        event = event or tk.rsplit("-", 1)[0]
        meta[tk] = (series, event)
        settle_hours[tk].append(cs)
        if _aware(hr) < reward_since or (reward_until is not None and _aware(hr) >= reward_until):
            continue
        side, price = cs
        hrw = hourly_reward(pool, start, end, live_qty(price), ys if side == "yes" else ns)
        if hrw is None:
            continue
        r = rew[series]
        r["h"] += 1
        if hrw[1] > THIN_SHARE:
            r["thin_h"] += 1
            continue
        r["usd"] += hrw[0]
        r["days"].add((tk, _aware(hr).date()))
    print(f"qualifying cheap-side hours: {nq}; markets: {len(settle_hours)}")

    markets = {} if args.reward_only else fetch_markets(sorted(settle_hours))
    gvals: dict[str, list[tuple[str, float]]] = defaultdict(list)
    excl = Counter()
    for tk, hours in settle_hours.items():
        res = _result(markets.get(tk) or {})
        if args.reward_only:
            break
        if res not in ("yes", "no"):
            excl["unsettled/void"] += 1
            continue
        side, p = market_cheap_side(hours)
        series, event = meta[tk]
        gvals[series].append((event, (100.0 if res == side else 0.0) - p))
    print(f"settled markets scored: {sum(len(v) for v in gvals.values())}; excluded {dict(excl)}")

    series_all = sorted(set(rew) | set(gvals))
    rows = []
    for s in series_all:
        r = rew.get(s)
        md = len(r["days"]) if r else 0
        rmd = (r["usd"] / md) if r and md else None
        g = cluster_mean(gvals.get(s, []))
        lab = label_reward(md, rmd) if args.reward_only else label(md, rmd, g)
        rows.append((s, md, rmd, r["thin_h"] / r["h"] if r and r["h"] else None, g,
                     lab, in_live_universe(s)))
    rows.sort(key=lambda t: -(t[2] or -1))

    print(f"\n== series (reward window {args.reward_since[:16]} -> "
          f"{(args.reward_until or 'now')[:16]}; settlement since {SETTLE_SINCE[:10]}"
          f"{'; REWARD-ONLY, settlement not used' if args.reward_only else ''})")
    print(f"  {'series':26s} {'live':>4} {'mkt-days':>8} {'$/md':>7} {'thin%':>6} "
          f"{'settled':>7} {'evts':>5} {'G c/ct':>7} {'±se':>5}  label")
    hidden = 0
    for s, md, rmd, thin, g, lab, live in rows:
        if md < args.min_print_md and not (g and g["n"] >= MIN_SETTLED):
            hidden += 1
            continue
        print(f"  {s[:26]:26s} {'yes' if live else '':>4} {md:8d} {_fmt_usd(rmd):>7} "
              f"{_f(thin and 100 * thin, '{:.0f}'):>6} {(g or {}).get('n', 0):7d} "
              f"{(g or {}).get('events', 0):5d} {_f(g and g['mean']):>7} "
              f"{_f(g and g['se'], '{:.2f}'):>5}  {lab}")
    if hidden:
        print(f"  ... {hidden} series below every floor not printed")

    live_rows = [r for r in rows if r[6]]
    live_md = sum(r[1] for r in live_rows)
    live_usd = sum((r[2] or 0) * r[1] for r in live_rows)
    live_rmd = live_usd / live_md if live_md else None
    live_g = cluster_mean([v for s in series_all if in_live_universe(s) for v in gvals.get(s, [])])
    lead = "REWARD-LEAD" if args.reward_only else "ADD-CANDIDATE"
    out_cands = [r for r in rows if not r[6] and r[5] == lead
                 and live_rmd is not None and (r[2] or 0) > live_rmd]
    print("\n== live universe pooled")
    print(f"  reward {_fmt_usd(live_rmd)}/market-day over {live_md} market-days; settlement G "
          f"{_f(live_g and live_g['mean'])}c ± {_f(live_g and live_g['se'], '{:.2f}')} "
          f"({(live_g or {}).get('n', 0)} markets, {(live_g or {}).get('events', 0)} events)")
    s1 = ("FAIL — outside-universe ADD-CANDIDATEs above the live figure: "
          + ", ".join(r[0] for r in out_cands)) if out_cands else "PASS (none)"
    if live_g and not math.isnan(live_g["se"]):
        s2 = ("cheap side OVERPRICED (UCB < 0)" if live_g["mean"] + Z * live_g["se"] < 0
              else "not shown overpriced")
    else:
        s2 = "n/a"
    print("\n== VERDICT (descriptive; authorizes nothing)")
    print(f"  S1 universe is not the bottleneck: {s1}")
    print(f"  S2 live-universe cheap side: {s2}")
    labs = (("REWARD-LEAD", "NO-LEAD") if args.reward_only
            else ("ADD-CANDIDATE", "COSTLY-REWARD", "REWARD-ONLY", "AVOID"))
    for lab in labs:
        names = [r[0] + ("*" if r[6] else "") for r in rows if r[5] == lab]
        print(f"  {lab:14s} {len(names):3d}  {', '.join(names)}")
    print("  (* = in the live universe)")
    return 0


def _fmt_usd(x):
    return "n/a" if x is None else f"${x:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
