"""mmsell LADDER PROBE — is a multi-outcome event's structure a usable entry signal?

Two of the "genuinely unexplored" mmsell ideas are about the SHAPE of the event a cheap tail
sits in, not about the tail's own price:

  * **Ladder overround** — for a mutually-exclusive multi-outcome event, sum every outcome's
    YES mid. A fair partition sums to 100. If it sums to 115, the field is collectively 15%
    overpriced, which is a DIRECT per-event measurement of the favorite-longshot bias mmsell
    harvests (rather than the static price band we use as a proxy for it). Trade only when
    overround exceeds a threshold.
  * **Outcome count** — a 2-way market and a 20-way ladder have very different longshot
    dynamics; nothing in the book conditions on it today.

Both ideas share one unstated premise: **that mmsell's candidates actually live in mutually
exclusive ladders.** They mostly do not. Kalshi's sports "ladders" are overwhelmingly NESTED
THRESHOLDS ("Over 6.5 runs", "Over 7.5 runs", "Over 8.5 runs") whose YES legs are a survival
function, not a partition — summing their mids yields a number near (n_markets / 2), which
carries no overround information whatsoever. Applying an overround threshold to those events
would be thresholding on strike count. So this probe measures **applicability before effect**:
how much of the candidate flow is even in a partition-shaped event, and whether overround has
any exploitable variance where it IS well defined.

Method (public read-only API, no auth — same event scan the mmsell tracker runs):
  1. Page open events with nested markets, skip the mmsell skip-list, rank by volume, cap.
  2. Classify each event's shape: PARTITION (mutually exclusive: strike buckets, exact-score,
     winner-of-field), THRESHOLD (nested over/under or spread ladder), BINARY (<=2 markets).
     Classification is structural, from the mutual-exclusivity flag when the payload carries
     one and from the strike geometry otherwise — see classify_event().
  3. For PARTITION events only, overround = sum of every market's YES mid (in %).
  4. Cross-tab against whether the event carries an mmsell CANDIDATE (a market whose yes mid
     is in the entry band AND whose yes-ask clears the maxyes ceiling) — i.e. the events the
     book would actually have traded.

Output answers, in order: what share of candidate flow the overround filter can even see; what
the overround distribution looks like where it is defined; and the outcome-count distribution
of the events we trade. Nothing here is a P&L claim — it is the feasibility read that decides
whether the expensive forward-collection + settlement study is worth building.

Usage:
    {"type": "script", "name": "mmsell_ladder_probe"}
    {"type": "script", "name": "mmsell_ladder_probe", "args": ["--top-events", "300"]}

classify_event() and overround() are pure functions unit-tested in
tests/test_mmsell_ladder_probe.py, independent of the network.
"""

from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser-UA fetch), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

# Mirrors Settings.mmsell_skip_series (parlays + weather have their own books).
SKIP_SERIES = ("KXMVE", "KXHIGH", "KXLOW")

# Payload keys that, when present and true, mark an event as a genuine partition. Kalshi has
# used more than one spelling across API versions; check all and treat absence as "unknown"
# rather than "false", so a renamed field degrades to the geometric fallback instead of
# silently reclassifying every event.
_MUTEX_KEYS = ("mutually_exclusive", "is_mutually_exclusive", "mutually_exclusive_markets")

# Substrings in a market's yes-subtitle that mark a NESTED THRESHOLD leg ("Over 6.5 runs").
# These sum to a survival function, not to 1, so overround is undefined for them.
_THRESHOLD_TOKENS = ("or more", "or above", "or fewer", "or less", "or below",
                     "over ", "under ", "at least", "at most", "above ", "below ",
                     "greater than", "less than", "+ ", "wins by")


# Quote field spellings seen across Kalshi API revisions. Cents fields come first; the
# `*_dollars` variants are scaled up by 100 so everything downstream is in cents. Reading only
# `yes_bid`/`yes_ask` is what made the first run of this probe classify every event as THIN.
_BID_KEYS = ("yes_bid", "yes_bid_cents", "yes_bid_dollars")
_ASK_KEYS = ("yes_ask", "yes_ask_cents", "yes_ask_dollars")


def _quote(market, keys) -> float | None:
    for k in keys:
        v = market.get(k)
        if v is None:
            continue
        v = xl._num(v)
        return v * 100.0 if k.endswith("_dollars") else v
    return None


def _mid(market) -> float | None:
    """YES midpoint in cents, or None when the market is not two-sided."""
    yb, ya = _quote(market, _BID_KEYS), _quote(market, _ASK_KEYS)
    if yb is None or ya is None:
        return None
    if yb <= 0 and ya <= 0:
        return None
    return (yb + ya) / 2.0


def _subtitle(market) -> str:
    return (market.get("yes_sub_title") or market.get("subtitle") or "").lower()


def classify_event(event) -> str:
    """PARTITION | THRESHOLD | BINARY | THIN — the event's outcome geometry.

    Only PARTITION events have a meaningful overround (their YES legs are exhaustive and
    mutually exclusive, so a fair book sums to 100). THRESHOLD ladders are nested survival
    legs; BINARY is a 2-way (overround is just the bid/ask spread); THIN is too few priced
    markets to judge.

    An explicit mutual-exclusivity flag from the payload wins when present. Absent one, the
    fallback is the subtitle geometry: a majority of legs reading like "Over 6.5" / "or more"
    is a threshold ladder, everything else with >=3 priced legs is treated as a partition.
    """
    markets = [m for m in (event.get("markets") or []) if _mid(m) is not None]
    if len(markets) < 2:
        return "THIN"
    if len(markets) == 2:
        return "BINARY"
    for key in _MUTEX_KEYS:
        if key in event and event.get(key) is not None:
            return "PARTITION" if bool(event.get(key)) else "THRESHOLD"
    thresholdish = sum(1 for m in markets
                       if any(tok in _subtitle(m) for tok in _THRESHOLD_TOKENS))
    return "THRESHOLD" if thresholdish * 2 >= len(markets) else "PARTITION"


def overround(event) -> float | None:
    """Sum of every priced market's YES mid, in percent. 100 = fair; >100 = the field is
    collectively overpriced (the favorite-longshot bias, measured directly). None when fewer
    than two markets are priced."""
    mids = [_mid(m) for m in (event.get("markets") or [])]
    mids = [m for m in mids if m is not None]
    if len(mids) < 2:
        return None
    return sum(mids)


def candidate_legs(event, lo: float, hi: float, maxyes: float) -> list[dict]:
    """The markets in this event that the mmsell10-regime entry would treat as candidates:
    YES mid inside the entry band AND the actual sell price (yes-ask) at or under the ceiling.
    Mirrors MmSellTracker.run_once's band + maxyes checks."""
    out = []
    for m in event.get("markets") or []:
        mid = _mid(m)
        if mid is None or not (lo <= mid <= hi):
            continue
        ya = _quote(m, _ASK_KEYS)
        if ya is None or ya <= 0 or ya > maxyes:
            continue
        out.append(m)
    return out


def fetch_open_events(top_events: int, min_volume: float, max_pages: int = 40) -> list[dict]:
    """Liquid open events, newest page first, ranked by total volume and capped — the same
    universe the mmsell entry scan walks."""
    scored: list[tuple[float, dict]] = []
    cursor = ""
    for _ in range(max_pages):
        page = xl._get(f"{KALSHI}/events?status=open&with_nested_markets=true"
                       f"&limit=200&cursor={cursor}")
        evs = (page or {}).get("events") or []
        for e in evs:
            series = (e.get("series_ticker") or "").upper()
            if any(series.startswith(p) for p in SKIP_SERIES):
                continue
            vol = sum(xl._num(m.get("volume")) or xl._num(m.get("volume_fp"))
                      for m in e.get("markets") or [])
            if vol < min_volume:
                continue
            scored.append((vol, e))
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not evs:
            break
    scored.sort(key=lambda se: -se[0])
    return [e for _v, e in scored[:top_events]]


def _pct(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return xs[i]


def _bucket_count(n: int) -> str:
    if n <= 2:
        return "a.2 (binary)"
    if n <= 4:
        return "b.3-4"
    if n <= 8:
        return "c.5-8"
    if n <= 15:
        return "d.9-15"
    return "e.16+"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top-events", type=int, default=150,
                    help="event scan cap, matching mmsell_top_events")
    ap.add_argument("--min-volume", type=float, default=100.0)
    ap.add_argument("--band", default="5,10", help="entry band lo,hi in yes-mid cents")
    ap.add_argument("--maxyes", type=float, default=7.0, help="mmsell10 entry-price ceiling")
    args = ap.parse_args(argv)
    lo, hi = (float(x) for x in args.band.split(","))

    events = fetch_open_events(args.top_events, args.min_volume)
    if not events:
        print("no open events returned (API unreachable or all filtered) — nothing to report")
        return 0

    # Does the payload even carry a mutual-exclusivity flag? Everything downstream depends on
    # whether classification is authoritative or a subtitle heuristic, so say so up front.
    flag_present = sorted({k for e in events for k in _MUTEX_KEYS if k in e})
    priced_total = sum(1 for e in events for m in (e.get("markets") or [])
                       if _mid(m) is not None)
    if priced_total == 0:
        # Every market unpriced means the quote keys moved again, not that the board is empty.
        # Dump the payload shape rather than reporting a confidently wrong "0% applicable".
        sample = next((m for e in events for m in (e.get("markets") or [])), None)
        print("NO PRICED MARKETS — the nested payload carries none of the expected quote keys.")
        print(f"  looked for bids {_BID_KEYS} / asks {_ASK_KEYS}")
        print(f"  market keys actually present: {sorted(sample) if sample else '(no markets)'}")
        if sample:
            print(f"  sample market: { {k: v for k, v in sample.items() if 'yes' in k or 'no' in k or 'price' in k} }")
        return 0
    print("=" * 78)
    print(f"MMSELL LADDER PROBE — {len(events)} open events "
          f"(band {lo:.0f}-{hi:.0f}c, maxyes {args.maxyes:.0f}c)")
    print(f"mutual-exclusivity flag in payload: "
          f"{', '.join(flag_present) if flag_present else 'ABSENT (subtitle geometry fallback)'}")
    print("=" * 78)

    shape_events: dict[str, int] = defaultdict(int)
    shape_cands: dict[str, int] = defaultdict(int)
    cnt_events: dict[str, int] = defaultdict(int)
    cnt_cands: dict[str, int] = defaultdict(int)
    ovr_by_shape: dict[str, list[float]] = defaultdict(list)
    ovr_partition_with: list[float] = []
    ovr_partition_without: list[float] = []
    by_series: dict[str, list] = defaultdict(list)

    for e in events:
        shape = classify_event(e)
        priced = [m for m in (e.get("markets") or []) if _mid(m) is not None]
        cands = candidate_legs(e, lo, hi, args.maxyes)
        ovr = overround(e)
        # series_ticker is not always populated on the nested-event payload; the event ticker's
        # first dash-segment is the same series prefix the tracker falls back to.
        series = (e.get("series_ticker")
                  or (e.get("event_ticker") or "").split("-")[0]).upper() or "?"

        shape_events[shape] += 1
        shape_cands[shape] += len(cands)
        cnt_events[_bucket_count(len(priced))] += 1
        cnt_cands[_bucket_count(len(priced))] += len(cands)
        if ovr is not None:
            ovr_by_shape[shape].append(ovr)
            if shape == "PARTITION":
                (ovr_partition_with if cands else ovr_partition_without).append(ovr)
                by_series[series].append((ovr, len(cands), len(priced)))

    total_cands = sum(shape_cands.values())
    print("\n1) EVENT SHAPE — where does the candidate flow actually live?")
    print(f"{'shape':<12} {'events':>7} {'candidates':>11} {'cand share':>11}  overround defined?")
    for shape in ("PARTITION", "THRESHOLD", "BINARY", "THIN"):
        share = (100.0 * shape_cands[shape] / total_cands) if total_cands else 0.0
        note = "YES — sums to 100 if fair" if shape == "PARTITION" else "no (meaningless sum)"
        print(f"{shape:<12} {shape_events[shape]:>7} {shape_cands[shape]:>11} "
              f"{share:>10.1f}%  {note}")
    print(f"{'TOTAL':<12} {len(events):>7} {total_cands:>11}")
    if total_cands:
        print(f"\n   >> APPLICABILITY: the overround filter can see "
              f"{100.0 * shape_cands['PARTITION'] / total_cands:.1f}% of candidate flow.")

    print("\n2) OUTCOME COUNT — priced markets per event (idea #6)")
    print(f"{'markets/event':<15} {'events':>7} {'candidates':>11} {'cand/event':>11}")
    for b in sorted(cnt_events):
        ce = cnt_events[b]
        print(f"{b:<15} {ce:>7} {cnt_cands[b]:>11} {cnt_cands[b] / ce:>11.2f}")

    print("\n3) OVERROUND DISTRIBUTION (sum of YES mids, %) — is there variance to trade?")
    print(f"{'shape':<12} {'n':>5} {'p10':>8} {'median':>8} {'p90':>8} {'spread p90-p10':>15}")
    for shape in ("PARTITION", "THRESHOLD", "BINARY"):
        xs = ovr_by_shape[shape]
        if len(xs) < 3:
            continue
        p10, med, p90 = _pct(xs, 0.10), statistics.median(xs), _pct(xs, 0.90)
        print(f"{shape:<12} {len(xs):>5} {p10:>8.1f} {med:>8.1f} {p90:>8.1f} {p90 - p10:>15.1f}")
    if len(ovr_partition_with) >= 3 and len(ovr_partition_without) >= 3:
        print(f"\n   partition events WITH an mmsell candidate:    n={len(ovr_partition_with):>4} "
              f"median overround {statistics.median(ovr_partition_with):.1f}")
        print(f"   partition events WITHOUT an mmsell candidate: n={len(ovr_partition_without):>4} "
              f"median overround {statistics.median(ovr_partition_without):.1f}")
        print("   (a candidate-bearing event that is NOT more overround than the rest means the "
              "\n    price band already captures whatever overround would have told us)")

    if by_series:
        print("\n4) PARTITION EVENTS BY SERIES — where a threshold could ever apply")
        print(f"{'series':<24} {'events':>7} {'median ovr':>11} {'p10':>8} {'p90':>8} "
              f"{'cands':>7} {'mkts/evt':>9}")
        rows = sorted(by_series.items(), key=lambda kv: -len(kv[1]))
        for series, vals in rows[:20]:
            if len(vals) < 3:
                continue
            ovrs = [v[0] for v in vals]
            print(f"{series:<24} {len(vals):>7} {statistics.median(ovrs):>11.1f} "
                  f"{_pct(ovrs, 0.10):>8.1f} {_pct(ovrs, 0.90):>8.1f} "
                  f"{sum(v[1] for v in vals):>7} "
                  f"{statistics.mean([v[2] for v in vals]):>9.1f}")

    print("\nREAD: idea #2 (overround) is only actionable on the PARTITION share of flow in (1). "
          "\nIf that share is small, or (3) shows partition overround has little spread, the "
          "\nfilter cannot move the book and the expensive settlement study is not worth building.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
