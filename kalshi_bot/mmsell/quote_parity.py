"""QUOTE PARITY — does the event page's inline quote agree with the orderbook's?

WHY THIS EXISTS
---------------
The mmsell entry scan fetches ONE orderbook per market that clears the volume + hours-to-close
gates: measured 2026-08-10/11, that is ~650-1,600 `GET /markets/{t}/orderbook` calls per cycle,
burst-rate ~6-25 requests/sec against a Kalshi read bucket whose Basic tier allows 20/sec. It is
the single reason the scan is capped at the top 150 events by volume, and therefore the reason
~1,700 eligible events go unscanned every cycle.

But `GET /events?with_nested_markets=true` — the ONE call the scan already makes to enumerate
the universe — returns a top-of-book quote on every nested market. Verified live 2026-08-11:

    {'ticker': 'KXELONMARS-99', 'vol': 116844, 'bid$': '0.1000', 'ask$': '0.1100'}

The mmsell band gate needs only the midpoint and the cheap-side ask. If the inline quote agrees
with the orderbook, the scan can PRE-FILTER on it for free and fetch orderbooks only for markets
that are actually in band (~220-450/cycle) — a 3-7x reduction that turns the rate limit from the
binding constraint into a non-issue, and makes a wider (A/B union) scan cost LESS than today.

WHY IT IS AN EXPERIMENT AND NOT AN ASSUMPTION
---------------------------------------------
Two ways the two quotes can legitimately differ:

  1. STALENESS. The events endpoint may serve a denormalized or cached quote, refreshed on its
     own schedule. The orderbook is fetched live, seconds later.
  2. DERIVATION. Our `best_yes_ask` is computed as `100 - best_no_bid` off the orderbook's NO
     side. Kalshi's inline `yes_ask` should equal that by construction, but need not on a
     one-sided book, and the two can round differently (`_dollars` strings -> cents).

This matters MORE than the rate limit, because the failure is silent: a pre-filter that rejects
a market never fetches its orderbook, so a false negative is a candidate that simply stops
existing. Every mmsell book — including the live one — would quietly see a different candidate
stream, which is exactly the contamination the A/B scan exists to prevent.

WHAT IS MEASURED
----------------
For every market where the scan ALREADY fetched an orderbook (so we have ground truth), the
inline quote is scored against it, and the whole cycle's totals go to `system_events` as
`component='mmsell_quote_parity'`. Two kinds of number:

  * AGREEMENT — bucketed |inline - orderbook| on the bid, the ask and the midpoint. Diagnostic.
  * THE DECISION TABLE — the number that actually decides this. For each band and each margin
    m, how many orderbooks a pre-filter of `band widened by m` would fetch, and how many real
    in-band candidates it would MISS. A margin large enough to make misses zero, while still
    fetching far fewer orderbooks than today, is the whole result.

Deliberate limitation, stated because it bounds the conclusion: the population is markets the
scan already fetches (volume + htc gates passed). We have no orderbook truth for anything else,
so this can validate a pre-filter applied at that point in the funnel and nothing wider.

Pure and dependency-free: no I/O, no DB, no clock. See docs/MMSELL_QUOTE_PARITY.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Margins (cents) the decision table is evaluated at. A pre-filter admits a market when its
# INLINE midpoint sits inside [lo - m, hi + m] (and its inline ask under maxyes + m), so a
# larger margin trades extra orderbook fetches for fewer missed candidates.
MARGINS: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 5.0)

# Buckets for |inline - orderbook|, in cents. Order matters (first match wins).
_BUCKETS: tuple[tuple[str, float], ...] = (
    ("0", 0.0), ("1", 1.0), ("2", 2.0), ("3-5", 5.0), ("6-10", 10.0),
)
_BUCKET_OVER = ">10"
BUCKET_KEYS: tuple[str, ...] = tuple(k for k, _ in _BUCKETS) + (_BUCKET_OVER,)


def _bucket(delta: float) -> str:
    for key, hi in _BUCKETS:
        if delta <= hi:
            return key
    return _BUCKET_OVER


# A disagreement this large is not rounding — it is a different market state, and it is what
# drives the pre-filter's irreducible ~1% miss floor. 0.6% of quotes land here and one was 90c
# off. Characterizing them is the difference between "a lossy pre-filter is a judgement call"
# and "exclude this class and the loss nearly vanishes".
OUTLIER_CENTS = 5.0
# Per cycle. The accumulator must stay a fixed-size summary — a scan sees thousands of markets
# and this rides in a JSON column — so outliers are SAMPLED, not enumerated. 25/cycle across
# ~48 cycles/day is ample to test whether they cluster, and the aggregate counts below stay
# exact regardless of what the sample caught.
MAX_OUTLIER_SAMPLES = 25


def admits(mid: float | None, ask: float | None, lo: float, hi: float,
           maxyes: float | None, margin: float = 0.0) -> bool:
    """The mmsell band decision, widened by `margin`.

    Mirrors the entry scan exactly (tracker.run_once): the BAND gates the midpoint, and
    `maxyes` caps the cheap side's actual entry price, which for the ordinary cheap-YES leg is
    `100 - no_bid` — i.e. the yes-ask. A missing quote is not a decision: it returns False, and
    the caller counts it separately so "no data" never masquerades as "out of band"."""
    if mid is None:
        return False
    if not (lo - margin <= mid <= hi + margin):
        return False
    if maxyes is not None:
        if ask is None:
            return False
        if ask > maxyes + margin:
            return False
    return True


def excursion(mid: float | None, ask: float | None, lo: float, hi: float,
              maxyes: float | None) -> float:
    """How far outside the band this quote sits, in cents — 0.0 when inside.

    Applied to the INLINE quote of a market the ORDERBOOK admits, this is the margin a
    pre-filter would have needed to keep that candidate. The max over all such markets is the
    smallest safe margin, which is the number the gate in docs/MMSELL_QUOTE_PARITY.md turns on.
    A missing quote has no finite excursion — no margin recovers it — so it reports `inf` and
    is reported separately from the margin curve."""
    if mid is None:
        return float("inf")
    out = 0.0
    if mid < lo:
        out = lo - mid
    elif mid > hi:
        out = mid - hi
    if maxyes is not None:
        if ask is None:
            return float("inf")
        out = max(out, ask - maxyes)
    return max(0.0, out)


@dataclass
class BandProbe:
    """One band the decision table is evaluated for.

    Bands are FIXED constants rather than reads of live book config on purpose: the experiment
    has to mean the same thing across the days it accumulates over, and a book whose knobs are
    retuned mid-run would silently redefine its own result."""

    name: str
    lo: float
    hi: float
    maxyes: float | None = None


@dataclass
class BandResult:
    ob_in: int = 0                  # orderbook (ground truth) says in band
    # per margin: how many orderbooks a pre-filter would fetch, and how many truths it misses
    fetch_at: dict[str, int] = field(default_factory=dict)
    miss_at: dict[str, int] = field(default_factory=dict)
    # in-band truths the inline quote could never recover, at ANY margin (quote absent)
    miss_no_quote: int = 0
    # largest finite excursion over in-band truths = the smallest margin that misses nothing
    worst_margin: float = 0.0

    def as_dict(self) -> dict:
        return {"ob_in": self.ob_in, "fetch_at": dict(self.fetch_at),
                "miss_at": dict(self.miss_at), "miss_no_quote": self.miss_no_quote,
                "worst_margin": round(self.worst_margin, 2)}


@dataclass
class QuoteParityAccumulator:
    """Accumulates one cycle's inline-vs-orderbook comparison. `observe` is called once per
    market the scan fetched an orderbook for; `as_dict` is persisted to system_events.

    Every field is a plain int/float/str so the whole thing round-trips through JSON, and the
    accumulator holds no per-market rows — a scan sees thousands of markets a cycle and this
    must not grow with them."""

    bands: tuple[BandProbe, ...]
    compared: int = 0
    inline_missing: int = 0     # orderbook two-sided but the inline quote is absent/one-sided
    ob_not_two_sided: int = 0   # no ground truth to score against; excluded from everything else
    bid_hist: dict[str, int] = field(default_factory=dict)
    ask_hist: dict[str, int] = field(default_factory=dict)
    mid_hist: dict[str, int] = field(default_factory=dict)
    max_mid_delta: float = 0.0
    max_ask_delta: float = 0.0
    results: dict[str, BandResult] = field(default_factory=dict)
    # Large-disagreement characterization. `outliers` is the exact COUNT (never sampled, so the
    # rate stays honest); `outlier_samples` is a bounded sample carrying the market attributes a
    # class could hide in — series, liquidity, time to close, depth at the touch.
    outliers: int = 0
    outlier_samples: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        for b in self.bands:
            r = self.results.setdefault(b.name, BandResult())
            for m in MARGINS:
                r.fetch_at.setdefault(_mkey(m), 0)
                r.miss_at.setdefault(_mkey(m), 0)

    def observe(self, *, ob_bid: float | None, ob_ask: float | None,
                inline_bid: float | None, inline_ask: float | None,
                context: dict | None = None) -> None:
        """`context` carries the market attributes recorded ONLY when this market turns out to
        be a large-disagreement outlier. Optional so the pure arithmetic stays testable without
        it, and evaluated lazily — a scan calls this thousands of times a cycle and must not pay
        for a dict it will discard 99.4% of the time."""
        ob_mid = _mid(ob_bid, ob_ask)
        if ob_mid is None:
            # The scan itself skips these (`skipped_illiquid`) — with no ground truth there is
            # nothing to validate a pre-filter against, so they stay out of every denominator.
            self.ob_not_two_sided += 1
            return

        self.compared += 1
        inline_mid = _mid(inline_bid, inline_ask)
        if inline_mid is None:
            self.inline_missing += 1

        if inline_bid is not None and ob_bid is not None:
            _bump(self.bid_hist, _bucket(abs(inline_bid - ob_bid)))
        if inline_ask is not None and ob_ask is not None:
            d = abs(inline_ask - ob_ask)
            _bump(self.ask_hist, _bucket(d))
            self.max_ask_delta = max(self.max_ask_delta, d)
        d_mid = None
        if inline_mid is not None:
            d_mid = abs(inline_mid - ob_mid)
            _bump(self.mid_hist, _bucket(d_mid))
            self.max_mid_delta = max(self.max_mid_delta, d_mid)

        d_ask = (abs(inline_ask - ob_ask)
                 if inline_ask is not None and ob_ask is not None else None)
        if (d_mid is not None and d_mid > OUTLIER_CENTS) or \
                (d_ask is not None and d_ask > OUTLIER_CENTS):
            self.outliers += 1
            if len(self.outlier_samples) < MAX_OUTLIER_SAMPLES:
                sample = {"d_mid": round(d_mid, 1) if d_mid is not None else None,
                          "d_ask": round(d_ask, 1) if d_ask is not None else None,
                          "ob_bid": ob_bid, "ob_ask": ob_ask,
                          "in_bid": inline_bid, "in_ask": inline_ask}
                if context:
                    # Clamped: a diagnostic must never be the thing that bloats a JSON column.
                    sample.update({k: v for k, v in context.items() if v is not None})
                self.outlier_samples.append(sample)

        for b in self.bands:
            res = self.results[b.name]
            truth = admits(ob_mid, ob_ask, b.lo, b.hi, b.maxyes)
            if truth:
                res.ob_in += 1
                exc = excursion(inline_mid, inline_ask, b.lo, b.hi, b.maxyes)
                if exc == float("inf"):
                    res.miss_no_quote += 1
                else:
                    res.worst_margin = max(res.worst_margin, exc)
            for m in MARGINS:
                key = _mkey(m)
                if admits(inline_mid, inline_ask, b.lo, b.hi, b.maxyes, margin=m):
                    res.fetch_at[key] += 1
                elif truth:
                    res.miss_at[key] += 1

    def as_dict(self) -> dict:
        return {
            "compared": self.compared,
            "inline_missing": self.inline_missing,
            "ob_not_two_sided": self.ob_not_two_sided,
            "bid_hist": dict(self.bid_hist),
            "ask_hist": dict(self.ask_hist),
            "mid_hist": dict(self.mid_hist),
            "max_mid_delta": round(self.max_mid_delta, 2),
            "max_ask_delta": round(self.max_ask_delta, 2),
            "margins": [_mkey(m) for m in MARGINS],
            "bands": {name: r.as_dict() for name, r in self.results.items()},
            "outliers": self.outliers,
            "outlier_cents": OUTLIER_CENTS,
            "outlier_samples": self.outlier_samples,
        }


def _mkey(m: float) -> str:
    return f"{m:g}"


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _bump(d: dict[str, int], key: str) -> None:
    d[key] = d.get(key, 0) + 1
