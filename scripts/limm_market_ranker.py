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
CLOCK_SHARE = 0.4               # a clock is "clear" when this share of episodes start in one UTC hour
MIN_JUMPS_FOR_CLOCK = 3         # episodes needed before a clock is shown
EPISODE_GAP_HOURS = 12          # jumps closer than this (in a series) are one repricing episode
RISK_EP_HIGH, RISK_EP_MED = 3.0, 1.0            # repricing episodes per 30 observed days
#: Publication schedules verified against the publisher (cycle 4, 2026-11-08). A series matches
#: when its ticker ends with the key. Each entry: (what, next release UTC, source, advice).
KNOWN_RELEASES = {
    "EMP": ("BLS State Employment (10:00 ET)", "2026-11-20T15:00:00+00:00",
            "https://www.bls.gov/schedule/2026/",
            "pull bids before the release; markets kept repricing for ~48 h after Sep 18"),
}
#: Cycle 8 (DEC-044.3): one yardstick. Calibration = paid / estimated (scripts/
#: limm_estimate_calibration.py), per market type once the type has CAL_MIN_EVENTS well-covered
#: paid events, else the global median (GLOBAL_CALIBRATION when the live read is unavailable).
CAL_MIN_EVENTS = 10
GLOBAL_CALIBRATION = 1.34
#: Fill cost per resting market-hour, cents, by market type (limm_scorecard.category). Mention
#: events and YouTube views come from the threshold study (tools/review/limm/study/,
#: 2026-11-08: closed mention events 3.1, closed YouTube views 2.5). Every other type is
#: "unknown" until study 5 (docs/LIMM_FILL_HAZARD.md) reads on 2026-11-13.
TYPE_FILL_C_PER_H = {"mention_event": 3.1, "youtube_views": 2.5}
FILL_COST_SOURCE = {"mention_event": "threshold study 2026-11-08",
                    "youtube_views": "threshold study 2026-11-08"}
KALSHI_MINIMUM_USD = 1.00
AAA_NOTE = ("AAA daily national average; Kalshi gas contracts expire 10:15-11:00 ET (15-16Z), "
            "the hour these markets reprice")
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


UNWATCHED_SQL = """
WITH live AS (
  SELECT market_ticker, split_part(market_ticker, '-', 1) AS series, end_date,
         period_reward_usd / greatest(extract(epoch FROM (end_date - start_date)) / 86400.0, 0.04)
           AS pool_day
    FROM incentive_programs
   WHERE coalesce(incentive_type, '') ILIKE '%%liquid%%' AND superseded_at IS NULL
     AND disappeared_at IS NULL AND end_date > %(now)s AND start_date <= %(now)s
)
SELECT series, count(*) AS markets, sum(pool_day) AS pool_day, max(pool_day) AS max_market_pool_day,
       min(end_date) AS first_end
  FROM live WHERE NOT (market_ticker = ANY(%(tickers)s))
 GROUP BY 1 ORDER BY 3 DESC
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


def episodes(jumps: list[dt.datetime]) -> list[dt.datetime]:
    """Start times of repricing episodes: jumps (across a series' markets) closer than
    EPISODE_GAP_HOURS belong to one episode — one release moves every strike, over hours."""
    out: list[dt.datetime] = []
    last = None
    for j in sorted(set(jumps)):
        if last is None or (j - last).total_seconds() > EPISODE_GAP_HOURS * 3600:
            out.append(j)
        last = j
    return out


def known_release(series: str) -> tuple | None:
    s = (series or "").upper()
    if s.startswith("KXAAAGAS"):
        return ("AAA gas", None, "https://gasprices.aaa.com/", AAA_NOTE)
    for key, val in KNOWN_RELEASES.items():
        if s.endswith(key):
            return val
    return None


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
        return f"too few ({n} episode{'s' if n > 1 else ''})"
    return (f"{clock['hour']:02d}Z±1 {100 * clock['hour_share']:.0f}%, "
            f"{WEEKDAYS[clock['weekday']]} {100 * clock['weekday_share']:.0f}% of {n}")


def risk_grade(episodes_per_30d: float | None) -> str:
    if episodes_per_30d is None:
        return "?"
    if episodes_per_30d >= RISK_EP_HIGH:
        return "HIGH"
    if episodes_per_30d >= RISK_EP_MED:
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
        pay_h = hours_to_step_aside(r.get("end"), close, now)
        flags = []
        if not (r["my"] and r["mn"]):
            flags.append("not-qualifying")
        if hours_left is not None and hours_left < STEP_ASIDE_HOURS:
            flags.append("inside-step-aside")
        if best is None:
            flags.append("no-bid")
        out.append({"ticker": r["ticker"], "series": (r["series"] or r["ticker"].split("-")[0]).upper(),
                    "event": r["event"], "pool_day": pd, "days_left": None if hours_left is None
                    else hours_left / 24.0, "pay_days_left": None if pay_h is None else pay_h / 24.0,
                    "flags": flags, "title": r.get("title"), **(best or {})})
    out.sort(key=lambda x: (bool(x["flags"]), -(x.get("reward_day") or 0.0)))
    return out


def hours_to_step_aside(end, close, now: dt.datetime) -> float | None:
    """Hours a bid placed now can rest: to the programme's end or 48 h before close."""
    end, close = _aware(end), _aware(close)
    cuts = [t for t in (end, close - dt.timedelta(hours=STEP_ASIDE_HOURS) if close else None) if t]
    if not cuts:
        return None
    return max(0.0, (min(cuts) - now).total_seconds() / 3600.0)


def market_type(ticker: str) -> str:
    from limm_scorecard import category  # the threshold study's types, so the costs line up

    return category(ticker)


def type_factors(events: list[dict], global_median: float | None) -> dict[str, dict]:
    """Per market type: the calibration factor and where it came from. A type uses its own
    median paid/est once it has CAL_MIN_EVENTS paid events with >= 70% snapshot coverage."""
    g = global_median if global_median else GLOBAL_CALIBRATION
    by: dict[str, list[float]] = defaultdict(list)
    for e in events:
        if not e.get("paid_recorded") or not e.get("rest_h") or e.get("ratio") is None:
            continue
        if e.get("covered_h", 0.0) / e["rest_h"] < 0.7:
            continue
        by[market_type(e["event"])].append(float(e["ratio"]))
    out = {"_global": {"factor": g, "source": "global median" if global_median else
                       "global median (fixed 1.34; live calibration unavailable)", "events": None}}
    for t, ratios in by.items():
        ratios.sort()
        if len(ratios) >= CAL_MIN_EVENTS:
            out[t] = {"factor": ratios[len(ratios) // 2], "source": f"{t} median", "events": len(ratios)}
        else:
            out[t] = {"factor": g, "source": f"global (only {len(ratios)} paid {t} events)",
                      "events": len(ratios)}
    return out


def yardstick(ranked: list[dict], factors: dict[str, dict]) -> list[dict]:
    """Cycle 8: calibrated paid c/h beside fill c/h, per quotable market, ranked by the net where
    the type's fill cost is known. `under_1usd`: a $10 bid's projected calibrated reward to the
    step-aside is under Kalshi's $1 minimum (S4's universe)."""
    out = []
    for x in ranked:
        if x["flags"] or x.get("reward_day") is None:
            continue
        t = market_type(x["ticker"])
        f = factors.get(t) or factors["_global"]
        paid = 100.0 * x["reward_day"] / 24.0 * f["factor"]
        fill = TYPE_FILL_C_PER_H.get(t)
        proj = None if x.get("pay_days_left") is None else x["reward_day"] * x["pay_days_left"] * f["factor"]
        out.append({"ticker": x["ticker"], "type": t, "side": x["side"], "price": x["price"],
                    "factor": f["factor"], "factor_source": f["source"], "paid_c_h": paid,
                    "fill_c_h": fill, "net_c_h": None if fill is None else paid - fill,
                    "projected_usd": proj,
                    "under_1usd": proj is not None and proj < KALSHI_MINIMUM_USD})
    out.sort(key=lambda r: (r["net_c_h"] is None, -(r["net_c_h"] if r["net_c_h"] is not None
                                                     else r["paid_c_h"])))
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
        days = len({d for _tk, d in mdays.get(s, ())})
        j = jumps.get(s, [])
        ep = episodes(j)
        rate = 30.0 * len(ep) / days if days else None
        clock = reprice_clock(ep)
        rel = known_release(s)
        step = step_aside_text(clock)
        if rel is not None and rel[1]:
            step = f"pull before {rel[1][5:10]} {rel[1][11:13]}Z release (+48 h)"
        v = vols.get(s, [])
        out[s] = {"market_days": md, "days": days, "jumps": len(j), "episodes": len(ep),
                  "episodes_per_30d": rate, "jumps_per_30md": 30.0 * len(j) / md if md else None,
                  "risk": risk_grade(rate), "clock": clock, "step_aside": step,
                  "release": rel,
                  "contracts_per_md": (sum(v) / md) if md else None,
                  "last_jump": max(j) if j else None,
                  "jump_times": sorted(set(j))[-6:], "episode_times": ep[-6:]}
    return out


MIN_MARKET_DAYS_FOR_OWN_RISK = 3


def market_risk(hours: list, series_entry: dict | None) -> dict:
    """The market's OWN repricing record. A far-from-the-money strike in a busy series rarely
    moves itself, so the series grade overstates its risk. Falls back to the series grade when
    the market has fewer than MIN_MARKET_DAYS_FOR_OWN_RISK observed days."""
    days = len({h.date() for h, m in hours if m is not None})
    if days < MIN_MARKET_DAYS_FOR_OWN_RISK:
        return {"risk": (series_entry or {}).get("risk", "?"), "own": False, "episodes": None,
                "days": days}
    ep = episodes(find_jumps(hours))
    rate = 30.0 * len(ep) / days
    return {"risk": risk_grade(rate), "own": True, "episodes": len(ep), "days": days}


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
        cur.execute(UNWATCHED_SQL, {"tickers": tickers, "now": now})
        unwatched = [{"series": a, "markets": int(b), "pool_day": float(c or 0.0),
                      "max_market_pool_day": float(d or 0.0), "first_end": e}
                     for a, b, c, d, e in cur.fetchall()]
        try:
            import limm_estimate_calibration as cal

            o, pr, sn = cal.fetch(cur, cal.LIVE_TAG)
            cal_s = cal.summarise(o, pr, sn, cal.load_payouts(), cal.load_scoring(), now)
            factors = type_factors(cal_s["events"], cal_s.get("median_ratio_well_covered"))
        except Exception as exc:  # noqa: BLE001 — the yardstick still prints, on the fixed 1.34
            print(f"(calibration read failed: {type(exc).__name__}: {exc}; using {GLOBAL_CALIBRATION})")
            factors = type_factors([], None)
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
    mrisk = {x["ticker"]: market_risk(hist.get(x["ticker"], []), risk.get(x["series"]))
             for x in ranked}

    quotable = [x for x in ranked if not x["flags"]]
    total_pool = sum(x["pool_day"] or 0.0 for x in ranked)
    print(f"LIMM market ranker — as of {now.isoformat()[:16]}Z; live liquidity markets "
          f"{len(ranked)} ({len(quotable)} quotable), pools ${total_pool:,.0f}/day; history "
          f"{HISTORY_DAYS} d: {len(hist_raw)} hourly snapshots, {sum(len(v) for v in vol.values())} "
          f"market-days of prints\n")
    print(f"== top {args.top} markets: estimated reward per day for a $10 bid (at the best bid / "
          f"one tick behind), with the series' repricing risk")
    print(f"  {'#':>2} {'market':34s} {'side':4s} {'px':>3} {'$/day':>6} {'behind':>6} "
          f"{'share':>6} {'days':>4} {'risk':5s}{'turn':>5}  step-aside")
    for i, x in enumerate(quotable[:args.top], 1):
        r = risk.get(x["series"], {})
        print(f"  {i:2d} {x['ticker'][:34]:34s} {x['side']:4s} {x['price']:3d} "
              f"{_f(x['reward_day']):>6} {_f(x['reward_day_behind']):>6} "
              f"{_f(100 * x['share'], '{:.1f}%'):>6} {_f(x['days_left'], '{:.0f}'):>4} "
              f"{mrisk[x['ticker']]['risk'] + ('' if mrisk[x['ticker']]['own'] else '*'):5s}"
              f"{_f(turnover(r.get('contracts_per_md'), x['field']), '{:.2f}'):>5}  "
              f"{r.get('step_aside', '')}")
    print("\n== series: 3 x $10 bids (top 3 quotable markets) per day, and how the series reprices")
    print(f"  {'series':20s} {'quot':>4} {'pool/d':>6} {'top3':>5} {'days':>4} {'jumps':>5} "
          f"{'eps':>3} {'/30d':>4} {'risk':4s} {'ct/md':>6}  clock")
    srows = by_series(ranked)
    for srow in srows[:30]:
        r = risk.get(srow["series"], {})
        clock = clock_text(r.get("clock", {}))
        print(f"  {srow['series'][:20]:20s} {srow['quotable']:4d} {srow['pool_day']:6.0f} "
              f"{srow['top3_sum']:5.2f} {r.get('days', 0):4d} {r.get('jumps', 0):5d} "
              f"{r.get('episodes', 0):3d} {_f(r.get('episodes_per_30d'), '{:.1f}'):>4} {r.get('risk', '?'):4s} "
              f"{_f(r.get('contracts_per_md'), '{:.0f}'):>6}  {clock}")
    print("\n== repricing history: the latest episode starts per series (UTC; a jump = hourly mid "
          f"move >= {JUMP_CENTS}c; jumps within {EPISODE_GAP_HOURS} h are one episode)")
    for srow in srows[:30]:
        jt = risk.get(srow["series"], {}).get("episode_times") or []
        if jt:
            print(f"  {srow['series'][:20]:20s} " + ", ".join(
                f"{WEEKDAYS[t.weekday()]} {t:%m-%d %H}Z" for t in jt))
    yard = yardstick(ranked, factors)
    print("\n== cycle 8 yardstick: calibrated paid c/h against fill c/h for a $10 bid, by market type")
    print("  calibration: " + "; ".join(f"{t} {v['factor']:.2f} ({v['source']})"
                                        for t, v in sorted(factors.items())))
    print("  fill c/h: " + ", ".join(f"{t} {c} ({FILL_COST_SOURCE[t]})" for t, c in TYPE_FILL_C_PER_H.items())
          + "; other types unknown until study 5 reads on 2026-11-13")
    print(f"  {'#':>2} {'market':34s} {'type':14s} {'px':>3} {'cal':>4} {'paid':>6} {'fill':>5} "
          f"{'net':>6} {'proj$':>6}  <$1")
    for i, y in enumerate(yard[:args.top], 1):
        print(f"  {i:2d} {y['ticker'][:34]:34s} {y['type'][:14]:14s} {y['price']:3d} {y['factor']:4.2f} "
              f"{y['paid_c_h']:6.2f} {_f(y['fill_c_h'], '{:.1f}'):>5} {_f(y['net_c_h'], '{:+.2f}'):>6} "
              f"{_f(y['projected_usd']):>6}  {'YES' if y['under_1usd'] else ''}")
    under = [y for y in yard if y["under_1usd"]]
    print(f"  under $1 at $10 to the step-aside (S4's universe): {len(under)} of {len(yard)} quotable "
          f"markets" + (": " + ", ".join(y["ticker"] for y in under[:10]) if under else ""))
    uw_pool = sum(u["pool_day"] for u in unwatched)
    uw_mk = sum(u["markets"] for u in unwatched)
    print(f"\n== NOT WATCHED by the collector: {uw_mk} live markets in {len(unwatched)} series, "
          f"pools ${uw_pool:,.0f}/day (watched: {len(ranked)} markets, ${total_pool:,.0f}/day).")
    print("   No order-book data, so no share estimate: these are pools we cannot see the field for.")
    print(f"  {'series':22s} {'mkts':>5} {'pool/d':>8} {'best mkt/d':>10}  ends")
    for u in unwatched[:15]:
        print(f"  {u['series'][:22]:22s} {u['markets']:5d} {u['pool_day']:8.0f} "
              f"{u['max_market_pool_day']:10.1f}  {str(u['first_end'])[:10]}")
    flagged = defaultdict(int)
    for x in ranked:
        for f in x["flags"]:
            flagged[f] += 1
    print("\n  not ranked: " + (", ".join(f"{k} {v}" for k, v in sorted(flagged.items())) or "none"))
    print("  risk (top table): the market's own repricing episodes per 30 days; * = too little "
          "history, series grade shown.")
    print("  turn: contracts traded per market-day over the competing size on our side (rough fill "
          "pressure; 1.0 = the whole queue trades in a day).")
    print(f"  risk: repricing episodes (hourly mid moves >= {JUMP_CENTS}c; jumps within "
          f"{EPISODE_GAP_HOURS} h are one episode) per 30 observed days — LOW < {RISK_EP_MED}, "
          f"MED < {RISK_EP_HIGH}, HIGH.")
    for srow in srows[:30]:
        rel = risk.get(srow["series"], {}).get("release")
        if rel:
            print(f"  release: {srow['series']}: {rel[0]}"
                  + (f", next {rel[1][:16]}Z" if rel[1] else "") + f" — {rel[3]} ({rel[2]})")
    if args.json:
        import json
        print("JSON " + json.dumps({
            "as_of": now.isoformat(), "pool_day": total_pool, "markets": len(ranked),
            "unwatched": {"markets": uw_mk, "series": len(unwatched), "pool_day": uw_pool,
                          "top": unwatched[:15]},
            "quotable": len(quotable),
            "calibration": factors, "yardstick": yard[:args.top],
            "under_1usd": [y["ticker"] for y in yard if y["under_1usd"]],
            "top": [{**{k: x.get(k) for k in ("ticker", "series", "side", "price", "reward_day",
                                               "reward_day_behind", "share", "days_left", "title",
                                               "field")},
                     "risk": mrisk[x["ticker"]]["risk"],
                     "risk_own": mrisk[x["ticker"]]["own"],
                     "risk_series": risk.get(x["series"], {}).get("risk"),
                     "step_aside": risk.get(x["series"], {}).get("step_aside"),
                     "turnover": turnover(risk.get(x["series"], {}).get("contracts_per_md"),
                                          x.get("field"))}
                    for x in quotable[:args.top]],
            "series": [{**srow, **{k: v for k, v in risk.get(srow["series"], {}).items()
                                   if k not in ("clock", "last_jump", "jump_times",
                                                "episode_times", "release")},
                        "release": (list(risk.get(srow["series"], {}).get("release"))
                                    if risk.get(srow["series"], {}).get("release") else None),
                        "jump_times": [t.isoformat() for t in
                                       risk.get(srow["series"], {}).get("episode_times") or []],
                        "clock_text": clock_text(risk.get(srow["series"], {}).get("clock", {})),
                        "clock": risk.get(srow["series"], {}).get("clock")}
                       for srow in srows[:30]]}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
