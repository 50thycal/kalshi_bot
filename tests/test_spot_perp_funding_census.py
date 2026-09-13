"""Missing funding cannot become a yield; all allocated cash counts."""
import json
from datetime import timedelta

import pytest

from scripts import spot_perp_funding_census as c


def row(at=None, **kw):
    return {"market_ticker": "KXBTCPERP", "funding_time": (at or c.START).isoformat(),
            "funding_rate": -0.001, "mark_price": "100", **kw}


def test_all_capital_and_both_assets_count_once():
    for scenario in c.capital_scenarios():
        cap = scenario["total_capital_usd"]
        assert scenario["spot_usd"] + scenario["perp_collateral_usd"] + scenario["cash_reserve_usd"] == cap
        assert 2 * scenario["per_asset_spot_usd"] == scenario["spot_usd"]
    primary = c.capital_scenarios()[1]
    assert primary["spot_usd"] == 800
    assert primary["minimum_net_annual_return_for_100_monthly_pct"] == 60
    costs = primary["hypothetical_cost_scenarios"][0]
    assert costs["four_leg_fees_usd_flat_prices"] == pytest.approx(1.92)
    assert costs["funding_needed_for_100_usd_month"] == pytest.approx(105.92)


def test_signed_rates_retained_without_interpreting_units():
    rows, errors = c.validate({"funding_rates": [row()]}, "KXBTCPERP", c.START, c.END)
    assert not errors
    assert rows[0]["rate_raw"] == -0.001


@pytest.mark.parametrize("change", [{"funding_rate": "NaN"}, {"mark_price": 0},
                                    {"market_ticker": "OTHER"}, {"funding_time": "2026-08-14"},
                                    {"funding_rate": True}])
def test_invalid_row_blocks_inference(change):
    rows, errors = c.validate({"funding_rates": [row(**change)]}, "KXBTCPERP", c.START, c.END)
    assert not rows and errors


def test_chunk_boundary_is_owned_by_next_chunk():
    rows, errors = c.validate({"funding_rates": [row(c.START), row(c.END)]},
                              "KXBTCPERP", c.START, c.END)
    assert len(rows) == 1 and not errors


def test_access_denial_stops_history_requests():
    calls = []
    def denied(url):
        calls.append(url)
        return {"status": 403, "error": "http_error"}
    result = c.collect("KXBTCPERP", denied)
    assert result["count"] == 0
    assert result["errors"] == ["history_request_failed"]
    assert sum("historical" in u for u in calls) == 1


def test_duplicate_payments_and_unexpected_paging_are_errors():
    def duplicate(url):
        return {"status": 200, "payload": {"funding_rates": [row(), row()], "cursor": "more"}}
    result = c.collect("KXBTCPERP", duplicate)
    assert "duplicate_payment_times" in result["errors"]
    assert "unexpected_pagination" in result["errors"]


def test_offline_never_fetches_or_claims_profit(monkeypatch, capsys):
    monkeypatch.setattr(c, "collect", lambda *_: pytest.fail("network called"))
    assert c.main(["--capital-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "HOLD" and result["realized_net_pnl_usd"] is None
    assert not result["data_floor_met"]


def test_empty_schema_and_out_of_window_not_success():
    for payload in ({}, {"funding_rates": [row(c.START - timedelta(seconds=1))]}):
        rows, errors = c.validate(payload, "KXBTCPERP", c.START, c.END)
        assert not rows and errors
