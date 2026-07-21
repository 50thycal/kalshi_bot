"""FEDRV — is Kalshi's Fed complex internally coherent, or is the rate-cut PATH mispriced?
(idea-model Area 1, 2026-07-21; see docs/FEDRV_THESIS.md)

Two Kalshi markets describe the same object from different angles:
  - KXFEDDECISION — per-MEETING decision (25/50 bps cut, hold, hike), one event per FOMC date.
  - KXRATECUTCOUNT — the AGGREGATE "number of rate cuts in 2026?" bucket ladder.

If retail prices the aggregate PATH inconsistently with the per-meeting decisions, there is a
STRUCTURAL relative-value trade (buy the cheap representation of the path, sell the rich one) —
and, unlike a forecasting edge, it needs NO external data and NO view on the Fed: it is pure
internal coherence, the same spirit as `kalshi_arb`'s monotonicity check but on the rate strip.

**Honest prior: LOW.** The live board shows KXRATECUTCOUNT at a 0.3c spread — the sharp money
is already on it — so the base case is "coherent / efficient," and this probe is mostly a cheap,
totally-uncorrelated RULING-OUT (ruling out is a win). This is Kalshi-public-API-only for v1;
the external Fed-funds-futures cross-check is a v2 only if v1 shows life.

The probe, in order:
  1. CHARACTERIZE — enumerate the KXFEDDECISION meetings (outcomes, prices, spread, volume) and
     the KXRATECUTCOUNT ladder (buckets, prices, spread, volume). Reports whether the data even
     supports a coherence check and how liquid each leg is.
  2. COHERENCE (best-effort) — map each upcoming meeting's decision prices to a per-meeting
     cuts distribution, convolve into a total-additional-cuts distribution, add cuts already
     realized at settled 2026 meetings, and compare to KXRATECUTCOUNT's direct distribution.
     Reports coverage (meetings mapped) + the divergence (total-variation + max per-bucket gap
     in cents) so a thin/unmappable read is visible rather than a bogus number.

Verdict: COHERENT (efficient — ruling-out) / INCOHERENT-BEYOND-COST (structural RV — promote) /
INSUFFICIENT-MAPPING (HOLD — the count/decision tickers need refreshing). Read-only public
Kalshi REST, stdlib only. Usage:
    {"type":"script","name":"fed_rv_study","args":["--max-event-pages","40"],"id":"fedrv-0"}
"""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

DECISION_SERIES = re.compile(r"^KXFED(DECISION|FUNDS|RATE)?", re.I)
CUTCOUNT_SERIES = re.compile(r"^KX(RATECUTCOUNT|RATECUTS|FEDCUTS|CUTS2026)", re.I)
YEAR = "2026"
MAX_CUTS = 6  # bucket 0..5 explicit, 6 = "6+"

# Map a decision market's subtitle to a number of 25bp cuts at that meeting.
_BPS = re.compile(r"(\d+)\s*\+?\s*(?:bps|basis)", re.I)
_DECR = re.compile(r"decrease|cut|lower|reduc", re.I)
_INCR = re.compile(r"increase|hike|raise", re.I)
_HOLD = re.compile(r"no change|hold|unchanged|maintain|leave", re.I)


def cuts_of(text: str) -> int | None:
    """25bp-cut equivalents implied by a decision subtitle. Hold/hike -> 0. None -> unparseable."""
    t = (text or "").lower()
    if _HOLD.search(t):
        return 0
    if _INCR.search(t) and not _DECR.search(t):
        return 0  # a hike is 0 cuts for a cut-COUNT market
    if _DECR.search(t):
        m = _BPS.search(t)
        if m:
            return max(1, round(int(m.group(1)) / 25.0))
        return 1  # "rate cut" with no bps stated -> assume 25bp
    return None


def bucket_count(text: str) -> int | None:
    """Integer cut count a KXRATECUTCOUNT bucket refers to (capped at MAX_CUTS = '6+')."""
    t = (text or "").lower()
    if re.search(r"\bno\b.*cut|zero|\b0\b", t) and "cut" in t:
        return 0
    m = re.search(r"(\d+)\s*\+?", t)
    if m:
        return min(int(m.group(1)), MAX_CUTS)
    return None


def yes_mid(m: dict) -> float | None:
    """Yes mid in dollars (0..1) from bid/ask, falling back to last price."""
    b = xl._num(m.get("yes_bid_dollars"))
    a = xl._num(m.get("yes_ask_dollars"))
    if a > 0 or b > 0:
        if a > 0 and b > 0:
            return (a + b) / 2.0
        return a or b
    lp = xl._num(m.get("last_price_dollars")) or xl._num(m.get("last_price"))
    return (lp / 100.0 if lp > 1 else lp) if lp else None


def spread_c(m: dict) -> float:
    b = xl._num(m.get("yes_bid_dollars"))
    a = xl._num(m.get("yes_ask_dollars"))
    return (a - b) * 100 if (a > 0 and b > 0) else 0.0


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


def _match(evs: list[dict], rx) -> list[dict]:
    out = []
    for ev in evs:
        st = ev.get("series_ticker") or ""
        mk = ev.get("markets") or []
        st2 = (mk[0].get("series_ticker") if mk else "") or ""
        if rx.search(st) or rx.search(st2):
            out.append(ev)
    return out


def normalized(markets: list[dict], key) -> dict[int, float]:
    """{integer bucket -> normalized yes-prob}, aggregating markets that map to the same bucket."""
    raw: dict[int, float] = defaultdict(float)
    for m in markets:
        k = key(" ".join(filter(None, [m.get("subtitle"), m.get("title"), m.get("yes_sub_title")])))
        p = yes_mid(m)
        if k is not None and p is not None and p > 0:
            raw[k] += p
    tot = sum(raw.values())
    return {k: v / tot for k, v in raw.items()} if tot > 0 else {}


def convolve(dists: list[dict[int, float]]) -> dict[int, float]:
    """Convolve independent per-meeting cut distributions into a total-cuts distribution."""
    total: dict[int, float] = {0: 1.0}
    for d in dists:
        if not d:
            continue
        nxt: dict[int, float] = defaultdict(float)
        for n, pn in total.items():
            for c, pc in d.items():
                nxt[min(n + c, MAX_CUTS)] += pn * pc
        total = dict(nxt)
    return total


def tv_distance(a: dict[int, float], b: dict[int, float]) -> float:
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FEDRV rate-path internal-coherence probe")
    ap.add_argument("--max-event-pages", type=int, default=40)
    ap.add_argument("--cost-cents", type=float, default=4.0,
                    help="per-bucket gap that would clear a round-trip and count as tradeable")
    args = ap.parse_args(argv)

    print("FEDRV — is Kalshi's Fed complex internally coherent, or is the cut PATH mispriced?")
    print("(read-only Kalshi public REST; Kalshi-internal coherence, no external futures in v1)\n")

    open_evs = events("open", args.max_event_pages)
    settled_evs = events("settled", args.max_event_pages)
    dec_open = _match(open_evs, DECISION_SERIES)
    dec_settled = _match(settled_evs, DECISION_SERIES)
    cnt_open = _match(open_evs, CUTCOUNT_SERIES)
    print(f"scanned: open={len(open_evs)} settled={len(settled_evs)}  ->  "
          f"decision events open={len(dec_open)} settled={len(dec_settled)}  "
          f"cut-count events open={len(cnt_open)}")

    # ---- 1. characterize --------------------------------------------------------------
    def is_2026(ev: dict) -> bool:
        blob = " ".join(filter(None, [ev.get("title"), ev.get("sub_title"),
                                      ev.get("event_ticker"), ev.get("series_ticker")]))
        return YEAR in blob

    dec_open_26 = [e for e in dec_open if is_2026(e)]
    dec_settled_26 = [e for e in dec_settled if is_2026(e)]
    print(f"\n== KXFEDDECISION meetings (2026) ==  upcoming={len(dec_open_26)} "
          f"decided={len(dec_settled_26)}")
    for ev in dec_open_26[:8]:
        mk = ev.get("markets") or []
        vol = sum(_vol(m) for m in mk)
        spr = sum(spread_c(m) for m in mk) / max(1, len(mk))
        dist = normalized(mk, cuts_of)
        mapped = sum(1 for m in mk if cuts_of(" ".join(filter(
            None, [m.get("subtitle"), m.get("title"), m.get("yes_sub_title")]))) is not None)
        print(f"  {ev.get('event_ticker', '?'):26s} mkts={len(mk):2d} mapped={mapped:2d} "
              f"vol={vol:9.0f} avg_spr={spr:4.1f}c  dist={ {k: round(v, 2) for k, v in sorted(dist.items())} }")

    cnt_ev = None
    for ev in cnt_open:
        if is_2026(ev):
            cnt_ev = ev
            break
    cnt_ev = cnt_ev or (cnt_open[0] if cnt_open else None)
    cnt_dist: dict[int, float] = {}
    if cnt_ev:
        mk = cnt_ev.get("markets") or []
        vol = sum(_vol(m) for m in mk)
        spr = sum(spread_c(m) for m in mk) / max(1, len(mk))
        cnt_dist = normalized(mk, bucket_count)
        print(f"\n== KXRATECUTCOUNT ladder ==  {cnt_ev.get('event_ticker', '?')} "
              f"mkts={len(mk)} vol={vol:.0f} avg_spr={spr:.1f}c")
        print(f"  direct distribution: { {k: round(v, 3) for k, v in sorted(cnt_dist.items())} }")
    else:
        print("\n== KXRATECUTCOUNT ladder ==  NOT FOUND (series prefix may need refreshing)")

    # ---- 2. coherence (best-effort) ---------------------------------------------------
    per_meeting = [normalized(ev.get("markets") or [], cuts_of) for ev in dec_open_26]
    per_meeting = [d for d in per_meeting if d]
    realized = 0
    for ev in dec_settled_26:
        for m in ev.get("markets") or []:
            if (m.get("result") or "").lower() == "yes":
                c = cuts_of(" ".join(filter(None, [m.get("subtitle"), m.get("title"),
                                                   m.get("yes_sub_title")])))
                if c:
                    realized += c
    coverage = len(per_meeting)
    print(f"\n== coherence check ==  mapped upcoming meetings={coverage} "
          f"realized cuts at decided 2026 meetings={realized}")

    if not per_meeting or not cnt_dist:
        print("  VERDICT: INSUFFICIENT-MAPPING (HOLD) — could not build BOTH a per-meeting "
              "convolution and a direct cut-count ladder. Refresh DECISION_SERIES / "
              "CUTCOUNT_SERIES prefixes and the bps/bucket parsers against the printed tickers "
              "above, then re-run. Not a kill.")
        return 0

    conv_add = convolve(per_meeting)
    conv_total: dict[int, float] = defaultdict(float)
    for n, p in conv_add.items():
        conv_total[min(n + realized, MAX_CUTS)] += p
    conv_total = dict(conv_total)

    print(f"  implied-from-decisions total-cuts: { {k: round(v, 3) for k, v in sorted(conv_total.items())} }")
    print(f"  direct cut-count ladder:           { {k: round(v, 3) for k, v in sorted(cnt_dist.items())} }")

    tv = tv_distance(conv_total, cnt_dist)
    gaps = {k: (conv_total.get(k, 0.0) - cnt_dist.get(k, 0.0)) * 100
            for k in set(conv_total) | set(cnt_dist)}
    max_gap_k = max(gaps, key=lambda k: abs(gaps[k]))
    max_gap = gaps[max_gap_k]
    print(f"  total-variation distance: {tv:.3f}   max per-bucket gap: {max_gap:+.1f}c "
          f"at {max_gap_k} cuts")

    # ---- verdict ----------------------------------------------------------------------
    print("\n== verdict (Kalshi-internal; external Fed-funds-futures cross-check is v2) ==")
    if abs(max_gap) >= args.cost_cents:
        print(f"  VERDICT: INCOHERENT-BEYOND-COST — the aggregate path disagrees with the "
              f"convolved per-meeting decisions by {max_gap:+.1f}c at {max_gap_k} cuts "
              f"(> {args.cost_cents:.0f}c cost). Potential structural RV; write the pre-registered "
              "RV probe (fillability + persistence). CAUTION: verify the bps/bucket mapping and "
              "the independence assumption before trusting the gap.")
    else:
        print(f"  VERDICT: COHERENT / EFFICIENT — max per-bucket gap {max_gap:+.1f}c is within "
              f"cost ({args.cost_cents:.0f}c); the path is priced consistently with the "
              "per-meeting decisions (as the 0.3c KXRATECUTCOUNT spread predicted). Clean "
              "ruling-out — log rates as internally efficient and close unless the futures "
              "cross-check (v2) later shows a Kalshi-vs-futures lag.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
