"""The spliced EVT probability model (kalshi_bot/theta/tailmodel.py).

The incumbent model's defect is mechanical, not statistical: an empirical distribution has no
mass past its own sample maximum, so 93.1% of theta's ladder output was exactly 0 or exactly 1.
The tests below pin the three properties that make the replacement a fix rather than a
different set of numbers:

  * it NEVER returns exactly 0 or 1, at any distance;
  * past the sample maximum it still DISCRIMINATES — a strike twice as far out prices lower,
    which an earlier draft's blanket floor destroyed;
  * it knows when it cannot know: below the independent-observation bar it says so, because a
    floor-dominated probability pooled with an estimated one reads as evidence and is not.
"""

from __future__ import annotations

import math
import random

import pytest

from kalshi_bot.theta import tailmodel as tm
from kalshi_bot.theta.spot import SpotModel


def gbm(n: int, sigma: float = 0.0006, seed: int = 3) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0.0, sigma) for _ in range(n)]


def pareto_excesses(alpha: float, n: int, seed: int = 5) -> list[float]:
    rng = random.Random(seed)
    return [(rng.random() ** (-1.0 / alpha)) - 1.0 for _ in range(n)]


class TestGpdFit:
    def test_an_exponential_sample_recovers_xi_zero_and_the_mean_scale(self):
        rng = random.Random(1)
        ys = [rng.expovariate(1.0) for _ in range(20000)]
        sigma, xi, fitted = tm.fit_gpd(ys)
        assert fitted
        assert xi == pytest.approx(0.0, abs=0.05)
        assert sigma == pytest.approx(1.0, abs=0.05)

    def test_a_pareto_sample_recovers_its_tail_index(self):
        # GPD shape xi = 1/alpha for a Pareto tail.
        sigma, xi, fitted = tm.fit_gpd(pareto_excesses(3.0, 20000))
        assert fitted
        assert xi == pytest.approx(1.0 / 3.0, abs=0.06)
        assert sigma > 0

    def test_too_few_excesses_falls_back_to_exponential_and_says_so(self):
        sigma, xi, fitted = tm.fit_gpd([0.1, 0.2, 0.3])
        assert fitted is False
        assert xi == 0.0
        assert sigma == pytest.approx(0.2)

    def test_an_empty_sample_does_not_raise(self):
        assert tm.fit_gpd([]) == (0.0, 0.0, False)

    def test_pwm_saturates_on_a_tail_heavier_than_it_can_represent(self):
        # PWM's moments exist only for xi < 0.5. Against Pareto(alpha=0.5), whose true xi is 2,
        # it returns ~0.95 rather than diverging. Pinned because the bias runs DOWNWARD — the
        # dangerous direction for a seller — so anyone reading a large xi must read it as "at
        # least this heavy" and not as a point estimate.
        _sigma, xi, fitted = tm.fit_gpd(pareto_excesses(0.5, 500, seed=9))
        assert fitted is True
        assert 0.5 < xi < 1.0
        assert xi < 2.0


class TestNoDegenerateProbabilities:
    def test_never_exactly_zero_or_one_however_far_out(self):
        rets = gbm(5000)
        m = tm.build(rets, h_min=35)
        mx, mn = max(rets), min(rets)
        for mult in (1.0, 2.0, 10.0, 100.0):
            assert 0.0 < m.p_greater(mx * mult) < 1.0
            assert 0.0 < m.p_less(mn * mult) < 1.0

    def test_the_incumbent_model_IS_exactly_zero_there(self):
        # The defect this module exists to remove, pinned so the comparison stays honest.
        rets = gbm(5000)
        far = max(rets) * 1.5
        spot = 65000.0
        assert SpotModel.prob_from_returns(
            rets, spot, "greater", spot * math.exp(far), None, 1.0) == 0.0

    def test_vol_mult_cannot_escape_the_truncation(self):
        # Widening rescales the THRESHOLD, so it pulls some strikes back inside the support but
        # leaves anything beyond `max(rets) * k` at exactly zero. This is why mult=2.0 reduced
        # theta4's miss without removing it.
        rets = gbm(5000)
        spot = 65000.0
        strike = spot * math.exp(max(rets) * 3.0)
        assert SpotModel.prob_from_returns(rets, spot, "greater", strike, None, 2.0) == 0.0


class TestTailStillDiscriminates:
    def test_probability_decreases_monotonically_past_the_sample_maximum(self):
        # Two regressions in one. An early draft floored every probability at 1/(2*n_eff),
        # returning the SAME number for a strike 1x, 1.5x and 2x beyond the data. A later one
        # extrapolated a fitted xi < 0 to its endpoint and returned exactly zero past it —
        # reinstating "impossible", just relocated. A Gaussian sample fits xi < 0, so this is
        # exactly the case that caught it.
        rets = gbm(20000)
        m = tm.build(rets, h_min=5)          # n_eff = 4000, so the floor is tiny
        mx = max(rets)
        ps = [m.p_greater(mx * k) for k in (1.0, 1.5, 2.0, 3.0)]
        assert all(a > b for a, b in zip(ps, ps[1:], strict=False)), ps
        assert all(p > 0.0 for p in ps), ps

    def test_a_bounded_fit_still_extrapolates_past_its_own_endpoint(self):
        rets = gbm(20000)
        m = tm.build(rets, h_min=5)
        assert m.upper.xi < 0, "a Gaussian sample should fit a bounded tail"
        endpoint = m.upper.threshold - m.upper.scale / m.upper.xi
        assert m.upper.sf(endpoint * 2.0) > 0.0

    def test_a_heavier_tail_prices_a_far_strike_higher(self):
        light = tm.build(gbm(20000, sigma=0.0006, seed=11), h_min=5)
        heavy_rets = gbm(20000, sigma=0.0006, seed=11)
        heavy_rets[:200] = [r * 12 for r in heavy_rets[:200]]     # inject fat tail events
        heavy = tm.build(heavy_rets, h_min=5)
        x = max(gbm(20000, sigma=0.0006, seed=11)) * 2.0
        assert heavy.p_greater(x) > light.p_greater(x)


class TestDeclustering:
    """One shock must not be counted as many independent tail observations.

    Two levels of pseudo-replication had to go. Overlapping h-minute returns turn one move into
    ~h neighbouring extremes; `build` now consumes non-overlapping blocks, so that is structural.
    Volatility ALSO clusters across genuinely disjoint blocks, which runs-declustering handles.
    """

    @staticmethod
    def one_storm(n: int = 1000, storm_at: int = 500, storm_len: int = 10) -> list[float]:
        rng = random.Random(4)
        # A quiet series whose noise never reaches the storm's magnitude, so the exceedances
        # above a high threshold come from the storm and nowhere else.
        out = [rng.gauss(0.0, 0.0005) for _ in range(n)]
        for i in range(storm_at, storm_at + storm_len):
            out[i] = 0.05
        return out

    def test_a_single_storm_collapses_to_one_cluster(self):
        blocks = self.one_storm()
        u = 0.01                       # above every noise block, below the storm
        assert sum(1 for b in blocks if b > u) == 10
        assert len(tm.decluster(blocks, u)) == 1

    def test_the_cluster_is_represented_by_its_maximum(self):
        blocks = [0.0, 0.02, 0.05, 0.03, 0.0, 0.0, 0.0]
        assert tm.decluster(blocks, 0.01) == [0.05]

    def test_separate_storms_stay_separate(self):
        blocks = [0.0] * 5 + [0.05] * 3 + [0.0] * 5 + [0.04] * 3 + [0.0] * 5
        assert len(tm.decluster(blocks, 0.01)) == 2

    def test_declustering_needs_time_order_and_the_fit_supplies_it(self):
        # Sorting would put every exceedance adjacent and report a single cluster. This is why
        # `build` documents that its input must be in time order.
        blocks = self.one_storm()
        assert len(tm.decluster(sorted(blocks), 0.01)) == 1
        assert len(tm.decluster(blocks, 0.01)) == 1  # same here only because there IS one storm
        spread = [0.05 if i % 100 == 0 else 0.0 for i in range(1000)]
        assert len(tm.decluster(spread, 0.01)) == 10
        assert len(tm.decluster(sorted(spread), 0.01)) == 1   # the failure mode, pinned

    def test_a_fit_reports_raw_and_declustered_counts_separately(self):
        fit = tm._fit_side(self.one_storm(), 0.99)
        assert fit.n_excess > fit.n_clusters
        assert fit.n_clusters == 1

    def test_one_storm_is_never_enough_evidence_to_be_powered(self):
        fit = tm._fit_side(self.one_storm(), 0.99)
        assert fit.powered is False


class TestPowerDependsOnTailQ:
    """400 blocks at tail_q=0.99 is ~4 exceedances. An earlier version gated on a fixed block
    count and would have called that powered."""

    @staticmethod
    def blocks(n: int) -> list[float]:
        rng = random.Random(6)
        # Independent draws, so declustering leaves essentially every exceedance standing and
        # the count is governed by (1 - tail_q) alone.
        return [rng.gauss(0.0, 0.002) for _ in range(n)]

    def test_the_same_block_count_is_powered_at_one_threshold_and_not_another(self):
        bl = self.blocks(400)
        loose = tm.build(bl, h_min=35, tail_q=0.90)
        tight = tm.build(bl, h_min=35, tail_q=0.99)
        assert loose.upper.n_clusters >= tm.MIN_TAIL_EXCEEDANCES_FOR_POWER
        assert tight.upper.n_clusters < tm.MIN_TAIL_EXCEEDANCES_FOR_POWER
        assert loose.underpowered is False
        assert tight.underpowered is True

    def test_a_long_window_is_powered_even_at_a_tight_threshold(self):
        m = tm.build(self.blocks(4000), h_min=35, tail_q=0.99)
        assert m.upper.powered and m.lower.powered
        assert m.underpowered is False

    def test_thetas_five_day_window_cannot_power_a_fit(self):
        # 5 days of 1-minute closes at h=35 is 7200/35 ~= 205 non-overlapping blocks.
        m = tm.build(self.blocks(205), h_min=35, tail_q=0.95)
        assert m.n == 205
        assert m.underpowered is True

    def test_ninety_days_can(self):
        # 90 days is ~129,600 minutes, i.e. ~3,700 blocks at h=35.
        m = tm.build(self.blocks(3700), h_min=35, tail_q=0.95, fit_days=90.0)
        assert m.underpowered is False
        assert m.describe()["fit_days"] == 90.0

    def test_a_thin_window_yields_no_model_at_all(self):
        assert tm.build(self.blocks(10), h_min=35) is None
        assert tm.build([], h_min=35) is None

    def test_the_floor_is_half_a_block_resolution_step(self):
        m = tm.build(self.blocks(500), h_min=35)
        assert m.p_floor == pytest.approx(1.0 / (2 * 500))


class TestPerSideEvidence:
    """A `less` strike prices off the LOWER tail. Reporting the upper tail's shape beside it
    describes the wrong distribution."""

    @staticmethod
    def skewed(n: int = 2000) -> list[float]:
        rng = random.Random(8)
        # Heavy upper tail, thin lower tail: the two sides must not report the same numbers.
        return [abs(rng.gauss(0.0, 0.002)) * 3 if i % 7 == 0 else -abs(rng.gauss(0.0, 0.0005))
                for i in range(n)]

    def test_active_xi_follows_the_strike_direction(self):
        m = tm.build(self.skewed(), h_min=35, tail_q=0.95)
        assert m.active_xi("greater") == m.upper.xi
        assert m.active_xi("less") == m.lower.xi
        assert m.active_xi("between") is None

    def test_active_cluster_count_follows_the_strike_direction(self):
        m = tm.build(self.skewed(), h_min=35, tail_q=0.95)
        assert m.active_clusters("greater") == m.upper.n_clusters
        assert m.active_clusters("less") == m.lower.n_clusters

    def test_powered_for_is_per_side(self):
        m = tm.build(self.skewed(), h_min=35, tail_q=0.95)
        assert m.powered_for("greater") == m.upper.powered
        assert m.powered_for("less") == m.lower.powered

    def test_underpowered_is_true_if_EITHER_side_is_weak(self):
        m = tm.build(self.skewed(), h_min=35, tail_q=0.999)
        assert m.underpowered is True

    def test_describe_carries_both_sides_and_the_fit_settings(self):
        d = tm.build(self.skewed(), h_min=35, tail_q=0.95, fit_days=90.0).describe()
        for key in ("upper_n_clusters", "lower_n_clusters", "upper_powered", "lower_powered",
                    "tail_q", "fit_days", "horizon_min", "run_separation",
                    "min_tail_exceedances", "blocks"):
            assert key in d, key
        assert d["tail_q"] == 0.95
        assert d["horizon_min"] == 35


class TestStrikeSemantics:
    @pytest.fixture()
    def model(self):
        return tm.build(gbm(20000), h_min=5)

    def test_greater_and_less_at_the_same_strike_sum_to_one(self, model):
        spot = 65000.0
        for k in (0.99, 1.0, 1.01):
            g = tm.p_yes(model, spot, "greater", spot * k, None)
            lt = tm.p_yes(model, spot, "less", None, spot * k)
            assert g + lt == pytest.approx(1.0, abs=0.02)

    def test_a_wider_between_band_is_more_probable(self, model):
        spot = 65000.0
        narrow = tm.p_yes(model, spot, "between", spot * 0.999, spot * 1.001)
        wide = tm.p_yes(model, spot, "between", spot * 0.99, spot * 1.01)
        assert wide > narrow

    def test_an_inverted_between_band_is_not_negative(self, model):
        assert tm.p_yes(model, 65000.0, "between", 66000.0, 64000.0) > 0.0

    @pytest.mark.parametrize("args", [
        (None, "greater", 100.0, None),          # no model
        ("model", "greater", None, None),        # no floor strike
        ("model", "less", None, None),           # no cap
        ("model", "weird", 100.0, 200.0),        # unknown strike type
    ])
    def test_unpriceable_inputs_return_None_rather_than_a_guess(self, model, args):
        m = model if args[0] == "model" else None
        assert tm.p_yes(m, 65000.0, args[1], args[2], args[3]) is None

    def test_a_nonpositive_spot_is_unpriceable(self, model):
        assert tm.p_yes(model, 0.0, "greater", 100.0, None) is None
        assert tm.p_yes(model, None, "greater", 100.0, None) is None


class TestTrailingTelemetry:
    def _closes(self, n=500, sigma=0.0005, seed=5):
        rng = random.Random(seed)
        base = 1_700_000_000 // 60 * 60
        px, out = 65000.0, {}
        for i in range(n):
            px *= math.exp(rng.gauss(0.0, sigma))
            out[base + i * 60] = px
        return base, out

    def test_realized_vol_recovers_the_generating_sigma(self):
        base, closes = self._closes(sigma=0.0005)
        m = SpotModel(closes)
        ts = base + 499 * 60
        # 0.0005 in log terms == 5 bps/min.
        assert m.realized_vol_bps(ts, 240) == pytest.approx(5.0, rel=0.25)

    def test_too_few_returns_gives_None_rather_than_a_number(self):
        base, closes = self._closes()
        assert SpotModel(closes).realized_vol_bps(base + 499 * 60, 5) is None

    def test_trailing_move_is_signed(self):
        base = 1_700_000_000 // 60 * 60
        up = {base + i * 60: 100.0 * (1.0 + i * 0.001) for i in range(120)}
        down = {base + i * 60: 100.0 * (1.0 - i * 0.001) for i in range(120)}
        ts = base + 119 * 60
        assert SpotModel(up).trailing_move_bps(ts, 60) > 0
        assert SpotModel(down).trailing_move_bps(ts, 60) < 0

    def test_a_missing_lookback_point_gives_None(self):
        base = 1_700_000_000 // 60 * 60
        m = SpotModel({base: 100.0})
        assert m.trailing_move_bps(base, 60) is None


class TestRefitHarness:
    """`scripts/theta_tail_refit.py` — the out-of-sample scorer. Its own helpers, pinned away
    from the DB, because a wrong horizon bucket or product map would silently score the model
    against the wrong return sample."""

    @staticmethod
    def _mod():
        import importlib.util
        import pathlib
        import sys as _sys

        path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "theta_tail_refit.py"
        spec = importlib.util.spec_from_file_location("theta_tail_refit", path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_it_loads_the_ONE_tail_model_rather_than_a_copy(self):
        # The ops runner installs psycopg only, so the script loads tailmodel.py by path. If
        # that ever silently fell back to a duplicated definition, the validation would be
        # scoring a model the worker does not run.
        mod = self._mod()
        assert mod.tm.MIN_TAIL_EXCEEDANCES_FOR_POWER == tm.MIN_TAIL_EXCEEDANCES_FOR_POWER
        assert mod.tm.RUN_SEPARATION == tm.RUN_SEPARATION
        assert mod.tm.DEFAULT_TAIL_Q == tm.DEFAULT_TAIL_Q

    @pytest.mark.parametrize("minutes,expected", [
        (10.0, 10), (12.4, 10), (13.0, 15), (22.0, 20), (33.0, 35), (35.0, 35),
    ])
    def test_horizon_rounds_to_the_nearest_cached_grid_point(self, minutes, expected):
        assert self._mod().h_bucket(minutes) == expected

    @pytest.mark.parametrize("series,product", [
        ("KXETHD", "ETH"), ("KXETH", "ETH"), ("KXBTCD", "BTC"), ("KXBTC", "BTC"),
        ("KXSOLD", "BTC"), (None, "BTC"),
    ])
    def test_product_map_sends_eth_series_to_the_eth_feed(self, series, product):
        # KXSOLD falling to BTC is a real limitation, not an oversight: there is no SOL candle
        # feed, so a SOL strike is priced off BTC returns. It is called out in the study.
        assert self._mod().product_of(series) == product

    def test_deep_buckets_resolve_below_one_percent(self):
        mod = self._mod()
        # The whole point: 0.001 and 0.015 must not land in one bucket, because a short-tail
        # book lives between them.
        assert mod.deep_bucket(0.001) != mod.deep_bucket(0.015)
        assert mod.deep_bucket(0.001) == "0.000-0.002"
        assert mod.deep_bucket(0.9) == "0.500-1.010"

    def test_the_block_cache_is_shared_across_tail_q_instances(self):
        # Block samples depend on (product, hour, horizon, fit_days) but NOT on tail_q, so
        # rebuilding them per threshold would multiply the dominant cost of a sweep for
        # identical numbers.
        mod = self._mod()
        a = mod.FitCache({"BTC": {}, "ETH": {}}, 0.90, fit_days=90.0)
        b = mod.FitCache({"BTC": {}, "ETH": {}}, 0.99, fit_days=90.0)
        assert a._BLOCKS is b._BLOCKS

    def test_the_harness_block_builder_agrees_with_the_models(self):
        """The harness duplicates `SpotModel.block_returns` because the ops runner has no
        SQLAlchemy. A drift between the two would validate a model the worker does not run."""
        mod = self._mod()
        base = 1_700_000_000 // 60 * 60
        closes = {base + i * 60: 100.0 * math.exp(i * 0.0001) for i in range(6000)}
        as_of = base + 5999 * 60

        cache = mod.FitCache({"BTC": closes, "ETH": {}}, 0.95, fit_days=3.0)
        from_harness = cache._blocks("BTC", as_of, 35)
        from_model = SpotModel(closes).block_returns(as_of, 35, window_days=3.0)
        assert from_harness == pytest.approx(from_model)
        assert len(from_harness) > 0

    def test_the_harness_scores_the_incumbent_over_its_OWN_window(self):
        # The incumbent must be judged as it runs (5 days, overlapping), not as a version of
        # itself that adopted the replacement's sampling.
        assert self._mod().INCUMBENT_TRAIL_DAYS == 5.0


class TestForwardPath:
    """`scripts/theta_forward_path.py` — the toxic-flow research product.

    Retention makes the decision->candle join possible; it does not make it correct. These pin
    the two things that would silently corrupt it: an excursion oriented the wrong way round
    (which would report adverse moves as favourable for half the book), and a missing offset
    quietly becoming a number.
    """

    @staticmethod
    def _mod():
        import importlib.util
        import pathlib
        import sys as _sys

        path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "theta_forward_path.py"
        spec = importlib.util.spec_from_file_location("theta_forward_path", path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _row(strike_type: str, mtc: float = 30.0):
        import datetime as _dt

        base = 1_700_000_000 // 60 * 60
        return {"ticker": "KXBTCD-X", "series": "KXBTCD", "product": "BTC",
                "strike_type": strike_type, "floor": 100.0, "cap": 100.0, "mid": 6.0,
                "model_p": 0.01, "excess": 5.0, "mtc": mtc, "spot": 100.0,
                "captured_at": _dt.datetime.fromtimestamp(base, tz=_dt.timezone.utc)}, base

    def _rising(self, base: int, n: int = 40):
        return {base + i * 60: 100.0 * math.exp(i * 0.0005) for i in range(n)}

    def _falling(self, base: int, n: int = 40):
        return {base + i * 60: 100.0 * math.exp(-i * 0.0005) for i in range(n)}

    def test_forward_returns_are_signed_and_scaled_in_basis_points(self):
        mod = self._mod()
        row, base = self._row("greater")
        out = mod.forward(row, self._rising(base))
        # +5 minutes at 5 bps/min of drift is ~+25 bps.
        assert out["fwd_5m"] == pytest.approx(25.0, rel=0.02)
        assert out["fwd_15m"] > out["fwd_5m"]

    def test_a_rising_market_is_ADVERSE_to_a_sold_greater_strike(self):
        mod = self._mod()
        row, base = self._row("greater")
        out = mod.forward(row, self._rising(base))
        assert out["mae_bps"] > 0
        assert out["mfe_bps"] <= 0 or out["mfe_bps"] == pytest.approx(0.0, abs=1e-6)

    def test_a_rising_market_is_FAVOURABLE_to_a_sold_less_strike(self):
        # The orientation that a single sign error would invert for half the book.
        mod = self._mod()
        row, base = self._row("less")
        out = mod.forward(row, self._rising(base))
        assert out["mfe_bps"] > 0
        assert out["mae_bps"] <= 0 or out["mae_bps"] == pytest.approx(0.0, abs=1e-6)

    def test_a_falling_market_is_adverse_to_a_sold_less_strike(self):
        mod = self._mod()
        row, base = self._row("less")
        out = mod.forward(row, self._falling(base))
        assert out["mae_bps"] > 0

    def test_a_missing_offset_is_None_and_never_imputed(self):
        mod = self._mod()
        row, base = self._row("greater", mtc=30.0)
        sparse = {base: 100.0, base + 5 * 60: 101.0}   # nothing at +15m or +30m
        out = mod.forward(row, sparse)
        assert out["fwd_5m"] is not None
        assert out["fwd_15m"] is None
        assert out["fwd_close"] is None

    def test_a_missing_decision_close_yields_no_features_at_all(self):
        mod = self._mod()
        row, base = self._row("greater")
        assert mod.forward(row, {base + 999 * 60: 100.0})["has_t0"] is False

    def test_the_close_offset_follows_minutes_to_close(self):
        mod = self._mod()
        closes = self._rising(1_700_000_000 // 60 * 60, n=60)
        short, _ = self._row("greater", mtc=10.0)
        long_, _ = self._row("greater", mtc=30.0)
        assert mod.forward(long_, closes)["fwd_close"] > mod.forward(short, closes)["fwd_close"]

    def test_the_close_tolerance_accepts_a_gap_but_not_a_chasm(self):
        mod = self._mod()
        base = 1_700_000_000 // 60 * 60
        closes = {base: 100.0}
        assert mod._at(closes, base + 2 * 60) == 100.0     # within the 3-minute tolerance
        assert mod._at(closes, base + 30 * 60) is None     # far outside it

    @pytest.mark.parametrize("series,product", [
        ("KXETHD", "ETH"), ("KXBTCD", "BTC"), (None, "BTC")])
    def test_product_map(self, series, product):
        assert self._mod().product_of(series) == product


class TestSelectionStatistic:
    """The TRAIN statistic that freezes a configuration must be able to score every candidate.

    `|log(o/e)|` was undefined at o=0 and silently dropped exactly the configurations whose deep
    tail produced no hits — which is what a well-calibrated deep tail looks like on a short
    window. It froze a 30-day window while three 90-day candidates went unscored.
    """

    @staticmethod
    def _mod():
        import importlib.util
        import pathlib
        import sys as _sys

        path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "theta_tail_refit.py"
        spec = importlib.util.spec_from_file_location("theta_tail_refit", path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    def test_a_perfect_match_scores_zero(self):
        assert self._mod().poisson_deviance(10, 10.0) == pytest.approx(0.0)

    def test_zero_observed_is_SCORED_not_discarded(self):
        d = self._mod().poisson_deviance(0, 5.0)
        assert math.isfinite(d)
        assert d == pytest.approx(10.0)      # 2 * expected

    def test_it_penalises_over_and_under_prediction_alike(self):
        mod = self._mod()
        assert mod.poisson_deviance(20, 10.0) > 0
        assert mod.poisson_deviance(5, 10.0) > 0

    def test_a_closer_match_always_scores_lower(self):
        mod = self._mod()
        assert mod.poisson_deviance(11, 10.0) < mod.poisson_deviance(20, 10.0)
        assert mod.poisson_deviance(9, 10.0) < mod.poisson_deviance(2, 10.0)

    def test_no_expectation_means_no_score_rather_than_a_default(self):
        assert math.isnan(self._mod().poisson_deviance(3, 0.0))

    def test_coverage_gate_exists_and_is_strict(self):
        # Without it the sweep rewards a configuration that powers almost nothing: unnormalised
        # deviance falls with less data, and the quotes a thin configuration powers are the ones
        # carrying the most tail evidence. Observed for real in run `refit-7`, where a 5-day
        # window powering 5% of TRAIN scored "best" and was frozen.
        assert self._mod().MIN_POWERED_COVERAGE >= 0.9
