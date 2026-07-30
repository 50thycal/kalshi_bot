"""mmsell CRYPTO cheap-tail backtest — can the BTC/ETH daily tail-sell become an "anchor"?

Our own paper history for this slice is n=38 settled (11 with a captured tick path, zero
losers), which cannot decide anything. This backtests the same trade over **Kalshi's own
settled history** to answer three questions:

  1. STOP-LOSS GRID — does a catastrophic stop beat hold-to-settlement, and at what level?
     Sports losses are JUMP losses (a goal is scored; 6c -> near-certain in one tick), which is
     why every stop failed in docs/MMSELL_EXIT_STUDY.md. A BTC threshold is CONTINUOUS-path
     (spot drifts toward the strike, so yes walks 6->15->30->60->100), so a stop is mechanically
     able to work here in a way it cannot on a discrete event. This measures whether it does.
  2. VOLATILITY GATES — as an ENTRY filter (skip entries when the pre-entry tape is already
     volatile: the 6c is then underpriced and we're the adversely-selected side) and as an EXIT
     rule, both swept across several lookback windows.
  3. SHORT STRANGLE — sell the cheap tail on BOTH ends of the same event (cheap YES on a high
     strike + cheap NO on a low strike). They are mutually exclusive, so you collect two
     premiums and can lose at most one. Measures whether that beats a single leg per dollar.

WHY THE TRIGGER USES THE BID/MID, NEVER THE ASK
-----------------------------------------------
A previous candle-based stop backtest (scripts/kalshi_mm_exits.py on the cheap band) returned
-93c / 100%-exit for EVERY stop rule — an artifact, not a result: at these prices thin books
throw spurious high **ask** prints, so an ask-triggered stop fires on essentially every
position on one bad tick. Here the stop TRIGGERS on the yes-MID and requires K consecutive
minutes above the level (the confirm defeats the single-print whipsaw); the ask is used only
for the EXIT PRICE, which is correct — you really do pay the ask to get out.

METHOD
------
One synthetic entry per settled market (mirroring mmsell's one-position-per-ticker rule): the
first minute whose yes-MID sits in the cheap band with time-to-close inside the holding window.
Entry assumes the resting maker order fills at that price — the same assumption the paper book
makes, so these numbers are comparable to it; the live maker adverse-selection haircut
(docs/MMSELL_FILL_MODEL.md) applies on top of anything found here.

Position = short yes at entry (== mmsell buying NO at 100-entry). Fees use the paper engine's
ceil(0.07*C*p*(1-p)) at 1 contract on BOTH legs, which is deliberately conservative (a real
maker entry is cheaper) and keeps the output comparable to our paper P&L.

Read-only public API (no auth). Usage:
    {"type": "script", "name": "mmsell_crypto_study"}
    {"type": "script", "name": "mmsell_crypto_study", "args": ["--sample", "400", "--band", "2,7"]}

The replay/vol/pairing math lives in pure functions unit-tested by
tests/test_mmsell_crypto_study.py, independent of the network.
"""

from __future__ import annotations

import argparse
import math
import statistics

import kalshi_flb as flb

# Crypto series whose cheap tails this study covers (matches the theta/mmsell crypto slice).
DEFAULT_SERIES = ("KXBTCD", "KXETHD", "KXBTC", "KXETH")

# Stop levels (yes-mid cents) to sweep, plus the K-confirm depths.
STOP_LEVELS = (12, 15, 20, 25, 30, 40)
STOP_CONFIRMS = (1, 2)
# Volatility windows (minutes) and range thresholds (cents) for the gates.
VOL_WINDOWS = (15, 30, 60, 120)
VOL_THRESHOLDS = (3, 6, 10, 15)


# ---------------------------------------------------------------- pure math


def fee_c(price_c: float, qty: int = 1) -> float:
    """Fee in CENTS per contract at a price, matching the paper engine's kalshi_fee
    (ceil(0.07*qty*p*(1-p)) rounded up to the cent, then per contract)."""
    p = price_c / 100.0
    return (math.ceil(0.07 * qty * p * (1 - p) * 100) / 100.0) * 100.0 / qty


def mid_range(mids) -> float:
    """Realized volatility proxy: peak-to-trough range of the yes-mid over a window, in cents.
    Same definition the live exit study uses, so the two are directly comparable."""
    vals = [m for m in mids if m is not None]
    if len(vals) < 2:
        return 0.0
    return max(vals) - min(vals)


def replay_short(entry_yes: float, settle_yes: float, path,
                 stop_l=None, stop_k: int = 2,
                 vol_w: int | None = None, vol_v: float | None = None,
                 trigger: str = "bid"):
    """P&L in CENTS for a short-yes (mmsell buy-NO) position, under an optional stop / vol exit.

    entry_yes  : yes price sold at, cents.
    settle_yes : 100 (longshot HIT -> we lose) or 0 (longshot missed -> we keep the premium).
    path       : post-entry minutes, time-ordered list of (yes_mid, yes_bid, yes_ask).
    stop_l/k   : exit once the TRIGGER price >= stop_l for stop_k CONSECUTIVE minutes.
    trigger    : which price arms the stop — "bid" (default, most robust: a rising BID is real
                 buying interest at that level) or "mid". NEVER the ask: at these prices thin
                 books throw spurious high asks, and even the MID is contaminated when the book
                 is wide, which is why a K=1 mid-triggered stop exits ~100% of positions.
                 Exit always PAYS THE ASK, which is what closing really costs.
    vol_w/v    : exit once the yes-mid range over the trailing vol_w minutes reaches vol_v cents.

    Returns (pnl_cents, kind) with kind in {"settle","stop","vol"}.
    """
    entry_fee = fee_c(entry_yes)
    run = 0
    window: list[float] = []
    for mid, bid, ask in path:
        if mid is None or ask is None or bid is None:
            continue
        trig = bid if trigger == "bid" else mid
        window.append(mid)
        if vol_w is not None and len(window) > vol_w:
            window.pop(0)
        # Volatility exit (needs a meaningful window before it may fire).
        if (vol_v is not None and vol_w is not None and len(window) >= max(3, vol_w // 4)
                and mid_range(window) >= vol_v):
            return (entry_yes - ask - entry_fee - fee_c(ask), "vol")
        # Confirmed catastrophic stop.
        if stop_l is not None:
            run = run + 1 if trig >= stop_l else 0
            if run >= stop_k:
                return (entry_yes - ask - entry_fee - fee_c(ask), "stop")
    return (entry_yes - settle_yes - entry_fee, "settle")


def event_key(ticker: str) -> str:
    """Event a market belongs to, for strangle pairing: drop the trailing strike/outcome segment
    (KXBTCD-26JUL1917-T64749.99 -> KXBTCD-26JUL1917)."""
    return ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker


def pair_strangle(upper, lower):
    """Combined P&L (cents) of selling BOTH cheap tails of one event.

    upper = (yes_entry, settle_yes) for the high strike, where the cheap side is YES.
    lower = (yes_entry, settle_yes) for the low strike, where the cheap side is NO — so its
            premium is the NO price (100 - yes) and it loses only if yes settles 0.
    Mutually exclusive: at most one leg can lose. Returns (total_pnl, n_legs_lost).
    """
    uy, us = upper
    ly, ls = lower
    up = uy - us - fee_c(uy)                       # short yes on the high strike
    no_entry = 100.0 - ly
    no_settle = 100.0 - ls
    lo = no_entry - no_settle - fee_c(no_entry)    # short no on the low strike
    lost = (1 if us > 50 else 0) + (1 if ls < 50 else 0)
    return (up + lo, lost)


def hold_pnl(entry) -> float:
    """Hold-to-settlement P&L (cents) for either leg type. A 'sell_yes' leg is the ordinary mmsell
    trade (short the cheap YES tail of a HIGH strike). A 'sell_no' leg is its mirror on a LOW
    strike, where the cheap side is NO — that is the second leg of a strangle."""
    if entry["side"] == "sell_yes":
        return entry["entry_yes"] - entry["settle_yes"] - fee_c(entry["entry_yes"])
    no_entry = 100.0 - entry["entry_yes"]
    no_settle = 100.0 - entry["settle_yes"]
    return no_entry - no_settle - fee_c(no_entry)


def _pctile(xs, q):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(q * (len(s) - 1))))]


def _stats(pnls):
    if not pnls:
        return None
    return {
        "n": len(pnls),
        "mean": statistics.fmean(pnls),
        "p5": _pctile(pnls, 0.05),
        "win": 100.0 * sum(1 for p in pnls if p > 0) / len(pnls),
        "total": sum(pnls),
    }


# ------------------------------------------------------------- data gather


def _first_in_band(seq, close_min, lo, hi, hlo, hhi):
    """Index of the first minute whose yes-mid is in [lo,hi] AND whose hours-to-close is inside
    [hlo,hhi]. Returns (index_or_None, saw_band_at_all) — the second flag separates "this price
    never occurred" from "it occurred but outside the holding window"."""
    saw = False
    for i, (mn, yb, ya) in enumerate(seq):
        mid = (yb + ya) / 2.0
        if lo <= mid <= hi:
            saw = True
            if hlo <= (close_min - mn) / 60.0 <= hhi:
                return i, saw
    return None, saw


def _make_entry(market, seq, i, close_min, settle_yes, side):
    """Build one entry record: prices at minute i, the pre-entry mids (for the vol entry gate)
    and the forward path (for the replay)."""
    emn, eyb, eya = seq[i]
    return {
        "ticker": market["ticker"], "event": event_key(market["ticker"]),
        "series": market["series"], "side": side,
        "entry_yes": (eyb + eya) / 2.0, "settle_yes": settle_yes,
        "htc_at_entry": (close_min - emn) / 60.0,
        "pre_mids": [(y + a) / 2.0 for _mn, y, a in seq[:i]],
        "path": [((y + a) / 2.0, y, a) for _mn, y, a in seq[i + 1:]],
    }


def build_entries(series_list, per_series, min_vol, sample, band, htc, path_days, verbose=True):
    """One synthetic entry per settled market: first minute whose yes-mid is in `band` with
    time-to-close inside `htc` hours. Returns (entries, markets_scanned).

    entry dict: ticker, event, series, entry_yes, settle_yes, pre_mids (before entry),
                path [(mid, ask)] after entry.
    """
    blo, bhi = band
    hlo, hhi = htc
    settled = []
    for s in series_list:
        got = flb.fetch_settled_for_series(s, per_series, min_vol)
        if verbose:
            print(f"  {s}: {len(got)} settled markets")
        settled.extend(got)
    if not settled:
        return [], 0
    step = max(1, len(settled) // sample)
    chosen = settled[::step][:sample]

    entries = []
    # Rejection counters — a zero-entry run is otherwise undiagnosable (is it no candles? a
    # band that never occurs? an htc window that excludes these markets entirely?).
    diag = {"no_candles": 0, "never_in_band": 0, "band_but_htc": 0, "ok": 0}
    # Per-market summaries (one row each, so the distribution is unbiased — an append-every-tick
    # sample with a cap would just describe the first few markets).
    per_mkt: list[tuple[str, float, float]] = []   # (series, max_htc_available, min_mid_seen)
    for m in chosen:
        close = m["close"]
        candles = flb.fetch_candles(m["series"], m["ticker"],
                                    close - int(path_days * 86400), close + 60)
        if not candles:
            diag["no_candles"] += 1
            continue
        seq = sorted((mn, yb * 100.0, ya * 100.0) for mn, (yb, ya) in candles.items())
        settle_yes = 100.0 if m["result"] == "yes" else 0.0
        close_min = close // 60
        max_htc = (close_min - seq[0][0]) / 60.0 if seq else 0.0
        min_mid = min((yb + ya) / 2.0 for _mn, yb, ya in seq) if seq else float("nan")
        per_mkt.append((m["series"], max_htc, min_mid))

        # Leg 1: the ordinary mmsell trade — short the cheap YES tail (a HIGH strike).
        ei, saw_band = _first_in_band(seq, close_min, blo, bhi, hlo, hhi)
        # Leg 2 (strangle only): the mirror — short the cheap NO tail (a LOW strike), i.e. yes
        # trading near 100. Selected purely by price, which guarantees low_strike < high_strike,
        # which in turn is what makes the two legs mutually exclusive.
        li, _ = _first_in_band(seq, close_min, 100.0 - bhi, 100.0 - blo, hlo, hhi)
        if ei is None and li is None:
            diag["band_but_htc" if saw_band else "never_in_band"] += 1
            continue
        diag["ok"] += 1
        if ei is not None:
            entries.append(_make_entry(m, seq, ei, close_min, settle_yes, "sell_yes"))
        if li is not None:
            entries.append(_make_entry(m, seq, li, close_min, settle_yes, "sell_no"))
    if verbose:
        print(f"  rejects: no_candles={diag['no_candles']} never_in_band={diag['never_in_band']} "
              f"in_band_but_htc_window={diag['band_but_htc']} -> kept={diag['ok']}")
        by_s: dict[str, list] = {}
        for s, mh, mm in per_mkt:
            by_s.setdefault(s, []).append((mh, mm))
        print(f"  {'series':9s} {'mkts':>5} {'candle-hours available (max htc)':>34} "
              f"{'cheapest mid seen':>18}")
        for s in sorted(by_s):
            hs = sorted(x[0] for x in by_s[s])
            ms = sorted(x[1] for x in by_s[s])
            def q(xs, p):
                return xs[max(0, min(len(xs) - 1, int(p * (len(xs) - 1))))]
            print(f"  {s:9s} {len(by_s[s]):>5}   median={q(hs,0.5):>6.2f}h  "
                  f"p90={q(hs,0.9):>7.2f}h        median={q(ms,0.5):>5.1f}c")
        if entries:
            eh = sorted(e["htc_at_entry"] for e in entries)
            print(f"  entries' htc at entry: median={eh[len(eh)//2]:.2f}h "
                  f"min={eh[0]:.2f}h max={eh[-1]:.2f}h")
    return entries, len(chosen)


# ----------------------------------------------------------------- reports


def report_stops(entries):
    print("\n=== 1) STOP-LOSS GRID — confirmed stop vs hold (cheap-YES legs = the mmsell trade) ===")
    entries = [e for e in entries if e["side"] == "sell_yes"]
    if not entries:
        print("  (no cheap-YES legs)")
        return
    hold = [replay_short(e["entry_yes"], e["settle_yes"], e["path"])[0] for e in entries]
    hs = _stats(hold)
    print(f"  baseline HOLD: n={hs['n']} mean={hs['mean']:+.2f}c p5={hs['p5']:+.1f}c "
          f"win={hs['win']:.1f}% total={hs['total']/100:+.2f}$")
    print(f"  {'rule':14s} {'mean_c':>7} {'p5_c':>7} {'win%':>6} {'%exit':>6} "
          f"{'dmean':>7} {'dp5':>7}")
    rows = []
    for trig in ("bid", "mid"):
        print(f"  --- trigger on yes-{trig.upper()} "
              f"{'(robust: real buying interest)' if trig == 'bid' else '(contaminated by wide books)'}")
        for k in STOP_CONFIRMS:
            for lvl in STOP_LEVELS:
                out = [replay_short(e["entry_yes"], e["settle_yes"], e["path"],
                                    stop_l=lvl, stop_k=k, trigger=trig) for e in entries]
                pnls = [p for p, _ in out]
                ex = 100.0 * sum(1 for _, kind in out if kind != "settle") / len(out)
                st = _stats(pnls)
                label = f"{trig} L{lvl} K{k}"
                rows.append((st["mean"], label, st, ex))
                print(f"  {label:14s} {st['mean']:+7.2f} {st['p5']:+7.1f} "
                      f"{st['win']:5.1f}% {ex:5.0f}% {st['mean']-hs['mean']:+7.2f} "
                      f"{st['p5']-hs['p5']:+7.1f}")
    best = max(rows, key=lambda r: r[0])
    verdict = "BEATS hold" if best[0] > hs["mean"] else "hold WINS"
    print(f"  -> best by mean: {best[1]} ({best[0]:+.2f}c vs hold {hs['mean']:+.2f}c) — {verdict}")
    print("  (a stop is only worth it if it lifts p5 while barely denting mean. Compare the two")
    print("   trigger blocks: a huge %exit on the MID block at K=1 is the thin-book artifact, not")
    print("   a real signal — that is precisely why the BID is the default trigger.)")


def report_vol_entry(entries):
    print("\n=== 2a) VOLATILITY ENTRY GATE — outcome by PRE-ENTRY tape volatility ===")
    entries = [e for e in entries if e["side"] == "sell_yes"]
    print("  Thesis: if the tape is already moving before we rest, the cheap tail is underpriced")
    print("  and we are the adversely-selected side. Skipping those entries should help.")
    for w in VOL_WINDOWS:
        buckets: dict[str, list] = {}
        for e in entries:
            pre = e["pre_mids"][-w:]
            if len(pre) < max(3, w // 4):
                continue                       # not enough pre-entry tape to judge
            rng = mid_range(pre)
            key = ("a_calm <3c" if rng < 3 else "b_mild 3-6c" if rng < 6
                   else "c_active 6-10c" if rng < 10 else "d_wild >=10c")
            pnl = replay_short(e["entry_yes"], e["settle_yes"], e["path"])[0]
            buckets.setdefault(key, []).append(pnl)
        if not buckets:
            continue
        print(f"\n  lookback W={w}min")
        print(f"    {'pre-entry range':16s} {'n':>5} {'mean_c':>7} {'p5_c':>7} {'win%':>6}")
        for key in sorted(buckets):
            st = _stats(buckets[key])
            print(f"    {key:16s} {st['n']:>5} {st['mean']:+7.2f} {st['p5']:+7.1f} "
                  f"{st['win']:5.1f}%")


def report_vol_exit(entries):
    print("\n=== 2b) VOLATILITY EXIT GATE — bail when the held position gets volatile ===")
    entries = [e for e in entries if e["side"] == "sell_yes"]
    if not entries:
        print("  (no cheap-YES legs)")
        return
    hold = [replay_short(e["entry_yes"], e["settle_yes"], e["path"])[0] for e in entries]
    hs = _stats(hold)
    print(f"  baseline HOLD: mean={hs['mean']:+.2f}c p5={hs['p5']:+.1f}c win={hs['win']:.1f}%")
    print(f"  {'W':>4} {'V':>4} {'mean_c':>7} {'p5_c':>7} {'win%':>6} {'%exit':>6} "
          f"{'dmean':>7} {'dp5':>7}")
    best = None
    for w in VOL_WINDOWS:
        for v in VOL_THRESHOLDS:
            out = [replay_short(e["entry_yes"], e["settle_yes"], e["path"], vol_w=w, vol_v=v)
                   for e in entries]
            pnls = [p for p, _ in out]
            ex = 100.0 * sum(1 for _, k in out if k != "settle") / len(out)
            st = _stats(pnls)
            if best is None or st["mean"] > best[0]:
                best = (st["mean"], f"W={w} V={v}")
            print(f"  {w:>4} {v:>4} {st['mean']:+7.2f} {st['p5']:+7.1f} {st['win']:5.1f}% "
                  f"{ex:5.0f}% {st['mean']-hs['mean']:+7.2f} {st['p5']-hs['p5']:+7.1f}")
    if best:
        v = "BEATS hold" if best[0] > hs["mean"] else "hold WINS"
        print(f"  -> best: {best[1]} ({best[0]:+.2f}c vs hold {hs['mean']:+.2f}c) — {v}")


def report_strangle(entries):
    print("\n=== 3) SHORT STRANGLE — cheap-YES (high strike) + cheap-NO (low strike), same event ===")
    print("  Both legs are selected purely on price, which forces low_strike < high_strike — that")
    print("  ordering is what makes them MUTUALLY EXCLUSIVE (BTC cannot finish above the high")
    print("  strike AND below the low strike), so at most one leg can lose.")
    by_event: dict[str, dict] = {}
    for e in entries:
        ev = by_event.setdefault(e["event"], {"sell_yes": [], "sell_no": []})
        ev[e["side"]].append(e)
    uppers = [e for e in entries if e["side"] == "sell_yes"]
    lowers = [e for e in entries if e["side"] == "sell_no"]
    paired_events = [v for v in by_event.values() if v["sell_yes"] and v["sell_no"]]
    print(f"  legs found: {len(uppers)} cheap-YES, {len(lowers)} cheap-NO across "
          f"{len(by_event)} events; {len(paired_events)} events have BOTH -> strangle-able")

    su = _stats([hold_pnl(e) for e in uppers]) if uppers else None
    sl = _stats([hold_pnl(e) for e in lowers]) if lowers else None
    if su:
        print(f"\n  cheap-YES leg alone : n={su['n']:>4} mean={su['mean']:+.2f}c "
              f"p5={su['p5']:+.1f}c win={su['win']:.1f}%")
    if sl:
        print(f"  cheap-NO  leg alone : n={sl['n']:>4} mean={sl['mean']:+.2f}c "
              f"p5={sl['p5']:+.1f}c win={sl['win']:.1f}%")
    if not paired_events:
        print("\n  No event had BOTH a cheap-YES and a cheap-NO leg in this sample — the true")
        print("  two-sided strangle could not be tested. (Widen --band or raise --sample.)")
        return
    pairs = []
    for v in paired_events:
        a = min(v["sell_yes"], key=lambda x: x["entry_yes"])          # cheapest YES tail
        b = max(v["sell_no"], key=lambda x: x["entry_yes"])           # cheapest NO tail
        total, lost = pair_strangle((a["entry_yes"], a["settle_yes"]),
                                    (b["entry_yes"], b["settle_yes"]))
        risk = (100.0 - a["entry_yes"]) + (100.0 - b["entry_yes"])    # capital deployed, cents
        pairs.append((total, risk, lost))
    tot = [p for p, _r, _l in pairs]
    per_dollar = [p / (r / 100.0) for p, r, _l in pairs]
    both_lost = sum(1 for _p, _r, lost in pairs if lost == 2)
    ps, pd = _stats(tot), _stats(per_dollar)
    print(f"\n  STRANGLE (paired)   : n={ps['n']:>4} mean={ps['mean']:+.2f}c per PAIR "
          f"p5={ps['p5']:+.1f}c win={ps['win']:.1f}%")
    print(f"  per-dollar deployed : strangle {pd['mean']:+.2f}c/$", end="")
    if su:
        single_pd = statistics.fmean([hold_pnl(e) / ((100.0 - e["entry_yes"]) / 100.0)
                                      for e in uppers])
        print(f"   vs single cheap-YES leg {single_pd:+.2f}c/$")
    else:
        print()
    print(f"  BOTH legs lost in {both_lost}/{len(pairs)} pairs "
          f"({100.0*both_lost/len(pairs):.1f}%) — MUST be 0% or the exclusivity claim is broken.")
    print("  (Exclusivity caps the worst case; it does NOT add edge per dollar. Its real value is")
    print("   throughput when qualifying markets are scarce — and it is a pure SHORT-VOL bet, so")
    print("   it amplifies exactly the risk the entry vol gate above exists to control.)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="mmsell crypto cheap-tail backtest")
    ap.add_argument("--series", default=",".join(DEFAULT_SERIES))
    ap.add_argument("--per-series", type=int, default=400)
    ap.add_argument("--sample", type=int, default=300, help="markets to pull candles for")
    ap.add_argument("--min-vol", type=float, default=50.0)
    ap.add_argument("--band", default="2,7", help="cheap yes-mid entry band, cents lo,hi")
    ap.add_argument("--htc", default="1,48", help="hours-to-close window at entry, lo,hi")
    ap.add_argument("--path-days", type=float, default=3.0)
    args = ap.parse_args(argv)

    band = tuple(float(x) for x in args.band.split(","))
    htc = tuple(float(x) for x in args.htc.split(","))
    series_list = [s.strip().upper() for s in args.series.split(",") if s.strip()]

    print(f"=== mmsell crypto cheap-tail backtest — yes {band[0]:.0f}-{band[1]:.0f}c, "
          f"htc {htc[0]:.0f}-{htc[1]:.0f}h, series {','.join(series_list)} ===")
    entries, scanned = build_entries(series_list, args.per_series, args.min_vol,
                                     args.sample, band, htc, args.path_days)
    print(f"  candles pulled for {scanned} markets -> {len(entries)} qualifying entries")
    if len(entries) < 20:
        print("\n  Too few qualifying entries to draw conclusions. Widen --band/--htc, raise")
        print("  --sample/--per-series, or check that these series have cheap-tail markets.")
        if not entries:
            return 0
    uppers = [e for e in entries if e["side"] == "sell_yes"]
    if uppers:
        lo = sum(1 for e in uppers if e["settle_yes"] > 50)
        print(f"  cheap-YES legs: {len(uppers)}, of which losers {lo} "
              f"({100.0*lo/len(uppers):.1f}%) — the tail a stop would target")

    report_stops(entries)
    report_vol_entry(entries)
    report_vol_exit(entries)
    report_strangle(entries)
    print("\n  Entry assumes the resting maker order filled at the quoted mid (same assumption")
    print("  the paper book makes). The live maker adverse-selection haircut applies on top —")
    print("  see docs/MMSELL_FILL_MODEL.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
