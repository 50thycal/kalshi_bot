"""Policy accounting and censoring checks; never exercise trading or a database."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mmsell_research_probe_common as p  # noqa: E402


def order(**changes):
    row = dict.fromkeys(p.ORDER_FIELDS)
    row.update(tag="Fmmsell10", ticker="KXTRUMPSAY-26OCT05-PRED", created=100,
               limit=94, quantity=3, status="filled", filled=3, fill_price=94,
               fee=.1, first_fill=200, settle=100, settled_at=300, context=True,
               hot=False, offset=0, start=100, end=200, ticks=3, valid_ticks=3,
               trades=1, control_at=200, touch_at=None, through_at=None)
    return row | changes


def candidate(**changes):
    row = dict.fromkeys(p.SLOT_FIELDS)
    row.update(tag="Fmmsell10", ticker="KXTRUMPSAY-26OCT05-PRED", first_at=100,
               placed=False, open_cap=True, paper_cap=False, contest_cap=False,
               tier=False, paused=False, cap_price=94, outcomes=["gate:open_cap"],
               repeated_rows=10, settle=100)
    return row | changes


def test_normalize_contracts_and_actual_fee():
    assert p.actual_net(order()) == pytest.approx(5.9)
    assert p.actual_net(order(filled=.5)) == pytest.approx(2.95)


@pytest.mark.parametrize("changes", [{"settle": None}, {"fee": None}, {"fill_price": None},
                                     {"status": "resting", "filled": 0}])
def test_missing_and_censored_are_not_zero(changes):
    assert p.actual_net(order(**changes)) is None


def test_terminal_unfilled_is_zero_even_without_fee():
    assert p.actual_net(order(status="canceled", filled=0, fee=None)) == 0


@pytest.mark.parametrize("changes", [{"context": False}, {"hot": True}, {"hot": None},
                                     {"offset": -3}, {"limit": None}])
def test_hot_or_unreadable_context_is_excluded(changes):
    assert not p.normal_order(order(**changes))


def test_price_offset_changes_price_only_after_a_proxy_hit():
    r = p.inverse_report([order(touch_at=200, through_at=None), order(ticker="OTHER", filled=0,
                              status="canceled", touch_at=None, fee=None)])
    scenarios = r["groups"]["Fmmsell10"]["scenarios"]
    assert scenarios["lower_touch"]["net_usd"] == pytest.approx(.0698)
    assert scenarios["lower_touch"]["proxy_hits"] == 1
    assert scenarios["lower_strict_through"]["proxy_hits"] == 0
    assert r["groups"]["Fmmsell10"]["verdict"].startswith("HOLD(model)")


@pytest.mark.parametrize("fill_time,expected", [(111.999, 0), (112, -5.9), (120, -5.9)])
def test_cancel_latency_boundary(fill_time, expected):
    assert p.withdrawal_delta(order(first_fill=fill_time, end=fill_time), 110) == pytest.approx(expected)


def test_cancel_discards_winners_but_avoids_losses():
    assert p.withdrawal_delta(order(settle=0), 110) == pytest.approx(94.1)
    assert p.withdrawal_delta(order(), 110) == pytest.approx(-5.9)
    assert p.withdrawal_delta(order(), None) == 0


def test_cancel_cannot_read_a_postfill_signal():
    with pytest.raises(ValueError, match="outside observed"):
        p.withdrawal_delta(order(), 200)


def test_withdrawal_replay_does_not_credit_missing_orders_or_free_slots():
    a, b = order(settle=0), order(ticker="OTHER")
    trace = dict.fromkeys(p.WITHDRAWAL_FIELDS)
    trace.update(tag=a["tag"], ticker=a["ticker"], created=a["created"], paired_ticks=2,
                 coarse_windows=1, clean_windows=1, first_coarse=110, signal_at=110)
    g = p.withdrawal_report([a, b], [trace])["groups"]["Fmmsell10"]
    signal = g["signal_variants"]["verified_withdrawal"]
    assert g["paired_window_coverage"] == .5
    assert signal["delta_usd"] == pytest.approx(.941)
    assert signal["policy_net_usd"] == pytest.approx(0)
    assert signal["verdict"].startswith("HOLD(instrument)")


def test_silent_trade_feed_cannot_be_certified_by_absence_of_errors():
    a = order(settle=0)
    trace = dict.fromkeys(p.WITHDRAWAL_FIELDS)
    trace.update(tag=a["tag"], ticker=a["ticker"], created=a["created"], paired_ticks=2,
                 coarse_windows=1, clean_windows=1, first_coarse=110, signal_at=110)
    g = p.withdrawal_report([a], [trace])["groups"]["Fmmsell10"]
    assert g["paired_window_coverage"] == 1
    assert not g["raw_trade_coverage_independently_verified"]
    assert "complete trade coverage" in g["signal_variants"]["verified_withdrawal"]["verdict"]


def test_cap_delays_are_not_missed_and_paper_cap_is_not_live_cap():
    r = p.slots_report([candidate(placed=True), candidate(ticker="KXTRUMPSAY-X",
                         open_cap=False, paper_cap=True)])
    g = r["groups"]["Fmmsell10"]
    assert g["priority_open_cap_later_placed"] == 1
    assert g["priority_open_cap_never_placed"] == 0
    assert g["missed_paper_scenario_count"] == 0
    assert g["verdict"].startswith("HOLD(accrual)")


def test_retries_do_not_create_independent_date_blocks():
    assert p.bootstrap([("2026-10-04", 1)] * 1000)["p05"] is None
    rows = [(f"2026-09-{i:02d}", 1) for i in range(1, 11)]
    assert p.bootstrap(rows) == {"dates": 10, "p05": 1.0, "p95": 1.0}


def test_export_transport_detects_missing_chunks_and_runner_failure():
    encoded = base64.b64encode(json.dumps([[1, "x"]]).encode()).decode()
    assert p.decode_export("part payload\n1 " + encoded) == [[1, "x"]]
    for bad in ["2 " + encoded, "1 " + encoded + "\n1 " + encoded,
                "Traceback\n1 " + encoded, "output capped\n1 " + encoded]:
        with pytest.raises(ValueError):
            p.decode_export(bad)


def test_sql_readonly_price_convention_and_asof_cutoff():
    for kind in ("orders", "slots", "withdrawal"):
        sql = p.export_sql(kind)
        assert "__" not in sql
        assert not any(word in sql.lower().split() for word in ("insert", "delete", "update", "drop"))
    sql = p.export_sql("orders")
    assert "yes_price_cents >= 101 - f.limit_price" in sql
    assert "yes_price_cents > 101 - f.limit_price" in sql
    assert "ts_ms <= extract(epoch FROM f.observed_end)" in sql
    assert "WHERE kalshi_order_id=o.kalshi_order_id AND status IN ('canceled','cancelled')" in sql
    assert "received_at <= p.at" in p.export_sql("withdrawal")
    assert "baseline_depth >= 40" not in p.export_sql("withdrawal")
