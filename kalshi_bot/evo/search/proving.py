"""Deterministic synthetic corpora for the historical proving run.

The proving run has to answer mechanical questions — are documents content-addressed, do
replays reproduce, is a score explainable, do ledgers reconcile, is there look-ahead —
and every one of them is a question about the *machinery*, not about whether some
strategy makes money. Synthetic history answers them better than real
history does, because the right answer is known in advance and the adversarial cases can
be constructed rather than hoped for.

The corpus is registered into `evo.sandbox` under the `synthetic:` namespace, so it
replays through **the same loop** the real datasets use. A proving run against a second,
simpler engine would prove nothing about the engine that runs the real data.

Four adversarial profiles exist because the evaluator is supposed to tell them apart:

* `reckless`  — genuinely high total P&L, bought with a drawdown that would have ended
                the account. Must not outrank `steady`.
* `lucky`     — a huge per-trade number off a handful of trades. Must be held on thin
                evidence, not crowned.
* `steady`    — a moderate, consistent edge across the whole window. Should win.
* `broken`    — data that fails integrity. Must be classified invalid and reported as a
                data defect, not ranked badly and quietly dismissed as a bad strategy.

Every series is generated from a hash of its key, so the corpus is identical on every
machine and every run without shipping a fixture file.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from .. import sandbox
from ..marketdata import Quote

DATASET = "synthetic:proving"
PROVENANCE = "synthetic_deterministic_fixture"

#: The corpus window. Markets are generated across it; a candidate replaying a subwindow
#: sees only the markets whose close falls inside it.
CORPUS_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CORPUS_DAYS = 180

#: Series prefixes stand in for market families, so concentration and breadth are
#: measurable over the proving corpus.
SERIES = ("KXSYNTHA", "KXSYNTHB", "KXSYNTHC", "KXSYNTHD")

MARKETS_PER_DAY = 8
CANDLES_PER_MARKET = 12
TAPE_HOURS = 36


def _unit(*parts: object) -> float:
    """Deterministic pseudo-uniform in [0, 1) from a key — no RNG state anywhere."""
    key = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:7], "big") / float(1 << 56)


def _profile_for(series: str) -> str:
    """Which behaviour a series exhibits. Fixed by series so a genome's universe
    selects its profile — which is how the adversarial cases are constructed rather
    than hoped for."""
    return {
        "KXSYNTHA": "steady",
        "KXSYNTHB": "reckless",
        "KXSYNTHC": "lucky",
        "KXSYNTHD": "broken",
    }[series]


def _edge_for(profile: str, day: int, index: int) -> float:
    """The synthetic mispricing, in cents, that a well-aimed genome can capture.

    Positive means the YES side is cheap relative to how the market settles."""
    u = _unit(profile, day, index)
    if profile == "steady":
        # A solid, consistent edge — and it has to be genuinely solid, not merely
        # positive. Binary contracts pay 0 or 100, so a single trade's dispersion is
        # ~50c whatever the edge; at this corpus size an edge of ~10c sits only about
        # 1.4 standard errors above zero, and a lower-confidence-bound scorer is right
        # to be unimpressed by it. A profile named "steady" that scores as weak evidence
        # is misnamed, so the edge is set where the evidence is actually strong.
        return 20.0 + 6.0 * u
    if profile == "reckless":
        # Genuinely profitable overall — it must be able to top a raw-P&L ranking — but
        # it buys that with a long block of losses at the end of every 40-day period, so
        # the chronological drawdown is severe. This is the candidate the evaluator has
        # to rank BELOW `steady` despite the bigger headline number.
        #
        # The proving window is a whole number of 40-day periods, so it always contains
        # the drawdown. Tuned the other way the pathology could fall outside the window,
        # and whether the adversarial case held would depend on which dates the run
        # happened to draw — which would be measuring the window, not the evaluator.
        if (day % 40) >= 30:
            return -(48.0 + 10.0 * u)
        # The win phase is deliberately BIMODAL rather than uniformly strong. That is
        # what separates reckless from good: dispersion. A strategy whose wins are
        # uniform earns a tight confidence bound and deserves its high score; one that
        # earns the same mean through occasional large wins between mediocre trades has
        # the same P&L and far weaker evidence, and the bound should say so. Without
        # this, "reckless" is just a profitable strategy with a drawdown, and the
        # adversarial case tests nothing the edge component would not already reward.
        return (56.0 + 8.0 * u) if _unit("reckless-spike", day, index) < 0.90 else (4.0 * u)
    if profile == "lucky":
        # Very large edge, but the universe is so narrow that only a handful of
        # markets qualify (see `_qualifies`), so the sample stays tiny.
        return 30.0 + 20.0 * u
    return 1.0 * u  # broken: noise; its defect is in the data, not the edge


def _qualifies(profile: str, day: int, index: int) -> bool:
    """Whether this market exists at all for its profile.

    `lucky` is deliberately sparse: that is what makes it low-sample."""
    if profile == "lucky":
        return _unit("lucky-gate", day, index) < 0.06
    return True


def _markets(session, spec, date_from: str | None, date_to: str | None):
    """Adapter matching `evo.sandbox`'s contract: yields settled `_Market` objects.

    The date filter is applied to each market's **close** time, and a market is only
    yielded when its whole tape lies at or before `date_to`. That is the no-look-ahead
    guarantee the proving run asserts: a genome cannot see a market that had not settled
    by the declared cutoff."""
    start = _parse(date_from) or CORPUS_START
    end = _parse(date_to)
    prefixes = tuple(spec.universe.series_prefixes or ())

    for day in range(CORPUS_DAYS):
        day_start = CORPUS_START + timedelta(days=day)
        if day_start < start:
            continue
        for index in range(MARKETS_PER_DAY):
            series = SERIES[(day * MARKETS_PER_DAY + index) % len(SERIES)]
            if prefixes and not series.startswith(prefixes):
                continue
            profile = _profile_for(series)
            if not _qualifies(profile, day, index):
                continue

            close_at = day_start + timedelta(hours=20)
            if end is not None and close_at.date() > end.date():
                # Not settled by the cutoff — invisible, not merely excluded.
                continue

            market = _build_market(series, profile, day, index, close_at)
            if market is None:
                continue
            if spec.universe.max_spread_cents < 2:
                continue
            yield market


def _build_market(series: str, profile: str, day: int, index: int, close_at: datetime):
    ticker = f"{series}-{close_at:%y%b%d}".upper() + f"-{index}"
    edge = _edge_for(profile, day, index)

    # Settlement is what the edge predicts, with the residual noise that makes the
    # per-trade dispersion (and therefore the confidence bound) meaningful.
    settle_yes = _unit("settle", profile, day, index) < (0.5 + edge / 100.0)
    result = "yes" if settle_yes else "no"

    # The tape walks from an entry-friendly quote toward settlement.
    base = 50.0 - edge / 2.0
    candles = []
    for step in range(CANDLES_PER_MARKET):
        hours_before = TAPE_HOURS * (1.0 - step / (CANDLES_PER_MARKET - 1))
        ts = close_at - timedelta(hours=hours_before)
        drift = (100.0 if settle_yes else 0.0) - base
        progress = step / (CANDLES_PER_MARKET - 1)
        mid = base + drift * progress * 0.75
        mid = max(2.0, min(98.0, mid))
        spread = 2
        yes_bid = int(max(1, min(98, round(mid - spread / 2))))
        yes_ask = int(max(yes_bid + 1, min(99, round(mid + spread / 2))))

        if profile == "broken" and step == CANDLES_PER_MARKET // 2:
            # The integrity defect: a crossed book. A quote whose bid exceeds its ask
            # cannot be traded against, and a corpus containing it is not measuring what
            # it claims to. The proving run asserts this is *detected*, not tolerated.
            yes_bid, yes_ask = 80, 20

        # Same convention as the real adapters (`_quote_from_candle`): a tape quote is
        # `active` with an empty result — the realized outcome lives on `_Market.result`
        # and must not be visible to the entry rule — and `close_time` is translated to
        # wall-relative so the interpreter's hours-to-close gate, which compares against
        # the wall clock, sees the horizon the strategy would have seen live. No
        # look-ahead: `remaining` uses only this candle's own timestamp.
        remaining = close_at - ts
        quote = Quote(
            ticker=ticker,
            captured_at=ts,
            source="synthetic",
            status="active",
            result="",
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=100 - yes_ask,
            no_ask=100 - yes_bid,
            yes_levels=[(yes_bid, 500)],
            no_levels=[(100 - yes_ask, 500)],
            last_price=int(round(mid)),
            volume=5000,
            open_interest=5000,
            close_time=datetime.now(timezone.utc) + remaining,
            event_ticker=f"{series}-{close_at:%y%b%d}".upper(),
            category="synthetic",
            title=f"{series} proving market",
        )
        candles.append(
            sandbox._Candle(ts=ts, quote=quote, price_low=float(yes_bid - 1))
        )

    return sandbox._Market(
        ticker=ticker,
        result=result,
        month=f"{close_at:%Y-%m}",
        candles=candles,
    )


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_REGISTERED = False


def register() -> str:
    """Register the corpus with the shared replay engine. Idempotent."""
    global _REGISTERED
    if not _REGISTERED:
        sandbox.register_dataset(DATASET, _markets, provenance=PROVENANCE)
        _REGISTERED = True
    return DATASET


def window(day_from: int, day_to: int) -> tuple[str, str]:
    """An ISO window over the corpus, by day offset."""
    start = CORPUS_START + timedelta(days=day_from)
    end = CORPUS_START + timedelta(days=day_to)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


__all__ = [
    "CORPUS_DAYS",
    "CORPUS_START",
    "DATASET",
    "PROVENANCE",
    "SERIES",
    "register",
    "window",
]
