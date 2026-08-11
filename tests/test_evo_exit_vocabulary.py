"""Agents could only exit three ways: hold to settlement, a flat take-profit/stop-loss,
or a clock. The mmsell exit study (docs/MMSELL_EXIT_STUDY.md) measured two rules that
matter on the books we actually trade and that none of those three can express:

  * a **confirmed** catastrophic stop — the position's own mid at or below a level for K
    CONSECUTIVE ticks. The K-confirm is the whole point: at longshot prices a single
    print routinely lies, and an unconfirmed stop sells the noise. A plain stop_loss
    fires on that first bad print, which is why the study's unconfirmed variants looked
    like an edge that evaporated on inspection.
  * a **volatility exit** — the mid's range over the trailing W ticks reaching V. It
    fires on movement regardless of direction, on the thesis that a position being
    actively repriced is more likely to be a loser than a quiet one.

Both are path-dependent, which is why they need a mid tape rather than a single quote.
Without one they must hold — an exit rule that cannot see the path must never guess.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import kalshi_bot.evo.models as em
from kalshi_bot.evo import paper, sandbox, strategy_runner
from kalshi_bot.evo.marketdata import Quote, StaticMarketData
from kalshi_bot.evo.strategy_spec import exit_signal, validate_spec
from kalshi_bot.models import MmSellPositionTick, PaperTrade

BASE = {
    "name": "exit_vocab",
    "family": "testfam",
    "universe": {"series_prefixes": ["T"], "min_volume": 0, "max_spread_cents": 20,
                 "min_hours_to_close": 0.0, "max_hours_to_close": 500},
    "entry": {"side": "yes", "style": "taker", "min_price_cents": 1,
              "max_price_cents": 99, "size_contracts": 5},
    "exit": {"mode": "settlement"},
    "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
             "max_cost_per_position_usd": 50},
}


def _quote(ticker="T1", yes_bid=44, no_bid=53, hours=5.0, volume=500):
    yes_ask = 100 - no_bid
    return Quote(
        ticker=ticker, captured_at=datetime.now(timezone.utc), status="active",
        yes_bid=yes_bid, yes_ask=yes_ask, no_bid=no_bid, no_ask=100 - yes_bid,
        yes_levels=[(yes_bid, 100)], no_levels=[(no_bid, 100)],
        volume=volume, open_interest=1000,
        close_time=datetime.now(timezone.utc) + timedelta(hours=hours),
    )


def _spec(exit_doc):
    spec, err = validate_spec({**BASE, "exit": exit_doc})
    assert err is None, err
    return spec


# --- the rules --------------------------------------------------------------


def test_a_confirmed_stop_ignores_a_single_bad_print():
    """THE reason this mode exists. One tick through the level is noise at longshot
    prices; a plain stop_loss would have sold it."""
    spec = _spec({"mode": "confirmed_stop", "stop_mid_cents": 40, "confirm_ticks": 2})
    hold = exit_signal(spec, _quote(), side="yes", entry_price_cents=60,
                       held_hours=1, mid_history=[70, 65, 38])
    assert hold is None, "one print below the level is not a confirmation"

    fire = exit_signal(spec, _quote(), side="yes", entry_price_cents=60,
                       held_hours=1, mid_history=[70, 65, 38, 36])
    assert fire == "confirmed_stop"


def test_a_recovery_resets_the_confirmation():
    """Consecutive means consecutive — a tick back above the level starts the count
    over, or a position that oscillates around the level exits on a technicality."""
    spec = _spec({"mode": "confirmed_stop", "stop_mid_cents": 40, "confirm_ticks": 3})
    assert exit_signal(spec, _quote(), side="yes", entry_price_cents=60, held_hours=1,
                       mid_history=[38, 37, 45, 36, 35]) is None


def test_the_stop_level_is_read_in_the_positions_own_side():
    """The tape is YES mids, but the rule is about the position going against YOU. For a
    NO holder that means YES rising — the study's 'yes-mid >= L' form, re-expressed so
    one spec field works for either leg (no-mid = 100 - yes-mid)."""
    spec = _spec({"mode": "confirmed_stop", "stop_mid_cents": 20, "confirm_ticks": 2})
    # NO position: no-mid <= 20 is yes-mid >= 80.
    assert exit_signal(spec, _quote(), side="no", entry_price_cents=90, held_hours=1,
                       mid_history=[50, 85, 88]) == "confirmed_stop"
    # the identical tape is harmless to a YES holder — it moved in their favor
    assert exit_signal(spec, _quote(), side="yes", entry_price_cents=10, held_hours=1,
                       mid_history=[50, 85, 88]) is None


def test_a_short_tape_holds_rather_than_guessing():
    """Fewer observations than the confirmation window is not a satisfied stop. Fail
    closed — this is the state right after a worker restart, when the tape is empty."""
    spec = _spec({"mode": "confirmed_stop", "stop_mid_cents": 40, "confirm_ticks": 3})
    for tape in (None, [], [10], [10, 10]):
        assert exit_signal(spec, _quote(), side="yes", entry_price_cents=60,
                           held_hours=1, mid_history=tape) is None


def test_volatility_exit_fires_on_range_not_direction():
    """A position that swings 20c and comes back is 'unchanged' to every price-level
    rule and is exactly what this mode is for."""
    spec = _spec({"mode": "volatility_exit", "vol_window_ticks": 4, "vol_range_cents": 15})
    assert exit_signal(spec, _quote(), side="yes", entry_price_cents=50, held_hours=1,
                       mid_history=[50, 65, 52, 50]) == "volatility_exit"


def test_volatility_exit_ignores_a_quiet_tape_and_only_looks_back_w_ticks():
    spec = _spec({"mode": "volatility_exit", "vol_window_ticks": 3, "vol_range_cents": 15})
    assert exit_signal(spec, _quote(), side="yes", entry_price_cents=50, held_hours=1,
                       mid_history=[50, 51, 52, 53]) is None
    # the 20c move is real but has aged out of the trailing window
    assert exit_signal(spec, _quote(), side="yes", entry_price_cents=50, held_hours=1,
                       mid_history=[30, 50, 51, 52]) is None


def test_the_new_modes_require_their_parameters():
    """A confirmed_stop with no level, or a volatility_exit with no range, is a spec
    that can never fire — reject it at validation instead of silently never exiting."""
    assert validate_spec({**BASE, "exit": {"mode": "confirmed_stop"}})[1] is not None
    assert validate_spec({**BASE, "exit": {"mode": "volatility_exit"}})[1] is not None
    assert validate_spec({**BASE, "exit": {"mode": "confirmed_stop",
                                           "stop_mid_cents": 30}})[1] is None


def test_the_older_modes_are_unchanged():
    spec = _spec({"mode": "tp_sl", "take_profit_cents": 10, "stop_loss_cents": 8})
    q = _quote(yes_bid=60, no_bid=39)
    assert exit_signal(spec, q, side="yes", entry_price_cents=45, held_hours=1) == "tp"
    assert exit_signal(_spec({"mode": "settlement"}), q, side="yes",
                       entry_price_cents=45, held_hours=1, mid_history=[1, 1, 1]) is None


# --- replayed in the sandbox ------------------------------------------------


def _seed_path(session, ticker, mids, *, resolved=0):
    """One settled market whose captured tick path walks `mids`."""
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    session.add(PaperTrade(
        market_ticker=ticker, strategy="mmsell", status="settled",
        assumed_price=50, resolved_value=resolved, side="no", quantity=5,
        created_at=base, closed_at=base + timedelta(hours=6),
    ))
    for i, mid in enumerate(mids):
        session.add(MmSellPositionTick(
            market_ticker=ticker, captured_at=base + timedelta(minutes=10 * i),
            yes_bid=mid - 2, yes_ask=mid + 2, no_bid=98 - mid, no_ask=102 - mid,
            mid=float(mid), volume=100,
        ))
    session.flush()


def _backtest(session, settings, agent, cohort, exit_doc):
    spec = {**BASE, "universe": {**BASE["universe"], "series_prefixes": ["KXEXIT"]},
            "exit": exit_doc}
    result, err = sandbox.run_backtest(
        session, settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec, dataset="mmsell", charge_budget=False, persist=False,
    )
    assert err is None, err
    return result


def test_the_sandbox_replays_a_confirmed_stop(evo_session, evo_settings, evo_agent):
    """The backtester has to be able to measure the rule, or agents can adopt it but
    never test it. Path: steady at 50, then collapses to 25 and stays there."""
    agent, cohort = evo_agent
    _seed_path(evo_session, "KXEXIT-26JUN01-T1", [50, 50, 25, 25, 25])
    result = _backtest(evo_session, evo_settings, agent, cohort,
                       {"mode": "confirmed_stop", "stop_mid_cents": 30,
                        "confirm_ticks": 2})
    assert result["n_trades"] == 1
    assert result["by_exit"] == {"confirmed_stop": 1}


def test_one_bad_tick_does_not_stop_the_replay_out(evo_session, evo_settings, evo_agent):
    """Same market, same rule, one dip instead of a collapse: the position must survive
    to settlement. This is the difference the K-confirm buys."""
    agent, cohort = evo_agent
    _seed_path(evo_session, "KXEXIT-26JUN01-T1", [50, 50, 25, 50, 50])
    result = _backtest(evo_session, evo_settings, agent, cohort,
                       {"mode": "confirmed_stop", "stop_mid_cents": 30,
                        "confirm_ticks": 2})
    assert result["by_exit"] == {"settlement": 1}


def test_the_sandbox_replays_a_volatility_exit(evo_session, evo_settings, evo_agent):
    agent, cohort = evo_agent
    _seed_path(evo_session, "KXEXIT-26JUN01-T1", [50, 52, 70, 51, 50])
    result = _backtest(evo_session, evo_settings, agent, cohort,
                       {"mode": "volatility_exit", "vol_window_ticks": 3,
                        "vol_range_cents": 15})
    assert result["by_exit"] == {"volatility_exit": 1}


# --- carried across live cycles ---------------------------------------------


def test_the_runner_carries_the_tape_between_cycles(evo_session, evo_settings, evo_agent):
    """THE live requirement. One orchestrator cycle sees one tick, so a K=2 confirmation
    is only reachable if the tape survives from one cycle to the next. Without that the
    mode would validate, deploy, and silently never fire."""
    agent, cohort = evo_agent
    spec = {**BASE, "exit": {"mode": "confirmed_stop", "stop_mid_cents": 40,
                             "confirm_ticks": 2}}
    row, _ = sandbox.save_strategy(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, spec_doc=spec)
    sandbox.activate_strategy(evo_session, agent.agent_uuid, row.id)

    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1"))  # mid 45.5, entry taker at 47
    tape = strategy_runner.MidHistory()
    strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                              candidate_tickers=["T1"], mid_history=tape)
    paper.process_open_orders(evo_session, evo_settings, md)

    md.set_quote(_quote(ticker="T1", yes_bid=36, no_bid=60))  # mid 38 — first breach
    counts = strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                                       candidate_tickers=[], mid_history=tape)
    assert counts["exits"] == 0, "one cycle below the level is not a confirmation"

    counts = strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                                       candidate_tickers=[], mid_history=tape)
    assert counts["exits"] == 1
    order = evo_session.scalar(
        select(em.EvoOrder).where(em.EvoOrder.action == "sell"))
    assert order is not None and order.intent_json["exit_reason"] == "confirmed_stop"


def test_without_a_tape_the_runner_holds(evo_session, evo_settings, evo_agent):
    """A caller that passes no tape (or a freshly restarted worker) gets no exit rather
    than a stop evaluated off a single observation."""
    agent, cohort = evo_agent
    spec = {**BASE, "exit": {"mode": "confirmed_stop", "stop_mid_cents": 40,
                             "confirm_ticks": 2}}
    row, _ = sandbox.save_strategy(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, spec_doc=spec)
    sandbox.activate_strategy(evo_session, agent.agent_uuid, row.id)
    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1"))
    strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                              candidate_tickers=["T1"])
    paper.process_open_orders(evo_session, evo_settings, md)
    md.set_quote(_quote(ticker="T1", yes_bid=36, no_bid=60))
    for _ in range(3):
        counts = strategy_runner.run_cycle(evo_session, evo_settings, md,
                                           cohort_id=cohort.id, candidate_tickers=[])
        assert counts["exits"] == 0


def test_the_tape_is_bounded_and_drops_closed_positions():
    """It lives in the worker's memory for the life of the process, so it has to forget:
    unbounded per-position history in a long-running loop is a leak."""
    tape = strategy_runner.MidHistory(max_ticks=4)
    for m in (10, 11, 12, 13, 14, 15):
        seen = tape.observe(("a", "T1", "yes"), m)
    assert seen == [12, 13, 14, 15]

    tape.observe(("a", "T2", "yes"), 50)
    tape.prune({("a", "T1", "yes")})
    assert tape.observe(("a", "T2", "yes"), 51) == [51]  # T2's history was dropped
    assert tape.observe(("a", "T1", "yes"), 16) == [13, 14, 15, 16]  # T1's survived


def test_the_replay_does_not_re_enter_after_its_own_stop(
    evo_session, evo_settings, evo_agent
):
    """An exit-rule study compares "stop out" against "hold to settlement". If the replay
    buys back in on the next tick it is measuring a re-entry policy instead, and the stop
    looks free. One entry per market, matching the live runner's per-strategy/ticker
    dedup."""
    agent, cohort = evo_agent
    _seed_path(evo_session, "KXEXIT-26JUN01-T1", [50, 50, 25, 25, 25, 25, 25])
    result = _backtest(evo_session, evo_settings, agent, cohort,
                       {"mode": "confirmed_stop", "stop_mid_cents": 30,
                        "confirm_ticks": 2})
    assert result["n_trades"] == 1
    assert "settlement" not in result["by_exit"]


def test_two_active_strategies_do_not_double_tick_one_position(
    evo_session, evo_settings, evo_agent
):
    """The exit pass runs once per active strategy over the SAME open positions. If each
    pass appended to the tape, one cycle would look like two and every confirm_ticks
    window would fire at half its stated length."""
    agent, cohort = evo_agent
    for name in ("exit_vocab", "exit_vocab_b"):
        row, _ = sandbox.save_strategy(
            evo_session, evo_settings, agent_uuid=agent.agent_uuid,
            spec_doc={**BASE, "name": name,
                      "exit": {"mode": "confirmed_stop", "stop_mid_cents": 40,
                               "confirm_ticks": 2}})
        sandbox.activate_strategy(evo_session, agent.agent_uuid, row.id)

    md = StaticMarketData()
    md.set_quote(_quote(ticker="T1"))
    tape = strategy_runner.MidHistory()
    strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                              candidate_tickers=["T1"], mid_history=tape)
    paper.process_open_orders(evo_session, evo_settings, md)

    md.set_quote(_quote(ticker="T1", yes_bid=36, no_bid=60))
    counts = strategy_runner.run_cycle(evo_session, evo_settings, md, cohort_id=cohort.id,
                                       candidate_tickers=[], mid_history=tape)
    assert counts["exits"] == 0, "one cycle produced two ticks — the tape double-counted"
