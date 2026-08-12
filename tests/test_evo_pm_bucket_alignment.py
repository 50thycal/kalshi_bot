"""pm_divergence has to survive the two venues NOT using the same bucket grid.

Measured in production the day after the metric shipped: over a 30-minute window,
60 Kalshi weather buckets carried a fresh mid and 33 Polymarket buckets carried a
fresh price — and the exact `(city, kind, target_date, low_f, high_f)` join matched
FOUR of them. All four were LAX. The metric was not broken; it was inert.

The cause is not staleness and not a bad join. It is that each venue picks its own
2F grid and the grids interleave:

    AUS  Kalshi  <=98  99-100  101-102  103-104  105-106  107+
         Poly    <=91  92-93   94-95    96-97    98-99    100-101  ...

Kalshi's bucket boundaries fall at 98.5 / 100.5 / 102.5; Polymarket's at 97.5 /
99.5 / 101.5. The two sets are disjoint, so no Kalshi bucket in Austin can EVER
equal a Polymarket bucket, however fresh both feeds are. Same for Miami. LAX
matched only because Kalshi happened to start its ladder on an even degree there.

So the fix is to stop requiring the grids to agree. A Kalshi bucket is a range of
temperatures; Polymarket's ladder is a probability distribution over the same
temperatures. Re-bin one onto the other:

    P(Kalshi 99-100) = 0.5 * P(Poly 98-99) + 0.5 * P(Poly 100-101)

That is the standard overlap allocation for misaligned bins, and it carries the
standard assumption — mass is spread uniformly WITHIN a Polymarket bucket. The
assumption is wrong in the third decimal (a peaked distribution puts more mass on
the side nearer the mode), which is why the exact match is still preferred whenever
it exists and why the estimate is labelled.

Two properties this file pins down, both of which are ways the re-binning could
quietly lie:

  NO INVENTED MASS — if the Polymarket ladder does not fully cover the Kalshi
  bucket's range, the result is None, not a partial sum. A bucket half-covered by
  the ladder would otherwise report roughly half the probability it should, i.e. a
  large fake NEGATIVE divergence, which reads as "Kalshi is expensive" and is
  exactly the direction that makes an agent sell.

  NO GUESSING INSIDE A TAIL — an open-ended bucket (`107+`, `<=98`) has unbounded
  support, so uniform allocation is meaningless there. Any overlap with a tail
  bucket that is not an exact match fails closed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.evo import signals
from kalshi_bot.models import (
    PolymarketSnapshot,
    WeatherBucketSnapshot,
    WeatherForecast,
)

NOW = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)


# --- the pure re-binning function -------------------------------------------
# Ladder entries are (low_f, high_f, yes_prob); None means open-ended on that side.


AUS_POLY = [
    (None, 91.0, 0.02), (92.0, 93.0, 0.05), (94.0, 95.0, 0.10),
    (96.0, 97.0, 0.18), (98.0, 99.0, 0.25), (100.0, 101.0, 0.20),
    (102.0, 103.0, 0.12), (104.0, 105.0, 0.05), (106.0, None, 0.03),
]


def test_an_exact_bucket_match_is_used_as_is():
    """When the grids DO agree, nothing is estimated — this is the LAX case, and it
    must keep returning precisely the quoted probability."""
    got = signals.pm_probability_for_bucket(94.0, 95.0, AUS_POLY)
    assert got == (0.10, True)  # (probability, exact)


def test_an_interleaved_bucket_is_rebinned_from_its_overlaps():
    """The Austin case that motivated the fix: Kalshi 99-100 straddles Poly 98-99 and
    100-101, taking half of each."""
    prob, exact = signals.pm_probability_for_bucket(99.0, 100.0, AUS_POLY)
    assert exact is False
    assert abs(prob - (0.5 * 0.25 + 0.5 * 0.20)) < 1e-9  # 0.225


def test_rebinning_conserves_probability_across_the_whole_ladder():
    """A re-binned ladder must not create or destroy mass. Summing the Kalshi ladder
    over the region Polymarket covers reproduces Polymarket's own total there."""
    covered = [(92.0, 93.0), (94.0, 95.0), (96.0, 97.0), (98.0, 99.0),
               (100.0, 101.0), (102.0, 103.0), (104.0, 105.0)]
    poly_total = sum(p for lo, hi, p in AUS_POLY if lo is not None and hi is not None)
    # Kalshi's odd-start ladder over the same span, offset by one degree
    kalshi = [(93.0, 94.0), (95.0, 96.0), (97.0, 98.0), (99.0, 100.0),
              (101.0, 102.0), (103.0, 104.0)]
    got = sum(signals.pm_probability_for_bucket(lo, hi, AUS_POLY)[0] for lo, hi in kalshi)
    # six 2F buckets of the seven-bucket covered span => all but one bucket's worth
    assert 0.0 < got < poly_total
    # and each interior degree is counted exactly once: the offset ladder plus the
    # two half-buckets at the ends must recover the full covered mass
    ends = (signals.pm_probability_for_bucket(92.0, 92.0, AUS_POLY)[0]
            + signals.pm_probability_for_bucket(105.0, 105.0, AUS_POLY)[0])
    assert abs((got + ends) - poly_total) < 1e-9
    assert covered  # the span the assertion above is about


def test_a_bucket_the_ladder_does_not_cover_returns_nothing():
    """NO INVENTED MASS, with no tail involved so it is coverage being tested and
    nothing else: 90-91 sits entirely below a ladder that starts at 92."""
    bounded = [(lo, hi, p) for lo, hi, p in AUS_POLY if lo is not None and hi is not None]
    assert signals.pm_probability_for_bucket(90.0, 91.0, bounded) is None
    # ...and partial coverage is refused just as flatly as none at all
    assert signals.pm_probability_for_bucket(91.0, 92.0, bounded) is None


def test_overlapping_an_open_ended_tail_fails_closed():
    """NO GUESSING INSIDE A TAIL: 105-106 straddles the bounded 104-105 bucket and the
    unbounded 106+ tail, whose mass has no defined shape."""
    assert signals.pm_probability_for_bucket(105.0, 106.0, AUS_POLY) is None


def test_an_exactly_matching_tail_is_still_allowed():
    """The tail is only unusable when it has to be SPLIT. An exact match reads it off."""
    assert signals.pm_probability_for_bucket(106.0, None, AUS_POLY) == (0.03, True)


def test_a_gap_in_the_middle_of_the_ladder_fails_closed():
    ladder = [(92.0, 93.0, 0.30), (96.0, 97.0, 0.40)]  # 94-95 missing
    assert signals.pm_probability_for_bucket(93.0, 94.0, ladder) is None


def test_an_empty_ladder_is_not_a_zero_probability():
    assert signals.pm_probability_for_bucket(94.0, 95.0, []) is None


# --- end to end through compute_signals -------------------------------------


def _seed_interleaved(session, *, kalshi_low, kalshi_high, ticker):
    """Austin: Kalshi on odd starts, Polymarket on even. No bucket can match exactly."""
    session.add(WeatherForecast(
        captured_at=NOW - timedelta(minutes=5), city="AUS",
        event_ticker="KXHIGHAUS-26AUG12", target_date="2026-08-12", kind="high",
        forecast_high_f=100.0, source="nws",
    ))
    session.add(WeatherBucketSnapshot(
        captured_at=NOW - timedelta(minutes=1),
        event_ticker="KXHIGHAUS-26AUG12", market_ticker=ticker, city="AUS",
        kind="high", subtitle=f"{kalshi_low:.0f} to {kalshi_high:.0f}",
        low_f=kalshi_low, high_f=kalshi_high,
        yes_bid_cents=18.0, yes_ask_cents=22.0, mid_cents=20.0,
    ))
    for low, high, prob in AUS_POLY:
        session.add(PolymarketSnapshot(
            captured_at=NOW - timedelta(minutes=1), city="AUS", kind="high",
            target_date="2026-08-12", subtitle=f"{low}-{high}",
            low_f=low, high_f=high, yes_prob=prob,
        ))
    session.flush()


def test_the_austin_market_that_used_to_have_no_signal_now_has_one(
    evo_session, evo_settings
):
    """The production symptom, end to end: an interleaved Kalshi bucket produced no
    pm_divergence at all. Poly mass 0.225 -> 22.5c against a 20c Kalshi mid."""
    ticker = "KXHIGHAUS-26AUG12-B99.5"
    _seed_interleaved(evo_session, kalshi_low=99.0, kalshi_high=100.0, ticker=ticker)
    out = signals.compute_signals(
        evo_session, [ticker], now=NOW, settings=evo_settings)
    assert abs(out[ticker]["pm_divergence"] - 2.5) < 1e-6


def test_staleness_still_wins_over_the_new_matching(evo_session, evo_settings):
    """Re-binning must not become a way around FAIL CLOSED: a stale Polymarket ladder
    is dropped whether or not it would have matched."""
    ticker = "KXHIGHAUS-26AUG12-B99.5"
    session = evo_session
    session.add(WeatherForecast(
        captured_at=NOW - timedelta(minutes=5), city="AUS",
        event_ticker="KXHIGHAUS-26AUG12", target_date="2026-08-12", kind="high",
        forecast_high_f=100.0, source="nws",
    ))
    session.add(WeatherBucketSnapshot(
        captured_at=NOW - timedelta(minutes=1),
        event_ticker="KXHIGHAUS-26AUG12", market_ticker=ticker, city="AUS",
        kind="high", subtitle="99 to 100", low_f=99.0, high_f=100.0,
        yes_bid_cents=18.0, yes_ask_cents=22.0, mid_cents=20.0,
    ))
    stale = NOW - timedelta(minutes=evo_settings.signal_max_age_minutes + 5)
    for low, high, prob in AUS_POLY:
        session.add(PolymarketSnapshot(
            captured_at=stale, city="AUS", kind="high", target_date="2026-08-12",
            subtitle=f"{low}-{high}", low_f=low, high_f=high, yes_prob=prob,
        ))
    session.flush()
    out = signals.compute_signals(session, [ticker], now=NOW, settings=evo_settings)
    assert out.get(ticker, {}).get("pm_divergence") is None


def test_a_bucket_outside_the_ladder_still_has_no_signal(evo_session, evo_settings):
    """Coverage grew; it did not become total. A Kalshi bucket the ladder cannot
    cover must stay absent rather than acquire an estimated value."""
    ticker = "KXHIGHAUS-26AUG12-B89.5"
    _seed_interleaved(evo_session, kalshi_low=89.0, kalshi_high=90.0, ticker=ticker)
    out = signals.compute_signals(
        evo_session, [ticker], now=NOW, settings=evo_settings)
    assert out.get(ticker, {}).get("pm_divergence") is None
