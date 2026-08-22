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
