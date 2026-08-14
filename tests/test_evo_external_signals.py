"""Let a strategy say something the order book cannot: information vs price.

Every DSL metric to date is a property of the book (yes_bid, spread, mid, volume,
hours_to_close...), so the only hypotheses an agent can state are price patterns —
"buy when cheap", "buy when tight". On a roughly efficient book those earn the
spread minus two fees, which is exactly what the control arm measures. The fleet
has run 900+ backtests inside a hypothesis space that is close to empty.

Two metrics change the KIND of hypothesis available, and neither needs our
forecast model to be any good:

  pm_divergence   Polymarket's implied probability MINUS Kalshi's mid, in cents,
                  for the same weather bucket. Two venues pricing one event; the
                  disagreement is the signal. Positive => Kalshi's YES is cheap
                  relative to the other venue.
  spot_vs_strike  Percent distance from the underlying's spot to a crypto market's
                  decision boundary, signed so POSITIVE always means "YES is
                  currently winning", whatever the strike_type.

Two properties this file exists to pin down:

  FAIL CLOSED ON STALENESS — a signal older than the configured window is dropped
  to None, and a None metric fails its condition, so a dead feed blocks entries
  rather than trading on a number nobody refreshed.

  NO SILENT DEAD SPECS — a backtest whose dataset cannot compute a metric the spec
  uses is REJECTED, not run. Otherwise an agent gates on pm_divergence, replays it
  over a dataset with no Polymarket join, gets zero trades, and concludes the
  signal is worthless when the truth is that it was never evaluated. That is the
  same class of lie as the maker-fill assumption.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.evo import sandbox, signals
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.marketdata import Quote, StaticMarketData
from kalshi_bot.evo.strategy_spec import METRICS, entry_signal, validate_spec
from kalshi_bot.models import CryptoLadderSnapshot, CryptoSpotCandle

# Fixed, because the signal tests below drive `compute_signals(now=NOW)` explicitly and their
# staleness assertions are relative to it. Do NOT derive close_time from this — see _quote.
NOW = datetime(2026, 8, 12, 15, 0, tzinfo=timezone.utc)
TICKER = "KXHIGHLAX-26AUG12-B85.5"


def _quote(**kw):
    """A quote that is always 5 hours from close, measured against the REAL clock.

    `close_time` cannot be anchored to `NOW` here the way the rest of this file anchors things.
    The signal tests below call `compute_signals(..., now=NOW)` and are deterministic because
    they pass the instant in explicitly — but `entry_signal(spec, quote)` takes no clock, and
    the universe gate it applies calls `Quote.hours_to_close()`, which falls back to
    `datetime.now(timezone.utc)`. With a fixed `close_time` of NOW+5h (2026-08-12 20:00 UTC)
    these tests passed until that wall-clock instant and then failed forever after, which is
    exactly what happened: the suite went red at 20:00 UTC on 2026-08-12 with no code change.

    Anchoring to real now keeps the property the tests actually care about — the market is
    comfortably inside the universe's hours-to-close window, so any None they observe comes
    from the SIGNAL condition under test rather than from the clock."""
    real_now = datetime.now(timezone.utc)
    base = dict(
        ticker=TICKER, captured_at=real_now, status="active",
        yes_bid=44, yes_ask=47, no_bid=53, no_ask=56,
        yes_levels=[(44, 100)], no_levels=[(53, 100)],
        volume=500, open_interest=1000, close_time=real_now + timedelta(hours=5),
    )
    base.update(kw)
    return Quote(**base)


def _spec(conditions, **entry):
    doc = {
        "name": "signal_spec",
        "universe": {"series_prefixes": [], "min_volume": 0, "max_spread_cents": 99,
                     "min_hours_to_close": 0.0, "max_hours_to_close": 500},
        "entry": {"side": "yes", "style": "taker", "min_price_cents": 1,
                  "max_price_cents": 99, "size_contracts": 5,
                  "conditions": conditions, **entry},
        "exit": {"mode": "settlement"},
        "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
                 "max_cost_per_position_usd": 50},
    }
    spec, err = validate_spec(doc)
    assert err is None, err
    return spec


# --- the metrics are real DSL citizens --------------------------------------


def test_the_fixture_quote_is_not_a_time_bomb():
    """Guard on the fixture itself. `entry_signal` reads Quote.hours_to_close(), which defaults
    to the REAL clock, so a close_time pinned to this file's fixed NOW makes every entry test
    below pass until wall-clock overtakes it and fail permanently after. That happened: pinned at
    NOW + 5h, the suite went red ~14 hours later with htc = -9.6h. If this assertion fails,
    close_time has been re-anchored to a constant — fix the fixture, not this test."""
    htc = _quote().hours_to_close()
    assert htc is not None and 4.9 < htc <= 5.0


def test_a_strategy_can_gate_on_information_not_price():
    """THE point of the feature, now carried by spot_vs_strike: an entry condition that
    references something the order book does not contain. (pm_divergence used to carry this
    test; it was retired after measurement — tests/test_evo_pm_divergence_retired.py.)"""
    spec = _spec([{"metric": "spot_vs_strike", "op": ">=", "value": 1.0}])
    # spot 1.56% above the strike => YES is winning by a clear margin, condition passes
    assert entry_signal(spec, _quote(spot_vs_strike=1.5625)) is not None
    # barely above the boundary => no margin, no entry
    assert entry_signal(spec, _quote(spot_vs_strike=0.2)) is None


def test_a_missing_signal_blocks_the_entry_rather_than_defaulting():
    """None must never read as zero. A market with no fresh spot cannot satisfy the
    condition, and a NEGATIVE gate is not satisfied by absence either."""
    spec = _spec([{"metric": "spot_vs_strike", "op": ">=", "value": 1.0}])
    assert entry_signal(spec, _quote(spot_vs_strike=None)) is None
    assert entry_signal(spec, _quote()) is None
    loose = _spec([{"metric": "spot_vs_strike", "op": "<=", "value": 100}])
    assert entry_signal(loose, _quote(spot_vs_strike=None)) is None


def test_the_surviving_external_signal_is_a_real_dsl_citizen():
    """pm_divergence was retired after measurement (tests/test_evo_pm_divergence_retired.py);
    spot_vs_strike is what is left, and it must stay reachable."""
    assert "spot_vs_strike" in METRICS


# --- computing them from our own tables -------------------------------------


def _seed_crypto(session, *, strike_type="greater", floor=64000.0, cap=None,
                 spot=65000.0, age_min=1):
    session.add(CryptoSpotCandle(
        product="BTC-USD", minute_ts=NOW - timedelta(minutes=age_min), close=spot))
    session.add(CryptoLadderSnapshot(
        captured_at=NOW - timedelta(minutes=1), series="KXBTCD",
        event_ticker="KXBTCD-26AUG1215", market_ticker="KXBTCD-26AUG1215-T64000",
        strike_type=strike_type, floor_strike=floor, cap_strike=cap,
        yes_bid_cents=60.0, yes_ask_cents=64.0, mid_cents=62.0,
    ))
    session.flush()


def test_spot_vs_strike_is_signed_so_positive_always_means_yes_is_winning(
    evo_session, evo_settings
):
    """One sign convention across strike types, or an agent has to encode three
    different rules to express one idea."""
    tk = "KXBTCD-26AUG1215-T64000"

    _seed_crypto(evo_session, strike_type="greater", floor=64000.0, spot=65000.0)
    out = signals.compute_signals(evo_session, [tk], now=NOW, settings=evo_settings)
    assert round(out[tk]["spot_vs_strike"], 4) == 1.5625  # (65000-64000)/64000*100

    evo_session.query(CryptoLadderSnapshot).delete()
    evo_session.query(CryptoSpotCandle).delete()
    # 'less' market: spot BELOW the cap means YES wins -> still positive
    _seed_crypto(evo_session, strike_type="less", floor=None, cap=66000.0, spot=65000.0)
    out = signals.compute_signals(evo_session, [tk], now=NOW, settings=evo_settings)
    assert out[tk]["spot_vs_strike"] > 0


def test_spot_vs_strike_goes_negative_when_yes_is_losing(evo_session, evo_settings):
    tk = "KXBTCD-26AUG1215-T64000"
    _seed_crypto(evo_session, strike_type="greater", floor=66000.0, spot=65000.0)
    out = signals.compute_signals(evo_session, [tk], now=NOW, settings=evo_settings)
    assert out[tk]["spot_vs_strike"] < 0


def test_stale_spot_blocks_the_crypto_signal(evo_session, evo_settings):
    """Spot is the one genuinely continuous feed here, so its staleness matters most."""
    tk = "KXBTCD-26AUG1215-T64000"
    _seed_crypto(evo_session, age_min=evo_settings.signal_max_age_minutes + 5)
    out = signals.compute_signals(evo_session, [tk], now=NOW, settings=evo_settings)
    assert out.get(tk, {}).get("spot_vs_strike") is None


# --- reaching the live interpreter ------------------------------------------


# --- the backtest must not lie about coverage -------------------------------


def test_a_dataset_that_cannot_compute_a_metric_rejects_the_spec(
    evo_session, evo_settings, evo_agent
):
    """THE trap this guards. backfill_weather is a Kalshi REST archive with no
    Polymarket join, so a pm_divergence spec replayed there would return 0 trades and
    read as 'no edge' when it was never evaluated at all."""
    agent, cohort = evo_agent
    spec_doc = {
        "name": "pm_probe",
        "universe": {"series_prefixes": ["TBACK"]},
        "entry": {"side": "yes", "style": "taker",
                  "conditions": [{"metric": "spot_vs_strike", "op": ">=", "value": 5}]},
        "exit": {"mode": "settlement"},
    }
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec_doc, dataset="backfill_weather", charge_budget=False, persist=False,
    )
    assert result is None
    assert "spot_vs_strike" in err and "backfill_weather" in err


def test_the_crypto_dataset_accepts_the_metric_it_can_replay(
    evo_session, evo_settings, evo_agent
):
    agent, cohort = evo_agent
    spec_doc = {
        "name": "spot_probe",
        "universe": {"series_prefixes": ["KXBTCD"]},
        "entry": {"side": "yes", "style": "taker",
                  "conditions": [{"metric": "spot_vs_strike", "op": ">=", "value": 1}]},
        "exit": {"mode": "settlement"},
    }
    result, err = sandbox.run_backtest(
        evo_session, evo_settings, agent_uuid=agent.agent_uuid, cohort_id=cohort.id,
        spec_doc=spec_doc, dataset="crypto", charge_budget=False, persist=False,
    )
    assert err is None and result is not None  # no rows seeded, but not rejected


def test_dataset_signal_support_is_declared_not_guessed():
    assert "spot_vs_strike" in sandbox.DATASET_SIGNALS["crypto"]
    assert "spot_vs_strike" not in sandbox.DATASET_SIGNALS["backfill_weather"]
    for dataset in sandbox.DATASETS:
        assert dataset in sandbox.DATASET_SIGNALS, f"{dataset} declares no signal support"


def test_the_staleness_window_is_operator_tunable():
    """A feed's cadence can change without a deploy; the gate has to follow."""
    assert EvoSettings(_env_file=None).signal_max_age_minutes > 0


# --- asking for a source that does not exist yet ----------------------------


def test_registering_an_uncollected_source_files_a_real_ticket(
    evo_session, evo_settings, evo_agent
):
    """Registering a name used to return ok and change nothing — no fetcher, no rows,
    no path to use it — which taught agents that naming a feed obtained one. The
    honest path (a ticket the operator can action) is now the same action."""
    from kalshi_bot.evo.heartbeats import run_heartbeat
    from kalshi_bot.evo.models import EvoTicket

    agent, cohort = evo_agent
    hb = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="src-1", md=StaticMarketData(),
        cognition=__import__(
            "kalshi_bot.evo.cognition", fromlist=["ScriptedCognition"]
        ).ScriptedCognition(lambda ctx: {
            "journal": {"decision": "I want FAA delay data"},
            "actions": [{"type": "register_data_source", "name": "faa_delays",
                         "provider": "faa", "endpoint": "https://example.gov/x",
                         "update_frequency": "5m", "cost_note": "free"}],
        }),
    )
    outcome = next(o for o in hb.actions_json if o["type"] == "register_data_source")
    assert outcome["ok"] is True
    assert outcome["collected"] is False
    assert outcome["ticket_id"] is not None
    assert "NOT collected" in outcome["note"]

    ticket = evo_session.get(EvoTicket, outcome["ticket_id"])
    assert ticket.category == "external_data_pipeline"
    assert "faa_delays" in ticket.capability


def test_registering_a_source_we_really_collect_files_nothing(
    evo_session, evo_settings, evo_agent
):
    """No ticket for a feed that already works — the queue is for gaps."""
    from kalshi_bot.evo import cognition as cog
    from kalshi_bot.evo.heartbeats import run_heartbeat

    agent, cohort = evo_agent
    hb = run_heartbeat(
        evo_session, evo_settings, agent=agent, cohort=cohort, kind="routine",
        slot_id="src-2", md=StaticMarketData(),
        cognition=cog.ScriptedCognition(lambda ctx: {
            "journal": {"decision": "note the crypto feed"},
            "actions": [{"type": "register_data_source", "name": "crypto_spot",
                         "provider": "coinbase", "cost_note": "free"}],
        }),
    )
    outcome = next(o for o in hb.actions_json if o["type"] == "register_data_source")
    assert outcome["ok"] is True and outcome["collected"] is True
    assert "ticket_id" not in outcome
