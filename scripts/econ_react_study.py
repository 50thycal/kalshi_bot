"""ECON-REACT — is there a takeable post-release reaction lag on Kalshi's scheduled
economic-print markets? (idea-model Area 1, 2026-07-21; see docs/ECON_REACT_THESIS.md)

Scheduled data releases (CPI, jobs / non-farm payrolls, initial jobless claims, GDP, PCE,
PPI, ISM, retail sales, unemployment rate) drop the number at a known instant (mostly 8:30am
ET). The thesis: right after the release the winning bucket is *determined* but the ladder
converges slowly, so the winner is buyable below fair for a window — an observation-pin on a
scheduled release (the one econ angle the edge map sanctions). It promotes the parked PINNED
post-jobs-print residual (+43.4c/ct, n=36) with its own pre-registration + a release-time audit.

This probe is Kalshi-public-API-only (no external release calendar, no futures feed) and
answers, in order:

  1. STRUCTURE + CAPACITY (the testability gate) — which econ-print series exist, of what
     structure (bucket ladder vs binary threshold), and how many have SETTLED with 1-min
     candle coverage and volume. New-venue categories can be too thin to test; this says so,
     with a matched (series [category]) diagnostic so a miss/false-positive is visible.
  2. CONVERGENCE BY TIME-TO-CLOSE — for each settled event's WINNING market, the yes price and
     the buyable EV (held to settlement, net of the ceil(7*p*(1-p)) entry fee) at TTC bins.
     Shows how late the pin actually resolves.
  3. POST-RELEASE WINDOW (the load-bearing precondition) — detect the release as the winning
     market's largest 1-min up-jump; report whether the market keeps TRADING after it (a lag to
     harvest vs a settle-at-release jump) and the buyable EV at release + {1,5,15,30} minutes.

Verdict is a testability/structure call that feeds the thesis' pre-registered EV predictions:
PROMOTE-TO-EV-PROBE if a post-release tradeable window with +EV exists; HOLD if the settled
sample / candle coverage is too thin; KILL if markets settle at the release (no lag) or the
winner is already ~100c (efficient). Read-only public Kalshi REST, stdlib only. Usage:
    {"type":"script","name":"econ_react_study","args":["--max-event-pages","60"],"id":"econ-0"}
"""

from __future__ import annotations

import argparse
import math
import re
import time
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Scheduled-release econ-print series. Precision over recall: gate on a curated print-series
# allowlist AND a contaminant denylist, and dump the matched (series [category]) so a mis-filter
# stays visible. NO broad category fallback — the first run's `category == Economics` fallback
# also swept in continuous series (AAA gas, TSA throughput) that are NOT scheduled 8:30 macro
# prints. Deliberately EXCLUDES the Fed-decision complex (FEDRV's domain, a non-8:30 release).
PRINT_SERIES = re.compile(
    r"^KX("
    r"CPI|CPIYOY|CPICORE|CORECPI|"  # inflation (CPI + core)
    r"PCE|COREPCE|"                 # PCE inflation
    r"PPI|"                         # producer prices
    r"PAYROLL|NFP|JOBS|"            # employment situation (non-farm payrolls)
    r"UNRATE|UNEMP|U3|"             # unemployment rate
    r"JOBLESS|CLAIMS|ICSA|"        # weekly initial jobless claims
    r"GDP|"                         # output
    r"RETAIL|"                      # retail sales
    r"ISM|PMI|"                     # activity surveys
    r"JOLTS|ADP|"                   # job openings / private payrolls
    r"ECONSTAT"                     # Kalshi's econ-statistic release family (CPI/U3/...)
    r")", re.I)
# Continuous / non-8:30-print series that share the Economics category — excluded explicitly.
DENY_SERIES = re.compile(r"^KX(AAAGAS|USGAS|TSA)", re.I)
FED_SERIES = re.compile(r"^KX(FED|RATE|RATECUT)", re.I)  # excluded (FEDRV's domain)

# Structure classifier on the human title/subtitle (NOT the ticker). Order matters.
_RANGE = re.compile(r"\bto\b|-\s*\d|between|–|—|\d\s*%?\s*[-–]\s*\d", re.I)
_THRESHOLD = re.compile(r"above|below|greater|less than|at least|over|under|>=|<=|>|<", re.I)


def structure_of(text: str) -> str:
    t = text or ""
    if _RANGE.search(t):
        return "bucket_ladder"
    if _THRESHOLD.search(t):
        return "threshold"
    return "binary_other"


def fee_cents(price_dollars: float, qty: int = 1) -> int:
    """Kalshi per-leg fee in cents: ceil(0.07 * qty * P * (1-P) * 100), P in dollars."""
    p = min(max(price_dollars, 0.0), 1.0)
    return math.ceil(0.07 * qty * p * (1.0 - p) * 100)


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _vol(m: dict) -> float:
    return xl._num(m.get("volume_fp")) or xl._num(m.get("volume")) or 0.0


def events(status: str, max_pages: int) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status={status}&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        out.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def is_print(ev: dict, m: dict) -> bool:
    series = m.get("series_ticker") or ev.get("series_ticker") or ""
    if FED_SERIES.search(series) or DENY_SERIES.search(series):
        return False
    # Series allowlist only — no category fallback (it swept in continuous non-print series).
    return bool(PRINT_SERIES.search(series))


def candle_path(series: str, ticker: str, start: int, end: int) -> list[tuple[float, float, float, float]]:
    """[(ts, yes_ask_$, yes_price_$, volume)] over [start,end], 1-min candles, ts-sorted."""
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=1")
    rows: list[tuple[float, float, float, float]] = []
    for c in (data or {}).get("candlesticks") or []:
        ts = xl._num(c.get("end_period_ts"))
        ya = (c.get("yes_ask") or {}).get("close_dollars")
        px = (c.get("price") or {}).get("close_dollars")
        yb = (c.get("yes_bid") or {}).get("close_dollars")
        # yes_ask is the taker-buy price; fall back to price/mid then bid so a partial candle
        # still contributes a usable point rather than being dropped.
        ask = xl._num(ya) if ya is not None else (xl._num(px) if px is not None else xl._num(yb))
        last = xl._num(px) if px is not None else ask
        if ts and (ask > 0 or last > 0):
            rows.append((ts, ask, last, xl._num(c.get("volume"))))
    rows.sort(key=lambda r: r[0])
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ECON-REACT post-release reaction-lag probe")
    ap.add_argument("--max-event-pages", type=int, default=60)
    ap.add_argument("--candle-samples", type=int, default=40,
                    help="settled winning markets to pull candles for (bounds API cost)")
    ap.add_argument("--window-hours", type=float, default=8.0,
                    help="candle window before close to inspect")
    ap.add_argument("--min-ev-cents", type=float, default=2.0,
                    help="buyable-EV bar (net of fee) that counts as a takeable lag")
    args = ap.parse_args(argv)

    print("ECON-REACT — post-release reaction-lag probe on scheduled economic-print markets")
    print("(read-only Kalshi public REST; testability/structure pre-stage feeding the EV thesis)\n")

    settled_evs = events("settled", args.max_event_pages)
    open_evs = events("open", args.max_event_pages)
    print(f"scanned events: settled={len(settled_evs)} open={len(open_evs)}")

    # ---- 1. structure + capacity ------------------------------------------------------
    matched: dict[str, int] = defaultdict(int)
    struct_settled: dict[str, dict] = defaultdict(lambda: {"n": 0, "vol": 0.0, "series": set()})
    struct_open: dict[str, dict] = defaultdict(lambda: {"n": 0, "vol": 0.0, "series": set()})
    winners: list[dict] = []  # settled markets that resolved YES, with a close_time (probe pool)

    def ingest(evs: list[dict], bucket: dict, keep_winners: bool) -> None:
        for ev in evs:
            for m in ev.get("markets") or []:
                if not is_print(ev, m):
                    continue
                series = m.get("series_ticker") or ev.get("series_ticker") or ""
                cat = ev.get("category") or "?"
                matched[f"{series or '?'} [{cat}]"] += 1
                text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"),
                                              m.get("title"), m.get("subtitle")]))
                rec = bucket[structure_of(text)]
                rec["n"] += 1
                rec["vol"] += _vol(m)
                if series:
                    rec["series"].add(series)
                if keep_winners and (m.get("result") or "").lower() == "yes" and m.get("close_time"):
                    winners.append({"series": series, "ticker": m.get("ticker") or "",
                                    "close": _ts(m.get("close_time")), "vol": _vol(m),
                                    "struct": structure_of(text)})

    ingest(settled_evs, struct_settled, keep_winners=True)
    ingest(open_evs, struct_open, keep_winners=False)

    total_settled = sum(r["n"] for r in struct_settled.values())
    total_open = sum(r["n"] for r in struct_open.values())
    print(f"\nECON-PRINT markets (strict filter): settled={total_settled} open={total_open}")
    print("  matched (series [category] -> count) — verify these are genuine scheduled prints:")
    for k, c in sorted(matched.items(), key=lambda kv: -kv[1])[:18]:
        print(f"    {c:5d}  {k}")
    if not total_settled:
        print("\nVERDICT: HOLD (UNTESTABLE) — no SETTLED econ-print markets under the strict "
              "filter. Either the print-series prefixes need refreshing against live tickers, "
              "or too few have settled yet. Not a kill; re-run after more prints settle.")
        return 0

    print("\n== structure + capacity by market type ==")
    print(f"  {'structure':14s} {'settled':>8s} {'open':>6s} {'settled_vol':>12s}  series(sample)")
    for t in sorted(set(struct_settled) | set(struct_open),
                    key=lambda k: -(struct_settled[k]['n'] + struct_open[k]['n'])):
        sser = sorted(struct_settled[t]['series'] | struct_open[t]['series'])[:3]
        print(f"  {t:14s} {struct_settled[t]['n']:8d} {struct_open[t]['n']:6d} "
              f"{struct_settled[t]['vol']:12.0f}  {sser}")

    # ---- 2 + 3. convergence by time-to-close AND post-release window ------------------
    # sample the highest-volume winners (they have the deepest candle tape)
    pool = sorted([w for w in winners if w["series"] and w["ticker"] and w["close"]],
                  key=lambda w: -w["vol"])[:args.candle_samples]
    print(f"\n== convergence + post-release probe on {len(pool)} settled winning markets "
          f"(top by volume) ==")

    ttc_bins = [(240, 120), (120, 60), (60, 30), (30, 10), (10, 5), (5, 1), (1, 0)]
    ttc_px: dict[tuple, list[float]] = defaultdict(list)     # winning-bucket yes price ($)
    ttc_ev: dict[tuple, list[float]] = defaultdict(list)     # buyable EV (c) net of fee
    post_rel_trading = 0                                      # markets that trade after release
    probed = 0
    rel_ev: dict[int, list[float]] = defaultdict(list)       # buyable EV (c) at release+k min
    rel_offsets_min: list[float] = []                        # release detected at TTC (min)

    for w in pool:
        start = int(w["close"] - args.window_hours * 3600)
        end = int(w["close"] + 1200)  # a little past close to catch post-release trading
        path = candle_path(w["series"], w["ticker"], start, end)
        probed += 1
        time.sleep(0.05)
        if len(path) < 3:
            continue
        # convergence by time-to-close (robust; no release detection needed)
        for ts, ask, _last, _v in path:
            ttc_min = (w["close"] - ts) / 60.0
            if ttc_min < -1:
                continue
            for lo, hi in ttc_bins:
                if hi <= ttc_min <= lo:
                    ttc_px[(lo, hi)].append(ask)
                    ttc_ev[(lo, hi)].append((1.0 - ask) * 100 - fee_cents(ask))
                    break
        # release = largest 1-min UP move in the winning bucket's last price
        jump_i, jump_d = -1, 0.0
        for i in range(1, len(path)):
            d = path[i][2] - path[i - 1][2]
            if d > jump_d:
                jump_d, jump_i = d, i
        if jump_i > 0 and jump_d >= 0.10:  # a >=10c up-jump = the number landing
            rel_offsets_min.append((w["close"] - path[jump_i][0]) / 60.0)
            after = path[jump_i:]
            if any(v > 0 for _, _, _, v in after[1:]):
                post_rel_trading += 1
            rel_ts = path[jump_i][0]
            for k in (1, 5, 15, 30):
                # nearest candle at/after release+k, within 3 min tolerance
                target = rel_ts + k * 60
                best = None
                for ts, ask, _l, _v in after:
                    if ts >= target and (ts - target) <= 180:
                        best = ask
                        break
                if best is not None:
                    rel_ev[k].append((1.0 - best) * 100 - fee_cents(best))

    print("\n  -- CALIBRATION ONLY (hindsight): winning-bucket price by time-to-close. This "
          "CONDITIONS ON THE EVENTUAL WINNER, so the 'buyable EV' is survivorship, NOT a "
          "tradeable edge (a real trader can't pre-select the winner). Shown only to confirm "
          "the market is calibrated (winners drift toward 100c); it does NOT drive the verdict. --")
    print(f"  {'TTC (min)':>14s} {'n':>5s} {'avg yes':>8s} {'hindsight EV':>12s} {'>bar%':>7s}")
    for lo, hi in ttc_bins:
        px = ttc_px[(lo, hi)]
        ev = ttc_ev[(lo, hi)]
        if not ev:
            continue
        good = 100.0 * sum(1 for e in ev if e >= args.min_ev_cents) / len(ev)
        print(f"  {f'{hi}-{lo}':>14s} {len(ev):5d} {sum(px) / len(px):8.2f} "
              f"{sum(ev) / len(ev):10.2f} {good:6.1f}%")

    print("\n  -- post-release window (release = largest >=10c up-jump in the winner) --")
    if rel_offsets_min:
        rel_offsets_min.sort()
        med = rel_offsets_min[len(rel_offsets_min) // 2]
        print(f"  detected a release jump in {len(rel_offsets_min)}/{probed} probed; "
              f"median release at TTC {med:.0f} min")
        print(f"  markets that KEEP TRADING after the release: {post_rel_trading}"
              f"/{len(rel_offsets_min)} "
              f"({100.0 * post_rel_trading / max(1, len(rel_offsets_min)):.0f}%)")
        print(f"  {'release+k min':>14s} {'n':>5s} {'avg buyable EV(c)':>18s} {'>bar%':>7s}")
        for k in (1, 5, 15, 30):
            ev = rel_ev[k]
            if not ev:
                continue
            good = 100.0 * sum(1 for e in ev if e >= args.min_ev_cents) / len(ev)
            print(f"  {k:14d} {len(ev):5d} {sum(ev) / len(ev):18.2f} {good:6.1f}%")
    else:
        print("  no >=10c up-jumps detected — either markets settle at release (no post-release "
              "tape) or candle coverage is too sparse to see the number land.")

    # ---- verdict ---------------------------------------------------------------------
    # ONLY the post-release window is a non-lookahead edge signal: after the number is public,
    # is the DETERMINED winner still buyable while the quote lags? The TTC convergence table
    # above is hindsight (it conditions on the winner) and deliberately does NOT drive this.
    post_ev_all = [e for k in (1, 5, 15, 30) for e in rel_ev[k]]
    post_avg = sum(post_ev_all) / len(post_ev_all) if post_ev_all else 0.0
    has_post_window = bool(rel_offsets_min) and post_rel_trading > 0

    print("\n== verdict (non-lookahead: the post-release trading window only) ==")
    print(f"  settled winning markets probed:      {probed}")
    print(f"  markets with a detectable release:   {len(rel_offsets_min)}")
    print(f"  ... that KEEP TRADING after it:       {post_rel_trading}")
    print(f"  post-release avg buyable EV:          {post_avg:+.2f}c "
          f"(only meaningful when trading occurs)")
    if has_post_window and post_avg >= args.min_ev_cents:
        print("  VERDICT: PROMOTE-TO-EV-PROBE — a post-release window with ACTUAL TRADING and "
              "+EV exists; run the full pre-registered convergence/EV measurement (P1-P3) with "
              "the release-time audit next.")
    elif probed < 20:
        print("  VERDICT: HOLD — too few settled winners with candle coverage to read the "
              "window (testability-thin). Re-run after more prints settle.")
    else:
        print("  VERDICT: KILL — no post-release trading window: the winner is determined at "
              "the release, but the market does not keep trading (settles in a jump / closes at "
              "release), so there is no lagging quote to take. Per P1's kill criterion the "
              "post-release reaction-lag pin does not exist on these markets — clean ruling-out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
