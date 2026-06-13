"""Entry study: three ways to squeeze P&L out of the weather books — measured, like
the TP/SL sweep, by replaying what we already collected rather than running new books.

The exit sweep showed exits only modulate the bleed (no SL/TP combo turns a losing
entry profitable), so every idea here attacks the ENTRY:

A) CALIBRATION — is the market's price honest? For each settled event's bucket-ladder
   snapshot at the entry windows, compare each bucket's implied probability (price) to
   how often it actually won. A systematic gap (favorite-longshot bias) is the classic
   structural edge in prediction markets, and it dictates both a skip rule (only trade
   price bands where the market is wrong) and a side rule (buy NO on overpriced bands).

B) PRICE-BAND P&L — same question through our own books: realized P&L of the settled
   paper trades bucketed by entry price. Uses ALL settled trades (no price path needed),
   so it has the largest sample of any analysis here.

C) OBS-CONFIRMED ENTRY — the station's running max/min is a hard one-sided bound on the
   day's extreme (the high can only go UP from the running max). Simulate: after a local
   cutoff hour, buy the bucket containing the running extreme if its ask is still under a
   cap — i.e. trade the market's lag behind the thermometer, an information edge.

D) LIMIT ENTRY — the books pay the ask; the ~4-5c round-trip cost IS the bleed. Simulate
   posting a limit k cents below the entry ask, filled only if a later snapshot's ask
   trades at/through it. Reports fill rate and P&L both per-fill and including misses.
   (Caveats: queue position unmodeled, and fills are adversely selected — the limit fills
   exactly when the market moves against the trade — so treat improvements skeptically.)

Read-only and self-contained (stdlib + psycopg only) so it runs on the ops runner:
    {"type": "script", "name": "weather_entry_study"}
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

WINDOWS = (20.0, 14.0, 8.0)
WINDOW_TOLERANCE_H = 2.0
PRICE_BANDS = ((1, 20), (20, 40), (40, 60), (60, 80), (80, 99))

# Mirrors kalshi_bot/weather/cities.py.
CITY_TZ = {
    "NYC": "America/New_York", "CHI": "America/Chicago", "MIA": "America/New_York",
    "AUS": "America/Chicago", "LAX": "America/Los_Angeles", "PHIL": "America/New_York",
    "DEN": "America/Denver",
}

BOOKS = {
    "low": {"fav": "weather_low_fav", "nws": "weather_low_nws", "cal": "weather_low_cal",
            "pm": "weather_low_pm", "obs": "weather_low_obs", "dist": "weather_low_dist"},
    "high": {"fav": "weather_fav", "nws": "weather_nws", "cal": "weather_cal",
             "pm": "weather_pm", "cwin": "weather_cwin", "obs": "weather_obs",
             "dist": "weather_dist"},
}

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def book_of(strategy: str | None) -> tuple[str, str] | None:
    s = strategy or ""
    for kind in ("low", "high"):
        for book, prefix in BOOKS[kind].items():
            if s.startswith(prefix):
                return (kind, book)
    return None


def fee_cents(price_cents: float, enabled: bool = True) -> float:
    """Kalshi fee in cents at qty=1 (mirrors paper/engine.py::kalshi_fee)."""
    if not enabled:
        return 0.0
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


def value_in_bucket(value: float, low: float | None, high: float | None) -> bool:
    """Same rounding semantics as kalshi_bot/weather/buckets.py::forecast_in_bucket."""
    r = round(value)
    if low is not None and r < low:
        return False
    if high is not None and r > high:
        return False
    return True


def band_of(price: float) -> tuple[int, int] | None:
    for lo, hi in PRICE_BANDS:
        if lo <= price < hi or (hi == 99 and price >= 99):
            return (lo, hi)
    return None


# --- shared snapshot helpers (mirrors weather_model_check.py) ---------------------


@dataclass
class Bucket:
    market_ticker: str | None
    low_f: float | None
    high_f: float | None
    yes_bid: float | None
    yes_ask: float | None
    mid: float | None


@dataclass
class Cycle:
    captured_at: datetime
    hours_to_close: float | None
    buckets: list[Bucket]


def cluster_cycles(rows: list[dict], gap_seconds: float = 90.0) -> list[Cycle]:
    rows = sorted(rows, key=lambda r: r["captured_at"])
    cycles: list[Cycle] = []
    group: list[dict] = []
    for r in rows:
        if group and (r["captured_at"] - group[-1]["captured_at"]).total_seconds() > gap_seconds:
            cycles.append(_make_cycle(group))
            group = []
        group.append(r)
    if group:
        cycles.append(_make_cycle(group))
    return cycles


def _make_cycle(group: list[dict]) -> Cycle:
    by_ticker = {r["market_ticker"]: r for r in group}
    buckets = [
        Bucket(r["market_ticker"], r["low_f"], r["high_f"],
               r["yes_bid_cents"], r["yes_ask_cents"], r["mid_cents"])
        for r in by_ticker.values()
    ]
    htcs = [r["hours_to_close"] for r in group if r["hours_to_close"] is not None]
    return Cycle(
        captured_at=max(r["captured_at"] for r in group),
        hours_to_close=max(htcs) if htcs else None,
        buckets=buckets,
    )


def pick_cycle_for_window(cycles, window_h, tolerance_h=WINDOW_TOLERANCE_H):
    eligible = [c for c in cycles if c.hours_to_close is not None and c.hours_to_close <= window_h]
    if not eligible:
        return None
    best = max(eligible, key=lambda c: c.hours_to_close)
    if window_h - best.hours_to_close > tolerance_h:
        return None
    return best


# --- A) calibration -----------------------------------------------------------------


def calibration(settlements: list[dict], ladders: dict[str, list[dict]]):
    """(window, band) -> [n, implied_prob_sum, wins, yes_ev_cents_sum] over every
    priced bucket at every window snapshot of every settled event."""
    out: dict[tuple[float, tuple[int, int]], list[float]] = {}
    for st in settlements:
        rows = ladders.get(st["event_ticker"]) or []
        if not rows:
            continue
        cycles = cluster_cycles(rows)
        for w in WINDOWS:
            cycle = pick_cycle_for_window(cycles, w)
            if cycle is None:
                continue
            for b in cycle.buckets:
                if b.mid is None or not (1 <= b.mid <= 99):
                    continue
                band = band_of(b.mid)
                if band is None:
                    continue
                won = b.market_ticker == st["winning_ticker"]
                cell = out.setdefault((w, band), [0, 0.0, 0, 0.0])
                cell[0] += 1
                cell[1] += b.mid / 100.0
                cell[2] += int(won)
                if b.yes_ask is not None and 1 <= b.yes_ask <= 99:
                    cell[3] += (100.0 if won else 0.0) - b.yes_ask - fee_cents(b.yes_ask)
    return out


def report_calibration(cal) -> None:
    print("\n=== A) Market calibration — implied price vs actual win rate ===")
    if not cal:
        print("  (no settled events with ladder coverage yet)")
        return
    print("  (actual > implied -> buckets in this band are UNDERpriced: buying YES has edge;")
    print("   actual < implied -> overpriced: the NO side has edge. yes_ev includes ask+fee.)")
    print(f"  {'band':>8} {'n':>5} {'implied':>8} {'actual':>7} {'gap':>6} {'yes_ev/trade':>12}")
    pooled: dict[tuple[int, int], list[float]] = {}
    for (_w, band), (n, imp, wins, ev) in cal.items():
        cell = pooled.setdefault(band, [0, 0.0, 0, 0.0])
        cell[0] += n
        cell[1] += imp
        cell[2] += wins
        cell[3] += ev
    for band in sorted(pooled):
        n, imp, wins, ev = pooled[band]
        gap = wins / n - imp / n
        print(f"  {band[0]:3d}-{band[1]:<3d} {n:6d} {imp / n:8.2f} {wins / n:7.2f}"
              f" {gap:+6.2f} {ev / n:+10.1f}c")
    for w in WINDOWS:
        cells = {band: v for (win, band), v in cal.items() if win == w}
        if not cells:
            continue
        line = "   ".join(
            f"{band[0]}-{band[1]}: {v[2]}/{v[0]} (imp {v[1] / v[0]:.2f})"
            for band, v in sorted(cells.items())
        )
        print(f"  h{int(w)}: {line}")


# --- B) our books by entry-price band -------------------------------------------------


def report_price_bands(trades: list[dict]) -> None:
    print("\n=== B) Our settled trades by entry-price band (all books, full history) ===")
    if not trades:
        print("  (no settled trades)")
        return
    cells: dict[tuple[str, tuple[int, int]], list[float]] = {}
    for t in trades:
        kb = book_of(t["strategy"])
        band = band_of(float(t["assumed_price"]))
        if kb is None or band is None:
            continue
        key = (f"{kb[0]} {kb[1]}", band)
        cell = cells.setdefault(key, [0, 0, 0.0])
        cell[0] += 1
        cell[1] += int(t["resolved_value"] == 100)
        cell[2] += float(t["pnl"] or 0.0) * 100.0
    print(f"  {'book':<10} {'band':>8} {'n':>4} {'win%':>5} {'pnl/trade':>9}")
    for (bk, band), (n, wins, pnl) in sorted(cells.items()):
        print(f"  {bk:<10} {band[0]:3d}-{band[1]:<3d} {n:5d} {wins / n * 100:4.0f}%"
              f" {pnl / n:+8.1f}c")


# --- C) obs-confirmed entry ------------------------------------------------------------


def obs_confirmed_entries(
    settlements, ladders, obs_paths, *, kind: str, min_local_hour: int, ask_cap: float
):
    """Simulate: after min_local_hour (city time), buy the bucket containing the running
    extreme at the next snapshot's ask if ask <= cap. One entry per event. Returns
    (n, wins, pnl_cents_total, ask_sum)."""
    n = wins = 0
    pnl_sum = ask_sum = 0.0
    for st in settlements:
        if (st["kind"] or "high") != kind:
            continue
        rows = ladders.get(st["event_ticker"]) or []
        path = obs_paths.get((st["city"], st["target_date"])) or []
        tz_name = CITY_TZ.get(st["city"] or "")
        if not rows or not path or not tz_name:
            continue
        tz = ZoneInfo(tz_name)
        cycles = cluster_cycles(rows)
        entry = None
        for ob in path:  # time-ordered observation snapshots
            extreme = ob["running_max_f"] if kind == "high" else ob["running_min_f"]
            if extreme is None or ob["captured_at"].astimezone(tz).hour < min_local_hour:
                continue
            cycle = next((c for c in cycles if c.captured_at > ob["captured_at"]), None)
            if cycle is None:
                continue
            bucket = next(
                (b for b in cycle.buckets if value_in_bucket(extreme, b.low_f, b.high_f)),
                None,
            )
            if bucket is None or bucket.yes_ask is None or not (1 <= bucket.yes_ask <= ask_cap):
                continue
            entry = (bucket, bucket.yes_ask)
            break
        if entry is None:
            continue
        bucket, ask = entry
        won = bucket.market_ticker == st["winning_ticker"]
        n += 1
        wins += int(won)
        ask_sum += ask
        pnl_sum += (100.0 if won else 0.0) - ask - fee_cents(ask)
    return n, wins, pnl_sum, ask_sum


def report_obs_confirmed(settlements, ladders, obs_paths) -> None:
    print("\n=== C) Obs-confirmed entry — buy the bucket the thermometer already points to ===")
    print("  (running max/min is a one-sided bound: entered bucket can only be overtaken,"
          " never undercut)")
    print(f"  {'kind':<5} {'after':>6} {'ask<=':>6} {'n':>3} {'win%':>5} {'avg_ask':>7}"
          f" {'pnl/trade':>9}")
    any_rows = False
    for kind, hours in (("high", (13, 15, 17)), ("low", (7, 9, 11))):
        for h in hours:
            for cap in (70.0, 85.0, 95.0):
                n, wins, pnl, asks = obs_confirmed_entries(
                    settlements, ladders, obs_paths,
                    kind=kind, min_local_hour=h, ask_cap=cap,
                )
                if not n:
                    continue
                any_rows = True
                print(f"  {kind:<5} {h:5d}h {cap:6.0f} {n:3d} {wins / n * 100:4.0f}%"
                      f" {asks / n:7.1f} {pnl / n:+8.1f}c")
    if not any_rows:
        print("  (no settled events with joint obs + ladder coverage yet)")


# --- D) limit-entry fill simulation ------------------------------------------------------


def limit_entry(trades: list[dict], ask_paths: dict[str, list[tuple]], k: float):
    """Post entry at (ask - k); filled if a later snapshot's ask <= limit, at the limit.
    Returns (n, fills, filled_pnl_cents); unfilled trades simply don't trade."""
    n = fills = 0
    filled_pnl = 0.0
    for t in trades:
        path = [
            float(a) for cap, a in ask_paths.get(t["market_ticker"], [])
            if cap > t["created_at"] and a is not None
        ]
        if not path:
            continue
        n += 1
        limit = float(t["assumed_price"]) - k
        if limit < 1:
            continue
        if any(a <= limit for a in path):
            fills += 1
            filled_pnl += float(t["resolved_value"]) - limit - fee_cents(limit)
    return n, fills, filled_pnl


def report_limit_entry(trades, ask_paths) -> None:
    print("\n=== D) Limit entry — post below the ask instead of paying it ===")
    print("  (filled only if a later snapshot's ask trades at/through the limit;"
          " fills are adversely selected — judge skeptically)")
    print(f"  {'k':>3} {'n':>4} {'fills':>5} {'fill%':>5} {'pnl/fill':>8} {'pnl_total':>9}")
    base_n = base_pnl = 0.0
    for t in trades:
        if ask_paths.get(t["market_ticker"]):
            base_n += 1
            base_pnl += (float(t["resolved_value"]) - float(t["assumed_price"])
                         - fee_cents(float(t["assumed_price"])))
    if not base_n:
        print("  (no replayable trades)")
        return
    print(f"  ask {int(base_n):4d} {int(base_n):5d}  100% {base_pnl / base_n:+7.1f}c"
          f" {base_pnl / 100.0:+8.2f}$")
    for k in (1.0, 2.0, 3.0, 5.0):
        n, fills, filled_pnl = limit_entry(trades, ask_paths, k)
        if not n:
            continue
        pf = filled_pnl / fills if fills else math.nan
        print(f"  -{k:g}c {n:4d} {fills:5d} {fills / n * 100:4.0f}% {pf:+7.1f}c"
              f" {filled_pnl / 100.0:+8.2f}$")


# --- data access ---------------------------------------------------------------------


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def fetch_all(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ticker, city, target_date, kind, winning_ticker"
            " FROM weather_settlements WHERE winning_ticker IS NOT NULL"
        )
        cols = [d.name for d in cur.description]
        settlements = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        tickers = [s["event_ticker"] for s in settlements]
        ladders: dict[str, list[dict]] = {}
        if tickers:
            cur.execute(
                "SELECT event_ticker, market_ticker, low_f, high_f, yes_bid_cents,"
                " yes_ask_cents, mid_cents, hours_to_close, captured_at"
                " FROM weather_bucket_snapshots WHERE event_ticker = ANY(%s)"
                " ORDER BY captured_at",
                (tickers,),
            )
            cols = [d.name for d in cur.description]
            for r in cur.fetchall():
                row = dict(zip(cols, r, strict=True))
                ladders.setdefault(row["event_ticker"], []).append(row)

        dates = sorted({s["target_date"] for s in settlements if s["target_date"]})
        obs_paths: dict[tuple[str, str], list[dict]] = {}
        if dates:
            cur.execute(
                "SELECT city, target_date, captured_at, running_max_f, running_min_f"
                " FROM weather_observations WHERE target_date = ANY(%s) ORDER BY captured_at",
                (dates,),
            )
            cols = [d.name for d in cur.description]
            for r in cur.fetchall():
                row = dict(zip(cols, r, strict=True))
                obs_paths.setdefault((row["city"], row["target_date"]), []).append(row)

        cur.execute(
            "SELECT market_ticker, strategy, assumed_price, resolved_value, pnl, created_at"
            " FROM paper_trades WHERE strategy LIKE 'weather%' AND status='settled'"
            " AND side='yes' AND assumed_price IS NOT NULL AND resolved_value IS NOT NULL"
        )
        cols = [d.name for d in cur.description]
        trades = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        ask_paths: dict[str, list[tuple]] = {}
        ttickers = sorted({t["market_ticker"] for t in trades})
        if ttickers:
            cur.execute(
                "SELECT market_ticker, captured_at, yes_ask_cents FROM weather_bucket_snapshots"
                " WHERE market_ticker = ANY(%s) ORDER BY captured_at",
                (ttickers,),
            )
            for ticker, cap, ask in cur.fetchall():
                ask_paths.setdefault(ticker, []).append((cap, ask))

    return {
        "settlements": settlements, "ladders": ladders,
        "obs_paths": obs_paths, "trades": trades, "ask_paths": ask_paths,
    }


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg  # deferred so the pure helpers import without the driver

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        data = fetch_all(conn)

    with_ladders = sum(1 for s in data["settlements"] if s["event_ticker"] in data["ladders"])
    print("=== Entry study: calibration, price bands, obs-confirmed entry, limit entry ===")
    print(f"  settled events: {len(data['settlements'])} (with ladder coverage: {with_ladders})"
          f"   settled trades: {len(data['trades'])}")
    print("  (coverage starts Jun 10; every section sharpens as settlements accumulate)")

    report_calibration(calibration(data["settlements"], data["ladders"]))
    report_price_bands(data["trades"])
    report_obs_confirmed(data["settlements"], data["ladders"], data["obs_paths"])
    report_limit_entry(data["trades"], data["ask_paths"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
