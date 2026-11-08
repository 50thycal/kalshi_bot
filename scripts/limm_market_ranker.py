"""LIMM MARKET RANKER — which live incentive markets pay the most per $10 bid right now?

A read-only screen (owner request 2026-11-08: "what are the best ones right now, and how do we
measure which ones are best?"). It is a SCREEN, not a pre-registered verdict: it ranks, it never
decides. Choosing markets for the live book stays an owner decision (a universe change is a new
epoch). Method and limits: docs/LIMM_MARKET_RANKER.md.

Measure (per market, per side): estimated reward per day for a $10 bid resting AT the side's best
bid, using the published LIP scoring (reward is paid per contract; scoring.py R4-R6) and the
study 3 estimator: pool per day x our share / 2, share = qty / (field score + qty).

Read-only: DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"limm_market_ranker","args":[],"id":"rl-rank-<date>"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEG_USD_CENTS = 1000            # a $10 bid, the live book's size (§9.72)
MAX_QTY = 1000                  # 1c x 1000 = $10
FRESH_HOURS = 3                 # a market's latest snapshot must be this recent
STEP_ASIDE_HOURS = 48.0         # the live book's entry step-aside before close (§9.63)
TOP_N = 25
HISTORY_DAYS = 60               # repricing clock and activity look-back
JUMP_CENTS = 8                  # an hourly mid move this large is a "jump" (a repricing)
MAX_GAP_HOURS = 2               # consecutive snapshots further apart than this are not compared
CLOCK_SHARE = 0.4               # a clock is "clear" when this share of jumps share one UTC hour
MIN_JUMPS_FOR_CLOCK = 3
RISK_JUMPS_HIGH, RISK_JUMPS_MED = 4.0, 1.5      # jumps per 30 market-days
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

NOW_SQL = """
SELECT DISTINCT ON (s.market_ticker)
       s.market_ticker, s.at, s.best_yes_bid, s.best_no_bid, s.est_yes_score_total,
       s.est_no_score_total, s.est_yes_meets_target, s.est_no_meets_target,
       p.series_ticker, p.event_ticker, p.period_reward_usd, p.start_date, p.end_date,
       p.close_time, p.target_size, p.market_title, p.discount_factor_bps
  FROM incentive_market_snapshots s
  JOIN incentive_programs p ON p.id = s.program_row_id
 WHERE s.at > %(fresh)s AND s.book_valid
   AND coalesce(p.incentive_type, '') ILIKE '%%liquid%%'
   AND p.end_date > %(now)s
 ORDER BY s.market_ticker, s.at DESC
"""


HIST_SQL = """
SELECT DISTINCT ON (market_ticker, date_trunc('hour', at))
       market_ticker, date_trunc('hour', at) AS hr, best_yes_bid, best_no_bid
  FROM incentive_market_snapshots
 WHERE market_ticker = ANY(%(tickers)s) AND at >= %(since)s AND at < %(now)s AND book_valid
 ORDER BY market_ticker, date_trunc('hour', at), at DESC
"""

VOL_SQL = """
SELECT market_ticker, date_trunc('day', received_at) AS d, sum(count_fp) AS contracts, count(*) AS prints
  FROM incentive_trade_events
 WHERE market_ticker = ANY(%(tickers)s) AND received_at >= %(since)s AND received_at < %(now)s
 GROUP BY 1, 2
"""


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_limm_market_ranker.py)


def _aware(x):
    if x is None:
        return None
    if isinstance(x, str):
        x = dt.datetime.fromisoformat(x)
    return x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)


def pool_per_day(period_reward_usd, start, end) -> float | None:
    start, end = _aware(start), _aware(end)
    if period_reward_usd is None or start is None or end is None or end <= start:
        return None
    return float(period_reward_usd) / ((end - start).total_seconds() / 86400.0)


def leg_qty(price: int) -> int:
    return min(MAX_QTY, int(LEG_USD_CENTS // int(price)))


def side_reward_day(pool_day: float | None, price, field) -> tuple[float, float] | None:
    """(USD per day, our share) for a $10 bid at `price` on a side whose field score is
    `field`. None when the side has no bid or the pool is unreadable."""
    if pool_day is None or price is None or int(price) < 1 or field is None or field < 0:
        return None
    q = leg_qty(int(price))
    share = q / (float(field) + q)
    return pool_day * share / 2.0, share


def discount(bps) -> float:
    """Per-tick multiplier below the reference price (scoring.py R4); missing reads as 1.0."""
    if bps is None:
        return 1.0
    return max(0.0, min(1.0, int(bps) / 10_000.0))


def mid(yb, nb) -> float | None:
    if yb is None or nb is None:
        return None
    return (int(yb) + (100 - int(nb))) / 2.0


def find_jumps(hours: list[tuple[dt.datetime, float | None]]) -> list[dt.datetime]:
    """Hours (the later of the pair) where the mid moved >= JUMP_CENTS since the previous
    snapshot no more than MAX_GAP_HOURS earlier."""
    out = []
    prev = None
    for hr, m in sorted(((h, m) for h, m in hours if m is not None), key=lambda x: x[0]):
        if prev is not None and (hr - prev[0]).total_seconds() <= MAX_GAP_HOURS * 3600 \
                and abs(m - prev[1]) >= JUMP_CENTS:
            out.append(hr)
        prev = (hr, m)
    return out


def reprice_clock(jumps: list[dt.datetime]) -> dict:
    """When repricings happen: the commonest UTC hour, weekday and day of month, with shares.
    `clear` when >= CLOCK_SHARE of >= MIN_JUMPS_FOR_CLOCK jumps fall in one UTC hour (+-1 h)."""
    n = len(jumps)
    if n == 0:
        return {"n": 0, "clear": False}
    by_hour = defaultdict(int)
    by_wd = defaultdict(int)
    by_dom = defaultdict(int)
    for j in jumps:
        by_hour[j.hour] += 1
        by_wd[j.weekday()] += 1
        by_dom[j.day] += 1
    # a +-1 h window around each hour, so a release that lands at :59 or :01 still clusters
    win = {h: by_hour.get((h - 1) % 24, 0) + by_hour.get(h, 0) + by_hour.get((h + 1) % 24, 0)
           for h in range(24)}
    h_top = max(win, key=lambda h: (win[h], by_hour.get(h, 0)))
    wd_top = max(by_wd, key=by_wd.get)
    return {"n": n, "hour": h_top, "hour_share": win[h_top] / n, "weekday": wd_top,
            "weekday_share": by_wd[wd_top] / n,
            "clear": n >= MIN_JUMPS_FOR_CLOCK and win[h_top] / n >= CLOCK_SHARE}


def step_aside_text(clock: dict) -> str:
    if not clock.get("clear"):
        return "no clear clock" if clock.get("n") else "no repricing seen"
    h = clock["hour"]
    wd = WEEKDAYS[clock["weekday"]]
    wd_part = f" (mostly {wd})" if clock["weekday_share"] >= 0.5 else ""
    return f"pull {(h - 2) % 24:02d}-{(h + 2) % 24:02d}Z{wd_part}"


def turnover(contracts_per_md: float | None, field: float | None) -> float | None:
    """Contracts traded per market-day over the competing score on our side: a rough measure of
    how fast the queue we would join is consumed (~1 = the whole field's size trades each day)."""
    if contracts_per_md is None or field is None or field <= 0:
        return None
    return contracts_per_md / field


def clock_text(clock: dict) -> str:
    n = clock.get("n", 0)
    if not n:
        return "-"
    if n < MIN_JUMPS_FOR_CLOCK:
        return f"too few ({n} jump{'s' if n > 1 else ''})"
    return (f"{clock['hour']:02d}Z±1 {100 * clock['hour_share']:.0f}%, "
            f"{WEEKDAYS[clock['weekday']]} {100 * clock['weekday_share']:.0f}% of {n}")


def risk_grade(jumps_per_30md: float | None) -> str:
    if jumps_per_30md is None:
        return "?"
    if jumps_per_30md >= RISK_JUMPS_HIGH:
        return "HIGH"
    if jumps_per_30md >= RISK_JUMPS_MED:
        return "MED"
    return "LOW"


def band(price: int) -> str:
    p = int(price)
    return "1-10" if p <= 10 else "11-25" if p <= 25 else "26-50" if p <= 50 else "51-99"


def rank_rows(rows: list[dict], now: dt.datetime) -> list[dict]:
    """One entry per market: its better side, with the reward per $10-day. Markets whose
    snapshot does not qualify (a side below target) or that are inside the step-aside are
    kept but marked, never ranked above a quotable market."""
    out = []
    for r in rows:
        pd = pool_per_day(r["pool"], r["start"], r["end"])
        best = None
        for side, price, field in (("yes", r["yb"], r["fy"]), ("no", r["nb"], r["fn"])):
            got = side_reward_day(pd, price, field)
            if got is None:
                continue
            if best is None or got[0] > best["reward_day"]:
                disc = discount(r.get("disc_bps"))
                behind = got[0] * disc if int(price) > 1 else got[0]
                best = {"side": side, "price": int(price), "reward_day": got[0], "share": got[1],
                        "field": float(field), "reward_day_behind": behind}
        close = _aware(r["close"])
        hours_left = (close - now).total_seconds() / 3600.0 if close else None
        flags = []
        if not (r["my"] and r["mn"]):
            flags.append("not-qualifying")
        if hours_left is not None and hours_left < STEP_ASIDE_HOURS:
            flags.append("inside-step-aside")
        if best is None:
            flags.append("no-bid")
        out.append({"ticker": r["ticker"], "series": (r["series"] or r["ticker"].split("-")[0]).upper(),
                    "event": r["event"], "pool_day": pd, "days_left": None if hours_left is None
                    else hours_left / 24.0, "flags": flags, "title": r.get("title"), **(best or {})})
    out.sort(key=lambda x: (bool(x["flags"]), -(x.get("reward_day") or 0.0)))
    return out


def by_series(ranked: list[dict]) -> list[dict]:
    agg: dict[str, dict] = defaultdict(lambda: {"markets": 0, "quotable": 0, "pool_day": 0.0,
                                                "best": 0.0, "sum_top": []})
    for x in ranked:
        a = agg[x["series"]]
        a["markets"] += 1
        a["pool_day"] += x["pool_day"] or 0.0
        if not x["flags"]:
            a["quotable"] += 1
            a["sum_top"].append(x["reward_day"])
            a["best"] = max(a["best"], x["reward_day"])
    out = []
    for s, a in agg.items():
        top3 = sorted(a["sum_top"], reverse=True)[:3]
        out.append({"series": s, "markets": a["markets"], "quotable": a["quotable"],
                    "pool_day": a["pool_day"], "best": a["best"], "top3_sum": sum(top3)})
    out.sort(key=lambda x: -x["top3_sum"])
    return out


# ---------------------------------------------------------------------------------------------


def _f(x, spec="{:.2f}"):
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else spec.format(x)


def series_risk(hist: dict[str, list], vol: dict[str, list], series_of: dict[str, str],
                now: dt.datetime) -> dict[str, dict]:
    """Per series: market-days observed, jumps per 30 market-days, the repricing clock, and
    contracts traded per market-day. `hist`: ticker -> [(hour, mid)]; `vol`: ticker -> [contracts
    per day]."""
    jumps: dict[str, list] = defaultdict(list)
    mdays: dict[str, set] = defaultdict(set)
    for tk, hs in hist.items():
        s = series_of.get(tk)
        if s is None:
            continue
        jumps[s].extend(find_jumps(hs))
        mdays[s] |= {(tk, h.date()) for h, m in hs if m is not None}
    vols: dict[str, list] = defaultdict(list)
    for tk, v in vol.items():
        if tk in series_of:
            vols[series_of[tk]].extend(v)
    out = {}
    for s in set(series_of.values()):
        md = len(mdays.get(s, ()))
        j = jumps.get(s, [])
        rate = 30.0 * len(j) / md if md else None
        clock = reprice_clock(j)
        v = vols.get(s, [])
        out[s] = {"market_days": md, "jumps": len(j), "jumps_per_30md": rate,
                  "risk": risk_grade(rate), "clock": clock, "step_aside": step_aside_text(clock),
                  "contracts_per_md": (sum(v) / md) if md else None,
                  "last_jump": max(j) if j else None,
                  "jump_times": sorted(set(j))[-6:]}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now", default=None, help="ISO time to rank as of (default: now)")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--json", action="store_true", help="also print one JSON line (for a page)")
    args = ap.parse_args(argv)
    now = _aware(args.now) if args.now else dt.datetime.now(dt.timezone.utc)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    opts = RO_OPTIONS.replace("statement_timeout=300000", "statement_timeout=1500000")
    with psycopg.connect(url, options=opts) as conn, conn.cursor() as cur:
        cur.execute(NOW_SQL, {"fresh": now - dt.timedelta(hours=FRESH_HOURS), "now": now})
        raw = cur.fetchall()
        tickers = sorted({r[0] for r in raw})
        since = now - dt.timedelta(days=HISTORY_DAYS)
        cur.execute(HIST_SQL, {"tickers": tickers, "since": since, "now": now})
        hist_raw = cur.fetchall()
        cur.execute(VOL_SQL, {"tickers": tickers, "since": since, "now": now})
        vol_raw = cur.fetchall()
    rows = [{"ticker": t, "at": at, "yb": yb, "nb": nb, "fy": fy, "fn": fn, "my": my, "mn": mn,
             "series": s, "event": e, "pool": pool, "start": st, "end": en, "close": cl,
             "target": tg, "title": ti, "disc_bps": db}
            for (t, at, yb, nb, fy, fn, my, mn, s, e, pool, st, en, cl, tg, ti, db) in raw]
    ranked = rank_rows(rows, now)
    series_of = {x["ticker"]: x["series"] for x in ranked}
    hist: dict[str, list] = defaultdict(list)
    for tk, hr, yb, nb in hist_raw:
        hist[tk].append((_aware(hr), mid(yb, nb)))
    vol: dict[str, list] = defaultdict(list)
    for tk, _d, contracts, _n in vol_raw:
        vol[tk].append(float(contracts or 0.0))
    risk = series_risk(hist, vol, series_of, now)

    quotable = [x for x in ranked if not x["flags"]]
    total_pool = sum(x["pool_day"] or 0.0 for x in ranked)
    print(f"LIMM market ranker — as of {now.isoformat()[:16]}Z; live liquidity markets "
          f"{len(ranked)} ({len(quotable)} quotable), pools ${total_pool:,.0f}/day; history "
          f"{HISTORY_DAYS} d: {len(hist_raw)} hourly snapshots, {sum(len(v) for v in vol.values())} "
          f"market-days of prints\n")
    print(f"== top {args.top} markets: estimated reward per day for a $10 bid (at the best bid / "
          f"one tick behind), with the series' repricing risk")
    print(f"  {'#':>2} {'market':34s} {'side':4s} {'px':>3} {'$/day':>6} {'behind':>6} "
          f"{'share':>6} {'days':>4} {'risk':4s} {'turn':>5}  step-aside")
    for i, x in enumerate(quotable[:args.top], 1):
        r = risk.get(x["series"], {})
        print(f"  {i:2d} {x['ticker'][:34]:34s} {x['side']:4s} {x['price']:3d} "
              f"{_f(x['reward_day']):>6} {_f(x['reward_day_behind']):>6} "
              f"{_f(100 * x['share'], '{:.1f}%'):>6} {_f(x['days_left'], '{:.0f}'):>4} "
              f"{r.get('risk', '?'):4s} {_f(turnover(r.get('contracts_per_md'), x['field']), '{:.2f}'):>5}  "
              f"{r.get('step_aside', '')}")
    print("\n== series: 3 x $10 bids (top 3 quotable markets) per day, and how the series reprices")
    print(f"  {'series':20s} {'quot':>4} {'pool/d':>6} {'top3':>5} {'mkt-d':>5} {'jumps':>5} "
          f"{'/30md':>5} {'risk':4s} {'ct/md':>6}  clock")
    srows = by_series(ranked)
    for srow in srows[:30]:
        r = risk.get(srow["series"], {})
        clock = clock_text(r.get("clock", {}))
        print(f"  {srow['series'][:20]:20s} {srow['quotable']:4d} {srow['pool_day']:6.0f} "
              f"{srow['top3_sum']:5.2f} {r.get('market_days', 0):5d} {r.get('jumps', 0):5d} "
              f"{_f(r.get('jumps_per_30md'), '{:.1f}'):>5} {r.get('risk', '?'):4s} "
              f"{_f(r.get('contracts_per_md'), '{:.0f}'):>6}  {clock}")
    print("\n== repricing history: the latest jumps per series (UTC; a jump = hourly mid move "
          f">= {JUMP_CENTS}c)")
    for srow in srows[:30]:
        jt = risk.get(srow["series"], {}).get("jump_times") or []
        if jt:
            print(f"  {srow['series'][:20]:20s} " + ", ".join(
                f"{WEEKDAYS[t.weekday()]} {t:%m-%d %H}Z" for t in jt))
    flagged = defaultdict(int)
    for x in ranked:
        for f in x["flags"]:
            flagged[f] += 1
    print("\n  not ranked: " + (", ".join(f"{k} {v}" for k, v in sorted(flagged.items())) or "none"))
    print("  turn: contracts traded per market-day over the competing size on our side (rough fill "
          "pressure; 1.0 = the whole queue trades in a day).")
    print(f"  risk: jumps (hourly mid moves >= {JUMP_CENTS}c) per 30 market-days — LOW < {RISK_JUMPS_MED}, MED < {RISK_JUMPS_HIGH}, HIGH."
          )
    if args.json:
        import json
        print("JSON " + json.dumps({
            "as_of": now.isoformat(), "pool_day": total_pool, "markets": len(ranked),
            "quotable": len(quotable),
            "top": [{**{k: x.get(k) for k in ("ticker", "series", "side", "price", "reward_day",
                                               "reward_day_behind", "share", "days_left", "title",
                                               "field")},
                     "risk": risk.get(x["series"], {}).get("risk"),
                     "step_aside": risk.get(x["series"], {}).get("step_aside"),
                     "turnover": turnover(risk.get(x["series"], {}).get("contracts_per_md"),
                                          x.get("field"))}
                    for x in quotable[:args.top]],
            "series": [{**srow, **{k: v for k, v in risk.get(srow["series"], {}).items()
                                   if k not in ("clock", "last_jump", "jump_times")},
                        "jump_times": [t.isoformat() for t in
                                       risk.get(srow["series"], {}).get("jump_times") or []],
                        "clock_text": clock_text(risk.get(srow["series"], {}).get("clock", {})),
                        "clock": risk.get(srow["series"], {}).get("clock")}
                       for srow in srows[:30]]}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
