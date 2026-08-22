"""How far back does Coinbase serve 1-MINUTE candles, and can theta's fit window be backfilled?

`docs/RESEARCH_THETA_REMEDIATION.md` needs one fact it cannot get from the database: whether the
replacement model's fit window can be filled from history TODAY, or whether it has to accumulate
forward from now. `crypto_spot_candles` was pruned at 6 days, so the answer decides between
"refit and validate this week" and "the earliest date calibration becomes estimable is X".

Read-only against Coinbase's PUBLIC candles endpoint; touches no database and writes nothing.
stdlib only (urllib, not httpx) because the ops runner installs psycopg alone for `script`
requests.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.error
import urllib.request

BASE = "https://api.exchange.coinbase.com"
PROBE_DAYS = (1, 7, 14, 30, 60, 90, 180, 365)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def probe(product: str, days_ago: int, timeout: float = 20.0) -> tuple[int, str]:
    """(candles returned, note) for a 300-minute window `days_ago` days back."""
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    start = end - dt.timedelta(minutes=300)
    url = (f"{BASE}/products/{product}/candles?granularity=60"
           f"&start={_iso(start)}&end={_iso(end)}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "kalshi-bot theta backfill probe (research)",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed host
            rows = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return (0, f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 — a probe must report, not raise
        return (0, f"error: {str(exc)[:80]}")
    if not isinstance(rows, list):
        return (0, f"unexpected payload: {str(rows)[:80]}")
    if not rows:
        return (0, "empty")
    oldest = dt.datetime.fromtimestamp(min(int(r[0]) for r in rows), tz=dt.timezone.utc)
    return (len(rows), f"oldest {oldest:%Y-%m-%d %H:%M}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--products", default="BTC-USD,ETH-USD")
    args = ap.parse_args(argv)

    print("Coinbase 1-minute candle availability, 300-minute probe window per row.")
    print("A full window (300) means that depth is backfillable; 0 means it is not.\n")
    print(f"  {'product':<10} {'days ago':>9} {'candles':>8}  note")
    print("  " + "-" * 56)
    deepest: dict[str, int] = {}
    for product in [p.strip() for p in args.products.split(",") if p.strip()]:
        for d in PROBE_DAYS:
            n, note = probe(product, d)
            print(f"  {product:<10} {d:>9} {n:>8}  {note}")
            if n >= 250:
                deepest[product] = d
    print()
    if deepest:
        for product, d in deepest.items():
            print(f"  {product}: 1-minute history reaches at least {d} days back.")
        least = min(deepest.values())
        print(f"\n  BACKFILLABLE DEPTH (worst product): ~{least} days.")
        print(f"  At 300 candles/request that is ~{least * 1440 // 300} requests per product.")
    else:
        print("  NO depth confirmed. The fit window must ACCUMULATE forward from now;")
        print("  see docs/RESEARCH_THETA_REMEDIATION.md for the resulting date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
