"""The common-universe split: comparing the two books on the SAME market universe.

WHY THIS EXISTS. Since 2026-09-05 two LIVE-ONLY bars (the review-tier bar and the
series pause) gated the live mirror but not the twin, so the twin traded markets live
was never permitted to attempt. Measured on `Fmmsell10`/`Fmmsell10_pt4`: 177 of the
194 candidates live "never attempted" were refused at the tier bar and 15 at the
series pause (docs/OPS_FMMSELL10_PARITY_DIAGNOSIS.md). A whole-book paper figure that
includes those markets is not comparable to live at all.

What must never break, in the order in which breaking it would matter:

  * **every existing read is byte-identical.** `paper` on the payload, and every
    caller of `paper_leg`, must be untouched. The split is ADDITIVE; a page that
    silently restated its headline number would repeat the very confusion this
    exists to end.
  * **it is self-scoping.** A pair with no universe-barred rows gets `universe: None`
    and renders exactly as before. Nothing is hardcoded to one book.
  * **only the UNIVERSE bars are excluded.** The open cap, contest cap, dedup and
    daily-loss gates are CAPACITY differences on markets live COULD have taken —
    the twin exists to price those, so they stay in.
  * **exclusion fails OPEN.** A twin trade the tape has no verdict on stays in the
    comparison; a gap in evidence must under-state the correction, never silently
    delete trades from the page.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import models as m
from kalshi_bot.livedash import data, legs, pairs
from kalshi_bot.models import Base

T0 = datetime(2026, 9, 7, 2, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=6)


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _epoch(session, *, twin="mm10_pt", live="mmsell10"):
    row = m.LivePaperTwin(twin_tag=twin, live_tag=live, started_at=T0,
                          params_json={"lo": 5, "hi": 10, "maxyes": 7})
    session.add(row)
    session.flush()
    return row


def _paper_trade(session, ticker, *, tag="mm10_pt", pnl=0.14, price=93, qty=1):
    session.add(m.PaperTrade(
        market_ticker=ticker, strategy=tag, created_at=T0 + timedelta(minutes=5),
        side="no", action="buy", assumed_price=price, quantity=qty, fees=0.01,
        fill_assumption="twin", status="settled", pnl=pnl,
        closed_at=T0 + timedelta(hours=2), resolved_value=100, legacy=False))
    session.flush()


def _parity(session, ticker, *, twin="mm10_pt", live="mmsell10",
            twin_outcome="opened", parent_outcome="opened"):
    session.add(m.LivePaperParityEvent(
        twin_tag=twin, live_tag=live, market_ticker=ticker,
        recorded_at=T0 + timedelta(minutes=5),
        twin_outcome=twin_outcome, parent_outcome=parent_outcome))
    session.flush()


def _run(session):
    return data.build_run(session, "mm10_pt", now=NOW)


# --- the regression guard: nothing existing may move ---------------------------

def test_the_existing_paper_leg_is_untouched_by_the_split():
    """The whole point. `paper` keeps meaning what every existing reader thinks it
    means, barred markets included, even when the split is active."""
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXMLBTOTAL-A", pnl=0.10)
    _parity(s, "KXMLBTOTAL-A")                                   # live could take it
    _paper_trade(s, "KXNCAAFSPREAD-B", pnl=1.00)
    _parity(s, "KXNCAAFSPREAD-B", parent_outcome="skip_live_tier")  # live barred

    run = _run(s)
    assert run["paper"]["positions_closed"] == 2
    assert round(run["paper"]["realized_pnl_usd"], 2) == 1.10


def test_a_pair_with_no_barred_rows_is_completely_unaffected():
    """Self-scoping. No asymmetry, no split, page renders as before."""
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXMLBTOTAL-A", pnl=0.10)
    _parity(s, "KXMLBTOTAL-A")

    assert _run(s)["universe"] is None


# --- the split itself ----------------------------------------------------------

def test_common_excludes_the_barred_markets_and_excluded_holds_them():
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXMLBTOTAL-A", pnl=0.10)
    _parity(s, "KXMLBTOTAL-A")
    _paper_trade(s, "KXNCAAFSPREAD-B", pnl=1.00)
    _parity(s, "KXNCAAFSPREAD-B", parent_outcome="skip_live_tier")

    u = _run(s)["universe"]
    assert u["barred_tickers"] == 1
    assert u["common"]["positions_closed"] == 1
    assert round(u["common"]["realized_pnl_usd"], 2) == 0.10
    assert u["excluded"]["positions_closed"] == 1
    assert round(u["excluded"]["realized_pnl_usd"], 2) == 1.00


def test_the_series_pause_bar_counts_as_a_universe_difference_too():
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXMLBTOTAL-A", pnl=0.10)
    _parity(s, "KXMLBTOTAL-A")
    _paper_trade(s, "KXNFLSPREAD-C", pnl=0.50)
    _parity(s, "KXNFLSPREAD-C", parent_outcome="skip_live_paused")

    u = _run(s)["universe"]
    assert u["barred_tickers"] == 1
    assert round(u["common"]["realized_pnl_usd"], 2) == 0.10


def test_capacity_gates_are_NOT_excluded():
    """A contest-cap or dedup refusal is live declining a market it was ALLOWED to
    take. That is the execution cost the twin exists to measure — excluding it would
    flatter live by hiding its own capacity limits."""
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXMLBTOTAL-A", pnl=0.10)
    _parity(s, "KXMLBTOTAL-A")
    _paper_trade(s, "KXMLBTOTAL-D", pnl=0.70)
    _parity(s, "KXMLBTOTAL-D", parent_outcome="skip_contest_cap")

    assert _run(s)["universe"] is None          # no UNIVERSE asymmetry at all


def test_a_trade_with_no_parity_row_stays_in_the_comparison():
    """Fail-open. Missing evidence under-states the correction; it never deletes a
    trade from the page."""
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXNCAAFSPREAD-B", pnl=1.00)
    _parity(s, "KXNCAAFSPREAD-B", parent_outcome="skip_live_tier")
    _paper_trade(s, "KXUNKNOWN-E", pnl=0.25)   # no parity row at all

    u = _run(s)["universe"]
    assert u["common"]["positions_closed"] == 1
    assert round(u["common"]["realized_pnl_usd"], 2) == 0.25


def test_paper_leg_default_call_signature_is_unchanged():
    """Directly pins the helper every other module calls."""
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXMLBTOTAL-A", pnl=0.10)
    _paper_trade(s, "KXNCAAFSPREAD-B", pnl=1.00)
    pair = pairs.get_pair(s, "mm10_pt")
    since, _ = pair.window(NOW)
    _, _, marks = data.load_run(s, pair, now=NOW, marks=data.MARKS_NONE)

    leg = legs.paper_leg(s, "mm10_pt", since, marks)
    assert len(leg.positions) == 2


def test_the_barred_ticker_list_is_published_for_the_card():
    """The card filters its own open-market table with this. Without it the headline and
    the table below it would be scoped to two different universes — the subtler version
    of the confusion this whole split exists to end."""
    s = _session()
    _epoch(s)
    _paper_trade(s, "KXMLBTOTAL-A", pnl=0.10)
    _parity(s, "KXMLBTOTAL-A")
    _paper_trade(s, "KXNCAAFSPREAD-B", pnl=1.00)
    _parity(s, "KXNCAAFSPREAD-B", parent_outcome="skip_live_tier")

    u = _run(s)["universe"]
    assert u["tickers"] == ["KXNCAAFSPREAD-B"]
    assert len(u["tickers"]) == u["barred_tickers"]
