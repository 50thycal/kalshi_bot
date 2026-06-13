"""Entry-window sweep: what hours-to-close is the best moment to ENTER? Measured by
replaying the collected bucket-ladder snapshots — the entry analog of the TP/SL sweep,
so we map the P&L-vs-entry-hour curve continuously instead of running a live book per
window for months.

For every settled event with ladder coverage, at each candidate window we take the
snapshot nearest that hours-to-close and simulate two books, held to settlement:
  - FAVORITE: buy the max-mid bucket at that snapshot (the market's pick).
  - NWS:      buy the bucket containing the event's morning NWS forecast (a fixed
              bucket per event; only the entry ask moves with the window).
Both buy YES at the snapshot ask, pay the Kalshi entry fee, and settle 0/100 — exactly
how the live books score, so the curve says where to put WEATHER_ENTRY_HOURS.

Read-only, stdlib + psycopg. Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_window_sweep.py
    {"type": "script", "name": "weather_window_sweep", "args": ["--windows", "24,12,6,2"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from datetime import timezone

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

DEFAULT_WINDOWS = (24, 22, 20, 18, 16, 14, 12, 10, 8, 6, 4, 2)
WINDOW_TOLERANCE_H = 1.5  # only score a window if a snapshot sits within this of it
MIN_PER_CELL = 8  # below this a (book, window) per-trade is noise — flag it


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


@dataclass
class Bucket:
    market_ticker: str | None
    low_f: float | None
    high_f: float | None
    yes_ask: float | None
    mid: float | None


@dataclass
class Cycle:
    hours_to_close: float
    buckets: list[Bucket]


def cluster_cycles(rows: list[dict], gap_seconds: float = 90.0) -> list[Cycle]:
    """Group ladder rows into per-cycle snapshots (one insert batch lands within
    microseconds; cycles are minutes apart)."""
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
    return [c for c in cycles if c is not None]


def _make_cycle(group: list[dict]) -> Cycle | None:
    by_ticker = {r["market_ticker"]: r for r in group}
    htcs = [r["hours_to_close"] for r in group if r["hours_to_close"] is not None]
    if not htcs:
        return None
    buckets = [
        Bucket(r["market_ticker"], r["low_f"], r["high_f"], r["yes_ask_cents"], r["mid_cents"])
        for r in by_ticker.values()
    ]
    return Cycle(hours_to_close=max(htcs), buckets=buckets)


def pick_cycle_nearest(cycles: list[Cycle], window_h: float) -> Cycle | None:
    """The snapshot whose hours-to-close is closest to the window, within tolerance."""
    if not cycles:
        return None
    best = min(cycles, key=lambda c: abs(c.hours_to_close - window_h))
    if abs(best.hours_to_close - window_h) > WINDOW_TOLERANCE_H:
        return None
    return best


def _entry_pnl(bucket: Bucket, winner_ticker, fees: bool) -> float | None:
    """YES at the bucket's ask, held to settlement (0/100), entry fee only."""
    ask = bucket.yes_ask
    if ask is None or not (1 <= ask <= 99):
        return None
    won = bucket.market_ticker == winner_ticker
    return (100.0 if won else 0.0) - ask - fee_cents(ask, fees)


def favorite_bucket(cycle: Cycle) -> Bucket | None:
    priced = [b for b in cycle.buckets if b.mid is not None]
    return max(priced, key=lambda b: b.mid) if priced else None


def forecast_bucket(cycle: Cycle, forecast_f: float | None) -> Bucket | None:
    if forecast_f is None:
        return None
    for b in cycle.buckets:
        if value_in_bucket(forecast_f, b.low_f, b.high_f):
            return b
    return None


def sweep(events: list[dict], windows: list[float], fees: bool):
    """events: [{kind, city, winner, forecast, cycles}]. Returns nested dict
    cell[book][(kind, window)] = [n, wins, pnl_cents] and a per-(city,window) favorite
    breakdown cell_city[(kind, city, window)] = [n, wins, pnl_cents]."""
    cell: dict[str, dict[tuple[str, float], list[float]]] = {"fav": {}, "nws": {}}
    cell_city: dict[tuple[str, str, float], list[float]] = {}
    for ev in events:
        kind, city, winner = ev["kind"], ev["city"], ev["winner"]
        for w in windows:
            cyc = pick_cycle_nearest(ev["cycles"], w)
            if cyc is None:
                continue
            picks = {"fav": favorite_bucket(cyc), "nws": forecast_bucket(cyc, ev["forecast"])}
            for book, bucket in picks.items():
                if bucket is None:
                    continue
                pnl = _entry_pnl(bucket, winner, fees)
                if pnl is None:
                    continue
                c = cell[book].setdefault((kind, w), [0, 0, 0.0])
                c[0] += 1
                c[1] += int(bucket.market_ticker == winner)
                c[2] += pnl
                if book == "fav":
                    cc = cell_city.setdefault((kind, city or "?", w), [0, 0, 0.0])
                    cc[0] += 1
                    cc[1] += int(bucket.market_ticker == winner)
                    cc[2] += pnl
    return cell, cell_city


def _row(n, wins, cents):
    wr = f"{wins / n * 100:.0f}%" if n else "n/a"
    pt = f"{cents / n:+.1f}c" if n else "n/a"
    return n, wr, pt, (cents / n if n else None)


def report(cell, cell_city, windows, n_events, *, source: str = "live", show_nws: bool = True) -> None:
    tag = "BACKFILL (Kalshi REST history, hourly candles, market-only)" if source == "backfill" \
        else "live-collected 5-min ladders"
    print("=== Entry-window sweep — favorite"
          + (" & nws" if show_nws else "")
          + f" by hours-to-close (fees on, YES at ask, held to settlement) [{tag}] ===")
    print(f"  settled events with ladder coverage: {n_events}")
    if source == "backfill":
        print("  (backfill has no NWS forecast history, so this is the favorite book only;"
              " hourly candle granularity)")
    else:
        print("  (snapshots start ~Jun 10, so later windows have more coverage than earlier"
              " ones for now; the curve sharpens as data accrues)")

    for kind in ("high", "low"):
        kwins = [w for w in windows if (kind, w) in cell["fav"] or (kind, w) in cell["nws"]]
        if not kwins:
            continue
        print(f"\n  --- {kind.upper()}: P&L per trade by entry window ---")
        header = f"  {'window':6s} | {'fav_n':>5} {'fav_win':>7} {'fav/trade':>9}"
        if show_nws:
            header += f" | {'nws_n':>5} {'nws_win':>7} {'nws/trade':>9}"
        print(header)
        best = {"fav": None, "nws": None}
        books = ("fav", "nws") if show_nws else ("fav",)
        for w in sorted(kwins, reverse=True):
            parts = [f"  h{int(w):<4d} |"]
            for book in books:
                n, wr, pt, pv = _row(*cell[book].get((kind, w), [0, 0, 0.0]))
                flag = "*" if (n and n < MIN_PER_CELL) else " "
                parts.append(f" {n:5d} {wr:>7} {pt:>8}{flag}")
                if pv is not None and n >= MIN_PER_CELL and (
                    best[book] is None or pv > best[book][0]
                ):
                    best[book] = (pv, w, n)
            print(" |".join(parts))
        for book in books:
            if best[book]:
                pv, w, n = best[book]
                print(f"  best {kind} {book} entry window: h{int(w)} ({pv:+.1f}c/trade, n={n})"
                      f"  [n>={MIN_PER_CELL} only; * = thinner]")

    # Favorite by city (the star books are city-specific, e.g. low fav).
    print("\n  --- FAVORITE P&L/trade by (kind, city) x window (n>=5) ---")
    keys = sorted({(k, c) for (k, c, _w) in cell_city})
    for kind, city in keys:
        cells = {w: cell_city[(kind, city, w)] for w in windows if (kind, city, w) in cell_city}
        ranked = sorted(
            ((cents / n, w, n) for w, (n, _wins, cents) in cells.items() if n >= 5),
            reverse=True,
        )
        if ranked:
            top = ", ".join(f"h{int(w)} {pv:+.1f}c(n{n})" for pv, w, n in ranked[:3])
            print(f"  {kind:4s} {city:5s}: best windows -> {top}")


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_events(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ticker, city, kind, winning_ticker FROM weather_settlements"
            " WHERE winning_ticker IS NOT NULL"
        )
        settlements = {
            r[0]: {"event_ticker": r[0], "city": r[1], "kind": r[2] or "high", "winner": r[3]}
            for r in cur.fetchall()
        }
        if not settlements:
            return []
        tickers = list(settlements)
        cur.execute(
            "SELECT event_ticker, market_ticker, low_f, high_f, yes_ask_cents, mid_cents,"
            " hours_to_close, captured_at FROM weather_bucket_snapshots"
            " WHERE event_ticker = ANY(%s) ORDER BY captured_at",
            (tickers,),
        )
        cols = [d.name for d in cur.description]
        ladders: dict[str, list[dict]] = {}
        for r in cur.fetchall():
            row = dict(zip(cols, r, strict=True))
            ladders.setdefault(row["event_ticker"], []).append(row)
        # earliest (morning) non-null forecast per event — the tradeable value
        cur.execute(
            "SELECT DISTINCT ON (event_ticker) event_ticker, forecast_high_f"
            " FROM weather_forecasts WHERE forecast_high_f IS NOT NULL"
            " AND event_ticker = ANY(%s) ORDER BY event_ticker, captured_at ASC",
            (tickers,),
        )
        forecasts = {r[0]: float(r[1]) for r in cur.fetchall()}

    events = []
    for tk, st in settlements.items():
        rows = ladders.get(tk)
        if not rows:
            continue
        events.append({
            "kind": st["kind"], "city": st["city"], "winner": st["winner"],
            "forecast": forecasts.get(tk), "cycles": cluster_cycles(rows),
        })
    return events


def load_events_backfill(conn) -> list[dict]:
    """Same event shape from the SEPARATE backfill_* tables (Kalshi REST history):
    reconstruct per-event hourly cycles from candles. No forecast -> favorite only.
    Only events with exactly one backfilled winner are used."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ticker, market_ticker, city, kind, result, low_f, high_f,"
            " close_time, target_date FROM backfill_weather_markets"
            " WHERE candles_fetched AND close_time IS NOT NULL AND event_ticker IS NOT NULL"
        )
        markets: dict[str, dict] = {}
        events: dict[str, dict] = {}
        for ev_tk, mk_tk, city, kind, result, lo, hi, close, tdate in cur.fetchall():
            markets[mk_tk] = {"event": ev_tk, "low": lo, "high": hi}
            e = events.setdefault(
                ev_tk, {"city": city, "kind": kind or "high", "close": close, "date": tdate,
                        "winner": None, "winners": 0}
            )
            if (result or "").lower() == "yes":
                e["winner"] = mk_tk
                e["winners"] += 1
        if not markets:
            return []
        cur.execute(
            "SELECT market_ticker, end_period_ts, price_close, yes_bid_close, yes_ask_close"
            " FROM backfill_weather_candles WHERE market_ticker = ANY(%s)",
            (list(markets),),
        )
        per_event_time: dict[str, dict] = {}
        for mk_tk, ts, price_close, bid, ask in cur.fetchall():
            m = markets.get(mk_tk)
            if m is None:
                continue
            if bid is not None and ask is not None:
                mid = (float(bid) + float(ask)) / 2.0
            elif price_close is not None:
                mid = float(price_close)
            else:
                mid = None
            bucket = Bucket(mk_tk, m["low"], m["high"],
                            float(ask) if ask is not None else None, mid)
            per_event_time.setdefault(m["event"], {}).setdefault(ts, []).append(bucket)

    out = []
    for ev_tk, e in events.items():
        if e["winners"] != 1:  # incomplete/ambiguous backfill coverage
            continue
        close = e["close"]
        if close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        cycles = []
        for ts, buckets in (per_event_time.get(ev_tk) or {}).items():
            t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            htc = (close - t).total_seconds() / 3600.0
            if htc >= 0:
                cycles.append(Cycle(htc, buckets))
        if cycles:
            out.append({"kind": e["kind"], "city": e["city"], "winner": e["winner"],
                        "forecast": None, "date": e["date"], "cycles": cycles})
    return out


def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _fav_pnl_at(ev: dict, w: float, fees: bool) -> float | None:
    cyc = pick_cycle_nearest(ev["cycles"], w)
    if cyc is None:
        return None
    fb = favorite_bucket(cyc)
    if fb is None:
        return None
    return _entry_pnl(fb, ev["winner"], fees)


def validate_windows(events: list[dict], windows: list[float], fees: bool,
                     *, min_n: int = 25, train_frac: float = 0.6):
    """Guard the city x window 'edge' against overfitting: significance (t-stat),
    month stability, and a train/test holdout of a per-city window MAP vs one global
    window. Favorite book only (backfill has no forecast)."""
    # per (city, kind, window) -> list of (date, pnl)
    cell: dict[tuple[str, str, float], list[tuple[str, float]]] = {}
    for ev in events:
        for w in windows:
            pnl = _fav_pnl_at(ev, w, fees)
            if pnl is not None and ev.get("date"):
                cell.setdefault((ev["city"], ev["kind"], w), []).append((ev["date"], pnl))

    # 1) significance per cell (full sample)
    sig = {}
    for key, dp in cell.items():
        pnls = [p for _d, p in dp]
        n = len(pnls)
        if n >= min_n:
            mean = sum(pnls) / n
            se = _stdev(pnls) / math.sqrt(n)
            sig[key] = (n, mean, mean / se if se > 0 else 0.0)

    # 2) month stability for the strongest high cells (by |t|)
    top = sorted((k for k in sig if k[1] == "high"), key=lambda k: -abs(sig[k][2]))[:6]
    stab = {}
    for key in top:
        months: dict[str, list[float]] = {}
        for d, p in cell[key]:
            months.setdefault(d[:7], []).append(p)
        stab[key] = {m: (sum(v) / len(v), len(v)) for m, v in sorted(months.items())}

    # 3) train/test: pick each (city,kind)'s best window on train, evaluate on test;
    #    compare to ONE global best window (chosen on train) evaluated on test.
    by_ck: dict[tuple[str, str], list[dict]] = {}
    for ev in events:
        if ev.get("date"):
            by_ck.setdefault((ev["city"], ev["kind"]), []).append(ev)
    map_test, flat_test = [], []  # per-trade pnls on the test set
    # global window chosen on the pooled train half
    all_sorted = sorted((ev for ev in events if ev.get("date")), key=lambda e: e["date"])
    cut = int(len(all_sorted) * train_frac)
    global_train: dict[float, list[float]] = {}
    for ev in all_sorted[:cut]:
        for w in windows:
            p = _fav_pnl_at(ev, w, fees)
            if p is not None:
                global_train.setdefault(w, []).append(p)
    global_w = max(
        (w for w, ps in global_train.items() if len(ps) >= min_n),
        key=lambda w: sum(global_train[w]) / len(global_train[w]),
        default=None,
    )
    picks = {}
    for ck, evs in by_ck.items():
        evs_sorted = sorted(evs, key=lambda e: e["date"])
        c = int(len(evs_sorted) * train_frac)
        train, test = evs_sorted[:c], evs_sorted[c:]
        # best window on train
        tr: dict[float, list[float]] = {}
        for ev in train:
            for w in windows:
                p = _fav_pnl_at(ev, w, fees)
                if p is not None:
                    tr.setdefault(w, []).append(p)
        best_w = max(
            (w for w, ps in tr.items() if len(ps) >= min_n // 2),
            key=lambda w: sum(tr[w]) / len(tr[w]),
            default=None,
        )
        if best_w is None:
            continue
        picks[ck] = best_w
        for ev in test:
            pm = _fav_pnl_at(ev, best_w, fees)
            if pm is not None:
                map_test.append(pm)
            if global_w is not None:
                pf = _fav_pnl_at(ev, global_w, fees)
                if pf is not None:
                    flat_test.append(pf)
    return {"sig": sig, "stab": stab, "picks": picks, "global_w": global_w,
            "map_test": map_test, "flat_test": flat_test}


def report_validate(res, windows) -> None:
    print("=== City x entry-window validation (favorite, backfill) ===")
    print("  --- per (city, kind, window) favorite EV with t-stat (full sample, n>=25) ---")
    print("  (|t|>2 ~ unlikely to be pure noise; but we're scanning many cells, so the")
    print("   train/test holdout below is the real test against overfitting)")
    sig = res["sig"]
    # show the high-kind cells sorted by EV, flag significance
    highs = sorted((k for k in sig if k[1] == "high"), key=lambda k: -sig[k][1])
    print(f"  {'city':5s} {'kind':4s} {'win':>4} {'n':>4} {'ev/trade':>9} {'t':>6}")
    for k in highs[:12]:
        n, mean, t = sig[k]
        star = " *" if abs(t) > 2 else ""
        print(f"  {k[0]:5s} {k[1]:4s} h{int(k[2]):<3d} {n:4d} {mean:+8.1f}c {t:+6.2f}{star}")

    if res["stab"]:
        print("\n  --- month stability of the strongest high cells (is it one heat wave?) ---")
        for key, months in res["stab"].items():
            cells = "  ".join(f"{m}:{v:+.0f}c(n{n})" for m, (v, n) in months.items())
            print(f"  {key[0]} h{int(key[2])}: {cells}")

    print("\n  --- TRAIN/TEST holdout: per-city window map vs one global window ---")
    print(f"  global window chosen on train: h{int(res['global_w'])}"
          if res["global_w"] is not None else "  (no global window)")
    picks = "  ".join(f"{c}/{k[:1]}=h{int(w)}" for (c, k), w in sorted(res["picks"].items()))
    print(f"  per-(city,kind) windows picked on train: {picks}")
    mt, ft = res["map_test"], res["flat_test"]
    if mt and ft:
        mm, fm = sum(mt) / len(mt), sum(ft) / len(ft)
        print(f"  TEST per-trade EV — per-city MAP: {mm:+.1f}c (n={len(mt)})   "
              f"global FLAT: {fm:+.1f}c (n={len(ft)})   edge: {mm - fm:+.1f}c")
        print("  (if MAP <= FLAT out-of-sample, the per-city window edge was overfit)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default=",".join(str(w) for w in DEFAULT_WINDOWS))
    ap.add_argument("--source", choices=("live", "backfill"), default="live")
    ap.add_argument("--validate", action="store_true",
                    help="significance + month-stability + train/test of a per-city window map")
    ap.add_argument("--no-fees", action="store_true")
    args = ap.parse_args(argv)
    windows = [float(w) for w in args.windows.split(",") if w.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        events = load_events_backfill(conn) if args.source == "backfill" else load_events(conn)

    if not events:
        print("=== Entry-window sweep ===\n  (no settled events with ladder coverage yet)")
        return 0
    if args.validate:
        report_validate(validate_windows(events, windows, not args.no_fees), windows)
        return 0
    cell, cell_city = sweep(events, windows, not args.no_fees)
    report(cell, cell_city, windows, len(events),
           source=args.source, show_nws=(args.source == "live"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
