"""Why does the event page's inline quote disagree with the orderbook?

WHAT THIS TESTS
---------------
`mmsell_quote_parity` measured the inline quote from `GET /events?with_nested_markets=true`
against the orderbook the scan fetches, and found ~0.9% of markets disagree by more than 5c —
the irreducible floor under the pre-filter's miss rate. Characterizing those outliers turned up
a pattern the aggregate could not explain (2026-08-13):

    ob=6/9    inline=6/54     bid identical, ask +45
    ob=3/13   inline=3/50     bid identical, ask +37
    ob=2/72   inline=40/72    ask identical, bid +38
    ob=35/40  inline=0/41     ask ~identical, bid -35

ONE SIDE MATCHES EXACTLY WHILE THE OTHER IS WILDLY OFF. A stale cache would move both. That
points at a DEFINITIONAL difference rather than staleness — and the two have opposite fixes:

  * staleness      -> a pre-filter must distrust the whole quote, and the ~1% loss is real;
  * definitional   -> the inline bid and ask are each fine, and only the DERIVED midpoint is
                      wrong, so the pre-filter should use them directly and the loss mostly
                      disappears.

Kalshi's book holds resting BIDS only: a YES ask at price p is a resting NO bid at 100-p, which
is how `scanner.metrics` derives `best_yes_ask = 100 - best_no_bid`. If Kalshi's inline
`yes_ask` is computed some other way (a literal ask field, a last-trade fallback, a different
snapshot), the two will diverge exactly on markets where one side of the book is thin.

So: fetch the event page and each market's orderbook AT THE SAME INSTANT and print both, with
the raw ladder. No inference, no waiting for telemetry to accumulate — the payloads either agree
or they do not.

READ THE VERDICT TABLE
----------------------
Per market it reports whether `inline_yes_ask == 100 - best_no_bid` and whether
`inline_yes_bid == best_yes_bid`. The interesting rows are where one holds and the other fails,
and for those it prints the top of BOTH ladders so the reason is visible rather than guessed.

Read-only: GETs Kalshi's PUBLIC market-data endpoints only (no API key needed, no orders).
A browser User-Agent clears Cloudflare's 1010, same as railway_logs.py. Stdlib only.

    {"type":"script","name":"kalshi_quote_probe"}
    {"type":"script","name":"kalshi_quote_probe","args":["--series","KXWNBAPTS","KXWTASETWINNER"]}
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# The series the outlier characterization flagged, at 17-120x their share of candidate flow.
DEFAULT_SERIES = ("KXWNBAPTS", "KXWTASETWINNER", "KXWNBA1HTOTAL", "KXLEAGUESCUPSCORE",
                  "KXMLBTEAMTOTAL", "KXWNBASPREAD")


def _get(path: str, params: dict | None = None) -> dict | None:
    url = f"{BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"  HTTP {exc.code} for {path}: {exc.read().decode('utf-8', 'replace')[:200]}",
              file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"  error for {path}: {exc}", file=sys.stderr)
    return None


def _cents(market: dict, key: str) -> int | None:
    """A quote field in cents. Kalshi sends `<key>_dollars` as a STRING and omits the legacy
    integer key on live data, so reading `market[key]` alone is silently always None — the same
    trap documented in scanner.metrics.market_price_cents."""
    if market.get(key) is not None:
        try:
            return int(market[key])
        except (TypeError, ValueError):
            pass
    raw = market.get(f"{key}_dollars")
    try:
        return int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return None


def _book_sides(ob: dict | None):
    """The (yes, no) ladders out of either payload shape, mirroring
    scanner.metrics.orderbook_levels. Kalshi migrated this endpoint to `orderbook_fp` with
    `yes_dollars`/`no_dollars` STRING prices and dropped the legacy integer-cent keys — the same
    migration that made `market.get("yes_bid")` silently None. Assuming the classic shape here
    produced a table of Nones on the first run of this script, which its own verdict text then
    mis-explained as evidence about staleness."""
    ob = ob or {}
    book = ob.get("orderbook") or ob.get("orderbook_fp") or ob
    yes = book.get("yes")
    if yes is None:
        yes = book.get("yes_dollars")
    no = book.get("no")
    if no is None:
        no = book.get("no_dollars")
    return _levels(yes), _levels(no)


def _price_cents(v) -> int | None:
    """A level price in cents, from either an integer-cent value or a `_dollars` string."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Dollar strings are < 1.0 for every real contract price; integer cents are >= 1.
    return int(round(f * 100)) if isinstance(v, str) or f < 1.0 else int(round(f))


def _levels(raw) -> list[tuple[int, int]]:
    out = []
    for lvl in raw or []:
        try:
            price = _price_cents(lvl[0])
            size = int(round(float(lvl[1])))
        except (TypeError, ValueError, IndexError):
            continue
        if price is not None:
            out.append((price, size))
    return sorted(out, key=lambda x: -x[0])


def probe(series_list: list[str], per_series: int, min_volume: int) -> int:
    print(f"=== inline quote vs orderbook — {len(series_list)} series,"
          f" up to {per_series} markets each ===\n")
    rows = []
    for series in series_list:
        events = _get("/events", {"status": "open", "with_nested_markets": "true",
                                  "limit": 50, "series_ticker": series})
        if not events:
            continue
        markets = [(e, m) for e in (events.get("events") or [])
                   for m in (e.get("markets") or [])]
        picked = 0
        for _event, m in markets:
            if picked >= per_series:
                break
            ticker = m.get("ticker")
            if not ticker:
                continue
            vol = m.get("volume") or m.get("volume_fp") or 0
            try:
                if float(vol) < min_volume:
                    continue
            except (TypeError, ValueError):
                continue
            # Fetched immediately after the event page, so the two are as close to the same
            # instant as two HTTP calls allow. A disagreement here is structural, not a race.
            ob = _get(f"/markets/{ticker}/orderbook", {"depth": 5})
            yes, no = _book_sides(ob)
            if not rows and not (yes or no):
                # The shape is not what we assumed and NOTHING downstream can be trusted.
                # Printed rather than silently producing empty columns, because a table of
                # Nones reads like "the markets are empty" instead of "the probe is broken" —
                # and this script's own verdict text would then confidently mis-explain it.
                print(f"  !! no levels parsed for {ticker}. Raw response:\n"
                      f"     {json.dumps(ob)[:900]}\n")
            picked += 1
            rows.append({
                "ticker": ticker, "vol": vol,
                "in_bid": _cents(m, "yes_bid"), "in_ask": _cents(m, "yes_ask"),
                "in_no_bid": _cents(m, "no_bid"),
                "ob_yes_bid": yes[0][0] if yes else None,
                "ob_no_bid": no[0][0] if no else None,
                "yes_levels": yes[:3], "no_levels": no[:3],
            })

    if not rows:
        print("no markets fetched — series may be out of season or the API refused.")
        return 0

    print(f"  {'ticker':<38} {'inline bid/ask':>15} {'ob yes_bid':>11} {'ob no_bid':>10}"
          f" {'100-no_bid':>11} {'bid?':>5} {'ask?':>5}")
    agree_bid = agree_ask = 0
    interesting = []
    for r in rows:
        derived = (100 - r["ob_no_bid"]) if r["ob_no_bid"] is not None else None
        bid_ok = r["in_bid"] is not None and r["in_bid"] == r["ob_yes_bid"]
        ask_ok = r["in_ask"] is not None and derived is not None and r["in_ask"] == derived
        agree_bid += bid_ok
        agree_ask += ask_ok
        if bid_ok != ask_ok:
            interesting.append((r, derived))
        print(f"  {r['ticker'][:38]:<38} {str(r['in_bid']) + '/' + str(r['in_ask']):>15}"
              f" {str(r['ob_yes_bid']):>11} {str(r['ob_no_bid']):>10} {str(derived):>11}"
              f" {'Y' if bid_ok else 'no':>5} {'Y' if ask_ok else 'no':>5}")

    n = len(rows)
    print(f"\n  bid agrees {agree_bid}/{n}   ask agrees (vs 100-no_bid) {agree_ask}/{n}")

    # A verdict requires DATA. Without orderbook levels every comparison is None-vs-None, which
    # scores as "they fail together" and would print a confident staleness conclusion drawn from
    # nothing at all. Refuse instead — this exact failure produced a wrong answer on the first
    # run of this script (2026-08-13).
    if not any(r["ob_yes_bid"] is not None or r["ob_no_bid"] is not None for r in rows):
        print("\n=== NO VERDICT: no orderbook levels were parsed for ANY market ===")
        print("  Every comparison above is None-vs-None, so nothing here says anything about"
              "\n  staleness or derivation. Fix the fetch/parse first — see the raw response"
              "\n  printed above for the actual payload shape.")
        return 0

    print("\n=== THE ONE-SIDED CASES (bid and ask disagree differently) ===")
    if not interesting:
        print("  none — the two sides agree or fail together, which argues for STALENESS"
              "\n  rather than a definitional difference. A pre-filter must then distrust the"
              "\n  whole quote, and the ~1% miss floor is the honest price of the idea.")
    for r, derived in interesting[:8]:
        print(f"\n  {r['ticker']}  vol={r['vol']}")
        print(f"    inline : yes_bid={r['in_bid']}  yes_ask={r['in_ask']}"
              f"  no_bid={r['in_no_bid']}")
        print(f"    book   : yes {r['yes_levels']}   no {r['no_levels']}")
        print(f"    derived: 100 - no_bid({r['ob_no_bid']}) = {derived}")
        if r["in_no_bid"] is not None and r["ob_no_bid"] is not None:
            print(f"    inline no_bid {r['in_no_bid']} vs book no_bid {r['ob_no_bid']}"
                  f"  -> {'AGREE' if r['in_no_bid'] == r['ob_no_bid'] else 'DIFFER'}")
    print("\n  If the inline no_bid matches the book while the inline yes_ask does not equal"
          "\n  100 - no_bid, the inline ask is NOT derived the way ours is, and the fix is to"
          "\n  use the inline sides directly rather than distrusting the quote.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series", nargs="*", default=list(DEFAULT_SERIES))
    ap.add_argument("--per-series", type=int, default=6)
    ap.add_argument("--min-volume", type=int, default=50)
    args = ap.parse_args(argv)
    return probe(args.series, args.per_series, args.min_volume)


if __name__ == "__main__":
    raise SystemExit(main())
