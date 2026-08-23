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

import datetime as _dt
import math
import random
import time

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

    def test_the_harness_block_builder_IS_the_models(self):
        """Both call `tailmodel.block_sample`. The harness used to hold a copy of the loop,
        kept in step by a test — and a copy kept in step by a test is still a copy."""
        mod = self._mod()
        base = 1_700_000_000 // 60 * 60
        closes = {base + i * 60: 100.0 * math.exp(i * 0.0001) for i in range(6000)}
        as_of = base + 5999 * 60

        cache = mod.FitCache({"BTC": closes, "ETH": {}}, 0.95, fit_days=3.0)
        from_harness, meta = cache._blocks("BTC", as_of, 35)
        from_model, meta_model = SpotModel(closes).block_sample(as_of, 35, window_days=3.0)
        assert from_harness == pytest.approx(from_model)
        assert meta == meta_model
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

    def test_excursions_are_withheld_when_the_path_is_incomplete(self):
        # An excursion is a statement about a PATH. Two surviving candle points do not make a
        # smaller excursion, they make a different quantity — so it is missing, not small.
        mod = self._mod()
        row, base = self._row("greater", mtc=30.0)
        sparse = {base: 100.0, base + 30 * 60: 101.0}      # 2 of 31 expected minutes
        out = mod.forward(row, sparse)
        assert out["mae_bps"] is None if "mae_bps" in out else True
        assert out.get("mae_bps") is None
        assert out["excursion_excluded"] == "path incomplete"
        assert out["path_completeness"] < 0.2

    def test_path_completeness_is_observed_over_expected(self):
        mod = self._mod()
        row, base = self._row("greater", mtc=30.0)
        out = mod.forward(row, self._rising(base, n=40))
        assert out["expected_path_minutes"] == 31
        assert out["path_completeness"] == pytest.approx(1.0, abs=0.05)

    def test_BETWEEN_strikes_are_excluded_from_directional_excursion(self):
        # From inside the band a move toward either edge is favourable; from outside a move
        # toward the band is adverse. One signed excursion cannot represent that, and the
        # earlier `max(up, -dn)` was wrong from inside and meaningless from outside.
        mod = self._mod()
        row, base = self._row("between", mtc=30.0)
        out = mod.forward(row, self._rising(base, n=40))
        assert out.get("mae_bps") is None
        assert out.get("mfe_bps") is None
        assert out["excursion_excluded"] == "non-directional strike type"
        # The forward RETURNS are still computed — only the orientation-dependent part is out.
        assert out["fwd_5m"] is not None

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
    """The TRAIN statistic that freezes a configuration.

    Three versions failed here, each found by running it:

    1. `|log(o/e)|` was undefined at zero observed and silently dropped every 90-day candidate.
    2. Raw Poisson deviance rewarded a configuration that powered almost nothing, because it is
       an absolute quantity and less data means a smaller sum — `refit-7` froze a window covering
       5.7% of quotes on that basis.
    3. Both were AGGREGATE counts over a set each configuration defined for itself
       (`p_new < 0.02`), so quotes crossed the objective boundary between configurations and the
       numbers being compared described different populations.

    Now: a proper per-prediction scoring rule on a population fixed by the market's own price.
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

    def test_log_loss_rewards_a_confident_correct_call(self):
        mod = self._mod()
        assert mod.log_loss(0.9, True) < mod.log_loss(0.6, True) < mod.log_loss(0.1, True)

    def test_log_loss_is_symmetric_between_the_outcomes(self):
        mod = self._mod()
        assert mod.log_loss(0.3, True) == pytest.approx(mod.log_loss(0.7, False))

    def test_a_certain_wrong_call_is_penalised_but_finite(self):
        # The eps clamp exists so one confident miss cannot make the whole score infinite and
        # silently disqualify a configuration on a single market.
        v = self._mod().log_loss(0.0, True)
        assert math.isfinite(v) and v > 10

    def test_both_rules_are_proper_the_truth_minimises_expected_score(self):
        # With a true rate of 0.3, no other forecast beats 0.3 in expectation. That is what
        # "proper" means, and it is why an aggregate count statistic was the wrong tool.
        mod = self._mod()

        def expected(rule, p, truth=0.3):
            return truth * rule(p, True) + (1 - truth) * rule(p, False)

        for rule in (mod.log_loss, mod.brier):
            best = expected(rule, 0.3)
            for p in (0.05, 0.15, 0.45, 0.6, 0.9):
                assert expected(rule, p) > best, (rule.__name__, p)

    def test_brier_is_bounded_where_log_loss_is_not(self):
        mod = self._mod()
        assert mod.brier(0.0, True) == pytest.approx(1.0)
        assert mod.log_loss(0.0, True) > 1.0

    def test_score_population_averages_per_prediction(self):
        mod = self._mod()
        rows = [{"p": 0.5, "yes_resolved": True}, {"p": 0.5, "yes_resolved": False}]
        out = mod.score_population(rows, "p")
        assert out["n"] == 2
        assert out["log_loss"] == pytest.approx(math.log(2))
        assert out["brier"] == pytest.approx(0.25)

    def test_an_empty_population_scores_nothing_rather_than_zero(self):
        assert self._mod().score_population([], "p") == {"n": 0}

    def test_the_common_population_is_defined_by_the_MARKET_not_the_model(self):
        # A model-defined population lets quotes cross the boundary between configurations, so
        # the numbers compared describe different sets. The ceiling is theta's own band.
        mod = self._mod()
        assert mod.COMMON_POPULATION_MAX_MID == 20.0

    def test_coverage_gate_exists_and_is_strict(self):
        # Without it the sweep rewards a configuration that powers almost nothing: unnormalised
        # scores fall with less data, and the quotes a thin configuration powers are the ones
        # carrying the most tail evidence. Observed for real in run `refit-7`.
        assert self._mod().MIN_POWERED_COVERAGE >= 0.9


class TestShadowUsesItsOwnWindow:
    """The live shadow must fit over `theta_spliced_fit_days`, not the incumbent's five days.

    An earlier version loaded ONE close set bounded by `theta_trail_days`, handed that object to
    the shadow, and asked it for `block_returns(window_days=90)`. A 5-day object cannot yield a
    90-day fit however it is asked — the result was ~205 blocks tagged `fit_days=90`, so the
    stored metadata described a window the fit never saw. These run a 90-day store through both
    paths and assert they diverge in exactly the right way.
    """

    @pytest.fixture(scope="class")
    def ninety_days(self):
        rng = random.Random(12)
        base = 1_700_000_000 // 60 * 60
        px, out = 65000.0, {}
        for i in range(90 * 1440):
            px *= math.exp(rng.gauss(0.0, 0.0006))
            out[base + i * 60] = px
        return out

    @pytest.fixture(scope="class")
    def five_days(self, ninety_days):
        cutoff = max(ninety_days) - 5 * 86400
        return {k: v for k, v in ninety_days.items() if k > cutoff}

    def test_the_incumbent_still_sees_only_five_days(self, ninety_days):
        # `returns` is bounded by the model's OWN trail, so a 90-day store does not widen it.
        incumbent = SpotModel(ninety_days, trail_days=5.0)
        n = len(incumbent.returns(max(ninety_days), 35))
        assert 7000 < n < 7300, n

    def test_the_shadow_window_yields_a_ninety_day_block_sample(self, ninety_days):
        shadow = SpotModel(ninety_days, trail_days=90.0)
        blocks = shadow.block_returns(max(ninety_days), 35, window_days=90.0)
        # 90 days is ~129,600 minutes; at h=35 that is ~3,700 non-overlapping blocks.
        assert 3500 < len(blocks) < 3800, len(blocks)

    def test_a_five_day_object_CANNOT_produce_a_ninety_day_sample(self, five_days):
        # The defect, pinned: asking for a wider window than the object holds silently returns
        # the narrow one, so the fix has to be a second LOAD, not a second argument.
        model = SpotModel(five_days, trail_days=5.0)
        blocks = model.block_returns(max(five_days), 35, window_days=90.0)
        assert len(blocks) < 300, len(blocks)

    def test_stored_fit_metadata_reports_the_ACTUAL_span_not_the_request(self, five_days):
        model = SpotModel(five_days, trail_days=5.0)
        blocks = model.block_returns(max(five_days), 35, window_days=90.0)
        actual = (max(model.closes) - min(model.closes)) / 86400.0
        m = tm.build(blocks, 35, fit_days=min(90.0, actual), requested_fit_days=90.0)
        assert m is not None
        assert m.requested_fit_days == 90.0
        assert m.fit_days < 6.0, "the actual span must not be reported as the request"
        assert m.describe()["requested_fit_days"] == 90.0

    def test_a_full_window_reports_matching_requested_and_actual(self, ninety_days):
        model = SpotModel(ninety_days, trail_days=90.0)
        blocks = model.block_returns(max(ninety_days), 35, window_days=90.0)
        actual = (max(model.closes) - min(model.closes)) / 86400.0
        m = tm.build(blocks, 35, fit_days=min(90.0, actual), requested_fit_days=90.0)
        assert m.fit_days == pytest.approx(m.requested_fit_days, abs=0.5)
        assert m.underpowered is False


class TestMarginalCoherence:
    """`zeta` and the severity must describe the SAME distribution.

    An earlier version paired a marginal per-block exceedance rate with a severity fitted to
    declustered cluster MAXIMA, which is the survival function of nothing.
    """

    @staticmethod
    def _clustered(n: int = 2000) -> list[float]:
        rng = random.Random(21)
        out = [rng.gauss(0.0, 0.002) for _ in range(n)]
        for start in (300, 900, 1500):
            for i in range(start, start + 8):
                out[i] = 0.03 + rng.random() * 0.01
        return out

    def test_zeta_is_the_marginal_per_block_exceedance_frequency(self):
        blocks = self._clustered()
        fit = tm._fit_side(blocks, 0.95)
        assert fit.exceedance == pytest.approx(fit.n_excess / len(blocks))

    def test_the_severity_is_fitted_to_marginal_excesses_not_cluster_maxima(self):
        blocks = self._clustered()
        fit = tm._fit_side(blocks, 0.95)
        u = tm._quantile(sorted(blocks), 0.95)
        marginal = tm.fit_gpd([v - u for v in blocks if v > u])
        clustered = tm.fit_gpd([c - u for c in tm.decluster(blocks, u)])
        assert (fit.scale, fit.xi) == pytest.approx((marginal[0], marginal[1]))
        # And the two really do differ, so the test is not vacuous.
        assert (marginal[0], marginal[1]) != pytest.approx((clustered[0], clustered[1]))

    def test_declustering_still_governs_POWER_only(self):
        blocks = self._clustered()
        fit = tm._fit_side(blocks, 0.95)
        assert fit.n_clusters < fit.n_excess          # clustering still bites
        assert fit.powered == (fit.n_clusters >= tm.MIN_TAIL_EXCEEDANCES_FOR_POWER)

    def test_the_survival_function_is_continuous_at_the_threshold(self):
        # A coherent splice means sf(u) equals the empirical exceedance rate there.
        blocks = self._clustered()
        m = tm.build(blocks, 35, tail_q=0.95)
        u = m.upper.threshold
        assert m.upper.sf(u) == pytest.approx(m.upper.exceedance)
        assert m.p_greater(u) == pytest.approx(m.upper.exceedance, rel=0.05)


class TestBlockCoverage:
    """A span is not completeness. `fit_days` measures the distance from the oldest stored
    minute to the newest; a window can reach ninety days back and be a third holes, and those
    holes are collector restarts and deploys — not random with respect to what the model is
    being asked to price."""

    @staticmethod
    def _closes(minutes: int, *, drop=()):
        base = 1_700_000_000 // 60 * 60
        out = {}
        for i in range(minutes):
            if any(lo <= i < hi for lo, hi in drop):
                continue
            out[base + i * 60] = 100.0 * math.exp(i * 1e-5)
        return out, base + (minutes - 1) * 60

    def test_a_complete_window_reports_full_coverage(self):
        closes, as_of = self._closes(4320)                    # 3 days
        blocks, meta = tm.block_sample(closes, as_of, 30, 3.0)
        assert meta["block_coverage"] > 0.99
        assert meta["max_gap_min"] == 0.0
        assert len(blocks) == meta["usable_blocks"]

    def test_a_gappy_window_reports_the_hole_the_span_hides(self):
        # Same first and last minute, so the SPAN is identical — a third of the middle is gone.
        full, as_of = self._closes(4320)
        gappy, _ = self._closes(4320, drop=((1000, 2400),))
        assert max(full) == max(gappy) and min(full) == min(gappy)
        _, m_full = tm.block_sample(full, as_of, 30, 3.0)
        _, m_gap = tm.block_sample(gappy, as_of, 30, 3.0)
        assert m_full["block_coverage"] > 0.99
        assert m_gap["block_coverage"] < 0.75
        assert m_gap["max_gap_min"] >= 1400

    def test_a_model_below_the_coverage_bar_is_not_emittable(self):
        rng = random.Random(5)
        blocks = [rng.gauss(0.0, 0.01) for _ in range(4000)]
        powered = tm.build(blocks, 30, fit_days=90.0, expected_blocks=len(blocks))
        starved = tm.build(blocks, 30, fit_days=90.0, expected_blocks=int(len(blocks) / 0.5))
        # Identical fits — identical tails, identical evidence. Only the window differs.
        assert powered.upper.n_clusters == starved.upper.n_clusters
        assert powered.powered_for("greater") and starved.powered_for("greater")
        assert powered.emittable_for("greater") is True
        assert starved.emittable_for("greater") is False
        assert starved.block_coverage == pytest.approx(0.5, abs=0.01)

    def test_coverage_defaults_to_complete_when_the_caller_does_not_measure_it(self):
        # Only the two real callers measure it; a bare build() must not be silently blocked.
        blocks = [0.001 * i for i in range(200)]
        m = tm.build(blocks, 30, fit_days=90.0)
        assert m.expected_blocks == len(blocks)
        assert m.block_coverage == 1.0


class TestRuntimeOfflineParity:
    """The live shadow and the offline validation harness must produce the SAME probability
    and the SAME fit metadata from the same candles.

    This is the requirement the earlier revisions failed in three separate ways, each silent:
    the runtime fitted at whatever integer minutes-to-close a market showed while the harness
    fitted on a 10..35 grid; the runtime used the module's default tail quantile while the
    harness used whichever one the sweep was scoring; and the block loop existed twice. A
    verdict about a model the runtime does not run is not a verdict about anything.
    """

    @staticmethod
    def _harness():
        import importlib.util
        import pathlib
        import sys as _sys

        path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "theta_tail_refit.py"
        spec = importlib.util.spec_from_file_location("theta_tail_refit_parity", path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    @pytest.fixture(scope="class")
    def candles(self):
        # A full frozen window, so the fit the runtime asks for is the fit it can build.
        rng = random.Random(77)
        base = 1_700_000_000 // 60 * 60
        px, out = 65000.0, {}
        for i in range(int(tm.FROZEN_FIT_DAYS) * 1440):
            px *= math.exp(rng.gauss(0.0, 0.0006))
            out[base + i * 60] = px
        return out

    @pytest.fixture
    def tracker(self, settings):
        from kalshi_bot.theta.tracker import ThetaTracker

        # The REAL defaults. Nothing about the frozen specification is set here: if a default
        # drifts away from what `refit-12` scored, these tests must fail rather than paper over
        # it by configuring the tracker into agreement.
        settings.theta_spliced_budget_ms = 0.0          # parity, not throughput — not the spec
        return ThetaTracker(object(), settings, spot_client=object())

    def test_the_shipped_defaults_ARE_the_frozen_specification(self, settings):
        """The runtime must fit the window the offline validation actually scored.

        This shipped at 90 days while the sweep was still open, so the shadow would have
        produced probabilities from a configuration no run had selected. It tracks the freeze in
        both directions: `refit-12` froze 30 under the derived labels and `refit-13` froze 90
        under Kalshi's own settled results, and the default follows the current one rather than
        whichever is convenient. The fit window is also NOT the retention window; the two were
        the same number once and every claim about what retention bought was false as a result.
        """
        assert settings.theta_spliced_fit_days == tm.FROZEN_FIT_DAYS == 90.0
        assert settings.theta_spliced_tail_q == tm.FROZEN_TAIL_Q == 0.90
        assert settings.theta_spot_retention_days == 100.0
        assert settings.theta_spot_retention_days > settings.theta_spliced_fit_days
        assert settings.theta_spliced_fit_days > settings.theta_trail_days

    @pytest.mark.parametrize("tte_min", [10.0, 17.0, 22.4, 31.0, 35.0])
    def test_probability_and_metadata_match_at_every_horizon(self, tracker, candles, tte_min):
        # A decision time deliberately NOT on the hour: both sides must anchor it the same way.
        as_of = max(candles) - 1_500
        model = SpotModel(candles, trail_days=tm.FROZEN_FIT_DAYS)
        runtime = tracker._spliced(model, "BTC", as_of, tte_min)
        assert runtime is not None

        mod = self._harness()
        cache = mod.FitCache({"BTC": candles, "ETH": {}}, tm.FROZEN_TAIL_Q,
                             fit_days=tm.FROZEN_FIT_DAYS)
        offline = cache.get("BTC", _dt.datetime.fromtimestamp(as_of, tz=_dt.timezone.utc),
                            tm.h_bucket(tte_min))
        assert offline is not None

        spot = model.spot_at(as_of)
        for strike_type, floor_k, cap_k in (("greater", spot * 1.004, None),
                                            ("less", None, spot * 0.996),
                                            ("between", spot * 0.998, spot * 1.002)):
            a = tm.p_yes(runtime, spot, strike_type, floor_k, cap_k)
            b = tm.p_yes(offline, spot, strike_type, floor_k, cap_k)
            assert a == pytest.approx(b, rel=1e-12), strike_type
        for field in ("n", "horizon_min", "tail_q", "expected_blocks", "max_gap_min"):
            assert getattr(runtime, field) == getattr(offline, field), field
        assert runtime.upper.xi == pytest.approx(offline.upper.xi, rel=1e-12)
        assert runtime.lower.xi == pytest.approx(offline.lower.xi, rel=1e-12)
        assert runtime.upper.n_clusters == offline.upper.n_clusters
        assert runtime.lower.n_clusters == offline.lower.n_clusters

    def test_the_runtime_snaps_the_horizon_onto_the_frozen_grid(self, tracker, candles):
        # 22.4 and 20.0 minutes are the same fit; 22.4 and 25.0 are too. Without this the
        # runtime fits a horizon the harness never scored.
        as_of = max(candles)
        model = SpotModel(candles, trail_days=tm.FROZEN_FIT_DAYS)
        assert tracker._spliced(model, "BTC", as_of, 22.4).horizon_min == 20
        assert tracker._spliced(model, "BTC", as_of, 24.0).horizon_min == 25
        assert tracker._spliced(model, "BTC", as_of, 99.0).horizon_min == 35

    def test_the_runtime_uses_the_CONFIGURED_tail_quantile(self, tracker, candles):
        as_of = max(candles)
        model = SpotModel(candles, trail_days=tm.FROZEN_FIT_DAYS)
        tracker.settings.theta_spliced_tail_q = 0.97
        tracker._spliced_cache.clear()
        assert tracker._spliced(model, "BTC", as_of, 30.0).tail_q == 0.97

    def test_the_shadow_stops_when_its_cycle_budget_is_spent(self, tracker, candles):
        as_of = max(candles)
        model = SpotModel(candles, trail_days=tm.FROZEN_FIT_DAYS)
        tracker.settings.theta_spliced_budget_ms = 1e-6   # exhausted by the first fit
        assert tracker._spliced(model, "BTC", as_of, 30.0) is not None
        tracker._spliced_cache.clear()
        # A gap in a research series is recoverable; a late quote is not.
        assert tracker._spliced(model, "BTC", as_of, 35.0) is None
        assert tracker._shadow_budget_hit is True

    def test_a_fit_is_reused_across_cycles_inside_one_hour(self, tracker, candles):
        # The saving that keeps the shadow off the scan's critical path: a 90-day window cannot
        # be moved by one 5-minute cycle, so refitting every cycle buys nothing.
        as_of = max(candles) // 3600 * 3600 + 120
        model = SpotModel(candles, trail_days=tm.FROZEN_FIT_DAYS)
        first = tracker._spliced(model, "BTC", as_of, 30.0)
        fits_after_first = tracker._shadow_fits
        # A later cycle in the same hour, and a REBUILT SpotModel: the cache must not key on
        # the object's identity, or every cycle would refit.
        again = tracker._spliced(SpotModel(candles, trail_days=tm.FROZEN_FIT_DAYS), "BTC", as_of + 600, 30.0)
        assert again is first
        assert tracker._shadow_fits == fits_after_first
        # The hour turns: a fresh fit.
        tracker._spliced(model, "BTC", as_of + 3600, 30.0)
        assert tracker._shadow_fits == fits_after_first + 1

    def test_the_shadow_close_set_is_held_for_the_refit_anchor(self, tracker, candles,
                                                              monkeypatch):
        """The shadow's dominant cost is loading ~130,000 closes per product. Reloading that
        every five minutes to feed a fit that is itself anchored to the hour is pure waste, and
        it is database work on the trading loop's thread."""
        import kalshi_bot.repository as repo
        from kalshi_bot.theta import tracker as trk

        calls = {"n": 0}

        def fake_load(_session, _product, _since, *, statement_timeout_ms=None):
            calls["n"] += 1
            calls["timeout"] = statement_timeout_ms
            return candles

        monkeypatch.setattr(repo, "load_spot_closes", fake_load)
        monkeypatch.setattr(trk.repo, "load_spot_closes", fake_load, raising=False)
        base = max(candles) // 3600 * 3600
        monkeypatch.setattr(trk.time, "time", lambda: base + 60)
        assert tracker._refresh_shadow_spot(None, "BTC") is not None
        assert calls["n"] == 1
        # Another cycle inside the same hour: no second load.
        monkeypatch.setattr(trk.time, "time", lambda: base + 900)
        tracker._refresh_shadow_spot(None, "BTC")
        assert calls["n"] == 1
        # The hour turns.
        monkeypatch.setattr(trk.time, "time", lambda: base + 3700)
        tracker._refresh_shadow_spot(None, "BTC")
        assert calls["n"] == 2

    def test_each_load_authorises_only_the_REMAINING_cycle_budget(self, tracker, candles,
                                                                  monkeypatch):
        """The dominant cost is the close-set load, and a budget that gates only fit time bounds
        the cheap half. Worse, an earlier version handed every load the CONFIGURED total, so two
        products could authorise two full budgets and authorisation was unbounded in the number
        of products.

        The invariant that matters is not the sum of authorisations — it is that at every load,
        `already_spent + authorised <= budget`.

        Note what this does and does not prove. It bounds what the DATABASE is authorised to
        spend; it is not a wall-clock ceiling on the cycle. The statement timeout ends the
        statement, and client-side row transfer plus `SpotModel` construction run afterwards on
        this thread with nothing to interrupt them, so a load already in flight can still overrun.
        The budget stops the NEXT unit of work. See `_refresh_shadow_spot` and
        `docs/RESEARCH_THETA_REMEDIATION.md` §5.4.
        """
        from kalshi_bot.theta import tracker as trk

        seen: list[tuple[float, int | None]] = []

        def slow_load(_session, _product, _since, *, statement_timeout_ms=None):
            seen.append((tracker._shadow_ms, statement_timeout_ms))
            time.sleep(0.02)
            return candles

        monkeypatch.setattr(trk.repo, "load_spot_closes", slow_load, raising=False)
        budget = 400.0
        tracker.settings.theta_spliced_budget_ms = budget
        base = max(candles) // 3600 * 3600
        monkeypatch.setattr(trk.time, "time", lambda: base + 60)

        assert tracker._refresh_shadow_spot(None, "BTC") is not None
        assert tracker._refresh_shadow_spot(None, "ETH") is not None
        assert len(seen) == 2

        spent_before_first, authorised_first = seen[0]
        spent_before_second, authorised_second = seen[1]
        # The first load is charged to the budget, so the second sees less of it.
        assert spent_before_first == 0.0
        assert spent_before_second >= 20.0
        assert authorised_second < authorised_first
        # THE invariant: no load can authorise more than what is left.
        for spent, authorised in seen:
            assert spent + authorised <= budget + 1e-6
        # ...and the load really is charged, not just the fits.
        assert tracker._shadow_load_ms >= 40.0
        assert tracker._shadow_ms == tracker._shadow_load_ms

    def test_a_load_is_not_started_on_a_scrap_of_budget(self, tracker, candles, monkeypatch):
        """Authorising the database for a few milliseconds cannot finish a ~43,000-row read, so
        it would spend the remainder to produce nothing and still risk an abort."""
        from kalshi_bot.theta import tracker as trk

        calls = {"n": 0}

        def load(_session, _product, _since, *, statement_timeout_ms=None):
            calls["n"] += 1
            return candles

        monkeypatch.setattr(trk.repo, "load_spot_closes", load, raising=False)
        tracker.settings.theta_spliced_budget_ms = 100.0
        tracker._shadow_ms = 100.0 - trk.MIN_LOAD_BUDGET_MS / 2.0    # under the floor
        assert tracker._refresh_shadow_spot(None, "BTC") is None
        assert calls["n"] == 0
        assert tracker._shadow_budget_hit is True

    def test_the_budget_is_SOFT_work_in_flight_can_overrun_it(self, tracker, candles,
                                                              monkeypatch):
        """The correction, pinned as a property rather than left as a comment.

        Two earlier revisions described this budget as bounding total elapsed time. It does not.
        The PostgreSQL statement timeout ends the STATEMENT; client-side row transfer and
        `SpotModel` construction run afterwards on this thread and nothing interrupts them. A
        load that starts inside its budget can therefore finish outside it.

        The loader here spends real wall-clock AFTER the notional statement returns, which is
        exactly the uninterruptible client-side work production does. If someone later makes the
        budget a genuine hard ceiling — by moving the load off-thread — this test SHOULD fail,
        and its failure is the signal to update §5.4 rather than to weaken the assertion.
        """
        from kalshi_bot.theta import tracker as trk

        def overrunning_load(_session, _product, _since, *, statement_timeout_ms=None):
            time.sleep(0.15)          # 150 ms of client-side work inside a 50 ms budget
            return candles

        monkeypatch.setattr(trk.repo, "load_spot_closes", overrunning_load, raising=False)
        tracker.settings.theta_spliced_budget_ms = 50.0
        tracker._refresh_shadow_spot(None, "BTC")

        assert tracker._shadow_ms > 50.0, (
            "the shadow somehow stayed inside its budget; if the load is now interruptible, "
            "§5.4's soft-budget language needs updating"
        )
        # And the guarantee that DOES hold: no further work is started once it is spent.
        assert tracker._over_budget() is True

    def test_an_unbudgeted_tracker_authorises_no_timeout(self, tracker, candles, monkeypatch):
        from kalshi_bot.theta import tracker as trk

        seen = {}

        def load(_session, _product, _since, *, statement_timeout_ms=None):
            seen["timeout"] = statement_timeout_ms
            return candles

        monkeypatch.setattr(trk.repo, "load_spot_closes", load, raising=False)
        tracker.settings.theta_spliced_budget_ms = 0.0
        assert tracker._refresh_shadow_spot(None, "BTC") is not None
        assert seen["timeout"] is None
        assert tracker._shadow_remaining_ms() == float("inf")

    def test_a_failed_shadow_load_does_not_break_the_cycle(self, tracker, monkeypatch):
        from kalshi_bot.theta import tracker as trk

        def boom(*_a, **_k):
            raise RuntimeError("statement timeout")

        monkeypatch.setattr(trk.repo, "load_spot_closes", boom, raising=False)
        assert tracker._refresh_shadow_spot(None, "BTC") is None
        assert tracker._shadow_loads == 1

    def test_products_do_not_share_a_fit(self, tracker, candles):
        as_of = max(candles)
        btc = SpotModel(candles, trail_days=tm.FROZEN_FIT_DAYS)
        eth = SpotModel({k: v * 0.03 for k, v in candles.items()}, trail_days=tm.FROZEN_FIT_DAYS)
        a = tracker._spliced(btc, "BTC", as_of, 30.0)
        b = tracker._spliced(eth, "ETH", as_of, 30.0)
        assert a is not b


class TestRealLabelPath:
    """`scripts/theta_tail_refit.py --labels kalshi` — scoring against Kalshi's own settled
    results rather than the last-snapshot-spot derivation.

    The derivation was a proxy from the start, and under a zero-failure-valid clustered bound it
    does not clear its own 97% agreement bar. The answer to a proxy that fails its bar is not a
    looser bar; it is the real thing, which Kalshi's public endpoint serves for 100% of this
    universe.
    """

    @staticmethod
    def _mod():
        import importlib.util
        import pathlib as _p
        import sys as _sys

        path = _p.Path(__file__).resolve().parent.parent / "scripts" / "theta_tail_refit.py"
        spec = importlib.util.spec_from_file_location("theta_tail_refit_labels", path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _rows(n=40, flip=0):
        return [{"ticker": f"KXBTCD-T{i}", "event": f"E{i // 8}",
                 "yes_resolved": (i >= flip)} for i in range(n)]

    def test_real_results_replace_the_derived_label_and_keep_the_old_one(self, monkeypatch):
        mod = self._mod()
        rows = self._rows()
        # Kalshi says the opposite on exactly one market.
        real = {r["ticker"]: r["yes_resolved"] for r in rows}
        real["KXBTCD-T3"] = not real["KXBTCD-T3"]
        monkeypatch.setattr(mod, "fetch_kalshi_results", lambda ev, **k: (real, len(ev)))
        kept, ok = mod.real_labels(rows)
        assert ok is True
        assert len(kept) == len(rows)
        flipped = next(r for r in kept if r["ticker"] == "KXBTCD-T3")
        assert flipped["yes_resolved"] == real["KXBTCD-T3"]
        assert flipped["derived_resolved"] != flipped["yes_resolved"]

    def test_markets_without_a_real_result_are_dropped_not_guessed(self, monkeypatch):
        mod = self._mod()
        rows = self._rows()
        partial = {r["ticker"]: r["yes_resolved"] for r in rows[:30]}
        monkeypatch.setattr(mod, "fetch_kalshi_results", lambda ev, **k: (partial, len(ev)))
        kept, ok = mod.real_labels(rows)
        assert len(kept) == 30
        assert ok is False, "30/40 is below the 90% coverage bar"

    def test_no_results_at_all_blocks(self, monkeypatch):
        mod = self._mod()
        monkeypatch.setattr(mod, "fetch_kalshi_results", lambda ev, **k: ({}, 0))
        kept, ok = mod.real_labels(self._rows())
        assert kept == [] and ok is False

    def test_the_derived_gate_now_tests_the_LOWER_bound(self, monkeypatch):
        # An all-agreeing overlap used to "PASS" on a bootstrap [1, 1]. With an exact clustered
        # bound, a few dozen events cannot clear 97% however perfectly they agree.
        mod = self._mod()
        rows = self._rows(n=200)
        for r in rows:
            r.update({"strike_type": "greater", "floor": 100.0, "cap": None,
                      "final": 200.0, "final_mtc": 1.0, "product": "BTC"})
        truth = {r["ticker"]: r["yes_resolved"] for r in rows}       # perfect agreement
        scale = {"BTC": {1: (1.0, 10_000)}}
        kept, ok = mod.label_quality(rows, truth, scale)
        assert len(kept) == len(rows), "every strike is 99 sigma from spot; none is ambiguous"
        assert ok is False, "25 events cannot put a lower bound above 97%"
