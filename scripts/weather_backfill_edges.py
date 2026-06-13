"""Mine the Kalshi REST backfill for two structural edges (read-only, market-only data).

OVERROUND — in an N-bucket mutually-exclusive market the bucket YES prices "should"
sum to ~100c. We measure the actual sum of mids/bids/asks across every event x hourly
snapshot, by hours-to-close: a persistent sum != 100 is a structural (calibration-
independent) edge, and any snapshot where the BIDS sum to >100 is a risk-free
sell-the-ladder arb (collect the credit, pay 100 to the lone winner). Fees on N legs
are modelled before calling anything tradeable.

PERSISTENCE / SEASONAL DRIFT — temperature is strongly autocorrelated (heat waves,
cold snaps) and the season trends. If the market resets toward climatology each morning
and underweights yesterday's anomaly, the persistent bucket is underpriced at the open.
We (a) measure day-over-day outcome correlation, (b) compare how well the market's
open-implied mean temperature tracks yesterday vs how well the actual does, (c) test the
concrete strategy "buy the bucket containing YESTERDAY's settled temperature at today's
open" vs buying the open favorite, and (d) check the seasonal mean(actual - implied) by
month for a warming-lag.

Reads ONLY the backfill_* tables. Caveats: hourly candles, spring-only (Apr-Jun), final
~48h per market. Usage:
    {"type": "script", "name": "weather_backfill_edges", "args": ["--analysis", "both"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, timezone

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=180000 "
    "-c idle_in_transaction_session_timeout=180000"
)

HTC_BINS = ((0, 6), (6, 12), (12, 24), (24, 48))


def fee_cents(price_cents: float, enabled: bool = True) -> float:
    """Kalshi fee in cents at qty=1 (mirrors paper/engine.py::kalshi_fee)."""
    if not enabled or price_cents is None:
        return 0.0
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


def bucket_mid_f(low: float | None, high: float | None) -> float | None:
    """A bucket's representative temperature (degF). Open ends use the edge."""
    if low is not None and high is not None:
        return (low + high) / 2.0
    if high is not None:
        return high
    if low is not None:
        return low
    return None


def value_in_bucket(value: float, low: float | None, high: float | None) -> bool:
    r = round(value)
    if low is not None and r < low:
        return False
    if high is not None and r > high:
        return False
    return True


@dataclass
class Bkt:
    ticker: str
    low: float | None
    high: float | None
    bid: float | None
    ask: float | None
    mid: float | None


@dataclass
class Cycle:
    htc: float
    buckets: list[Bkt]


@dataclass
class Event:
    city: str
    kind: str
    date: str | None
    winner: str
    winner_mid_f: float | None
    cycles: list[Cycle]

    def open_cycle(self) -> Cycle | None:
        return max(self.cycles, key=lambda c: c.htc) if self.cycles else None


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


def implied_mean_f(buckets: list[Bkt]) -> float | None:
    """Price-weighted mean temperature implied by the ladder (mids as weights)."""
    num = den = 0.0
    for b in buckets:
        pt = bucket_mid_f(b.low, b.high)
        if b.mid is not None and b.mid > 0 and pt is not None:
            num += (b.mid / 100.0) * pt
            den += b.mid / 100.0
    return num / den if den > 0 else None


def ladder_sums(buckets: list[Bkt]) -> tuple[float, float, float] | None:
    """(sum_mid, sum_bid, sum_ask) over buckets with a usable price; None if too sparse."""
    mids = [b.mid for b in buckets if b.mid is not None]
    if len(mids) < 3:
        return None
    sb = sum(b.bid for b in buckets if b.bid is not None)
    sa = sum(b.ask for b in buckets if b.ask is not None)
    return sum(mids), sb, sa


# --- analysis A: overround ---------------------------------------------------------


def overround(events: list[Event], fees: bool):
    bins: dict[tuple[int, int], list[float]] = {b: [] for b in HTC_BINS}
    sellable = {b: [0, 0, 0.0] for b in HTC_BINS}  # n, credit_count, mean_net_credit_cents
    for ev in events:
        for cyc in ev.cycles:
            b = next((hb for hb in HTC_BINS if hb[0] <= cyc.htc < hb[1]), None)
            if b is None:
                continue
            sums = ladder_sums(cyc.buckets)
            if sums is None:
                continue
            sum_mid, sum_bid, _sum_ask = sums
            bins[b].append(sum_mid)
            # Sell every bucket's YES at its bid -> collect sum_bid, pay 100 to the lone
            # winner, minus a fee per leg. Net credit if positive (a free arb).
            leg_fees = sum(fee_cents(x, fees) for x in ev_buckets_with_bid(cyc.buckets))
            net = sum_bid - 100.0 - leg_fees
            cell = sellable[b]
            cell[0] += 1
            cell[1] += int(net > 0)
            cell[2] += net
    return bins, sellable


def ev_buckets_with_bid(buckets: list[Bkt]):
    return [b.bid for b in buckets if b.bid is not None and 1 <= b.bid <= 99]


def report_overround(bins, sellable) -> None:
    print("=== A) Ladder overround — do the bucket YES prices sum to 100c? ===")
    print("  (sum_mid >> 100 => structural overround/vig; a sell-the-ladder credit would")
    print("   be a risk-free arb. bids almost always sum < 100 on a tight CLOB — we check.)")
    print(f"  {'htc':>8} {'n':>6} {'mean_sum_mid':>12} {'sellable%':>10} {'mean_net_credit':>16}")
    for b in HTC_BINS:
        sums = bins[b]
        n, cred, net = sellable[b]
        if not sums:
            continue
        label = f"{b[0]}-{b[1]}h"
        msum = sum(sums) / len(sums)
        sell_pct = f"{cred / n * 100:.1f}%" if n else "n/a"
        mean_net = f"{net / n:+.1f}c" if n else "n/a"
        print(f"  {label:>8} {len(sums):6d} {msum:12.1f} {sell_pct:>10} {mean_net:>16}")
    print("  (sellable% = fraction of snapshots where selling every bucket at its bid,"
          " net of per-leg fees, collects a credit > the 100c payout)")


# --- analysis B: persistence / seasonal drift --------------------------------------


def _month(d: str | None) -> str:
    return d[:7] if d else "?"


def persistence(events: list[Event], fees: bool):
    # series per (city, kind), date-ordered
    series: dict[tuple[str, str], list[Event]] = {}
    for ev in events:
        if ev.date and ev.winner_mid_f is not None:
            series.setdefault((ev.city, ev.kind), []).append(ev)
    for evs in series.values():
        evs.sort(key=lambda e: e.date)

    pers_x, pers_y = [], []          # actual_{t-1}, actual_t
    impl_x, impl_y = [], []          # actual_{t-1}, implied_t  (does market track yday?)
    err_anom, err_val = [], []       # anomaly_{t-1}, market_error_t
    by_month: dict[str, list[float]] = {}
    # strategy: buy yesterday's-outcome bucket at the open vs buy the open favorite
    strat = {"pers": [0, 0, 0.0], "fav": [0, 0, 0.0]}  # n, wins, pnl_cents

    for evs in series.values():
        mean_actual = sum(e.winner_mid_f for e in evs) / len(evs)
        prev: Event | None = None
        for ev in evs:
            cyc = ev.open_cycle()
            implied = implied_mean_f(cyc.buckets) if cyc else None
            if prev is not None:
                pers_x.append(prev.winner_mid_f)
                pers_y.append(ev.winner_mid_f)
                if implied is not None:
                    impl_x.append(prev.winner_mid_f)
                    impl_y.append(implied)
                    err = ev.winner_mid_f - implied
                    err_anom.append(prev.winner_mid_f - mean_actual)
                    err_val.append(err)
                    by_month.setdefault(_month(ev.date), []).append(err)
                # strategy entries at the open
                if cyc is not None:
                    _strat_entry(strat["pers"], cyc, ev.winner,
                                 _bucket_containing(cyc.buckets, prev.winner_mid_f), fees)
                    _strat_entry(strat["fav"], cyc, ev.winner, _favorite(cyc.buckets), fees)
            prev = ev

    return {
        "pers_corr": pearson(pers_x, pers_y), "pers_n": len(pers_x),
        "impl_corr": pearson(impl_x, impl_y),
        "err_corr": pearson(err_anom, err_val), "err_n": len(err_val),
        "mean_err": (sum(err_val) / len(err_val)) if err_val else None,
        "by_month": {m: (sum(v) / len(v), len(v)) for m, v in sorted(by_month.items())},
        "strat": strat,
    }


def _favorite(buckets: list[Bkt]) -> Bkt | None:
    priced = [b for b in buckets if b.mid is not None]
    return max(priced, key=lambda b: b.mid) if priced else None


def _bucket_containing(buckets: list[Bkt], value: float | None) -> Bkt | None:
    if value is None:
        return None
    for b in buckets:
        if value_in_bucket(value, b.low, b.high):
            return b
    return None


def _strat_entry(cell, cyc: Cycle, winner: str, bucket: Bkt | None, fees: bool) -> None:
    if bucket is None or bucket.ask is None or not (1 <= bucket.ask <= 99):
        return
    won = bucket.ticker == winner
    cell[0] += 1
    cell[1] += int(won)
    cell[2] += (100.0 if won else 0.0) - bucket.ask - fee_cents(bucket.ask, fees)


def report_persistence(res) -> None:
    print("\n=== B) Persistence & seasonal drift — does the market underweight yesterday? ===")
    pc, ic = res["pers_corr"], res["impl_corr"]
    print(f"  day-over-day outcome corr (actual_t vs actual_t-1): "
          f"{pc:+.2f} (n={res['pers_n']})" if pc is not None else "  (insufficient data)")
    if ic is not None:
        print(f"  market open-implied vs actual_t-1 corr:            {ic:+.2f}"
              "   (if << the above, the market underweights persistence)")
    ec = res["err_corr"]
    if ec is not None:
        print(f"  corr(market_error_t, anomaly_t-1):                 {ec:+.2f} (n={res['err_n']})")
        print("    (>0 => after a hot day the market UNDER-forecasts -> high buckets cheap)")
    if res["mean_err"] is not None:
        print(f"  mean(actual - implied) overall:                    {res['mean_err']:+.2f} F")
    if res["by_month"]:
        cells = "   ".join(f"{m}: {v:+.2f}F(n{n})" for m, (v, n) in res["by_month"].items())
        print(f"  seasonal mean error by month: {cells}")
        print("    (systematically positive & growing => market lags the warming season)")

    print("\n  --- strategy: buy a bucket at the OPEN, hold to settlement (fees on) ---")
    print(f"  {'entry':28s} {'n':>5} {'win%':>5} {'pnl/trade':>10}")
    for key, label in (("pers", "yesterday's-outcome bucket"), ("fav", "open favorite (baseline)")):
        n, wins, pnl = res["strat"][key]
        if n:
            print(f"  {label:28s} {n:5d} {wins / n * 100:4.0f}% {pnl / n:+9.1f}c")


# --- data load (backfill_* only) ----------------------------------------------------


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_events(conn) -> list[Event]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ticker, market_ticker, city, kind, result, low_f, high_f,"
            " target_date, close_time FROM backfill_weather_markets"
            " WHERE candles_fetched AND close_time IS NOT NULL AND event_ticker IS NOT NULL"
        )
        markets: dict[str, dict] = {}
        meta: dict[str, dict] = {}
        for ev_tk, mk_tk, city, kind, result, lo, hi, tdate, close in cur.fetchall():
            markets[mk_tk] = {"event": ev_tk, "low": lo, "high": hi}
            m = meta.setdefault(ev_tk, {"city": city, "kind": kind or "high", "date": tdate,
                                        "close": close, "winner": None, "winners": 0,
                                        "wlow": None, "whigh": None})
            if (result or "").lower() == "yes":
                m["winner"], m["wlow"], m["whigh"] = mk_tk, lo, hi
                m["winners"] += 1
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
            bkt = Bkt(mk_tk, m["low"], m["high"],
                      float(bid) if bid is not None else None,
                      float(ask) if ask is not None else None, mid)
            per_event_time.setdefault(m["event"], {}).setdefault(ts, []).append(bkt)

    out: list[Event] = []
    for ev_tk, m in meta.items():
        if m["winners"] != 1:
            continue
        close = m["close"]
        if close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        cycles = []
        for ts, buckets in (per_event_time.get(ev_tk) or {}).items():
            t = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            htc = (close - t).total_seconds() / 3600.0
            if htc >= 0:
                cycles.append(Cycle(htc, buckets))
        if not cycles:
            continue
        out.append(Event(
            city=m["city"], kind=m["kind"],
            date=m["date"] or _date_from_ticker(ev_tk),
            winner=m["winner"], winner_mid_f=bucket_mid_f(m["wlow"], m["whigh"]),
            cycles=cycles,
        ))
    return out


def _date_from_ticker(tk: str | None) -> str | None:
    import re
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    m = re.search(r"(\d{2})([A-Z]{3})(\d{2})", tk or "")
    if not m:
        return None
    mo = months.get(m.group(2))
    if not mo:
        return None
    try:
        return date(2000 + int(m.group(1)), mo, int(m.group(3))).isoformat()
    except ValueError:
        return None


def meanrev(events: list[Event], fees: bool):
    """Push the fade-after-extreme signal: bin today's market error by yesterday's
    anomaly tercile, check per-city robustness, and sweep a 'shrink the prior anomaly'
    correction (pred = implied - k*anomaly) as a tradeable bucket entry vs the favorite."""
    series: dict[tuple[str, str], list[Event]] = {}
    for ev in events:
        if ev.date and ev.winner_mid_f is not None:
            series.setdefault((ev.city, ev.kind), []).append(ev)
    for evs in series.values():
        evs.sort(key=lambda e: e.date)

    samples = []  # (city, anomaly, error, implied, cyc, winner)
    per_city_anom: dict[str, list[float]] = {}
    per_city_err: dict[str, list[float]] = {}
    for (city, _kind), evs in series.items():
        mean_actual = sum(e.winner_mid_f for e in evs) / len(evs)
        prev = None
        for ev in evs:
            cyc = ev.open_cycle()
            implied = implied_mean_f(cyc.buckets) if cyc else None
            if prev is not None and implied is not None and cyc is not None:
                anom = prev.winner_mid_f - mean_actual
                err = ev.winner_mid_f - implied
                samples.append((city, anom, err, implied, cyc, ev.winner))
                per_city_anom.setdefault(city, []).append(anom)
                per_city_err.setdefault(city, []).append(err)
            prev = ev

    # anomaly terciles -> mean error (monotone reversion check)
    anoms = sorted(s[1] for s in samples)
    terc = {}
    if len(anoms) >= 9:
        lo, hi = anoms[len(anoms) // 3], anoms[2 * len(anoms) // 3]
        for label, lo_b, hi_b in (("cold", -1e9, lo), ("normal", lo, hi), ("hot", hi, 1e9)):
            errs = [s[2] for s in samples if lo_b <= s[1] < hi_b]
            if errs:
                terc[label] = (sum(errs) / len(errs), len(errs))

    # per-city correlation (robustness of the pooled -0.18)
    city_corr = {c: pearson(per_city_anom[c], per_city_err[c]) for c in per_city_anom}

    # fade strategy: pred = implied - k*anomaly; buy bucket containing pred at open ask
    ks = (0.0, 0.25, 0.5, 0.75, 1.0)
    fade = {k: [0, 0, 0.0] for k in ks}
    for _city, anom, _err, implied, cyc, winner in samples:
        for k in ks:
            pred = implied - k * anom
            _strat_entry(fade[k], cyc, winner, _bucket_containing(cyc.buckets, pred), fees)
    return {"terc": terc, "city_corr": city_corr, "fade": fade, "n": len(samples)}


def report_meanrev(res) -> None:
    print("\n=== B2) Mean-reversion (fade after extreme) — is the -0.18 tradeable? ===")
    print(f"  paired days: {res['n']}")
    if res["terc"]:
        cells = "   ".join(
            f"{lab}: {v:+.2f}F(n{n})" for lab, (v, n) in
            sorted(res["terc"].items(), key=lambda x: {"cold": 0, "normal": 1, "hot": 2}[x[0]])
        )
        print(f"  mean(actual - implied) by yesterday's anomaly tercile: {cells}")
        print("    (if hot << cold, the market over-extrapolates heat -> fade it)")
    print("  per-city corr(error, anomaly_t-1) [robustness of the pooled signal]:")
    for c, cc in sorted(res["city_corr"].items()):
        print(f"    {c:5s} {cc:+.2f}" if cc is not None else f"    {c:5s}  n/a")
    print("\n  --- fade strategy: buy bucket at pred = implied - k*anomaly, open, fees on ---")
    print(f"  {'k':>5} {'n':>5} {'win%':>5} {'pnl/trade':>10}  (k=0 is the plain implied-center)")
    for k, (n, wins, pnl) in sorted(res["fade"].items()):
        if n:
            print(f"  {k:5.2f} {n:5d} {wins / n * 100:4.0f}% {pnl / n:+9.1f}c")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--analysis", choices=("overround", "persistence", "meanrev", "both"),
                    default="both")
    ap.add_argument("--no-fees", action="store_true")
    args = ap.parse_args(argv)
    fees = not args.no_fees

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        events = load_events(conn)

    print(f"=== Backfill edge probe (Kalshi REST history) — {len(events)} complete events ===")
    if not events:
        return 0
    if args.analysis in ("overround", "both"):
        bins, sellable = overround(events, fees)
        report_overround(bins, sellable)
    if args.analysis in ("persistence", "both"):
        report_persistence(persistence(events, fees))
    if args.analysis in ("meanrev", "both"):
        report_meanrev(meanrev(events, fees))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
