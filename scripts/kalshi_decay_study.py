"""DECAY probe — do Kalshi "will X happen by <date>" markets overprice the YES side as the
deadline approaches (a harvestable deadline-hazard premium), or is the sticky quote fair
insurance?

Pre-registered thesis in docs/IDEA_MODEL_20260710.md (DECAY). READ-ONLY study on public
Kalshi data only — no DB, no collector, no auth:

  - /events?status=settled&with_nested_markets=true -> universe of settled events with
    category + titles + results; sports/crypto/weather excluded, titles must match a
    by-date pattern ("... by July 31?", "... before August?", "... this year?").
  - /series/{series}/markets/{ticker}/candlesticks (hourly) -> the executable entry price at
    T-28d / T-14d / T-7d before close: the strategy SELLS YES (buys NO), so the entry uses the
    yes_bid close (you short at the bid — conservative).

Strategy under test: at T-N days before close, if YES trades in [5,20]c, buy NO and hold to
settlement. EV cents/contract net of the entry-leg taker fee ceil(0.07*P*(1-P)*100)
(hold-to-settlement -> no exit leg; settlement is free).

Pre-registered predictions (graded verbatim, do not re-scope):
  P1 the premium clears cost: T-14d cohort, YES in [5,20]c -> EV >= +2c/ct pooled at >=85% win,
     n >= 150 (KILL < +1c).
  P2 the hazard signature: EV monotone in time-to-deadline, T-7 >= T-14 >= T-28. If flat or
     inverted, it's generic longshot premium, NOT decay -> fold into the mmsell family's
     knowledge; do not build a book.
  P3 not a mirage: split-half (by close date) both halves positive AND the pooled result
     survives excluding the single largest-loss market.
  P4 survivable tails: worst single settlement-DAY loss <= 10x the median winning day.
  Decision rule: paper book only if P1 AND P2 AND P3; P4 fail -> size-capped flag, not a kill.

Read-only public API, stdlib only. Usage (via the ops channel):
    {"type":"script","name":"kalshi_decay_study","args":["--max-event-pages","80"],"id":"decay-0"}
"""

from __future__ import annotations

import argparse
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

SKIP_CATEGORIES = re.compile(r"sport|crypto|climate|weather", re.I)
SKIP_SERIES = re.compile(r"^KX(HIGH|LOW|BTC|ETH|MVE)", re.I)
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|"
          "december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
BY_DATE = re.compile(
    rf"\b(by|before)\s+(the\s+)?(end\s+of\s+)?(({MONTHS})\b|\d{{1,2}}[/-]\d{{1,2}}|\d{{4}}\b)"
    rf"|\bthis year\b|\bin 202\d\b", re.I)

COHORTS = (28, 14, 7)  # days before close


def fee_cents(price_cents: float) -> float:
    p = max(0.0, min(1.0, price_cents / 100.0))
    return math.ceil(0.07 * p * (1 - p) * 100)


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def settled_events(max_pages: int) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=settled&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        out.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def hourly_bids(series: str, ticker: str, start: int, end: int) -> dict[int, float]:
    """hour-bucket ts -> yes_bid close in CENTS, from hourly candles (one call per market)."""
    url = (f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
           f"?start_ts={start}&end_ts={end}&period_interval=60")
    data = xl._get(url)
    out: dict[int, float] = {}
    for c in (data or {}).get("candlesticks") or []:
        ts = c.get("end_period_ts")
        yb = (c.get("yes_bid") or {}).get("close_dollars")
        if ts is None or yb is None:
            continue
        bid = xl._num(yb) * 100.0
        if 0 < bid < 100:
            out[int(ts)] = bid
    return out


def snapshot(bids: dict[int, float], at_ts: float, lookback_h: int = 48) -> float | None:
    """Last hourly yes_bid at or before at_ts (within lookback) — the executable short price."""
    best = None
    for ts, bid in bids.items():
        if ts <= at_ts and at_ts - ts <= lookback_h * 3600:
            if best is None or ts > best[0]:
                best = (ts, bid)
    return best[1] if best else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DECAY probe (pre-registered 2026-07-10)")
    ap.add_argument("--max-event-pages", type=int, default=80)
    ap.add_argument("--max-markets", type=int, default=600)
    ap.add_argument("--band", default="5,20", help="YES entry band in cents, lo,hi")
    args = ap.parse_args(argv)
    lo, hi = (float(x) for x in args.band.split(","))

    print("DECAY probe — by-date deadline premium (pre-registered docs/IDEA_MODEL_20260710.md)")
    events = settled_events(args.max_event_pages)
    print(f"settled events scanned: {len(events)}")

    targets: list[tuple[str, dict, str]] = []  # (series, market, category)
    for ev in events:
        cat = ev.get("category") or "?"
        if SKIP_CATEGORIES.search(cat):
            continue
        series = (ev.get("event_ticker") or "").split("-", 1)[0]
        if SKIP_SERIES.match(series):
            continue
        text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title")]))
        for m in ev.get("markets") or []:
            mtext = " ".join(filter(None, [text, m.get("title"), m.get("subtitle"),
                                           m.get("rules_primary", "")[:200]]))
            if not BY_DATE.search(mtext):
                continue
            if (m.get("result") or "").lower() not in ("yes", "no"):
                continue
            targets.append((series, m, cat))
    print(f"by-date settled markets matched: {len(targets)} "
          f"(cap {args.max_markets})")
    targets = targets[: args.max_markets]

    # rows[cohort] = list of (ev_cents, close_ts, ticker, category, win?)
    rows: dict[int, list[tuple[float, float, str, str, bool]]] = defaultdict(list)
    pulled = 0
    for series, m, cat in targets:
        close = _ts(m.get("close_time"))
        if not close:
            continue
        start = int(close - (max(COHORTS) + 2) * 86400)
        bids = hourly_bids(series, m["ticker"], start, int(close))
        if not bids:
            continue
        pulled += 1
        result = (m.get("result") or "").lower()
        for n in COHORTS:
            bid = snapshot(bids, close - n * 86400)
            if bid is None or not (lo <= bid <= hi):
                continue
            # buy NO at (100 - yes_bid): win -> +yes_bid, lose -> -(100 - yes_bid); fee at cost
            cost = 100 - bid
            ev = (bid if result == "no" else -cost) - fee_cents(cost)
            rows[n].append((ev, close, m["ticker"], cat, result == "no"))
        time.sleep(0.03)
    print(f"markets with candle history: {pulled}")

    def stats(rs):
        if not rs:
            return 0.0, 0, 0.0
        evs = [r[0] for r in rs]
        return sum(evs) / len(evs), len(rs), 100.0 * sum(1 for r in rs if r[4]) / len(rs)

    print("\n== cohorts (YES in [{:.0f},{:.0f}]c at entry; EV c/ct net of entry fee) ==".format(lo, hi))
    ev_by_cohort = {}
    for n in COHORTS:
        ev_n, cnt, win = stats(rows[n])
        ev_by_cohort[n] = ev_n
        print(f"  T-{n:<3d} EV {ev_n:+.2f}  n={cnt}  win {win:.1f}%")

    main_rows = rows[14]
    ev14, n14, win14 = stats(main_rows)

    print("\n== per category (T-14) ==")
    by_cat = defaultdict(list)
    for r in main_rows:
        by_cat[r[3]].append(r)
    for c, rs in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
        e, cnt, w = stats(rs)
        print(f"  {c:28.28s} EV {e:+.2f}  n={cnt}  win {w:.0f}%")

    # P3: split-half by close date + drop-worst-market
    sorted_rows = sorted(main_rows, key=lambda r: r[1])
    half = len(sorted_rows) // 2
    ev_a = stats(sorted_rows[:half])[0] if half else 0.0
    ev_b = stats(sorted_rows[half:])[0] if half else 0.0
    worst_by_mkt = defaultdict(float)
    for r in main_rows:
        worst_by_mkt[r[2]] += r[0]
    worst_mkt = min(worst_by_mkt, key=worst_by_mkt.get) if worst_by_mkt else None
    ev_wo_worst = stats([r for r in main_rows if r[2] != worst_mkt])[0]
    print(f"\nsplit-half (T-14): first {ev_a:+.2f} / second {ev_b:+.2f}; "
          f"excl worst market ({worst_mkt}): {ev_wo_worst:+.2f}")

    # P4: worst settlement-day loss vs median winning day
    by_day = defaultdict(float)
    for r in main_rows:
        by_day[int(r[1] // 86400)] += r[0]
    day_pnls = list(by_day.values())
    worst_day = min(day_pnls) if day_pnls else 0.0
    win_days = sorted(p for p in day_pnls if p > 0)
    med_win = win_days[len(win_days) // 2] if win_days else 0.0
    print(f"worst day {worst_day:+.1f}c vs median winning day {med_win:+.1f}c")

    print("\n== pre-registered verdicts (thresholds fixed 2026-07-10; do not re-scope) ==")
    p1 = ("PASS" if (ev14 >= 2.0 and win14 >= 85.0 and n14 >= 150)
          else ("KILL" if ev14 < 1.0 else "GREY"))
    mono = ev_by_cohort[7] >= ev_by_cohort[14] >= ev_by_cohort[28]
    p2 = "PASS" if mono else "FAIL"
    p3 = "PASS" if (ev_a > 0 and ev_b > 0 and ev_wo_worst > 0) else "FAIL"
    p4 = "PASS" if (med_win <= 0 or abs(worst_day) <= 10 * med_win) else "FAIL"
    print(f"P1 T-14 EV >= +2c, win >= 85%, n >= 150 (kill < +1c): {p1} "
          f"({ev14:+.2f}c, win {win14:.0f}%, n={n14})")
    print(f"P2 hazard signature (T-7 >= T-14 >= T-28): {p2} "
          f"({ev_by_cohort[7]:+.2f} / {ev_by_cohort[14]:+.2f} / {ev_by_cohort[28]:+.2f})")
    print(f"P3 split-half + drop-worst all positive: {p3}")
    print(f"P4 worst day <= 10x median winning day: {p4}")
    verdict = (p1 == "PASS" and p2 == "PASS" and p3 == "PASS")
    print(f"DECISION (P1 & P2 & P3): {'PROMOTE to paper book' if verdict else 'do not promote'}"
          f"{' [size-capped: P4 fail]' if verdict and p4 == 'FAIL' else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
