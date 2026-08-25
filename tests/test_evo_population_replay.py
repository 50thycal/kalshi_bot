"""Replay determinism, the no-look-ahead boundary, and the virtual ledger."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.evo.population import genome as g
from kalshi_bot.evo.population import proving, replay


@pytest.fixture
def corpus():
    return proving.register()


def _doc(series="KXSYNTHA", **entry):
    e = {
        "side": "yes",
        "min_price_cents": 10,
        "max_price_cents": 90,
        "size_contracts": 5,
    }
    e.update(entry)
    doc, err = g.normalize(
        g.spec_document(
            name=f"probe-{series.lower()}",
            family="test",
            universe={
                "series_prefixes": [series],
                "max_spread_cents": 10,
                "max_hours_to_close": 48,
            },
            entry=e,
            exit_={"mode": "settlement"},
        )
    )
    assert err is None
    return doc


# ---------------------------------------------------------------------------
# Windows / no look-ahead
# ---------------------------------------------------------------------------


def test_window_past_the_cutoff_is_refused_not_trimmed():
    with pytest.raises(replay.ReplayRefused, match="look-ahead refused"):
        replay.check_window("2026-01-01", "2026-06-01", "2026-03-01")


def test_open_ended_window_is_refused_when_a_cutoff_exists():
    with pytest.raises(replay.ReplayRefused, match="open-ended"):
        replay.check_window("2026-01-01", None, "2026-03-01")


def test_inverted_window_is_refused():
    with pytest.raises(replay.ReplayRefused, match="after window end"):
        replay.check_window("2026-06-01", "2026-01-01", None)


def test_window_within_the_cutoff_is_accepted():
    assert replay.check_window("2026-01-01", "2026-02-01", "2026-03-01") == (
        "2026-01-01",
        "2026-02-01",
    )


def test_no_trade_settles_after_the_window(evo_session, evo_settings, corpus):
    start, end = proving.window(0, 40)
    result = replay.replay(
        evo_session, evo_settings, document=_doc(), dataset=corpus,
        window_start=start, window_end=end, data_cutoff=end,
        starting_capital_usd=500.0,
    )
    assert result.outcome["n_trades"] > 0
    latest = max(t["exited_at"] for t in result.trades if t["exited_at"])
    assert latest.date().isoformat() <= end


def test_a_later_window_reveals_markets_the_earlier_one_could_not_see(
    evo_session, evo_settings, corpus
):
    """The cutoff hides markets rather than merely excluding them from the result."""
    early_start, early_end = proving.window(0, 20)
    late_start, late_end = proving.window(0, 60)
    early = replay.replay(
        evo_session, evo_settings, document=_doc(), dataset=corpus,
        window_start=early_start, window_end=early_end, data_cutoff=early_end,
        starting_capital_usd=500.0,
    )
    late = replay.replay(
        evo_session, evo_settings, document=_doc(), dataset=corpus,
        window_start=late_start, window_end=late_end, data_cutoff=late_end,
        starting_capital_usd=500.0,
    )
    assert late.outcome["n_trades"] > early.outcome["n_trades"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_same_genome_over_the_same_window_reproduces(
    evo_session, evo_settings, corpus
):
    start, end = proving.window(0, 40)
    kwargs = dict(
        dataset=corpus, window_start=start, window_end=end, data_cutoff=end,
        starting_capital_usd=500.0,
    )
    a = replay.replay(evo_session, evo_settings, document=_doc(), **kwargs)
    b = replay.replay(evo_session, evo_settings, document=_doc(), **kwargs)
    assert (
        a.reproducibility["outcome_fingerprint"]
        == b.reproducibility["outcome_fingerprint"]
    )
    assert a.outcome == b.outcome


def test_candidates_do_not_contaminate_each_other(evo_session, evo_settings, corpus):
    start, end = proving.window(0, 40)
    kwargs = dict(
        dataset=corpus, window_start=start, window_end=end, data_cutoff=end,
        starting_capital_usd=500.0,
    )
    a = replay.replay(evo_session, evo_settings, document=_doc("KXSYNTHA"), **kwargs)
    b = replay.replay(evo_session, evo_settings, document=_doc("KXSYNTHB"), **kwargs)
    a_again = replay.replay(evo_session, evo_settings, document=_doc("KXSYNTHA"), **kwargs)
    assert a.outcome != b.outcome
    assert a.outcome == a_again.outcome
    assert not ({t["ticker"] for t in a.trades} & {t["ticker"] for t in b.trades})


def test_an_unknown_dataset_is_refused(evo_session, evo_settings, corpus):
    with pytest.raises(replay.ReplayRefused, match="unknown dataset"):
        replay.replay(
            evo_session, evo_settings, document=_doc(), dataset="not-a-dataset",
            window_start="2026-01-01", window_end="2026-02-01",
            data_cutoff="2026-02-01", starting_capital_usd=500.0,
        )


def test_an_invalid_genome_is_refused_rather_than_run(evo_session, evo_settings, corpus):
    bad = g.spec_document(
        name="bad-band", family="test",
        entry={"min_price_cents": 80, "max_price_cents": 20},
    )
    with pytest.raises(replay.ReplayRefused, match="invalid genome"):
        replay.replay(
            evo_session, evo_settings, document=bad, dataset=corpus,
            window_start="2026-01-01", window_end="2026-02-01",
            data_cutoff="2026-02-01", starting_capital_usd=500.0,
        )


# ---------------------------------------------------------------------------
# Integrity
# ---------------------------------------------------------------------------


def test_a_corrupt_book_is_flagged_not_traded(evo_session, evo_settings, corpus):
    """KXSYNTHD's tape contains a crossed quote. The engine must skip it and say so —
    trading against bid>ask would mint P&L out of a data defect."""
    start, end = proving.window(0, 40)
    result = replay.replay(
        evo_session, evo_settings, document=_doc("KXSYNTHD"), dataset=corpus,
        window_start=start, window_end=end, data_cutoff=end,
        starting_capital_usd=500.0,
    )
    assert result.integrity["crossed_quotes"] > 0
    assert result.integrity["data_broken"] is True


def test_a_clean_corpus_is_not_flagged(evo_session, evo_settings, corpus):
    start, end = proving.window(0, 40)
    result = replay.replay(
        evo_session, evo_settings, document=_doc("KXSYNTHA"), dataset=corpus,
        window_start=start, window_end=end, data_cutoff=end,
        starting_capital_usd=500.0,
    )
    assert result.integrity["data_broken"] is False


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _trade(ticker, pnl, qty=5, price=50.0, fees=0.1, start_h=0, end_h=1):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "ticker": ticker,
        "month": "2026-01",
        "pnl": pnl,
        "fees": fees,
        "quantity": qty,
        "entry_price_cents": price,
        "entered_at": base + timedelta(hours=start_h),
        "exited_at": base + timedelta(hours=end_h),
        "settled": True,
        "win": pnl > 0,
        "exit": "settlement",
        "cents_per_contract": 100.0 * pnl / qty,
        "maker_yes_c": None,
    }


def test_ledger_ties_to_its_own_tape():
    trades = [_trade("KXA-1", 2.0), _trade("KXA-2", -1.0), _trade("KXB-1", 0.5)]
    led = replay.build_ledger(trades, starting_capital_usd=100.0)
    assert led.realized_pnl_usd == pytest.approx(1.5)
    assert led.fees_usd == pytest.approx(0.3)
    assert led.gross_pnl_usd == pytest.approx(1.8)
    assert led.contracts == 15
    assert led.markets == 3
    assert led.ending_capital_usd == pytest.approx(101.5)


def test_drawdown_is_chronological_not_iteration_order():
    """The replay visits markets one at a time, so its own trade order is not
    chronological. A drawdown read off that order would be an artifact of iteration."""
    trades = [
        _trade("KXA-1", +10.0, start_h=0, end_h=1),   # first chronologically
        _trade("KXB-1", -10.0, start_h=20, end_h=21),  # last chronologically
        _trade("KXA-2", -10.0, start_h=10, end_h=11),  # middle
    ]
    led = replay.build_ledger(trades, starting_capital_usd=100.0)
    # Chronologically: +10, 0, -10 → peak 10, trough -10 → drawdown 20.
    assert led.max_drawdown_usd == pytest.approx(20.0)


def test_concurrency_and_exposure_come_from_overlapping_intervals():
    overlapping = [
        _trade("KXA-1", 1.0, qty=4, price=50.0, start_h=0, end_h=10),
        _trade("KXB-1", 1.0, qty=4, price=50.0, start_h=5, end_h=15),
    ]
    led = replay.build_ledger(overlapping, starting_capital_usd=100.0)
    assert led.max_concurrent_positions == 2
    assert led.peak_exposure_usd == pytest.approx(4.0)  # 2 × 4 contracts @ 50c

    sequential = [
        _trade("KXA-1", 1.0, qty=4, price=50.0, start_h=0, end_h=5),
        _trade("KXB-1", 1.0, qty=4, price=50.0, start_h=5, end_h=10),
    ]
    led2 = replay.build_ledger(sequential, starting_capital_usd=100.0)
    assert led2.max_concurrent_positions == 1, (
        "a position that ends exactly when another begins never held capital at the "
        "same time"
    )


def test_capital_breach_is_measured():
    heavy = [
        _trade(f"KX{i}-1", 0.0, qty=100, price=90.0, start_h=0, end_h=10)
        for i in range(3)
    ]
    led = replay.build_ledger(heavy, starting_capital_usd=100.0)
    assert led.peak_exposure_usd > 100.0
    assert led.capital_breached is True


def test_concentration_reflects_event_spread():
    concentrated = [_trade("KXA-1", 1.0), _trade("KXA-2", 1.0), _trade("KXA-3", 1.0)]
    spread = [_trade("KXA-1", 1.0), _trade("KXB-1", 1.0), _trade("KXC-1", 1.0)]
    assert (
        replay.build_ledger(concentrated, starting_capital_usd=100.0).concentration_hhi
        > replay.build_ledger(spread, starting_capital_usd=100.0).concentration_hhi
    )


def test_empty_tape_is_a_valid_empty_ledger():
    led = replay.build_ledger([], starting_capital_usd=100.0)
    assert led.realized_pnl_usd == 0.0
    assert led.ending_capital_usd == 100.0
    assert led.capital_breached is False


# ---------------------------------------------------------------------------
# Shared-engine isolation and settlement-time accounting
# ---------------------------------------------------------------------------


def test_crossed_book_skipping_is_opt_in(evo_session, evo_settings, corpus):
    """Refusing to trade a step changes what every existing caller's replay returns,
    which is a shared execution-semantics change. It stays off by default until
    Platform Change Review rules on it; only the search tool opts in."""
    from kalshi_bot.evo import sandbox

    doc = _doc("KXSYNTHD")  # the corpus whose tape carries crossed quotes
    start, end = proving.window(0, 40)
    common = dict(
        agent_uuid="test", cohort_id=0, spec_doc=doc, dataset=corpus,
        date_from=start, date_to=end, charge_budget=False, persist=False,
    )
    default, err1 = sandbox.run_backtest(evo_session, evo_settings, **common)
    strict, err2 = sandbox.run_backtest(
        evo_session, evo_settings, **common, skip_crossed_quotes=True
    )
    assert err1 is None and err2 is None

    # Counting is unconditional — the defect is visible to every caller...
    assert default["crossed_quotes"] > 0
    assert strict["crossed_quotes"] == default["crossed_quotes"]
    # ...but only the opt-in run changes what it trades.
    assert strict["n_trades"] <= default["n_trades"]


def test_the_population_layer_opts_into_strict_handling(evo_session, evo_settings, corpus):
    start, end = proving.window(0, 40)
    result = replay.replay(
        evo_session, evo_settings, document=_doc("KXSYNTHD"), dataset=corpus,
        window_start=start, window_end=end, data_cutoff=end,
        starting_capital_usd=500.0,
    )
    assert result.integrity["data_broken"] is True


def test_settlement_exits_are_marked_inexact(evo_session, evo_settings, corpus):
    """A settlement trade's exit timestamp is the last candle observed, which is a lower
    bound — settlement happens at or after it, not at it."""
    start, end = proving.window(0, 40)
    result = replay.replay(
        evo_session, evo_settings, document=_doc("KXSYNTHA"), dataset=corpus,
        window_start=start, window_end=end, data_cutoff=end,
        starting_capital_usd=500.0,
    )
    settled = [t for t in result.trades if t["exit"] == "settlement"]
    assert settled, "the steady corpus holds to settlement"
    assert all(t["exit_time_exact"] is False for t in settled)


def test_concurrency_excludes_inexact_exits_and_reports_coverage():
    """Using a lower bound as the close would end positions early and understate
    overlap, so those trades are left out and the shortfall is reported."""
    exact = [
        _trade("KXA-1", 1.0, qty=4, price=50.0, start_h=0, end_h=10),
        _trade("KXB-1", 1.0, qty=4, price=50.0, start_h=5, end_h=15),
    ]
    for t in exact:
        t["exit_time_exact"] = True
    led = replay.build_ledger(exact, starting_capital_usd=100.0)
    assert led.max_concurrent_positions == 2
    assert led.concurrency_coverage == 1.0

    mixed = list(exact) + [
        dict(_trade("KXC-1", 1.0, qty=4, price=50.0, start_h=0, end_h=20),
             exit_time_exact=False)
    ]
    led2 = replay.build_ledger(mixed, starting_capital_usd=100.0)
    assert led2.concurrency_coverage < 1.0
    # The inexact trade overlapped both others but must not inflate the figure.
    assert led2.max_concurrent_positions == 2


def test_drawdown_still_uses_every_trade():
    """Ordering tolerates a lower bound — settlement is at or after the last
    observation, so the sequence holds — and dropping those trades would silently
    remove most of the equity curve."""
    trades = [
        dict(_trade("KXA-1", +10.0, start_h=0, end_h=1), exit_time_exact=False),
        dict(_trade("KXB-1", -10.0, start_h=20, end_h=21), exit_time_exact=False),
        dict(_trade("KXA-2", -10.0, start_h=10, end_h=11), exit_time_exact=False),
    ]
    led = replay.build_ledger(trades, starting_capital_usd=100.0)
    assert led.max_drawdown_usd == pytest.approx(20.0)
    assert led.realized_pnl_usd == pytest.approx(-10.0)
    assert led.concurrency_coverage == 0.0
