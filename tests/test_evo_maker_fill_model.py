"""The sandbox must stop paying agents for fills a maker would never get.

Paper's resting orders always fill. Live they fill ~70% of the time, and the ~30%
they miss are the winners — a passive bid is only hit when someone actively takes the
other side, so the quiet longshots that drift to zero never trade against you. On the
same mmsell3 tickers at the same entry price, paper booked -0.67c/trade on what live
actually filled and +3.77c/trade on what live tried and never could.

That gap is not a rounding error, it is the sign of the edge. A backtest that reports
the optimistic number is telling an agent to trade a book that loses money.

These tests pin both halves of the correction:
  * the fill GATE — how many resting orders get hit at all (measured rate, decided once
    per market, not re-rolled every candle);
  * the REALIZABLE number — what the fills you do get are worth, reported beside the
    optimistic one so a mirage is visible rather than inferred.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.evo import fill_model as fm
from kalshi_bot.evo import sandbox
from kalshi_bot.models import MmSellPositionTick, PaperTrade

# --- the calibration itself -------------------------------------------------


def test_a_resting_no_bid_is_priced_in_its_yes_cell():
    """Fillability is a property of the market's price cell, not of which leg you took:
    a resting NO bid at 92c and a resting YES bid at 8c are the same book event. mmsell
    rests NO bids at 91-92c, which is why its data lands in the 8-9c YES cells."""
    assert fm.yes_equivalent_cents("no", 92) == 8
    assert fm.yes_equivalent_cents("yes", 8) == 8
    assert fm.yes_equivalent_cents("no", 91.4) == 9  # rounds to a cell


def test_thin_cells_are_never_trusted():
    """5c has one live fill and 13c has four. Believing a fill rate off that few
    observations is how a made-up number gets laundered into a gate."""
    assert fm.cell_for("yes", 5) is None
    assert fm.cell_for("yes", 13) is None
    assert fm.cell_for("yes", 8) is not None
    assert fm.cell_for("yes", 40) is None  # outside the measured range entirely


def test_the_draw_is_deterministic_and_lands_on_the_measured_rate():
    """Same market, same price -> same answer forever, so a backtest stays reproducible
    and two runs of one spec are comparable. Across markets it reproduces the rate."""
    first = fm.maker_order_fills(ticker="KXX-1", side="yes", limit_price_cents=8)
    assert fm.maker_order_fills(ticker="KXX-1", side="yes", limit_price_cents=8) is first

    fills = sum(
        1 for i in range(2000)
        if fm.maker_order_fills(ticker=f"KXX-{i}", side="yes", limit_price_cents=8)
    )
    assert abs(fills / 2000 - fm.MAKER_FILL_CALIBRATION[8].fill_rate) < 0.03


def test_an_uncovered_price_defers_instead_of_guessing():
    """None means 'no measurement here' — the caller keeps its own heuristic. Returning
    a made-up probability would be worse than the assumption being replaced."""
    assert fm.maker_order_fills(ticker="KXX-1", side="yes", limit_price_cents=40) is None
    assert fm.maker_fill_probability("yes", 40) is None
    assert fm.realizable_cents("yes", 40) is None
    assert fm.realizable_cents("no", 92) == fm.MAKER_FILL_CALIBRATION[8].realizable_cents


def test_projection_excludes_uncovered_prices_and_says_so():
    hist = {8: (10, 100.0), 40: (10, 500.0)}  # half the trades at an unmeasured price
    proj = fm.project_realizable(hist)
    assert proj["total_n"] == 20 and proj["covered_n"] == 10
    assert proj["coverage"] == 0.5
    assert proj["est_realizable_cents"] == fm.MAKER_FILL_CALIBRATION[8].realizable_cents
    assert proj["opt_cents"] == 30.0  # 600c / 20 trades — over ALL trades


def test_verdict_names_the_mirage():
    """A book that is positive on paper and negative once you can only book the fills
    you would really get. This is the one an agent must not trade."""
    assert fm.verdict(fm.project_realizable({8: (10, 900.0)})) == "MIRAGE"
    assert fm.verdict(fm.project_realizable({6: (10, 900.0)})) == "REAL"
    assert fm.verdict(fm.project_realizable({6: (10, -900.0)})) == "NEGATIVE"
    assert fm.verdict(fm.project_realizable({40: (10, 900.0)})) == "UNCOVERED"


# --- wired into the backtest ------------------------------------------------


LONGSHOT_SPEC = {
    "name": "longshot_maker",
    "family": "testfam",
    "universe": {"series_prefixes": ["KXFILL"], "min_volume": 0, "max_spread_cents": 20,
                 "min_hours_to_close": 0.0, "max_hours_to_close": 500},
    "entry": {"side": "yes", "style": "maker", "maker_offset_cents": 1,
              "min_price_cents": 1, "max_price_cents": 20, "size_contracts": 1},
    "exit": {"mode": "settlement"},
    "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
             "max_cost_per_position_usd": 50},
}


def _seed_longshots(session, n=40, *, yes_bid=7, yes_ask=9):
    """n settled cheap-YES markets that all resolve YES, each with a tick path. A maker
    YES entry rests at yes_bid+1 = 8c — squarely inside the measured 8c cell (68.8%)."""
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for i in range(n):
        ticker = f"KXFILL-26JUN{i:02d}-T1"
        session.add(PaperTrade(
            market_ticker=ticker, strategy="mmsell", status="settled",
            assumed_price=91, resolved_value=0, side="no", quantity=5,
            created_at=base + timedelta(hours=i),
            closed_at=base + timedelta(hours=i, minutes=90),
        ))
        for h in range(3):
            session.add(MmSellPositionTick(
                market_ticker=ticker,
                captured_at=base + timedelta(hours=i, minutes=h * 20),
                yes_bid=yes_bid, yes_ask=yes_ask, no_bid=100 - yes_ask,
                no_ask=100 - yes_bid, mid=(yes_bid + yes_ask) / 2.0, volume=100,
            ))
    session.flush()


def _run(session, settings, agent, cohort, spec=None):
    result, err = sandbox.run_backtest(
        session, settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec or LONGSHOT_SPEC, dataset="mmsell", charge_budget=False,
        persist=False,
    )
    assert err is None, err
    return result


def test_the_measured_fill_rate_thins_maker_entries(evo_session, evo_settings, evo_agent):
    """THE correction. Every one of these markets trades through the limit, so the old
    trade-through check fills all 40. Live, a resting order at 8c is hit ~69% of the
    time — and the backtest must say so rather than banking the other 31%."""
    agent, cohort = evo_agent
    _seed_longshots(evo_session, n=40)
    result = _run(evo_session, evo_settings, agent, cohort)

    assert result["markets_considered"] == 40
    assert 20 <= result["n_trades"] <= 34, (
        f"expected ~69% of 40 resting orders to fill, got {result['n_trades']}"
    )
    fmr = result["fill_model"]
    assert fmr["applied"] is True
    assert fmr["markets_blocked"] == 40 - result["n_trades"]


def test_the_gate_is_decided_once_per_market_not_once_per_candle(
    evo_session, evo_settings, evo_agent
):
    """A per-candle re-roll would compound to a near-certain fill (0.31 ** 3 over even a
    short path), quietly restoring the 100% assumption this exists to remove. Lengthen
    the tick path and the fill count must not move."""
    agent, cohort = evo_agent
    _seed_longshots(evo_session, n=40)
    short = _run(evo_session, evo_settings, agent, cohort)

    for tick in list(evo_session.query(MmSellPositionTick).all()):
        for k in range(1, 9):
            evo_session.add(MmSellPositionTick(
                market_ticker=tick.market_ticker,
                captured_at=tick.captured_at + timedelta(seconds=90 * k),
                yes_bid=tick.yes_bid, yes_ask=tick.yes_ask, no_bid=tick.no_bid,
                no_ask=tick.no_ask, mid=tick.mid, volume=tick.volume,
            ))
    evo_session.flush()
    long = _run(evo_session, evo_settings, agent, cohort)

    assert long["rows_processed"] > short["rows_processed"]  # the path really did grow
    assert long["n_trades"] == short["n_trades"]


def test_realizable_is_reported_beside_the_optimistic_number(
    evo_session, evo_settings, evo_agent
):
    """Paper says every one of these pays +92c a contract. The fills a maker actually
    gets at 8c are worth -0.81c. The agent must see both numbers and the word MIRAGE,
    not have to derive the correction itself."""
    agent, cohort = evo_agent
    _seed_longshots(evo_session, n=40)
    result = _run(evo_session, evo_settings, agent, cohort)

    fmr = result["fill_model"]
    assert fmr["version"] == fm.CALIBRATION_VERSION
    assert fmr["coverage"] == 1.0
    assert fmr["optimistic_cents_per_contract"] > 0
    assert fmr["realizable_cents_per_contract"] == fm.MAKER_FILL_CALIBRATION[8].realizable_cents
    assert fmr["verdict"] == "MIRAGE"


def test_uncovered_maker_prices_keep_the_trade_through_rule(
    evo_session, evo_settings, evo_agent
):
    """Outside the measured range we have no fill data, so behavior is unchanged and the
    result says the paper number is untested rather than endorsing it."""
    agent, cohort = evo_agent
    _seed_longshots(evo_session, n=10, yes_bid=44, yes_ask=48)
    spec = {**LONGSHOT_SPEC,
            "entry": {**LONGSHOT_SPEC["entry"], "min_price_cents": 1,
                      "max_price_cents": 99}}
    result = _run(evo_session, evo_settings, agent, cohort, spec)

    assert result["n_trades"] == 10  # every market still fills, as before
    fmr = result["fill_model"]
    assert fmr["markets_blocked"] == 0
    assert fmr["coverage"] == 0.0
    assert fmr["verdict"] == "UNCOVERED"
    assert fmr["realizable_cents_per_contract"] is None


def test_taker_backtests_are_untouched(evo_session, evo_settings, evo_agent):
    """A taker crosses the spread and gets the fill; there is no resting order to miss,
    so none of this applies and the trade count must not change."""
    agent, cohort = evo_agent
    _seed_longshots(evo_session, n=10)
    spec = {**LONGSHOT_SPEC, "entry": {**LONGSHOT_SPEC["entry"], "style": "taker"}}
    result = _run(evo_session, evo_settings, agent, cohort, spec)

    assert result["n_trades"] == 10
    assert result["fill_model"]["applied"] is False


def test_the_correction_can_be_switched_off_from_ops(
    evo_session, evo_settings, evo_agent
):
    """A calibration is a snapshot of one book's live data. If it ever starts distorting
    a domain it was not measured on, the gate has to be reachable without a deploy."""
    agent, cohort = evo_agent
    _seed_longshots(evo_session, n=40)
    evo_settings.sandbox_maker_fill_model = False
    result = _run(evo_session, evo_settings, agent, cohort)

    assert result["n_trades"] == 40  # back to the optimistic trade-through path
    assert result["fill_model"]["applied"] is False
    # ...but the realizable projection still reports, because knowing what the fills are
    # worth is useful even when we are not gating on it.
    assert result["fill_model"]["verdict"] == "MIRAGE"
