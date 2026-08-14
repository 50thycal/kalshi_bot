"""The `econ` backtest dataset — the fleet's own request, actioned.

Four tickets since 2026-07-22 (`data_collection` ×3, `external_data_pipeline` ×1) asked for
"settled CPI market corpus + official CPI actuals as a run_backtest dataset (like 'crypto')".
That is the fleet generating a NON-weather thesis and naming the data it needs to test it —
the whole point of the capability queue — and nothing in the repo touched CPI.

Censused before building, because the record said to be sceptical: the scorecard has
ECON-REACT logged as HOLD (testability-thin) from 2026-07-21, "only ~20 settled genuine econ
prints", and Kalshi retains a rolling ~70-day settled window against a MONTHLY print. The
census refuted that worry — `mmsell_regime_backtest --series KXCPI,KXCPIYOY,KXPAYROLL,KXPCE`
returned 102 settled markets, 102 with candles, median candle span 335h. CPI prints are
LADDERS quoted for weeks, so one print yields many markets each carrying a long price path.

The dataset is regime-backed rather than CPI-specific: `backfill_regime_markets` /
`backfill_regime_candles` already exist, already carry a `regime` column, and are already
populated by the ride-along `RegimeHistoryCapture`. Adding the econ series to
`mmsell_history_series` starts the capture; this adapter makes the result replayable. The
same code serves payrolls, PCE and the Fed ladder when someone asks next.

Pinned here:

  SETTLEMENT LABELS, NEVER PRICES — `result` selects which market to replay; it must not
  reach a Quote. A backtest that can see the outcome is worse than no backtest.

  NO UNLABELLED OR UNPRICED MARKETS — a row without a settled result, or without candles,
  cannot be replayed and must be skipped rather than silently scored as a loss.

  REGIME ISOLATION — the same tables hold NFL, MLB and Elections. An `econ` backtest that
  quietly replayed football would be a fabricated result wearing the right name.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.evo import sandbox
from kalshi_bot.models import BackfillRegimeCandle, BackfillRegimeMarket

CLOSE = datetime(2026, 7, 15, 14, 30, tzinfo=timezone.utc)


def _spec(**over):
    doc = {
        "name": "econ_spec",
        "universe": {"series_prefixes": [], "min_volume": 0, "max_spread_cents": 99,
                     "min_hours_to_close": 0.0, "max_hours_to_close": 5000},
        "entry": {"side": "yes", "style": "taker", "min_price_cents": 1,
                  "max_price_cents": 99, "size_contracts": 5,
                  "conditions": [{"metric": "yes_ask", "op": "<=", "value": 99}]},
        "exit": {"mode": "settlement"},
        "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
                 "max_cost_per_position_usd": 50},
    }
    doc.update(over)
    spec, err = sandbox.validate_spec(doc)
    assert err is None, err
    return spec


def _market(session, ticker, *, result="yes", regime="Econ", series="KXCPI",
            candles=6, volume=500.0):
    session.add(BackfillRegimeMarket(
        market_ticker=ticker, event_ticker=ticker.split("-")[0], series_ticker=series,
        regime=regime, title="CPI above 3.0%", result=result, volume=volume,
        open_interest=1000.0, open_time=CLOSE - timedelta(days=14), close_time=CLOSE,
        candles_fetched=True, candle_count=candles,
    ))
    for i in range(candles):
        session.add(BackfillRegimeCandle(
            market_ticker=ticker,
            end_period_ts=CLOSE - timedelta(hours=(candles - i) * 6),
            period_minutes=60,
            price_open=40.0, price_high=46.0, price_low=38.0, price_close=42.0,
            yes_bid_close=41.0, yes_ask_close=43.0, volume=120, open_interest=900,
        ))
    session.flush()


# --- the dataset exists and is wired ----------------------------------------


def test_econ_is_a_registered_dataset():
    assert "econ" in sandbox.DATASETS
    assert sandbox._PROVENANCE["econ"]


def test_econ_declares_which_external_signals_it_can_replay():
    """A dataset that cannot reconstruct a metric must say so, or a spec gating on that
    metric silently returns zero trades and the agent concludes the signal is worthless
    when it was never evaluated."""
    assert "econ" in sandbox.DATASET_SIGNALS


# --- replay ------------------------------------------------------------------


def test_a_settled_cpi_market_replays(evo_session):
    _market(evo_session, "KXCPI-26JUL-T3.0")
    markets = list(sandbox._econ_markets(evo_session, _spec(), None, None))
    assert len(markets) == 1
    m = markets[0]
    assert m.ticker == "KXCPI-26JUL-T3.0"
    assert m.result == "yes"
    assert len(m.candles) == 6
    assert m.month == "2026-07"


def test_candles_are_ordered_in_time(evo_session):
    """Replay walks the path forward; out-of-order candles would let an exit rule see a
    price before its entry."""
    _market(evo_session, "KXCPI-26JUL-T3.0", candles=8)
    m = next(iter(sandbox._econ_markets(evo_session, _spec(), None, None)))
    assert [c.ts for c in m.candles] == sorted(c.ts for c in m.candles)


def test_the_settled_outcome_never_reaches_the_quote(evo_session):
    """SETTLEMENT LABELS, NEVER PRICES. `result` picks the market and scores the trade; a
    Quote carrying it would let a strategy read the answer off its own input."""
    _market(evo_session, "KXCPI-26JUL-T3.0", result="yes")
    m = next(iter(sandbox._econ_markets(evo_session, _spec(), None, None)))
    for c in m.candles:
        assert not getattr(c.quote, "result", "")


def test_close_time_is_wall_relative_so_hours_to_close_gates_work(evo_session):
    """The interpreter compares hours_to_close against the WALL clock, so a historical
    close_time would make every horizon gate see a market that closed months ago."""
    _market(evo_session, "KXCPI-26JUL-T3.0")
    m = next(iter(sandbox._econ_markets(evo_session, _spec(), None, None)))
    htc = m.candles[0].quote.hours_to_close()
    assert htc is not None and htc > 0


# --- what must be skipped -----------------------------------------------------


def test_an_unsettled_market_is_skipped(evo_session):
    """No label, no backtest. Scoring it either way invents a result."""
    _market(evo_session, "KXCPI-26AUG-T3.0", result=None)
    assert list(sandbox._econ_markets(evo_session, _spec(), None, None)) == []


def test_a_market_with_no_candles_is_skipped(evo_session):
    _market(evo_session, "KXCPI-26JUL-T3.0", candles=0)
    assert list(sandbox._econ_markets(evo_session, _spec(), None, None)) == []


def test_other_regimes_are_not_replayed_as_econ(evo_session):
    """REGIME ISOLATION. The same two tables hold NFL/MLB/Elections history; an econ
    backtest that swept those up would be a fabricated result wearing the right name."""
    _market(evo_session, "KXCPI-26JUL-T3.0", regime="Econ", series="KXCPI")
    _market(evo_session, "KXNFLGAME-26SEP-KC", regime="NFL", series="KXNFLGAME")
    _market(evo_session, "KXMLBGAME-26JUL-NYY", regime="MLB", series="KXMLBGAME")
    tickers = [m.ticker for m in sandbox._econ_markets(evo_session, _spec(), None, None)]
    assert tickers == ["KXCPI-26JUL-T3.0"]


def test_the_spec_universe_still_filters(evo_session):
    """An agent that scoped its universe to one series must not be handed the others."""
    _market(evo_session, "KXCPI-26JUL-T3.0", series="KXCPI")
    _market(evo_session, "KXPAYROLL-26JUL-T200", series="KXPAYROLL")
    spec = _spec(universe={"series_prefixes": ["KXCPI"], "min_volume": 0,
                           "max_spread_cents": 99, "min_hours_to_close": 0.0,
                           "max_hours_to_close": 5000})
    tickers = [m.ticker for m in sandbox._econ_markets(evo_session, spec, None, None)]
    assert tickers == ["KXCPI-26JUL-T3.0"]


def test_the_date_window_is_honoured(evo_session):
    """Walk-forward splits an in-sample from an out-of-sample window; if the filter leaked,
    the two halves would overlap and the OOS result would be meaningless."""
    _market(evo_session, "KXCPI-26JUL-T3.0")
    assert list(sandbox._econ_markets(evo_session, _spec(), "2026-08-01", None)) == []
    assert len(list(sandbox._econ_markets(evo_session, _spec(), "2026-07-01", None))) == 1


# --- end to end through run_backtest ------------------------------------------


def test_a_backtest_runs_end_to_end_on_econ(evo_session, evo_settings):
    for i in range(4):
        _market(evo_session, f"KXCPI-26JUL-T{i}.0", result="yes" if i % 2 else "no")
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid="agent-1", cohort_id=1,
        spec_doc=_spec().model_dump(), dataset="econ",
        charge_budget=False, persist=False,
    )
    assert err is None, err
    assert result["dataset"] == "econ"
    assert result["provenance"] == sandbox._PROVENANCE["econ"]
