"""The mmsell10 cell-size successor (Kmmsell10): 3 contracts on the slow-information cell,
1 elsewhere, and NOTHING else moved against the size-split predecessor.

What these pin, in the order in which breaking it would cost money:

  * the keep gate and the promotion gate ARE the predecessor's objects (no quiet relaxation);
  * every dollar bound in the envelope is the predecessor's; no global setting moves;
  * the book spec the package registers is the one the worker parses, with the type rule
    inside the drift-checked book params, and the cell is exactly P-SLOW's frozen set;
  * the tags are fresh and collide with nothing under LIVE_STRATEGIES' PREFIX match;
  * Jmmsell10 drains beside it: registering never ends a live deployment.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_cell_size as cs
from kalshi_bot.experiment_os import successor_mmsell10_size_split as ss
from kalshi_bot.livedash import overview

UTC = timezone.utc
T0 = datetime(2026, 10, 11, 18, 0, tzinfo=UTC)


# --- gates ---------------------------------------------------------------------


def test_the_promotion_gate_is_the_predecessors_object():
    assert cs.PROMOTION_GATE_SPEC is ss.PROMOTION_GATE_SPEC


def test_the_keep_gate_is_the_predecessors_contract_unchanged():
    mine, theirs = cs.KEEP_GATE_SPEC, ss.KEEP_GATE_SPEC
    assert {k: v for k, v in mine.items() if k != "description"} == {
        k: v for k, v in theirs.items() if k != "description"}
    by_metric = {c["metric"]: c["value"] for c in mine["fail_any"]}
    assert by_metric["live_realized_pnl_usd"] == -30.0
    assert by_metric["live_max_realized_loss_usd"] == 3.0


def test_register_refuses_a_promotion_sample_floor():
    with pytest.raises(svc.ExperimentOsError):
        cs.register(object(), actor="cal", promotion_sample_floor=150, now=T0)


# --- the envelope --------------------------------------------------------------


def test_the_envelope_moves_only_sizing_prose_and_the_footprint_estimate():
    mine, theirs = cs.RISK_ENVELOPE, ss.RISK_ENVELOPE
    assert set(mine) == set(theirs)
    changed = {k for k in mine if mine[k] != theirs[k]}
    assert changed == {"stage", "contracts_per_order", "expected_book_exposure_usd"}
    assert mine["expected_book_exposure_usd"] < theirs["expected_book_exposure_usd"]
    for key in ("max_order_dollars", "max_market_exposure_usd", "max_event_exposure_usd",
                "max_book_exposure_usd", "total_canary_loss_budget_usd", "settings"):
        assert mine[key] == theirs[key], key


def test_the_largest_clip_fits_the_unchanged_dollar_caps():
    assert cs.BIG_CONTRACTS * 0.97 <= cs.RISK_ENVELOPE["max_order_dollars"]
    assert cs.BIG_CONTRACTS * 0.97 <= float(cs.RISK_ENVELOPE["settings"]["LIVE_MAX_ORDER_DOLLARS"])


def test_every_declared_activation_var_clears_the_ops_allowlist():
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    pkg = _packages()["mmsell-cell-size-canary"]
    assert pkg.activation_vars == cs.ACTIVATION_VARS
    assert {"MMSELL_VARIANTS", "LIVE_STRATEGIES"} <= pkg.activation_vars
    for name in pkg.activation_vars:
        assert name in railway_env.ALLOWED_VARS, name


# --- the book spec ---------------------------------------------------------------


def test_the_cell_is_exactly_p_slows_frozen_set_and_the_dashboards():
    assert frozenset(cs.SLOW_CELL_TYPES) == overview.SLOW_CELL_TYPES


def test_the_book_spec_parses_into_the_book_the_plan_describes(settings):
    settings.mmsell_variants = cs.LIVE_BOOK_SPEC
    (book,) = settings.mmsell_variant_list
    assert book["tag"] == cs.LIVE_TAG
    assert sorted(book["bigtypes"]) == sorted(cs.SLOW_CELL_TYPES)
    assert (book["bigsize"], book["size"]) == (3, 1)
    assert book["sizes"] is None
    assert (book["contestcap"], book["contestkey"]) == (1, "split")
    assert (book["lo"], book["hi"], book["maxyes"]) == (5.0, 10.0, 7.0)


def test_only_the_sizing_keys_move_against_the_predecessors_book():
    pred = dict(kv.split("=") for kv in ss.BOOK_PARAMS.split(","))
    mine = dict(kv.split("=") for kv in cs.BOOK_PARAMS.split(","))
    assert set(pred) - set(mine) == {"sizes"}
    assert set(mine) - set(pred) == {"size", "bigtypes", "bigsize"}
    assert {k: v for k, v in mine.items() if k in pred} == {
        k: v for k, v in pred.items() if k != "sizes"}


def test_the_drift_baseline_carries_the_type_rule():
    material = cs.material_config()["material"]
    assert material["live_strategies_contains"] == [cs.LIVE_TAG]
    assert material["twin_pairs"] == {cs.LIVE_TAG: cs.TWIN_TAG}
    assert "bigsize=3" in material["book_params"][cs.LIVE_TAG]
    assert "bigtypes=" in material["book_params"][cs.LIVE_TAG]


# --- tags --------------------------------------------------------------------------


def test_tags_are_fresh_derived_and_fit_the_column():
    used = {"Fmmsell10", "Fmmsell10_pt4", "Hmmsell10", "Hmmsell10_pt4", "Jmmsell10",
            "Jmmsell10_pt4", "mmsell10"}
    assert cs.LIVE_TAG not in used and cs.TWIN_TAG not in used
    assert cs.TWIN_TAG == f"{cs.LIVE_TAG}{cs.RISK_ENVELOPE['settings']['LIVE_PAPER_TWIN_SUFFIX']}"
    assert len(cs.TWIN_TAG) <= 24
    assert cs.PAPER_TAG == ss.PAPER_TAG == "mmsell10"


def test_no_running_tag_is_a_prefix_of_the_live_tag_or_vice_versa():
    running = {"mmsell5", "mmsell6", "mmsell7", "mmsell8", "mmsell9", "mmsell10", "Tmmsell1",
               "Tmmsell2", "Tmmsell5", "Tmmsell6", "Cmmsell10", "Dmmsell10", "Gmmsell0",
               "Gmmsell1", "Gmmsell2", "Fmmsell10", "Hmmsell10", "Jmmsell10", "Rmmsell1",
               "Rmmsell2", "Alimm1", "Alimm1_pt3", "Jmmsell10_pt4", "theta4", "Lmmsell8",
               "Lmmsell10"}
    for tag in running:
        assert not tag.startswith(cs.LIVE_TAG), tag
        assert not cs.LIVE_TAG.startswith(tag), tag


# --- lineage -------------------------------------------------------------------------


def test_a_draining_live_book_does_NOT_block_the_successor(monkeypatch):
    """Jmmsell10 winds down beside Kmmsell10: its live deployment must not be ended."""
    seen = {}

    class _Pred:
        id = 9

    class _Live:
        id, kind, ended_at = 1, "live", None
        deployment_key = "mmsell-sizesplit-live-2"

    class _Session:
        def scalars(self, *a, **k):
            return type("R", (), {"all": staticmethod(lambda: [_Live()])})()

    monkeypatch.setattr(cs, "get_experiment",
                        lambda s, key: _Pred() if key == cs.PREDECESSOR_KEY else None)
    monkeypatch.setattr(cs, "_epoch_experiment_id", lambda s, d: _Pred.id)
    monkeypatch.setattr(cs, "_tags_of", lambda s, d: ["Jmmsell10"])

    import kalshi_bot.repository as repo
    monkeypatch.setattr(repo, "count_live_book_open", lambda s, tag: 15)
    monkeypatch.setattr(svc, "end_deployment",
                        lambda *a, **k: seen.setdefault("ended", []).append(a))
    monkeypatch.setattr(svc, "create_experiment",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached")))

    with pytest.raises(RuntimeError, match="reached"):
        cs.register(_Session(), actor="cal", now=T0)
    assert "ended" not in seen


def test_register_refuses_to_run_twice(monkeypatch):
    monkeypatch.setattr(cs, "get_experiment", lambda s, key: object())
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        cs.register(object(), actor="cal", now=T0)


def test_arm_refuses_before_the_contract_exists(monkeypatch):
    monkeypatch.setattr(cs, "get_experiment", lambda s, key: None)
    with pytest.raises(svc.ExperimentOsError, match="REGISTER_PACKAGE first"):
        cs.arm(object(), approved_by="cal")


def test_the_package_is_registered_and_arming_is_LIVE_OPS_only():
    from kalshi_bot.experiment_os.experiment_commands import ACTION_ROLES, _packages

    pkg = _packages()["mmsell-cell-size-canary"]
    assert pkg.experiment_key == cs.SUCCESSOR_KEY
    assert pkg.register is cs.register and pkg.arm is cs.arm
    assert set(pkg.strategy_tags) == {cs.LIVE_TAG, cs.TWIN_TAG}
    assert ACTION_ROLES["ARM_CANARY"] == frozenset({"LIVE_OPS"})
