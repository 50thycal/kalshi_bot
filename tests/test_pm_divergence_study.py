"""The PMDIV probe's measurement math, tested without a database.

The probe answers "when Polymarket and Kalshi disagree about tomorrow's temperature, who is
right?" — but every number it prints rests on first RECONSTRUCTING a Polymarket ladder that the
database does not store as a unit. `repository.insert_polymarket_snapshots` stamps
`captured_at=_now()` inside its row loop, so all 451,198 timestamps in the table hold exactly one
row and no ladder shares a timestamp. Code that selects "the newest ladder" by exact-equality
against `max(captured_at)` — which is what `weather/validation.py::_pm_implied_mean` does — gets a
SINGLE BUCKET and reports its midpoint as an implied mean. That is the defect behind the
`pm_err=9.38F` in weather_validation.

So `cluster_batches` is the load-bearing function here: if it were wrong, every downstream number
would be the same artifact the probe exists to expose, and it would look like a finding rather than
a bug. Its tests come first and are the strictest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.pm_divergence_study import (
    BANDS,
    band_of,
    bucket_mid_temp,
    bucket_wins,
    cluster_batches,
    favored_bucket,
    fee_c,
    implied_mean,
    settle_cents,
)

T0 = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)


def _capture(at: datetime, n: int, spacing_ms: int = 3) -> list[tuple]:
    """One ladder capture as the DB stores it: n rows, each a few ms apart, never sharing a ts."""
    return [(at + timedelta(milliseconds=i * spacing_ms), 90.0 + i, 91.0 + i, 0.1) for i in range(n)]


# --- reconstructing the ladder (the load-bearing part) -----------------------


def test_a_capture_written_row_by_row_is_recovered_as_one_ladder():
    """The real storage pattern: 11 buckets, each with its own microsecond timestamp."""
    rows = _capture(T0, 11)
    batches = cluster_batches(rows)
    assert len(batches) == 1
    assert len(batches[0]) == 11


def test_successive_captures_minutes_apart_stay_separate():
    rows = _capture(T0, 11) + _capture(T0 + timedelta(minutes=5), 11)
    batches = cluster_batches(rows)
    assert [len(b) for b in batches] == [11, 11]


def test_batches_come_back_in_time_order_so_the_last_is_the_newest():
    """The probe takes the latest batch at-or-before a cycle; order is not incidental."""
    rows = _capture(T0 + timedelta(minutes=5), 3) + _capture(T0, 3)
    batches = cluster_batches(rows)
    assert batches[0][0][0] < batches[1][0][0]


def test_the_exact_equality_bug_is_what_clustering_replaces():
    """Pin the defect itself: selecting rows whose captured_at == max(captured_at) keeps ONE row,
    so the 'implied mean' becomes a single bucket's midpoint rather than the ladder's."""
    rows = _capture(T0, 11)
    newest = max(r[0] for r in rows)
    naive = [r for r in rows if r[0] == newest]
    assert len(naive) == 1                       # <- the bug, reproduced
    assert len(cluster_batches(rows)[0]) == 11   # <- what the probe does instead


def test_an_unusually_slow_capture_still_groups():
    """Tolerance is the point of the gap threshold — a capture stretched over seconds is still one
    ladder, because the alternative (splitting it) silently recreates the single-bucket artifact."""
    rows = _capture(T0, 8, spacing_ms=4000)  # 4s between rows, 28s span
    assert len(cluster_batches(rows, gap_s=120.0)) == 1


def test_no_rows_is_no_batches():
    assert cluster_batches([]) == []


# --- the implied mean --------------------------------------------------------


def test_implied_mean_is_probability_weighted_over_bucket_midpoints():
    ladder = [(98.0, 99.0, 0.04), (100.0, 101.0, 0.72), (102.0, 103.0, 0.22)]
    expected = (0.04 * 98.5 + 0.72 * 100.5 + 0.22 * 102.5) / (0.04 + 0.72 + 0.22)
    assert implied_mean(ladder) == pytest.approx(expected)


def test_implied_mean_normalizes_so_kalshi_mids_and_pm_probs_are_comparable():
    """Kalshi mids sum ABOVE 1 because of the spread; without normalization the two venues'
    'means' would not be on the same scale and every divergence would be fiction."""
    pm = [(70.0, 71.0, 0.25), (72.0, 73.0, 0.75)]
    kalshi = [(70.0, 71.0, 27.0), (72.0, 73.0, 81.0)]  # same shape, sums to 108c
    assert implied_mean(pm) == pytest.approx(implied_mean(kalshi))


def test_open_ended_buckets_use_the_finite_edge():
    assert bucket_mid_temp(None, 83.0) == pytest.approx(83.0 - 1.5)
    assert bucket_mid_temp(102.0, None) == pytest.approx(102.0 + 1.5)
    assert bucket_mid_temp(None, None) is None


def test_an_empty_or_zero_weight_ladder_has_no_mean():
    assert implied_mean([]) is None
    assert implied_mean([(90.0, 91.0, 0.0)]) is None


# --- settlement and P&L ------------------------------------------------------


def test_a_bucket_wins_only_when_it_contains_the_settled_extreme():
    assert bucket_wins(90.0, 91.0, 90.0) is True
    assert bucket_wins(90.0, 91.0, 91.0) is True
    assert bucket_wins(90.0, 91.0, 92.0) is False
    assert bucket_wins(90.0, 91.0, 89.0) is False


def test_open_ended_buckets_win_on_their_unbounded_side():
    assert bucket_wins(None, 83.0, 40.0) is True     # "83 or below"
    assert bucket_wins(None, 83.0, 84.0) is False
    assert bucket_wins(102.0, None, 130.0) is True   # "102 or above"
    assert bucket_wins(102.0, None, 101.0) is False


def test_settle_pnl_is_net_of_the_entry_fee_and_pays_nothing_on_a_loss():
    assert settle_cents(40.0, True) == pytest.approx(100.0 - 40.0 - fee_c(40.0))
    assert settle_cents(40.0, False) == pytest.approx(-40.0 - fee_c(40.0))


def test_the_fee_is_quadratic_and_worst_mid_ladder():
    assert fee_c(50.0) > fee_c(10.0)
    assert fee_c(50.0) > fee_c(90.0)
    assert fee_c(50.0) == 2  # ceil(7 * 0.25)


def test_an_unquotable_ask_yields_no_trade_rather_than_a_free_one():
    """0c/100c asks are not tradable prices; scoring them would manufacture P&L."""
    assert settle_cents(None, True) is None
    assert settle_cents(0.0, True) is None
    assert settle_cents(100.0, True) is None


# --- band assignment ---------------------------------------------------------


def test_divergence_bands_match_the_preregistered_edges():
    assert band_of(-5.0) == BANDS[0]
    assert band_of(-2.0) == BANDS[1]
    assert band_of(0.0) == BANDS[2]
    assert band_of(2.0) == BANDS[3]
    assert band_of(5.0) == BANDS[4]


def test_band_edges_are_half_open_so_every_cycle_lands_in_exactly_one():
    assert band_of(-3.0) == BANDS[1]  # -3 belongs to [-3,-1), not (-inf,-3)
    assert band_of(-1.0) == BANDS[2]
    assert band_of(1.0) == BANDS[3]
    assert band_of(3.0) == BANDS[4]


# --- which bucket a pm-following trade buys ----------------------------------


def test_the_favored_bucket_is_the_one_nearest_polymarkets_mean():
    buckets = [(70.0, 71.0, 5.0), (72.0, 73.0, 30.0), (74.0, 75.0, 60.0)]
    assert favored_bucket(buckets, 74.4) == 2
    assert favored_bucket(buckets, 70.1) == 0


def test_no_usable_bucket_means_no_trade():
    assert favored_bucket([(None, None, 10.0)], 74.0) is None
    assert favored_bucket([], 74.0) is None
