"""The MMSELL selection-bias audit (scripts/mmsell_selection_audit.py) — pins the pre-registered
pricing, bucketing, clustering and cell-label rules of docs/MMSELL_SELECTION_BIAS_AUDIT.md
independently of the DB and the network.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("mmsell_selection_audit",
                                               _SCRIPTS / "mmsell_selection_audit.py")
sa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sa)  # type: ignore[union-attr]

import mmsell_market_types as mt  # noqa: E402


def test_taker_fee_is_per_order_ceiling_spread_over_the_clip():
    # 1 contract at NO 92c: ceil(0.07*0.92*0.08*100)=ceil(0.515)=1c
    assert sa.taker_fee_per_ct(92, 1) == 1.0
    # 10 contracts: ceil(5.152)=6c -> 0.6c per contract
    assert math.isclose(sa.taker_fee_per_ct(92, 10), 0.6)
    assert sa.taker_fee_per_ct(100, 5) == 0.0


def test_maker_fee_has_no_ceiling():
    assert math.isclose(sa.maker_fee_per_ct(93), 0.0175 * 0.93 * 0.07 * 100)


def test_fill_qty_capped_at_depth():
    assert sa.fill_qty(3) == 3
    assert sa.fill_qty(500) == sa.SIZE
    assert sa.fill_qty(None) == 1
    assert sa.fill_qty(0) == 1


def test_taker_and_maker_pnl_signs():
    # sell YES into a 6c bid, depth 1: NO settles -> +6 - 1 fee
    assert sa.taker_pnl(6, False, 1) == 5.0
    assert sa.taker_pnl(6, True, 1) == -95.0
    assert sa.taker_pnl(0, False, 1) is None      # no bid to sell into
    m = sa.maker_pnl(7, False)
    assert 6.8 < m < 7.0
    assert sa.maker_pnl(7, True) < -92.9


def test_proxy_books_zero_unless_bid_reached_our_ask():
    assert sa.proxy_pnl(7, 6, False) == 0.0
    assert sa.proxy_pnl(7, None, False) == 0.0
    assert sa.proxy_pnl(7, 7, True) == sa.maker_pnl(7, True)


def test_bands_and_buckets():
    assert sa.price_band(7) == "<=7"
    assert sa.price_band(8) == "8-10"
    assert sa.price_band(31) == "31+"
    assert sa.hte_bucket(None) == "na"
    assert sa.hte_bucket(-0.2) == "<=0"
    assert sa.hte_bucket(0.5) == "0-1"
    assert sa.hte_bucket(1.0) == "0-1"
    assert sa.hte_bucket(100) == ">72"


def test_strike_struct_and_event_fallback():
    assert sa.strike_struct("greater", 3.5, None) == "greater|F-"
    assert sa.strike_struct(None, None, None) == "none|--"
    assert sa.event_of("KXMLBTOTAL-26AUG02KCCOL-12", None) == "KXMLBTOTAL-26AUG02KCCOL"
    assert sa.event_of("X-Y-Z", "EV") == "EV"


def test_crypto_rule_is_a_substring_exactly_like_xos_000009():
    assert sa.crypto_substring_hit("KXHEGSETHANNOUNCEOUT")    # the documented false positive
    assert sa.crypto_substring_hit("KXBTCD")
    assert not sa.crypto_substring_hit("KXMLBTOTAL")
    assert sa.is_crypto_category("Crypto")
    assert sa.is_crypto_category("Crypto (list)")
    assert not sa.is_crypto_category("Politics")


def test_live_pause_is_a_prefix():
    assert sa.live_paused("KXITFWMATCH")
    assert sa.live_paused("KXNFLSPREAD")
    assert not sa.live_paused("KXNFLTOTAL")


def test_tier_of_mirrors_registry_state_of():
    manifest = {"KXMLB": "graduated", "KXMLBSPREAD": "barred"}
    assert sa.tier_of("KXMLBSPREAD", manifest, mt.classify) == "barred"
    assert sa.tier_of("KXMLBTOTAL", manifest, mt.classify) == "graduated"
    assert sa.tier_of("KXZZZNOTASERIES", manifest, mt.classify) == "identified"


def _rows(vals, events=None, depth=20, month="2026-09"):
    events = events or [f"E{i}" for i in range(len(vals))]
    return [{"event": e, "maker": v, "taker": v - 1, "depth_bid": depth, "month": month}
            for e, v in zip(events, vals, strict=True)]


def test_cluster_stats_collapses_correlated_markets():
    indep = sa.cluster_stats(_rows([1, -1] * 50), "maker")
    clustered = sa.cluster_stats(_rows([1, 1, -1, -1] * 25,
                                       events=[f"E{i // 2}" for i in range(100)]), "maker")
    assert indep["events"] == 100 and clustered["events"] == 50
    assert clustered["se"] > indep["se"]


def test_labels():
    assert sa.label_cell(_rows([5.0] * 20), 2.58) == "THIN"
    assert sa.label_cell(_rows([-5.0, -6.0] * 60), 2.58) == "CUT"
    assert sa.label_cell(_rows([5.0, 6.0] * 60), 2.58) == "SIZE-SAFE"
    assert sa.label_cell(_rows([5.0, 6.0] * 60, depth=2), 2.58) == "HOLDS"
    maker_only = [{"event": f"E{i}", "maker": 1.0 + (i % 2) * 0.2, "taker": -2.0,
                   "depth_bid": 20, "month": "2026-09"} for i in range(120)]
    assert sa.label_cell(maker_only, 2.58) == "MAKER-ONLY"
    assert sa.label_cell(_rows([5.0] * 200), 3.29, series="KXMLBTOTAL") == "(XOS-000046)"
    assert sa.label_cell(_rows([5.0] * 200), 3.29, series="KXNFLTOTAL") == "(XOS-000047)"


def test_month_stability_gates_size_safe():
    rows = _rows([5.0, 6.0] * 30, month="2026-08") + _rows([-30.0, -29.0] * 30, month="2026-09") \
        + _rows([5.0, 6.0] * 30, month="2026-10")
    assert sa.month_stability(rows) == (3, 2)


def test_capacity_caps_at_depth():
    rows = [{"taker": 5.0, "depth_bid": 3}, {"taker": 5.0, "depth_bid": 100}]
    # (3*5 + 10*5)/100 dollars over 30 days -> per 30 days unchanged
    assert math.isclose(sa.capacity_usd_30d(rows, 30.0), 0.65)


def test_build_rows_scores_only_settled_binary(monkeypatch):
    at = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    raw = [
        ("KXMLBTOTAL-26SEP01AB-9", at, "KXMLBTOTAL", 50.0, 2.0, "greater", 8.5, None, 4, 9,
         5, 7, 300, "1-3", 8, "KXMLBTOTAL-26SEP01AB"),
        ("KXFOO-1", at, "KXFOO", 50.0, None, None, None, None, None, None, 6, 8, 300, "na", None,
         None),
        ("KXBAR-1", at, "KXBAR", 50.0, None, None, None, None, None, None, 6, 8, 300, "na", None,
         None),
    ]
    markets = {"KXMLBTOTAL-26SEP01AB-9": {"result": "no"}, "KXFOO-1": {"result": ""}}
    rows, excl = sa.build_rows(raw, markets, mt.classify)
    assert len(rows) == 1 and excl == {"unsettled/void": 1, "not found": 1}
    r = rows[0]
    assert r["band"] == "<=7" and r["proxy_filled"] and r["mtype"] == "total"
    assert r["taker"] == 5 - sa.taker_fee_per_ct(95, 4)


def test_allowlisted_on_the_ops_runner():
    import ops_runner

    assert "mmsell_selection_audit" in ops_runner.ALLOWED_SCRIPTS
