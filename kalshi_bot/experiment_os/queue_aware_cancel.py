"""MMSELL10 QUEUE-AWARE CANCELLATION — the registration package.

This module is DATA plus two functions. It places no orders, arms nothing on
import, opens no exposure, and cannot cancel anything: the executor step it
describes (`LiveExecutor.evaluate_queue_cancellations`) is off by default and,
in the mode this package registers, records decisions without acting on them.
Nothing here runs until an operator submits a `REGISTER_PACKAGE` envelope naming
`mmsell10-queue-aware-cancel`.

Scientific contract: `docs/MMSELL_QUEUE_AWARE_CANCEL.md`. That document carries
the baseline read the numbers below were taken from; this module is the
executable form of it, and the two disagreeing is a bug here.

THE QUESTION
------------
mmsell10 rests a one-contract BUY-NO maker order for four hours. While it rests
it holds one of the book's 40 open slots — `count_live_book_open` counts a
resting order as open — and the baseline shows those slots BIND: the live
canaries refused 67–145 candidates a day at `gate:open_cap`. Some resting orders
are so deep in Kalshi's queue that they are unlikely to fill before the timeout.
Cancelling those, and only those, would recycle a slot roughly two hours sooner.
The treatment is that cancellation rule; everything else about the book is held
constant.

WHY THE FIRST EPOCH IS A SHADOW, AND WHY SHADOW IS A PROBE
----------------------------------------------------------
The paper books cannot express this treatment at all: paper assumes the resting
order fills at entry and has no queue. Queue telemetry exists only for LIVE
orders (`live_order_queue_ticks`, sampled every reconcile since 2026-08-14). So
the only honest way to run the treatment without real-money authorization is to
run the frozen rule against the live book's real resting orders and RECORD what
it would have done — a shadow — then join each "would cancel" to what the order
actually did next. That is an instrument reading a live book, not a deployment
of anything, so it is registered as a PROBE deployment at the PROBE stage.

The probe's treatment arm carries a strategy tag (`qacshadow1`) for one reason
only: the canonical evaluator resolves a gate's scope through the tags on a
deployment's arms, and a tagless arm has no scope. No book is configured with
that tag, no order is ever placed under it, and the executor stamps shadow
decision rows with this arm's lineage id rather than with any trading tag.

WHAT THE BASELINE ALREADY SAYS, STATED BEFORE THE SHADOW RUNS
-------------------------------------------------------------
Two findings cut against the hypothesis and are pre-registered as the things
this shadow must overturn or confirm:

  * Fills that land after 90 minutes were ALL winners in the baseline (29
    settled, 0 losses, ~+7c/contract); the losers are the fast fills. A rule that
    cancels late-resting orders forgoes the book's best fills. The shadow measures
    that cost directly (`qac_forgone_cents_per_would_cancel`).
  * At a $1 clip the capital-hours a cancel releases are worth nothing in
    dollars. The ONLY value is the slot, and the slot has value only while the
    cap binds. The promotion bar therefore asks for evidence the rule fires where
    the cap binds, not merely that it fires.

A higher fill rate is not on the bar at all: the book's problem is that the
fills it wins are the losers, and nothing here changes which fills it wins.

WHAT A PASS HERE AUTHORIZES
---------------------------
A PASS on `shadow_to_paper` moves the experiment to PAPER — where the treatment
still cancels nothing, because the paper stage of THIS experiment is defined as
continued shadow under the same frozen rule. Reaching a live canary is a
separate Version (the rule carried verbatim), a separate risk envelope, and a
separate operator approval through `arm_live_canary`, which this package cannot
call: it has no `arm` function.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ..live.queue_cancel import FROZEN_RULE
from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .models import ExperimentDeployment, ExperimentDeploymentArm
from .read import get_experiment, latest_version, open_epoch_for

EXPERIMENT_KEY = "mmsell10-queue-aware-cancel"
THESIS_DOC = "docs/MMSELL_QUEUE_AWARE_CANCEL.md"
WORKSTREAM_DOC = "docs/workstreams/WS-015-mmsell10-queue-aware-cancel.md"

ARM_CONTROL = "qac_control"
ARM_TREATMENT = "qac_treat"
#: The shadow instrument's tag. See the module docstring: a scope handle, never a book.
SHADOW_TAG = "qacshadow1"
SHADOW_DEPLOYMENT_KEY = "mmsell10-qac-shadow-1"

PROMOTION_GATE_KEY = "shadow_to_paper"
KILL_GATE_KEY = "shadow_kill"

#: The pre-registered rule, as the executor runs it. Imported rather than retyped
#: so "the registered rule is the running rule" is a property of the code; a test
#: asserts `Settings()` defaults equal this too.
FROZEN_RULE_INPUTS: dict = FROZEN_RULE.inputs()

#: Observed live tags the shadow reads. Informational — the executor evaluates every
#: resting order and the decision row records the order's own tag; this list is what
#: the baseline was measured on and what the gates' scope is expected to see.
BASELINE_LIVE_TAGS: tuple[str, ...] = ("Cmmsell10", "Dmmsell10", "Emmsell10", "Fmmsell10")

#: No real-money change. Stated as a risk envelope anyway so the Version is not blank
#: where a reader expects one, and so a later live Version has something to diff.
RISK_ENVELOPE: dict = {
    "stage": "shadow",
    "exposure_change_usd": 0.0,
    "cancels_sent": "none — LIVE_QUEUE_CANCEL_MODE=shadow records decisions only",
    "entry_price_offset_cents": "unchanged (0)",
    "order_size": "unchanged",
    "order_timeout_seconds": "unchanged (14400) and applied before this step",
    "exit_policy": "unchanged — hold to settlement",
    "rule": FROZEN_RULE_INPUTS,
    "settings": {
        "LIVE_QUEUE_CANCEL_MODE": "shadow",
        "LIVE_QUEUE_CANCEL_TAGS": "",
    },
}

_TREAT_PROBE = {"arm": ARM_TREATMENT, "deployment_kind": "probe"}

#: Promotion bar, PROBE -> PAPER. Every clause addresses the probe deployment
#: explicitly. `sample` floors the bar at 40 would-cancel orders: below that the
#: later-fill rate cannot separate 10% from 25%.
PROMOTION_GATE_SPEC: dict = {
    "description": (
        "The shadow must show, on the live book's real resting orders, that the frozen "
        "rule (a) can be evaluated — telemetry coverage is high; (b) is selective — the "
        "orders it would cancel rarely fill afterwards; (c) is cheap — the realized "
        "profit those later fills DID earn, spread over every would-cancel order, is at "
        "most 1c; and (d) fires where it matters — most would-cancel decisions land "
        "while the book's open cap is binding. A pass authorizes PAPER, which for this "
        "experiment is continued shadow; no cancel is sent by a pass."
    ),
    "sample": {
        ARM_TREATMENT: {"metric": "qac_would_cancel_orders", "deployment_kind": "probe",
                        "op": ">=", "value": 40},
    },
    "max_evidence_horizon": {
        "metric": "qac_would_cancel_orders", "value": 400,
        "arms": [ARM_TREATMENT], "deployment_kind": "probe",
    },
    "pass_all": [
        {"metric": "qac_telemetry_coverage_pct", "op": ">=", "value": 90.0, **_TREAT_PROBE},
        {"metric": "qac_would_cancel_later_fill_pct", "op": "<=", "value": 15.0, **_TREAT_PROBE},
        {"metric": "qac_forgone_cents_per_would_cancel", "op": "<=", "value": 1.0, **_TREAT_PROBE},
        {"metric": "qac_would_cancel_cap_bound_pct", "op": ">=", "value": 50.0, **_TREAT_PROBE},
    ],
}

#: Kill contract. Each clause has its own evidence floor so a small sample cannot
#: FAIL on noise, and none of them reads a number the promotion bar does not.
KILL_GATE_SPEC: dict = {
    "description": (
        "Stop the shadow when its own numbers say the rule cannot work: the fills it "
        "would forgo are worth more than 3c per would-cancel order (the baseline's late "
        "fills earned ~7c each, so a rule that catches many of them is net negative before "
        "any capacity benefit), or telemetry coverage collapses so the rule is deciding on "
        "guesses."
    ),
    "fail_any": [
        {"metric": "qac_forgone_cents_per_would_cancel", "op": ">=", "value": 3.0,
         **_TREAT_PROBE,
         "min_evidence": {"metric": "qac_would_cancel_orders", "op": ">=", "value": 40}},
        {"metric": "qac_telemetry_coverage_pct", "op": "<=", "value": 60.0, **_TREAT_PROBE,
         "min_evidence": {"metric": "qac_decisions", "op": ">=", "value": 200}},
    ],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def material_config() -> dict:
    """What a drift check would compare the running worker against."""
    return {"mode": "shadow", "rule": FROZEN_RULE_INPUTS, "shadow_tag": SHADOW_TAG}


def register(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Create the experiment, freeze v1 with its two arms and two gates, open v1/e1 on
    the active platform snapshot, walk IDEA -> PROBE, and register the shadow probe
    deployment. Arms nothing, places nothing, cancels nothing.

    `promotion_sample_floor` may only RAISE the reviewed floor of 40 would-cancel
    orders; a lower value is refused."""
    floor = 40 if promotion_sample_floor is None else int(promotion_sample_floor)
    if floor < 40:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is below the reviewed floor 40 — an "
            "envelope may make a pre-registered bar stricter, never weaker"
        )
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package refuses "
            "rather than re-running, so a repeated command cannot fork the lineage"
        )

    experiment = service.create_experiment(
        session, key=EXPERIMENT_KEY, origin="operator",
        title="mmsell10 — queue-aware early cancellation of deep-queue resting orders",
        family="maker",
        hypothesis=(
            "Cancelling an unfilled mmsell10 maker order once its age and queue depth "
            "put its pre-timeout fill probability at or below 10% releases an open slot "
            "the cap is otherwise holding, and does so without forgoing fills worth "
            "more than the slot — raising net realized dollars at equal capital."
        ),
        mechanism=(
            "The open-position cap counts resting orders and binds daily (67–145 "
            "candidates/day refused at gate:open_cap). A slot freed ~2h earlier admits "
            "a later candidate. The cost is the profit of the fills the cancelled "
            "orders would still have won; the baseline says late fills are winners, so "
            "the rule must be selective enough that the forgone profit stays small."
        ),
        falsification=(
            "In shadow, the orders the rule would cancel go on to fill more than 15% of "
            "the time, or the realized profit of those later fills exceeds 1c per "
            "would-cancel order, or fewer than half of the would-cancel decisions occur "
            "while the cap binds — any of which means the rule buys capacity that is "
            "not scarce with fills that are."
        ),
        universe=(
            "the resting live orders of the mmsell10 price-ceiling live canaries "
            f"({', '.join(BASELINE_LIVE_TAGS)} and their successors); no market "
            "selection of its own"
        ),
        docs={"thesis": THESIS_DOC, "workstream": WORKSTREAM_DOC,
              "telemetry": "docs/LIVE_QUEUE_POSITION.md"},
        actor=actor, now=at,
    )

    version = service.create_experiment_version(
        session, experiment,
        hypothesis=experiment.hypothesis,
        mechanism=experiment.mechanism,
        falsification=experiment.falsification,
        universe_selector=experiment.universe,
        entry_rule=(
            "UNCHANGED from the observed book: rest a buy-NO maker order at the no-bid "
            "(offset 0). This experiment places no orders of its own."
        ),
        exit_rule=(
            "Treatment: cancel a RESTING order when age >= 90 min, its queue telemetry "
            "is observed and < 10 min old, and the frozen baseline table gives "
            "P(fill before timeout | age checkpoint, contracts-ahead bucket) <= 10% "
            "from a cell of >= 20 orders. Otherwise, and on any missing/stale/"
            "malformed/errored telemetry, the existing 4h timeout applies unchanged. "
            "Filled positions: hold to settlement, unchanged. In this Version the "
            "cancel is RECORDED, never sent."
        ),
        sizing_rule="unchanged — one contract per order; the rule never touches size or price",
        execution_style="maker (unchanged); shadow instrument over the live book",
        independent_variable=(
            "whether the queue-aware cancellation rule is applied (shadow: would-apply) "
            "versus the plain 4h timeout"
        ),
        held_constant=[
            "entry universe and the mmsell10 pricing rule",
            "entry price offset 0c",
            "order size and every existing risk cap",
            "hold-to-settlement for filled positions",
            "the 4h timeout as the fallback wherever the rule does not fire",
            "the contest, event-rung, settlement-date and daily-loss controls",
        ],
        control_required=True,
        risk=RISK_ENVELOPE,
        provenance={
            "baseline": "ops requests qac-qp-1, qac-b-2..6 on 2026-09-07 over the prior "
                        "336h: 408 live orders, 18,143 queue samples, 100% readable",
            "survival_table": "live/queue_cancel.BASELINE_SURVIVAL_2026_09_07",
            "threshold_choice": (
                "10% with min cell n=20 is the loosest threshold under which every "
                "qualifying baseline cell is also below 10% at the NEXT checkpoint, i.e. "
                "the estimate does not depend on which side of a checkpoint the order "
                "sits; it opens b5k+ from 90 min and b0 from 180 min and nothing else"
            ),
        },
        monitoring={
            "coverage": "qac_telemetry_coverage_pct is read beside every number",
            "counterfactual": "qac_would_cancel_later_fill_pct / qac_forgone_cents_per_would_cancel",
            "capacity": "qac_would_cancel_cap_bound_pct",
        },
        docs={"thesis": THESIS_DOC, "workstream": WORKSTREAM_DOC},
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_CONTROL, role=ArmRole.CONTROL,
        description=("the observed live book's existing behaviour: rest at the maker "
                     "price, cancel at 4h if unfilled, no queue-based cancellation"),
        params={"timeout_seconds": 14_400, "queue_cancel": False},
        strategy_tag=None,
    )
    service.add_arm(
        session, version, arm_key=ARM_TREATMENT, role=ArmRole.TREATMENT,
        description=("the same book plus the frozen queue-aware cancellation rule "
                     f"{FROZEN_RULE.rule_version} (shadow: recorded, not sent)"),
        params={"timeout_seconds": 14_400, "queue_cancel": True, **FROZEN_RULE_INPUTS},
        strategy_tag=SHADOW_TAG,
    )

    promotion_spec = {k: v for k, v in PROMOTION_GATE_SPEC.items()}
    if floor != 40:
        promotion_spec["sample"] = {
            ARM_TREATMENT: {"metric": "qac_would_cancel_orders", "deployment_kind": "probe",
                            "op": ">=", "value": floor},
        }
        promotion_spec["description"] = (
            f"{PROMOTION_GATE_SPEC['description']} Floored by the registering envelope "
            f"at {floor} would-cancel orders (reviewed floor 40)."
        )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=promotion_spec,
        from_state=LifecycleState.PROBE, to_state=LifecycleState.PAPER,
        registered_at=at,
        notes="pre-registered before any shadow decision exists",
    )
    kill_gate = service.register_gate(
        session, version, gate_key=KILL_GATE_KEY, kind="kill",
        spec=KILL_GATE_SPEC, registered_at=at,
        notes="pre-registered before any shadow decision exists",
    )
    service.freeze_version(session, version, now=at)
    service.mark_gate_evidence_started(session, promotion_gate, at=at)
    service.mark_gate_evidence_started(session, kill_gate, at=at)

    epoch = service.open_epoch(
        session, version,
        reason=("first shadow interval, pinned to the platform snapshot active at "
                "registration; decisions before this instant do not exist"),
        started_at=at,
    )
    transition = service.transition_experiment(
        session, experiment, LifecycleState.PROBE,
        actor=actor, occurred_at=at, version=version, epoch=epoch,
        reason=("contract frozen and gates pre-registered; the shadow instrument reads "
                "the live book and sends nothing"),
    )
    probe = service.register_deployment(
        session, epoch,
        deployment_key=SHADOW_DEPLOYMENT_KEY,
        stage=LifecycleState.PROBE, kind=DeploymentKind.PROBE,
        arms={ARM_CONTROL: None, ARM_TREATMENT: SHADOW_TAG},
        config={"instrument": "LiveExecutor.evaluate_queue_cancellations",
                "material": material_config()},
        started_at=at,
        notes=("SHADOW. The treatment arm's tag is a scope handle for the evaluator; no "
               "book carries it and no order is placed under it. Rows land in "
               "live_order_queue_decisions stamped with this arm's lineage id. The "
               "control arm is the observed book itself, so it needs no tag."),
    )
    return {
        "experiment": experiment,
        "version": version,
        "epoch": epoch,
        "gates": [promotion_gate, kill_gate],
        "promotion_gate": promotion_gate,
        "keep_gate": kill_gate,
        "probe": probe,
        "transition": transition,
        "registered_at": at,
    }


def active_lineage(session) -> dict:
    """The queue experiment's OPEN deployment arms, keyed by deployment kind, for the
    executor to stamp decision rows and to gate live cancellation.

        {"probe": {"deployment_key", "arm_link_id", "by_tag": {tag: arm_link_id}},
         "live":  {...}}

    Empty when the experiment does not exist or has no open epoch/deployment. Cheap
    (three small selects) and read once per reconcile."""
    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        return {}
    version = latest_version(session, experiment)
    if version is None:
        return {}
    epoch = open_epoch_for(session, version)
    if epoch is None:
        return {}
    out: dict = {}
    deployments = session.scalars(
        select(ExperimentDeployment).where(
            ExperimentDeployment.epoch_id == epoch.id,
            ExperimentDeployment.ended_at.is_(None),
        )
    ).all()
    for dep in deployments:
        rows = session.execute(
            select(ExperimentDeploymentArm.id, ExperimentDeploymentArm.strategy_tag)
            .where(ExperimentDeploymentArm.deployment_id == dep.id)
        ).all()
        by_tag = {tag: link_id for link_id, tag in rows if tag}
        # The treatment arm is the one carrying a tag on this experiment's deployments;
        # the control is deliberately tagless.
        arm_link_id = next(iter(by_tag.values()), None)
        out[dep.kind] = {"deployment_key": dep.deployment_key,
                         "arm_link_id": arm_link_id, "by_tag": by_tag}
    return out


def arm_link_ids_for_deployment_keys(session, keys: tuple[str, ...]) -> list[int]:
    """Deployment-arm link ids behind a set of deployment keys — the join the qac_*
    metric providers use to find this scope's decision rows."""
    if not keys:
        return []
    rows = session.execute(
        select(ExperimentDeploymentArm.id)
        .join(ExperimentDeployment,
              ExperimentDeployment.id == ExperimentDeploymentArm.deployment_id)
        .where(ExperimentDeployment.deployment_key.in_(keys),
               ExperimentDeploymentArm.strategy_tag.is_not(None))
    ).all()
    return [r[0] for r in rows]


__all__ = [
    "ARM_CONTROL", "ARM_TREATMENT", "BASELINE_LIVE_TAGS", "EXPERIMENT_KEY",
    "FROZEN_RULE_INPUTS", "KILL_GATE_KEY", "KILL_GATE_SPEC", "PROMOTION_GATE_KEY",
    "PROMOTION_GATE_SPEC", "RISK_ENVELOPE", "SHADOW_DEPLOYMENT_KEY", "SHADOW_TAG",
    "active_lineage", "arm_link_ids_for_deployment_keys", "material_config", "register",
]
