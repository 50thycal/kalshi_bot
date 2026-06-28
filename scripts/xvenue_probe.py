"""Cross-venue discovery probe — confirm we can pull price data from BOTH Kalshi and
Polymarket public APIs (no auth), and surface the identifiers to hand-match a few liquid
markets for the lead-lag measurement.

Two read-only public sources (both reachable from the ops runner, browser UA clears
Cloudflare):
  - Polymarket Gamma (gamma-api.polymarket.com): top open markets by volume, with their
    question, yes-price, volume, and clobTokenIds (needed for CLOB price history).
  - Kalshi (api.elections.kalshi.com): confirm the candlesticks history endpoint works for a
    liquid series, printing the data shape so the analyzer knows the fields.

This is plumbing/identification only — the lead-lag analyzer is the next step. Stdlib only.
Usage: {"type":"script","name":"xvenue_probe","args":["--kalshi-series","KXBTCY"]}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _get(url: str) -> object:
    for attempt in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310 (fixed hosts)
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
    return None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _first_token(m: dict) -> str:
    raw = m.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    return (raw[0] if isinstance(raw, list) and raw else "") or ""


def probe_polymarket(top: int, max_pages: int) -> None:
    print("=== Polymarket (Gamma) top OPEN markets by volume ===")
    markets: list[dict] = []
    for page in range(max_pages):
        batch = _get(f"{GAMMA}/markets?closed=false&active=true&limit=100&offset={page * 100}")
        if not isinstance(batch, list) or not batch:
            break
        markets.extend(batch)
        if len(batch) < 100:
            break
    print(f"  fetched {len(markets)} open markets")
    ranked = sorted(markets, key=lambda m: _num(m.get("volumeNum") or m.get("volume")), reverse=True)
    print(f"  {'volume':>12} {'yes':>5} {'token (clob id)':>20}  question")
    for m in ranked[:top]:
        vol = _num(m.get("volumeNum") or m.get("volume"))
        bid, ask = m.get("bestBid"), m.get("bestAsk")
        yes = (_num(bid) + _num(ask)) / 2.0 if bid is not None and ask is not None \
            else _num((json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str)
                       else [None])[0]) if m.get("outcomePrices") else 0.0
        tok = _first_token(m)
        q = (m.get("question") or "")[:54]
        print(f"  {vol:12,.0f} {yes:4.2f}  {tok[:20]:>20}  {q}")
    print("  (clobTokenIds[0] = YES token; price history: clob.polymarket.com/prices-history)")


def probe_kalshi_candles(series: str, hours: float) -> None:
    print(f"\n=== Kalshi candlesticks probe — series {series} ===")
    page = _get(f"{KALSHI}/markets?series_ticker={series}&status=open&limit=10")
    mkts = (page or {}).get("markets") or []
    if not mkts:
        print(f"  no open markets under {series}")
        return
    mkts.sort(key=lambda m: _num(m.get("volume_fp")), reverse=True)
    m = mkts[0]
    ticker = m.get("ticker")
    print(f"  most-liquid open market: {ticker}  (vol={_num(m.get('volume_fp')):,.0f},"
          f" yes_bid={m.get('yes_bid_dollars')} yes_ask={m.get('yes_ask_dollars')})")
    end = int(time.time())
    start = end - int(hours * 3600)
    for interval in (1, 60):
        url = (f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
               f"?start_ts={start}&end_ts={end}&period_interval={interval}")
        try:
            data = _get(url)
        except urllib.error.HTTPError as exc:
            print(f"  period={interval}min -> HTTP {exc.code} ({exc.reason})")
            continue
        cs = (data or {}).get("candlesticks") or []
        print(f"  period={interval}min -> {len(cs)} candles over last {hours:g}h")
        if cs:
            print(f"    sample keys: {sorted(cs[-1].keys())}")
            print(f"    last candle: {json.dumps(cs[-1])[:300]}")


def probe_pm_history(token: str, hours: float) -> None:
    if not token:
        return
    print(f"\n=== Polymarket price-history probe — token {token[:20]}... ===")
    end = int(time.time())
    start = end - int(hours * 3600)
    url = (f"https://clob.polymarket.com/prices-history?market={token}"
           f"&startTs={start}&endTs={end}&fidelity=1")
    try:
        data = _get(url)
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code} ({exc.reason})")
        return
    hist = (data or {}).get("history") or []
    print(f"  {len(hist)} points (fidelity=1min) over last {hours:g}h")
    if hist:
        print(f"    sample: {json.dumps(hist[-1])[:200]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument("--kalshi-series", default="KXBTCY")
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--pm-token", default="", help="optional clob token id to test history")
    args = ap.parse_args(argv)

    try:
        probe_polymarket(args.top, args.max_pages)
    except Exception as exc:  # noqa: BLE001 — keep going to the Kalshi probe
        print(f"  polymarket probe failed: {exc}", file=sys.stderr)
    probe_kalshi_candles(args.kalshi_series, args.hours)
    if args.pm_token:
        probe_pm_history(args.pm_token, args.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
