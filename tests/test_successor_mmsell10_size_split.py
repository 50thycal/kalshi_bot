"""The mmsell10 size-split successor (Hmmsell10): a randomized 1-vs-3 clip, the contest key
corrected, and the envelope restated ONLY where a 3-lot makes the old number wrong.

What these pin, in the order in which breaking it would cost money:

  * the restatement is exactly what the plan says — every other envelope key and every other
    keep/stop clause is the predecessor's, asserted structurally;
  * the predecessor's frozen objects are not mutated by the restatement;
  * the global knobs the activation touches are the two the plan names, and the shared ones
    (MAX_DAILY_LOSS, MAX_MARKET_EXPOSURE, LIVE_PAPER_TWIN_SUFFIX) are not moved;
  * the book spec the package registers is the one the worker parses, with the size split and
    the corrected key inside the drift-checked book params;
  * the tags are fresh and collide with nothing under LIVE_STRATEGIES' PREFIX match.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import canary_mmsell10 as v2
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_contest_cap as cc
from kalshi_bot.experiment_os import successor_mmsell10_size_split as ss

UTC = timezone.utc
T0 = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)


# --- gates ---------------------------------------------------------------------


def test_the_promotion_gate_is_the_predecessors_object():
    assert ss.PROMOTION_GATE_SPEC is v2.PROMOTION_GATE_SPEC
    assert ss.PROMOTION_GATE_SPEC is cc.PROMOTION_GATE_SPEC


def test_the_keep_gate_differs_from_the_predecessors_by_exactly_two_thresholds():
    """A successor that quietly relaxed a stop would defeat pre-registration. Everything but
    the loss budget and the per-market loss bound (and the prose description) is equal."""
    mine, theirs = ss.KEEP_GATE_SPEC, v2.KEEP_GATE_SPEC
    assert set(mine) == set(theirs)
    for key in mine:
        if key not in ("description", "fail_any"):
            assert mine[key] == theirs[key], key
    assert len(mine["fail_any"]) == len(theirs["fail_any"])
    changed = []
    for a, b in zip(mine["fail_any"], theirs["fail_any"], strict=True):
        if a != b:
            assert {k for k in a if a[k] != b.get(k)} == {"value"}, a
            changed.append((a["metric"], b["value"], a["value"]))
    assert sorted(changed) == [("live_max_realized_loss_usd", 1.0, 3.0),
                               ("live_realized_pnl_usd", -15.0, -30.0)]


def test_the_restatement_did_not_mutate_the_predecessors_frozen_spec():
    by_metric = {c["metric"]: c["value"] for c in v2.KEEP_GATE_SPEC["fail_any"]}
    assert by_metric["live_realized_pnl_usd"] == -15.0
    assert by_metric["live_max_realized_loss_usd"] == 1.0
    assert cc.RISK_ENVELOPE["max_order_dollars"] == 1.00
    assert cc.RISK_ENVELOPE["settings"]["LIVE_MAX_ORDER_DOLLARS"] == "1.0"


def test_the_per_market_loss_bound_admits_exactly_one_3_lot():
    """At the old 1.0 the FIRST 3-lot loss reads as 'envelope not applied' and stops a canary
    working as designed; above 3 x 97c it would stop catching a real envelope failure."""
    bound = ss.RESTATED_KEEP_THRESHOLDS["live_max_realized_loss_usd"]
    assert 3 * 0.97 <= bound < 4 * 0.90


def test_no_gate_clause_references_the_contest_key_or_the_size_split():
    import json

    clauses = [{k: v for k, v in spec.items() if k != "description"}
               for spec in (ss.KEEP_GATE_SPEC, ss.PROMOTION_GATE_SPEC)]
    blob = json.dumps(clauses).lower()
    for word in ("contest", "sizes", "split"):
        assert word not in blob, word


def test_register_refuses_a_promotion_sample_floor():
    with pytest.raises(svc.ExperimentOsError):
        ss.register(object(), actor="cal", promotion_sample_floor=150, now=T0)


# --- the envelope --------------------------------------------------------------


EXPECTED_CHANGED = {"max_order_dollars", "max_market_exposure_usd", "max_event_exposure_usd",
                    "max_book_exposure_usd", "total_canary_loss_budget_usd",
                    "contracts_per_order", "settings"}
EXPECTED_ADDED = {"contest_key", "expected_book_exposure_usd", "portfolio_note"}


def test_the_envelope_differs_from_the_predecessors_only_where_planned():
    mine, theirs = dict(ss.RISK_ENVELOPE), dict(cc.RISK_ENVELOPE)
    for prose in ("stage", "shard_note", "live_tier_note"):
        mine.pop(prose, None)
        theirs.pop(prose, None)
    added = set(mine) - set(theirs)
    removed = set(theirs) - set(mine)
    changed = {k for k in set(mine) & set(theirs) if mine[k] != theirs[k]}
    assert added == EXPECTED_ADDED, added
    assert not removed, removed
    assert changed == EXPECTED_CHANGED, changed
    # Named because the plan names them: band-adjacent and timing bounds do NOT move.
    for key in ("max_event_rungs", "max_contest_positions", "max_open_positions",
                "daily_realized_loss_stop_usd", "order_timeout_seconds",
                "entry_price_offset_cents", "max_events_per_settlement_date",
                "settlement_date_concentration_pct"):
        assert ss.RISK_ENVELOPE[key] == cc.RISK_ENVELOPE[key], key


def test_the_dollar_caps_admit_exactly_one_3_lot():
    assert ss.RISK_ENVELOPE["max_order_dollars"] == 3.00
    assert ss.RISK_ENVELOPE["max_market_exposure_usd"] == 3.00
    assert float(ss.RISK_ENVELOPE["settings"]["LIVE_MAX_ORDER_DOLLARS"]) >= 3 * 0.97
    assert max(ss.SIZES) == 3


def test_settings_change_only_the_two_named_globals_and_drop_the_shared_ones():
    mine, theirs = ss.RISK_ENVELOPE["settings"], cc.RISK_ENVELOPE["settings"]
    changed = {k for k in set(mine) & set(theirs) if mine[k] != theirs[k]}
    assert changed == {"LIVE_MAX_ORDER_DOLLARS"}
    assert set(mine) - set(theirs) == {"MAX_TOTAL_EXPOSURE"}
    # Shared with the incentive book and raised by operator decision; never re-asserted here.
    assert set(theirs) - set(mine) == {"MAX_MARKET_EXPOSURE", "MAX_DAILY_LOSS"}
    assert mine["LIVE_PAPER_TWIN_SUFFIX"] == theirs["LIVE_PAPER_TWIN_SUFFIX"] == "_pt4"
    assert not {n for n in mine if "CONTEST_CAP" in n}


def test_every_declared_activation_var_clears_the_ops_allowlist():
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    pkg = _packages()["mmsell-size-split-canary"]
    assert pkg.activation_vars == ss.ACTIVATION_VARS
    assert {"MMSELL_VARIANTS", "LIVE_STRATEGIES"} <= pkg.activation_vars
    for name in pkg.activation_vars:
        assert name in railway_env.ALLOWED_VARS, name


# --- the book spec ---------------------------------------------------------------


def test_the_book_spec_parses_into_the_book_the_plan_describes(settings):
    settings.mmsell_variants = ss.LIVE_BOOK_SPEC
    (book,) = settings.mmsell_variant_list
    assert book["tag"] == ss.LIVE_TAG
    assert book["sizes"] == ss.SIZES == (1, 3)
    assert book["size"] is None
    assert (book["contestcap"], book["contestkey"]) == (1, "split")
    assert (book["lo"], book["hi"], book["maxyes"]) == (5.0, 10.0, 7.0)


def test_only_the_size_and_the_key_move_against_the_predecessors_book():
    pred = dict(kv.split("=") for kv in cc.BOOK_PARAMS.split(","))
    mine = dict(kv.split("=") for kv in ss.BOOK_PARAMS.split(","))
    assert set(pred) - set(mine) == {"size"}
    assert set(mine) - set(pred) == {"sizes", "contestkey"}
    assert {k: v for k, v in mine.items() if k in pred} == {
        k: v for k, v in pred.items() if k != "size"}


def test_the_drift_baseline_carries_the_split_and_the_key():
    material = ss.material_config()["material"]
    assert material["live_strategies_contains"] == [ss.LIVE_TAG]
    assert material["twin_pairs"] == {ss.LIVE_TAG: ss.TWIN_TAG}
    assert "sizes=1+3" in material["book_params"][ss.LIVE_TAG]
    assert "contestkey=split" in material["book_params"][ss.LIVE_TAG]


# --- tags --------------------------------------------------------------------------


def test_tags_are_fresh_derived_and_fit_the_column():
    generations = {v2.LIVE_TAG, v2.TWIN_TAG, cc.LIVE_TAG, cc.TWIN_TAG, "Fmmsell10",
                   "Fmmsell10_pt4", "mmsell10"}
    assert ss.LIVE_TAG not in generations and ss.TWIN_TAG not in generations
    assert ss.TWIN_TAG == f"{ss.LIVE_TAG}{ss.RISK_ENVELOPE['settings']['LIVE_PAPER_TWIN_SUFFIX']}"
    assert len(ss.TWIN_TAG) <= 24
    assert ss.PAPER_TAG == cc.PAPER_TAG == "mmsell10"


def test_no_running_tag_is_a_prefix_of_the_live_tag_or_vice_versa():
    """Production MMSELL_VARIANTS / LIVE_STRATEGIES tags read 2026-10-03 (ops rl-cn-env-1)."""
    running = {"mmsell5", "mmsell6", "mmsell7", "mmsell8", "mmsell9", "mmsell10", "Tmmsell1",
               "Tmmsell2", "Tmmsell5", "Tmmsell6", "Cmmsell10", "Dmmsell10", "Gmmsell0",
               "Gmmsell1", "Gmmsell2", "Fmmsell10", "Rmmsell1", "Rmmsell2", "Alimm1",
               "Alimm1_pt3", "Fmmsell10_pt4", "theta4", "Lmmsell8", "Lmmsell10"}
    for tag in running:
        assert not tag.startswith(ss.LIVE_TAG), tag
        assert not ss.LIVE_TAG.startswith(tag), tag


# --- lineage -------------------------------------------------------------------------


def test_a_draining_live_book_does_NOT_block_the_successor(monkeypatch):
    """Fmmsell10 winds down beside Hmmsell10: its live deployment must not be ended."""
    seen = {}

    class _Pred:
        id = 7

    class _Live:
        id, kind, ended_at = 1, "live", None
        deployment_key = "mmsell-contestcap-live-2"

    class _Session:
        def scalars(self, *a, **k):
            return type("R", (), {"all": staticmethod(lambda: [_Live()])})()

    monkeypatch.setattr(ss, "get_experiment",
                        lambda s, key: _Pred() if key == ss.PREDECESSOR_KEY else None)
    monkeypatch.setattr(ss, "_epoch_experiment_id", lambda s, d: _Pred.id)
    monkeypatch.setattr(ss, "_tags_of", lambda s, d: ["Fmmsell10"])

    import kalshi_bot.repository as repo
    monkeypatch.setattr(repo, "count_live_book_open", lambda s, tag: 23)
    monkeypatch.setattr(svc, "end_deployment",
                        lambda *a, **k: seen.setdefault("ended", []).append(a))
    monkeypatch.setattr(svc, "create_experiment",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached")))

    with pytest.raises(RuntimeError, match="reached"):
        ss.register(_Session(), actor="cal", now=T0)
    assert "ended" not in seen


def test_register_refuses_to_run_twice(monkeypatch):
    monkeypatch.setattr(ss, "get_experiment", lambda s, key: object())
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        ss.register(object(), actor="cal", now=T0)


def test_arm_refuses_before_the_contract_exists(monkeypatch):
    monkeypatch.setattr(ss, "get_experiment", lambda s, key: None)
    with pytest.raises(svc.ExperimentOsError, match="REGISTER_PACKAGE first"):
        ss.arm(object(), approved_by="cal")


def test_the_package_is_registered_and_arming_is_LIVE_OPS_only():
    from kalshi_bot.experiment_os.experiment_commands import ACTION_ROLES, _packages

    pkg = _packages()["mmsell-size-split-canary"]
    assert pkg.experiment_key == ss.SUCCESSOR_KEY
    assert pkg.register is ss.register and pkg.arm is ss.arm
    assert set(pkg.strategy_tags) == {ss.LIVE_TAG, ss.TWIN_TAG}
    assert ACTION_ROLES["ARM_CANARY"] == frozenset({"LIVE_OPS"})
