"""ART pre-stage enumeration — is the ART hold family (docs/IDEA_MODEL_20260712.md) TESTABLE yet?

The 2026-07-12 idea-model run parked an ART family (ARTSUM: evening-sale running-total pin;
GUARPIN: third-party-guarantee floor pin) behind a named trigger: "fall auction season AND a
cheap KXART* enumeration confirming testability." This is that enumeration — a read-only survey,
no thesis, no book. It answers the three questions that decide whether ART is worth a thesis:

  1. STRUCTURE — what art markets exist, and of what type? The ARTSUM pin needs **sale-TOTAL**
     markets (a ladder on "total realized value across the sale"); GUARPIN needs **per-lot**
     markets ("will lot X sell / hammer >= $Y"). Classify each market's title into
     total / per-lot-price / record-binary / other.
  2. CAPACITY — how many have SETTLED (a track record can only be built on settled history), and
     what volume do they trade? Art settles are lumpy and seasonal; a handful of $5k markets is a
     hobby, not $100/mo. Report settled vs open counts + volume per type.
  3. INTRA-SALE ACTIVITY (the load-bearing one for ARTSUM) — do these markets actually TRADE
     DURING the live auction? The partial-sum pin only exists if the sale-total quote reprices
     lot-by-lot as hammers fall. Probe candlesticks around a few settled markets' close windows
     and report whether there are trades in the final hours (a live tape) vs a single settlement
     jump (no intra-sale market to pin).

Verdict is a TESTABILITY call, not an edge call: PROMOTE-TO-THESIS if sale-total markets exist,
have settled with volume, AND show intra-sale trading; else HOLD (still too thin / no live tape)
with the specific missing piece named. Read-only public Kalshi REST, stdlib only. Usage:
    {"type":"script","name":"kalshi_art_survey","args":["--max-event-pages","60"],"id":"art-0"}
"""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Art markets, gated on RELIABLE signals only. The first cut used loose title keywords
# (lot/total/price/realize) and got contaminated by non-art markets — e.g. KXKBOTOTAL (Korean
# baseball game TOTALS) matched "total". Gate instead on a known art SERIES prefix OR a strict
# art category OR an unambiguous auction-house title token; weak tokens are dropped. A diagnostic
# dump of the distinct (series, category) actually matched makes any remaining miss/false-positive
# visible rather than silently folded into the counts.
ART_SERIES = re.compile(r"^KX(ART|AUCTION|SOTHEB|CHRISTIE|HAMMER|LOT|MASTERPIECE)", re.I)
ART_CATEGORY = re.compile(r"^art|fine art|auction", re.I)
ART_TITLE = re.compile(r"auction house|sothu?eby|christie|hammer price|evening sale|"
                       r"masterpiece|at auction", re.I)

# Market-type classifier (first match wins). Order matters: total before per-lot-price so a
# "total realized" market isn't caught by the generic price rule.
TYPE_RULES = [
    (re.compile(r"total|aggregate|sale\s+total|realized?\s+value|hammer\s+total|sum\b", re.I),
     "sale_total"),
    (re.compile(r"record|break.*record|highest|most expensive", re.I), "record_binary"),
    (re.compile(r"sell\s+for|sale\s+price|hammer|price\b|fetch|\$", re.I), "per_lot_price"),
    (re.compile(r"sell\b|sold|unsold|bought[- ]in|clear", re.I), "per_lot_sells"),
]


def art_type_of(text: str) -> str:
    for rx, name in TYPE_RULES:
        if rx.search(text or ""):
            return name
    return "other"


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _vol(m: dict) -> float:
    return xl._num(m.get("volume_fp")) or xl._num(m.get("volume")) or 0.0


def events(status: str, max_pages: int) -> list[dict]:
    out, cursor = [], ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status={status}&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        out.extend(evs)
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
        time.sleep(0.05)
    return out


def is_art(ev: dict, m: dict) -> bool:
    series = (m.get("series_ticker") or ev.get("series_ticker") or "")
    cat = ev.get("category") or ""
    text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"), m.get("title"),
                                  m.get("subtitle")]))
    return bool(ART_SERIES.search(series) or ART_CATEGORY.search(cat) or ART_TITLE.search(text))


def candles(series: str, ticker: str, start: int, end: int) -> list[dict]:
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=1")
    return (data or {}).get("candlesticks") or []


def intra_sale_activity(series: str, ticker: str, close_ts: float) -> tuple[int, int]:
    """(#candles with volume in the final 6h, total candles) around a settled market's close —
    a proxy for whether the market traded DURING the auction vs jumped once at settlement."""
    if not series or not ticker or not close_ts:
        return 0, 0
    start = int(close_ts) - 6 * 3600
    end = int(close_ts) + 600
    cs = candles(series, ticker, start, end)
    # a candlestick's `volume` is contracts traded in that 1-min interval; >0 means the market
    # actually traded that minute (a live tape) rather than sitting until a settlement jump.
    active = sum(1 for c in cs if xl._num(c.get("volume")) > 0)
    return active, len(cs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ART pre-stage enumeration (idea-model 2026-07-12)")
    ap.add_argument("--max-event-pages", type=int, default=60)
    ap.add_argument("--candle-samples", type=int, default=6, help="settled markets to probe for a tape")
    args = ap.parse_args(argv)

    print("ART pre-stage enumeration — is the ART hold family testable yet? (read-only survey)")

    settled_evs = events("settled", args.max_event_pages)
    open_evs = events("open", args.max_event_pages)
    print(f"\nscanned events: settled={len(settled_evs)} open={len(open_evs)}")

    # structure + capacity ----------------------------------------------------------
    settled: dict[str, dict] = defaultdict(lambda: {"n": 0, "vol": 0.0, "series": set(),
                                                     "samples": []})
    open_ct: dict[str, dict] = defaultdict(lambda: {"n": 0, "vol": 0.0, "series": set()})

    matched_series: dict[str, int] = defaultdict(int)  # diagnostic: what actually matched

    def ingest(evs: list[dict], bucket: dict, keep_samples: bool) -> None:
        for ev in evs:
            for m in ev.get("markets") or []:
                if not is_art(ev, m):
                    continue
                series = m.get("series_ticker") or ev.get("series_ticker") or ""
                cat = ev.get("category") or "?"
                matched_series[f"{series or '?'} [{cat}]"] += 1
                # classify on the human title/subtitle only — NOT the ticker (the ticker substring
                # "…TOTAL" is what mis-tagged KBO baseball totals as sale_total in the first cut).
                text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"),
                                              m.get("title"), m.get("subtitle")]))
                rec = bucket[art_type_of(text)]
                rec["n"] += 1
                rec["vol"] += _vol(m)
                if series:
                    rec["series"].add(series)
                if keep_samples and (m.get("result") or "").lower() in ("yes", "no") \
                        and m.get("close_time"):
                    rec["samples"].append((series, m.get("ticker") or "", _ts(m.get("close_time"))))

    ingest(settled_evs, settled, keep_samples=True)
    ingest(open_evs, open_ct, keep_samples=False)

    total_settled = sum(r["n"] for r in settled.values())
    total_open = sum(r["n"] for r in open_ct.values())
    print(f"\nART markets found (strict filter): settled={total_settled} open={total_open}")
    print("  matched (series [category] -> count) — verify these are genuinely art, not "
          "false positives:")
    for k, c in sorted(matched_series.items(), key=lambda kv: -kv[1])[:15]:
        print(f"    {c:5d}  {k}")
    if not total_settled and not total_open:
        print("\nVERDICT: HOLD (UNTESTABLE) — no art markets classified under the strict filter. "
              "Likely the fall auction season hasn't opened these yet (Jul–Sep is the auction "
              "trough); possibly the ART_SERIES prefixes need refreshing against live tickers. "
              "Not a kill — re-run near the Oct/Nov evening sales.")
        return 0

    print("\n== structure + capacity by market type ==")
    print(f"  {'type':16s} {'settled':>8s} {'open':>6s} {'settled_vol':>12s}  series")
    for t in sorted(set(settled) | set(open_ct), key=lambda k: -(settled[k]['n'] + open_ct[k]['n'])):
        sser = sorted(settled[t]['series'] | open_ct[t]['series'])[:3]
        print(f"  {t:16s} {settled[t]['n']:8d} {open_ct[t]['n']:6d} "
              f"{settled[t]['vol']:12.0f}  {sser}")

    # intra-sale activity (ARTSUM's load-bearing precondition) -----------------------
    print("\n== intra-sale tape probe (do markets trade DURING the auction?) ==")
    probed = 0
    tape_seen = 0
    # prioritize sale_total samples (ARTSUM's instrument), then any settled art market
    sample_pool = settled["sale_total"]["samples"] or [
        s for r in settled.values() for s in r["samples"]]
    for series, ticker, close_ts in sample_pool[:args.candle_samples]:
        active, total = intra_sale_activity(series, ticker, close_ts)
        probed += 1
        d = datetime.fromtimestamp(close_ts, tz=timezone.utc).strftime("%Y-%m-%d") if close_ts else "?"
        flag = "LIVE-TAPE" if active >= 3 else ("thin" if active else "settle-jump-only")
        if active >= 3:
            tape_seen += 1
        print(f"  {ticker:28s} close {d}  active-candles(final 6h) {active}/{total}  [{flag}]")
        time.sleep(0.05)
    if not probed:
        print("  (no settled art markets with a close_time to probe — capacity too thin to read a tape)")

    # verdict -----------------------------------------------------------------------
    has_total = (settled["sale_total"]["n"] + open_ct["sale_total"]["n"]) > 0
    total_settled_with_vol = settled["sale_total"]["n"] > 0 and settled["sale_total"]["vol"] > 0
    print("\n== testability verdict (pre-stage; a THESIS follows only if this promotes) ==")
    print(f"  sale-total markets exist:        {has_total}")
    print(f"  sale-total settled with volume:  {total_settled_with_vol}")
    print(f"  intra-sale live tape observed:   {tape_seen}/{probed} probed")
    if has_total and total_settled_with_vol and tape_seen:
        print("  VERDICT: PROMOTE-TO-THESIS — ARTSUM has an instrument (sale-total ladder), a "
              "settled track record with volume, AND a live intra-sale tape to pin against. "
              "Write the pre-registered ARTSUM thesis next.")
    else:
        missing = []
        if not has_total:
            missing.append("no sale-total instrument (only per-lot/record markets)")
        if has_total and not total_settled_with_vol:
            missing.append("sale-total markets exist but none settled with volume (seasonal/too new)")
        if not tape_seen:
            missing.append("no intra-sale live tape (markets settle in a jump, no partial-sum to pin)")
        print(f"  VERDICT: HOLD — not testable yet. Missing: {'; '.join(missing)}. "
              f"Re-run near the Oct/Nov evening sales; not a kill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
