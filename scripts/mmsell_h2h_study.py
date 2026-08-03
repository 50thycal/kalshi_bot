"""mmsell HEAD-TO-HEAD STUDY — why do in-play game/match winners carry the tail, and is the
structure predictable enough to filter on?

THE FINDING THIS EXTENDS
------------------------
docs/MMSELL_ROADMAP.md Sec 9: deduplicated to distinct markets, the cheap band (yes <= 7c) runs a
3.8% loss rate against a 5.5% break-even — except on head-to-head "who wins" markets, where it
inverts:

    KXMLBGAME  n=27  11.1% loss  -5.70c/trade      (avg hold 1.2h -> deep in-play)
    KXATPMATCH n=40  10.0% loss  -4.65c/trade
    ...every total / spread / prop / scheduled-settle cell:  <= 3.5% loss

That is a strong mechanistic signal on a sample too small to act on: n=27 with 3 losses has a 95%
CI of [3.9%, 28.1%], which does not exclude break-even. Our own paper history cannot fix that — it
accrues ~800 cheap markets a QUARTER across the whole book. So this study goes to Kalshi's own
settled history for the population our paper book cannot supply.

THE STRUCTURAL HYPOTHESIS BEING TESTED
--------------------------------------
Not "baseball is bad" — that is a label, not a mechanism, and it does not generalize. The
hypothesis is about **clock discipline**:

    In a CLOCKED sport (soccer, basketball, football, hockey) time monotonically extinguishes
    comeback paths. A team down 2 scores with 3 minutes left is nearly dead, and the market can
    price that from the clock alone.

    In an UNCLOCKED sport (baseball, tennis, cricket, golf) the trailing side always retains a
    live path. There is no way to run out the clock; outs, games and holes remaining are not
    time-bounded, and a rally has no upper limit. The market's cheap tail is therefore
    systematically too cheap late in the contest.

Prediction, pre-registered: cheap-tail selling on UNCLOCKED h2h markets shows a realized loss rate
materially above its implied rate, while CLOCKED h2h markets do not. If the split does NOT appear,
the roadmap's KXMLBGAME reading was small-sample noise and the h2h exclusion should not be built.

Both halves matter. A confirmed split is a filter with a reason; a null is a kill that saves us
from cutting 3.4% of flow for nothing.

METHOD (public read-only API, no auth)
--------------------------------------
Per h2h series: pull settled binaries, then per market pull 1-min candles and take ONE synthetic
entry at the first minute whose yes-MID sits in the cheap band inside the in-play window — the
same one-entry-per-ticker rule the live book follows. Entry SELLS yes at the yes-ask (== mmsell
buying NO at the no-bid), held to settlement, net of the paper fee model, so the numbers are
directly comparable to our paper books.

TWO TRAPS THIS INHERITS (both documented in docs/MMSELL_CRYPTO_STUDY.md — read before trusting
any number here):
  * Kalshi serves only a limited candle window for many series. The script reports COVERAGE per
    series; a series whose entries all land at htc < 1h is a different population from what the
    book trades, and is labelled as such rather than pooled silently.
  * Thin-book quotes make the mid a fiction. Entry uses the yes-ASK as the sell price (what a
    maker would actually receive), never the mid, and markets with no two-sided quote are dropped.

Usage:
    {"type": "script", "name": "mmsell_h2h_study"}
    {"type": "script", "name": "mmsell_h2h_study", "args": ["--sample", "600", "--band", "3,8"]}

The classification and P&L maths are pure functions unit-tested in tests/test_mmsell_h2h_study.py,
independent of the network.
"""

from __future__ import annotations

import argparse
import math
import statistics
from collections import defaultdict

import kalshi_flb as flb

# --- the classification under test -------------------------------------------------------
# Explicit table rather than a heuristic: this split IS the hypothesis, so it has to be
# auditable and falsifiable rather than inferred from a series name at runtime.
#
# clock=True  -> a game clock monotonically extinguishes comeback paths
# clock=False -> the trailing side always retains a live path (outs / games / holes remaining)
H2H_SERIES = (
    # --- unclocked: the hypothesis says these carry the fat cheap tail
    ("KXMLBGAME", "baseball", False),
    ("KXATPMATCH", "tennis", False),
    ("KXATPCHALLENGERMATCH", "tennis", False),
    ("KXWTAMATCH", "tennis", False),
    ("KXITFMATCH", "tennis", False),
    ("KXITFWMATCH", "tennis", False),
    ("KXT20MATCH", "cricket", False),
    ("KXODIMATCH", "cricket", False),
    # --- clocked: the hypothesis says these behave like the rest of the book
    ("KXNBAGAME", "basketball", True),
    ("KXWNBAGAME", "basketball", True),
    ("KXNBASUMMERGAME", "basketball", True),
    ("KXNFLGAME", "football", True),
    ("KXNHLGAME", "hockey", True),
    ("KXWCGAME", "soccer", True),
    ("KXLIGAMXGAME", "soccer", True),
    ("KXCLUBFGAME", "soccer", True),
    ("KXEPLGAME", "soccer", True),
    ("KXUCLGAME", "soccer", True),
)


def classify(series: str):
    """(sport, clocked) for a series ticker, or None when it is not a head-to-head winner market.

    Longest-prefix match, so KXATPCHALLENGERMATCH is not swallowed by KXATPMATCH."""
    s = (series or "").upper()
    best = None
    for prefix, sport, clocked in H2H_SERIES:
        if s.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, sport, clocked)
    return (best[1], best[2]) if best else None


def fee_c(price_c: float) -> float:
    """Kalshi fee in CENTS/contract — matches the paper engine's ceil(7*p*(1-p)).

    NOTE (docs/MMSELL_ROADMAP.md Sec 2): live MAKER fills were measured at ~0.013c/contract, so
    this is conservative by ~1c for a resting entry. It is kept here deliberately so these numbers
    stay directly comparable to our paper books, which charge the same. Any absolute-cent gate
    read off this script inherits that ~1c pessimism."""
    p = price_c / 100.0
    return math.ceil(7.0 * p * (1.0 - p))


def sell_yes_pnl(sell_price_c: float, settled_yes: bool) -> float:
    """Hold-to-settlement P&L in cents for selling one YES contract at `sell_price_c`.

    Wins (keeps the premium) when the cheap tail MISSES; loses (100 - premium) when it hits.
    Same convention as the paper book: mmsell sells yes at the ask == buys NO at the no-bid."""
    if settled_yes:
        return -(100.0 - sell_price_c) - fee_c(sell_price_c)
    return sell_price_c - fee_c(sell_price_c)


def break_even_loss_rate(sell_price_c: float) -> float:
    """The loss rate at which selling at this price is exactly flat, as a percentage.

    win = sell_price - fee, loss = (100 - sell_price) + fee; break-even q = win / (win + loss)."""
    fee = fee_c(sell_price_c)
    win = sell_price_c - fee
    loss = (100.0 - sell_price_c) + fee
    if win + loss <= 0:
        return float("nan")
    return 100.0 * win / (win + loss)


def first_entry(seq, close_min: int, lo: float, hi: float, htc_max_h: float):
    """Index of the first minute whose yes-MID is in [lo,hi] with time-to-close <= htc_max_h.

    One entry per ticker, mirroring the live book's one-position-per-ticker rule. Returns
    (index_or_None, saw_band) so "this price never occurred" is distinguishable from "it occurred
    but outside the in-play window"."""
    saw = False
    for i, (mn, yb, ya) in enumerate(seq):
        if yb is None or ya is None or ya <= 0:
            continue
        mid = (yb + ya) / 2.0
        if lo <= mid <= hi:
            saw = True
            if 0 <= (close_min - mn) / 60.0 <= htc_max_h:
                return i, saw
    return None, saw


def _pctile(xs, q):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))]


def summarize(entries) -> dict | None:
    """Aggregate a cohort of entries into the decision numbers. Pure; unit-tested."""
    if not entries:
        return None
    pnls = [e["pnl"] for e in entries]
    losses = sum(1 for e in entries if e["settled_yes"])
    n = len(entries)
    implied = statistics.fmean(e["sell_px"] for e in entries)
    return {
        "n": n,
        "losses": losses,
        "loss_pct": 100.0 * losses / n,
        "implied_pct": implied,                      # the price IS the market's implied loss rate
        "break_even_pct": break_even_loss_rate(implied),
        "mean": statistics.fmean(pnls),
        "p5": _pctile(pnls, 0.05),
        "total": sum(pnls),
        "htc": statistics.fmean(e["htc"] for e in entries),
    }


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def build_entries(series_list, per_series, min_vol, sample, band, htc_max, verbose=True):
    """One synthetic entry per settled h2h market. Returns (entries, scanned, coverage_notes)."""
    lo, hi = band
    entries, scanned = [], 0
    notes = []
    for series in series_list:
        meta = classify(series)
        if meta is None:
            continue
        sport, clocked = meta
        markets = flb.fetch_settled_for_series(series, per_series, min_vol)
        if not markets:
            continue
        markets = markets[:sample]
        got = saw_band = 0
        for m in markets:
            scanned += 1
            close = m["close"]
            candles = flb.fetch_candles(series, m["ticker"], close - int(htc_max * 3600) - 3600,
                                        close)
            if not candles:
                continue
            # kalshi_flb.fetch_candles returns DOLLARS (the API's close_dollars, e.g. "0.6200");
            # every consumer scales to cents at the call site — see mmsell_crypto_study. Skipping
            # this makes every mid ~0-1 so no cent-denominated band ever matches, and the study
            # reports a clean-looking "the price never occurred" null instead of a unit bug.
            seq = sorted((mn, yb * 100.0, ya * 100.0) for mn, (yb, ya) in candles.items())
            idx, saw = first_entry(seq, close // 60, lo, hi, htc_max)
            saw_band += 1 if saw else 0
            if idx is None:
                continue
            mn, yb, ya = seq[idx]
            settled_yes = m["result"] == "yes"
            entries.append({
                "ticker": m["ticker"], "series": series, "sport": sport, "clocked": clocked,
                "sell_px": float(ya),          # the maker SELLS yes at the ask
                "settled_yes": settled_yes,
                "htc": (close // 60 - mn) / 60.0,
                "pnl": sell_yes_pnl(float(ya), settled_yes),
            })
            got += 1
        if verbose:
            print(f"  {series:<24} {sport:<11} {'clocked' if clocked else 'UNCLOCKED':<9} "
                  f"markets={len(markets):>4} in-band={saw_band:>4} entries={got:>4}")
        if got and saw_band and got < saw_band * 0.5:
            notes.append(f"{series}: only {got}/{saw_band} in-band markets had an entry inside "
                         f"the {htc_max:g}h window (candle history may be truncated)")
    return entries, scanned, notes


def _diagnose(series_list, per_series, min_vol, htc_max) -> None:
    """Why did the candle fetch come back empty? Distinguish the three causes that look identical
    from the outside: no settled markets, an API/field-name change, and a candle window that
    simply does not reach back to these settlements.

    This exists because the first run of this study reported in-band=0 for all 16 series, which
    reads like a clean null ('the price band never occurs') when it was actually zero candles.
    An empty dataset must never be presentable as a result."""
    import xvenue_leadlag as xl

    for series in series_list:
        markets = flb.fetch_settled_for_series(series, per_series, min_vol)
        if not markets:
            print(f"  {series}: no settled markets returned — nothing to diagnose here")
            continue
        m = markets[0]
        close = m["close"]
        start = close - int(htc_max * 3600) - 3600
        url = (f"{flb.KALSHI}/series/{series}/markets/{m['ticker']}/candlesticks"
               f"?start_ts={start}&end_ts={close}&period_interval=1")
        raw = xl._get(url)
        print(f"\n  probe series={series} ticker={m['ticker']}")
        print(f"    window   {start}..{close} ({(close - start) / 3600:.1f}h before close)")
        print(f"    response {'None (HTTP error/unreachable)' if raw is None else type(raw).__name__}"
              f"{'' if raw is None else ' keys=' + str(sorted(raw)[:8])}")
        if isinstance(raw, dict):
            cs = raw.get("candlesticks")
            print(f"    candlesticks: {0 if not cs else len(cs)}")
            if cs:
                c0 = cs[0]
                print(f"    sample keys: {sorted(c0)}")
                print(f"    sample yes_bid={c0.get('yes_bid')!r} yes_ask={c0.get('yes_ask')!r}")
                print("    -> candles EXIST, so this is NOT a retention limit. Check the UNIT: "
                      "fetch_candles\n       returns dollars ('0.6200' = 62c) and callers must "
                      "scale by 100 before applying a\n       cent-denominated band.")
            else:
                print("    -> candles are EMPTY for this window; Kalshi's candle retention does "
                      "not reach\n       these settlements. A historical backtest on this series "
                      "is not available.")
        return  # one probe is enough to identify the cause


def _row(label, st, extra=""):
    if st is None:
        return f"{label:<22} {'-- no entries --':>10}"
    lo, hi = wilson(st["losses"], st["n"])
    return (f"{label:<22} {st['n']:>5} {st['losses']:>7} {st['loss_pct']:>7.1f}% "
            f"[{100 * lo:>4.1f},{100 * hi:>5.1f}]% {st['break_even_pct']:>8.1f}% "
            f"{st['mean']:>8.2f} {st['p5']:>7.1f} {st['htc']:>7.1f}h {extra}")


_HEAD = (f"{'cohort':<22} {'n':>5} {'losses':>7} {'loss%':>8} {'95% CI':>14} "
         f"{'break-even':>9} {'c/trade':>8} {'p5':>7} {'htc':>8}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--band", default="3,10", help="entry yes-MID band in cents, lo,hi")
    ap.add_argument("--htc-max", type=float, default=6.0,
                    help="only take entries within this many hours of close (in-play window)")
    ap.add_argument("--per-series", type=int, default=400)
    ap.add_argument("--sample", type=int, default=250, help="markets per series to pull candles for")
    ap.add_argument("--min-volume", type=float, default=100.0)
    args = ap.parse_args(argv)
    lo, hi = (float(x) for x in args.band.split(","))

    print("=" * 100)
    print(f"MMSELL HEAD-TO-HEAD STUDY — sell the cheap YES tail, band {lo:g}-{hi:g}c, "
          f"in-play window <= {args.htc_max:g}h")
    print("hypothesis: UNCLOCKED sports (no way to run out the clock) underprice the late "
          "comeback; CLOCKED sports do not")
    print("=" * 100)
    print("\nCollecting (one entry per settled market):")

    series_list = [p for p, _s, _c in H2H_SERIES]
    entries, scanned, notes = build_entries(
        series_list, args.per_series, args.min_volume, args.sample, (lo, hi), args.htc_max)

    if not entries:
        print("\nNo entries built — running the candle diagnostic before concluding anything.")
        _diagnose(series_list, args.per_series, args.min_volume, args.htc_max)
        print("\nNothing can be concluded from the absence of entries — this is a "
              "data-availability\nresult, not a null. Fix the collection, then re-run.")
        return 0

    print(f"\nscanned {scanned} settled markets -> {len(entries)} entries")
    for n in notes:
        print(f"  COVERAGE WARNING: {n}")

    unclocked = [e for e in entries if not e["clocked"]]
    clocked = [e for e in entries if e["clocked"]]
    su, sc = summarize(unclocked), summarize(clocked)

    print("\n1) THE HYPOTHESIS — unclocked vs clocked head-to-head")
    print(_HEAD)
    print(_row("UNCLOCKED", su))
    print(_row("CLOCKED", sc))
    print(_row("all h2h", summarize(entries)))

    if su and sc:
        print("\n   loss% ABOVE break-even is the tell (the market's price is its implied loss "
              "rate):")
        for name, st in (("UNCLOCKED", su), ("CLOCKED", sc)):
            gap = st["loss_pct"] - st["break_even_pct"]
            lo_ci, hi_ci = wilson(st["losses"], st["n"])
            verdict = ("LOSES — CI excludes break-even" if 100 * lo_ci > st["break_even_pct"]
                       else "EARNS — CI excludes break-even" if 100 * hi_ci < st["break_even_pct"]
                       else "UNDECIDED at this n")
            print(f"     {name:<10} realized {st['loss_pct']:.1f}% vs break-even "
                  f"{st['break_even_pct']:.1f}%  ({gap:+.1f}pp)  -> {verdict}")

    print("\n2) BY SPORT")
    print(_HEAD)
    by_sport = defaultdict(list)
    for e in entries:
        by_sport[(e["sport"], e["clocked"])].append(e)
    for (sport, clk), es in sorted(by_sport.items(), key=lambda kv: (kv[0][1], -len(kv[1]))):
        st = summarize(es)
        if st and st["n"] >= 10:
            print(_row(f"{sport} ({'clk' if clk else 'UNCLK'})", st))

    print("\n3) BY TIME TO CLOSE — the comeback window should tighten as the contest ends")
    print(_HEAD)
    buckets = (("a. <1h", 0, 1), ("b. 1-2h", 1, 2), ("c. 2-4h", 2, 4), ("d. 4h+", 4, 1e9))
    for label, blo, bhi in buckets:
        for name, cohort in (("UNCLK", unclocked), ("CLK", clocked)):
            es = [e for e in cohort if blo <= e["htc"] < bhi]
            st = summarize(es)
            if st and st["n"] >= 10:
                print(_row(f"{label} {name}", st))

    print("\n4) BY ENTRY PRICE")
    print(_HEAD)
    for name, cohort in (("UNCLK", unclocked), ("CLK", clocked)):
        for plo, phi in ((0, 5), (5, 8), (8, 11), (11, 100)):
            es = [e for e in cohort if plo <= e["sell_px"] < phi]
            st = summarize(es)
            if st and st["n"] >= 10:
                print(_row(f"{plo}-{phi}c {name}", st))

    print("\nGATE (docs/MMSELL_ROADMAP.md Sec 9): the h2h exclusion is built only if UNCLOCKED's "
          "realized\nloss rate exceeds its break-even with the 95% CI excluding break-even, AND "
          "CLOCKED does not.\nAnything else means the KXMLBGAME reading was small-sample noise "
          "and the exclusion is not justified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
