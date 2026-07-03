"""Discovery probe for the in-play game-market lead-lag test — reveal how Kalshi and
Polymarket each structure live/recent SPORTS GAME markets (team encoding, tickers, tokens),
and confirm the sub-minute trade-tape endpoints work, so the matched shock analyzer can be
built against real formats instead of guesses.

Prints:
  - Kalshi recent game markets for a few game series (ticker, title, subtitle, price, close).
  - Polymarket recent sports game markets (question, token, endDate, price) via tag slugs.
  - A sample of the trade tape on each venue (Kalshi /markets/{t}/trades, Polymarket
    data-api /trades) to confirm sub-minute timestamps + price are available.

Read-only public APIs, stdlib only. Usage:
    {"type":"script","name":"xvenue_game_probe","args":["--kalshi-series","KXMLBGAME,KXWCGAME"]}
"""

from __future__ import annotations

import argparse
import json

import xvenue_leadlag as xl  # _get, _num

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def probe_kalshi(series_list: list[str]) -> str | None:
    sample_ticker = None
    for s in series_list:
        print(f"\n=== Kalshi game markets — series {s} ===")
        page = xl._get(f"{KALSHI}/markets?series_ticker={s}&limit=12")
        mkts = (page or {}).get("markets") or []
        if not mkts:
            print("  (none open; trying settled)")
            page = xl._get(f"{KALSHI}/markets?series_ticker={s}&status=settled&limit=12")
            mkts = (page or {}).get("markets") or []
        mkts.sort(key=lambda m: xl._num(m.get("volume_fp")), reverse=True)
        for m in mkts[:8]:
            print(f"  {m.get('ticker'):<34} vol={xl._num(m.get('volume_fp')):>9,.0f}"
                  f" yb={m.get('yes_bid_dollars')} ya={m.get('yes_ask_dollars')}"
                  f" st={m.get('status')} close={(m.get('close_time') or '')[:16]}")
            print(f"       title={(m.get('title') or '')[:60]!r} sub={m.get('yes_sub_title')!r}")
            if sample_ticker is None and xl._num(m.get("volume_fp")) > 0:
                sample_ticker = (s, m.get("ticker"))
    return sample_ticker


def probe_polymarket(tags: list[str]) -> str | None:
    sample_token = None
    for tag in tags:
        print(f"\n=== Polymarket markets — tag '{tag}' ===")
        evs = xl._get(f"{GAMMA}/events?closed=false&tag_slug={tag}&limit=20&order=volume&ascending=false")
        if not isinstance(evs, list):
            evs = (evs or {}).get("events") if isinstance(evs, dict) else []
        shown = 0
        for e in evs or []:
            for m in (e.get("markets") or [])[:1]:
                toks = m.get("clobTokenIds")
                if isinstance(toks, str):
                    try:
                        toks = json.loads(toks)
                    except json.JSONDecodeError:
                        toks = None
                tok = (toks[0] if isinstance(toks, list) and toks else "")
                vol = xl._num(m.get("volumeNum") or m.get("volume"))
                print(f"  vol={vol:>10,.0f} end={(m.get('endDate') or '')[:16]}"
                      f" q={(m.get('question') or '')[:56]!r}")
                if sample_token is None and tok and vol > 0:
                    sample_token = (tok, m.get("conditionId"))
                shown += 1
            if shown >= 12:
                break
    return sample_token


def probe_trades(kalshi_pair, pm_pair) -> None:
    print("\n=== Trade-tape endpoints (sub-minute) ===")
    if kalshi_pair:
        s, t = kalshi_pair
        data = xl._get(f"{KALSHI}/markets/{t}/trades?limit=5")
        trades = (data or {}).get("trades") or []
        print(f"  Kalshi /markets/{t}/trades -> {len(trades)} trades")
        if trades:
            print(f"    keys: {sorted(trades[0].keys())}")
            print(f"    sample: {json.dumps(trades[0])[:240]}")
    if pm_pair:
        tok, cond = pm_pair
        for url in (f"{DATA}/trades?market={cond}&limit=5", f"{DATA}/trades?asset={tok}&limit=5"):
            data = xl._get(url)
            n = len(data) if isinstance(data, list) else len((data or {}).get("trades") or [])
            print(f"  Polymarket {url.split('?')[1]} -> {n} trades")
            if isinstance(data, list) and data:
                print(f"    keys: {sorted(data[0].keys())}")
                print(f"    sample: {json.dumps(data[0])[:240]}")
                break


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kalshi-series", default="KXMLBGAME,KXWCGAME,KXWCROUND,KXNBA")
    ap.add_argument("--pm-tags", default="mlb,soccer,sports")
    args = ap.parse_args(argv)
    kp = probe_kalshi([s.strip() for s in args.kalshi_series.split(",") if s.strip()])
    pp = probe_polymarket([t.strip() for t in args.pm_tags.split(",") if t.strip()])
    probe_trades(kp, pp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
