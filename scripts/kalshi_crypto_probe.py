"""Discovery probe of Kalshi's BTC crypto series — distinguish EUROPEAN (terminal-settled
'BTC above $X at time T') threshold markets from BARRIER (max/min-over-period) markets, and
reveal each series' strike encoding + expiry, so the Kalshi<->Deribit digital comparison
matches like-for-like (European Kalshi binary vs same-expiry Deribit digital).

Groups open Kalshi crypto markets by series and prints, per BTC series: count, distinct
close-times (many = daily/hourly European; one = single-expiry barrier), and 2 sample markets
(ticker, title, subtitle, close, strike-from-ticker, price). Read-only public API, stdlib.
Usage: {"type":"script","name":"kalshi_crypto_probe","args":["--asset","BTC"]}
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import xvenue_leadlag as xl  # _get, _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--max-pages", type=int, default=60)
    args = ap.parse_args(argv)

    by_series: dict[str, list[dict]] = defaultdict(list)
    cursor = ""
    n_ev = 0
    for _ in range(args.max_pages):
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for e in evs:
            if (e.get("category") or "") != "Crypto":
                continue
            n_ev += 1
            series = (e.get("series_ticker") or "").upper()
            if args.asset.upper() not in series:
                continue
            for m in e.get("markets") or []:
                by_series[series].append(m)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break

    print(f"=== Kalshi {args.asset} crypto series ({n_ev} crypto events scanned) ===")
    print(f"  {'series':>22} {'mkts':>5} {'#close':>6} {'sample_strike':>13}  example")
    for series in sorted(by_series, key=lambda s: -len(by_series[s])):
        mkts = by_series[series]
        closes = {(m.get("close_time") or "")[:13] for m in mkts}   # to the hour
        mkts.sort(key=lambda m: xl._num(m.get("volume_fp")), reverse=True)
        s0 = mkts[0]
        tail = (s0.get("ticker") or "").rsplit("-", 1)[-1]
        kind = "EUROPEAN?" if len(closes) > 3 else "barrier/single?"
        print(f"  {series[:22]:>22} {len(mkts):5d} {len(closes):6d} {tail:>13}  [{kind}]")
        for m in mkts[:2]:
            print(f"      tk={m.get('ticker')}")
            print(f"        title={(m.get('title') or '')[:62]!r}")
            print(f"        sub={m.get('yes_sub_title')!r} close={(m.get('close_time') or '')[:16]}"
                  f" yb={m.get('yes_bid_dollars')} ya={m.get('yes_ask_dollars')}"
                  f" vol={xl._num(m.get('volume_fp')):.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
