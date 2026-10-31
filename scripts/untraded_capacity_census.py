"""UNTRADED-CAPACITY CENSUS — which recurring, liquid Kalshi series has no book ever touched?

Pre-registered in docs/UNTRADED_CAPACITY_CENSUS.md (2026-10-31, catalog research study 4). A census:
its output is a shortlist for kalshi-idea-model screening; it tests no edge and authorizes nothing.

Settled, non-parlay markets from Kalshi public REST over the last WINDOW_DAYS (inside the live
endpoint's ~70-day retention), grouped by series: settled count, events, distinct close dates,
total / median volume, ladder width, YES share. Each series is tiered against what the bot has
touched in Postgres: A = never traded nor seen by the MMSELL scan, B = seen by the scan only.

Read-only: public Kalshi REST plus DATABASE_URL_RO (read-only transaction). Stdlib + psycopg.

Usage: {"type":"script","name":"untraded_capacity_census","args":[],"id":"rl-s4-run1-<date>"}
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import urllib.parse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
PAUSE_S = 0.08

# --- pre-registered constants ----------------------------------------------------------------
WINDOW_DAYS = 60
MIN_SETTLED, MIN_CLOSE_DATES = 60, 20
MIN_MEDIAN_VOLUME, MIN_TOTAL_VOLUME = 500.0, 100_000.0
PRINT_TOP = 40
SHORTLIST_PASS = 10
GRAVEYARD_CATEGORY = "climate and weather"
CRYPTO_CATEGORY = "crypto"

TOUCHED_SQL = {
    "paper": "SELECT DISTINCT split_part(market_ticker, '-', 1) FROM paper_trades",
    "live": "SELECT DISTINCT split_part(market_ticker, '-', 1) FROM live_orders",
    "scan": "SELECT DISTINCT series FROM mmsell_candidate_ticks",
}


# ---------------------------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_untraded_capacity_census.py)


def market_volume(m: dict) -> float:
    for key in ("volume_fp", "volume"):
        v = m.get(key)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def series_of(m: dict) -> str:
    return (m.get("series_ticker") or (m.get("ticker") or "").split("-", 1)[0]).upper()


def summarize(markets: list[dict]) -> dict:
    """Per-series census measures over its settled markets."""
    vols = [market_volume(m) for m in markets]
    events: dict[str, int] = defaultdict(int)
    for m in markets:
        events[m.get("event_ticker") or (m.get("ticker") or "").rsplit("-", 1)[0]] += 1
    dates = {(m.get("close_time") or "")[:10] for m in markets if m.get("close_time")}
    results = [(m.get("result") or "").lower() for m in markets]
    yn = [r for r in results if r in ("yes", "no")]
    return {
        "settled": len(markets), "events": len(events), "close_dates": len(dates),
        "total_volume": sum(vols), "median_volume": statistics.median(vols) if vols else 0.0,
        "ladder_width": statistics.median(events.values()) if events else 0,
        "yes_share": (sum(1 for r in yn if r == "yes") / len(yn)) if yn else None,
    }


def tier(series: str, traded: set[str], scanned: set[str]) -> str:
    if series in traded:
        return "traded"
    return "B" if series in scanned else "A"


def passes(s: dict) -> bool:
    return (s["settled"] >= MIN_SETTLED and s["close_dates"] >= MIN_CLOSE_DATES
            and s["median_volume"] >= MIN_MEDIAN_VOLUME
            and s["total_volume"] >= MIN_TOTAL_VOLUME)


def flags(category: str) -> str:
    c = (category or "").strip().lower()
    if c == GRAVEYARD_CATEGORY:
        return "graveyard-weather"
    if c == CRYPTO_CATEGORY:
        return "crypto"
    return ""


# ---------------------------------------------------------------------------------------------


def fetch_settled(now: int, max_pages: int) -> tuple[list[dict], int, bool]:
    import xvenue_leadlag as xl
    out, cursor, pages = [], "", 0
    lo = now - WINDOW_DAYS * 86400
    exhausted = False
    for _ in range(max_pages):
        q = {"status": "settled", "mve_filter": "exclude", "min_close_ts": lo,
             "max_close_ts": now, "limit": 1000}
        if cursor:
            q["cursor"] = cursor
        page = xl._get(f"{KALSHI}/markets?{urllib.parse.urlencode(q)}") or {}
        mk = page.get("markets") or []
        out.extend(mk)
        pages += 1
        cursor = page.get("cursor") or ""
        if not cursor or not mk:
            exhausted = True
            break
        time.sleep(PAUSE_S)
    return out, pages, exhausted


def fetch_categories() -> dict[str, tuple[str, str]]:
    import xvenue_leadlag as xl
    out, cursor = {}, ""
    for _ in range(200):
        q = {"cursor": cursor} if cursor else {}
        page = xl._get(f"{KALSHI}/series?{urllib.parse.urlencode(q)}") or {}
        for s in page.get("series") or []:
            if s.get("ticker"):
                out[s["ticker"].upper()] = (s.get("category") or "", s.get("frequency") or "")
        cursor = page.get("cursor") or ""
        if not cursor:
            break
        time.sleep(PAUSE_S)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-pages", type=int, default=3000, help="bounds API cost")
    args = ap.parse_args(argv)

    import psycopg
    from mmsell_flow_veto_probe import RO_OPTIONS, _to_libpq_url

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 1
    print("Untraded-capacity census (pre-registered docs/UNTRADED_CAPACITY_CENSUS.md)\n")
    touched: dict[str, set[str]] = {}
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        for k, sql in TOUCHED_SQL.items():
            cur.execute(sql)
            touched[k] = {(r[0] or "").upper() for r in cur.fetchall() if r[0]}
    traded = touched["paper"] | touched["live"]
    scanned = touched["scan"]
    print(f"bot has traded {len(traded)} series; the MMSELL scan has seen {len(scanned)}")

    now = int(time.time())
    t0 = time.time()
    markets, pages, exhausted = fetch_settled(now, args.max_pages)
    print(f"settled non-parlay markets, last {WINDOW_DAYS} days: {len(markets)} in {pages} pages "
          f"({'complete' if exhausted else 'TRUNCATED at --max-pages'}) [{time.time() - t0:.0f}s]")
    cats = fetch_categories()
    by_series: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        by_series[series_of(m)].append(m)
    rows = []
    for s, ms in by_series.items():
        summ = summarize(ms)
        cat, freq = cats.get(s, ("", ""))
        rows.append({"series": s, "tier": tier(s, traded, scanned), "category": cat,
                     "frequency": freq, "flag": flags(cat), "pass": passes(summ), **summ})
    print(f"series with settled markets: {len(rows)}; categories known for "
          f"{sum(1 for r in rows if r['category'])}")

    counts = defaultdict(lambda: [0, 0])
    for r in rows:
        counts[r["tier"]][0] += 1
        counts[r["tier"]][1] += r["pass"]
    for t in ("A", "B", "traded"):
        print(f"  tier {t:6s}: {counts[t][0]:5d} series, {counts[t][1]:4d} pass the volume/cadence rule")

    hdr = (f"  {'series':28s} {'category':22s} {'freq':9s} {'settled':>7} {'events':>6} "
           f"{'dates':>5} {'vol60d':>10} {'medvol':>8} {'ladder':>6} {'yes%':>5}  flag")
    for t, title in (("A", "TIER A — never traded, never seen by the MMSELL scan"),
                     ("B", "TIER B — seen by the MMSELL scan, never traded")):
        sl = sorted((r for r in rows if r["tier"] == t and r["pass"]),
                    key=lambda r: -r["total_volume"])
        print(f"\n== SHORTLIST {title} ({len(sl)} pass; top {PRINT_TOP})")
        print(hdr)
        for r in sl[:PRINT_TOP]:
            ys = "n/a" if r["yes_share"] is None else f"{100 * r['yes_share']:.0f}"
            print(f"  {r['series'][:28]:28s} {r['category'][:22]:22s} {r['frequency'][:9]:9s} "
                  f"{r['settled']:7d} {r['events']:6d} {r['close_dates']:5d} "
                  f"{r['total_volume']:10.0f} {r['median_volume']:8.0f} {r['ladder_width']:6.0f} "
                  f"{ys:>5}  {r['flag']}")
    by_cat = defaultdict(lambda: [0, 0.0])
    for r in rows:
        if r["tier"] in ("A", "B") and r["pass"]:
            by_cat[r["category"] or "?"][0] += 1
            by_cat[r["category"] or "?"][1] += r["total_volume"]
    print("\n== shortlisted series by category (tiers A+B)")
    for c, (n, v) in sorted(by_cat.items(), key=lambda kv: -kv[1][1]):
        print(f"  {c[:30]:30s} {n:4d} series  {v:12.0f} contracts / 60d")
    n_pass = sum(1 for r in rows if r["tier"] in ("A", "B") and r["pass"])
    verdict = "PASS — hand the shortlist to kalshi-idea-model" if n_pass >= SHORTLIST_PASS \
        else "FAIL — the untraded universe is thin"
    print(f"\n== VERDICT\n  C1 capacity exists ({n_pass} shortlisted vs bar {SHORTLIST_PASS}): "
          f"{verdict}\n  A census authorizes nothing; volume is not edge.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
