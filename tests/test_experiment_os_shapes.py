"""The three proving shapes (spec §34 PR 1 acceptance).

Three materially different real experiments from this repo, represented end-to-end in
Experiment OS — data taken from docs/BOOK_REGISTRY.md, not invented:

  1. FREEZE   — a multi-arm treatment/control PAPER experiment (freeze1..freeze4,
               freeze3 the open-window control);
  2. mmsell offset A/B — a LIVE_CANARY + paper-twin experiment (mmsell10a/10b with
               their _pt twins), including its real 2026-08-14 kill;
  3. pin15    — a retired historical experiment imported as legacy, with exactly the
               metadata history recorded and nothing invented.

Each fixture then has to answer the spec §30 production-invariant questions through
the read API alone — no strategy-specific archaeology.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from kalshi_bot.experiment_os import read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.lifecycle import IllegalTransition
from kalshi_bot.experiment_os.models import (
    ExperimentArm,
    ExperimentEpoch,
    ExperimentGate,
    ExperimentVersion,
    PlatformSnapshot,
)

UTC = timezone.utc


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _naive(dt: datetime) -> datetime:
    """sqlite drops tzinfo on round-trip; normalize for instant comparison."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ---------------------------------------------------------------------------
# Shape 1 — FREEZE: multi-arm treatment/control paper experiment
# ---------------------------------------------------------------------------


@pytest.fixture
def freeze_experiment(xos_session, xos_platform):
    s = xos_session
    exp = svc.create_experiment(
        s,
        key="freeze-dark-window-pin",
        title="Exchange-closure pin on commodity-hub grain/soft markets",
        origin="operator",
        family="structural",
        hypothesis=(
            "While a contract's settlement source is dark (CBOT grains / ICE softs), "
            "its favorite is underpriced relative to otherwise-comparable open-window "
            "favorites — the outcome is mechanically decided while retail keeps "
            "quoting the decided side cents from certainty."
        ),
        mechanism="source-dark window: no new prints can move the outcome",
        counterparty="launch-era retail quoting decided contracts",
        falsification=(
            "freeze1 fails to beat the open-window control freeze3 — that is tfav on "
            "a new venue and closes the off-weather pinning family"
        ),
        docs={"thesis": "docs/FREEZE_THESIS.md"},
        now=_dt(2026, 8, 12),
    )
    ver = svc.create_experiment_version(
        s,
        exp,
        hypothesis=exp.hypothesis,
        universe_selector="Kalshi commodity hub, groups grain+soft only (Pyth-continuous never dark)",
        entry_rule="taker-buy the favorite at ask when remaining window is fully source-dark and discount >= arm bar",
        exit_rule="hold to settlement (no exits)",
        sizing_rule="sized to resting depth and per-book order cap",
        execution_style="taker",
        independent_variable="source dark vs open condition at entry",
        held_constant=[
            "market universe (grain/soft commodity hub)",
            "favorite bar",
            "taker entry at ask",
            "hold to settlement",
            "sizing rule",
            "fee model",
        ],
        sample={"minimum_n": 150, "unit": "settled trades per arm"},
        metrics={"primary": "pnl_cents_per_trade delta vs control"},
        docs={"thesis": "docs/FREEZE_THESIS.md"},
        now=_dt(2026, 8, 12),
    )
    svc.add_arm(
        s, ver, arm_key="freeze1", role="treatment",
        description="dark window, discount >= 3c (the thesis)",
        params={"window": "dark", "min_discount_cents": 3}, strategy_tag="freeze1",
    )
    svc.add_arm(
        s, ver, arm_key="freeze2", role="secondary",
        description="dark window, discount >= 8c (deep-discount slice)",
        params={"window": "dark", "min_discount_cents": 8}, strategy_tag="freeze2",
    )
    svc.add_arm(
        s, ver, arm_key="freeze3", role="control",
        description="OPEN window, same discount bar — THE CONTROL",
        params={"window": "open", "min_discount_cents": 3}, strategy_tag="freeze3",
    )
    svc.add_arm(
        s, ver, arm_key="freeze4", role="secondary",
        description="dark window, entry price capped at 95c",
        params={"window": "dark", "min_discount_cents": 3, "max_price_cents": 95},
        strategy_tag="freeze4",
    )
    probe_gate = svc.register_gate(
        s, ver, gate_key="probe_to_paper", kind="promotion",
        from_state="PROBE", to_state="PAPER",
        spec={
            "pass_all": [
                {"metric": "pooled_cents_per_contract", "op": ">", "value": 0},
                {"metric": "delta_vs_favorite_control_cents", "op": ">=", "value": 3.0},
                {"metric": "wrong_pins", "op": "==", "value": 0},
            ],
        },
        registered_at=_dt(2026, 8, 11),
        notes="the five pre-registered probe gates of scripts/kalshi_freeze_study.py",
    )
    svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={
            "sample": {
                "freeze1": {"metric": "settled_trades", "op": ">=", "value": 150},
                "freeze3": {"metric": "settled_trades", "op": ">=", "value": 150},
            },
            "pass_all": [
                {"metric": "pnl_cents_per_trade", "arm": "freeze1", "op": ">", "value": 0},
                {
                    "metric": "delta.pnl_cents_per_trade",
                    "treatment": "freeze1", "control": "freeze3",
                    "op": ">=", "value": 3.0,
                },
            ],
            "fail_any": [
                {
                    "metric": "delta.pnl_cents_per_trade",
                    "treatment": "freeze1", "control": "freeze3",
                    "op": "<=", "value": 0,
                },
            ],
        },
        registered_at=_dt(2026, 8, 13),
        notes="gate on freeze1 MINUS freeze3, never freeze1 alone (docs/BOOK_REGISTRY.md)",
    )
    svc.freeze_version(s, ver, now=_dt(2026, 8, 13))

    svc.transition_experiment(
        s, exp, "PROBE", actor="operator",
        reason="probe: scripts/kalshi_freeze_study.py over settled REST history",
        occurred_at=_dt(2026, 8, 11),
    )
    probe_pass = svc.record_gate_result(
        s, probe_gate, verdict="PASS",
        computed_at=_dt(2026, 8, 12),
        metrics={
            "pooled_cents_per_contract": 16.10,
            "delta_vs_favorite_control_cents": 15.50,
            "wrong_pins": 0,
        },
        sample={"post_pin_trades": 39429},
        evidence_ref="scripts/kalshi_freeze_study.py run 2026-08-12",
        explanation=(
            "all five pre-registered gates cleared; forward paper test still required "
            "— headline sits near a known lookahead-artifact magnitude and is one "
            "commodity's flow"
        ),
    )
    svc.transition_experiment(
        s, exp, "PAPER", actor="operator", gate_result=probe_pass,
        reason="probe PASS -> paper book with its own control",
        occurred_at=_dt(2026, 8, 13),
    )
    epoch = svc.open_epoch(
        s, ver, reason="initial paper epoch", started_at=_dt(2026, 8, 13)
    )
    deployment = svc.register_deployment(
        s, epoch, deployment_key="freeze-paper-1", stage="PAPER", kind="paper",
        arms={
            "freeze1": "freeze1", "freeze2": "freeze2",
            "freeze3": "freeze3", "freeze4": "freeze4",
        },
        config={"engine": "shared paper engine, no-timeout set"},
        started_at=_dt(2026, 8, 13),
    )
    gate = next(
        g for g in read.gates_for(s, ver) if g.gate_key == "paper_to_live_canary"
    )
    svc.mark_gate_evidence_started(s, gate, at=_dt(2026, 8, 13))
    s.commit()
    return exp, ver, epoch, deployment


def test_freeze_answers_the_production_invariants(xos_session, freeze_experiment):
    """Spec §30: hypothesis / state / arms / independent variable / snapshot / gate —
    answerable from the read API with no book-specific archaeology."""
    s = xos_session
    exp, ver, epoch, deployment = freeze_experiment

    # 1–3: hypothesis, state, active version
    assert "source is dark" in exp.hypothesis or "dark" in exp.hypothesis
    assert exp.state == "PAPER"
    assert read.latest_version(s, exp).version == 1

    # 5: which arm is treatment, which is control
    arms = {a.arm_key: a for a in read.arms_for(s, ver)}
    assert arms["freeze1"].role == "treatment"
    assert arms["freeze3"].role == "control"
    assert len(arms) == 4

    # 6–7: independent variable and held-constants, machine-readable
    assert ver.independent_variable == "source dark vs open condition at entry"
    assert "taker entry at ask" in ver.held_constant_json

    # 8: complete platform snapshot pinned to the epoch
    snap = s.get(PlatformSnapshot, epoch.platform_snapshot_id)
    assert svc.snapshot_missing_components(s, snap) == []

    # 11-ish: the paper gate is structured data with an immutable hash and started
    # evidence; its clauses are executable, not prose.
    gate = next(g for g in read.gates_for(s, ver) if g.gate_key == "paper_to_live_canary")
    assert gate.spec_hash == svc.canonical_hash(gate.spec_json)
    assert gate.evidence_started_at is not None
    assert gate.spec_json["pass_all"][1]["control"] == "freeze3"

    # 14: strategy tag → full lineage (deployment → arm → epoch → version → snapshot)
    lineage = read.strategy_tag_lineage(s, "freeze3")
    assert len(lineage) == 1
    row = lineage[0]
    assert row["experiment_key"] == "freeze-dark-window-pin"
    assert row["arm_role"] == "control"
    assert row["epoch_number"] == 1
    assert row["deployment_stage"] == "PAPER"
    assert row["platform_snapshot_fingerprint"] == snap.fingerprint

    # The whole tree serializes (the CLI `show` read).
    tree = read.experiment_tree(s, exp)
    assert json.dumps(tree, default=str)
    assert [a["arm_key"] for a in tree["versions"][0]["arms"]] == [
        "freeze1", "freeze2", "freeze3", "freeze4",
    ]


def test_freeze_gate_cannot_be_rescued_after_evidence(xos_session, freeze_experiment):
    """The pre-registered freeze1−freeze3 bar cannot be moved now that the paper book
    is accumulating settles (spec §11.2)."""
    s = xos_session
    _, ver, _, _ = freeze_experiment
    gate = next(g for g in read.gates_for(s, ver) if g.gate_key == "paper_to_live_canary")
    weakened = dict(gate.spec_json)
    weakened["pass_all"] = [
        {**weakened["pass_all"][0]},
        {**weakened["pass_all"][1], "value": 1.0},  # 3.0c bar quietly lowered
    ]
    gate.spec_json = weakened
    with pytest.raises(svc.ImmutableRecord, match="may not be edited in place"):
        s.flush()
    s.rollback()


def test_freeze_paper_cannot_promote_without_its_gate(xos_session, freeze_experiment):
    """PAPER must not advance on a good-looking absolute number: the transition
    demands a PASS result of a pre-registered gate plus operator approval."""
    s = xos_session
    exp, *_ = freeze_experiment
    with pytest.raises(IllegalTransition):
        svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator")
    with pytest.raises(svc.ExperimentOsError, match="PASS gate result"):
        svc.transition_experiment(
            s, exp, "LIVE_CANARY", actor="operator", approved_by="operator"
        )


# ---------------------------------------------------------------------------
# Shape 2 — mmsell maker-offset A/B: live canary + paper twin
# ---------------------------------------------------------------------------


@pytest.fixture
def mmsell_offset_experiment(xos_session, xos_platform):
    s = xos_session
    exp = svc.create_experiment(
        s,
        key="mmsell-maker-offset-ab",
        title="Does +1c of maker queue priority improve realizable economics?",
        origin="operator",
        family="maker",
        hypothesis=(
            "Paying +1 cent improves maker queue placement enough to improve "
            "realizable economics, not merely fill rate"
        ),
        counterparty="takers crossing into the cheap-band NO queue",
        falsification=(
            "the offset arm's twin-vs-live gap is no better than the control arm's "
            "(higher fill rate bought adverse selection)"
        ),
        docs={"thesis": "docs/MMSELL_OFFSET_AB.md"},
        now=_dt(2026, 7, 18),
    )
    ver = svc.create_experiment_version(
        s,
        exp,
        hypothesis=exp.hypothesis,
        universe_selector="mmsell10 entry: volume-ranked events, yes-mid 5..10c, yes-ask <= 7c",
        entry_rule="rest a buy-NO maker order; arm decides the resting price offset",
        exit_rule="hold to settlement",
        sizing_rule="1-contract clips",
        execution_style="maker",
        independent_variable="maker price offset (0c vs +1c)",
        held_constant=[
            "market universe", "entry window", "price band", "max price",
            "order size", "exit logic", "risk envelope", "scanner depth",
            "market taxonomy", "platform snapshot", "sample window",
        ],
        sample={"minimum_n": 150, "unit": "settled contracts per arm"},
        metrics={"primary": "twin-vs-live gap in cents/contract, per arm"},
        docs={"thesis": "docs/MMSELL_OFFSET_AB.md"},
        now=_dt(2026, 7, 18),
    )
    svc.add_arm(
        s, ver, arm_key="offset_0c", role="control",
        description="rests AT the no-bid", params={"maker_offset_cents": 0},
        strategy_tag="mmsell10",
    )
    svc.add_arm(
        s, ver, arm_key="offset_1c", role="treatment",
        description="rests 1c better than the no-bid", params={"maker_offset_cents": 1},
        strategy_tag="mmsell10",
    )
    paper_gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={
            "pass_all": [
                {"metric": "realizable_cents_per_trade", "op": ">", "value": 0},
                {"metric": "fill_model_coverage_pct", "op": ">=", "value": 99.0},
            ],
        },
        registered_at=_dt(2026, 7, 20),
        notes="gate on the fill model's realizable column, never blended paper",
    )
    svc.register_gate(
        s, ver, gate_key="live_twin_divergence", kind="kill",
        spec={
            "sample": {
                "offset_0c": {"metric": "settled_contracts", "op": ">=", "value": 400},
                "offset_1c": {"metric": "settled_contracts", "op": ">=", "value": 400},
            },
            "fail_any": [
                {
                    "metric": "delta.twin_live_gap_cents",
                    "treatment": "offset_1c", "control": "offset_0c",
                    "op": ">=", "value": 0,
                },
            ],
        },
        registered_at=_dt(2026, 8, 4),
        notes="KILL the offset when gap_b >= gap_a — twin-paired read is primary",
    )
    svc.freeze_version(s, ver, now=_dt(2026, 7, 20))

    svc.transition_experiment(
        s, exp, "PROBE", actor="operator",
        reason="probe: mmsell fill model over live mmsell3 calibration",
        occurred_at=_dt(2026, 7, 19),
    )
    svc.transition_experiment(
        s, exp, "PAPER", actor="operator",
        reason="mmsell10 paper book accumulating under the price ceiling",
        occurred_at=_dt(2026, 7, 20),
    )
    paper_epoch = svc.open_epoch(
        s, ver, reason="initial paper epoch (offset unobservable in paper — both arms "
        "collapse to the mmsell10 book; paper cannot test a fill assumption)",
        started_at=_dt(2026, 7, 20),
    )
    svc.register_deployment(
        s, paper_epoch, deployment_key="mmsell10-paper-1", stage="PAPER", kind="paper",
        arms={"offset_0c": "mmsell10"},
        config={"lo": 5, "hi": 10, "maxyes": 7},
        started_at=_dt(2026, 7, 20),
    )
    paper_pass = svc.record_gate_result(
        s, paper_gate, verdict="PASS",
        epoch=paper_epoch,
        computed_at=_dt(2026, 8, 3),
        metrics={"realizable_cents_per_trade": 1.40, "fill_model_coverage_pct": 100.0},
        evidence_ref="scripts/mmsell_fill_model.py (docs/MMSELL_FILL_MODEL.md)",
        explanation="mmsell10 is the one live candidate: 100% coverage, +1.40c realizable",
    )
    svc.transition_experiment(
        s, exp, "LIVE_CANARY", actor="operator", approved_by="operator",
        gate_result=paper_pass,
        reason="arm the offset A/B live at 1-contract clips",
        occurred_at=_dt(2026, 8, 4),
    )
    # The live boundary changes execution semantics (paper assumed fills; live must
    # earn them) — a fresh epoch, so live evidence never pools with paper evidence.
    svc.close_epoch(s, paper_epoch, ended_at=_dt(2026, 8, 4))
    live_epoch = svc.open_epoch(
        s, ver, reason="live execution boundary: offset observable only live; fresh "
        "deployment identities so no mature paper state is inherited",
        impact_class="I2",
        started_at=_dt(2026, 8, 4),
    )
    live = svc.register_deployment(
        s, live_epoch, deployment_key="mmsell-offset-ab-live-1",
        stage="LIVE_CANARY", kind="live",
        arms={"offset_0c": "mmsell10a", "offset_1c": "mmsell10b"},
        config={
            "clip_contracts": 1,
            "arm_assignment": "deterministic ticker hash (books never contest a ticker)",
        },
        started_at=_dt(2026, 8, 4),
    )
    twin = svc.register_deployment(
        s, live_epoch, deployment_key="mmsell-offset-ab-twin-1",
        stage="LIVE_CANARY", kind="paper_twin", twin_of=live,
        arms={"offset_0c": "mmsell10a_pt", "offset_1c": "mmsell10b_pt"},
        config={"parameterized_to": "live knobs (entry price rule, sizing, open cap)"},
        started_at=_dt(2026, 8, 4),
    )
    kill_gate = next(
        g for g in read.gates_for(s, ver) if g.gate_key == "live_twin_divergence"
    )
    svc.mark_gate_evidence_started(s, kill_gate, at=_dt(2026, 8, 4))
    s.commit()
    return exp, ver, live_epoch, live, twin


def test_mmsell_live_canary_with_twin(xos_session, mmsell_offset_experiment):
    s = xos_session
    exp, ver, live_epoch, live, twin = mmsell_offset_experiment

    assert exp.state == "LIVE_CANARY"

    # The twin is a first-class pair: same epoch, same start instant, pointed at live.
    assert read.twin_for(s, live).id == twin.id
    assert twin.epoch_id == live.epoch_id
    assert _naive(twin.started_at) == _naive(live.started_at)

    # Spec §13: the strategy tag changed between paper and live for operational
    # reasons (fresh live tags avoid inheriting mature paper state) while the ARM
    # identity stayed stable.
    paper_lineage = read.strategy_tag_lineage(s, "mmsell10")
    live_lineage = read.strategy_tag_lineage(s, "mmsell10a")
    twin_lineage = read.strategy_tag_lineage(s, "mmsell10a_pt")
    assert paper_lineage[0]["arm_key"] == "offset_0c" and paper_lineage[0]["epoch_number"] == 1
    assert live_lineage[0]["arm_key"] == "offset_0c" and live_lineage[0]["epoch_number"] == 2
    assert twin_lineage[0]["deployment_kind"] == "paper_twin"
    assert live_lineage[0]["deployment_stage"] == "LIVE_CANARY"

    # The promotion is audited with approval + the PASS evidence that justified it.
    promo = [t for t in read.transitions_for(s, exp) if t.to_state == "LIVE_CANARY"][0]
    assert promo.approved_by == "operator"
    assert promo.gate_result_id is not None

    # Paper evidence and live evidence live in different epochs (never pooled).
    epochs = read.epochs_for(s, ver)
    assert [e.epoch_number for e in epochs] == [1, 2]
    assert _naive(epochs[0].ended_at) == _naive(epochs[1].started_at)
    assert epochs[1].impact_class == "I2"


def test_mmsell_kill_verdict_and_retirement(xos_session, mmsell_offset_experiment):
    """The real 2026-08-14 outcome: the offset worked mechanically and that killed it
    — recorded as an immutable FAIL result, then an audited retirement."""
    s = xos_session
    exp, ver, live_epoch, live, twin = mmsell_offset_experiment
    kill_gate = next(
        g for g in read.gates_for(s, ver) if g.gate_key == "live_twin_divergence"
    )
    verdict = svc.record_gate_result(
        s, kill_gate, verdict="FAIL",
        epoch=live_epoch,
        computed_at=_dt(2026, 8, 14),
        evidence_start=_dt(2026, 8, 4), evidence_end=_dt(2026, 8, 14),
        sample={"offset_0c": {"settled_contracts": 420}, "offset_1c": {"settled_contracts": 384}},
        metrics={
            "gap_cents": {"offset_0c": -0.06, "offset_1c": 3.45},
            "delta.twin_live_gap_cents": 3.51,
            "fill_rate_pct": {"offset_0c": 55.3, "offset_1c": 58.2},
            "front_of_queue_pct": {"offset_0c": 35.9, "offset_1c": 77.0},
        },
        evidence_ref="scripts/mmsell_offset_ab.py + live_order_queue_ticks",
        explanation=(
            "KILL: gap_b >= gap_a by 3.51c. The cent bought the front of the queue "
            "(77.0% vs 35.9%) and the front of the queue is where losers cross into "
            "you. Honesty note: offset_1c killed at n=384 against its n>=400 bar "
            "(operator ended live mmsell trading); nothing may later be promoted on "
            "a claim the bar was cleared."
        ),
    )
    svc.transition_experiment(
        s, exp, "RETIRED", actor="operator", gate_result=verdict,
        reason=(
            "1c offset KILLed (bought adverse selection); mmsell10a itself was "
            "profitable when retired (+0.84c/ct, n=420) — ended by operator decision "
            "to clear the deck, not by failure"
        ),
        occurred_at=_dt(2026, 8, 14),
    )
    svc.end_deployment(s, live, ended_at=_dt(2026, 8, 14))
    svc.end_deployment(s, twin, ended_at=_dt(2026, 8, 14))
    s.commit()

    assert exp.state == "RETIRED"
    assert _naive(exp.retired_at) == _naive(_dt(2026, 8, 14))
    # The retiring transition points at the exact FAIL evidence.
    last = read.transitions_for(s, exp)[-1]
    assert last.gate_result_id == verdict.id
    # Terminal: no reopen, no new epoch, no new deployment.
    with pytest.raises(IllegalTransition, match="terminal"):
        svc.transition_experiment(s, exp, "LIVE_CANARY", actor="operator",
                                  approved_by="operator")
    with pytest.raises(svc.ExperimentOsError, match="RETIRED"):
        svc.open_epoch(s, ver, reason="zombie epoch")
    # History stays fully queryable after retirement.
    assert read.strategy_tag_lineage(s, "mmsell10b")[0]["experiment_state"] == "RETIRED"


# ---------------------------------------------------------------------------
# Shape 3 — pin15: retired historical experiment, imported as legacy
# ---------------------------------------------------------------------------


@pytest.fixture
def pin15_experiment(xos_session):
    """Imported with NO platform registry in this session at all — the import path
    must not need one, because inventing a 2026-07 snapshot would be fabrication."""
    s = xos_session
    exp = svc.import_legacy_experiment(
        s,
        key="pin15",
        state="RETIRED",
        legacy_class="RETIRED_OR_KILLED",
        migration_integrity="C",
        origin="operator",
        family="observation_pin",
        title="15-min crypto endgame observation-pin",
        hypothesis=(
            "taker-buy the drift-favored side 2-3 min before close, held to the "
            "60s-average settle"
        ),
        docs={
            "thesis": "docs/PIN15_THESIS.md",
            "verdict": "docs/RESEARCH_JOURNAL.md (2026-07-16)",
        },
        verdict=(
            "Gate FAILED at n=405: target T~120-180s window nets +0.27c/trade "
            "(< the +1.5c bar); cumulative loss traces to the T 60-120s sub-window "
            "(-53c/trade, n=37)"
        ),
        imported_at=_dt(2026, 8, 15),
        retired_at=_dt(2026, 7, 16),
    )
    svc.attach_legacy_evidence(
        s, exp,
        label="paper book lifetime (as recorded at the kill)",
        evidence_class="context_only",
        source="paper_trades.strategy='pin15' (pin15_enabled=False since 2026-07-16)",
        window_end=_dt(2026, 7, 16),
        summary={
            "n": 405,
            "target_window_cents_per_trade": 0.27,
            "bar_cents_per_trade": 1.5,
            "t60_120s_cents_per_trade": -53.0,
            "t60_120s_n": 37,
        },
        notes="numbers preserved exactly as recorded in docs/BOOK_REGISTRY.md",
    )
    s.commit()
    return exp


def test_pin15_imports_without_invented_history(xos_session, pin15_experiment):
    s = xos_session
    exp = pin15_experiment

    # Nothing reconstructed: no versions, arms, epochs, gates, or snapshot — and the
    # uncertainty is visible as integrity level C (CONTEXT_ONLY).
    assert read.versions_for(s, exp) == []
    assert s.scalar(select(func.count()).select_from(ExperimentVersion)) == 0
    assert s.scalar(select(func.count()).select_from(ExperimentArm)) == 0
    assert s.scalar(select(func.count()).select_from(ExperimentEpoch)) == 0
    assert s.scalar(select(func.count()).select_from(ExperimentGate)) == 0
    assert exp.platform_snapshot_id is None
    assert exp.migration_integrity == "C"
    assert exp.legacy_class == "RETIRED_OR_KILLED"

    # Real historical dates, not import-time defaults.
    assert _naive(exp.retired_at) == _naive(_dt(2026, 7, 16))
    assert _naive(exp.imported_at) == _naive(_dt(2026, 8, 15))

    # One import transition, actor=import, carrying the recorded kill verdict.
    rows = read.transitions_for(s, exp)
    assert [(r.from_state, r.to_state, r.actor) for r in rows] == [(None, "RETIRED", "import")]
    assert "n=405" in rows[0].reason

    # Graveyard semantics: terminal, out of the active set, but fully queryable.
    with pytest.raises(IllegalTransition, match="terminal"):
        svc.transition_experiment(s, exp, "IDEA", actor="operator")
    assert exp.key not in [e.key for e in read.active_experiments(s)]
    assert [e.key for e in read.list_experiments(s, legacy=True)] == ["pin15"]

    # The evidence is preserved verbatim and classed CONTEXT_ONLY — it can never
    # satisfy a clean new-system promotion gate.
    ev = read.legacy_evidence_for(s, exp)[0]
    assert ev.evidence_class == "context_only"
    assert ev.summary_json["n"] == 405
    assert ev.summary_json["t60_120s_cents_per_trade"] == -53.0

    # Its historical strategy tag is deliberately unmapped: mapping it would require
    # inventing a deployment/epoch/snapshot that was never recorded.
    assert read.strategy_tag_lineage(s, "pin15") == []


# ---------------------------------------------------------------------------
# The three shapes coexisting
# ---------------------------------------------------------------------------


def test_three_shapes_coexist_and_are_queryable(
    xos_session, freeze_experiment, mmsell_offset_experiment, pin15_experiment
):
    s = xos_session
    keys = [e.key for e in read.list_experiments(s)]
    assert keys == ["freeze-dark-window-pin", "mmsell-maker-offset-ab", "pin15"]

    by_state = {e.key: e.state for e in read.list_experiments(s)}
    assert by_state == {
        "freeze-dark-window-pin": "PAPER",
        "mmsell-maker-offset-ab": "LIVE_CANARY",
        "pin15": "RETIRED",
    }

    # Spec §35.7: a platform revision can list every active experiment it affects —
    # the two native experiments pin epochs whose snapshot includes FEE_MODEL v1;
    # pin15, imported without invented dependencies, is correctly absent.
    from kalshi_bot.experiment_os.models import PlatformComponent, PlatformRevision

    fee_v1 = s.scalar(
        select(PlatformRevision)
        .join(PlatformComponent, PlatformComponent.id == PlatformRevision.component_id)
        .where(PlatformComponent.key == "FEE_MODEL", PlatformRevision.version == "v1")
    )
    affected = [e.key for e in read.experiments_using_revision(s, fee_v1)]
    assert affected == ["freeze-dark-window-pin", "mmsell-maker-offset-ab"]

    # Every tag in play resolves to exactly one lineage row (or none for legacy).
    for tag in ("freeze1", "freeze3", "mmsell10", "mmsell10a", "mmsell10b_pt"):
        assert len(read.strategy_tag_lineage(s, tag)) == 1, tag
    assert read.strategy_tag_lineage(s, "pin15") == []
