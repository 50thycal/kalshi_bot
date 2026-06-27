"""Wide-cast market survey — map ALL of Kalshi's open markets by category and series, with
liquidity (volume / open interest) and spread (an efficiency proxy), to pick where to hunt
for edge after weather came up empty.

Read-only: GETs Kalshi's PUBLIC market-data endpoints (no API key needed) — /events for the
category/series of each event, /markets for per-market volume/OI/bid-ask. A browser UA clears
Cloudflare's 1010 (same trick as kalshi_market_probe.py). Stdlib only; runs on the ops runner.

Output: a category leaderboard (series count, open markets, total volume + OI, avg spread,
avg price) and the top series by volume — so we can target liquid-but-less-efficient
categories (wide spread + real volume) and avoid the razor-tight efficient ones.

Usage (via the ops channel):
    {"type":"script","name":"kalshi_market_survey","args":["--max-pages","40"]}
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections import defaultdict

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(path: str, params: dict) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
    url = f"{BASE}{path}?{qs}" if qs else f"{BASE}{path}"
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 (fixed host)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise
    return {}


def _paginate(path: str, key: str, params: dict, max_pages: int):
    cursor = ""
    for _ in range(max_pages):
        page = _get(path, {**params, "cursor": cursor})
        rows = page.get(key) or []
        yield from rows
        cursor = page.get("cursor") or ""
        if not cursor or not rows:
            return


def _series_of(event_ticker: str) -> str:
    # Kalshi event ticker = "{SERIES}-{DATE/EVENT}..."; the series is the leading token.
    return (event_ticker or "").split("-", 1)[0] or "?"


class Agg:
    __slots__ = ("series", "n", "vol", "oi", "spread_sum", "spread_n", "price_sum", "price_n")

    def __init__(self):
        self.series = set()
        self.n = self.vol = self.oi = 0
        self.spread_sum = self.spread_n = self.price_sum = self.price_n = 0.0

    def add(self, series, vol, oi, bid, ask):
        self.series.add(series)
        self.n += 1
        self.vol += vol or 0
        self.oi += oi or 0
        if bid is not None and ask is not None and 0 <= bid <= ask <= 100:
            self.spread_sum += ask - bid
            self.spread_n += 1
            self.price_sum += (ask + bid) / 2.0
            self.price_n += 1

    @property
    def spread(self):
        return self.spread_sum / self.spread_n if self.spread_n else float("nan")

    @property
    def price(self):
        return self.price_sum / self.price_n if self.price_n else float("nan")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-pages", type=int, default=60, help="pagination cap per endpoint")
    ap.add_argument("--top-series", type=int, default=35)
    ap.add_argument("--days", type=float, default=14.0,
                    help="only markets closing within N days (skips dead far-future parlays)")
    ap.add_argument("--min-volume", type=int, default=1, help="skip markets below this volume")
    args = ap.parse_args(argv)
    max_close = int(time.time() + args.days * 86400)

    # 1) events (near-term) -> category/series/title per event_ticker
    ev_cat: dict[str, str] = {}
    ev_title: dict[str, str] = {}
    n_events = 0
    for e in _paginate("/events", "events",
                       {"status": "open", "limit": 200}, args.max_pages):
        et = e.get("event_ticker")
        if not et:
            continue
        n_events += 1
        ev_cat[et] = (e.get("category") or "?").strip() or "?"
        ev_title[et] = e.get("title") or e.get("sub_title") or ""

    # 2) markets (near-term, traded) -> liquidity, joined to category/series
    by_cat: dict[str, Agg] = defaultdict(Agg)
    by_series: dict[str, Agg] = defaultdict(Agg)
    series_cat: dict[str, str] = {}
    series_title: dict[str, str] = {}
    n_mkts = n_kept = 0
    sample_keys = None
    for m in _paginate("/markets", "markets",
                       {"status": "open", "limit": 1000, "max_close_ts": max_close}, args.max_pages):
        n_mkts += 1
        et = m.get("event_ticker") or ""
        series = _series_of(et)
        if series.startswith("KXMVE"):           # auto-generated multivariate parlays — noise
            continue
        vol = m.get("volume") or m.get("volume_24h") or 0
        oi = m.get("open_interest") or 0
        if vol < args.min_volume and oi < args.min_volume:
            continue
        if sample_keys is None:
            sample_keys = sorted(m.keys())
        n_kept += 1
        bid, ask = m.get("yes_bid"), m.get("yes_ask")
        cat = ev_cat.get(et, m.get("category") or "?")
        by_cat[cat].add(series, vol, oi, bid, ask)
        by_series[series].add(series, vol, oi, bid, ask)
        series_cat.setdefault(series, cat)
        series_title.setdefault(series, ev_title.get(et, ""))

    print(f"=== Kalshi open-market survey — {n_events} near-term events, {n_mkts} markets scanned,"
          f" {n_kept} traded (vol/oi>={args.min_volume}, <= {args.days:g}d to close) ===")
    print(f"  (market fields seen: {sample_keys})")
    print("\n=== Categories by total volume (spread = efficiency proxy: tighter = more efficient) ===")
    print(f"  {'category':>22} {'series':>6} {'mkts':>6} {'volume':>12} {'open_int':>11}"
          f" {'avg_spr':>7} {'avg_px':>6}")
    for cat, a in sorted(by_cat.items(), key=lambda kv: -kv[1].vol):
        print(f"  {cat[:22]:>22} {len(a.series):6d} {a.n:6d} {a.vol:12,d} {int(a.oi):11,d}"
              f" {a.spread:6.1f}c {a.price:5.0f}c")

    print(f"\n=== Top {args.top_series} series by total volume ===")
    print(f"  {'series':>20} {'category':>16} {'mkts':>5} {'volume':>11} {'avg_spr':>7} {'avg_px':>6}")
    for s, a in sorted(by_series.items(), key=lambda kv: -kv[1].vol)[:args.top_series]:
        print(f"  {s[:20]:>20} {series_cat.get(s, '?')[:16]:>16} {a.n:5d} {a.vol:11,d}"
              f" {a.spread:6.1f}c {a.price:5.0f}c   {series_title.get(s, '')[:40]}")
    print("\n  (target: real volume + WIDE spread = liquid but less efficient. avoid razor-tight"
          " high-volume series — those are arbitraged.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
