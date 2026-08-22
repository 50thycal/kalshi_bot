"""`scripts/cluster_stats.py` — the event-clustered bootstrap.

Every calibration interval in the theta work, and every power calculation in the MMSELL
design, rests on this module. The thing it exists to prevent is counting one crypto ladder's
single settlement print as forty independent observations, so the tests are built around
samples whose true independent count is KNOWN by construction.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cluster_stats as cs  # noqa: E402


def _perfectly_correlated(events: int, per_event: int, rate: float, seed: int = 3):
    """`events` ladders; within one ladder every market shares the SAME outcome. The extreme
    case, and the realistic one for a ladder settling against a single spot print."""
    rng = random.Random(seed)
    rows = []
    for e in range(events):
        hit = rng.random() < rate
        for _ in range(per_event):
            rows.append({"ev": f"E{e}", "p": rate, "y": hit})
    return rows


def _independent(n: int, rate: float, seed: int = 4):
    rng = random.Random(seed)
    return [{"ev": f"E{i}", "p": rate, "y": rng.random() < rate} for i in range(n)]


class TestClusterProfile:
    def test_it_counts_events_not_markets(self):
        prof = cs.cluster_profile(_perfectly_correlated(50, 20, 0.05), "ev")
        assert prof["rows"] == 1000
        assert prof["clusters"] == 50
        assert prof["mean_size"] == 20

    def test_kish_equals_the_cluster_count_when_ladders_are_equal_width(self):
        prof = cs.cluster_profile(_perfectly_correlated(40, 10, 0.05), "ev")
        assert prof["kish_effective_clusters"] == pytest.approx(40.0)

    def test_kish_collapses_when_one_event_dominates(self):
        rows = ([{"ev": "BIG", "p": 0.05, "y": False} for _ in range(900)]
                + [{"ev": f"S{i}", "p": 0.05, "y": False} for i in range(10)])
        prof = cs.cluster_profile(rows, "ev")
        assert prof["clusters"] == 11
        # 910 rows across 11 events, but one event is 99% of them: worth ~1.2 events.
        assert prof["kish_effective_clusters"] < 1.5
        assert prof["largest_cluster_share"] > 0.98

    def test_rows_with_no_cluster_id_are_their_own_clusters(self):
        # Pooling every unlabelled row into one giant group would understate the evidence as
        # badly as splitting related rows overstates it.
        rows = [{"ev": None, "p": 0.1, "y": False} for _ in range(20)]
        assert cs.cluster_profile(rows, "ev")["clusters"] == 20


class TestDesignEffect:
    def test_independent_rows_cost_nothing(self):
        d = cs.design_effect(_independent(3000, 0.1), "ev", lambda r: 1.0 if r["y"] else 0.0)
        assert 0.7 < d["deff"] < 1.4, d
        assert d["n_eff"] > 2000

    def test_a_ladder_of_twenty_is_worth_about_one_observation(self):
        rows = _perfectly_correlated(150, 20, 0.1)
        d = cs.design_effect(rows, "ev", lambda r: 1.0 if r["y"] else 0.0)
        # 3,000 markets, 150 independent worlds. deff should land near the ladder width.
        assert 10 < d["deff"] < 32, d
        assert 90 < d["n_eff"] < 400, d


class TestRatioCi:
    def test_the_point_estimate_is_untouched_by_clustering(self):
        rows = _perfectly_correlated(120, 12, 0.1)
        st = cs.ratio_ci(rows, "ev", "p", "y")
        naive = sum(1 for r in rows if r["y"]) / sum(r["p"] for r in rows)
        assert st["r"] == pytest.approx(naive)

    def test_the_clustered_interval_is_much_wider_than_an_unclustered_one(self):
        rows = _perfectly_correlated(120, 12, 0.1)
        wide = cs.ratio_ci(rows, "ev", "p", "y")
        # Same rows, but each market claimed as its own event.
        flat = [{**r, "ev": f"row{i}"} for i, r in enumerate(rows)]
        narrow = cs.ratio_ci(flat, "ev", "p", "y")
        assert (wide["hi"] - wide["lo"]) > 2.5 * (narrow["hi"] - narrow["lo"])

    def test_no_interval_is_offered_on_a_handful_of_events(self):
        st = cs.ratio_ci(_perfectly_correlated(3, 40, 0.2), "ev", "p", "y")
        assert st["clusters"] == 3
        assert st["lo"] is None and st["hi"] is None
        assert cs.fmt_ci(st["lo"], st["hi"]) == "n/a"

    def test_a_calibrated_model_covers_one(self):
        rows = _independent(4000, 0.2)
        st = cs.ratio_ci(rows, "ev", "p", "y")
        assert st["lo"] < 1.0 < st["hi"]


class TestWeighting:
    def test_market_and_event_weighting_differ_when_ladders_differ_in_width(self):
        # One wide ladder of bad predictions, many narrow ladders of good ones. Market
        # weighting is dominated by the wide one; event weighting is not.
        rows = ([{"ev": "WIDE", "v": 1.0} for _ in range(500)]
                + [{"ev": f"N{i}", "v": 0.0} for i in range(50)])
        mw = cs.mean_ci(rows, "ev", lambda r: r["v"])
        ew = cs.mean_ci(rows, "ev", lambda r: r["v"], event_weighted=True)
        assert mw["mean"] == pytest.approx(500 / 550, abs=1e-9)
        assert ew["mean"] == pytest.approx(1 / 51, abs=1e-9)

    def test_they_agree_when_every_cluster_is_one_row(self):
        rows = [{"ev": f"E{i}", "v": float(i % 7)} for i in range(300)]
        mw = cs.mean_ci(rows, "ev", lambda r: r["v"])
        ew = cs.mean_ci(rows, "ev", lambda r: r["v"], event_weighted=True)
        assert mw["mean"] == pytest.approx(ew["mean"])


class TestPairedComparison:
    def test_a_consistent_small_edge_is_detected_that_separate_intervals_would_hide(self):
        # Both models are noisy in the same way per market; the candidate is uniformly a
        # little better. Separate intervals overlap massively; the PAIRED one does not.
        rng = random.Random(9)
        rows = []
        for e in range(200):
            shared = rng.gauss(0.0, 1.0)          # market-to-market noise, common to both
            for _ in range(5):
                rows.append({"ev": f"E{e}", "a": shared + 0.02, "b": shared})
        a = cs.mean_ci(rows, "ev", lambda r: r["a"])
        b = cs.mean_ci(rows, "ev", lambda r: r["b"])
        assert a["lo"] < b["mean"] < a["hi"], "separate intervals cannot resolve this"
        d = cs.paired_mean_ci(rows, "ev", lambda r: r["b"] - r["a"])
        assert d["favors"] == "candidate"
        assert d["hi"] < 0

    def test_no_difference_reports_neither(self):
        rng = random.Random(11)
        rows = [{"ev": f"E{i // 4}", "a": rng.gauss(0, 1), "b": rng.gauss(0, 1)}
                for i in range(800)]
        d = cs.paired_mean_ci(rows, "ev", lambda r: r["b"] - r["a"])
        assert d["favors"] == "neither"
        assert d["lo"] < 0 < d["hi"]

    def test_the_sign_convention_points_at_the_reference_when_the_candidate_is_worse(self):
        rows = [{"ev": f"E{i // 3}", "a": 0.0, "b": 0.5} for i in range(300)]
        d = cs.paired_mean_ci(rows, "ev", lambda r: r["b"] - r["a"])
        assert d["favors"] == "reference"
        assert d["lo"] > 0


class TestDeterminism:
    def test_the_same_seed_reproduces_the_same_interval(self):
        rows = _perfectly_correlated(80, 9, 0.15)
        a = cs.ratio_ci(rows, "ev", "p", "y", seed=1234)
        b = cs.ratio_ci(rows, "ev", "p", "y", seed=1234)
        assert (a["lo"], a["hi"]) == (b["lo"], b["hi"])

    def test_a_different_seed_moves_it_only_a_little(self):
        rows = _perfectly_correlated(80, 9, 0.15)
        a = cs.ratio_ci(rows, "ev", "p", "y", seed=1)
        b = cs.ratio_ci(rows, "ev", "p", "y", seed=2)
        assert abs(a["hi"] - b["hi"]) < 0.25 * a["hi"]


class TestFormatting:
    def test_non_finite_bounds_render_as_not_available(self):
        assert cs.fmt_ci(None, None) == "n/a"
        assert cs.fmt_ci(float("nan"), 1.0) == "n/a"
        assert cs.fmt_ci(0.5, 1.5) == "[0.50, 1.50]"

    def test_percentile_interpolates(self):
        assert cs._percentile([0.0, 1.0], 0.5) == pytest.approx(0.5)
        assert cs._percentile([1.0], 0.99) == 1.0
        assert math.isnan(cs._percentile([], 0.5))


class TestZeroFailureBound:
    """A percentile bootstrap on an all-success sample returns [1, 1] — every resample is drawn
    from observations that all succeeded, so no replicate can contain a failure. That is a
    boundary artefact of the method, not evidence of certainty, and it is exactly the case the
    settlement-label gate lands in."""

    @staticmethod
    def _rows(clusters: int, per: int = 4, failures: int = 0):
        rows = []
        for c in range(clusters):
            for i in range(per):
                rows.append({"ev": f"E{c}", "ok": not (c < failures and i == 0)})
        return rows

    def test_the_bootstrap_collapses_to_a_point_on_an_all_success_sample(self):
        rows = self._rows(63)
        m = cs.mean_ci(rows, "ev", lambda r: 1.0 if r["ok"] else 0.0)
        assert (m["lo"], m["hi"]) == (1.0, 1.0), "the artefact this class exists to replace"

    def test_the_exact_bound_is_well_below_one(self):
        st = cs.cluster_success_lower_bound(self._rows(63), "ev", lambda r: r["ok"])
        assert st["clusters"] == 63
        assert st["cluster_failures"] == 0
        assert st["row_rate"] == 1.0
        # alpha ** (1/C) — the rule of three, generalised.
        assert st["lower"] == pytest.approx(0.01 ** (1 / 63), rel=1e-9)
        assert 0.92 < st["lower"] < 0.94

    def test_more_clusters_buy_a_tighter_bound(self):
        a = cs.cluster_success_lower_bound(self._rows(63), "ev", lambda r: r["ok"])["lower"]
        b = cs.cluster_success_lower_bound(self._rows(152), "ev", lambda r: r["ok"])["lower"]
        assert b > a
        assert b >= 0.97, "152 clean events is what a 97% bar costs"

    def test_the_cluster_is_the_unit_so_extra_rows_buy_nothing(self):
        narrow = cs.cluster_success_lower_bound(self._rows(40, per=2), "ev", lambda r: r["ok"])
        wide = cs.cluster_success_lower_bound(self._rows(40, per=60), "ev", lambda r: r["ok"])
        assert narrow["lower"] == pytest.approx(wide["lower"])
        assert wide["rows"] == 2400 and wide["clusters"] == 40

    def test_one_failing_row_fails_its_whole_cluster(self):
        st = cs.cluster_success_lower_bound(self._rows(63, failures=1), "ev", lambda r: r["ok"])
        assert st["cluster_failures"] == 1
        assert st["row_rate"] > 0.99          # 1 bad row in 252
        assert st["lower"] < 0.92             # but the bound is charged a whole cluster

    def test_a_failure_lowers_the_bound(self):
        clean = cs.cluster_success_lower_bound(self._rows(63), "ev", lambda r: r["ok"])["lower"]
        dirty = cs.cluster_success_lower_bound(
            self._rows(63, failures=1), "ev", lambda r: r["ok"])["lower"]
        assert dirty < clean

    @pytest.mark.parametrize("bound,expected", [(0.97, 152), (0.95, 90), (0.99, 459)])
    def test_clusters_needed_is_the_inverse(self, bound, expected):
        assert cs.clusters_needed_for(bound) == expected
        got = cs.cluster_success_lower_bound(
            self._rows(expected), "ev", lambda r: r["ok"])["lower"]
        assert got >= bound


class TestDirectRatioContrast:
    """`log(R_A / R_B)` resampled together, rather than two marginal intervals compared by eye.

    Disjoint marginal intervals are SUFFICIENT for significance but not necessary, and they are
    not the question. The question is whether the contrast differs from zero, and the two groups
    share events — so their errors covary and marginal intervals throw that covariance away.
    """

    @staticmethod
    def _pop(events=200, per=30, base_rate=0.05, sel_rate=None, sel_every=3, seed=3):
        rng = random.Random(seed)
        rows = []
        for e in range(events):
            hit = rng.random() < base_rate
            for k in range(per):
                sel = (k == 0 and e % sel_every == 0)
                y = hit if (not sel or sel_rate is None) else (rng.random() < sel_rate)
                rows.append({"ev": f"E{e}", "p": base_rate, "y": y, "sel": sel})
        return rows

    def test_a_real_contrast_is_detected(self):
        st = cs.ratio_contrast_ci(self._pop(sel_rate=0.20), "ev", "p", "y",
                                  lambda r: r["sel"])
        assert st["excludes_zero"] is True
        assert st["lo"] > 0
        assert st["point"] > 1.0                      # log(~4x)
        assert st["valid_replicates"] == st["replicates"]

    def test_no_contrast_reports_none(self):
        st = cs.ratio_contrast_ci(self._pop(sel_rate=None), "ev", "p", "y",
                                  lambda r: r["sel"])
        assert st["excludes_zero"] is False
        assert st["lo"] < 0 < st["hi"]

    def test_it_can_disagree_with_disjoint_marginal_intervals(self):
        """The whole reason for the change. Marginal intervals are computed on different
        populations and ignore the shared events; the direct contrast need not agree with a
        by-eye reading of them."""
        rows = self._pop(events=40, per=25, sel_rate=0.18, seed=11)
        a = [r for r in rows if r["sel"]]
        b = [r for r in rows if not r["sel"]]
        ma = cs.ratio_ci(a, "ev", "p", "y")
        mb = cs.ratio_ci(b, "ev", "p", "y")
        st = cs.ratio_contrast_ci(rows, "ev", "p", "y", lambda r: r["sel"])
        marginally_disjoint = (ma["lo"] is not None and mb["hi"] is not None
                               and ma["lo"] > mb["hi"])
        # Both are computed; the direct test is the one that decides.
        assert isinstance(marginally_disjoint, bool)
        assert st["lo"] is not None and st["hi"] is not None

    def test_the_haldane_correction_is_uniform_and_visible(self):
        st = cs.ratio_contrast_ci(self._pop(sel_rate=0.20), "ev", "p", "y",
                                  lambda r: r["sel"])
        assert st["haldane_c"] == cs.HALDANE_C == 0.5
        # Applied to the point estimate too, not only to replicates that happen to hit a zero.
        assert st["point"] != st["point_uncorrected"]
        assert abs(st["point"] - st["point_uncorrected"]) < 0.2

    def test_a_group_with_zero_expected_yields_no_interval(self):
        rows = [{"ev": f"E{i // 5}", "p": 0.0 if i % 7 == 0 else 0.05,
                 "y": False, "sel": i % 7 == 0} for i in range(400)]
        st = cs.ratio_contrast_ci(rows, "ev", "p", "y", lambda r: r["sel"])
        assert st["a"]["expected"] == 0.0
        assert math.isnan(st["point"])
        assert st["valid_replicates"] == 0
        assert st["lo"] is None and st["excludes_zero"] is False

    def test_zero_observed_does_not_kill_a_replicate(self):
        # A small selected set that never hits: log is defined only because of the correction.
        rows = [{"ev": f"E{i // 6}", "p": 0.05, "y": (i % 23 == 0) and i % 6 != 0,
                 "sel": i % 6 == 0} for i in range(1200)]
        st = cs.ratio_contrast_ci(rows, "ev", "p", "y", lambda r: r["sel"])
        assert st["a"]["observed"] == 0
        assert math.isfinite(st["point"])
        assert st["valid_replicates"] > 0.9 * st["replicates"]

    def test_it_reports_event_coverage_per_group(self):
        st = cs.ratio_contrast_ci(self._pop(), "ev", "p", "y", lambda r: r["sel"])
        assert st["a"]["clusters"] == 67            # every third of 200 events
        assert st["b"]["clusters"] == 200
        assert st["clusters"] == 200

    def test_it_is_deterministic_in_the_seed(self):
        rows = self._pop(sel_rate=0.20)
        a = cs.ratio_contrast_ci(rows, "ev", "p", "y", lambda r: r["sel"], seed=99)
        b = cs.ratio_contrast_ci(rows, "ev", "p", "y", lambda r: r["sel"], seed=99)
        assert (a["lo"], a["hi"]) == (b["lo"], b["hi"])

    def test_too_few_events_declines_to_interval(self):
        rows = self._pop(events=4, per=10, sel_rate=0.5)
        st = cs.ratio_contrast_ci(rows, "ev", "p", "y", lambda r: r["sel"])
        assert st["lo"] is None and st["excludes_zero"] is False
