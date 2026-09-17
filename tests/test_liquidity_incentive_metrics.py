"""The Experiment OS metric providers for the liquidity-incentive shadow instrument (WS-020).

Two scoping rules carry the weight here, and both exist to stop a number being read as
something it is not: these metrics are computable only at `deployment_kind='probe'`, and only
experiment-wide. Everything else is arithmetic over the `incentive_*` tables.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot import models as m
from kalshi_bot.experiment_os.metrics import INCENTIVE_METRICS, MetricScope, compute_metric

START = datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc)
END = START + timedelta(days=1)
OUTSIDE = START - timedelta(days=3)


def _scope(*, kind="probe", arm=None, start=START, end=END):
    return MetricScope(
        experiment_key="liquidity-incentive-mm", version=1, epoch_number=1, arm_key=arm,
        deployment_kind=kind, strategy_tags=(), deployment_keys=(),
        window_start=start, window_end=end, platform_snapshot_fingerprint="f" * 32,
    )


def _v(session, key, **kw):
    return compute_metric(session, key, _scope(**kw))


# ------------------------------------------------------------------ scoping


@pytest.mark.parametrize("key", sorted(INCENTIVE_METRICS))
def test_every_incentive_metric_refuses_a_non_probe_scope(xos_session, key):
    for kind in ("paper", "live", "paper_twin"):
        mv = _v(xos_session, key, kind=kind)
        assert mv.missing and "probe" in mv.reason
        assert mv.provenance["addressing_error"] is True


@pytest.mark.parametrize("key", sorted(INCENTIVE_METRICS))
def test_every_incentive_metric_refuses_an_arm_scoped_read(xos_session, key):
    mv = _v(xos_session, key, arm="limm1side")
    assert mv.missing and "experiment-wide" in mv.reason
    assert mv.provenance["addressing_error"] is True


# ------------------------------------------------------------------ discovery


def _cycle(session, at, *, errors=0):
    session.add(m.IncentiveDiscoveryCycle(started_at=at, errors=errors, programs_listed=3))
    session.flush()


def test_discovery_counts_and_error_rate(xos_session):
    for i in range(4):
        _cycle(xos_session, START + timedelta(hours=i), errors=1 if i == 3 else 0)
    _cycle(xos_session, OUTSIDE, errors=1)          # outside the window: never counted
    assert _v(xos_session, "incentive_discovery_cycles").value == 4.0
    assert _v(xos_session, "incentive_discovery_error_pct").value == 25.0


def test_error_rate_is_undefined_rather_than_zero_on_an_empty_window(xos_session):
    """Missing is not zero. A 0% error rate on a window with no polls would read as a healthy
    instrument that had in fact recorded nothing at all."""
    mv = _v(xos_session, "incentive_discovery_error_pct")
    assert mv.value is None and "no discovery polls" in mv.reason
    # A count, by contrast, has a meaningful zero.
    assert _v(xos_session, "incentive_discovery_cycles").value == 0.0


# ------------------------------------------------------------------ programs


def _program(session, pid, *, first, last, ticker="KXA-1"):
    session.add(m.IncentiveProgram(
        program_id=pid, market_ticker=ticker, incentive_type="liquidity",
        terms_hash=f"h{pid}{first}", first_seen_at=first, last_seen_at=last))
    session.flush()


def test_programs_observed_counts_distinct_ids_overlapping_the_window(xos_session):
    # Two TERMS versions of one programme are one programme.
    _program(xos_session, "p1", first=START, last=START + timedelta(hours=3))
    _program(xos_session, "p1", first=START + timedelta(hours=3), last=END)
    _program(xos_session, "p2", first=START - timedelta(days=5), last=START + timedelta(hours=1))
    _program(xos_session, "p3", first=OUTSIDE, last=OUTSIDE)       # ended before the window
    assert _v(xos_session, "incentive_programs_observed").value == 2.0


# ------------------------------------------------------------------ shadow outcomes


def _outcome(session, *, ended, outcome, model="conservative", adverse=None, qty=None):
    session.add(m.IncentiveShadowOutcome(
        quote_id=hash((ended, outcome, model, adverse)) % 10**8, market_ticker="KXA-1",
        policy="break_even", capital_tier_usd=100, fill_model=model,
        placed_at=ended - timedelta(minutes=5), ended_at=ended, outcome=outcome,
        yes_filled_qty=0, no_filled_qty=0, matched_pairs=0,
        single_leg_max_adverse_usd=adverse, single_leg_qty=qty))
    session.flush()


def test_single_leg_rate_counts_partials_and_only_the_conservative_model(xos_session):
    at = START + timedelta(hours=1)
    _outcome(xos_session, ended=at, outcome="both_filled")
    _outcome(xos_session, ended=at, outcome="neither_filled")
    _outcome(xos_session, ended=at, outcome="yes_only")
    _outcome(xos_session, ended=at, outcome="partial_no")
    # The optimistic model's rows are never pooled with the conservative model's.
    _outcome(xos_session, ended=at, outcome="yes_only", model="optimistic")
    assert _v(xos_session, "incentive_shadow_outcomes").value == 4.0
    assert _v(xos_session, "incentive_shadow_single_leg_pct").value == 50.0


def test_max_adverse_is_per_contract_and_absolute(xos_session):
    at = START + timedelta(hours=1)
    _outcome(xos_session, ended=at, outcome="yes_only", adverse=-0.18, qty=2)   # 9c/contract
    _outcome(xos_session, ended=at, outcome="no_only", adverse=-0.11, qty=1)    # 11c/contract
    mv = _v(xos_session, "incentive_shadow_max_adverse_cents_per_contract")
    assert mv.value == pytest.approx(11.0)
    assert mv.n == 2


def test_max_adverse_is_undefined_rather_than_zero_without_single_leg_exposure(xos_session):
    _outcome(xos_session, ended=START + timedelta(hours=1), outcome="both_filled")
    mv = _v(xos_session, "incentive_shadow_max_adverse_cents_per_contract")
    assert mv.value is None and "no single-leg exposure" in mv.reason


# ------------------------------------------------------------------ collector health


def test_collector_error_events_count_missing_data_not_lifecycle(xos_session):
    at = START + timedelta(hours=1)
    for kind in ("seq_gap", "disconnected", "ws_error"):
        xos_session.add(m.IncentiveCollectorEvent(at=at, kind=kind))
    for kind in ("thread_started", "connected", "subscribed", "settled"):
        xos_session.add(m.IncentiveCollectorEvent(at=at, kind=kind))
    xos_session.flush()
    assert _v(xos_session, "incentive_collector_error_events").value == 3.0


def test_shadow_quotes_are_windowed_on_placement(xos_session):
    for at in (START + timedelta(hours=1), END - timedelta(minutes=1), OUTSIDE):
        xos_session.add(m.IncentiveShadowQuote(
            market_ticker="KXA-1", policy="break_even", capital_tier_usd=100, placed_at=at,
            yes_bid=20, no_bid=75, yes_bid_yes_scale=20, no_bid_yes_scale=25,
            qty_per_side=1, pair_cost_cents=95))
    xos_session.flush()
    mv = _v(xos_session, "incentive_shadow_quotes")
    assert mv.value == 2.0 and mv.provenance["window_basis"] == "placed_at"
