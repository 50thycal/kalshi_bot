"""WCPROP thesis validation probe (docs/IDEA_MODEL_20260704.md) — World Cup
cross-market coherence: does the tournament-WINNER ladder lag decisive MATCH results?

For each decisively settled World Cup match/advance market (KXWCGAME / KXWCROUND), find
the moment T the result became known, then watch the involved teams' KXMENWORLDCUP
winner-ladder contracts strictly AFTER T (1-min public candlesticks, no lookahead):

  P1  The winner market lags: median completion of the eventual repricing within the
      first 5 min < 70%. KILL if >= 90% (efficient — priced like cross-city lead-lag).
  P2  The lag clears cost: entering at the +5-min winner quote (taker, at the ask in
      the move direction) and exiting at T+H nets >= +3c/contract after both-leg
      worst-case taker fees. KILL if < +2c.
  P3  Not noise/illiquidity: the effect survives on entries where the winner contract's
      quoted spread at entry is <= 5c. KILL if it only appears on wide quotes.
  Decision: paper book `wcprop` only if P1 AND P2 AND P3. P1 fail -> cross-market
  coherence in sports is priced; close the family.

Result-known time T: detected from the settled match market's own candles — the first
minute the YES-settled side's mid pins >= --pin-cents and never leaves again (a goal
decides regulation; a bracket decides advancement); falls back to the market close_time
when no pin is detectable. Repricing horizon H (--horizon-min, default 120) defines the
"eventual" level; teams whose winner contract moved < --min-move-c over [T, T+H] are
skipped (nothing to reprice — e.g. a group-stage draw between longshots).

Read-only public Kalshi APIs, stdlib only (reuses xvenue_leadlag helpers). Usage:
    python scripts/xmarket_wc.py --days 21
    # ops channel:
    {"type":"script","name":"xmarket_wc","args":["--days","21","--horizon-min","120"]}
"""

from __future__ import annotations

import argparse
import calendar
import math
import time
from collections import defaultdict
from statistics import median

import xvenue_game as xg  # _norm, _ticker_day
import xvenue_leadlag as xl  # _get, _num, CODE2NAME, _norm_country

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
WINNER_SERIES = "KXMENWORLDCUP"
MATCH_SERIES = ("KXWCGAME", "KXWCROUND")


def taker_fee_c(p_c: float) -> float:
    return math.ceil(7.0 * (p_c / 100.0) * (1 - p_c / 100.0))


def _iso_to_unix(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


# --- data ----------------------------------------------------------------------------


def fetch_settled_match_markets(series: str, pages: int) -> list[dict]:
    out: list[dict] = []
    for status in ("settled", "closed"):
        cursor = ""
        for _ in range(pages):
            page = xl._get(f"{KALSHI}/markets?series_ticker={series}&status={status}"
                           f"&limit=1000&cursor={cursor}")
            mkts = (page or {}).get("markets") or []
            for m in mkts:
                tk = m.get("ticker") or ""
                if not tk:
                    continue
                out.append({
                    "series": series,
                    "ticker": tk,
                    "event": tk.rsplit("-", 1)[0],
                    "team": xg._norm((m.get("yes_sub_title") or "").split(":")[-1]),
                    "result": (m.get("result") or "").lower(),
                    "close": _iso_to_unix(m.get("close_time")),
                    "day": xg._ticker_day(tk),
                })
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not mkts:
                break
    return out


def fetch_winner_map() -> dict[str, str]:
    """normalized team -> KXMENWORLDCUP market ticker (open AND settled — eliminated
    teams' contracts settle NO but their candles are exactly the repricing we measure)."""
    out: dict[str, str] = {}
    for status in ("open", "settled", "closed"):
        cursor = ""
        for _ in range(4):
            page = xl._get(f"{KALSHI}/markets?series_ticker={WINNER_SERIES}"
                           f"&status={status}&limit=200&cursor={cursor}")
            mkts = (page or {}).get("markets") or []
            for m in mkts:
                tk = m.get("ticker") or ""
                code = tk.rsplit("-", 1)[-1]
                name = xl.CODE2NAME.get(code)
                if not name:
                    sub = xl._norm_country(m.get("yes_sub_title") or "")
                    if sub in xl.CODE2NAME.values():
                        name = sub
                if name and name not in out:
                    out[name] = tk
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not mkts:
                break
    return out


def fetch_candles(series: str, ticker: str, start: int, end: int) -> dict[int, tuple]:
    """{minute_ts: (yes_bid_c, yes_ask_c)} from 1-min candlesticks (chunked under the
    ~5000-period request cap)."""
    out: dict[int, tuple] = {}
    step = 4800 * 60
    s = start
    while s < end:
        e = min(s + step, end)
        data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                       f"?start_ts={s}&end_ts={e}&period_interval=1")
        for c in (data or {}).get("candlesticks") or []:
            ts = c.get("end_period_ts")
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            if ts is None or yb is None or ya is None:
                continue
            yb_c, ya_c = xl._num(yb) * 100.0, xl._num(ya) * 100.0
            if 0 <= yb_c <= 100 and 0 <= ya_c <= 100 and ya_c >= yb_c:
                out[int(ts) // 60 * 60] = (yb_c, ya_c)
        s = e
    return out


# --- result-known time ------------------------------------------------------------------


def pin_time(candles: dict[int, tuple], pin_c: float) -> int | None:
    """First minute the mid reaches >= pin_c and never drops below pin_c - 5 afterwards
    (the market 'knowing' the result), or None if no such pin exists."""
    ts_sorted = sorted(candles)
    mids = {ts: (candles[ts][0] + candles[ts][1]) / 2.0 for ts in ts_sorted}
    candidate = None
    for ts in ts_sorted:
        if mids[ts] >= pin_c:
            if candidate is None:
                candidate = ts
        elif mids[ts] < pin_c - 5.0:
            candidate = None  # un-pinned again; the earlier pin was premature
    return candidate


def at_or_before(candles: dict[int, tuple], ts: int, tol_s: int):
    best = None
    for t in candles:
        if t <= ts and ts - t <= tol_s and (best is None or t > best):
            best = t
    return (candles[best], best) if best is not None else (None, None)


def first_after(candles: dict[int, tuple], ts: int, tol_s: int):
    best = None
    for t in candles:
        if t > ts and t - ts <= tol_s and (best is None or t < best):
            best = t
    return (candles[best], best) if best is not None else (None, None)


def last_in_window(candles: dict[int, tuple], lo_exclusive: int, hi_inclusive: int):
    """Latest candle with lo < t <= hi — used for completion@k so the measurement never
    reuses the T candle itself (which straddles the result)."""
    best = None
    for t in candles:
        if lo_exclusive < t <= hi_inclusive and (best is None or t > best):
            best = t
    return candles[best] if best is not None else None


def _mid(q: tuple) -> float:
    return (q[0] + q[1]) / 2.0


# --- main ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=float, default=21.0, help="look back N days for matches")
    ap.add_argument("--pages", type=int, default=3)
    ap.add_argument("--horizon-min", type=int, default=120,
                    help="H: 'eventual repricing' horizon after T (minutes)")
    ap.add_argument("--min-move-c", type=float, default=3.0,
                    help="skip teams whose winner contract moved less than this over [T,T+H]")
    ap.add_argument("--pin-cents", type=float, default=95.0,
                    help="match-market mid that counts as 'result known'")
    ap.add_argument("--max-events", type=int, default=80)
    ap.add_argument("--spread-cap-c", type=float, default=5.0, help="P3 liquidity filter")
    args = ap.parse_args(argv)
    now = int(time.time())
    since = now - int(args.days * 86400)
    H = args.horizon_min * 60

    # 1) decisive match settlements, grouped per event ---------------------------------
    markets: list[dict] = []
    for series in MATCH_SERIES:
        markets.extend(fetch_settled_match_markets(series, args.pages))
    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        if m["close"] and m["close"] >= since:
            by_event[m["event"]].append(m)
    events = []
    for ev, mkts in sorted(by_event.items()):
        yes_side = [m for m in mkts if m["result"] == "yes"]
        teams = sorted({m["team"] for m in mkts if m["team"] and m["team"] != "tie"})
        if not yes_side or not teams:
            continue  # not decisively settled yet, or no team names parsed
        events.append({"event": ev, "series": mkts[0]["series"], "teams": teams,
                       "yes_mkt": yes_side[0], "close": max(m["close"] for m in mkts)})
    events = events[-args.max_events:]
    winner_map = fetch_winner_map()
    print("=== WCPROP — match-result -> winner-ladder propagation "
          f"({args.days:g}d lookback, H={args.horizon_min}m) ===")
    print(f"  decisive settled events: {len(events)} "
          f"(KXWCGAME+KXWCROUND)   winner-ladder teams mapped: {len(winner_map)}")
    if not events:
        print("  No settled match events in the window — widen --days once matches settle.")
        return 0

    # 2) per (event, involved team): completion + residual capture ----------------------
    rows = []  # dicts: event, team, series, t_src, comp1/5/15, resid, spread, direction
    for ev in events:
        m = ev["yes_mkt"]
        mc = fetch_candles(m["series"], m["ticker"], ev["close"] - 8 * 3600,
                           ev["close"] + 3600)
        t_known = pin_time(mc, args.pin_cents)
        t_src = "pin"
        if t_known is None:
            t_known, t_src = ev["close"], "close_time"
        for team in ev["teams"]:
            wtk = winner_map.get(team) or winner_map.get(xl._norm_country(team))
            if not wtk:
                continue
            wc = fetch_candles(WINNER_SERIES, wtk, t_known - 40 * 60, t_known + H + 40 * 60)
            pre_q, _ = at_or_before(wc, t_known, 30 * 60)
            end_q, _ = at_or_before(wc, t_known + H, 45 * 60)
            if pre_q is None or end_q is None:
                continue
            p_pre, p_end = _mid(pre_q), _mid(end_q)
            total = p_end - p_pre
            if abs(total) < args.min_move_c:
                continue
            comp = {}
            for k in (1, 5, 15):
                q = last_in_window(wc, t_known, t_known + k * 60)
                comp[k] = ((_mid(q) - p_pre) / total) if q is not None else None
            entry_q, entry_ts = first_after(wc, t_known + 5 * 60, 10 * 60)
            resid = spread = None
            if entry_q is not None:
                spread = entry_q[1] - entry_q[0]
                if total > 0:  # buy YES at ask, exit at bid at T+H
                    entry, exitp = entry_q[1], end_q[0]
                    resid = (exitp - entry) - taker_fee_c(entry) - taker_fee_c(exitp)
                else:          # buy NO at (100-bid), exit NO at (100-ask) at T+H
                    entry, exitp = 100.0 - entry_q[0], 100.0 - end_q[1]
                    resid = (exitp - entry) - taker_fee_c(entry) - taker_fee_c(exitp)
            rows.append({
                "event": ev["event"], "series": ev["series"], "team": team,
                "t_src": t_src, "total": total, "comp": comp, "resid": resid,
                "spread": spread, "direction": "up" if total > 0 else "down",
            })

    if not rows:
        print("  No measurable (event, team) repricings — check winner-map coverage above.")
        return 0

    def _pct(v: float | None) -> str:
        return f"{v * 100:4.0f}%" if v is not None else "  n/a"

    print(f"\n  {'event':>26} {'team':>13} {'src':>5} {'move':>6} "
          f"{'c@1':>5} {'c@5':>5} {'c@15':>5} {'resid':>7} {'sprd':>5}")
    for r in rows:
        resid = f"{r['resid']:+6.1f}c" if r["resid"] is not None else "    n/a"
        sprd = f"{r['spread']:4.1f}" if r["spread"] is not None else " n/a"
        print(f"  {r['event']:>26} {r['team']:>13} {r['t_src']:>5} {r['total']:+5.1f}c "
              f"{_pct(r['comp'].get(1)):>5} {_pct(r['comp'].get(5)):>5} "
              f"{_pct(r['comp'].get(15)):>5} {resid:>7} {sprd:>5}")

    # 3) aggregates + pre-registered verdicts -------------------------------------------
    def agg(rs: list[dict], label: str) -> tuple[float | None, float | None, int]:
        comp5 = [r["comp"][5] for r in rs if r["comp"].get(5) is not None]
        resid = [r["resid"] for r in rs if r["resid"] is not None]
        m5 = median(comp5) if comp5 else None
        mr = median(resid) if resid else None
        if m5 is not None or mr is not None:
            m5s = f"{m5 * 100:.0f}%" if m5 is not None else "n/a"
            mrs = f"{mr:+.2f}c" if mr is not None else "n/a"
            print(f"    {label:>26}: n={len(rs):3d}  median c@5={m5s:>5}  "
                  f"median resid={mrs:>8}")
        return m5, mr, len(resid)

    print("\n  --- aggregates ---")
    m5_all, resid_all, n_resid = agg(rows, "ALL")
    tight = [r for r in rows if r["spread"] is not None and r["spread"] <= args.spread_cap_c]
    m5_t, resid_t, n_t = agg(tight, f"spread<={args.spread_cap_c:g}c (P3)")
    for series in MATCH_SERIES:
        agg([r for r in rows if r["series"] == series], series)
    for d in ("up", "down"):
        agg([r for r in rows if r["direction"] == d], f"direction={d}")

    print("\n  === PRE-REGISTERED VERDICTS (docs/IDEA_MODEL_20260704.md — WCPROP) ===")
    if len(rows) < 15:
        print(f"  *** SAMPLE TOO SMALL (n={len(rows)} < 15) — verdicts are PROVISIONAL; "
              "re-run as the tournament progresses. ***")
    if m5_all is not None:
        v = ("PASS" if m5_all < 0.70 else
             "KILL (efficient)" if m5_all >= 0.90 else "GREY (70-90%)")
        print(f"  P1 median 5-min completion < 70%: {m5_all * 100:.0f}% -> {v}")
    else:
        print("  P1: no completion measurements yet")
    if resid_all is not None:
        v = "PASS" if resid_all >= 3.0 else ("KILL" if resid_all < 2.0 else "GREY (2-3c)")
        print(f"  P2 median residual (+5m entry, net both-leg fees) >= +3c: "
              f"{resid_all:+.2f}c (n={n_resid}) -> {v}")
    else:
        print("  P2: no residual measurements yet")
    if resid_t is not None:
        v = "PASS" if resid_t >= 2.0 else "KILL (only wide quotes carry it)"
        print(f"  P3 survives spread<={args.spread_cap_c:g}c filter: {resid_t:+.2f}c "
              f"(n={n_t}) -> {v}")
    else:
        print("  P3: no tight-spread entries measured yet")
    print("  Decision rule: paper book `wcprop` only if P1 AND P2 AND P3; "
          "P1 fail closes the cross-market-coherence family for sports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
