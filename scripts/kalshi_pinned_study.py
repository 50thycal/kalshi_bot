"""PINNED probe — do backwater Kalshi markets keep trading at a discount AFTER their public
settlement source has already decided the outcome?

Pre-registered thesis in docs/IDEA_MODEL_20260710.md (PINNED). READ-ONLY study on public
Kalshi data only — no DB, no collector, no auth:

  - /events?status=settled&with_nested_markets=true -> universe (category, titles, results)
  - /markets/trades?ticker=... -> the actual trade tape (every prediction is measured on REAL
    TRADES, never on quoted asks — the favbuy study's "already-decided favorite" mirage was a
    phantom-quote artifact, so fillability is load-bearing here)

Two pin families, classified by event/series title keywords:

  R (scheduled release): CPI / payrolls / jobless claims / GDP / Fed decision markets settle on
    a print published at a KNOWN clock time (8:30 ET for BLS/DOL/BEA prints, 14:00 ET for FOMC).
    After the release minute, the settlement-determining number is PUBLIC, so the eventual
    result IS the pinned side — using `result` for post-release trades is not lookahead.
  G (glacial daily index): gas-price style ladders over a slow-moving public index. The
    underlying path is reconstructed from the SAME SERIES' prior settled events (winning-bucket
    midpoints); a market pins on the first day the remaining time x the max historical per-day
    move (+ a bucket halfwidth guard) cannot reach the strike. The pinned side is computed from
    the reconstructed value vs the strike — never from settlement — and any pinned-side LOSS is
    a red flag printed loudly (it means the pin logic or the source assumption is wrong).

Pre-registered predictions (graded verbatim, do not re-scope):
  P1 post-pin discount exists and is traded: pooled post-pin EV >= +3c/ct on >=100 real trades
     (KILL < 1.5c).
  P2 the pin is the mechanism: post-pin EV exceeds a PRE-pin favorite-buy control (buy the
     >=85c side at traded prices, scored against actual outcomes) by >= 2c/ct — otherwise it's
     generic favorite drift, which is dead (tfav).
  P3 breadth: the P1 bar clears in >= 2 independent series families.
  P4 capacity: post-pin notional trading at >= 2c discount averages >= $200/week.
  Decision rule: paper book only if P1 AND P2 AND (P3 OR P4).

EV convention: cents/contract net of the entry-leg taker fee ceil(0.07*P*(1-P)*100) (hold to
settlement -> no exit leg). Read-only public API, stdlib only. Usage (via the ops channel):
    {"type":"script","name":"kalshi_pinned_study","args":["--max-event-pages","60"],"id":"pinned-0"}
"""

from __future__ import annotations

import argparse
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
ET = ZoneInfo("America/New_York")

# Release-family keywords -> release local time (ET). Matched against event/market titles.
RELEASE_RULES: list[tuple[re.Pattern, tuple[int, int], str]] = [
    (re.compile(r"\b(cpi|inflation rate|consumer price)\b", re.I), (8, 30), "cpi"),
    (re.compile(r"\b(payroll|jobs report|nonfarm)\b", re.I), (8, 30), "jobs"),
    (re.compile(r"\b(jobless|initial claims|unemployment claims)\b", re.I), (8, 30), "claims"),
    (re.compile(r"\bunemployment rate\b", re.I), (8, 30), "jobs"),
    (re.compile(r"\bgdp\b", re.I), (8, 30), "gdp"),
    (re.compile(r"\bretail sales\b", re.I), (8, 30), "retail"),
    (re.compile(r"\b(fed(eral)? (funds|reserve)|fomc|rate (hike|cut|decision))\b", re.I),
     (14, 0), "fed"),
]
GLACIAL_RE = re.compile(r"\b(gas price|gasoline|aaa)\b", re.I)

SKIP_CATEGORIES = re.compile(r"sport|crypto", re.I)


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


def trades(ticker: str, max_pages: int = 5) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/markets/trades?ticker={ticker}&limit=1000&cursor={cursor}")
        rows = (page or {}).get("trades") or []
        out.extend(rows)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not rows:
            break
        time.sleep(0.05)
    return out


def classify(ev: dict) -> tuple[str, tuple[int, int] | None, str]:
    """-> (family 'R'|'G'|'', release ET time, family key)."""
    text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title")]))
    for rx, hhmm, key in RELEASE_RULES:
        if rx.search(text):
            return "R", hhmm, key
    if GLACIAL_RE.search(text):
        return "G", None, "gas"
    return "", None, ""


def release_pin_ts(mkt: dict, hhmm: tuple[int, int]) -> float:
    """Pin = the scheduled release minute (ET) on the market's close date."""
    close = _ts(mkt.get("close_time"))
    if not close:
        return 0.0
    d = datetime.fromtimestamp(close, tz=ET)
    pin = d.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
    return pin.timestamp()


# --- G family: reconstruct the glacial index path from prior settled ladders -------


def bucket_bounds(m: dict) -> tuple[float | None, float | None]:
    lo = m.get("floor_strike")
    hi = m.get("cap_strike")
    lo = float(lo) if lo is not None else None
    hi = float(hi) if hi is not None else None
    return lo, hi


def event_day_value(markets: list[dict]) -> float | None:
    """The winning bucket's midpoint (or bound for open-ended rungs) for one settled event."""
    for m in markets:
        if (m.get("result") or "").lower() != "yes":
            continue
        lo, hi = bucket_bounds(m)
        if lo is not None and hi is not None:
            return (lo + hi) / 2.0
        return lo if lo is not None else hi
    return None


def glacial_pins(events: list[dict]) -> dict[str, tuple[float, str]]:
    """ticker -> (pin_ts, pinned_side). No-lookahead: day t uses only values from days < t."""
    daily: list[tuple[float, float, dict]] = []  # (close_ts_of_event, value, event)
    for ev in events:
        v = event_day_value(ev.get("markets") or [])
        ts = max((_ts(m.get("close_time")) for m in ev.get("markets") or []), default=0.0)
        if v is not None and ts:
            daily.append((ts, v, ev))
    daily.sort()
    pins: dict[str, tuple[float, str]] = {}
    for i, (ts_i, _v, ev) in enumerate(daily):
        hist = daily[:i]  # strictly earlier events only
        if len(hist) < 4:
            continue
        vals = [(t, v) for t, v, _ in hist]
        max_rate = max((abs(v2 - v1) / max((t2 - t1) / 86400.0, 0.5)
                        for (t1, v1), (t2, v2) in zip(vals, vals[1:])), default=0.0)
        max_rate = max(max_rate, 1e-9)
        halfw = _median([abs(h - l) / 2.0 for _, _, e in hist for m in e.get("markets") or []
                         for l, h in [bucket_bounds(m)] if l is not None and h is not None]) or 0.0
        t_now, v_now = vals[-1]  # latest knowledge strictly before this event
        for m in ev.get("markets") or []:
            close = _ts(m.get("close_time"))
            if not close or close <= t_now:
                continue
            lo, hi = bucket_bounds(m)
            days_left = (close - t_now) / 86400.0
            reach = days_left * max_rate + halfw
            side = None
            if lo is not None and hi is not None:          # range bucket
                if v_now < lo - reach or v_now > hi + reach:
                    side = "no"
                # a range bucket can pin YES only if both bounds are unreachable outward — rare
            elif lo is not None:                            # ">= lo" threshold
                side = "yes" if v_now > lo + reach else ("no" if v_now < lo - reach else None)
            elif hi is not None:                            # "<= hi" threshold
                side = "yes" if v_now < hi - reach else ("no" if v_now > hi + reach else None)
            if side:
                pins[m.get("ticker")] = (t_now, side)  # pinned from the moment of last knowledge
    return pins


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


# --- scoring ------------------------------------------------------------------------


def score_trades(rows: list[dict], result: str, pin_ts: float, pinned_side: str,
                 acc: dict, fam: str, week_notional: dict) -> None:
    for t in rows:
        ts = _ts(t.get("created_time"))
        p = xl._num(t.get("yes_price"))  # cents
        cnt = int(xl._num(t.get("count") or t.get("count_fp")) or 0) or 1
        if not ts or p <= 0 or p >= 100:
            continue
        if ts >= pin_ts:
            side = pinned_side
            cost = p if side == "yes" else 100 - p
            ev = (100 - cost if result == side else -cost) - fee_cents(cost)
            acc["post"].append((ev, cnt, fam))
            if result != side:
                acc["red_flags"].append((t.get("ticker"), fam, side, result, cost))
            if 100 - cost >= 2 and result == side:
                wk = int(ts // (7 * 86400))
                week_notional[wk] = week_notional.get(wk, 0.0) + cost * cnt / 100.0
        else:
            fav = "yes" if p >= 85 else ("no" if p <= 15 else None)  # pre-pin favorite control
            if fav:
                cost = p if fav == "yes" else 100 - p
                ev = (100 - cost if result == fav else -cost) - fee_cents(cost)
                acc["pre"].append((ev, cnt, fam))


def wavg(rows: list[tuple[float, int, str]]) -> tuple[float, int]:
    n = sum(c for _, c, _ in rows)
    return (sum(e * c for e, c, _ in rows) / n if n else 0.0), n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PINNED probe (pre-registered 2026-07-10)")
    ap.add_argument("--max-event-pages", type=int, default=60)
    ap.add_argument("--max-markets", type=int, default=400)
    ap.add_argument("--trade-pages", type=int, default=5)
    args = ap.parse_args(argv)

    print("PINNED probe — post-pin discount study (pre-registered docs/IDEA_MODEL_20260710.md)")
    events = settled_events(args.max_event_pages)
    print(f"settled events scanned: {len(events)}")

    fam_events: dict[str, list[dict]] = defaultdict(list)
    r_targets: list[tuple[dict, dict, tuple[int, int], str]] = []  # (event, market, hhmm, key)
    for ev in events:
        if SKIP_CATEGORIES.search(ev.get("category") or ""):
            continue
        fam, hhmm, key = classify(ev)
        if fam == "R":
            for m in ev.get("markets") or []:
                if (m.get("result") or "").lower() in ("yes", "no"):
                    r_targets.append((ev, m, hhmm, key))
        elif fam == "G":
            series = (ev.get("event_ticker") or "").split("-", 1)[0]
            fam_events[series].append(ev)

    g_pins: dict[str, tuple[float, str]] = {}
    g_lookup: dict[str, tuple[dict, str]] = {}
    for series, evs in fam_events.items():
        pins = glacial_pins(evs)
        g_pins.update(pins)
        for ev in evs:
            for m in ev.get("markets") or []:
                if m.get("ticker") in pins:
                    g_lookup[m["ticker"]] = (m, series)
    print(f"R-family (release) settled markets: {len(r_targets)}; "
          f"G-family pinned markets: {len(g_pins)} across {len(fam_events)} series")

    acc = {"post": [], "pre": [], "red_flags": []}
    week_notional: dict[int, float] = {}
    scanned = 0
    for ev, m, hhmm, key in r_targets:
        if scanned >= args.max_markets:
            break
        pin = release_pin_ts(m, hhmm)
        close = _ts(m.get("close_time"))
        if not pin or not close or close <= pin:
            continue  # market closed before the release -> no post-pin window
        rows = trades(m["ticker"], args.trade_pages)
        if rows:
            scanned += 1
            score_trades(rows, (m.get("result") or "").lower(), pin,
                         (m.get("result") or "").lower(), acc, key, week_notional)
    for ticker, (pin_ts_v, side) in g_pins.items():
        if scanned >= args.max_markets:
            break
        m, series = g_lookup[ticker]
        rows = trades(ticker, args.trade_pages)
        if rows:
            scanned += 1
            score_trades(rows, (m.get("result") or "").lower(), pin_ts_v, side,
                         acc, f"g:{series}", week_notional)
    print(f"markets with tape scanned: {scanned}")

    post_ev, post_n = wavg(acc["post"])
    pre_ev, pre_n = wavg(acc["pre"])
    print("\n== pooled ==")
    print(f"post-pin: EV {post_ev:+.2f} c/ct on n={post_n} contracts "
          f"({len(acc['post'])} trades)")
    print(f"pre-pin favorite-buy control (>=85c): EV {pre_ev:+.2f} c/ct on n={pre_n}")

    print("\n== per family (post-pin) ==")
    fams = defaultdict(list)
    for e, c, f in acc["post"]:
        fams[f].append((e, c, f))
    fam_pass = 0
    for f, rows in sorted(fams.items()):
        ev_f, n_f = wavg(rows)
        ok = ev_f >= 3.0 and n_f >= 30
        fam_pass += ok
        print(f"  {f:12s} EV {ev_f:+.2f} c/ct  n={n_f}  {'CLEARS P1 bar' if ok else ''}")

    weeks = max(len(week_notional), 1)
    wk_avg = sum(week_notional.values()) / weeks
    print(f"\npost-pin notional at >=2c discount: ${sum(week_notional.values()):.0f} "
          f"over {weeks} active weeks -> ${wk_avg:.0f}/week")

    if acc["red_flags"]:
        print(f"\n!! RED FLAGS — pinned side LOST on {len(acc['red_flags'])} trades "
              f"(pin logic or source assumption wrong; investigate before ANY promotion):")
        seen = set()
        for tk, f, side, res, cost in acc["red_flags"][:10]:
            if tk not in seen:
                seen.add(tk)
                print(f"   {tk} fam={f} pinned={side} result={res} cost={cost:.0f}c")

    print("\n== pre-registered verdicts (thresholds fixed 2026-07-10; do not re-scope) ==")
    p1 = "PASS" if (post_ev >= 3.0 and post_n >= 100) else ("KILL" if post_ev < 1.5 else "GREY")
    p2 = "PASS" if post_ev - pre_ev >= 2.0 else "FAIL"
    p3 = "PASS" if fam_pass >= 2 else "FAIL"
    p4 = "PASS" if wk_avg >= 200 else "FAIL"
    print(f"P1 post-pin EV >= +3c (n>=100, kill<1.5c): {p1}  ({post_ev:+.2f}c, n={post_n})")
    print(f"P2 post-pin beats pre-pin favorite control by >=2c: {p2} "
          f"({post_ev - pre_ev:+.2f}c)")
    print(f"P3 >=2 families clear the bar: {p3}  ({fam_pass} families)")
    print(f"P4 >=$200/week post-pin notional at >=2c: {p4}  (${wk_avg:.0f}/wk)")
    print(f"DECISION (P1 & P2 & (P3|P4)): "
          f"{'PROMOTE to paper book' if p1 == 'PASS' and p2 == 'PASS' and (p3 == 'PASS' or p4 == 'PASS') else 'do not promote'}")
    if acc["red_flags"]:
        print("NOTE: red flags above OVERRIDE any promote — resolve them first.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
