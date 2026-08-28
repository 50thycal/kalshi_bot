"""The keep/stop providers a live canary is judged on beyond its headline P&L.

`live_settled_contracts` / `live_cents_per_contract` / `twin_live_winrate_gap_pp`
answer "did it make money and did paper model it". They do not answer the
questions that decide whether a canary is SAFE to keep running: is it actually
getting filled, how much real money is committed right now, how bad was the worst
outcome, how often did the tail land, and which risk gate is doing the stopping.

Those five are pinned here, and one property binds all of them: **the live
addressing rule is obeyed, never repaired**. Every provider below returns MISSING
with the mismatch named when asked at `deployment_kind="paper"`. That is the
defect two imported live-canary gates already carry (clauses defaulting to
`paper` on epochs holding only live + paper_twin), and a provider that inferred
"they probably meant live" would let a keep/stop gate decide on evidence its
registered contract never asked for.

The second property is missing-is-not-zero, and it matters in opposite directions
per metric: a fill rate of 0% and "no orders reached the venue" are different
findings; a worst-loss of $0.00 over a settled book is real, but $0.00 over an
empty one is not.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os.metrics import MetricScope, compute_metric
from kalshi_bot.models import Fill, LiveOrder, LivePaperParityEvent, Position

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
_SEQ = itertools.count()

CANARY_METRICS = (
    "live_fill_rate_pct",
    "live_open_exposure_usd",
    "live_max_realized_loss_usd",
    "live_tail_loss_markets",
    "live_blocked_entries",
)


def _scope(*, kind="live", tags=("Lmm10c",), arm="mmsell10", deployments=()):
    return MetricScope(
        experiment_key="mmsell-price-ceiling", version=2, epoch_number=2,
        arm_key=arm, deployment_kind=kind, strategy_tags=tuple(tags),
        deployment_keys=tuple(deployments), window_start=T0,
        window_end=T0 + timedelta(days=30),
        platform_snapshot_fingerprint="f" * 64,
    )


def _order(s, *, tag="Lmm10c", ticker=None, qty=1, status="filled", price=93,
           filled=None, at=None):
    """One entry buy, with an optional fill of `filled` contracts."""
    ticker = ticker or f"MKT-{next(_SEQ)}"
    oid = f"ord-{next(_SEQ)}"
    s.add(LiveOrder(kalshi_order_id=oid, market_ticker=ticker, strategy=tag,
                    action="buy", side="no", quantity=qty, limit_price=price,
                    status=status, created_at=at or (T0 + timedelta(hours=1))))
    if filled:
        s.add(Fill(kalshi_fill_id=f"f-{next(_SEQ)}", kalshi_order_id=oid,
                   market_ticker=ticker, action="buy", quantity=filled, price=price,
                   filled_at=at or (T0 + timedelta(hours=1))))
    return ticker


def _settled(s, *, tag="Lmm10c", realized, contracts=1):
    """A live market that has closed with a realized P&L (dollars)."""
    ticker = _order(s, tag=tag, qty=contracts, filled=contracts)
    s.add(Position(market_ticker=ticker, captured_at=T0 + timedelta(days=2),
                   quantity=0, realized_pnl=realized))
    return ticker


def _parity(s, *, twin_outcome, live_outcome, live_tag="Lmm10c",
            twin_tag="Lmm10c_pt", at=None):
    s.add(LivePaperParityEvent(
        recorded_at=at or (T0 + timedelta(hours=2)), twin_tag=twin_tag,
        live_tag=live_tag, market_ticker=f"C-{next(_SEQ)}",
        twin_outcome=twin_outcome, live_outcome=live_outcome))


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", CANARY_METRICS)
@pytest.mark.parametrize("kind", ["paper", "paper_twin"])
def test_canary_metric_refuses_a_non_live_scope(xos_session, key, kind):
    """MISSING with the mismatch named — never a substituted deployment kind.

    Both wrong kinds are checked because they fail for different reasons: `paper`
    is the malformed-gate default, and `paper_twin` is the plausible-looking
    mistake (the twin is right there in the same epoch, and it even has orders'
    worth of paper rows to answer from)."""
    s = xos_session
    _settled(s, realized=-1.5)
    _parity(s, twin_outcome="opened", live_outcome="gate:open_cap")
    s.commit()

    mv = compute_metric(s, key, _scope(kind=kind))
    assert mv.missing is True
    assert mv.value is None
    assert "deployment_kind='live'" in mv.reason
    assert repr(kind) in mv.reason
    assert mv.provenance["addressing_error"] is True


# ---------------------------------------------------------------------------
# live_fill_rate_pct
# ---------------------------------------------------------------------------


def test_fill_rate_counts_contracts_not_orders(xos_session):
    """Two orders for 5 contracts each; one fills fully, one fills 1 of 5.

    Per ORDER that reads 50%. Per CONTRACT it is 6/10 = 60%, and the contract is
    the unit the P&L is denominated in."""
    s = xos_session
    _order(s, qty=5, filled=5, status="filled")
    _order(s, qty=5, filled=1, status="canceled")
    s.commit()

    mv = compute_metric(s, "live_fill_rate_pct", _scope())
    assert mv.value == pytest.approx(60.0)
    assert mv.provenance["ordered_contracts"] == 10
    assert mv.provenance["filled_contracts"] == 6


def test_fill_rate_excludes_orders_that_never_reached_the_venue(xos_session):
    """A rejected send did not FAIL to fill — it was never given the chance.

    Counting it would blame the maker's queue position for our own bad order
    shape, and would move the fill rate every time the venue changed a validation
    rule."""
    s = xos_session
    _order(s, qty=2, filled=2, status="filled")
    _order(s, qty=8, status="rejected")
    _order(s, qty=8, status="error")
    s.commit()

    mv = compute_metric(s, "live_fill_rate_pct", _scope())
    assert mv.value == pytest.approx(100.0)
    assert mv.provenance["ordered_contracts"] == 2
    assert mv.provenance["excluded_never_sent"] == 2


def test_fill_rate_excludes_indeterminate_orders_from_both_sides(xos_session):
    """`unknown` / `pending` mean we do not yet know whether Kalshi has it.

    Reconcile resolves them later. Guessing either way here would make the number
    move on bookkeeping rather than on execution."""
    s = xos_session
    _order(s, qty=4, filled=2, status="filled")
    _order(s, qty=6, status="unknown")
    _order(s, qty=6, status="pending")
    s.commit()

    mv = compute_metric(s, "live_fill_rate_pct", _scope())
    assert mv.provenance["ordered_contracts"] == 4
    assert mv.provenance["excluded_indeterminate"] == 2
    assert mv.value == pytest.approx(50.0)


def test_fill_rate_with_nothing_sent_is_undefined_not_zero(xos_session):
    """0% says "we get no fills"; undefined says "we placed nothing". A keep/stop
    clause reading `>= 40` must trip on the first and abstain on the second."""
    s = xos_session
    _order(s, qty=3, status="rejected")
    s.commit()

    mv = compute_metric(s, "live_fill_rate_pct", _scope())
    assert mv.value is None
    assert mv.missing is False          # structurally empty, not unanswerable
    assert "never sent" in mv.reason


def test_fill_rate_ignores_exit_sells(xos_session):
    """A position is entered by buys and closed by sells, and both produce fills.
    The quantity under test is the RESTING MAKER'S ENTRY fill rate."""
    s = xos_session
    ticker = _order(s, qty=4, filled=1)
    s.add(LiveOrder(kalshi_order_id="sell-1", market_ticker=ticker,
                    strategy="Lmm10c", action="sell", side="no", quantity=4,
                    limit_price=97, status="filled",
                    created_at=T0 + timedelta(days=1)))
    s.add(Fill(kalshi_fill_id="sf-1", kalshi_order_id="sell-1",
               market_ticker=ticker, action="sell", quantity=4, price=97,
               filled_at=T0 + timedelta(days=1)))
    s.commit()

    mv = compute_metric(s, "live_fill_rate_pct", _scope())
    assert mv.provenance["ordered_contracts"] == 4
    assert mv.provenance["filled_contracts"] == 1
    assert mv.value == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# live_open_exposure_usd
# ---------------------------------------------------------------------------


def test_open_exposure_counts_resting_orders_and_held_positions(xos_session):
    """Both halves, because only one of them used to be counted.

    A resting order is money that COULD be committed; a filled position is money
    that already is. On 2026-08-20, mid stand-down, a resting-only reading showed
    "$0.00 at risk" while 25 open positions held $43.04."""
    s = xos_session
    _order(s, qty=2, price=95, status="resting")          # $1.90 notional
    held = _order(s, qty=3, price=90, filled=3)
    s.add(Position(market_ticker=held, captured_at=T0 + timedelta(days=1),
                   quantity=3, market_exposure=2.70, realized_pnl=None))
    s.commit()

    mv = compute_metric(s, "live_open_exposure_usd", _scope())
    assert mv.value == pytest.approx(4.60)
    assert mv.provenance["exposure"]["notional_usd"] == pytest.approx(1.90)
    assert mv.provenance["exposure"]["position_usd"] == pytest.approx(2.70)
    assert mv.provenance["exposure"]["open_positions"] == 1


def test_open_exposure_survives_a_stand_down(xos_session):
    """The case the metric exists for: every resting order drained, positions still
    open. The answer must be the held money, not zero."""
    s = xos_session
    held = _order(s, qty=4, price=92, filled=4, status="filled")
    s.add(Position(market_ticker=held, captured_at=T0 + timedelta(days=1),
                   quantity=4, market_exposure=3.68, realized_pnl=None))
    s.commit()

    mv = compute_metric(s, "live_open_exposure_usd", _scope())
    assert mv.provenance["exposure"]["open_orders"] == 0
    assert mv.value == pytest.approx(3.68)


def test_open_exposure_uses_the_newest_position_snapshot(xos_session):
    """`positions` is append-only: a stale row showing an open quantity would keep
    reporting exposure on a market that has already settled."""
    s = xos_session
    t = _order(s, qty=2, price=94, filled=2, status="filled")
    s.add(Position(market_ticker=t, captured_at=T0 + timedelta(days=1),
                   quantity=2, market_exposure=1.88))
    s.add(Position(market_ticker=t, captured_at=T0 + timedelta(days=3),
                   quantity=0, market_exposure=0.0, realized_pnl=0.12))
    s.commit()

    mv = compute_metric(s, "live_open_exposure_usd", _scope())
    assert mv.value == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# live_max_realized_loss_usd / live_tail_loss_markets
# ---------------------------------------------------------------------------


def test_worst_loss_is_a_magnitude_over_settled_markets(xos_session):
    s = xos_session
    _settled(s, realized=0.07)
    _settled(s, realized=-0.93)
    _settled(s, realized=-0.41)
    s.commit()

    mv = compute_metric(s, "live_max_realized_loss_usd", _scope())
    assert mv.value == pytest.approx(0.93)
    assert mv.provenance["losing_markets"] == 2


def test_worst_loss_over_an_all_winning_book_is_zero_not_missing(xos_session):
    """Nothing lost is a real answer, and a `<= $X` stop should pass on it."""
    s = xos_session
    _settled(s, realized=0.06)
    s.commit()

    mv = compute_metric(s, "live_max_realized_loss_usd", _scope())
    assert mv.value == pytest.approx(0.0)
    assert mv.missing is False


def test_worst_loss_with_no_settled_markets_is_undefined(xos_session):
    """$0.00 here would read as "nothing has gone wrong" on a book that has not
    yet settled anything — the reassuring-zero failure this system exists to
    refuse."""
    s = xos_session
    _order(s, qty=1, filled=1, status="resting")
    s.commit()

    mv = compute_metric(s, "live_max_realized_loss_usd", _scope())
    assert mv.value is None
    assert "no settled live markets" in mv.reason


def test_tail_losses_count_markets_not_contracts(xos_session):
    """A 5-lot that lost is ONE tail event: the contracts share one settlement."""
    s = xos_session
    _settled(s, realized=-1.20, contracts=5)
    _settled(s, realized=0.08, contracts=2)
    s.commit()

    mv = compute_metric(s, "live_tail_loss_markets", _scope())
    assert mv.value == pytest.approx(1.0)
    assert mv.provenance["loss_usd_total"] == pytest.approx(-1.20)
    assert mv.provenance["worst_market_loss_usd"] == pytest.approx(1.20)


def test_tail_losses_on_a_clean_book_is_a_real_zero(xos_session):
    s = xos_session
    _settled(s, realized=0.05)
    s.commit()

    mv = compute_metric(s, "live_tail_loss_markets", _scope())
    assert mv.value == pytest.approx(0.0)
    assert mv.missing is False


# ---------------------------------------------------------------------------
# live_blocked_entries
# ---------------------------------------------------------------------------


def test_blocked_entries_breaks_down_by_gate(xos_session):
    s = xos_session
    _parity(s, twin_outcome="opened", live_outcome="gate:open_cap")
    _parity(s, twin_outcome="opened", live_outcome="gate:open_cap")
    _parity(s, twin_outcome="opened", live_outcome="gate:dedup")
    _parity(s, twin_outcome="opened", live_outcome="placed")
    s.commit()

    mv = compute_metric(s, "live_blocked_entries", _scope())
    assert mv.value == pytest.approx(3.0)
    assert mv.provenance["by_gate"] == {"gate:dedup": 1, "gate:open_cap": 2}
    assert mv.provenance["twin_opened_and_live_placed"] == 1
    assert mv.n == 4


def test_blocked_entries_ignores_candidates_the_twin_also_declined(xos_session):
    """The twin's own cap is not the live risk engine's block. Counting it would
    attribute a paper-side skip to real-money risk control and make the live gates
    look busier than they are."""
    s = xos_session
    _parity(s, twin_outcome="skip_cap", live_outcome="gate:open_cap")
    _parity(s, twin_outcome="opened", live_outcome="gate:daily_loss")
    s.commit()

    mv = compute_metric(s, "live_blocked_entries", _scope())
    assert mv.value == pytest.approx(1.0)
    assert mv.provenance["by_gate"] == {"gate:daily_loss": 1}


def test_blocked_entries_reports_venue_rejections_separately(xos_session):
    """A gate block placed no order; a venue rejection placed one and had it
    refused. The remedies are different (a cap change vs an order-shape fix), so
    the two are never summed into one number."""
    s = xos_session
    _parity(s, twin_outcome="opened", live_outcome="gate:spread")
    _order(s, qty=1, status="rejected")
    s.commit()

    mv = compute_metric(s, "live_blocked_entries", _scope())
    assert mv.value == pytest.approx(1.0)
    assert mv.provenance["venue_rejected_orders"] == 1
    assert "rejected" not in mv.provenance["by_gate"]


def test_blocked_entries_on_a_book_that_never_ran_is_zero(xos_session):
    """A count's zero is a real answer: nothing was blocked because nothing
    happened. The keep/stop contract reads it beside live_settled_contracts, which
    is what distinguishes "healthy" from "not trading"."""
    s = xos_session
    mv = compute_metric(s, "live_blocked_entries", _scope())
    assert mv.value == pytest.approx(0.0)
    assert mv.provenance["by_gate"] == {}


def test_blocked_entries_is_scoped_to_this_arms_live_tag(xos_session):
    """Another book's gate blocks are not this canary's evidence."""
    s = xos_session
    _parity(s, twin_outcome="opened", live_outcome="gate:open_cap")
    _parity(s, twin_outcome="opened", live_outcome="gate:open_cap",
            live_tag="Lother", twin_tag="Lother_pt")
    s.commit()

    mv = compute_metric(s, "live_blocked_entries", _scope())
    assert mv.value == pytest.approx(1.0)
