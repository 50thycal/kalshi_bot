"""SEASONPIN recon census — cheap, read-only enumeration answering ONLY "does a gradeable
tape exist?" for the season win-total ladder pin thesis (docs/SEASONPIN_THESIS.md).

Per the thesis's staged probe plan, this MUST run and clear before any full probe
(kalshi_seasonpin_study.py) gets written. It measures, against public Kalshi data only:

  1. Which win-total series exist (discovered dynamically off category=Sports events whose
     title matches "win total(s)" / "wins" ladder phrasing — no hardcoded ticker guess).
  2. Settled vs open rung counts, per-rung volume/spread (capacity read).
  3. The early-expiration question: `can_close_early` / `expiration_time` vs `close_time` on a
     sample of settled rungs, so we can tell whether Kalshi is settling the moment a rung is
     arithmetically decided (which would kill the thesis's latency budget) or only at season end.
  4. `rules_primary` text on a sample, to catch an explicit early-settlement clause in the rules
     the market fields alone might not show.

Pre-registered gate (from the thesis): if candle-covered decided-rung n < 40, or the sampled
early-expiration reads show settlement happening within ~24h of the rung going decided, this
is HOLD/KILL respectively and the full probe should NOT be written.

Read-only public Kalshi REST (no auth). Browser UA clears Cloudflare's 1010. Stdlib only.

Usage (via the ops channel):
    {"type":"script","name":"kalshi_seasonpin_census","args":["--max-event-pages","40"]}
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_WINTOTAL_RE = re.compile(r"win\s*total|(?<!\w)wins(?!\w)", re.I)


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
            return {}
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return {}
    return {}


def _paginate_events(status: str, max_pages: int):
    cursor = ""
    for _ in range(max_pages):
        page = _get("/events", {
            "status": status, "with_nested_markets": "true",
            "limit": 200, "cursor": cursor,
        })
        rows = page.get("events") or []
        yield from rows
        cursor = page.get("cursor") or ""
        if not cursor or not rows:
            return


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _cents(dollar_str) -> float | None:
    v = _num(dollar_str)
    return round(v * 100, 1) if v else None


def discover_series(events: list[dict]) -> dict[str, dict]:
    """series_ticker -> {title, category, event_tickers[]} for win-total-shaped events."""
    found: dict[str, dict] = {}
    for ev in events:
        if (ev.get("category") or "").lower() != "sports":
            continue
        title = ev.get("title") or ""
        if not _WINTOTAL_RE.search(title):
            continue
        series = ev.get("series_ticker") or (ev.get("event_ticker") or "").split("-", 1)[0]
        entry = found.setdefault(series, {"title": title, "category": ev.get("category"),
                                           "event_tickers": []})
        entry["event_tickers"].append(ev.get("event_ticker"))
    return found


def rung_stats(markets: list[dict]) -> dict:
    vols = [_num(m.get("volume")) for m in markets]
    spreads = []
    for m in markets:
        bid, ask = _cents(m.get("yes_bid_dollars")) or _num(m.get("yes_bid")), \
            _cents(m.get("yes_ask_dollars")) or _num(m.get("yes_ask"))
        if bid and ask and ask > bid:
            spreads.append(ask - bid)
    return {
        "n": len(markets),
        "total_vol": sum(vols),
        "avg_vol": (sum(vols) / len(vols)) if vols else 0.0,
        "avg_spread_c": (sum(spreads) / len(spreads)) if spreads else None,
    }


def early_expiration_sample(settled_markets: list[dict], sample_n: int) -> list[dict]:
    out = []
    for m in settled_markets[:sample_n]:
        close_t = m.get("close_time")
        exp_t = m.get("expiration_time")
        settle_t = m.get("settlement_timer_seconds")
        rules = (m.get("rules_primary") or "")[:280]
        out.append({
            "ticker": m.get("ticker"),
            "close_time": close_t,
            "expiration_time": exp_t,
            "can_close_early": m.get("can_close_early"),
            "settlement_timer_seconds": settle_t,
            "result": m.get("result"),
            "rules_primary_snip": rules,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-event-pages", type=int, default=40)
    ap.add_argument("--rules-sample", type=int, default=8)
    args = ap.parse_args(argv)

    print("=== SEASONPIN recon census (read-only) ===")

    open_events = list(_paginate_events("open", args.max_event_pages))
    settled_events = list(_paginate_events("settled", args.max_event_pages))
    print(f"scanned: {len(open_events)} open events, {len(settled_events)} settled events")

    open_series = discover_series(open_events)
    settled_series = discover_series(settled_events)
    all_series = sorted(set(open_series) | set(settled_series))

    if not all_series:
        print("\nNO win-total-shaped series found in the scanned window "
              "(broaden --max-event-pages or the title regex). CENSUS: NO INSTRUMENT FOUND.")
        return 0

    print(f"\nseries discovered (win-total-shaped, category=Sports): {all_series}")

    total_settled_rungs = 0
    for series in all_series:
        open_ev = open_series.get(series, {"title": "", "event_tickers": []})
        settled_ev = settled_series.get(series, {"title": "", "event_tickers": []})
        open_mkts = [m for ev in open_events if ev.get("series_ticker") == series or
                     (ev.get("event_ticker") or "").split("-", 1)[0] == series
                     for m in (ev.get("markets") or [])]
        settled_mkts = [m for ev in settled_events if ev.get("series_ticker") == series or
                         (ev.get("event_ticker") or "").split("-", 1)[0] == series
                         for m in (ev.get("markets") or [])]
        total_settled_rungs += len(settled_mkts)
        o_stats = rung_stats(open_mkts)
        s_stats = rung_stats(settled_mkts)
        print(f"\n--- {series} --- title sample: "
              f"{(open_ev['title'] or settled_ev['title'])!r}")
        print(f"  open rungs:    n={o_stats['n']:4d}  total_vol={o_stats['total_vol']:>10.0f}  "
              f"avg_vol={o_stats['avg_vol']:>8.1f}  avg_spread={o_stats['avg_spread_c']}")
        print(f"  settled rungs: n={s_stats['n']:4d}  total_vol={s_stats['total_vol']:>10.0f}  "
              f"avg_vol={s_stats['avg_vol']:>8.1f}  avg_spread={s_stats['avg_spread_c']}")

        if settled_mkts:
            print(f"  early-expiration sample ({min(args.rules_sample, len(settled_mkts))} "
                  f"of {len(settled_mkts)} settled rungs):")
            for row in early_expiration_sample(settled_mkts, args.rules_sample):
                print(f"    {row}")

    print(f"\n=== SUMMARY ===")
    print(f"series found: {len(all_series)} — {all_series}")
    print(f"total settled rungs across series: {total_settled_rungs} "
          f"(thesis P1 n-floor: 40 candle-covered decided rungs)")
    print("Gate check (manual read of the early-expiration samples above):")
    print("  - if expiration_time/close_time gap for settled rungs is consistently <24h after")
    print("    the rung's arithmetic decided-date (cross-check vs standings by hand), the")
    print("    thesis's latency budget is gone -> KILL, do not write the full probe.")
    print("  - if total settled rungs with real volume < 40, this is UNTESTABLE-yet -> HOLD,")
    print("    do not write the full probe; re-run once more of the season has settled.")
    print("  - otherwise: CENSUS CLEARS -> proceed to kalshi_seasonpin_study.py (full probe).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
