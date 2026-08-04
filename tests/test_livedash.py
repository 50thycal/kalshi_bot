"""Live-vs-paper comparison dashboard: pairing, P&L, divergence, series, API.

The fixtures build the same record shapes the real writers produce
(`kalshi_bot/live/executor.py` reconcile, `kalshi_bot/twin/harness.py`), so the
accounting under test is the accounting that runs in production:

* a live entry is `live_orders` + `fills`, and a settled live position is a
  `positions` snapshot with `quantity=0` and the exchange's own `realized_pnl`;
* a paper entry is a `paper_trades` row whose `pnl` is filled in at settlement by
  `qty x (resolved_value - assumed_price) / 100 - entry_fee`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import models as m
from kalshi_bot.livedash import compare, data, events, legs, pairs, series
from kalshi_bot.models import Base

T0 = datetime(2026, 7, 26, 21, 0, tzinfo=timezone.utc)
NOW = T0 + timedelta(hours=6)
TH = compare.Thresholds()


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def _epoch(session, *, twin="mm10_pt", live="mmsell10", started=T0, ended=None, params=None):
    row = m.LivePaperTwin(twin_tag=twin, live_tag=live, started_at=started, ended_at=ended,
                          params_json=params or {"lo": 5, "hi": 10, "maxyes": 7})
    session.add(row)
    session.flush()
    return row


def _tick(session, ticker, at, *, no_bid=93, no_ask=95):
    session.add(m.MmSellPositionTick(
        market_ticker=ticker, captured_at=at, no_bid=no_bid, no_ask=no_ask,
        yes_bid=100 - no_ask, yes_ask=100 - no_bid, mid=(100 - no_ask + 100 - no_bid) / 2))


def _live_entry(session, ticker, *, at=None, price=93, qty=2, fee=0.01, strategy="mmsell10",
                order_id=None, status="filled", fill=True):
    at = at or T0 + timedelta(minutes=5)
    oid = order_id or f"ord-{ticker}"
    session.add(m.LiveOrder(
        kalshi_order_id=oid, client_order_id=f"c-{oid}", market_ticker=ticker,
        strategy=strategy, created_at=at, side="no", action="buy",
        limit_price=price, quantity=qty, status=status))
    if fill:
        session.add(m.Fill(
            kalshi_fill_id=f"f-{oid}", kalshi_order_id=oid, market_ticker=ticker,
            filled_at=at + timedelta(seconds=30), side="no", action="buy",
            price=price, quantity=qty, fee=fee))
    session.flush()


def _live_settle(session, ticker, *, at=None, pnl=0.14):
    """The reconcile loop's settlement row: quantity 0 plus the exchange's realized P&L."""
    session.add(m.Position(
        market_ticker=ticker, captured_at=at or T0 + timedelta(hours=3), side="no",
        quantity=0, quantity_fp=0.0, avg_price=None, market_exposure=0.0, realized_pnl=pnl))
    session.flush()


def _live_open(session, ticker, *, at=None, qty=2, price=93):
    session.add(m.Position(
        market_ticker=ticker, captured_at=at or T0 + timedelta(hours=1), side="no",
        quantity=-qty, quantity_fp=-float(qty), avg_price=float(price),
        market_exposure=qty * price / 100.0, realized_pnl=0.0))
    session.flush()


def _paper_trade(session, ticker, *, tag="mm10_pt", at=None, price=93, qty=2, fee=0.01,
                 status="open", pnl=None, closed_at=None, resolved=None):
    row = m.PaperTrade(
        market_ticker=ticker, strategy=tag, created_at=at or T0 + timedelta(minutes=5),
        side="no", action="buy", assumed_price=price, quantity=qty, fees=fee,
        fill_assumption="twin", status=status, pnl=pnl, closed_at=closed_at,
        resolved_value=resolved, legacy=False)
    session.add(row)
    session.flush()
    return row


def _load(session, pair=None, *, now=NOW):
    pair = pair or pairs.get_pair(session, "mm10_pt")
    return data.load_run(session, pair, now=now)


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def test_pairing_comes_from_the_explicit_epoch_row():
    session = _session()
    _epoch(session)
    pair = pairs.get_pair(session, "mm10_pt")
    assert pair.live_tag == "mmsell10" and pair.twin_tag == "mm10_pt"
    assert pair.started_at == T0 and pair.is_open
    assert pairs.get_pair(session, "nope") is None
    # the default is the newest OPEN run, never a hardcoded tag
    assert pairs.default_pair(session).twin_tag == "mm10_pt"


def test_legacy_live_runs_are_reported_unpaired_not_guessed():
    """A live strategy with no epoch row must never be matched to a similarly named
    paper book — a confident wrong pairing is worse than a visible gap."""
    session = _session()
    _epoch(session)
    _live_entry(session, "AAA")
    _live_entry(session, "BBB", strategy="mmsell3", order_id="ord-old")
    # a paper book with a temptingly similar name exists
    _paper_trade(session, "BBB", tag="mmsell3")

    unpaired = pairs.unpaired_live_strategies(session)
    assert [u["live_tag"] for u in unpaired] == ["mmsell3"]
    assert "no live_paper_twins epoch row" in unpaired[0]["reason"]
    # and it is absent from the paired run list entirely
    assert [p.twin_tag for p in pairs.list_pairs(session)] == ["mm10_pt"]


def test_both_legs_are_scoped_to_the_epoch_window():
    """Records that predate the epoch belong to a different run and must not leak in."""
    session = _session()
    _epoch(session)
    _live_entry(session, "BEFORE", at=T0 - timedelta(days=2), order_id="ord-before")
    _live_entry(session, "AFTER", at=T0 + timedelta(minutes=5))
    _paper_trade(session, "BEFORE", at=T0 - timedelta(days=2))
    _paper_trade(session, "AFTER")

    live, paper, _ = _load(session)
    assert [p.ticker for p in live.positions] == ["AFTER"]
    assert [p.ticker for p in paper.positions] == ["AFTER"]


def test_closed_historical_run_uses_its_own_end_not_now():
    session = _session()
    ended = T0 + timedelta(hours=2)
    _epoch(session, ended=ended)
    pair = pairs.get_pair(session, "mm10_pt")
    since, until = pair.window(NOW)
    assert (since, until) == (T0, ended)
    assert pairs.pair_status(session, pair, now=NOW)["live"]["state"] in ("ended", "no_activity")


def test_params_snapshot_is_filtered_before_publication():
    session = _session()
    _epoch(session, params={"lo": 5, "api_key": "sk-LEAK", "note": "ok",
                            "database_url": "postgresql://u:p@h/d", "nested": {"x": 1}})
    published = pairs.get_pair(session, "mm10_pt").to_dict()["params"]
    assert published == {"lo": 5, "note": "ok"}


def test_retirement_note_is_published_alongside_ended_at():
    # repo.reconcile_stale_twin_epochs (kalshi_bot/repository.py) writes an explanatory `notes`
    # string when it closes a book dropped from LIVE_STRATEGIES; the dashboard's whole reason for
    # publishing `notes` is so an operator sees WHY a pair stopped, not just THAT it did.
    session = _session()
    ended = T0 + timedelta(hours=2)
    row = _epoch(session, ended=ended)
    row.notes = "mmsell10 removed from LIVE_STRATEGIES — no longer trading"
    session.flush()
    d = pairs.get_pair(session, "mm10_pt").to_dict()
    assert d["ended_at"] is not None
    assert d["notes"] == "mmsell10 removed from LIVE_STRATEGIES — no longer trading"


def test_a_still_running_pair_publishes_no_note():
    session = _session()
    _epoch(session)  # no notes set -- the default, matching every currently-live pair
    d = pairs.get_pair(session, "mm10_pt").to_dict()
    assert d["ended_at"] is None
    assert d["notes"] is None


# ---------------------------------------------------------------------------
# P&L extraction
# ---------------------------------------------------------------------------


def test_live_leg_reads_fills_positions_and_exchange_realized_pnl():
    session = _session()
    _epoch(session)
    _live_entry(session, "WIN", price=93, qty=2, fee=0.01)
    _live_settle(session, "WIN", pnl=0.13)
    _live_entry(session, "HOLD", price=90, qty=3, fee=0.02, order_id="ord-hold")
    _live_open(session, "HOLD", qty=3, price=90)
    _tick(session, "HOLD", NOW - timedelta(minutes=5), no_bid=94)
    session.flush()

    live, _paper, _marks = _load(session)
    by = live.by_ticker()
    assert by["WIN"].status == "settled" and by["WIN"].realized_pnl_usd == 0.13
    assert by["WIN"].entry_price_cents == 93 and by["WIN"].quantity == 2
    hold = by["HOLD"]
    assert hold.status == "open" and hold.mark_cents == 94
    # 3 contracts, cost 90c, marked 94c -> +$0.12
    assert hold.unrealized_pnl_usd == pytest.approx(0.12)
    s = live.summary()
    assert s["realized_pnl_usd"] == pytest.approx(0.13)
    assert s["unrealized_pnl_usd"] == pytest.approx(0.12)
    assert s["entry_fees_usd"] == pytest.approx(0.03)


def test_a_submitted_order_is_never_counted_as_a_fill():
    """An order that rested and was cancelled is not a position and carries no P&L —
    and it is the one state the paper twin cannot produce."""
    session = _session()
    _epoch(session)
    _live_entry(session, "RESTED", status="canceled", fill=False)
    live, _p, _m = _load(session)
    pos = live.by_ticker()["RESTED"]
    assert pos.status == "unfilled"
    assert pos.quantity is None and pos.realized_pnl_usd is None
    assert live.summary()["positions_unfilled"] == 1
    assert live.summary()["positions_closed"] == 0
    assert live.summary()["realized_pnl_usd"] == 0.0


def test_paper_leg_uses_the_simulator_formula_and_marks_off_the_shared_tick():
    session = _session()
    _epoch(session)
    # settled winner: 2 x (100-93)/100 - 0.01 = 0.13
    _paper_trade(session, "WIN", status="settled", pnl=0.13, resolved=100,
                 closed_at=T0 + timedelta(hours=3))
    _paper_trade(session, "HOLD", price=90, qty=3, fee=0.02)
    _tick(session, "HOLD", NOW - timedelta(minutes=5), no_bid=94)
    session.flush()

    _live, paper, _marks = _load(session)
    s = paper.summary()
    assert s["realized_pnl_usd"] == pytest.approx(0.13)
    assert s["unrealized_pnl_usd"] == pytest.approx(0.12)
    assert paper.by_ticker()["WIN"].resolved_value_cents == 100


def test_missing_mark_is_reported_not_valued_at_cost():
    session = _session()
    _epoch(session)
    _paper_trade(session, "NOTICKS")
    _live, paper, mark_index = _load(session)
    assert paper.summary()["unrealized_pnl_usd"] is None  # not 0.0
    assert any("unrealized P&L incomplete" in n for n in paper.notes)
    assert mark_index.coverage()["missing_count"] == 1


def test_win_rate_is_none_until_something_closes():
    session = _session()
    _epoch(session)
    _paper_trade(session, "OPEN")
    _live_entry(session, "OPEN")
    _live_open(session, "OPEN")
    _tick(session, "OPEN", NOW - timedelta(minutes=1))
    session.flush()
    live, paper, _ = _load(session)
    assert live.summary()["win_rate"] is None
    assert paper.summary()["win_rate"] is None
    assert live.summary()["pnl_per_contract_cents"] is None


def test_empty_run_yields_zeroed_but_labelled_legs():
    session = _session()
    _epoch(session)
    live, paper, _ = _load(session)
    assert live.summary()["positions_total"] == 0
    assert paper.summary()["positions_total"] == 0
    assert "no live orders in this epoch" in live.notes
    assert live.summary()["realized_pnl_usd"] == 0.0


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def _mismatched_run(session):
    """Live filled one market at a worse price and missed another the twin took."""
    _epoch(session)
    # matched market, live paid 1c more and settled slightly worse
    _live_entry(session, "MATCH", price=94, qty=2, fee=0.02)
    _live_settle(session, "MATCH", pnl=0.10)
    _paper_trade(session, "MATCH", price=93, qty=2, fee=0.01, status="settled",
                 pnl=0.13, resolved=100, closed_at=T0 + timedelta(hours=3))
    # twin-only market: live rested and never filled
    _live_entry(session, "MISSED", status="canceled", fill=False, order_id="ord-missed")
    _paper_trade(session, "MISSED", price=92, qty=2, fee=0.01, status="settled",
                 pnl=0.15, resolved=100, closed_at=T0 + timedelta(hours=4))
    session.flush()


def test_comparison_labels_each_metric_against_its_own_tolerance():
    session = _session()
    _mismatched_run(session)
    live, paper, _ = _load(session)
    c = compare.compare_legs(live, paper, TH, now=NOW)
    rows = {r["metric"]: r for r in c["rows"]}

    entry = rows["Entry price (matched markets)"]
    assert entry["difference"] == pytest.approx(1.0)   # live paid 1c more
    assert entry["verdict"] == compare.MATERIAL
    assert rows["Live orders that never filled"]["live"] == 1
    assert rows["Live orders that never filled"]["verdict"] == compare.MATERIAL
    assert rows["Realized P&L"]["difference"] == pytest.approx(0.18)
    assert c["worst_verdict"] == compare.MATERIAL


def test_a_metric_with_one_side_missing_says_so_rather_than_matching():
    session = _session()
    _epoch(session)
    _paper_trade(session, "P", status="settled", pnl=0.1, resolved=100,
                 closed_at=T0 + timedelta(hours=1))
    live, paper, _ = _load(session)
    rows = {r["metric"]: r for r in compare.compare_legs(live, paper, TH, now=NOW)["rows"]}
    assert rows["Win rate"]["verdict"] == compare.CANNOT_COMPARE
    assert rows["Entry price (matched markets)"]["verdict"] == compare.CANNOT_COMPARE
    # Live realized is a genuine 0.00 and paper's is 0.10 — inside the $0.25
    # tolerance — but live has NOTHING closed, so a numeric "match" would be a lie.
    assert rows["Realized P&L"]["live"] == 0.0
    assert rows["Realized P&L"]["verdict"] == compare.MISSING_LIVE
    assert "not a match" in rows["Realized P&L"]["note"]
    assert rows["Realized P&L per contract"]["verdict"] == compare.MISSING_LIVE


def test_stale_data_is_labelled_stale():
    session = _session()
    _epoch(session)
    _live_entry(session, "OLD")
    _live_settle(session, "OLD", at=T0 + timedelta(minutes=10))
    _paper_trade(session, "OLD", status="settled", pnl=0.1, resolved=100,
                 closed_at=T0 + timedelta(minutes=10))
    session.flush()
    live, paper, _ = _load(session, now=T0 + timedelta(days=2))
    rows = {r["metric"]: r
            for r in compare.compare_legs(live, paper, TH, now=T0 + timedelta(days=2))["rows"]}
    assert rows["Data freshness"]["verdict"] == compare.STALE


def test_thresholds_come_from_dashboard_env_only():
    loose = compare.Thresholds.from_env({"LIVEDASH_ENTRY_PRICE_MATERIAL_CENTS": "5"})
    assert loose.entry_price_material_cents == 5.0
    # a junk value degrades to the default instead of breaking the page
    assert compare.Thresholds.from_env(
        {"LIVEDASH_PNL_MATERIAL_USD": "not-a-number"}).pnl_material_usd == 1.00


# ---------------------------------------------------------------------------
# Divergence
# ---------------------------------------------------------------------------


def test_divergence_components_sum_to_the_total_gap_exactly():
    session = _session()
    _mismatched_run(session)
    live, paper, _ = _load(session)
    d = compare.decompose(live, paper, TH)

    assert d["total_gap_usd"] == pytest.approx(0.18)   # paper 0.28 vs live 0.10
    assert d["reconciles"] is True
    assert d["reconciliation_check_usd"] == pytest.approx(0.0, abs=1e-6)
    by = {c["key"]: c for c in d["components"]}
    # the missed market is attributed to fills, not smeared into price/fees
    assert by["missed_fills"]["amount_usd"] == pytest.approx(0.15)
    assert by["missed_fills"]["tickers"] == ["MISSED"]
    # live paid 1c more on 2 contracts = $0.02 against it
    assert by["entry_price"]["amount_usd"] == pytest.approx(0.02)
    assert by["fees"]["amount_usd"] == pytest.approx(0.01)
    assert d["matched_settled"] == 1


def test_residual_is_reported_not_absorbed():
    """A gap the records cannot explain must surface as residual, not be silently
    folded into a named bucket."""
    session = _session()
    _epoch(session)
    _live_entry(session, "ODD", price=93, qty=2, fee=0.01)
    _live_settle(session, "ODD", pnl=0.05)          # exchange says less than our model
    _paper_trade(session, "ODD", price=93, qty=2, fee=0.01, status="settled",
                 pnl=0.13, resolved=100, closed_at=T0 + timedelta(hours=2))
    session.flush()
    live, paper, _ = _load(session)
    d = compare.decompose(live, paper, TH)
    by = {c["key"]: c for c in d["components"]}
    assert by["entry_price"]["amount_usd"] == pytest.approx(0.0)
    assert by["fees"]["amount_usd"] == pytest.approx(0.0)
    assert by["residual"]["amount_usd"] == pytest.approx(0.08)
    assert "NOT distributed" in by["residual"]["explanation"]


def test_quantity_difference_is_its_own_component():
    session = _session()
    _epoch(session)
    _live_entry(session, "PART", price=93, qty=1, fee=0.01)   # partial fill
    _live_settle(session, "PART", pnl=0.06)
    _paper_trade(session, "PART", price=93, qty=2, fee=0.01, status="settled",
                 pnl=0.13, resolved=100, closed_at=T0 + timedelta(hours=2))
    session.flush()
    live, paper, _ = _load(session)
    by = {c["key"]: c for c in compare.decompose(live, paper, TH)["components"]}
    assert by["quantity"]["amount_usd"] == pytest.approx(0.06)  # 1 extra ct at live's rate
    assert by["quantity"]["tickers"] == ["PART"]


def test_verdict_is_too_early_before_enough_matched_markets():
    session = _session()
    _mismatched_run(session)
    live, paper, _ = _load(session)
    v = compare.decompose(live, paper, TH)["verdict"]
    assert v["code"] == "too_early" and "1 market" in v["detail"]


def test_discrepancies_name_missed_fills_prices_and_quantities():
    session = _session()
    _mismatched_run(session)
    live, paper, _ = _load(session)
    kinds = {d["kind"] for d in compare.discrepancies(live, paper, TH)}
    assert {"missed_fill", "entry_price"} <= kinds
    missed = next(d for d in compare.discrepancies(live, paper, TH)
                  if d["kind"] == "missed_fill")
    assert missed["ticker"] == "MISSED"
    assert missed["likely_source"] == "live order placed but never filled"
    assert missed["first_observed"] is not None


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


def test_pnl_series_uses_marks_as_of_each_point_never_the_latest_price():
    session = _session()
    _epoch(session)
    _paper_trade(session, "T", price=90, qty=2, fee=0.0)
    _live_entry(session, "T", price=90, qty=2, fee=0.0)
    _live_open(session, "T", qty=2, price=90)
    _tick(session, "T", T0 + timedelta(hours=1), no_bid=91)   # +$0.02
    _tick(session, "T", T0 + timedelta(hours=2), no_bid=95)   # +$0.10
    session.flush()

    live, paper, mk = _load(session)
    s = series.build_pnl_series(live, paper, mk, since=T0, until=NOW)
    first, last = s["points"][0], s["points"][-1]
    # the early point is valued at the EARLY book, not the later 95c
    assert first["paper_gross"] == pytest.approx(0.02)
    assert last["paper_gross"] == pytest.approx(0.10)
    assert first["difference"] == pytest.approx(0.0)


def test_series_flags_a_point_it_cannot_fully_value():
    session = _session()
    _epoch(session)
    _paper_trade(session, "HASTICK", price=90, qty=2, fee=0.0)
    _paper_trade(session, "NOTICK", price=90, qty=2, fee=0.0)
    _tick(session, "HASTICK", T0 + timedelta(hours=1), no_bid=92)
    session.flush()
    live, paper, mk = _load(session)
    s = series.build_pnl_series(live, paper, mk, since=T0, until=NOW)
    assert s["points"] and all(p["partial"] for p in s["points"])
    # the covered position is still valued; the gap is flagged, not zero-filled
    assert s["points"][-1]["paper_gross"] == pytest.approx(0.04)


def test_series_respects_a_time_range_and_is_ordered():
    session = _session()
    _epoch(session)
    _paper_trade(session, "T", price=90, qty=2, fee=0.0)
    for i in range(1, 6):
        _tick(session, "T", T0 + timedelta(hours=i), no_bid=90 + i)
    session.flush()
    live, paper, mk = _load(session)
    full = series.build_pnl_series(live, paper, mk, since=T0, until=NOW)
    assert len(full["points"]) == 5
    stamps = [p["at"] for p in full["points"]]
    assert stamps == sorted(stamps)

    narrow = series.build_pnl_series(
        live, paper, mk, since=T0 + timedelta(hours=3), until=T0 + timedelta(hours=4))
    assert len(narrow["points"]) == 2


def test_empty_series_says_so_instead_of_charting_nothing():
    session = _session()
    _epoch(session)
    live, paper, mk = _load(session)
    s = series.build_pnl_series(live, paper, mk, since=T0, until=NOW)
    assert s["points"] == [] and "no market ticks" in s["note"]


def test_realized_enters_the_series_at_its_settlement_time():
    session = _session()
    _epoch(session)
    settle_at = T0 + timedelta(hours=2)
    _paper_trade(session, "T", price=90, qty=2, fee=0.0, status="settled", pnl=0.20,
                 resolved=100, closed_at=settle_at)
    for i in range(1, 5):
        _tick(session, "T", T0 + timedelta(hours=i), no_bid=91)
    session.flush()
    live, paper, mk = _load(session)
    pts = series.build_pnl_series(live, paper, mk, since=T0, until=NOW)["points"]
    before = [p for p in pts if p["at"] < settle_at.isoformat()]
    after = [p for p in pts if p["at"] >= settle_at.isoformat()]
    assert before[-1]["paper_realized"] == 0.0        # not yet settled
    assert after[-1]["paper_realized"] == pytest.approx(0.20)


def test_price_series_marks_both_sides_execution():
    session = _session()
    _epoch(session)
    _live_entry(session, "T", price=93, qty=2)
    _paper_trade(session, "T", price=92, qty=2)
    _tick(session, "T", T0 + timedelta(hours=1))
    session.flush()
    live, paper, _mk = _load(session)
    p = series.build_price_series(session, "T", live, paper, since=T0, until=NOW)
    kinds = {e["kind"] for e in p["events"]}
    assert {"live_order", "live_fill", "paper_fill"} <= kinds
    paper_ev = next(e for e in p["events"] if e["kind"] == "paper_fill")
    assert paper_ev["price_cents"] == 92 and paper_ev["status"] == "assumed"
    assert p["points"] and p["points"][0]["no_bid"] == 93


def test_focus_ticker_prefers_a_market_live_missed():
    session = _session()
    _mismatched_run(session)
    live, paper, _ = _load(session)
    assert series.pick_focus_ticker(live, paper) == "MISSED"


def test_excursions_return_nulls_when_the_series_is_too_short():
    assert series.excursions([], "live")["max_drawdown_usd"] is None
    assert series.excursions([{"live_gross": 1.0, "partial": False}], "live")["samples"] == 1


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_timeline_contains_only_this_pair():
    session = _session()
    _epoch(session)
    _live_entry(session, "MINE")
    _live_entry(session, "THEIRS", strategy="mmsell3", order_id="ord-other")
    _paper_trade(session, "MINE")
    _paper_trade(session, "OTHERBOOK", tag="mmsell3")
    session.flush()
    pair = pairs.get_pair(session, "mm10_pt")
    payload = events.fetch_events(session, pair, limit=100, now=NOW)
    tickers = {e["ticker"] for e in payload["events"] if e["ticker"]}
    assert "THEIRS" not in tickers and "OTHERBOOK" not in tickers
    assert "MINE" in tickers


def test_decision_tape_is_its_own_category_and_not_in_the_default_view():
    session = _session()
    _epoch(session)
    _paper_trade(session, "MINE")
    for i in range(40):
        session.add(m.LivePaperParityEvent(
            recorded_at=T0 + timedelta(minutes=i), twin_tag="mm10_pt", live_tag="mmsell10",
            market_ticker=f"CAND{i}", twin_outcome="opened", twin_price=93,
            live_outcome="gate:open_cap"))
    session.flush()
    pair = pairs.get_pair(session, "mm10_pt")
    default = events.fetch_events(session, pair, limit=50, now=NOW)
    assert not any(e["category"] == "decisions" for e in default["events"])
    # the one paper trade is NOT buried by 40 tape rows
    assert any(e["kind"] == "paper_order_simulated" for e in default["events"])
    decisions = events.fetch_events(session, pair, category="decisions", limit=50, now=NOW)
    assert len(decisions["events"]) == 40
    assert all(e["category"] == "decisions" for e in decisions["events"])


def test_event_pagination_walks_the_whole_run_without_repeats():
    session = _session()
    _epoch(session)
    for i in range(30):
        _paper_trade(session, f"T{i}", at=T0 + timedelta(minutes=i))
    session.flush()
    pair = pairs.get_pair(session, "mm10_pt")
    seen, cursor, pages = [], None, 0
    while pages < 10:
        page = events.fetch_events(session, pair, limit=10, cursor=cursor, now=NOW)
        seen.extend(e["id"] for e in page["events"])
        cursor = page["next_cursor"]
        pages += 1
        if not cursor:
            break
    assert pages > 1
    assert len(seen) == len(set(seen)) == 30


def test_environment_filter_and_gate_breakdown():
    session = _session()
    _epoch(session)
    _live_entry(session, "L")
    _paper_trade(session, "P")
    session.add_all([
        m.LivePaperParityEvent(recorded_at=T0 + timedelta(minutes=1), twin_tag="mm10_pt",
                               live_tag="mmsell10", market_ticker="A",
                               twin_outcome="opened", live_outcome="placed"),
        m.LivePaperParityEvent(recorded_at=T0 + timedelta(minutes=2), twin_tag="mm10_pt",
                               live_tag="mmsell10", market_ticker="B",
                               twin_outcome="opened", live_outcome="gate:open_cap"),
    ])
    session.flush()
    pair = pairs.get_pair(session, "mm10_pt")
    live_only = events.fetch_events(session, pair, environment="live", limit=50, now=NOW)
    assert all(e["environment"] == "live" for e in live_only["events"])

    gates = events.gate_breakdown(session, pair, now=NOW)
    assert gates["total_candidates"] == 2 and gates["live_placed"] == 1
    blocked = [o for o in gates["outcomes"] if o["blocked"]]
    assert [o["outcome"] for o in blocked] == ["gate:open_cap"]


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def test_run_payload_shape_and_diagnostics():
    session = _session()
    _mismatched_run(session)
    d = data.build_run(session, "mm10_pt", now=NOW)
    for key in ("pair", "status", "window", "live", "paper", "incumbent_paper",
                "comparison", "divergence", "discrepancies", "gates", "marks", "diagnostics"):
        assert key in d
    # the diagnostics explain each headline figure's provenance
    diag = d["diagnostics"]
    assert diag["pairing"]["how"] == "explicit epoch row in live_paper_twins"
    assert "revenue/100" in diag["live_realized_pnl_usd"]["formula"]
    assert "resolved_value" in diag["paper_realized_pnl_usd"]["formula"]
    assert "SAME tick" in diag["unrealized_pnl_usd"]["caveat"]
    assert data.build_run(session, "does-not-exist", now=NOW) is None


def test_runs_payload_lists_history_and_never_hardcodes_a_run():
    session = _session()
    _epoch(session, twin="old_pt", started=T0 - timedelta(days=7),
           ended=T0 - timedelta(days=6))
    _epoch(session)
    payload = data.build_runs(session, now=NOW)
    assert [r["twin_tag"] for r in payload["runs"]] == ["mm10_pt", "old_pt"]
    assert payload["default_run"] == "mm10_pt"   # the open one
    assert payload["runs"][1]["open"] is False


def test_orders_payload_pairs_by_market_and_flags_unpaired():
    session = _session()
    _mismatched_run(session)
    d = data.build_orders(session, "mm10_pt", now=NOW)
    by_env_market = {(o["environment"], o["market"]): o for o in d["orders"]}
    assert by_env_market[("live", "MATCH")]["paired_order_id"] is not None
    assert by_env_market[("live", "MATCH")]["unpaired"] is False
    # a live order that never filled reports no filled quantity rather than 0
    missed = by_env_market[("live", "MISSED")]
    assert missed["filled_quantity"] is None and missed["status"] == "canceled"
    paper_missed = by_env_market[("paper", "MISSED")]
    assert paper_missed["filled_quantity"] == paper_missed["requested_quantity"]


def test_orders_pagination():
    session = _session()
    _epoch(session)
    for i in range(12):
        _paper_trade(session, f"T{i}", at=T0 + timedelta(minutes=i))
    session.flush()
    first = data.build_orders(session, "mm10_pt", limit=5, now=NOW)
    assert len(first["orders"]) == 5 and first["total"] == 12 and first["has_more"]
    last = data.build_orders(session, "mm10_pt", limit=5, offset=10, now=NOW)
    assert len(last["orders"]) == 2 and last["has_more"] is False


def test_series_payload_and_ticker_override():
    session = _session()
    _mismatched_run(session)
    _tick(session, "MATCH", T0 + timedelta(hours=1))
    _tick(session, "MISSED", T0 + timedelta(hours=1), no_bid=92)
    session.flush()
    d = data.build_series(session, "mm10_pt", now=NOW)
    assert d["price"]["ticker"] == "MISSED"     # the instructive one
    assert set(d["available_tickers"]) == {"MATCH", "MISSED"}
    override = data.build_series(session, "mm10_pt", ticker="MATCH", now=NOW)
    assert override["price"]["ticker"] == "MATCH"


def test_no_unrelated_strategy_leaks_into_any_payload():
    session = _session()
    _mismatched_run(session)
    _live_entry(session, "OTHER", strategy="weather_fav_h11", order_id="ord-w")
    _paper_trade(session, "OTHERPAPER", tag="theta1")
    session.flush()
    import json

    blob = json.dumps([
        data.build_run(session, "mm10_pt", now=NOW),
        data.build_runs(session, now=NOW),
        data.build_orders(session, "mm10_pt", now=NOW),
        data.build_events(session, "mm10_pt", now=NOW),
        data.build_series(session, "mm10_pt", now=NOW),
    ], default=str)
    assert "OTHERPAPER" not in blob and "theta1" not in blob
    # the unrelated live strategy appears ONLY in the explicit unpaired list
    assert blob.count("weather_fav_h11") == 1
    assert "OTHER\"" not in blob


def test_no_secrets_in_any_payload():
    session = _session()
    _epoch(session, params={"lo": 5, "kalshi_api_key": "sk-LEAK-ME",
                            "database_url": "postgresql://user:pw@host/db"})
    _live_entry(session, "T")
    session.add(m.SystemEvent(
        created_at=T0 + timedelta(minutes=1), level="ERROR", component="live",
        message="mmsell10 order failed", raw_json={"authorization": "Bearer SECRETTOKEN"}))
    session.flush()
    # a raw exchange payload exists on the order row and must not be published
    order = session.scalars(__import__("sqlalchemy").select(m.LiveOrder)).first()
    order.raw_order_json = {"api_key": "sk-RAW-ORDER-LEAK"}
    session.flush()

    import json

    blob = json.dumps([
        data.build_run(session, "mm10_pt", now=NOW),
        data.build_runs(session, now=NOW),
        data.build_orders(session, "mm10_pt", now=NOW),
        data.build_events(session, "mm10_pt", now=NOW),
    ], default=str).lower()
    for needle in ("sk-leak-me", "sk-raw-order-leak", "secrettoken", "postgresql://",
                   "authorization", "api_key", "traceback"):
        assert needle not in blob, f"leaked: {needle}"


def test_module_contains_no_write_path():
    """Read-only is a property of the code, not just of the routes."""
    import pathlib

    pkg = pathlib.Path(legs.__file__).parent
    for path in pkg.rglob("*.py"):
        source = path.read_text()
        for forbidden in ("session.add(", "session.commit(", "session.delete(",
                          "session.merge(", "session.execute(insert", "session.bulk_"):
            assert forbidden not in source, f"{path.name} contains a write: {forbidden}"
