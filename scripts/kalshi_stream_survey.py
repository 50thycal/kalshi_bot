"""STREAMPIN pre-stage enumeration — is the streaming-count observation-pin TESTABLE yet?
(idea-model Area 1, 2026-07-21; see docs/STREAMPIN_CENSUS.md)

The idea-model Area-1 run flagged the ~2,550 KXARTISTSTREAMS music-streaming markets the ART
survey tripped over and discarded as the purest port of the one proven mechanic (weather
`con`/`obs`): they settle on slowly-published stream counts (weekly Luminate/Spotify charts),
so if the running count is estimable before it posts, you pin the winning side while the quote
lags. The research record is explicit that off-weather inattention did NOT generalize to
gas/CPI, so this is promoted only to a CENSUS, not a thesis — the same discipline as
`kalshi_art_survey.py` / `kalshi_seasonpin_census.py`.

This read-only survey answers the three testability questions that gate a thesis:

  1. STRUCTURE — what streaming markets exist, and of what type? An obs-pin needs a
     CUMULATIVE-count instrument (a threshold/ladder on "total streams by date X"), not a
     one-shot ranking ("#1 song this week"). Classify each into cumulative_count /
     ranking / milestone_binary / other.
  2. CAPACITY — how many have SETTLED (a track record needs settled history) and what volume do
     they trade? A handful of $200 markets is a hobby, not $100/mo.
  3. INTRA-WINDOW ACTIVITY (load-bearing) — do these markets TRADE during the counting window
     (a live tape to pin against), or jump once at settlement? Probe candlesticks around a few
     settled markets' close windows.

It also names the piece Kalshi data alone CANNOT settle — C3, whether a public stream proxy is
estimable *ahead of* the chart post. That is the thesis-stage external-data question; the census
only decides whether it is worth asking. Verdict: PROMOTE-TO-THESIS if cumulative-count markets
exist, have settled with volume, AND show intra-window trading; else HOLD with the specific
missing piece named. Read-only public Kalshi REST, stdlib only. Usage:
    {"type":"script","name":"kalshi_stream_survey","args":["--max-event-pages","60"],"id":"stream-0"}
"""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Gate on a known streaming SERIES prefix OR an unambiguous streaming/chart title token.
# Precision over recall; a matched (series [category]) dump makes contamination visible.
STREAM_SERIES = re.compile(r"^KX(ARTISTSTREAMS|STREAMS|SPOTIFY|BILLBOARD|CHART|SONG|ALBUM|"
                           r"GRAMMY|RIAA)", re.I)
STREAM_CATEGORY = re.compile(r"entertain|music", re.I)
STREAM_TITLE = re.compile(r"stream(ed|s|ing)?\b|spotify|monthly listeners|billboard|"
                          r"hot 100|number one|#1 (song|album)|go platinum|riaa|"
                          r"first[- ]week (sales|units)", re.I)

# Market-type classifier (first match wins): cumulative count before ranking so a
# "total streams" market isn't caught by a generic chart rule.
TYPE_RULES = [
    (re.compile(r"total\s+streams|cumulative|reach\s+\d|streams?\s+by|billion\s+streams|"
                r"\d\s*(m|b|million|billion)\s+streams|first[- ]week (units|sales)", re.I),
     "cumulative_count"),
    (re.compile(r"go (platinum|gold|diamond)|riaa|certif", re.I), "milestone_binary"),
    (re.compile(r"number one|#1|top\s+\d|hot 100|chart|debut|win.*(grammy|award)", re.I),
     "ranking"),
]


def stream_type_of(text: str) -> str:
    for rx, name in TYPE_RULES:
        if rx.search(text or ""):
            return name
    return "other"


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
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


def is_stream(ev: dict, m: dict) -> bool:
    series = m.get("series_ticker") or ev.get("series_ticker") or ""
    cat = ev.get("category") or ""
    text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"), m.get("title"),
                                  m.get("subtitle")]))
    # category alone is too broad (all of Entertainment); require a stream token with it.
    return bool(STREAM_SERIES.search(series)
                or STREAM_TITLE.search(text)
                or (STREAM_CATEGORY.search(cat) and STREAM_TITLE.search(text)))


def candles(series: str, ticker: str, start: int, end: int) -> list[dict]:
    data = xl._get(f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
                   f"?start_ts={start}&end_ts={end}&period_interval=1")
    return (data or {}).get("candlesticks") or []


def intra_window_activity(series: str, ticker: str, close_ts: float) -> tuple[int, int]:
    """(#candles with volume in the final 24h, total candles) around a settled market's close —
    a proxy for whether the market traded DURING the counting window vs jumped at settlement.
    Streaming windows are day/week-scale (unlike an auction's hours), so use a 24h look-back."""
    if not series or not ticker or not close_ts:
        return 0, 0
    start = int(close_ts) - 24 * 3600
    end = int(close_ts) + 600
    cs = candles(series, ticker, start, end)
    active = sum(1 for c in cs if xl._num(c.get("volume")) > 0)
    return active, len(cs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STREAMPIN pre-stage enumeration (idea-model Area 1)")
    ap.add_argument("--max-event-pages", type=int, default=60)
    ap.add_argument("--candle-samples", type=int, default=8,
                    help="settled markets to probe for an intra-window tape")
    args = ap.parse_args(argv)

    print("STREAMPIN pre-stage enumeration — is the streaming-count obs-pin testable yet? "
          "(read-only survey)")

    settled_evs = events("settled", args.max_event_pages)
    open_evs = events("open", args.max_event_pages)
    print(f"\nscanned events: settled={len(settled_evs)} open={len(open_evs)}")

    settled: dict[str, dict] = defaultdict(lambda: {"n": 0, "vol": 0.0, "series": set(),
                                                    "samples": []})
    open_ct: dict[str, dict] = defaultdict(lambda: {"n": 0, "vol": 0.0, "series": set()})
    matched_series: dict[str, int] = defaultdict(int)

    def ingest(evs: list[dict], bucket: dict, keep_samples: bool) -> None:
        for ev in evs:
            for m in ev.get("markets") or []:
                if not is_stream(ev, m):
                    continue
                series = m.get("series_ticker") or ev.get("series_ticker") or ""
                cat = ev.get("category") or "?"
                matched_series[f"{series or '?'} [{cat}]"] += 1
                text = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"),
                                              m.get("title"), m.get("subtitle")]))
                rec = bucket[stream_type_of(text)]
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
    print(f"\nstreaming markets found (strict filter): settled={total_settled} open={total_open}")
    print("  matched (series [category] -> count) — verify these are genuine streaming markets:")
    for k, c in sorted(matched_series.items(), key=lambda kv: -kv[1])[:15]:
        print(f"    {c:5d}  {k}")
    if not total_settled and not total_open:
        print("\nVERDICT: HOLD (UNTESTABLE) — no streaming markets classified under the strict "
              "filter. Likely the STREAM_SERIES prefixes need refreshing against live tickers "
              "(the ART survey saw KXARTISTSTREAMS as a false-match — confirm the live prefix). "
              "Not a kill.")
        return 0

    print("\n== structure + capacity by market type ==")
    print(f"  {'type':18s} {'settled':>8s} {'open':>6s} {'settled_vol':>12s}  series(sample)")
    for t in sorted(set(settled) | set(open_ct),
                    key=lambda k: -(settled[k]['n'] + open_ct[k]['n'])):
        sser = sorted(settled[t]['series'] | open_ct[t]['series'])[:3]
        print(f"  {t:18s} {settled[t]['n']:8d} {open_ct[t]['n']:6d} "
              f"{settled[t]['vol']:12.0f}  {sser}")

    # intra-window activity (STREAMPIN's load-bearing precondition) --------------------
    print("\n== intra-window tape probe (do markets trade DURING the counting window?) ==")
    probed = tape_seen = 0
    sample_pool = settled["cumulative_count"]["samples"] or [
        s for r in settled.values() for s in r["samples"]]
    for series, ticker, close_ts in sample_pool[:args.candle_samples]:
        active, total = intra_window_activity(series, ticker, close_ts)
        probed += 1
        d = datetime.fromtimestamp(close_ts, tz=timezone.utc).strftime("%Y-%m-%d") if close_ts else "?"
        flag = "LIVE-TAPE" if active >= 3 else ("thin" if active else "settle-jump-only")
        if active >= 3:
            tape_seen += 1
        print(f"  {ticker:30s} close {d}  active-candles(final 24h) {active}/{total}  [{flag}]")
        time.sleep(0.05)
    if not probed:
        print("  (no settled streaming markets with a close_time to probe — capacity too thin)")

    # verdict --------------------------------------------------------------------------
    has_cum = (settled["cumulative_count"]["n"] + open_ct["cumulative_count"]["n"]) > 0
    cum_settled_vol = settled["cumulative_count"]["n"] > 0 and settled["cumulative_count"]["vol"] > 0
    print("\n== testability verdict (pre-stage; a THESIS follows only if this promotes) ==")
    print(f"  cumulative-count instrument exists:  {has_cum}")
    print(f"  cumulative-count settled w/ volume:  {cum_settled_vol}")
    print(f"  intra-window live tape observed:     {tape_seen}/{probed} probed")
    print("  NOTE: C3 (a public stream proxy estimable AHEAD of the chart post) is the "
          "thesis-stage external-data question — Kalshi data alone cannot settle it; this "
          "census only decides whether C1/C2 make it worth asking.")
    if has_cum and cum_settled_vol and tape_seen:
        print("  VERDICT: PROMOTE-TO-THESIS — a cumulative-count instrument with settled volume "
              "AND an intra-window tape exists; write the pre-registered STREAMPIN obs-pin "
              "thesis next, whose first probe is the C3 external-signal feasibility test.")
    else:
        missing = []
        if not has_cum:
            missing.append("no cumulative-count instrument (only ranking/milestone markets — "
                           "nothing to pin a running total against)")
        if has_cum and not cum_settled_vol:
            missing.append("cumulative-count markets exist but none settled with volume (too new)")
        if not tape_seen:
            missing.append("no intra-window live tape (markets settle in a jump)")
        print(f"  VERDICT: HOLD — not testable yet. Missing: {'; '.join(missing)}. Not a kill.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
