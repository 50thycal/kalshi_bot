"""The mmsell WIN-set canary package (WS-027 phase 2d, docs/MMSELL_WIN_SET_CANARY.md).

Pinned, in the order in which breaking it would cost money:

  * acceptance check 6 — the book trades ONLY WIN-set series that clear the live tier bar
    and both skip lists; a sports-total market (and every LOSE category) is refused;
  * the combined bound never exceeds the sum of the two books' registered per-market bounds,
    and the env value the package registers parses back to exactly the envelope's numbers;
  * the tags are fresh and prefix-safe against every tag production has ever used, and the
    paper tag cannot prefix-match the live tag;
  * Kmmsell10 is unchanged: its package objects are imported, never modified;
  * register -> arm runs end to end through `arm_live_canary` on the in-memory schema.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import mmsell_win_set_canary as ws
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_cell_size as cs
from kalshi_bot.mmsell.tracker import MmSellTracker, _live_eligible
from kalshi_bot.registry import state_of
from scripts.live_canary_patterns import LOSE, WIN, category_of

UTC = timezone.utc
T0 = datetime(2026, 9, 1, 18, 0, tzinfo=UTC)

#: Every strategy tag production had ever used, read 2026-10-16 (ops `ws027-tags-2`), mmsell
#: and live families. The weather/theta families share no prefix letter with the new tags.
PRODUCTION_TAGS = {
    "Alimm1", "Alimm1_pt3", "Cmmsell10", "Cmmsell10_pt3", "Dmmsell10", "Dmmsell10_pt4",
    "Emmsell10", "Emmsell10_pt4", "Fmmsell10", "Fmmsell10_pt4", "Gmmsell0", "Gmmsell1",
    "Hmmsell10", "Hmmsell10_pt4", "Jmmsell10", "Jmmsell10_pt4", "Kmmsell10", "Kmmsell10_pt4",
    "Lmmsell10", "Lmmsell10_pt3", "Lmmsell8", "Lmmsell8_pt3", "mmsell", "mmsell1", "mmsell10",
    "mmsell10a", "mmsell10a_pt", "mmsell10a_pt2", "mmsell10a_pt3", "mmsell10b", "mmsell10b_pt",
    "mmsell10b_pt2", "mmsell10b_pt3", "mmsell10d", "mmsell10e", "mmsell10_pt", "mmsell10_pt3",
    "mmsell11", "mmsell2", "mmsell3", "mmsell3_closeout", "mmsell4", "mmsell5", "mmsell6",
    "mmsell7", "mmsell8", "mmsell8_pt3", "mmsell9", "mmsellA1", "mmsellA2", "mmsellA3",
    "mmsellA4", "mmsellA5", "Rainmmsell1", "Rainmmsell2", "Rainmmsell3", "Rmmsell1",
    "Rmmsell2", "Tempmmsell1", "Tempmmsell2", "Tempmmsell3", "Tmmsell1", "Tmmsell2",
    "Tmmsell3", "Tmmsell4", "Tmmsell5", "Tmmsell6", "Wmmsell1", "Wmmsell2", "Wmmsell3",
    "Wmmsell4", "Wmmsell5", "Wmmsell6", "Wmmsell7", "Wmmsell8", "theta", "theta1", "theta2",
    "theta3", "theta4", "theta4_pt", "theta4_pt2", "theta4_pt3", "freeze1", "probe",
    "qacshadow1", "pin15", "momentum", "reversion", "tfav", "buy_favorite",
}


def _book(settings):
    settings.mmsell_variants = ws.LIVE_BOOK_SPEC
    (book,) = settings.mmsell_variant_list
    return book


# --- acceptance check 6: the universe -----------------------------------------------


def test_the_book_spec_parses_into_the_book_the_plan_describes(settings):
    book = _book(settings)
    assert book["tag"] == ws.LIVE_TAG
    assert sorted(book["onlyx"]) == sorted(ws.WIN_SERIES)
    assert (book["lo"], book["hi"], book["maxyes"]) == (5.0, 10.0, 7.0)
    assert (book["size"], book["contestcap"], book["contestkey"]) == (3, 1, "split")
    assert book["universe"] == "graduated"


@pytest.mark.parametrize("series", ws.WIN_SERIES)
def test_every_win_series_is_a_win_category_and_clears_both_live_bars(settings, series):
    assert category_of(series) in WIN
    assert state_of(series) == "graduated"
    assert _live_eligible(series, settings.mmsell_live_skip_series_list, "graduated")
    assert not any(series.startswith(p) for p in settings.mmsell_skip_series_list)
    assert MmSellTracker._book_admits_series(_book(settings), series)


@pytest.mark.parametrize("series", [
    "KXNFLTOTAL", "KXMLBTOTAL", "KXNFLSPREAD", "KXNBASPREAD",   # sports totals / spreads
    "KXBTCD", "KXBTC", "KXETH",                                 # crypto
    "KXNFLGAME", "KXATPMATCH",                                  # other sports
    "KXHIGHNY", "KXLOWTCHI",                                    # temperature: skipped
    "KXTRUMPSAYMONTH", "KXTRUMPSAYCOMPANY",                     # barred / in review
    "KXYTVIEWSHIGH",                                            # a category_of artifact
])
def test_anything_outside_the_win_set_is_refused(settings, series):
    assert not MmSellTracker._book_admits_series(_book(settings), series)


def test_the_sports_total_market_is_refused_end_to_end(settings):
    """The acceptance case in the handoff's words: a sports-total market, refused."""
    assert category_of("KXNFLTOTAL") in LOSE
    assert not MmSellTracker._book_admits_series(_book(settings), "KXNFLTOTAL")


def test_the_series_list_is_exactly_the_graduated_win_set_of_the_manifest():
    """A manifest change that graduates or bars a WIN series must fail here, so the list
    moves only by a new experiment — never silently. Since 2026-10-27 the newest WIN-set
    book is the successor (`mmsell_win_set_2`), which added KXTRUMPMENTION; this frozen v1
    list is that one minus the added series."""
    import json
    from pathlib import Path

    from kalshi_bot.experiment_os import mmsell_win_set_2 as ws2

    manifest = json.loads(Path("kalshi_bot/registry/series_manifest.json").read_text())
    found = sorted(
        e["series"] for e in manifest["series"]
        if category_of(e["series"]) in WIN and state_of(e["series"]) == "graduated"
        and e["series"] not in ws.CATEGORY_ARTIFACTS
    )
    assert tuple(found) == ws2.WIN_SERIES
    assert ws.WIN_SERIES == tuple(s for s in ws2.WIN_SERIES if s not in ws2.ADDED_SERIES)


# --- bounds ----------------------------------------------------------------------------


def test_the_combined_bound_is_inside_the_sum_of_the_registered_per_market_bounds():
    env = ws.RISK_ENVELOPE
    assert env["shared_market"]["max_combined_usd"] <= (
        env["max_market_exposure_usd"] + cs.RISK_ENVELOPE["max_market_exposure_usd"])
    # Kmmsell10's largest clip (3, its slow cell) plus this book's clip
    assert env["shared_market"]["max_combined_contracts"] == cs.BIG_CONTRACTS + ws.CONTRACTS


def test_the_registered_env_value_parses_to_the_envelopes_numbers(settings):
    settings.mmsell_live_shared_market_pairs = ws.SHARED_PAIR_ENV
    (pair,) = settings.mmsell_live_shared_pairs
    assert pair.tags == frozenset({ws.LIVE_TAG, cs.LIVE_TAG})
    assert pair.max_contracts == ws.COMBINED_MAX_CONTRACTS
    assert pair.max_usd == ws.COMBINED_MAX_USD
    assert pair.env_value() == ws.SHARED_PAIR_ENV


def test_the_clip_fits_the_live_dollar_cap():
    assert ws.CONTRACTS * 0.97 <= ws.RISK_ENVELOPE["max_order_dollars"]
    assert ws.CONTRACTS * 0.97 <= float(
        cs.RISK_ENVELOPE["settings"]["LIVE_MAX_ORDER_DOLLARS"])


def test_the_keep_gate_is_the_incumbents_on_this_arm_with_this_books_budget():
    by_metric = {c["metric"]: c["value"] for c in ws.KEEP_GATE_SPEC["fail_any"]}
    assert by_metric["live_realized_pnl_usd"] == -15.0
    assert by_metric["live_max_realized_loss_usd"] == 3.0
    assert "mmsell10" not in str({k: v for k, v in ws.KEEP_GATE_SPEC.items()
                                  if k != "description"})
    # the incumbent's object is untouched by the copy
    assert {c["metric"]: c["value"] for c in cs.KEEP_GATE_SPEC["fail_any"]}[
        "live_realized_pnl_usd"] == -30.0


def test_the_drift_baseline_carries_the_universe_and_the_pair():
    material = ws.material_config()["material"]
    assert material["live_strategies_contains"] == [ws.LIVE_TAG]
    assert material["twin_pairs"] == {ws.LIVE_TAG: ws.TWIN_TAG}
    assert "onlyx=" in material["book_params"][ws.LIVE_TAG]
    assert material["shared_market_pair"] == ws.SHARED_PAIR_ENV


def test_the_runtime_pair_is_compared_against_the_baseline(settings):
    from kalshi_bot.experiment_os.enforcement import _material_runtime_config

    material = ws.material_config()["material"]
    settings.mmsell_live_shared_market_pairs = ""
    assert _material_runtime_config(settings, material)["shared_market_pair"] is None
    settings.mmsell_live_shared_market_pairs = ws.SHARED_PAIR_ENV
    assert _material_runtime_config(settings, material)["shared_market_pair"] \
        == ws.SHARED_PAIR_ENV


def test_every_declared_activation_var_clears_the_ops_allowlist():
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    pkg = _packages()["mmsell-win-set-canary"]
    assert pkg.activation_vars == ws.ACTIVATION_VARS
    assert {"MMSELL_VARIANTS", "LIVE_STRATEGIES",
            "MMSELL_LIVE_SHARED_MARKET_PAIRS"} == set(pkg.activation_vars)
    for name in pkg.activation_vars:
        assert name in railway_env.ALLOWED_VARS, name


# --- tags --------------------------------------------------------------------------------


@pytest.mark.parametrize("new", [ws.LIVE_TAG, ws.PAPER_TAG, ws.TWIN_TAG])
def test_new_tags_are_fresh_and_prefix_safe_against_every_production_tag(new):
    assert new not in PRODUCTION_TAGS
    for tag in PRODUCTION_TAGS:
        assert not tag.startswith(new), tag
        if new != ws.TWIN_TAG:
            assert not new.startswith(tag), tag
    assert len(new) <= 24 and "mmsell" in new


def test_the_paper_tag_cannot_be_armed_by_the_live_tag():
    assert not ws.PAPER_TAG.startswith(ws.LIVE_TAG)
    assert not ws.LIVE_TAG.startswith(ws.PAPER_TAG)
    assert ws.TWIN_TAG == f"{ws.LIVE_TAG}_pt4"


# --- lineage ---------------------------------------------------------------------------


def test_register_refuses_a_promotion_sample_floor():
    with pytest.raises(svc.ExperimentOsError):
        ws.register(object(), actor="cal", promotion_sample_floor=150, now=T0)


def test_the_package_is_registered_and_arming_is_LIVE_OPS_only():
    from kalshi_bot.experiment_os.experiment_commands import ACTION_ROLES, _packages

    pkg = _packages()["mmsell-win-set-canary"]
    assert pkg.experiment_key == ws.EXPERIMENT_KEY
    assert pkg.register is ws.register and pkg.arm is ws.arm
    assert set(pkg.strategy_tags) == {ws.PAPER_TAG, ws.LIVE_TAG, ws.TWIN_TAG}
    assert ACTION_ROLES["ARM_CANARY"] == frozenset({"LIVE_OPS"})


def test_register_then_arm_end_to_end(xos_session, xos_platform):
    """Register touches nothing else; arm refuses on thin paper and arms on a PASS, with
    the live deployment and its twin at one instant and the baseline carrying the pair."""
    from kalshi_bot.experiment_os.read import get_experiment
    from kalshi_bot.models import PaperTrade

    s = xos_session
    out = ws.register(s, actor="cal", now=T0)
    assert out["experiment"].state == "PAPER"
    assert out["paper_deployment"].deployment_key == ws.PAPER_DEPLOYMENT_KEY
    with pytest.raises(svc.ExperimentOsError, match="not PASS"):
        ws.arm(s, approved_by="cal", started_at=T0 + timedelta(days=1))
    for i in range(30):
        s.add(PaperTrade(market_ticker=f"KXRAIN-26OCT{i:02d}-NYC", strategy=ws.PAPER_TAG,
                         status="settled", pnl=0.18, quantity=3, side="no",
                         created_at=T0 + timedelta(hours=1 + i),
                         closed_at=T0 + timedelta(hours=2 + i)))
    s.flush()
    armed = ws.arm(s, approved_by="cal", started_at=T0 + timedelta(days=3))
    live, twin = armed["live"], armed["twin"]
    assert live.started_at == twin.started_at
    assert live.config_json["material"]["shared_market_pair"] == ws.SHARED_PAIR_ENV
    assert get_experiment(s, ws.EXPERIMENT_KEY).state == "LIVE_CANARY"


# --- v2: the owner waiver of v1's paper smoke bar ------------------------------------------


def _settle(s, n, pnl, start):
    from kalshi_bot.models import PaperTrade

    for i in range(n):
        s.add(PaperTrade(market_ticker=f"KXRAIN-26OCT{i:02d}-CHI", strategy=ws.PAPER_TAG,
                         status="settled", pnl=pnl, quantity=3, side="no",
                         created_at=start + timedelta(minutes=10 * i + 1),
                         closed_at=start + timedelta(minutes=10 * i + 5)))
    s.flush()


def test_the_waiver_package_registers_and_arms_nothing():
    from kalshi_bot.experiment_os.experiment_commands import _packages

    pkg = _packages()["mmsell-win-set-owner-waiver"]
    assert pkg.experiment_key == ws.EXPERIMENT_KEY
    assert pkg.register is ws.apply_owner_waiver
    assert pkg.arm is None


def test_the_waiver_changes_only_the_promotion_bar():
    """Every live protection is v1's object: the keep gate and the envelope are not touched."""
    assert ws.OWNER_WAIVER_PROMOTION_SPEC != ws.PROMOTION_GATE_SPEC
    assert "sample" not in ws.OWNER_WAIVER_PROMOTION_SPEC
    assert ws.OWNER_WAIVER_PROMOTION_SPEC["pass_all"] == [
        {"metric": "realized_pnl_usd", "arm": ws.ARM_KEY, "op": ">", "value": -3.0}]
    svc.validate_gate_spec(ws.OWNER_WAIVER_PROMOTION_SPEC)


def test_the_waiver_refuses_a_sample_floor():
    with pytest.raises(svc.ExperimentOsError, match="no sample floor"):
        ws.apply_owner_waiver(object(), actor="cal", promotion_sample_floor=1, now=T0)


def test_waiver_then_arm_end_to_end(xos_session, xos_platform):
    """v1 refuses on thin paper; the waiver opens v2 with v1's keep gate and envelope, carries
    the paper book across, and arming then passes at once — the live protections intact."""
    from kalshi_bot.experiment_os.models import ExperimentGate
    from kalshi_bot.experiment_os.read import get_experiment, latest_version

    s = xos_session
    ws.register(s, actor="cal", now=T0)
    with pytest.raises(svc.ExperimentOsError, match="not PASS"):
        ws.arm(s, approved_by="cal", started_at=T0 + timedelta(hours=2))

    t1 = T0 + timedelta(hours=3)
    out = ws.apply_owner_waiver(s, actor="cal", now=t1)
    v2 = out["version"]
    assert v2.version == 2 and out["superseded_version"] == 1
    assert v2.risk_json == ws.RISK_ENVELOPE
    assert [k.split("-e")[0] for k in out["carried_deployments"]] == [ws.PAPER_DEPLOYMENT_KEY]
    gates = {g.gate_key: g for g in s.query(ExperimentGate).filter_by(version_id=v2.id)}
    assert gates[ws.KEEP_GATE_KEY].spec_json == ws.KEEP_GATE_SPEC
    assert gates[ws.PROMOTION_GATE_KEY].spec_json == ws.OWNER_WAIVER_PROMOTION_SPEC

    # it runs once
    with pytest.raises(svc.ExperimentOsError, match="v1 only"):
        ws.apply_owner_waiver(s, actor="cal", now=t1 + timedelta(minutes=1))

    armed = ws.arm(s, approved_by="Calvin", started_at=t1 + timedelta(minutes=5))
    assert armed["live"].started_at == armed["twin"].started_at
    assert latest_version(s, get_experiment(s, ws.EXPERIMENT_KEY)).version == 2
    assert get_experiment(s, ws.EXPERIMENT_KEY).state == "LIVE_CANARY"


def test_the_waiver_bar_still_refuses_a_paper_blowup(xos_session, xos_platform):
    s = xos_session
    ws.register(s, actor="cal", now=T0)
    t1 = T0 + timedelta(hours=3)
    ws.apply_owner_waiver(s, actor="cal", now=t1)
    _settle(s, 4, -0.90, t1)  # -$3.60 since v2
    with pytest.raises(svc.ExperimentOsError, match="not PASS"):
        ws.arm(s, approved_by="Calvin", started_at=t1 + timedelta(hours=2))


def test_the_waiver_does_not_overrule_negative_paper_evidence(xos_session, xos_platform):
    """Once v1's deciding metric has been recorded at or below zero, the waiver refuses."""
    from kalshi_bot.experiment_os.evaluator import evaluate_gate
    from kalshi_bot.experiment_os.models import ExperimentGate

    s = xos_session
    out = ws.register(s, actor="cal", now=T0)
    _settle(s, 30, -0.06, T0)
    gate = s.get(ExperimentGate, out["promotion_gate"].id)
    evaluate_gate(s, gate)
    with pytest.raises(svc.ExperimentOsError, match="negative evidence"):
        ws.apply_owner_waiver(s, actor="cal", now=T0 + timedelta(days=1))
