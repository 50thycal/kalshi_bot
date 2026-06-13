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

LONGSHOT — the favorite-longshot bias: do cheap tail buckets settle YES even less often
than their price implies (so selling them — buying NO at 100-bid — is +EV), and do heavy
favorites win MORE often than their ask (so buying YES is +EV)? At a fixed hours-to-close
we bin every bucket by its mid price into longshot bands (1-3, 3-5, 5-10, 10-20c) and
favorite bands (80-90, 90-95, 95-100c), grade on the actual winner, and report yes-win%
and the per-trade EV of each harvest (fees on both legs).

CONVERGENCE — favorite momentum / price-drift. Do bucket price MOVES trend or reverse
(autocorrelation of dprice[h24->h12] vs dprice[h12->h6])? Is buying the favorite YES at
its ask +EV at each horizon (h24/h12/h6 held to settlement), i.e. does the favorite firm
up underpriced or is it already priced? And does buying the biggest recent RISER at h12
beat buying the h12 favorite (tradeable momentum)?

DISTSHAPE — is the market's implied PDF the wrong SHAPE/width? Normalize the ladder mids
into a distribution over bucket temperatures, get its implied mean & sd, and standardize
each event's actual outcome (z = (actual-mean)/sd). SD(z) > 1 => the market is
underdispersed (outcomes land in the tails more than priced => tails too cheap, buy them);
< 1 => overdispersed (tails too rich). A PIT tail-mass check cross-confirms, and buckets
binned by implied sigma-distance from the mean are graded (YES at ask / NO at 100-bid) to
localize and price any tail edge. The principled version of the longshot probe.

LEADLAG — weather propagates west->east over ~1-2 days. Does a western city's daily
anomaly lead an eastern city's, and has the eastern market priced the incoming airmass?
Pooled corr(anomaly) by lag, downwind (west leads east) vs an upwind placebo; then
corr(east market_error[d+lag], west anomaly[d]) to test if the lead is unpriced; then a
tradeable lead-shift (buy east bucket at implied_east + k*west_anom[d-1]) vs the favorite.

Reads ONLY the backfill_* tables. Caveats: hourly candles, spring-only (Apr-Jun), final
~48h per market. Usage:
    {"type": "script", "name": "weather_backfill_edges", "args": ["--analysis", "both"]}
    {"type": "script", "name": "weather_backfill_edges", "args": ["--analysis", "longshot"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta, timezone

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=180000 "
    "-c idle_in_transaction_session_timeout=180000"
)

HTC_BINS = ((0, 6), (6, 12), (12, 24), (24, 48))
LONGSHOT_BANDS = ((1, 3), (3, 5), (5, 10), (10, 20))   # cheap tails (by mid) -> sell (buy NO)
FAVORITE_BANDS = ((80, 90), (90, 95), (95, 100))        # heavy favorites -> buy YES
SIGMA_BANDS = ((0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 99.0))  # implied sigma from mean
CITY_LON = {"LAX": -118.2, "DEN": -105.0, "AUS": -97.7, "CHI": -87.7,  # west -> east
            "MIA": -80.2, "PHIL": -75.2, "NYC": -74.0}


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


def _nearest_cycle(ev: Event, target_htc: float, tol: float):
    if not ev.cycles:
        return None
    best = min(ev.cycles, key=lambda c: abs(c.htc - target_htc))
    return best if abs(best.htc - target_htc) <= tol else None


def longshot(events: list[Event], fees: bool, *, target_htc: float = 12.0, tol: float = 3.0):
    """Harvest the tails at ~target_htc: SELL cheap longshots (buy NO at 100-bid) and BUY
    heavy favorites (YES at ask), binned by mid price, graded on the actual winner."""
    ls = {b: [0, 0, 0.0] for b in LONGSHOT_BANDS}  # n, yes_wins, no_pnl_cents
    fv = {b: [0, 0, 0.0] for b in FAVORITE_BANDS}  # n, yes_wins, yes_pnl_cents
    n_ev = 0
    for ev in events:
        cyc = _nearest_cycle(ev, target_htc, tol)
        if cyc is None:
            continue
        n_ev += 1
        for b in cyc.buckets:
            if b.mid is None:
                continue
            won = b.ticker == ev.winner
            for band in LONGSHOT_BANDS:
                if band[0] <= b.mid < band[1] and b.bid is not None and 1 <= (100 - b.bid) <= 99:
                    no_cost = 100 - b.bid  # buy NO = take the other side of the longshot YES
                    cell = ls[band]
                    cell[0] += 1
                    cell[1] += int(won)
                    cell[2] += (0.0 if won else 100.0) - no_cost - fee_cents(no_cost, fees)
                    break
            for band in FAVORITE_BANDS:
                if band[0] <= b.mid < band[1] and b.ask is not None and 1 <= b.ask <= 99:
                    cell = fv[band]
                    cell[0] += 1
                    cell[1] += int(won)
                    cell[2] += (100.0 if won else 0.0) - b.ask - fee_cents(b.ask, fees)
                    break
    return {"ls": ls, "fv": fv, "n_ev": n_ev, "htc": target_htc}


def report_longshot(res) -> None:
    print(f"\n=== C) Tail harvesting at ~h{int(res['htc'])} — sell longshots / buy favorites "
          f"(fees on, {res['n_ev']} events) ===")
    print("  --- SELL cheap longshots (buy NO at 100-bid); needs the bucket to LOSE ---")
    print(f"  {'yes band':>9} {'n':>5} {'yes_win%':>8} {'no_ev/trade':>12}")
    for band in LONGSHOT_BANDS:
        n, wins, pnl = res["ls"][band]
        if n:
            print(f"  {band[0]:3d}-{band[1]:<4d} {n:5d} {wins / n * 100:7.1f}% {pnl / n:+11.1f}c")
    print("  --- BUY heavy favorites (YES at ask); needs the bucket to WIN ---")
    print(f"  {'yes band':>9} {'n':>5} {'yes_win%':>8} {'yes_ev/trade':>12}")
    for band in FAVORITE_BANDS:
        n, wins, pnl = res["fv"][band]
        if n:
            print(f"  {band[0]:3d}-{band[1]:<4d} {n:5d} {wins / n * 100:7.1f}% {pnl / n:+11.1f}c")
    print("  (positive ev/trade beyond ~0 => a tradeable favorite-longshot edge)")


def _cycle_by_ticker(cyc: Cycle) -> dict[str, Bkt]:
    return {b.ticker: b for b in cyc.buckets}


def convergence(events: list[Event], fees: bool, *,
                early: float = 24.0, mid: float = 12.0, late: float = 6.0, tol: float = 4.0):
    """Favorite momentum / convergence. Two questions: (1) do bucket price MOVES trend or
    reverse (autocorr of Δprice[h24->h12] vs Δprice[h12->h6])? (2) is buying the favorite
    YES at its ask +EV at each horizon, and does buying the biggest recent RISER at h12
    beat buying the h12 favorite (momentum) — all held to settlement, fees on?"""
    leg1, leg2 = [], []                                   # paired bucket price moves
    fav_settle = {"early": [0, 0, 0.0], "mid": [0, 0, 0.0], "late": [0, 0, 0.0]}
    mom = {"riser": [0, 0, 0.0], "faller": [0, 0, 0.0], "fav": [0, 0, 0.0]}
    n_ev = 0
    for ev in events:
        ce, cm, cl = (_nearest_cycle(ev, h, tol) for h in (early, mid, late))
        for key, cyc in (("early", ce), ("mid", cm), ("late", cl)):
            if cyc is not None:
                _strat_entry(fav_settle[key], cyc, ev.winner, _favorite(cyc.buckets), fees)
        if ce is None or cm is None:
            continue
        n_ev += 1
        me, mm = _cycle_by_ticker(ce), _cycle_by_ticker(cm)
        ml = _cycle_by_ticker(cl) if cl is not None else {}
        moves = []                                        # (Δearly->mid, bucket@mid)
        for tk, b_mid in mm.items():
            b_early = me.get(tk)
            if b_early is None or b_early.mid is None or b_mid.mid is None:
                continue
            d1 = b_mid.mid - b_early.mid
            b_late = ml.get(tk)
            if b_late is not None and b_late.mid is not None:
                leg1.append(d1)
                leg2.append(b_late.mid - b_mid.mid)
            moves.append((d1, b_mid))
        if moves:
            _strat_entry(mom["riser"], cm, ev.winner, max(moves, key=lambda x: x[0])[1], fees)
            _strat_entry(mom["faller"], cm, ev.winner, min(moves, key=lambda x: x[0])[1], fees)
            _strat_entry(mom["fav"], cm, ev.winner, _favorite(cm.buckets), fees)
    return {"leg1": leg1, "leg2": leg2, "move_corr": pearson(leg1, leg2),
            "fav_settle": fav_settle, "mom": mom, "n_ev": n_ev,
            "horizons": (early, mid, late)}


def report_convergence(res) -> None:
    e, m, ll = (int(h) for h in res["horizons"])
    print(f"\n=== D) Favorite momentum / convergence (fees on, {res['n_ev']} events) ===")
    mc = res["move_corr"]
    if mc is not None:
        print(f"  price-move autocorr corr(d[h{e}->h{m}], d[h{m}->h{ll}]): "
              f"{mc:+.2f} (n={len(res['leg1'])})")
        print("    (>0 => moves trend/continue: momentum; <0 => moves reverse: fade)")
    print("\n  --- buy the favorite YES at its ask, hold to settlement, by horizon ---")
    print(f"  {'horizon':>8} {'n':>5} {'win%':>5} {'pnl/trade':>10}")
    for key, label in (("early", f"~h{e}"), ("mid", f"~h{m}"), ("late", f"~h{ll}")):
        n, wins, pnl = res["fav_settle"][key]
        if n:
            print(f"  {label:>8} {n:5d} {wins / n * 100:4.0f}% {pnl / n:+9.1f}c")
    print("    (more +EV as horizon shortens => favorite firms up underpriced; flat => priced)")
    print(f"\n  --- at ~h{m} buy by recent move, hold to settlement ---")
    print(f"  {'pick':>14} {'n':>5} {'win%':>5} {'pnl/trade':>10}")
    for key, label in (("riser", "biggest riser"), ("faller", "biggest faller"),
                       ("fav", "favorite")):
        n, wins, pnl = res["mom"][key]
        if n:
            print(f"  {label:>14} {n:5d} {wins / n * 100:4.0f}% {pnl / n:+9.1f}c")
    print("    (riser >> favorite => momentum tradeable; faller >> => reversion)")


def _implied_dist(buckets: list[Bkt]):
    """Normalize the ladder mids into a proper probability distribution over bucket
    temperatures; return (mean, sd, temps, probs) or None if too sparse."""
    pts, ps = [], []
    for b in buckets:
        pt = bucket_mid_f(b.low, b.high)
        if b.mid is not None and b.mid > 0 and pt is not None:
            pts.append(pt)
            ps.append(b.mid / 100.0)
    tot = sum(ps)
    if tot <= 0 or len(ps) < 3:
        return None
    ps = [p / tot for p in ps]
    mean = sum(p * t for p, t in zip(ps, pts, strict=True))
    var = sum(p * (t - mean) ** 2 for p, t in zip(ps, pts, strict=True))
    return mean, math.sqrt(var), pts, ps


def distshape(events: list[Event], fees: bool, *, target_htc: float = 12.0, tol: float = 3.0):
    """Is the market's implied PDF the wrong shape? Standardize each event's outcome by the
    ladder's own implied mean/sd (z); SD(z)>1 => underdispersed (tails realize too often =>
    too cheap), <1 => overdispersed. PIT tail mass cross-checks. Then grade buckets binned by
    implied sigma-distance from the mean (YES at ask / NO at 100-bid) to localize any edge."""
    zs, pits = [], []
    # per band: [n, yes_wins, sum_price, yes_n, yes_pnl, no_n, no_pnl]
    bands = {b: [0, 0, 0.0, 0, 0.0, 0, 0.0] for b in SIGMA_BANDS}
    n_ev = 0
    for ev in events:
        cyc = _nearest_cycle(ev, target_htc, tol)
        if cyc is None or ev.winner_mid_f is None:
            continue
        dist = _implied_dist(cyc.buckets)
        if dist is None:
            continue
        mean, sd, pts, ps = dist
        if sd <= 0:
            continue
        n_ev += 1
        actual = ev.winner_mid_f
        zs.append((actual - mean) / sd)
        below = sum(p for p, t in zip(ps, pts, strict=True) if t < actual - 1e-9)
        at = sum(p for p, t in zip(ps, pts, strict=True) if abs(t - actual) <= 1e-9)
        pits.append(below + 0.5 * at)
        for b in cyc.buckets:
            pt = bucket_mid_f(b.low, b.high)
            if b.mid is None or pt is None:
                continue
            zb = abs(pt - mean) / sd
            band = next((sb for sb in SIGMA_BANDS if sb[0] <= zb < sb[1]), None)
            if band is None:
                continue
            won = b.ticker == ev.winner
            cell = bands[band]
            cell[0] += 1
            cell[1] += int(won)
            cell[2] += b.mid
            if b.ask is not None and 1 <= b.ask <= 99:
                cell[3] += 1
                cell[4] += (100.0 if won else 0.0) - b.ask - fee_cents(b.ask, fees)
            if b.bid is not None and 1 <= (100 - b.bid) <= 99:
                no_cost = 100 - b.bid
                cell[5] += 1
                cell[6] += (0.0 if won else 100.0) - no_cost - fee_cents(no_cost, fees)
    mean_z = sum(zs) / len(zs) if zs else None
    sd_z = math.sqrt(sum((z - mean_z) ** 2 for z in zs) / len(zs)) if mean_z is not None and len(zs) > 1 else None
    pit_lo = sum(1 for p in pits if p < 0.1) / len(pits) if pits else None
    pit_hi = sum(1 for p in pits if p > 0.9) / len(pits) if pits else None
    return {"mean_z": mean_z, "sd_z": sd_z, "pit_lo": pit_lo, "pit_hi": pit_hi,
            "bands": bands, "n_ev": n_ev, "htc": target_htc}


def report_distshape(res) -> None:
    print(f"\n=== E) Distribution shape / tail mispricing (fees on, {res['n_ev']} events "
          f"at ~h{int(res['htc'])}) ===")
    if res["mean_z"] is not None:
        print(f"  mean standardized outcome (bias):  {res['mean_z']:+.2f} sigma"
              "   (>0 => actual runs hot vs implied mean)")
    if res["sd_z"] is not None:
        print(f"  SD of standardized outcome:         {res['sd_z']:.2f}"
              "   (>1 => UNDERdispersed: tails too cheap; <1 => overdispersed: tails too rich)")
    if res["pit_lo"] is not None:
        print(f"  PIT tail mass: lo(<0.1)={res['pit_lo'] * 100:.0f}%  hi(>0.9)={res['pit_hi'] * 100:.0f}%"
              "   (expect ~10%/10% if calibrated; more => outcomes land in the tails too often)")
    print("\n  --- buckets binned by implied sigma-distance from the mean ---")
    print(f"  {'sigma':>9} {'n':>5} {'impl_price':>10} {'win%':>5} {'yes_ev/tr':>10} {'no_ev/tr':>9}")
    for sb in SIGMA_BANDS:
        n, wins, sump, yn, ypnl, nn, npnl = res["bands"][sb]
        if not n:
            continue
        label = f"{sb[0]:.1f}-{sb[1]:.1f}" if sb[1] < 90 else f"{sb[0]:.1f}+"
        yev = f"{ypnl / yn:+.1f}c" if yn else "n/a"
        nev = f"{npnl / nn:+.1f}c" if nn else "n/a"
        print(f"  {label:>9} {n:5d} {sump / n:9.1f}c {wins / n * 100:4.0f}% {yev:>10} {nev:>9}")
    print("  (win% > impl_price & yes_ev>0 => that shell underpriced -> buy YES; "
          "win% < impl_price => buy NO)")


def _shift_date(d: str, days: int) -> str | None:
    try:
        y, m, dd = (int(x) for x in d.split("-"))
        return (date(y, m, dd) + timedelta(days=days)).isoformat()
    except (ValueError, TypeError):
        return None


def leadlag(events: list[Event], fees: bool, *, htc: float = 12.0, tol: float = 4.0):
    """Cross-city W->E lead-lag. Weather propagates west->east over ~1-2 days, so a western
    city's daily anomaly may lead an eastern city's. (1) Pooled corr(anomaly) by lag,
    downwind (west leads east) vs an upwind placebo. (2) corr(east market_error[d+lag],
    west anomaly[d]) — is any lead UNpriced? (3) tradeable: buy the east bucket at
    pred = implied_east + k*west_anom[d-1], graded vs the open favorite (fees on)."""
    series: dict[tuple[str, str], list[Event]] = {}
    for ev in events:
        if ev.date and ev.winner_mid_f is not None and ev.city in CITY_LON:
            series.setdefault((ev.city, ev.kind), []).append(ev)

    rows: dict[tuple[str, str], dict[str, dict]] = {}
    for (city, kind), evs in series.items():
        mean_actual = sum(e.winner_mid_f for e in evs) / len(evs)
        d: dict[str, dict] = {}
        for ev in evs:
            cyc = _nearest_cycle(ev, htc, tol)
            implied = implied_mean_f(cyc.buckets) if cyc else None
            d[ev.date] = {"anom": ev.winner_mid_f - mean_actual,
                          "err": (ev.winner_mid_f - implied) if implied is not None else None,
                          "cyc": cyc, "winner": ev.winner}
        rows[(city, kind)] = d

    lag_dn = {lag: ([], []) for lag in (0, 1, 2)}
    lag_up = {lag: ([], []) for lag in (0, 1, 2)}
    err_dn = {lag: ([], []) for lag in (1, 2)}
    ks = (0.0, 0.5, 1.0)
    trade = {k: [0, 0, 0.0] for k in ks}
    fav = [0, 0, 0.0]

    for kind in sorted({k for _c, k in series}):
        present = [c for c in CITY_LON if (c, kind) in rows]
        for w in present:
            for e in present:
                if CITY_LON[w] >= CITY_LON[e]:
                    continue                              # need w strictly west of e
                wd, ed = rows[(w, kind)], rows[(e, kind)]
                for lag in (0, 1, 2):
                    for dt, wr in wd.items():
                        er = ed.get(_shift_date(dt, lag))
                        if er is not None:
                            lag_dn[lag][0].append(wr["anom"])
                            lag_dn[lag][1].append(er["anom"])
                            if lag in (1, 2) and er["err"] is not None:
                                err_dn[lag][0].append(wr["anom"])
                                err_dn[lag][1].append(er["err"])
                    for dt, er in ed.items():               # upwind placebo: east leads west
                        wr = wd.get(_shift_date(dt, lag))
                        if wr is not None:
                            lag_up[lag][0].append(er["anom"])
                            lag_up[lag][1].append(wr["anom"])
                for dt, wr in wd.items():                    # tradeable lead-shift at lag 1
                    er = ed.get(_shift_date(dt, 1))
                    if er is None or er["cyc"] is None:
                        continue
                    implied_e = implied_mean_f(er["cyc"].buckets)
                    if implied_e is None:
                        continue
                    for k in ks:
                        _strat_entry(trade[k], er["cyc"], er["winner"],
                                     _bucket_containing(er["cyc"].buckets, implied_e + k * wr["anom"]),
                                     fees)
                    _strat_entry(fav, er["cyc"], er["winner"], _favorite(er["cyc"].buckets), fees)

    return {
        "dn": {lag: pearson(*lag_dn[lag]) for lag in (0, 1, 2)},
        "dn_n": {lag: len(lag_dn[lag][0]) for lag in (0, 1, 2)},
        "up": {lag: pearson(*lag_up[lag]) for lag in (0, 1, 2)},
        "err": {lag: pearson(*err_dn[lag]) for lag in (1, 2)},
        "err_n": {lag: len(err_dn[lag][0]) for lag in (1, 2)},
        "trade": trade, "fav": fav,
    }


def report_leadlag(res) -> None:
    print("\n=== F) Cross-city W->E lead-lag — does a western city lead an eastern one? ===")
    print("  pooled corr(anomaly) by lag: downwind (west leads east) vs upwind placebo")
    print(f"  {'lag(d)':>7} {'downwind':>9} {'n':>6} {'upwind':>9}")
    for lag in (0, 1, 2):
        dn, up = res["dn"][lag], res["up"][lag]
        dns = f"{dn:+.2f}" if dn is not None else "n/a"
        ups = f"{up:+.2f}" if up is not None else "n/a"
        print(f"  {lag:7d} {dns:>9} {res['dn_n'][lag]:6d} {ups:>9}")
    print("    (a real W->E airmass signal => downwind corr at lag 1-2 > upwind placebo)")
    print("  corr(east market_error[d+lag], west anomaly[d])  [is the lead UNpriced?]:")
    for lag in (1, 2):
        ec = res["err"][lag]
        print(f"    lag {lag}: {ec:+.2f} (n={res['err_n'][lag]})" if ec is not None
              else f"    lag {lag}: n/a")
    print("    (>0 => east market underweights the incoming airmass -> tradeable)")
    print("\n  --- strategy: buy east bucket at pred = implied_east + k*west_anom[d-1], fees ---")
    print(f"  {'k':>5} {'n':>5} {'win%':>5} {'pnl/trade':>10}  (k=0 = implied-center baseline)")
    for k, (n, wins, pnl) in sorted(res["trade"].items()):
        if n:
            print(f"  {k:5.2f} {n:5d} {wins / n * 100:4.0f}% {pnl / n:+9.1f}c")
    fn, fw, fp = res["fav"]
    if fn:
        print(f"  {'fav':>5} {fn:5d} {fw / fn * 100:4.0f}% {fp / fn:+9.1f}c   (open-favorite baseline)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--analysis",
                    choices=("overround", "persistence", "meanrev", "longshot",
                             "convergence", "distshape", "leadlag", "both"),
                    default="both")
    ap.add_argument("--no-fees", action="store_true")
    ap.add_argument("--htc", type=float, default=12.0,
                    help="target hours-to-close for the tail-harvest (longshot) probe")
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
    if args.analysis in ("longshot", "both"):
        report_longshot(longshot(events, fees, target_htc=args.htc))
    if args.analysis in ("convergence", "both"):
        report_convergence(convergence(events, fees))
    if args.analysis in ("distshape", "both"):
        report_distshape(distshape(events, fees, target_htc=args.htc))
    if args.analysis in ("leadlag", "both"):
        report_leadlag(leadlag(events, fees))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
