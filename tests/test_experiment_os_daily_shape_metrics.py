"""`settled_days` / `daily_pnl_stability` — the unit of evidence for a DRAWDOWN control.

A concentration cap trades total return for smoothness by construction, so a mean-per-trade
gate cannot decide one. On the mmsell cheap band the measured per-trade standard deviation is
$0.2343, which puts an 80%-power test of a +0.30c difference at ~95,700 settled trades per arm —
years at current flow (docs/MMSELL_CORRELATION_CAP.md). The daily series is enormously better
powered on the same evidence, which is what these two providers exist to expose.

What must never happen, pinned here:

  * the ratio computed as raw dispersion. `daily_pnl_stability` is mean/sd deliberately: a
    capped book takes far fewer trades, and ANY book that trades less has a lower daily sd, so
    a gate on sd alone would pay a cap for doing nothing but trading less.
  * a day the book settled nothing counted as a flat day. It is not a day.
  * a confident number returned where the statistic is undefined (fewer than two days, or zero
    variance) — a gate clause could pass on it.
  * the bucket keyed off a book-specific settlement table. The daily series has to mean the
    same thing for every experiment that reads it, so it buckets on `closed_at`.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from kalshi_bot.experiment_os.metrics import MetricScope, compute_metric
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
_SEQ = itertools.count()


def _scope(tags=("Gmmsell2",)):
    return MetricScope(
        experiment_key="mmsell-correlation-cap", version=1, epoch_number=1,
        arm_key="corr_cap_all", deployment_kind="paper", strategy_tags=tuple(tags),
        deployment_keys=(), window_start=T0, window_end=T0 + timedelta(days=60),
        platform_snapshot_fingerprint="f" * 64,
    )


def _trade(s, *, day, pnl, tag="Gmmsell2", status="settled"):
    s.add(PaperTrade(
        market_ticker=f"KX-{next(_SEQ)}", strategy=tag, status=status, side="no",
        pnl=pnl, quantity=1,
        created_at=T0 + timedelta(hours=1),
        closed_at=T0 + timedelta(days=day, hours=12),
    ))


def _days(s):
    return compute_metric(s, "settled_days", _scope())


def _stability(s):
    return compute_metric(s, "daily_pnl_stability", _scope())


def test_days_count_distinct_close_dates_not_trades(xos_session):
    s = xos_session
    for pnl in (0.05, 0.06, 0.07):
        _trade(s, day=1, pnl=pnl)
    _trade(s, day=2, pnl=0.05)
    s.commit()
    assert _days(s).value == 2.0        # four trades, two days


def test_stability_is_mean_over_sd_of_the_daily_series(xos_session):
    s = xos_session
    # daily totals: +1, +2, +3 -> mean 2.0, sample sd 1.0
    _trade(s, day=1, pnl=1.0)
    _trade(s, day=2, pnl=2.0)
    _trade(s, day=3, pnl=3.0)
    s.commit()
    mv = _stability(s)
    assert mv.value == 2.0 and mv.n == 3 and mv.missing is False


def test_stability_rewards_smoothness_not_merely_trading_less(xos_session):
    """The whole reason the metric is a ratio. A book with HALF the daily mean and half the
    daily sd is exactly as good, and must score the same — a raw-sd gate would call it better."""
    s = xos_session
    for day, pnl in ((1, 1.0), (2, 2.0), (3, 3.0)):
        _trade(s, day=day, pnl=pnl)
    for day, pnl in ((1, 0.5), (2, 1.0), (3, 1.5)):
        _trade(s, day=day, pnl=pnl, tag="Ghalf")
    s.commit()
    full = compute_metric(s, "daily_pnl_stability", _scope())
    half = compute_metric(s, "daily_pnl_stability", _scope(tags=("Ghalf",)))
    assert full.value == half.value


def test_a_day_with_no_settlement_is_absent_not_a_zero(xos_session):
    """A gap in the calendar is not a flat day. Counting it as 0.0 would drag the mean down and
    inflate the variance of any book that simply does not settle every day."""
    s = xos_session
    _trade(s, day=1, pnl=1.0)
    _trade(s, day=9, pnl=3.0)          # a week with nothing in between
    s.commit()
    assert _days(s).value == 2.0
    mv = _stability(s)
    assert mv.value == 1.4142          # mean 2.0 / sd sqrt(2), over TWO points not eight
    assert mv.n == 2


def test_undefined_on_a_single_day(xos_session):
    s = xos_session
    _trade(s, day=1, pnl=1.0)
    s.commit()
    mv = _stability(s)
    assert mv.value is None and "undefined" in (mv.reason or "")
    assert _days(s).value == 1.0       # the count is still perfectly well defined


def test_undefined_on_zero_variance(xos_session):
    """Two identical days give sd=0. Returning a huge number (or 0) here would let a gate pass
    on an artefact of a two-day sample."""
    s = xos_session
    _trade(s, day=1, pnl=1.0)
    _trade(s, day=2, pnl=1.0)
    s.commit()
    mv = _stability(s)
    assert mv.value is None and "variance" in (mv.reason or "")


def test_only_settled_trades_of_this_book_count(xos_session):
    s = xos_session
    _trade(s, day=1, pnl=1.0)
    _trade(s, day=2, pnl=2.0)
    _trade(s, day=3, pnl=3.0)
    _trade(s, day=4, pnl=99.0, status="open")        # not settled
    _trade(s, day=5, pnl=99.0, tag="mmsell10")       # a different book
    s.commit()
    assert _days(s).value == 3.0
    assert _stability(s).value == 2.0


def test_delta_form_pairs_two_books(xos_session):
    """The gate reads `delta.daily_pnl_stability`, so the paired form has to resolve."""
    s = xos_session
    for day, pnl in ((1, 1.0), (2, 2.0), (3, 3.0)):
        _trade(s, day=day, pnl=pnl)                  # stability 2.0
    for day, pnl in ((1, 1.0), (2, 2.0), (3, 6.0)):
        _trade(s, day=day, pnl=pnl, tag="mmsell10")  # mean 3, sd 2.6458 -> 1.1339
    s.commit()
    mv = compute_metric(s, "delta.daily_pnl_stability", _scope())
    assert mv is not None
