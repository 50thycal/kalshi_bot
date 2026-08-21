"""The theta tail diagnosis's estimators (scripts/theta_tail_diagnosis.py), pinned away from
the DB.

The write-up's two load-bearing claims are both shapes, not levels: R rises as the strike moves
out (a tail-SHAPE error, which a multiplier cannot fix), and R on the SELECTED quotes exceeds R
on the REJECTED ones within the same probability bucket (threshold-selection bias). Both depend
on the ratio machinery being right at small counts, where a normal approximation is not, and on
the market-outcome derivation being right in each strike direction — a flipped `less` would
invert the miss into a surplus.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "theta_tail_diagnosis",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "theta_tail_diagnosis.py",
)
td = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(td)


class TestNormPpf:
    @pytest.mark.parametrize("p,z", [
        (0.5, 0.0), (0.975, 1.959964), (0.025, -1.959964),
        (0.99, 2.326348), (0.01, -2.326348), (0.001, -3.090232),
    ])
    def test_matches_the_standard_quantiles(self, p, z):
        assert td.norm_ppf(p) == pytest.approx(z, abs=1e-5)

    @pytest.mark.parametrize("p", [0.0, 1.0, -0.1, 1.1, None])
    def test_a_degenerate_probability_has_no_finite_quantile(self, p):
        # 53.7% of the model's ladder output is exactly 0 and 39.4% exactly 1. Those rows must
        # drop out of the z table rather than be coerced onto a finite axis.
        assert td.norm_ppf(p) is None


class TestPoissonRatioCI:
    def test_a_calibrated_cell_brackets_one(self):
        lo, hi = td.poisson_ratio_ci(100, 100.0)
        assert lo < 1.0 < hi

    def test_the_live_canary_reading_excludes_one(self):
        lo, hi = td.poisson_ratio_ci(11, 2.66)
        assert lo > 1.0
        assert lo == pytest.approx(1.62, abs=0.05)

    def test_zero_observed_hits_gives_a_zero_lower_bound(self):
        lo, hi = td.poisson_ratio_ci(0, 5.0)
        assert lo == pytest.approx(0.0, abs=1e-6)
        assert hi > 0

    def test_the_interval_tightens_as_evidence_grows_at_a_fixed_ratio(self):
        narrow = td.poisson_ratio_ci(400, 200.0)
        wide = td.poisson_ratio_ci(4, 2.0)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])
        for lo, hi in (narrow, wide):
            assert lo < 2.0 < hi

    def test_no_expected_hits_means_no_interval_rather_than_infinity(self):
        assert td.poisson_ratio_ci(3, 0.0) == (None, None)

    def test_the_two_sided_lower_bound_sits_below_the_one_sided_99_bound(self):
        # The canary's recorded LCB99 was 1.79 (one-sided). A two-sided 99% interval spends
        # 0.5% per tail, so its lower bound must be MORE permissive, not less.
        lo, _hi = td.poisson_ratio_ci(11, 2.66)
        assert lo < 1.79


class TestPoissonSf:
    def test_matches_a_direct_summation(self):
        lam, k = 2.5, 4
        direct = 1.0 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k))
        assert td._pois_sf(k, lam) == pytest.approx(direct, abs=1e-12)

    def test_at_least_zero_events_is_certain(self):
        assert td._pois_sf(0, 3.0) == 1.0


class TestYesResolved:
    @pytest.mark.parametrize("st,floor,cap,spot,expected", [
        ("greater", 90.0, None, 100.0, True),
        ("greater", 90.0, None, 80.0, False),
        ("greater", 90.0, None, 90.0, False),      # strictly above the strike
        ("less", None, 90.0, 80.0, True),
        ("less", None, 90.0, 100.0, False),
        ("between", 90.0, 100.0, 95.0, True),
        ("between", 90.0, 100.0, 105.0, False),
        ("between", 90.0, 100.0, 90.0, True),      # inclusive at the floor
        ("between", 90.0, 100.0, 100.0, True),     # inclusive at the cap
        ("GREATER", 90.0, None, 100.0, True),      # case-insensitive
    ])
    def test_each_strike_direction(self, st, floor, cap, spot, expected):
        assert td._yes_resolved(st, floor, cap, spot) is expected

    @pytest.mark.parametrize("st,floor,cap", [
        (None, 90.0, None), ("greater", None, None), ("less", None, None),
        ("between", 90.0, None), ("weird", 90.0, 100.0),
    ])
    def test_an_underivable_outcome_is_None_and_never_guessed(self, st, floor, cap):
        assert td._yes_resolved(st, floor, cap, 95.0) is None


class TestBuckets:
    @pytest.mark.parametrize("p,label", [
        (0.0, "0.00-0.02"), (0.019, "0.00-0.02"), (0.02, "0.02-0.05"),
        (0.05, "0.05-0.10"), (0.35, "0.35-0.50"), (0.5, "0.50-1.01"), (1.0, "0.50-1.01"),
    ])
    def test_probability_buckets_are_half_open_and_cover_the_range(self, p, label):
        assert td.p_bucket(p) == label

    @pytest.mark.parametrize("z,label", [
        (-1.0, "0.0-0.5"), (0.0, "0.0-0.5"), (0.5, "0.5-1.0"),
        (2.0, "2.0-2.5"), (2.5, "2.5-9.0"), (7.0, "2.5-9.0"),
    ])
    def test_z_buckets_cover_the_axis_including_the_body(self, z, label):
        assert td.z_bucket(z) == label


class TestFilterConstantsMatchTheBook:
    def test_they_are_theta4s_own_knobs(self):
        # theta4: hi=20 over the base lo=3, edge=6, ttemax=35; theta_min_volume=100.
        # A drift here would silently redefine which quotes count as SELECTED, which is the
        # whole content of the selection test.
        assert td.THETA4_BAND == (3.0, 20.0)
        assert td.THETA4_EDGE_CENTS == 6.0
        assert td.THETA4_TTE_MAX == 35.0
        assert td.THETA_MIN_VOLUME == 100.0
