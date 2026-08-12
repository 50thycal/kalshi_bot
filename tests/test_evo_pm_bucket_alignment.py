"""pm_divergence has to survive the two venues NOT using the same bucket grid —
without inventing a disagreement where there is none.

Measured in production the day the metric shipped: over a 30-minute window, 60 Kalshi
weather buckets carried a fresh mid and 33 Polymarket buckets carried a fresh price,
and the exact `(city, kind, target_date, low_f, high_f)` join matched FOUR of them,
all LAX. The metric was not broken; it was inert. The cause is that each venue picks
its own 2F grid and the grids interleave:

    AUS  Kalshi  <=98  99-100  101-102  103-104  105-106  107+
         Poly    <=91  92-93   94-95    96-97    98-99    100-101  ...

Kalshi's boundaries fall at 98.5 / 100.5 / 102.5, Polymarket's at 97.5 / 99.5 / 101.5.
Disjoint sets, so no Austin bucket can ever equal a Polymarket bucket.

THE FIX THAT DOES NOT WORK, and why this file is mostly about refusing to answer:
re-binning by overlap, i.e. assuming mass is spread uniformly inside each Polymarket
bucket, so `P(Kalshi 99-100) = .5*P(Poly 98-99) + .5*P(Poly 100-101)`. That shipped,
and against the real Austin ladder it produced a +32c divergence on one bucket and
-37c on its neighbour between two venues that in fact agreed. Weather distributions
are extremely peaked — one or two degrees hold nearly all the mass — so halving a
bucket is not a small approximation. Polymarket had 72.5% on `100-101` while Kalshi
had 84% on `101-102`: essentially all of that 72.5% is P(101), and splitting it evenly
fabricates the disagreement. Worse, it fabricates it in the direction that makes an
agent trade.

So the contract is BOUNDS, not a point estimate:

    lower = mass of Polymarket buckets wholly inside the Kalshi bucket
    upper = lower + mass of every bucket that merely overlaps it

and an answer is returned only when that range is narrow enough to be worth less than
the gates agents write. The two properties below are what this file pins down.

  A WIDE RANGE IS NOT A SIGNAL — when straddling buckets hold real mass, the ladder
  does not determine the answer and the result is None. This is the property that the
  uniform-allocation version violated, and it is the one that matters, because the
  cases it suppresses are exactly the headline-looking ones.

  A NARROW RANGE STILL IS — when the straddling buckets are nearly empty (the common
  tail case) the midpoint is accurate to within half the range, so coverage does grow
  past the exact-match-only baseline rather than collapsing back to it.
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

# The real Austin ladder, read out of production on 2026-08-12. Peaked: 100-101 holds
# 72.5% and 102-103 holds 22%, everything else is rounding.
AUS_POLY_LIVE = [
    (None, 91.0, 0.0005), (92.0, 93.0, 0.0005), (94.0, 95.0, 0.0005),
    (96.0, 97.0, 0.0005), (98.0, 99.0, 0.04), (100.0, 101.0, 0.725),
    (102.0, 103.0, 0.22), (104.0, 105.0, 0.0085), (106.0, 107.0, 0.0035),
    (108.0, 109.0, 0.001), (110.0, None, 0.0005),
]

# A flat, synthetic ladder for exercising mechanics that peakedness would obscure.
FLAT_POLY = [
    (None, 91.0, 0.02), (92.0, 93.0, 0.05), (94.0, 95.0, 0.10),
    (96.0, 97.0, 0.18), (98.0, 99.0, 0.25), (100.0, 101.0, 0.20),
    (102.0, 103.0, 0.12), (104.0, 105.0, 0.05), (106.0, None, 0.03),
]


# --- the property the shipped version got wrong ------------------------------


def test_the_austin_artifact_that_uniform_allocation_produced_is_suppressed():
    """THE regression. Uniform allocation said Kalshi's 101-102 was worth 47.2c against
    an 84c mid — a fabricated -37c divergence. Polymarket's 100-101 (72.5%) straddles
    the bucket, so its mass could all be P(101) or all be P(100); the ladder simply
    does not say. The only honest answer is no answer."""
    assert signals.pm_probability_for_bucket(101.0, 102.0, AUS_POLY_LIVE) is None
    # ...and its neighbour, which uniform allocation scored at +32c against a 6c mid
    assert signals.pm_probability_for_bucket(99.0, 100.0, AUS_POLY_LIVE) is None


def test_a_straddling_bucket_holding_real_mass_blocks_the_answer():
    """The general form: 0.20 of ambiguous mass is far more than the 5c gates agents
    write, so no midpoint drawn from it can be trusted."""
    assert signals.pm_probability_for_bucket(99.0, 100.0, FLAT_POLY) is None


def test_the_uncertainty_band_is_what_decides_it_not_the_bucket_count():
    """Two straddling buckets are fine if they are nearly empty, and one is too many if
    it is not — the threshold is on ambiguous MASS, not on how many buckets overlap."""
    tight = [(96.0, 97.0, 0.001), (98.0, 99.0, 0.002), (100.0, 101.0, 0.001)]
    # 99-100 straddles 98-99 and 100-101: 0.003 ambiguous, well inside the band
    got = signals.pm_probability_for_bucket(99.0, 100.0, tight)
    assert got is not None and got[1] is False
    heavy = [(96.0, 97.0, 0.001), (98.0, 99.0, 0.30), (100.0, 101.0, 0.001)]
    assert signals.pm_probability_for_bucket(99.0, 100.0, heavy) is None


# --- but coverage does still grow --------------------------------------------


def test_a_bucket_in_the_quiet_tail_is_answerable():
    """A NARROW RANGE STILL IS a signal. Austin's 105-106 straddles 104-105 (0.85%) and
    106-107 (0.35%): 1.2% of ambiguous mass, so the midpoint is off by at most 0.6c."""
    got = signals.pm_probability_for_bucket(105.0, 106.0, AUS_POLY_LIVE)
    assert got is not None
    prob, exact = got
    assert exact is False
    assert abs(prob - (0.0085 + 0.0035) / 2.0) < 1e-9


def test_the_returned_value_is_the_midpoint_of_the_honest_range():
    """Contained mass counts fully; straddling mass counts half, because half the range
    is the point that minimises worst-case error over it."""
    ladder = [(98.0, 99.0, 0.01), (100.0, 101.0, 0.02), (102.0, 103.0, 0.01)]
    # 99-102 spans [98.5, 102.5]: 100-101 sits wholly inside, the other two straddle
    got = signals.pm_probability_for_bucket(99.0, 102.0, ladder)
    assert got is not None
    assert abs(got[0] - (0.02 + (0.01 + 0.01) / 2.0)) < 1e-9


def test_an_exact_match_is_used_verbatim_and_never_bounded():
    """When the grids DO agree nothing is estimated — the LAX case, which must keep
    returning precisely the quoted probability however peaked the ladder is."""
    assert signals.pm_probability_for_bucket(100.0, 101.0, AUS_POLY_LIVE) == (0.725, True)
    assert signals.pm_probability_for_bucket(94.0, 95.0, FLAT_POLY) == (0.10, True)


# --- the fail-closed paths ---------------------------------------------------


def test_a_bucket_the_ladder_does_not_cover_returns_nothing():
    """NO INVENTED MASS, with no tail involved so coverage is what is being tested:
    90-91 sits entirely below a ladder that starts at 92."""
    bounded = [(lo, hi, p) for lo, hi, p in FLAT_POLY if lo is not None and hi is not None]
    assert signals.pm_probability_for_bucket(90.0, 91.0, bounded) is None
    # ...and partial coverage is refused just as flatly as none at all
    assert signals.pm_probability_for_bucket(91.0, 92.0, bounded) is None


def test_overlapping_an_open_ended_tail_fails_closed():
    """An unbounded tail cannot be bounded, so it cannot be split — whatever its mass."""
    assert signals.pm_probability_for_bucket(105.0, 106.0, FLAT_POLY) is None


def test_an_exactly_matching_tail_is_still_allowed():
    """The tail is only unusable when it has to be SPLIT. An exact match reads it off."""
    assert signals.pm_probability_for_bucket(106.0, None, FLAT_POLY) == (0.03, True)


def test_a_gap_in_the_middle_of_the_ladder_fails_closed():
    ladder = [(92.0, 93.0, 0.30), (96.0, 97.0, 0.40)]  # 94-95 missing
    assert signals.pm_probability_for_bucket(93.0, 94.0, ladder) is None


def test_an_empty_ladder_is_not_a_zero_probability():
    assert signals.pm_probability_for_bucket(94.0, 95.0, []) is None


# --- end to end through compute_signals --------------------------------------


def _seed(session, *, kalshi_low, kalshi_high, ticker, ladder, pm_age_min=1):
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
        yes_bid_cents=0.0, yes_ask_cents=1.0, mid_cents=0.5,
    ))
    for low, high, prob in ladder:
        session.add(PolymarketSnapshot(
            captured_at=NOW - timedelta(minutes=pm_age_min), city="AUS", kind="high",
            target_date="2026-08-12", subtitle=f"{low}-{high}",
            low_f=low, high_f=high, yes_prob=prob,
        ))
    session.flush()


def test_the_live_austin_ladder_produces_no_signal_on_the_contested_bucket(
    evo_session, evo_settings
):
    """End to end on the real data: the bucket uniform allocation would have scored at
    -37c yields no metric at all, so no condition on it can fire."""
    ticker = "KXHIGHAUS-26AUG12-B101.5"
    _seed(evo_session, kalshi_low=101.0, kalshi_high=102.0, ticker=ticker,
          ladder=AUS_POLY_LIVE)
    out = signals.compute_signals(
        evo_session, [ticker], now=NOW, settings=evo_settings)
    assert out.get(ticker, {}).get("pm_divergence") is None


def test_the_quiet_tail_bucket_does_produce_a_signal(evo_session, evo_settings):
    """Coverage past exact-match-only is real, not theoretical: 0.6c of Polymarket
    probability against a 0.5c Kalshi mid."""
    ticker = "KXHIGHAUS-26AUG12-B105.5"
    _seed(evo_session, kalshi_low=105.0, kalshi_high=106.0, ticker=ticker,
          ladder=AUS_POLY_LIVE)
    out = signals.compute_signals(
        evo_session, [ticker], now=NOW, settings=evo_settings)
    assert abs(out[ticker]["pm_divergence"] - 0.1) < 1e-6


def test_staleness_still_wins_over_the_new_matching(evo_session, evo_settings):
    """Bounding must not become a way around FAIL CLOSED: a stale ladder is dropped
    whether or not it would have been answerable."""
    ticker = "KXHIGHAUS-26AUG12-B105.5"
    _seed(evo_session, kalshi_low=105.0, kalshi_high=106.0, ticker=ticker,
          ladder=AUS_POLY_LIVE,
          pm_age_min=evo_settings.signal_max_age_minutes + 5)
    out = signals.compute_signals(
        evo_session, [ticker], now=NOW, settings=evo_settings)
    assert out.get(ticker, {}).get("pm_divergence") is None


def test_a_bucket_outside_the_ladder_still_has_no_signal(evo_session, evo_settings):
    ticker = "KXHIGHAUS-26AUG12-B89.5"
    _seed(evo_session, kalshi_low=89.0, kalshi_high=90.0, ticker=ticker,
          ladder=AUS_POLY_LIVE)
    out = signals.compute_signals(
        evo_session, [ticker], now=NOW, settings=evo_settings)
    assert out.get(ticker, {}).get("pm_divergence") is None
