"""Live end-to-end probe for the evo `explore_markets` action (PR2).

Answers one question: can an agent's `explore_markets` call actually reach the Kalshi
API and get real markets back — for NON-weather domains (crypto etc.), not just the
weather series the orchestrator scans?

It exercises the REAL production code path, not a reimplementation:
    market_explore.explore(LiveMarketData(client), series=..., status=..., limit=...)
i.e. the exact function `cognition._execute_one` runs for an `explore_markets` action,
over the real `LiveMarketData.list_markets` -> `client.iter_markets` pagination. The
only swap is the low-level HTTP client: the ops runner has no Kalshi credentials and
only stdlib+psycopg installed, so we substitute a keyless client that GETs Kalshi's
PUBLIC /markets endpoint (a browser UA clears Cloudflare 1010 — same trick as
kalshi_market_probe.py). Market reads need no API key, and production's iter_markets
paginates that same endpoint, so a pass here means the agent path works live.

Read-only: only GETs /markets. Never places an order. Stdlib only for its own HTTP;
imports kalshi_bot.evo.{marketdata,market_explore} (both stdlib-importable).

Usage (via the ops channel):
    {"type":"script","name":"evo_explore_probe"}
    {"type":"script","name":"evo_explore_probe","args":["--series","KXBTCD","--limit","8"]}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

# The ops runner imports this module with scripts/ on sys.path[0]; add the repo root
# so `import kalshi_bot...` resolves (the package's own .py files, no pip deps needed).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalshi_bot.evo import market_explore  # noqa: E402
from kalshi_bot.evo.marketdata import LiveMarketData  # noqa: E402

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Series to probe by default: two crypto domains (the headline "beyond weather" case),
# a broad no-series sample (proves general discovery), and a weather control (should
# always work — it's what the orchestrator already scans every cycle).
_DEFAULT_SERIES = ("KXBTCD", "KXETHD", None, "KXHIGHNY")


def _get(path: str, params: dict) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
    url = f"{BASE}{path}?{qs}" if qs else f"{BASE}{path}"
    for attempt in range(4):
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"}
        )
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


class _PublicKalshiClient:
    """Minimal read-only stand-in for KalshiClient over the PUBLIC endpoints (no
    credentials). Exposes the iter_markets/get_markets contract for discovery AND the
    get_market/get_orderbook contract get_quote uses for enrichment, so the probe drives
    the real LiveMarketData.get_quote path. Mirrors production iter_markets pagination."""

    def get_markets(self, **params) -> dict:
        return _get("/markets", params)

    def iter_markets(
        self, *, status: str = "open", page_size: int = 100,
        max_markets: int | None = None, series_ticker: str | None = None,
        event_ticker: str | None = None, min_close_ts: int | None = None,
    ):
        fetched = 0
        cursor: str | None = None
        while True:
            page = self.get_markets(
                status=status, limit=page_size, cursor=cursor,
                series_ticker=series_ticker, event_ticker=event_ticker,
                min_close_ts=min_close_ts,
            )
            markets = page.get("markets") or []
            for market in markets:
                yield market
                fetched += 1
                if max_markets is not None and fetched >= max_markets:
                    return
            cursor = page.get("cursor")
            if not cursor or not markets:
                return

    def get_market(self, ticker: str) -> dict:
        # The per-market DETAIL endpoint carries the live quote (yes_bid/yes_ask/volume/
        # open_interest/last_price) that the /markets LIST endpoint omits — this is what
        # get_quote enrichment reads. Public, no key needed.
        return _get(f"/markets/{ticker}", {})

    def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
        # Best-effort: the public orderbook may be unavailable keyless. On any failure
        # return an empty book, so get_quote falls back to the market-detail bid/ask
        # rather than failing the whole enrichment (production uses the real depth).
        try:
            return _get(f"/markets/{ticker}/orderbook", {"depth": depth})
        except Exception:  # noqa: BLE001
            return {"orderbook": {"yes": [], "no": []}}


def _print_scan(label: str, scan: dict) -> bool:
    series = scan.get("series")
    n = scan.get("count", 0)
    print(f"\n=== explore_markets(series={series!r}, status={scan.get('status')!r}) "
          f"-> {n} markets, {scan.get('priced', 0)} priced  [{label}] ===")
    if not n:
        print("  (no markets returned)")
        return False
    for m in scan.get("markets", [])[:12]:
        print(f"  {str(m.get('ticker')):30} bid={m.get('yes_bid')!s:>4} "
              f"ask={m.get('yes_ask')!s:>4} mid={m.get('yes_mid')!s:>5} "
              f"spr={m.get('spread')!s:>4} vol={m.get('volume')!s:>8} "
              f"oi={m.get('open_interest')!s:>8} h2c={m.get('hours_to_close')!s:>6}")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series", action="append",
                    help="series_ticker to probe (repeatable); omit for the default set")
    ap.add_argument("--status", default="open", help="open|settled|closed (default open)")
    ap.add_argument("--limit", type=int, default=8, help="markets per scan (explore caps at 30)")
    args = ap.parse_args(argv)

    md = LiveMarketData(_PublicKalshiClient())
    series_list = args.series if args.series else list(_DEFAULT_SERIES)

    print("# evo explore_markets live probe — REAL market_explore.explore over "
          "LiveMarketData against Kalshi's public /markets endpoint")
    print(f"# fields surfaced to the agent: {list(market_explore._FIELDS)}")

    non_empty = 0
    total_priced = 0
    seen_series: Counter = Counter()
    for s in series_list:
        try:
            scan = market_explore.explore(md, series=s, status=args.status, limit=args.limit)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the whole probe
            print(f"\n=== explore_markets(series={s!r}) RAISED: {exc!r} ===")
            continue
        label = "crypto" if (s or "").startswith(("KXBTC", "KXETH")) else (
            "weather-control" if (s or "").startswith(("KXHIGH", "KXLOW")) else
            "broad-sample" if s is None else "series")
        if _print_scan(label, scan):
            non_empty += 1
        total_priced += int(scan.get("priced", 0) or 0)
        for m in scan.get("markets", []):
            tkr = str(m.get("ticker") or "")
            seen_series[tkr.split("-", 1)[0] or "?"] += 1

    print(f"\n=== VERDICT: {non_empty}/{len(series_list)} scans returned live markets; "
          f"{total_priced} markets came back with a live quote ===")
    if seen_series:
        top = ", ".join(f"{k}:{v}" for k, v in seen_series.most_common(12))
        print(f"  distinct series discovered: {top}")
    if non_empty:
        print("  PASS — an agent's explore_markets action reaches Kalshi and gets real "
              "markets back (including non-weather domains where present).")
        return 0
    print("  FAIL — no live markets returned; the explore path could not reach Kalshi.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
