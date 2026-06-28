"""Broad cross-venue crypto scan — match EVERY crypto market that exists on BOTH Kalshi and
Polymarket (all assets, not just BTC), measure how far apart the two venues price the same
contract, and (with --leadlag) whether Kalshi follows Polymarket on the matched pairs.

Crypto is matched STRUCTURALLY to avoid false pairs: (asset, strike +/- tol, direction
above/below, same resolution day). A mismatched pair would fake a divergence, so precision
beats recall here. Kalshi strike comes from the ticker (reliable); Polymarket strike from the
question text.

Phase 1 (default): match + CURRENT divergence snapshot, ranked. Cheap, broad.
Phase 2 (--leadlag): for matched pairs, pull aligned minute history and run the same
divergence / lead-lag / gap-closing analysis as xvenue_leadlag.

Read-only public APIs (Kalshi + Polymarket Gamma/CLOB), stdlib only. Reuses the stats
helpers from xvenue_leadlag. Usage:
    {"type":"script","name":"xvenue_crypto","args":["--leadlag","--days","3"]}
"""

from __future__ import annotations

import argparse
import json
import re
import time

import xvenue_leadlag as xl  # align, leadlag, gap_closing, pm_series, GapAcc

GAMMA = "https://gamma-api.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_UA = xl._UA

ASSETS = {
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL", "xrp": "XRP", "ripple": "XRP", "dogecoin": "DOGE",
    "doge": "DOGE", "cardano": "ADA", "litecoin": "LTC", "avalanche": "AVAX",
    "chainlink": "LINK", "polkadot": "DOT", "shiba": "SHIB",
}
_UP = ("above", "over", "reach", "hit", "exceed", "more than", ">=", "≥", "or above", "greater")
_DOWN = ("below", "under", "less than", "<=", "≤", "or below", "dip")
_STRIKE = re.compile(r"\$?\s*([0-9][0-9,]*\.?[0-9]*)\s*([kKmM])?")


def _get(url: str):
    return xl._get(url)


def _num(v) -> float:
    return xl._num(v)


def detect_asset(text: str) -> str | None:
    low = (text or "").lower()
    for kw, a in ASSETS.items():
        if re.search(rf"\b{kw}\b", low):
            return a
    return None


def parse_strike(text: str) -> float | None:
    m = _STRIKE.search(text or "")
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suf = (m.group(2) or "").lower()
    if suf == "k":
        val *= 1_000
    elif suf == "m":
        val *= 1_000_000
    return val


def parse_dir(text: str) -> str:
    low = (text or "").lower()
    if any(w in low for w in _DOWN):
        return "down"
    if any(w in low for w in _UP):
        return "up"
    return "up"  # default: most crypto markets are "above"


def _day(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(int(float(ts))))
    except (TypeError, ValueError, OSError):
        return ""


def _parse_iso_day(s: str) -> str:
    s = (s or "")[:10]
    return s if re.match(r"\d{4}-\d{2}-\d{2}", s) else ""


# --- venue fetchers ----------------------------------------------------------------


def fetch_kalshi_crypto(max_days: float) -> list[dict]:
    """Open Kalshi crypto markets via /events (category=Crypto): asset/strike/dir/day + prices."""
    out: list[dict] = []
    cursor = ""
    max_close = int(time.time() + max_days * 86400)
    for _ in range(60):
        page = _get(f"{KALSHI}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for e in evs:
            if (e.get("category") or "") != "Crypto":
                continue
            asset = detect_asset(f"{e.get('title', '')} {e.get('series_ticker', '')}")
            for m in e.get("markets") or []:
                close = m.get("close_time")
                cts = None
                if close:
                    try:
                        cts = int(time.mktime(time.strptime(close[:19], "%Y-%m-%dT%H:%M:%S")))
                    except (ValueError, TypeError):
                        cts = None
                if cts and cts > max_close:
                    continue
                title = f"{m.get('title', '')} {m.get('yes_sub_title', '')}"
                a = asset or detect_asset(title)
                strike = parse_strike(m.get("yes_sub_title") or "") or parse_strike(m.get("title") or "")
                if not a or strike is None:
                    continue
                mid = (_num(m.get("yes_bid_dollars")) + _num(m.get("yes_ask_dollars"))) / 2.0
                out.append({
                    "venue": "kalshi", "asset": a, "strike": strike, "dir": parse_dir(title),
                    "day": _day(cts) if cts else "", "ticker": m.get("ticker"),
                    "series": e.get("series_ticker"), "yes": mid,
                    "vol": _num(m.get("volume_fp")),
                })
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
    return out


def fetch_pm_crypto(max_pages: int) -> list[dict]:
    """Open Polymarket markets that are crypto price contracts: asset/strike/dir/day + token."""
    out: list[dict] = []
    for page in range(max_pages):
        batch = _get(f"{GAMMA}/markets?closed=false&active=true&limit=100&offset={page * 100}")
        if not isinstance(batch, list) or not batch:
            break
        for m in batch:
            q = m.get("question") or ""
            a = detect_asset(q)
            strike = parse_strike(q)
            if not a or strike is None:
                continue
            toks = m.get("clobTokenIds")
            if isinstance(toks, str):
                try:
                    toks = json.loads(toks)
                except json.JSONDecodeError:
                    toks = None
            if not (isinstance(toks, list) and toks):
                continue
            bid, ask = m.get("bestBid"), m.get("bestAsk")
            yes = (_num(bid) + _num(ask)) / 2.0 if bid is not None and ask is not None else 0.0
            out.append({
                "venue": "pm", "asset": a, "strike": strike, "dir": parse_dir(q),
                "day": _parse_iso_day(m.get("endDate") or ""), "token": str(toks[0]),
                "question": q[:48], "yes": yes, "vol": _num(m.get("volumeNum") or m.get("volume")),
            })
        if len(batch) < 100:
            break
    return out


def match(kal: list[dict], pm: list[dict], strike_tol: float) -> list[tuple[dict, dict, float]]:
    """Match on (asset, day, dir, strike within tol). Returns (kalshi, pm, strike_diff)."""
    by_key: dict[tuple, list[dict]] = {}
    for p in pm:
        by_key.setdefault((p["asset"], p["day"], p["dir"]), []).append(p)
    pairs = []
    for k in kal:
        cands = by_key.get((k["asset"], k["day"], k["dir"]), [])
        best = None
        for p in cands:
            d = abs(p["strike"] - k["strike"])
            rel = d / max(k["strike"], 1.0)
            if rel <= strike_tol and (best is None or d < best[2]):
                best = (k, p, d)
        if best:
            pairs.append(best)
    return pairs


# --- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-days", type=float, default=20.0, help="Kalshi markets closing within N days")
    ap.add_argument("--max-pages", type=int, default=16, help="Polymarket pages")
    ap.add_argument("--strike-tol", type=float, default=0.002, help="relative strike match tol")
    ap.add_argument("--leadlag", action="store_true", help="pull history + lead-lag on matches")
    ap.add_argument("--days", type=float, default=3.0, help="history window for --leadlag")
    ap.add_argument("--gap", type=float, default=4.0)
    ap.add_argument("--horizon", type=int, default=10)
    args = ap.parse_args(argv)

    kal = fetch_kalshi_crypto(args.max_days)
    pm = fetch_pm_crypto(args.max_pages)
    pairs = match(kal, pm, args.strike_tol)
    print("=== Cross-venue CRYPTO scan ===")
    print(f"  Kalshi crypto markets: {len(kal)}   Polymarket crypto markets: {len(pm)}"
          f"   matched pairs: {len(pairs)}")
    by_asset: dict[str, int] = {}
    for k, _p, _d in pairs:
        by_asset[k["asset"]] = by_asset.get(k["asset"], 0) + 1
    print(f"  matches by asset: {by_asset}")
    if not pairs:
        print("  (no structural matches — assets present:"
              f" kalshi={sorted({k['asset'] for k in kal})} pm={sorted({p['asset'] for p in pm})})")
        return 0

    print(f"\n  {'asset':>5} {'strike':>10} {'day':>10} {'dir':>4} {'kYes':>5} {'pmYes':>5}"
          f" {'div':>5} {'kVol':>9}  kalshi_ticker")
    pairs.sort(key=lambda x: -abs(x[0]["yes"] - x[1]["yes"]))
    for k, p, _d in pairs[:40]:
        div = abs(k["yes"] - p["yes"]) * 100
        print(f"  {k['asset']:>5} {k['strike']:>10,.0f} {k['day']:>10} {k['dir']:>4}"
              f" {k['yes'] * 100:4.0f}c {p['yes'] * 100:4.0f}c {div:4.1f}c {k['vol']:9,.0f}"
              f"  {k['ticker']}")
    divs = [abs(k["yes"] - p["yes"]) * 100 for k, p, _ in pairs]
    print(f"\n  current-snapshot divergence: avg={sum(divs) / len(divs):.1f}c"
          f"  median={sorted(divs)[len(divs) // 2]:.1f}c"
          f"  %>{args.gap:g}c={100.0 * sum(1 for d in divs if d >= args.gap) / len(divs):.0f}%")

    if args.leadlag:
        end = int(time.time())
        start = end - int(args.days * 86400)
        print(f"\n  --- lead-lag on matched pairs ({args.days:g}d, 1-min) ---")
        print(f"  {'asset':>5} {'strike':>9} {'pts':>5} {'|div|':>6} {'>gap%':>6} {'bestLag':>8}"
              f" {'corr':>6} {'gapN':>5} {'close%':>6} {'kMove':>6}")
        pool = xl.GapAcc()
        for k, p, _d in pairs[:25]:
            ks = kalshi_candles(k["series"], k["ticker"], start, end)
            ps = xl.pm_series(p["token"], start, end)
            x, y = xl.align(ks, ps)
            if len(x) < 30:
                continue
            div = [abs(b - a) * 100 for a, b in zip(x, y, strict=False)]
            ad = sum(div) / len(div)
            pg = 100.0 * sum(1 for d in div if d >= args.gap) / len(div)
            (bk, bc), _r = xl.leadlag(x, y, 10)
            ga = xl.gap_closing(x, y, args.gap, args.horizon)
            pool.n += ga.n
            pool.closed += ga.closed
            pool.move_sum += ga.move_sum
            cl = f"{100.0 * ga.closed / ga.n:5.0f}%" if ga.n else "  n/a"
            km = f"{ga.move_sum / ga.n * 100:+5.1f}c" if ga.n else "  n/a"
            print(f"  {k['asset']:>5} {k['strike']:>9,.0f} {len(x):5d} {ad:5.1f}c {pg:5.0f}%"
                  f" {bk:+8d} {bc:+5.2f} {ga.n:5d} {cl:>6} {km:>6}")
        if pool.n:
            print(f"\n  POOLED gap-events={pool.n}  closed={100.0 * pool.closed / pool.n:.0f}%"
                  f"  avg Kalshi move toward PM in {args.horizon}m={pool.move_sum / pool.n * 100:+.1f}c")
            print("  (tradeable if closed% > ~55% AND move clears Kalshi's ~2-4c round-trip)")
    return 0


def kalshi_candles(series: str, ticker: str, start: int, end: int) -> dict[int, float]:
    """Generalized chunked 1-min candle fetch for any Kalshi series."""
    out: dict[int, float] = {}
    step = 4800 * 60
    s = start
    while s < end:
        e = min(s + step, end)
        data = _get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                    f"?start_ts={s}&end_ts={e}&period_interval=1")
        for c in (data or {}).get("candlesticks") or []:
            ts = c.get("end_period_ts")
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            if ts is None:
                continue
            mid = (_num(yb) + _num(ya)) / 2.0 if yb is not None and ya is not None \
                else _num((c.get("price") or {}).get("close_dollars"))
            if mid > 0:
                out[int(ts) // 60] = mid
        s = e
    return out


if __name__ == "__main__":
    raise SystemExit(main())
