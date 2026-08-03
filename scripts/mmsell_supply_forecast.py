"""mmsell SUPPLY FORECAST — what will actually be tradeable in the coming weeks, and when it settles.

Our entire mmsell history is one regime: ~16 days of summer sports (World Cup props, MLB
totals/spreads, tennis, WNBA, PGA) plus BTC daily and a few news series. Sept-Nov brings NFL
(weekly, Sept), MLB playoffs (Oct), NBA/NHL openings (Oct) and the November midterms — none of
which we have a single settled paper trade on. The World Cup collapse already showed what a
regime change does to this book: entries fell ~5x and the best cell vanished. This script is the
forward half of the fix (the edge half is scripts/mmsell_regime_backtest.py): it forecasts the
SUPPLY of tradeable markets by regime and week, and the settlement-date CONCENTRATION that
supply implies.

Four questions, four sections:

  1. LIVE SUPPLY — how many markets clear the mmsell10 entry filter right now, by regime. The
     baseline any forecast is read against.
  2. WINDOW-ENTRY CALENDAR — the assumption-free forward look. mmsell only trades inside
     htc <= 336h (14 days), so a market closing in November is not supply until late October.
     Every already-listed cheap market is bucketed by the week it ENTERS that window
     (close - 14d). No modeling: these markets exist today and their close dates are published.
  3. SEASONAL CADENCE — the trailing settled-market cadence per regime, and an explicit report
     of the RETENTION WALL. Kalshi serves only a rolling ~70-day settled window (paging to
     cursor exhaustion bottoms out on the same date for every series; KXNFLGAME returns zero),
     so a prior-year comparison cannot be computed from this API at any page budget. What the
     section does show is which regimes are decaying and which are arriving inside that window.
  4. SETTLEMENT-DATE CONCENTRATION — the risk the forecast exists to surface. mmsell's risk
     model is "many small independent positions"; 60 positions settling on one election night
     is closer to ONE position at 60x size. Reports the busiest settlement dates as a fraction
     of the position caps, plus the ladder structure that provides a partial natural hedge.

Read-only public API (no auth), metadata only — no candles, so it is cheap to run often.
Usage:
    {"type": "script", "name": "mmsell_supply_forecast"}
    {"type": "script", "name": "mmsell_supply_forecast", "args": ["--weeks", "20"]}
    {"type": "script", "name": "mmsell_supply_forecast", "args": ["--probe"]}
"""

from __future__ import annotations

import argparse
import datetime as _dt
import time
from collections import defaultdict

import mmsell_seasonal as ms
import xvenue_leadlag as xl  # _get (browser-UA fetch, retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Series we want seasonal history for even though they may be dormant today (an NFL game market
# does not exist in August, so it cannot be discovered from the open feed). Discovery unions
# this seed with whatever /series and the open-events feed return, so a wrong guess here costs
# one empty row rather than a blind spot — and a right one rescues a whole regime.
SEED_SERIES = (
    # NFL / college football
    "KXNFLGAME", "KXNFLTOTAL", "KXNFLSPREAD", "KXNFLTD", "KXNFLPASSYDS", "KXNFLRUSHYDS",
    "KXNCAAFGAME", "KXNCAAFTOTAL", "KXNCAAFSPREAD",
    # NBA / NHL
    "KXNBAGAME", "KXNBATOTAL", "KXNBASPREAD", "KXNBASERIES", "KXNBAPTS",
    "KXNHLGAME", "KXNHLTOTAL", "KXNHLSPREAD", "KXNHLSERIES",
    # MLB (we have summer history; the playoffs are the new regime)
    "KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBHR", "KXWORLDSERIES",
    # college basketball
    "KXNCAABGAME", "KXNCAABTOTAL", "KXNCAABSPREAD",
    # Elections — the November concentration risk. These are the tickers Kalshi actually lists
    # for the 2026 midterms (confirmed against --list-series Elections); the obvious guesses
    # KXSENATE / KXHOUSE / KXGOV / KXMIDTERM do NOT exist as series.
    "KXHOUSERACE", "KXSENATEMID", "KXPRESPARTY", "KXGOVWINS", "KXHOUSEWINSTATE",
    "KXMIDTERMMOV", "KXSENATEDEMLEAD", "KXHOUSEPOPVOTEMARGIN",
)

# Position caps the concentration section measures against (kalshi_bot/config.py).
PAPER_MAX_OPEN = 200
LIVE_MAX_OPEN = 60

# Regimes the cadence section pulls prior-year history for: the six we have NEVER traded, plus
# the ones carrying the book today (so the forecast shows what we are trading DOWN from as the
# summer regime ends, not just what is arriving).
CADENCE_REGIMES = (*ms.UNSEEN_REGIMES, "MLB", "Soccer", "Tennis", "Golf", "OtherSport",
                   "Crypto", "Politics", "Econ")


# ------------------------------------------------------------------ fetch


def fetch_open_markets(max_pages: int, verbose: bool = False) -> list[dict]:
    """Every currently-open market with its series, close time, quote and volume.

    Uses /events?with_nested_markets=true rather than /markets because the event record carries
    series_ticker and category, which the flat market feed does not reliably return.

    Warns when `max_pages` cuts the scan short: every supply number in this script is a COUNT of
    these markets, so a truncated scan understates all of them silently — the one failure mode
    that would make the forecast quietly wrong rather than visibly incomplete."""
    out: list[dict] = []
    cursor = ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for e in evs:
            series = (e.get("series_ticker") or "").upper()
            for m in e.get("markets") or []:
                tk = m.get("ticker") or ""
                close = ms.close_unix(m.get("close_time") or "")
                if not tk or not close:
                    continue
                out.append({
                    "ticker": tk,
                    "event": e.get("event_ticker") or ms.event_of(tk),
                    "series": ms.series_of(tk, series),
                    "category": e.get("category") or "",
                    "close": close,
                    "yes_bid": ms.price_c(m, "yes_bid"),
                    "yes_ask": ms.price_c(m, "yes_ask"),
                    "volume": xl._num(m.get("volume") or m.get("volume_fp")),
                })
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
    if cursor and verbose:
        print(f"  WARNING: open-market scan hit the {max_pages}-page cap with more events "
              f"pending — every supply count below is an UNDERCOUNT. Raise --event-pages.")
    return out


def fetch_settled_deep(series: str, max_pages: int, min_vol: float,
                       since: int) -> tuple[list[dict], int]:
    """Settled markets for one series back to `since` (unix), newest-first pagination.

    Returns (rows, reach) where `reach` is the OLDEST close time the pagination actually got to.
    Reach matters as much as the rows: a high-frequency series (KXBTCD lists ~300 markets a day)
    exhausts the page budget inside a week, so its prior-year weeks come back empty — and an
    empty week is indistinguishable from a week with no markets unless the caller knows how far
    back the data goes. Callers must blank uncovered weeks rather than print them as zero.

    kalshi_flb.fetch_settled_for_series caps at 3 pages and stops on a count, which is right for
    an edge sample but wrong for a CADENCE count — a season's worth of NFL markets needs walking
    to the end."""
    out: list[dict] = []
    cursor = ""
    reach = None
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/markets?series_ticker={series}&status=settled"
                       f"&limit=1000&cursor={cursor}")
        mkts = (page or {}).get("markets") or []
        oldest = None
        for m in mkts:
            tk = m.get("ticker") or ""
            close = ms.close_unix(m.get("close_time") or "")
            if not close or not tk:
                continue
            oldest = close if oldest is None else min(oldest, close)
            if close < since:
                continue
            out.append({
                "ticker": tk, "series": series, "close": close,
                "result": (m.get("result") or "").lower(),
                "volume": xl._num(m.get("volume") or m.get("volume_fp")),
            })
        if oldest is not None:
            reach = oldest if reach is None else min(reach, oldest)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts or (oldest is not None and oldest < since):
            break
    rows = [m for m in out if m["volume"] >= min_vol or min_vol <= 0]
    return rows, (reach if reach is not None else int(time.time()))


def discover_series(open_markets: list[dict]) -> dict[str, str]:
    """series ticker -> the highest-priority source it was discovered from.

    Three sources, unioned in priority order (see mmsell_seasonal.select_series): series trading
    TODAY, the hand-picked seasonal seeds, then Kalshi's full catalogue — which is what makes a
    dormant season visible at all (an NFL game series has no open market in August), but is also
    3,000+ sports series deep, so the caller must bound it."""
    found: dict[str, str] = {}
    for m in open_markets:
        if m["series"]:
            found.setdefault(m["series"], "open")
    for s in SEED_SERIES:
        found.setdefault(s, "seed")
    for cat in ("Sports", "Politics", "Economics", "Financials", "Crypto", "Climate",
                "Companies", "Culture", "World", "Health", "Science"):
        page = xl._get(f"{KALSHI}/series?category={cat}")
        for s in (page or {}).get("series") or []:
            tk = (s.get("ticker") or "").upper()
            if tk:
                found.setdefault(tk, f"catalogue:{cat}")
    return found


# ------------------------------------------------------------------ report helpers


def _fmt_pct(x) -> str:
    return "  n/a" if x is None else f"{100.0 * x:5.1f}%"


def _table(rows, headers, widths):
    print("    " + " ".join(h.rjust(w) for h, w in zip(headers, widths, strict=False)))
    for r in rows:
        print("    " + " ".join(str(c).rjust(w) for c, w in zip(r, widths, strict=False)))


# ------------------------------------------------------------------ sections


def report_live_supply(markets: list[dict], now: int) -> dict[str, float]:
    """Section 1. Returns the per-regime BAND RATE (eligible / in-window), which sections 2-3
    use to turn a market count into an entry count."""
    print("\n=== 1) LIVE SUPPLY — markets clearing the mmsell10 filter right now ===")
    print(f"  filter: yes-mid {ms.BAND_LO_C:.0f}-{ms.BAND_HI_C:.0f}c, yes-ask <= {ms.MAX_YES_C:.0f}c, "
          f"htc {ms.HTC_MIN_H:.0f}-{ms.HTC_MAX_H:.0f}h, volume >= {ms.MIN_VOLUME:.0f}")

    per: dict[str, dict[str, int]] = defaultdict(lambda: {"all": 0, "window": 0, "elig": 0,
                                                          "cheap_far": 0})
    for m in markets:
        if ms.skip_series(m["series"]):
            continue
        reg = ms.regime(m["series"])
        htc = (m["close"] - now) / 3600.0
        mid = None if m["yes_bid"] is None or m["yes_ask"] is None else (m["yes_bid"] + m["yes_ask"]) / 2.0
        cell = per[reg]
        cell["all"] += 1
        in_window = ms.HTC_MIN_H <= htc <= ms.HTC_MAX_H
        if in_window:
            cell["window"] += 1
            if ms.eligible(mid, m["yes_ask"], htc, m["volume"]):
                cell["elig"] += 1
        elif htc > ms.HTC_MAX_H and mid is not None and ms.BAND_LO_C <= mid < ms.BAND_HI_C \
                and (m["yes_ask"] or 99) <= ms.MAX_YES_C:
            cell["cheap_far"] += 1        # cheap today, but too far out to trade — section 2's fuel

    rows, rates = [], {}
    for reg in sorted(per, key=lambda r: -per[r]["elig"]):
        c = per[reg]
        rate = (c["elig"] / c["window"]) if c["window"] else None
        rates[reg] = rate
        rows.append([reg, c["all"], c["window"], c["elig"], _fmt_pct(rate), c["cheap_far"],
                     "UNSEEN" if reg in ms.UNSEEN_REGIMES else ""])
    _table(rows, ["regime", "open", "in-window", "ELIGIBLE", "bandrate", "cheap-far", "note"],
           [12, 6, 9, 8, 8, 9, 7])
    # An unmapped series lands in "Other" and disappears from every per-regime table, so the
    # biggest unmapped drivers are worth naming — that is how mmsell_seasonal.REGIMES grows.
    other: dict[str, int] = defaultdict(int)
    for m in markets:
        if ms.regime(m["series"]) == "Other" and not ms.skip_series(m["series"]):
            other[m["series"]] += 1
    if other:
        top = sorted(other.items(), key=lambda kv: -kv[1])[:10]
        print("  biggest UNMAPPED series (candidates for mmsell_seasonal.REGIMES): "
              + ", ".join(f"{s}={n}" for s, n in top))
    tot_w = sum(c["window"] for c in per.values())
    tot_e = sum(c["elig"] for c in per.values())
    pooled = (tot_e / tot_w) if tot_w else 0.0
    print(f"    {'TOTAL':>12} {sum(c['all'] for c in per.values()):6d} {tot_w:9d} {tot_e:8d} "
          f"{_fmt_pct(pooled)} {sum(c['cheap_far'] for c in per.values()):9d}")
    print("  bandrate = eligible / in-window, measured on the CURRENT quote only. It undercounts:")
    print("  a market can enter the band later in its 14-day window. mmsell_regime_backtest's")
    print("  candle-based yield is the fuller number — prefer it when available.")
    print("  'UNSEEN' = a regime with ZERO settled trades in any mmsell paper book.")
    rates["_pooled"] = pooled
    return rates


def report_window_entry(markets: list[dict], now: int, weeks: int, rates: dict) -> None:
    """Section 2: already-listed cheap markets bucketed by the week they enter the 14-day window."""
    print(f"\n=== 2) WINDOW-ENTRY CALENDAR — already-listed markets, by the week they become "
          f"tradeable (close - {ms.HTC_MAX_H / 24:.0f}d) ===")
    print("  No forecasting: these markets exist today with published close dates. A market that")
    print("  is cheap now can reprice before it enters the window, so treat the CHEAP column as")
    print("  an upper bound on entries and the ALL column as the supply that will be screened.")

    horizon = now + int(weeks * 7 * 86400)
    buckets: dict[_dt.date, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"all": 0, "cheap": 0}))
    for m in markets:
        if ms.skip_series(m["series"]):
            continue
        enter = m["close"] - int(ms.HTC_MAX_H * 3600)
        if enter > horizon:
            continue
        wk = ms.week_start(max(enter, now))
        reg = ms.regime(m["series"])
        cell = buckets[wk][reg]
        cell["all"] += 1
        mid = None if m["yes_bid"] is None or m["yes_ask"] is None else (m["yes_bid"] + m["yes_ask"]) / 2.0
        if mid is not None and ms.BAND_LO_C <= mid < ms.BAND_HI_C and (m["yes_ask"] or 99) <= ms.MAX_YES_C:
            cell["cheap"] += 1

    regs = sorted({r for wk in buckets.values() for r in wk},
                  key=lambda r: -sum(wk.get(r, {}).get("all", 0) for wk in buckets.values()))[:8]
    _table(
        [[str(wk)] + [f"{buckets[wk][r]['all']}/{buckets[wk][r]['cheap']}" for r in regs]
         + [sum(c["all"] for c in buckets[wk].values()),
            sum(c["cheap"] for c in buckets[wk].values())]
         for wk in sorted(buckets)],
        ["week-of"] + regs + ["ALL", "CHEAP"],
        [10] + [max(7, len(r)) for r in regs] + [6, 6])
    print("  cells are all/cheap. A regime absent from this table lists nothing that far out —")
    print("  that is the listing horizon, and section 3 is what covers the weeks beyond it.")


def report_cadence(series_map: dict[str, str], now: int, weeks: int, rates: dict,
                   args) -> dict[_dt.date, dict[str, float]]:
    """Section 3: trailing settled cadence per regime, bounded by Kalshi's retention wall."""
    print("\n=== 3) SEASONAL CADENCE — settled-market cadence, and how far history actually goes ===")
    lookback_start = now - int(400 * 86400)
    want = {r.strip() for r in args.regimes.split(",") if r.strip()}
    interesting = ms.select_series(series_map, want, args.max_series)
    print(f"  discovered {len(series_map)} series; pulling settled history for "
          f"{len(interesting)} (<= {args.max_series} per regime, active series first)")

    # week-of-close -> regime -> settled market count, over the trailing ~13 months.
    # `reach` is how far back each regime's pagination actually got: a high-frequency series
    # exhausts the page budget in days, and printing its unreached weeks as 0 would fabricate
    # "no supply" out of "no data" — the single easiest way for this forecast to mislead.
    hist: dict[_dt.date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_series: dict[str, tuple[int, int]] = {}
    reach: dict[str, int] = {}
    for s in sorted(interesting):
        rows, got = fetch_settled_deep(s, args.hist_pages, args.min_vol, lookback_start)
        per_series[s] = (len(rows), got)
        reg = ms.regime(s)
        reach[reg] = min(reach.get(reg, got), got) if rows else reach.get(reg, now)
        for m in rows:
            hist[ms.week_start(m["close"])][reg] += 1
        time.sleep(args.sleep)

    live = sorted(((n, r, s) for s, (n, r) in per_series.items() if n), reverse=True)[:14]
    print("  deepest series pulled (rows, oldest close reached): "
          + ", ".join(f"{s}={n}@{_dt.datetime.utcfromtimestamp(r):%Y-%m-%d}"
                      for n, r, s in live))
    dead = [s for s, (n, _r) in per_series.items() if n == 0]
    if dead:
        print(f"  {len(dead)} series returned no settled history in the window "
              f"(dormant, renamed, or a bad seed guess): {', '.join(sorted(dead)[:14])}"
              + (" ..." if len(dead) > 14 else ""))

    # THE RETENTION WALL. Kalshi's settled-market feed serves only a rolling ~70 days: paging a
    # series to cursor exhaustion (not a page cap) bottoms out on the same date for every series
    # regardless of size, KXNFLGAME returns zero markets, and status=finalized/closed return
    # nothing at all. So a prior-year comparison — the obvious way to forecast a season — is not
    # available from this API at any page budget, and printing one would be inventing data.
    # What IS available is the trailing cadence inside the wall, which shows the CURRENT regime
    # decaying and the next one arriving.
    wall = max(reach.values()) if reach else now
    deepest = min(reach.values()) if reach else now
    print(f"\n  RETENTION WALL: settled history reaches back to "
          f"{_dt.datetime.utcfromtimestamp(deepest):%Y-%m-%d} "
          f"({(now - deepest) / 86400:.0f}d) at best; the shallowest regime stops at "
          f"{_dt.datetime.utcfromtimestamp(wall):%Y-%m-%d}.")
    print("  Kalshi serves a rolling ~70-day settled window, so LAST season is unavailable at any")
    print("  page budget — a prior-year projection cannot be computed and is not shown. The fix is")
    print("  to CAPTURE settled history as it happens (the weather backfill already does exactly")
    print("  this); see docs/MMSELL_SEASONAL_FORECAST.md. Trailing cadence inside the wall:")

    regs = sorted(want & set(reach),
                  key=lambda r: -sum(wk.get(r, 0) for wk in hist.values()))[:10]
    this_week = ms.week_start(now)
    back = sorted(w for w in hist if w <= this_week)[-10:]
    rows = []
    for wk in back:
        counts = hist.get(wk, {})
        covered = {r: wk >= ms.week_start(reach.get(r, now)) for r in regs}
        rows.append([str(wk)] + [(counts.get(r, 0) if covered[r] else "?") for r in regs]
                    + [sum(counts.get(r, 0) for r in regs if covered[r])])
    _table(rows, ["week-of"] + regs + ["ALL"], [10] + [max(6, len(r)) for r in regs] + [6])
    print("  Settled markets per week per regime. A '?' is outside that regime's retained window,")
    print("  NOT an empty week. Read the trend, not the level: a regime falling toward zero is the")
    print("  supply we are about to lose, which is what the World Cup collapse looked like.")
    return {ms.week_start(now): {r: 0.0 for r in regs}}


def report_concentration(markets: list[dict], now: int, weeks: int, rates: dict) -> None:
    """Section 4: settlement-date concentration + the ladder structure that partially hedges it."""
    print("\n=== 4) SETTLEMENT-DATE CONCENTRATION — the risk this forecast exists to surface ===")
    print("  mmsell's risk model is 'many small independent positions'. Positions that settle on")
    print("  ONE date against ONE shared driver (an election night, a single game's prop ladder)")
    print("  are closer to one position at Nx size. Cheap tails all lose together when the")
    print("  driver surprises, which is exactly the tail the book cannot absorb.")

    horizon = now + int(weeks * 7 * 86400)
    by_date: dict[_dt.date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for m in markets:
        if ms.skip_series(m["series"]) or m["close"] > horizon:
            continue
        mid = None if m["yes_bid"] is None or m["yes_ask"] is None else (m["yes_bid"] + m["yes_ask"]) / 2.0
        if mid is None or not (ms.BAND_LO_C <= mid < ms.BAND_HI_C) or (m["yes_ask"] or 99) > ms.MAX_YES_C:
            continue
        d = _dt.datetime.utcfromtimestamp(m["close"]).date()
        by_date[d][ms.regime(m["series"])] += 1

    if not by_date:
        print("  (no cheap already-listed markets inside the horizon)")
        return
    top = sorted(by_date.items(), key=lambda kv: -sum(kv[1].values()))[:15]
    rows = []
    for d, regs in top:
        n = sum(regs.values())
        drivers = ", ".join(f"{r}:{c}" for r, c in sorted(regs.items(), key=lambda kv: -kv[1])[:3])
        corr = "CORRELATED" if any(r in ms.CORRELATED_REGIMES for r in regs) else ""
        rows.append([str(d), n, f"{100.0 * n / PAPER_MAX_OPEN:.0f}%",
                     f"{100.0 * n / LIVE_MAX_OPEN:.0f}%", drivers[:38], corr])
    _table(rows, ["close-date", "cheap", "%paper", "%live", "top regimes", "flag"],
           [10, 5, 6, 5, 38, 10])
    print(f"  %paper / %live = share of the {PAPER_MAX_OPEN} / {LIVE_MAX_OPEN} open-position caps that")
    print("  would settle on that single date if every cheap market there were entered.")

    # Ladder structure — the partial natural hedge inside one event.
    print("\n  --- ladder structure of the CORRELATED regimes (the partial natural hedge) ---")
    ladders: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for m in markets:
        if ms.regime(m["series"]) not in ms.CORRELATED_REGIMES or m["close"] > horizon:
            continue
        ladders[m["series"]][m["event"]].add(m["ticker"])
    if not ladders:
        print("    (no correlated-regime markets listed inside the horizon yet — expected until")
        print("     the November ladders enter the 14-day window in late October)")
        return
    rows = []
    for s in sorted(ladders, key=lambda s: -sum(len(v) for v in ladders[s].values())):
        evs = ladders[s]
        sizes = sorted(len(v) for v in evs.values())
        rows.append([s, len(evs), sum(sizes), sizes[len(sizes) // 2], sizes[-1]])
    _table(rows, ["series", "events", "markets", "med/event", "max/event"], [22, 7, 8, 10, 10])
    print("    Within ONE event the rungs are mutually exclusive, so at most one cheap tail can")
    print("    lose — a real hedge. ACROSS events (different races) that protection disappears:")
    print("    a national swing moves every race the same direction at once. Size the cap on the")
    print("    number of EVENTS, not the number of markets.")


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--weeks", type=int, default=16, help="forward horizon in weeks")
    ap.add_argument("--event-pages", type=int, default=120,
                    help="open-event pages to scan (200 events/page); a cap that cuts the "
                         "scan short understates every supply count, so it warns")
    ap.add_argument("--hist-pages", type=int, default=2, help="settled pages per series")
    ap.add_argument("--regimes", default=",".join(CADENCE_REGIMES),
                    help="regimes to pull seasonal history for (see mmsell_seasonal.REGIMES)")
    ap.add_argument("--max-series", type=int, default=10,
                    help="series per regime in the cadence pull — Kalshi lists 3,000+ sports "
                         "series, most of them per-team spin-offs of one driver")
    ap.add_argument("--min-vol", type=float, default=0.0,
                    help="volume floor for the cadence counts (0 = count every settled market)")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between series pulls")
    ap.add_argument("--skip-cadence", action="store_true",
                    help="sections 1/2/4 only (skips the slow per-series history pull)")
    ap.add_argument("--probe", action="store_true", help="dump raw API shapes and exit")
    ap.add_argument("--list-series", default="",
                    help="comma list of regimes: print every discovered series in them and exit. "
                         "Use this to pick accurate SEED_SERIES instead of guessing tickers.")
    args = ap.parse_args(argv)

    now = int(time.time())
    if args.probe:
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true&limit=2")
        evs = (page or {}).get("events") or []
        print(f"/events open: {len(evs)} events, cursor={bool((page or {}).get('cursor'))}")
        if evs:
            print(f"  EVENT KEYS: {sorted(evs[0].keys())}")
            mk = (evs[0].get("markets") or [{}])[0]
            print(f"  MARKET KEYS: {sorted(mk.keys())}")
            print(f"  MARKET SAMPLE: {mk}")
        cat = xl._get(f"{KALSHI}/series?category=Sports")
        rows = (cat or {}).get("series") or []
        print(f"/series?category=Sports: {len(rows)} rows; keys="
              f"{sorted(rows[0].keys()) if rows else None}")
        print(f"  first 25 tickers: {[r.get('ticker') for r in rows[:25]]}")
        # Does the settled feed honour a close-time window? Newest-first pagination cannot reach
        # a year back on a busy series (KXBTCD lists ~300 markets/day), so the whole seasonal
        # cadence depends on being able to ASK for the prior-year window directly.
        lo = now - 371 * 86400
        hi = now - 357 * 86400
        variants = {
            "no-ts (control)": "series_ticker=KXBTCD&status=settled&limit=1000",
            "min+max_close_ts": (f"series_ticker=KXBTCD&status=settled"
                                 f"&min_close_ts={lo}&max_close_ts={hi}&limit=1000"),
            "max_close_ts only": (f"series_ticker=KXBTCD&status=settled"
                                  f"&max_close_ts={hi}&limit=1000"),
            "ts without status": (f"series_ticker=KXBTCD&min_close_ts={lo}"
                                  f"&max_close_ts={hi}&limit=1000"),
            "NFL min+max": (f"series_ticker=KXNFLGAME&status=settled"
                            f"&min_close_ts={lo}&max_close_ts={hi}&limit=1000"),
        }
        for name, qs in variants.items():
            page = xl._get(f"{KALSHI}/markets?{qs}")
            if page is None:
                print(f"  ts-probe {name:20s}: REQUEST FAILED (4xx/5xx -> params rejected)")
                continue
            mk = page.get("markets") or []
            closes = sorted(ms.close_unix(m.get("close_time") or "") for m in mk)
            span = (f"{_dt.datetime.utcfromtimestamp(closes[0]):%Y-%m-%d}"
                    f"..{_dt.datetime.utcfromtimestamp(closes[-1]):%Y-%m-%d}") if closes else "-"
            inside = sum(1 for c in closes if lo <= c <= hi)
            print(f"  ts-probe {name:20s}: {len(mk):5d} markets, closes {span}, "
                  f"{inside} inside window, cursor={bool(page.get('cursor'))}")

        # RETENTION: how far back does the settled feed go AT ALL? Every series in a real run
        # bottomed out on the same date regardless of row count, which looks like a retention
        # window rather than a pagination limit. If it is, no amount of paging reaches last
        # season, and the whole historical-regime plan needs a different data source.
        print("\n  --- settled-history RETENTION probe (walk each series to exhaustion) ---")
        for s in ("KXNHLGAME", "KXNBAGAME", "KXNFLGAME", "KXMLBGAME", "KXPRESPARTY"):
            for status in ("settled", "finalized", "closed"):
                rows, cursor, pages = [], "", 0
                while pages < 25:
                    page = xl._get(f"{KALSHI}/markets?series_ticker={s}&status={status}"
                                   f"&limit=1000&cursor={cursor}")
                    if page is None:
                        break
                    mk = page.get("markets") or []
                    rows.extend(ms.close_unix(m.get("close_time") or "") for m in mk)
                    cursor = page.get("cursor") or ""
                    pages += 1
                    if not cursor or not mk:
                        break
                cl = sorted(c for c in rows if c)
                oldest = (f"{_dt.datetime.utcfromtimestamp(cl[0]):%Y-%m-%d}" if cl else "-")
                newest = (f"{_dt.datetime.utcfromtimestamp(cl[-1]):%Y-%m-%d}" if cl else "-")
                age = f"{(now - cl[0]) / 86400:.0f}d" if cl else "-"
                print(f"    {s:14s} {status:10s}: n={len(cl):6d} pages={pages:3d} "
                      f"exhausted={not cursor} oldest={oldest} ({age} ago) newest={newest}")
        return 0

    if args.list_series:
        want = {r.strip() for r in args.list_series.split(",") if r.strip()}
        known = discover_series(fetch_open_markets(args.event_pages))
        by_reg: dict[str, list[str]] = defaultdict(list)
        for s, src in sorted(known.items()):
            if ms.regime(s) in want:
                by_reg[ms.regime(s)].append(f"{s}[{src.split(':')[0]}]")
        for reg in sorted(by_reg):
            print(f"\n--- {reg} ({len(by_reg[reg])} series) ---")
            for i in range(0, len(by_reg[reg]), 4):
                print("  " + "  ".join(x.ljust(30) for x in by_reg[reg][i:i + 4]))
        return 0

    print("=== mmsell SUPPLY FORECAST — upcoming tradeable markets by regime, week and "
          f"settlement date (as of {_dt.datetime.utcfromtimestamp(now):%Y-%m-%d %H:%MZ}) ===")
    markets = fetch_open_markets(args.event_pages, verbose=True)
    print(f"  scanned {len(markets)} open markets across "
          f"{len({m['series'] for m in markets})} series")
    if not markets:
        print("  (no open markets returned — check --probe)")
        return 0

    rates = report_live_supply(markets, now)
    report_window_entry(markets, now, args.weeks, rates)
    if not args.skip_cadence:
        report_cadence(discover_series(markets), now, args.weeks, rates, args)
    report_concentration(markets, now, args.weeks, rates)

    print("\n  Read this WITH scripts/mmsell_regime_backtest.py: this script says how many trades")
    print("  a regime will offer, that one says what each trade has historically been worth. A")
    print("  big supply number in a regime with an unmeasured or negative edge is a warning, not")
    print("  an opportunity. Pre-registered per-regime gates: docs/MMSELL_SEASONAL_FORECAST.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
