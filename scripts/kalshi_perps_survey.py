"""Perps survey — does Kalshi's mid-2026 zero-fee crypto PERPETUAL futures product expose a
price history to pull, and is there perp<->spot (or perp<->hourly-ladder) coherence/basis
structure that could ever survive NORMAL fees? (docs/IDEA_MODEL_20260709.md, Task 3 / slate
N4/N5/N15.) This is a SURVEY only — it does NOT promote a thesis. The zero-fee promo makes any
cost gate misleadingly easy; an edge that needs zero fee dies when fees normalize, so the
survey's job is just: (1) confirm a pullable perp price history exists, (2) measure the raw
perp-vs-spot basis so a later probe knows whether there's anything worth gating on fees.

Discovery is multi-angle because the perp product's series encoding is not known a priori:
  - scan open Crypto events for perp-like markers (no fixed expiry / 'perpetual' / 'perp');
  - probe candidate perp series tickers directly via /markets + /series;
  - for any perp mark series with candlesticks, pull the mark path and Coinbase spot and
    report the basis (perp - spot) distribution vs a normal-fee reference.

Read-only public APIs, stdlib only. Usage (via the ops channel):
    {"type":"script","name":"kalshi_perps_survey","args":["--asset","BTC"]}
"""

from __future__ import annotations

import argparse
import statistics
import time

import xvenue_leadlag as xl  # _get, _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
COINBASE = "https://api.exchange.coinbase.com"

# Candidate perp series tickers to probe directly (fail-soft; discovery prints which resolve).
PERP_CANDIDATES = {
    "BTC": ("KXBTCPERP", "KXPERPBTC", "BTCPERP", "KXBTCP", "KXBTCPERPUSD"),
    "ETH": ("KXETHPERP", "KXPERPETH", "ETHPERP", "KXETHP", "KXETHPERPUSD"),
}
SPOT_PRODUCT = {"BTC": "BTC-USD", "ETH": "ETH-USD"}


def scan_events(asset: str, max_pages: int) -> dict[str, list[dict]]:
    """series_ticker -> markets, for Crypto events whose series/title/subtitle look perp-like."""
    out: dict[str, list[dict]] = {}
    cursor = ""
    n_ev = 0
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for e in evs:
            if (e.get("category") or "") != "Crypto":
                continue
            n_ev += 1
            series = (e.get("series_ticker") or "").upper()
            if asset.upper() not in series and asset.upper() not in (e.get("title") or "").upper():
                continue
            blob = f"{series} {e.get('title','')} {e.get('sub_title','')}".lower()
            markers = ("perp" in blob or "perpetual" in blob or "continuous" in blob)
            mkts = e.get("markets") or []
            # perp-like structure: a single always-open market with no per-period close ladder
            if markers:
                out.setdefault(series, []).extend(mkts)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
    print(f"  scanned {n_ev} open Crypto events; perp-marked series: {sorted(out) or '(none)'}")
    return out


def probe_series(series: str) -> list[dict]:
    for status in ("", "&status=settled"):
        page = xl._get(f"{KALSHI}/markets?series_ticker={series}&limit=50{status}")
        mkts = (page or {}).get("markets") or []
        if mkts:
            return mkts
    return []


def kalshi_candles(series: str, ticker: str, start: int, end: int) -> dict[int, float]:
    """minute -> yes/mark mid (dollars). Chunked under the ~5000-period cap."""
    out: dict[int, float] = {}
    s = start
    while s < end:
        e = min(s + 4800 * 60, end)
        data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                       f"?start_ts={s}&end_ts={e}&period_interval=1")
        for c in (data or {}).get("candlesticks") or []:
            ts = c.get("end_period_ts")
            if ts is None:
                continue
            yb = (c.get("yes_bid") or {}).get("close_dollars")
            ya = (c.get("yes_ask") or {}).get("close_dollars")
            if yb is not None and ya is not None:
                out[int(ts) // 60] = (xl._num(yb) + xl._num(ya)) / 2.0
            else:
                px = xl._num((c.get("price") or {}).get("close_dollars"))
                if px > 0:
                    out[int(ts) // 60] = px
        s = e
    return out


def coinbase_candles(product: str, start: int, end: int) -> dict[int, float]:
    import json
    import urllib.request
    out: dict[int, float] = {}
    s = start
    while s < end:
        e = min(s + 300 * 60, end)
        url = (f"{COINBASE}/products/{product}/candles?granularity=60"
               f"&start={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s))}"
               f"&end={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(e))}")
        for attempt in range(4):
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0",
                "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                    for r in json.loads(resp.read().decode("utf-8")) or []:
                        out[int(r[0])] = float(r[4])
                break
            except Exception:  # noqa: BLE001
                if attempt == 3:
                    break
                time.sleep(1.5 * (attempt + 1))
        time.sleep(0.15)
        s = e
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--days", type=float, default=3.0)
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--normal-fee-bps", type=float, default=10.0,
                    help="reference normal round-trip fee (bps) the basis must beat to matter")
    args = ap.parse_args(argv)
    asset = args.asset.upper()
    now = int(time.time())
    start = now - int(args.days * 86400)

    print(f"=== Kalshi crypto PERPS survey — {asset}, {args.days:g}d ===\n")

    print("--- discovery (event scan) ---")
    found = scan_events(asset, args.max_pages)

    print("\n--- discovery (candidate series probe) ---")
    perp_series: dict[str, list[dict]] = dict(found)
    for cand in PERP_CANDIDATES.get(asset, ()):  # noqa: PLR2004
        mkts = probe_series(cand)
        print(f"  {cand:<16} -> {len(mkts)} markets"
              + (f"  e.g. {mkts[0].get('ticker')}" if mkts else ""))
        if mkts:
            perp_series.setdefault(cand.upper(), []).extend(mkts)

    if not perp_series:
        print("\n=== VERDICT ===")
        print("  No perpetual-futures series discovered via the public event/market API. Either")
        print("  the perp product is not exposed on the standard trade-api market endpoints, or")
        print("  its series encoding differs from the candidates probed. NO price history to pull")
        print("  yet -> the survey cannot proceed to a basis/coherence read. Do not promote; note")
        print("  the discovery gap and revisit with the correct perp endpoint/series if surfaced.")
        return 0

    # For the first perp series with a liquid market, pull the mark path + spot and report basis.
    print("\n--- basis vs spot (raw; before any fee gate) ---")
    product = SPOT_PRODUCT.get(asset, f"{asset}-USD")
    spot = coinbase_candles(product, start, now)
    print(f"  Coinbase {product}: {len(spot)} 1-min closes")
    ref = args.normal_fee_bps / 1e4
    for series, mkts in perp_series.items():
        mkts = sorted(mkts, key=lambda m: xl._num(m.get("volume_fp") or m.get("volume")), reverse=True)
        m0 = mkts[0]
        tk = m0.get("ticker") or ""
        marks = kalshi_candles(series, tk, start, now)
        print(f"  {series} top mkt {tk}: {len(marks)} candles  vol={xl._num(m0.get('volume_fp')):.0f}")
        if not marks or not spot:
            print("     (insufficient overlap to compute a basis)")
            continue
        # Perp candles are a probability (0..1) for event-style contracts, or a mark price for a
        # true perp. If they look like probabilities (all <=1), we can't basis them vs $ spot;
        # report and let a follow-up probe handle the real mark feed.
        vals = list(marks.values())
        if max(vals) <= 1.0:
            print("     candles are 0..1 (event-contract prob, not a $ mark) -> no direct $ basis;"
                  " a true perp mark feed is needed for a basis read.")
            continue
        common = sorted(set(marks) & set(spot))
        if len(common) < 20:
            print(f"     only {len(common)} overlapping minutes — too few for a basis distribution")
            continue
        bases = [(marks[t] - spot[t]) / spot[t] for t in common]  # relative basis
        med = statistics.median([abs(b) for b in bases])
        p90 = sorted(abs(b) for b in bases)[int(0.9 * len(bases))]
        print(f"     |basis| median={med*1e4:.1f}bps  p90={p90*1e4:.1f}bps  "
              f"(normal-fee ref={args.normal_fee_bps:g}bps) -> "
              f"{'exceeds fee (worth a probe)' if p90 > ref else 'inside fees (likely no edge)'}")

    print("\n=== VERDICT ===")
    print("  Survey only — NO thesis promoted. Zero-fee promo is not a durable edge; any signal")
    print("  above must clear NORMAL fees (see the bps read) before earning a pre-registered")
    print("  thesis. Next step only if a $ mark feed shows |basis| p90 > normal fees at a")
    print("  tradeable frequency: write a funding/basis staleness probe against that feed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
