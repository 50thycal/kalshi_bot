"""Short-window venue census: which rolling short-dated Kalshi series exist, and are they big enough?

Recon only (idea-model run docs/IDEA_MODEL_20261106.md). For each candidate series ticker it
reports: settled markets in the last N days, settled contract volume, median volume per market,
YES share, and the current open-market spread. Its question is "does a gradeable, liquid tape
exist NOW?" (the testability-NOW and venue-age gates). It is not an edge test and grades nothing.

Why a dedicated census: `kalshi_market_survey` counts only OPEN markets, and a 15-minute market
lives 15 minutes, so rolling short-window series are nearly invisible to it (the 2026-11-06
survey showed Crypto as 1 series / 11 markets).

Read-only: Kalshi PUBLIC REST, no key, no orders. Stdlib only.

Usage (ops channel):
    {"type":"script","name":"short_window_census","args":["--days","7"]}
    {"type":"script","name":"short_window_census","args":["KXBTC15M","KXGOLD15M","--days","3"]}
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kalshi_settled as ks  # noqa: E402

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Candidate tickers. Some are guesses on purpose: the census reports which exist.
DEFAULT_SERIES = (
    "KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M", "KXBNB15M", "KXHYPE15M",
    "KXBTC5M", "KXETH5M",
    "KXGOLD15M", "KXSILVER15M", "KXGOLDH", "KXSILVERH",
    "KXBTCD", "KXETHD", "KXSOLD", "KXBTC", "KXETH",
    "KXINXU", "KXNASDAQ100U", "KXINX", "KXEURUSDH", "KXUSDJPYH", "KXWTIH",
)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _get(path: str, params: dict) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
    url = f"{KALSHI}{path}?{qs}" if qs else f"{KALSHI}{path}"
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < 3:
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
    return {}


def markets(series: str, status: str, min_close_ts: int | None, max_pages: int,
            scan: ks.ScanCount | None = None) -> list[dict]:
    """One series' markets. A settled scan also reads Kalshi's historical tier when the window
    reaches past the historical cutoff (read at runtime): live /markets stops serving settled
    markets older than it (docs/handoffs/HANDOFF-kalshi-historical-cutoff.md). A failed LIVE
    read raises, as before (the series row prints ERROR); a failed historical read is counted
    in `scan` and printed."""
    part = ks.ScanCount()
    out = ks.series_markets(series, status=status, min_close_ts=min_close_ts,
                            max_pages=max_pages, get=_get, count=part)
    if part.errors[ks.LIVE]:
        raise RuntimeError(f"live /markets read failed for {series} ({status})")
    if scan is not None:
        for src in (ks.LIVE, ks.HISTORICAL):
            scan.counts[src] += part.counts[src]
            scan.errors[src] += part.errors[src]
            scan.truncated[src] = scan.truncated[src] or part.truncated[src]
    return out


def summarize(series: str, settled: list[dict], open_mkts: list[dict], days: float) -> dict:
    vols = [_num(m.get("volume_fp") or m.get("volume")) for m in settled]
    yes = sum(1 for m in settled if (m.get("result") or "").lower() == "yes")
    spreads = []
    for m in open_mkts:
        bid, ask = _num(m.get("yes_bid_dollars")) * 100, _num(m.get("yes_ask_dollars")) * 100
        if 0 < bid < ask < 100:
            spreads.append(ask - bid)
    return {
        "series": series,
        "exists": bool(settled or open_mkts),
        "settled": len(settled),
        "settled_per_day": round(len(settled) / days, 1) if days else 0.0,
        "settled_volume": int(sum(vols)),
        "volume_per_day": int(sum(vols) / days) if days else 0,
        "median_vol_per_mkt": int(statistics.median(vols)) if vols else 0,
        "yes_share": round(yes / len(settled), 3) if settled else None,
        "open": len(open_mkts),
        "open_median_spread_c": round(statistics.median(spreads), 2) if spreads else None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("series", nargs="*", default=list(DEFAULT_SERIES))
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--max-pages", type=int, default=6)
    args = ap.parse_args(argv)
    since = int(time.time() - args.days * 86400)
    print(f"# short-window census - last {args.days:g} days (docs/IDEA_MODEL_20261106.md)")
    print(f"{'series':<14} {'settled':>8} {'/day':>7} {'vol':>14} {'vol/day':>12} {'med/mkt':>8} "
          f"{'yes%':>6} {'open':>5} {'spr_c':>6}")
    scan = ks.ScanCount()
    for s in args.series:
        try:
            r = summarize(s, markets(s, "settled", since, args.max_pages, scan),
                          markets(s, "open", None, 1), args.days)
        except Exception as exc:  # noqa: BLE001 - one bad series must not sink the census
            print(f"{s:<14} ERROR {exc}")
            continue
        if not r["exists"]:
            print(f"{s:<14} (no markets: series absent or inactive)")
            continue
        ys = "" if r["yes_share"] is None else f"{100 * r['yes_share']:.1f}"
        sp = "" if r["open_median_spread_c"] is None else f"{r['open_median_spread_c']}"
        print(f"{s:<14} {r['settled']:>8} {r['settled_per_day']:>7} {r['settled_volume']:>14,} "
              f"{r['volume_per_day']:>12,} {r['median_vol_per_mkt']:>8,} {ys:>6} {r['open']:>5} {sp:>6}")
        time.sleep(0.2)
    print("note: settled counts are capped at --max-pages x 1000 per series (per tier); a cap hit "
          "reads as a floor.")
    print("settled " + scan.line())
    cut = ks.cutoff(_get)
    if cut:
        print("historical cutoff: " + json.dumps(cut, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
