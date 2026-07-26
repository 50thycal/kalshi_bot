"""WIDEQUOTE census — is there CURRENT live flow crossing Kalshi's widest-spread series, or is
the cumulative volume behind them a historical artifact? (idea-model 2026-07-25, structural/
arb-adjacent run; docs/WIDEQUOTE_CENSUS.md)

`kalshi_market_survey` found a handful of high-cumulative-volume series quoting enormous average
spreads (KXGOVCA ~92c, KXMAYORLA ~80c, KXPGATOUR ~55c) -- but that volume is cumulative-to-date,
not current flow. This census answers the pre-registered gates before any thesis gets written:

  C1 -- is the spread real and CURRENT (not dead rungs dragging up a stale average)? Answered
        from THIS run's live top-of-book snapshot; full multi-day confirmation needs repeat runs
        (this prints the current read and says so explicitly, never overclaims a single-snapshot
        3-day average).
  C2 -- is there recent flow (>=100 trades/week) in markets whose spread AT TRADE TIME was wide?
  C3 -- does that flow actually cross the wide spread, or only arrive once someone else has
        already tightened the touch (>=30% of trades executed while the spread was wide)?
  C4 -- does at least one target series settle weekly-or-faster (a Kelly-sizable, statistically
        readable cadence), or is every survivor a lumpy one-off election?

C2/C3 share one no-lookahead measurement: for each trade, the "prevailing spread" is read from
the last HOURLY CANDLE that closed strictly BEFORE the trade's own timestamp (never a candle at
or after it) -- the only point-in-time proxy for "what the book looked like right before this
trade" that Kalshi's public API exposes for these thin, uncollected-orderbook-history markets.

Target series are DISCOVERED live each run (ranked by current avg spread among series with real
volume) rather than hardcoded from the 07-25 survey -- liquidity drifts; --series overrides.

Read-only public Kalshi REST only, stdlib. Reuses kalshi_arb (_close_ts, fee) and kalshi_mm
(fetch_trades, trade_yes_price) -- the same tape reader that produced the maker-sell finding.
Usage: {"type":"script","name":"kalshi_widequote_census","args":["--top-n","5"],"id":"wq-1"}
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict

import kalshi_arb as arb  # KALSHI, _close_ts, fee
import kalshi_mm as km  # fetch_trades, trade_yes_price
import xvenue_leadlag as xl  # _get (browser UA + retries -> None), _num


def _series_of(event_ticker: str) -> str:
    return (event_ticker or "").split("-", 1)[0] or "?"


def discover_events(max_pages: int) -> list[dict]:
    events: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        page = xl._get(f"{arb.KALSHI}/events?status=open&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        events.extend(e for e in evs if not (e.get("series_ticker") or "").startswith("KXMVE"))
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return events


def group_by_series(events: list[dict]) -> dict[str, list[dict]]:
    """series -> [market dicts, each annotated with its parent event's close/series info]."""
    out: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        series = e.get("series_ticker") or _series_of(e.get("event_ticker") or "")
        for m in e.get("markets") or []:
            out[series].append(m)
    return out


def rank_by_current_spread(by_series: dict[str, list[dict]], min_volume: float) -> list[tuple]:
    """[(series, avg_spread_cents, total_volume, n_markets)], widest spread first, among series
    with at least `min_volume` total volume (excludes trivially-thin series from looking
    'widest' just because nobody has ever quoted them)."""
    rows = []
    for series, mkts in by_series.items():
        vol = sum(xl._num(m.get("volume_fp")) for m in mkts)
        spreads = []
        for m in mkts:
            yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
            if 0 <= yb <= ya <= 1:
                spreads.append((ya - yb) * 100.0)
        if vol < min_volume or not spreads:
            continue
        rows.append((series, sum(spreads) / len(spreads), vol, len(mkts)))
    rows.sort(key=lambda r: -r[1])
    return rows


def current_wide_fraction(mkts: list[dict], wide_threshold: float) -> tuple[float | None, int]:
    """(median spread cents among LIVE two-sided-quoted markets, count of those markets).
    'Live' excludes a fully dead 0/100 quote (no real market to cross) -- median, not mean, so
    one crazy-wide dead rung can't drag the read (the exact averaging-artifact C1 guards
    against)."""
    spreads = []
    for m in mkts:
        yb, ya = xl._num(m.get("yes_bid_dollars")), xl._num(m.get("yes_ask_dollars"))
        if not (0 <= yb <= ya <= 1):
            continue
        if yb == 0.0 and ya >= 0.99:      # both sides effectively unquoted -- not a live rung
            continue
        spreads.append((ya - yb) * 100.0)
    if not spreads:
        return None, 0
    spreads.sort()
    return spreads[len(spreads) // 2], len(spreads)


def fetch_candles(series: str, ticker: str, start_ts: int, end_ts: int) -> list[tuple[int, float, float]]:
    """[(end_period_ts, yes_bid_close_$, yes_ask_close_$)] hourly candles, ts-sorted, fail-soft."""
    data = xl._get(f"{arb.KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start_ts}&end_ts={end_ts}&period_interval=60")
    rows = []
    for c in (data or {}).get("candlesticks") or []:
        ts = int(xl._num(c.get("end_period_ts")))
        yb = (c.get("yes_bid") or {}).get("close_dollars")
        ya = (c.get("yes_ask") or {}).get("close_dollars")
        if ts and yb is not None and ya is not None:
            rows.append((ts, xl._num(yb), xl._num(ya)))
    rows.sort(key=lambda r: r[0])
    return rows


def prior_spread_cents(candles: list[tuple[int, float, float]], trade_ts: int) -> float | None:
    """The last candle that closed STRICTLY BEFORE trade_ts -- never one at or after it. No
    candle known yet at that point in time -> None (excluded from C2/C3, not guessed)."""
    best = None
    for ts, yb, ya in candles:
        if ts < trade_ts and (best is None or ts > best[0]):
            best = (ts, yb, ya)
    if best is None:
        return None
    _, yb, ya = best
    return (ya - yb) * 100.0 if ya >= yb else None


def trade_created_ts(t: dict) -> int:
    """Unix timestamp from a trade's created_time (ISO8601), 0 if unparseable."""
    return arb._close_ts({"close_time": t.get("created_time")})


def market_tape_stats(series: str, ticker: str, window_start_ts: int, now_ts: int,
                      wide_threshold: float, trade_cap: int) -> tuple[int, int]:
    """(n_trades_in_window, n_with_wide_prior_spread) for one market."""
    trades = km.fetch_trades(ticker, cap=trade_cap)
    in_window = [t for t in trades if trade_created_ts(t) >= window_start_ts]
    if not in_window:
        return 0, 0
    candles = fetch_candles(series, ticker, window_start_ts - 86400, now_ts)
    if not candles:
        return len(in_window), 0
    wide = 0
    for t in in_window:
        ps = prior_spread_cents(candles, trade_created_ts(t))
        if ps is not None and ps >= wide_threshold:
            wide += 1
    return len(in_window), wide


def cadence_gap_days(mkts: list[dict]) -> float | None:
    """Smallest gap (days) between two DISTINCT settle dates among this series' markets, or
    None if fewer than 2 distinct dates are observed (can't judge cadence from one date)."""
    dates = sorted({arb._close_ts(m) // 86400 for m in mkts if arb._close_ts(m)})
    if len(dates) < 2:
        return None
    return float(min(b - a for a, b in zip(dates, dates[1:], strict=False)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-n", type=int, default=5, help="target series to census, by current spread")
    ap.add_argument("--series", type=str, default="",
                    help="comma-separated series override instead of live discovery")
    ap.add_argument("--min-volume", type=float, default=1000.0,
                    help="volume floor for a series to be eligible for discovery ranking")
    ap.add_argument("--days", type=float, default=30.0, help="trailing window for the trade tape")
    ap.add_argument("--per-series-markets", type=int, default=10,
                    help="cap on markets (by volume) tape-pulled per series -- bounds API cost")
    ap.add_argument("--wide-threshold", type=float, default=20.0, help="cents; the 'wide' cutoff")
    ap.add_argument("--trade-cap", type=int, default=2000, help="max trades fetched per market")
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args(argv)

    now_ts = int(time.time())
    window_start_ts = now_ts - int(args.days * 86400)

    events = discover_events(args.max_pages)
    by_series = group_by_series(events)
    print(f"=== WIDEQUOTE census — {len(events)} open events, {len(by_series)} series ===\n")

    if args.series.strip():
        targets = [s.strip() for s in args.series.split(",") if s.strip()]
        print(f"  target series (override): {targets}\n")
    else:
        ranked = rank_by_current_spread(by_series, args.min_volume)
        targets = [s for s, _spr, _vol, _n in ranked[: args.top_n]]
        print(f"  target series (discovered, widest current spread, volume>={args.min_volume:.0f}):")
        for s, spr, vol, n in ranked[: args.top_n]:
            print(f"    {s:>20}  avg_spread={spr:5.1f}c  volume={vol:,.0f}  markets={n}")
        print()

    overall = []
    for series in targets:
        mkts = by_series.get(series, [])
        if not mkts:
            print(f"  {series}: 0 markets found on this snapshot -- skipped\n")
            continue

        c1_median, c1_n_live = current_wide_fraction(mkts, args.wide_threshold)

        picked = sorted(mkts, key=lambda m: -xl._num(m.get("volume_fp")))[: args.per_series_markets]
        total_trades = total_wide = 0
        for m in picked:
            tk = m.get("ticker") or ""
            if not tk:
                continue
            n, w = market_tape_stats(series, tk, window_start_ts, now_ts,
                                     args.wide_threshold, args.trade_cap)
            total_trades += n
            total_wide += w

        trades_per_week = total_wide / (args.days / 7.0) if args.days else 0.0
        c3_frac = (total_wide / total_trades) if total_trades else 0.0
        gap = cadence_gap_days(mkts)

        c1_pass = c1_median is not None and c1_median >= args.wide_threshold
        c2_pass = trades_per_week >= 100.0
        c3_pass = total_trades > 0 and c3_frac >= 0.30
        c4_pass = gap is not None and gap <= 8.0

        print(f"  --- {series} ({len(mkts)} markets, {len(picked)} tape-sampled) ---")
        print(f"    C1 (current spread real+wide): median={c1_median if c1_median is None else f'{c1_median:.1f}c'}"
              f" over {c1_n_live} live-quoted markets -> {'PASS' if c1_pass else 'FAIL'}"
              f"  [single-snapshot only -- confirm over >=3 days with repeat runs]")
        print(f"    C2 (recent wide-spread flow): {total_wide} wide-spread trades / {args.days:.0f}d"
              f" = {trades_per_week:.1f}/week (of {total_trades} total trades sampled)"
              f" -> {'PASS' if c2_pass else 'FAIL'}")
        print(f"    C3 (crosses the wide spread, not just the tightened touch): {c3_frac * 100:.0f}%"
              f" of trades at wide prior spread -> {'PASS' if c3_pass else 'FAIL'}")
        print(f"    C4 (weekly-or-faster cadence): min gap between settle dates ="
              f" {gap if gap is None else f'{gap:.0f}d'} -> {'PASS' if c4_pass else 'FAIL'}")
        all_pass = c1_pass and c2_pass and c3_pass and c4_pass
        print(f"    ALL FOUR: {'PASS -- promote to full thesis' if all_pass else 'not all pass'}\n")
        overall.append((series, c1_pass, c2_pass, c3_pass, c4_pass))

    print("=== verdict ===")
    any_all4 = [s for s, c1, c2, c3, c4 in overall if c1 and c2 and c3 and c4]
    any_c1c2c3_not_c4 = [s for s, c1, c2, c3, c4 in overall if c1 and c2 and c3 and not c4]
    if any_all4:
        print(f"  PROMOTE-candidate: {any_all4} clear C1-C4 -- write the full thesis"
              f" (mandatory adverse-selection haircut, per docs/WIDEQUOTE_CENSUS.md).")
    elif any_c1c2c3_not_c4:
        print(f"  CAPACITY-BLOCKED HOLD: {any_c1c2c3_not_c4} show real current wide-spread flow"
              f" but fail the weekly-cadence gate -- real edge, unreadable track record. Do not"
              f" build; not a kill.")
    elif not overall:
        print("  No target series had any markets on this snapshot -- re-run.")
    else:
        failed_c1 = [s for s, c1, *_ in overall if not c1]
        failed_c2 = [s for s, _c1, c2, *_ in overall if not c2]
        print(f"  RULED OUT (this run): no target series clears C1-C4. C1-failed: {failed_c1 or 'none'};"
              f" C2-failed (no real flow): {failed_c2 or 'none'}. Per the decision rule, a C1 or C2"
              f" failure closes the wide-spread pocket for these series -- the cumulative volume"
              f" `kalshi_market_survey` found is a historical artifact, not current flow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
