"""Fitness must reward capital actually put at risk, and must not treat a
refused no-op as misconduct.

Three defects this pins, all found by scoring the live cohort:

1. A cross-agent `supersedes_id` / `experiment_id` is ALREADY refused — the write
   never lands. Recording it at severity='integrity' anyway cost 20 fitness points
   (3 = disqualification) for what is an id-guess collision, not tampering. Live,
   this ranked the fleet's best book (best P&L, best profit/consistency/risk
   points) dead last with a score of 0.0.
2. `EvoPosition.quantity` is decremented to 0 when a position is closed by
   SELLING, so summing it to measure deployed capital reads an exit-managing
   agent as having deployed nothing (live: turnover 0.068 across 166 closed
   trades). That feeds both `turnover` and the concentration term of `risk`.
3. `_squash(0) == 0.5`, so an agent that never traded banked 45% of the profit
   component, and `risk` handed it a perfect 1.0 for never having drawn down.
   Doing nothing outscored trading and losing — the opposite of the north star.
"""

from __future__ import annotations

import uuid as uuidlib

from sqlalchemy import select

from kalshi_bot.evo import memory, paper
from kalshi_bot.evo import models as em
from kalshi_bot.evo.audit import integrity_violation
from kalshi_bot.evo.cognition import ACTION_PROTOCOL
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.fitness import HARMLESS_REFUSAL_KINDS, collect_metrics, component_scores
from kalshi_bot.evo.marketdata import Quote, StaticMarketData
from tests.test_evo_foundations import _mk_agent

S = EvoSettings(_env_file=None)


# --- 1. a refused cross-agent id is a rejection, not an integrity event ------


def test_cross_agent_belief_revision_is_refused_but_not_an_integrity_event(evo_session):
    a, b = _mk_agent(evo_session), _mk_agent(evo_session, surname="Voss")
    belief, _ = memory.revise_belief(
        evo_session, a.agent_uuid, title="b", new_belief="mine",
        evidence_for="e", evidence_against=None, confidence_after=0.5, heartbeat_id=None,
    )
    row, err = memory.revise_belief(
        evo_session, b.agent_uuid, title="b", new_belief="theirs",
        evidence_for="e", evidence_against=None, confidence_after=0.5,
        heartbeat_id=None, supersedes_id=belief.id,
    )
    # still refused — the peer's belief is untouched
    assert row is None and "another agent" in err
    assert belief.body_json.get("belief") == "mine"

    events = list(evo_session.scalars(
        select(em.EvoAuditEvent).where(em.EvoAuditEvent.agent_uuid == b.agent_uuid)
    ))
    assert events, "the attempt must still leave an audit trail"
    assert all(e.severity != "integrity" for e in events), (
        "a refused write causes no harm — penalising it retires good books for typos"
    )


def test_cross_agent_experiment_conclusion_is_refused_but_not_an_integrity_event(evo_session):
    a, b = _mk_agent(evo_session), _mk_agent(evo_session, surname="Voss")
    exp = memory.register_experiment(evo_session, a.agent_uuid, hypothesis="H")
    row, err = memory.conclude_experiment(
        evo_session, b.agent_uuid, exp.id,
        result={"pnl": 1}, confidence=0.7, interpretation="not mine",
    )
    assert row is None and "another agent" in err
    assert exp.status == "open" and exp.result_json is None

    events = list(evo_session.scalars(
        select(em.EvoAuditEvent).where(em.EvoAuditEvent.agent_uuid == b.agent_uuid)
    ))
    assert events
    assert all(e.severity != "integrity" for e in events)


def test_conclude_experiment_documents_where_the_id_comes_from():
    """revise_belief got this guidance in July; conclude_experiment never did, and
    it is the path still producing collisions live."""
    assert "conclude_experiment" in ACTION_PROTOCOL
    proto = " ".join(ACTION_PROTOCOL.split())
    idx = proto.index("conclude_experiment {")
    clause = proto[idx:idx + 400]
    assert "OPEN EXPERIMENTS" in clause, "must name where a valid id is shown"
    assert "guess" in clause.lower()


# --- 2. historical rows: the penalty must not survive the reclassification ---


def _cohort_id(session) -> int:
    return session.scalar(select(em.EvoCohort.id).order_by(em.EvoCohort.id.desc()))


def test_legacy_refusal_rows_no_longer_penalise(evo_session):
    """Rows already written at severity='integrity' before the fix still sit in
    the live DB, and the ops channel is read-only — so the penalty has to be
    dropped by KIND, not by rewriting history."""
    agent = _mk_agent(evo_session)
    for kind in ("cross_agent_belief_revision", "cross_agent_experiment_conclusion"):
        integrity_violation(evo_session, agent.agent_uuid, kind, target_row=1)
    m = collect_metrics(evo_session, S, agent.agent_uuid, _cohort_id(evo_session))
    assert m["integrity_events"] == 0


def test_real_integrity_events_still_penalise(evo_session):
    """Reaching for system rules is still misconduct and must still cost."""
    agent = _mk_agent(evo_session)
    integrity_violation(evo_session, agent.agent_uuid, "unpermitted_action",
                        action_type="rewrite_fitness")
    m = collect_metrics(evo_session, S, agent.agent_uuid, _cohort_id(evo_session))
    assert m["integrity_events"] == 1
    assert "unpermitted_action" not in HARMLESS_REFUSAL_KINDS


# --- 3. deployed capital survives a sell-to-close ---------------------------


TRADER = "f" * 36


def _quote(ticker: str) -> Quote:
    from datetime import datetime, timedelta, timezone
    return Quote(
        ticker=ticker, captured_at=datetime.now(timezone.utc), status="active",
        yes_bid=44, yes_ask=47, no_bid=53, no_ask=56,
        yes_levels=[(44, 500)], no_levels=[(53, 500)], volume=500, open_interest=1000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=5),
    )


def _md(*tickers: str) -> StaticMarketData:
    md = StaticMarketData()
    for t in tickers:
        md.set_quote(_quote(t))
    return md


def _buy_then_sell(session, settings, md, ticker, qty=20, cohort_id=1):
    """Open a position and close it by SELLING — the path that zeroes quantity."""
    for action in ("buy", "sell"):
        order, err = paper.place_order(
            session, settings, agent_uuid=TRADER, cohort_id=cohort_id,
            idem_key=f"{ticker}-{action}-{uuidlib.uuid4().hex[:6]}",
            market_ticker=ticker, side="yes", action=action, quantity=qty,
            limit_price_cents=47 if action == "buy" else 44,
        )
        assert err is None, err
        paper.process_open_orders(session, settings, md)


def test_turnover_counts_capital_closed_by_selling(evo_session, evo_settings):
    paper.ensure_portfolio(evo_session, TRADER, paper.LIFETIME, 1000.0)
    paper.ensure_portfolio(evo_session, TRADER, paper.cohort_ledger(1), 1000.0)
    _buy_then_sell(evo_session, evo_settings, _md("KXTEST-A"), "KXTEST-A", qty=20)

    pos = evo_session.scalar(select(em.EvoPosition).where(
        em.EvoPosition.agent_uuid == TRADER,
        em.EvoPosition.ledger == paper.cohort_ledger(1)))
    assert pos is not None and pos.status == "closed"
    assert float(pos.quantity) == 0, "precondition: the sell path zeroes quantity"

    m = collect_metrics(evo_session, evo_settings, TRADER, 1)
    # 20 contracts bought at 47c = $9.40 of capital deployed on a $1000 book
    assert m["turnover"] > 0.005, (
        f"turnover {m['turnover']} treats an exit-managing agent as never having "
        "deployed capital"
    )


def test_concentration_is_measured_across_closed_positions_too(evo_session, evo_settings):
    """Two equally-sized events should read as ~50% concentration, not 100% just
    because one of them was closed by selling."""
    paper.ensure_portfolio(evo_session, TRADER, paper.LIFETIME, 1000.0)
    paper.ensure_portfolio(evo_session, TRADER, paper.cohort_ledger(1), 1000.0)
    md = _md("KXAAA-B1", "KXBBB-B1")
    _buy_then_sell(evo_session, evo_settings, md, "KXAAA-B1", qty=20)
    _buy_then_sell(evo_session, evo_settings, md, "KXBBB-B1", qty=20)

    m = collect_metrics(evo_session, evo_settings, TRADER, 1)
    assert m["max_event_frac"] < 0.75, (
        f"max_event_frac {m['max_event_frac']} — closed positions vanished from the "
        "concentration denominator"
    )


# --- 4. never putting capital at risk earns no profit or risk credit --------


def _metrics(**kw):
    base = dict(
        start=1000.0, nav=1000.0, net_pnl=0.0, n_trades=0, n_eff=0, trade_pnls=[],
        daily_pnls=[], turnover=0.0, max_event_frac=0.0, max_drawdown_frac=0.0,
        min_equity_frac=1.0, open_positions=0, n_opportunities=0, n_acted=0,
        n_justified_no_trade=0, n_orders=0, n_rejected_orders=0, brier_mean=None,
        n_scored_forecasts=0, n_material_revisions=0, prereg_revisions=0,
        field_reversals=0, listener_useful=0, listener_useless=0, n_influences=0,
        blind_copies=0, n_journals=0, complete_journals=0, integrity_events=0,
    )
    base.update(kw)
    return base


def test_an_agent_that_never_deployed_capital_scores_zero_profit_and_risk():
    c = component_scores(_metrics(), S)
    assert c["profit"] == 0.0, "squash(0)=0.5 paid a no-trade agent 45% of profit"
    assert c["risk"] == 0.0, "never trading is not demonstrated risk management"


def test_a_losing_book_still_outranks_an_idle_one():
    """The north star is realised profit. An agent generating real evidence must
    beat one that opted out, or the fittest strategy becomes 'stop trading'."""
    idle = component_scores(_metrics(
        n_material_revisions=6, prereg_revisions=6, n_journals=10, complete_journals=10,
    ), S)
    loser = component_scores(_metrics(
        nav=800.0, net_pnl=-200.0, n_trades=60, n_eff=40,
        trade_pnls=[-3.33] * 60, daily_pnls=[-20.0] * 10, turnover=2.0,
        max_event_frac=0.2, max_drawdown_frac=0.25, min_equity_frac=0.8,
        n_opportunities=60, n_acted=60, n_orders=60, brier_mean=0.28,
        n_scored_forecasts=60, n_material_revisions=4, prereg_revisions=3,
        n_journals=8, complete_journals=6,
    ), S)
    w = {"profit": S.fitness_weight_profit, "consistency": S.fitness_weight_consistency,
         "risk": S.fitness_weight_risk, "evidence": S.fitness_weight_evidence,
         "forecast": S.fitness_weight_forecast, "adaptive": S.fitness_weight_adaptive}
    idle_total = sum(w[k] * idle[k] for k in w)
    loser_total = sum(w[k] * loser[k] for k in w)
    assert loser_total > idle_total, (
        f"idle {idle_total:.1f} >= losing trader {loser_total:.1f} — the scorer "
        "rewards inactivity"
    )


def test_deploying_capital_restores_normal_scoring():
    """The gate keys on capital at risk, not on closed trades: an agent holding
    open positions is being scored on real exposure."""
    open_only = component_scores(_metrics(
        nav=1020.0, net_pnl=20.0, turnover=0.5, open_positions=3,
        max_event_frac=0.3, min_equity_frac=1.02,
    ), S)
    assert open_only["profit"] > 0.0
    assert open_only["risk"] > 0.0
