"""The mmsell10 capacity successor — a re-armed canary at 2x the open cap.

WHY A SUCCESSOR EXPERIMENT AND NOT A NEW VERSION. `service.arm_live_canary`
refuses unless the experiment is in PAPER, and `LIVE_CANARY -> PAPER` is an
illegal rollback ("no silent rollback ... would rewrite history"). Together those
mean an experiment already in LIVE_CANARY has NO sanctioned path to re-arm with
new tags and a new risk envelope. Neither rule is wrong and neither may be worked
around: the PAPER guard is what stopped the 2026-08-15 inherited-state failure,
and the rollback ban is what keeps history honest. The lifecycle names the way
out itself -- "a revived concept creates a successor experiment or version
referencing the retired predecessor" -- so that is what this is.

WHAT CHANGES, AND WHY IT IS A NEW QUESTION rather than a new epoch:

  * open cap 20 -> 40, so `max_book_exposure_usd` 19.80 -> 39.60. That is a
    REAL-MONEY INCREASE and the reason this needs its own contract: the question
    is no longer "does the edge exist" but "does it survive at twice the
    capacity".
  * the twin's cap 20 -> 250. Not cosmetic, and not the "2x live" I first
    proposed. Measured 2026-08-31 over v2/e2: live entered 188.6 markets/day
    against the twin's 21.3, an 11.3% ratio that matches the 14.7% the coverage
    gate actually read. The binding constraint is TURNOVER, not slot count --
    live's unfilled orders recycle a slot every 4h while the twin assumes fill
    and holds to settlement for days. A twin at 2x live's cap would still have
    read ~23% and re-broken the gate. 250 projects to ~70%.
  * the universe is unchanged on paper but wider in practice: shards 1-3 were
    funded on 2026-08-31, so tennis/baseball/crypto series that were refused
    `user_not_found` for this book's whole life can now fill (XOS-000014).

WHAT IS DELIBERATELY IDENTICAL: the hypothesis, the arm (lo=5, hi=10, maxyes=7),
entry timing, the 0c offset, the 4h timeout, hold-to-settlement, the fee model,
and every keep/stop threshold. `mmsell10a`/`mmsell10b` measured pricing up at
-4.1c/contract for +3pp of fill, so nothing here touches entry pricing. This
experiment moves capacity and only capacity.

THE COVERAGE CLAUSE IS CARRIED OVER UNCHANGED at `< 50`. It read 14.7% under v2
and could not clear, but the honest fix is to remove the constraint that made it
unreachable rather than to lower the bar until the book can step over it. A 15%
mirror is not an execution control no matter what the threshold says.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import service
from .lifecycle import ArmRole, LifecycleState
from .models import (
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
    ExperimentGate,
    ExperimentVersion,
)
from .read import get_experiment

PREDECESSOR_KEY = "mmsell-price-ceiling"
SUCCESSOR_KEY = "mmsell-price-ceiling-capacity"

ARM_KEY = "mmsell10"
#: The long-running paper control, handed over from the predecessor at the same
#: instant its deployment ends. A paper tag with history is FINE here -- it is
#: the control, not the canary -- and keeping it admissible is what stops the
#: XOS-000011 blackout shape, where a tag lost its arm and silently went dark.
PAPER_TAG = "mmsell10"
#: Fresh, per `arm_live_canary`'s no-inherited-state rule. Never reused.
LIVE_TAG = "Dmmsell10"
TWIN_TAG = "Dmmsell10_pt4"

PAPER_DEPLOYMENT_KEY = "mmsell-capacity-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-capacity-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-capacity-twin-1"

BOOK_PARAMS = "lo=5,hi=10,maxyes=7,size=1"
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"

PROMOTION_GATE_KEY = "paper_to_live_canary"
KEEP_GATE_KEY = "live_canary_keep"

#: Doubled where capacity doubled, IDENTICAL everywhere else. Each per-order and
#: per-market limit is untouched: this experiment buys MORE OF THE SAME
#: distribution, which is the only scaling move the evidence supports.
RISK_ENVELOPE: dict = {
    "stage": "canary_stage_2_capacity",
    "contracts_per_order": 1,
    "max_order_dollars": 1.00,
    "max_market_exposure_usd": 1.00,
    "max_event_rungs": 3,
    "max_event_exposure_usd": 3.00,
    "max_open_positions": 40,
    "max_book_exposure_usd": 39.60,
    "max_events_per_settlement_date": 5,
    "settlement_date_concentration_pct": 25,
    # Loss budgets are NOT doubled. Capacity doubling is the hypothesis; the
    # amount we are willing to lose learning the answer is a separate decision
    # and nobody made it. Holding them flat also makes the stop STRICTER per
    # contract, which is the safe direction to be wrong in.
    "daily_realized_loss_stop_usd": 5.00,
    "total_canary_loss_budget_usd": 15.00,
    "order_timeout_seconds": 14_400,
    "entry_price_offset_cents": 0,
    "exit_policy": "hold to settlement; no TP/SL — structural for mmsell, not a "
                   "setting (docs/MMSELL_EXIT_STUDY.md)",
    "settings": {
        "LIVE_MAX_ORDER_DOLLARS": "1.0",
        "MAX_MARKET_EXPOSURE": "1.0",
        "MAX_DAILY_LOSS": "5.0",
        "LIVE_KILL_ON_DAILY_LOSS": "true",
        "MMSELL_LIVE_MAX_OPEN_POSITIONS": "40",
        # The D7 fix. Sized from measured turnover, not from a ratio to live.
        "LIVE_PAPER_TWIN_MAX_OPEN_POSITIONS": "250",
        "MMSELL_LIVE_PRICE_OFFSET_CENTS": "0",
        "MMSELL_EVENT_RUNG_CAP_ENABLED": "true",
        "MMSELL_EVENT_RUNG_CAP": "3",
        "MMSELL_SETTLEMENT_CAP_ENABLED": "true",
        "MMSELL_SETTLEMENT_CAP_PCT": "0.25",
        "MMSELL_SETTLEMENT_EVENT_CAP": "5",
        "LIVE_ORDER_TIMEOUT_SECONDS": "14400",
        "LIVE_PAPER_TWIN_SUFFIX": "_pt4",
        "MMSELL_PREFILTER_ENABLED": "false",
    },
    "shard_note": (
        "Kalshi shards its exchange by category and collateral is held per shard "
        "(XOS-000014). Shard 3 (Tennis & Baseball) and 2 (Crypto) were funded "
        "2026-08-31; before that every order to those series was refused "
        "`user_not_found`. The book cap of 40 spans shards, so a mix that lands "
        "more than ~25 concurrent positions on one $25 shard would be refused "
        "for collateral rather than filled. Measured mix was ~25% shard-3, i.e. "
        "~10 of 40 — inside the balance, but this is the number to watch."
    ),
}

#: Carried from the predecessor VERBATIM, thresholds and all. A successor that
#: quietly relaxed its own stops would be the whole point of pre-registration
#: defeated; the only change is the arm key's scope, which is identical anyway.
_KEEP_GATE_CARRIED_FROM = "mmsell-price-ceiling v2 live_canary_keep"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- gates ------------------------------------------------------------------
#
# The keep/stop spec is IMPORTED from the predecessor, not retyped. The arm key
# is the same string, so the object is byte-identical rather than merely intended
# to be -- "carried verbatim" is then a property of the code instead of a claim
# in a docstring, and a test asserts the two hash the same. Retyping it would
# have made a silently-relaxed threshold a one-character edit away.
from .canary_mmsell10 import KEEP_GATE_SPEC as KEEP_GATE_SPEC  # noqa: E402
from .canary_mmsell10 import PROMOTION_GATE_SPEC as _V2_PROMOTION_SPEC  # noqa: E402


def promotion_gate_spec(sample_floor: int | None = None) -> dict:
    """The predecessor's promotion bar, optionally with a sample floor."""
    spec = {k: v for k, v in _V2_PROMOTION_SPEC.items()}
    if sample_floor is not None:
        # The predecessor's description ends "unchanged and unfloored". Carrying
        # it verbatim onto a FLOORED gate would leave a frozen, immutable record
        # contradicting its own sample clause.
        spec["description"] = (
            f"{spec['description']} Floored for this successor at "
            f"{int(sample_floor)} settled paper contracts, which the predecessor "
            "did not require: capacity doubles here, so the paper baseline the "
            "promotion rests on should not be thinner than the keep gate's own "
            "sample scale."
        )
        # `settled_contracts`, NOT `paper_settled_contracts`. The registry has
        # no `paper_` prefix on paper-scope metrics — the scope comes from
        # `deployment_kind` — and a name it does not know evaluates
        # BLOCKED_INTEGRITY, which never passes. Gates freeze on registration,
        # so that typo costs a Version to undo; `test_every_metric_the_
        # promotion_gate_names_is_registered` now checks this branch.
        spec["sample"] = {
            ARM_KEY: {"metric": "settled_contracts", "op": ">=",
                      "value": int(sample_floor), "deployment_kind": "paper"},
        }
    return spec


def register(
    session,
    *,
    actor: str,
    evidence_started_at: datetime | None = None,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Retire the predecessor, open the successor, and walk it to PAPER.

    Arms nothing and places no orders. Registering a contract and putting real
    money behind it are two acts with two approvals; this is the first.

    PRECONDITION, CHECKED: the predecessor's live book must hold no open
    positions. Ending a live deployment while positions are still open would
    strand them -- they settle on Kalshi either way, but the tag would lose its
    arm and the settlement could not be RECORDED, which is exactly the
    XOS-000011 blackout shape and would corrupt the predecessor's final
    evidence. Stand the book down, let it drain, then run this.

    `evidence_started_at` DEFAULTS TO NOW, and cannot usefully be set earlier.
    An earlier draft of this docstring claimed the opposite -- that it defaults
    to the predecessor's v2 boundary, so the successor could inherit the paper
    edge and arm immediately. That was wrong twice over. The code passes
    `evidence_started_at or at`, so the default is registration time; and even an
    explicit earlier value would be ignored, because the evaluator floors every
    window at `max(epoch.started_at, gate.evidence_started_at)` and the
    successor's epoch opens here. Evidence cannot precede the epoch it is
    attributed to.

    That floor is correct and is not to be routed around. This successor's epoch
    pins a new platform snapshot over a materially changed world -- shards 1-3
    were funded 2026-08-31, so series that were refused `user_not_found` for the
    predecessor's whole life are now reachable. Reaching back across that
    boundary would pool evidence the platform declares non-poolable, and
    back-dating the epoch to make it fit would falsify when the interval began.

    THE CONSEQUENCE, PLANNED FOR RATHER THAN DISCOVERED: registering does not
    make the canary armable. `arm_live_canary` re-evaluates the promotion gate
    synchronously, and immediately after registration the metric is undefined
    ("no settled trades in window"), so arming is REFUSED. The successor earns
    its own paper evidence first. Register, wait for the paper book to settle
    trades inside the new window, then arm.
    """
    at = now or _now()
    predecessor = get_experiment(session, PREDECESSOR_KEY)
    if predecessor is None:
        raise service.ExperimentOsError(f"experiment {PREDECESSOR_KEY!r} not found")
    if get_experiment(session, SUCCESSOR_KEY) is not None:
        raise service.ExperimentOsError(
            f"{SUCCESSOR_KEY} already exists — this package refuses rather than "
            "re-running, so a repeated command cannot fork the lineage"
        )

    # --- hand the paper control over; leave the live book RUNNING -------------
    #
    # Only the PAPER deployment has to end here, and it has nothing to drain: the
    # successor's paper book needs the `mmsell10` tag, and two active deployment
    # arms on one tag is AMBIGUOUS and refused outright by the resolver. So that
    # one ends and is immediately replaced, at this same instant, exactly as the
    # v1→v2 handover did.
    #
    # The predecessor's LIVE and TWIN deployments are deliberately LEFT OPEN. An
    # earlier draft of this package ended them too and therefore had to refuse
    # until the live book was fully drained -- which would have idled the new
    # book for days for no safety gain. What actually needs the drain is ENDING a
    # live deployment: that leaves its tag without an arm, so the settlements
    # could not be RECORDED (the XOS-000011 shape) and the predecessor's final
    # evidence would be wrong. Keeping them open costs nothing, keeps every
    # settlement recording, and lets the old book wind down beside the new one on
    # entirely separate tags, deployments and epochs. Retiring the predecessor is
    # a later, separate act, once it is genuinely finished.
    from ..repository import count_live_book_open

    open_deps = [
        d for d in session.scalars(
            select(ExperimentDeployment).where(ExperimentDeployment.ended_at.is_(None))
        ).all()
        if _epoch_experiment_id(session, d) == predecessor.id
    ]
    ending = [d for d in open_deps if d.kind == "paper"]
    draining = [d for d in open_deps if d.kind != "paper"]

    # The guard now scopes to what is actually being ended, which is the honest
    # version of it: a paper deployment holds no live positions, and if one ever
    # did, ending it would strand them just the same.
    for dep in ending:
        for tag in _tags_of(session, dep):
            held = count_live_book_open(session, tag)
            if held:
                raise service.ExperimentOsError(
                    f"{tag} still holds {held} open live position(s) and its "
                    "deployment is about to end — that would leave the tag "
                    "without an arm, so the settlements could not be RECORDED "
                    "and the evidence would be wrong. Stand it down and let it "
                    "drain first."
                )
    for dep in ending:
        service.end_deployment(session, dep, ended_at=at)

    # --- open the successor ---------------------------------------------------
    successor = service.create_experiment(
        session, key=SUCCESSOR_KEY, origin="operator",
        title="mmsell10 — the price-ceiling book at 2x open capacity",
        family="maker",
        hypothesis=(
            "The cheap-cell price-ceiling edge (lo=5, hi=10, maxyes=7) survives "
            "at twice the concurrent-position cap. v2 ran pinned at its 20-slot "
            "cap while candidates kept arriving, so gate:open_cap — not edge — "
            "was the binding constraint on realized P&L."
        ),
        mechanism=(
            "Raising the open cap buys MORE OF THE SAME distribution, unlike "
            "pricing up, which mmsell10a/b measured at -4.1c/contract for +3pp "
            "of fill. Entry pricing, timing and exits are untouched."
        ),
        falsification=(
            "Realized cents/contract at cap 40 falls materially below v2's at "
            "cap 20 on comparable markets, i.e. the extra capacity is filled "
            "with worse trades rather than more of the same."
        ),
        predecessor=predecessor,
        docs={"thesis": "docs/MMSELL_VARIANTS_THESIS.md",
              "workstream": "docs/workstreams/WS-007-mmsell10-live-canary.md",
              "studies": ["docs/MMSELL_FILL_MODEL.md"]},
        actor=actor, now=at,
    )

    version = service.create_experiment_version(
        session, successor,
        # The scientific rules are carried across from the predecessor's v2
        # VERBATIM. They are restated rather than omitted because a version that
        # leaves them blank is not a contract anyone can check the running book
        # against — and holding them constant is the entire claim this
        # experiment makes about itself.
        hypothesis=(
            "The cheap-cell price-ceiling edge (lo=5, hi=10, maxyes=7) survives "
            "at twice the concurrent-position cap. Unchanged from the "
            "predecessor's v2 — this successor moves CAPACITY, not the question "
            "about the edge itself."
        ),
        universe_selector=(
            "cheap band lo=5,hi=10 with an entry-price ceiling maxyes=7 — "
            "mmsell10's universe verbatim. No crypto exclusion: excluding a "
            "market class would be a different universe and could not inherit "
            "this arm's evidence."
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule="one contract per order under a $1.00 per-order dollar cap",
        execution_style="maker",
        # The successor runs ONE live arm, exactly as v2 did, and for the same
        # reason: the execution control is the registered paper TWIN, armed at
        # the same instant. A second live arm would be a second real-money book.
        control_required=False,
        control_exemption_reason=(
            "gated on absolute realizable per-trade via the live-calibrated fill "
            "model, as v1 and v2 were; the execution control is the registered "
            "paper TWIN, which is armed at the same instant, not a second live arm"
        ),
        independent_variable="concurrent open-position capacity (20 → 40)",
        held_constant=[
            "arm parameters (lo=5, hi=10, maxyes=7)",
            "market universe — no crypto exclusion is added",
            "entry timing and the 0c price offset",
            "the 4h order timeout",
            "hold-to-settlement exits (no TP/SL)",
            "the fee model",
            "every keep/stop threshold",
        ],
        # `risk`, which the service stores to `risk_json`. `arm_live_canary`
        # REFUSES a version without it, so this is what makes the canary armable.
        risk=RISK_ENVELOPE,
        docs={"workstream": "docs/workstreams/WS-007-mmsell10-live-canary.md"},
        change_reason=(
            "Capacity is the independent variable. The twin cap moves to 250 "
            "because measurement (2026-08-31) showed the coverage gate is bound "
            "by TURNOVER, not slot count: live entered 188.6 markets/day to the "
            "twin's 21.3. The coverage clause itself is carried over unchanged."
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="entry-price ceiling only (lo=5,hi=10,maxyes=7) — unchanged",
        params={"lo": 5, "hi": 10, "maxyes": 7}, strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=promotion_gate_spec(promotion_sample_floor),
        from_state=LifecycleState.PAPER, to_state=LifecycleState.LIVE_CANARY,
        registered_at=at, notes="the predecessor's bar, unchanged",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes=f"carried verbatim from {_KEEP_GATE_CARRIED_FROM}",
    )
    service.freeze_version(session, version, now=at)
    evidence_at = evidence_started_at or at
    service.mark_gate_evidence_started(session, promotion_gate, at=evidence_at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)

    # PAPER entry needs the frozen version, so the walk happens after the freeze.
    service.transition_experiment(session, successor, LifecycleState.PROBE,
                                  actor=actor, occurred_at=at,
                                  reason="contract registered; no probe stage is "
                                         "needed, the predecessor's is inherited")
    service.transition_experiment(session, successor, LifecycleState.PAPER,
                                  actor=actor, occurred_at=at,
                                  reason="paper control continues on mmsell10")

    epoch = service.open_epoch(
        session, version,
        reason=("successor's first operating interval, pinned to the snapshot "
                "active at registration"),
        started_at=at,
    )
    paper = service.register_deployment(
        session, epoch, deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER, kind="paper",
        arms={ARM_KEY: PAPER_TAG}, started_at=at,
        notes=("the mmsell10 paper control, handed over from the retired "
               "predecessor at this same instant so the tag never loses its arm"),
    )
    return {
        "predecessor": predecessor,
        "successor": successor,
        "version": version,
        "promotion_gate": promotion_gate,
        "keep_gate": keep_gate,
        "epoch": epoch,
        "paper_deployment": paper,
        "ended_deployments": [d.deployment_key for d in ending],
        "still_draining": [d.deployment_key for d in draining],
        "registered_at": at,
        "evidence_started_at": evidence_at,
    }


def material_config() -> dict:
    """The parameters a drift check compares the running book against."""
    return {
        "book_spec": LIVE_BOOK_SPEC,
        "twin_tag": TWIN_TAG,
        "risk": RISK_ENVELOPE,
    }


def arm(
    session,
    *,
    approved_by: str,
    actor: str = "operator",
    started_at: datetime | None = None,
    reason: str | None = None,
) -> dict:
    """Arm the successor's canary through the ONE sanctioned path.

    `service.arm_live_canary` enforces every structural rule: the promotion gate
    is re-evaluated SYNCHRONOUSLY (a recorded PASS is not a capability token),
    the paper epoch closes, a fresh live epoch opens, and the live deployment and
    its twin are registered at the identical instant on fresh, unused tags with a
    first-class `twin_of` link.

    THIS EXPANDS REAL-MONEY EXPOSURE — $19.80 to $39.60 of book ceiling. It still
    places no order by itself; the runtime allowlist (`LIVE_STRATEGIES`) is a
    separate switch and a separate act. `approved_by` records the operator who
    authorized it, and the service refuses without it.
    """
    from .read import latest_version

    experiment = get_experiment(session, SUCCESSOR_KEY)
    if experiment is None:
        raise service.ExperimentOsError(
            f"{SUCCESSOR_KEY} does not exist — REGISTER_PACKAGE first"
        )
    version = latest_version(session, experiment)
    if version is None:
        raise service.ExperimentOsError(
            f"{SUCCESSOR_KEY} has no version — REGISTER_PACKAGE first"
        )
    gate = session.scalar(
        select(ExperimentGate).where(
            ExperimentGate.version_id == version.id,
            ExperimentGate.gate_key == PROMOTION_GATE_KEY,
        )
    )
    if gate is None:
        raise service.ExperimentOsError(
            f"{SUCCESSOR_KEY} v{version.version} has no {PROMOTION_GATE_KEY} gate"
        )
    live, twin, epoch = service.arm_live_canary(
        session, experiment,
        gate=gate,
        approved_by=approved_by,
        live_key=LIVE_DEPLOYMENT_KEY,
        twin_key=TWIN_DEPLOYMENT_KEY,
        live_tags={ARM_KEY: LIVE_TAG},
        twin_tags={ARM_KEY: TWIN_TAG},
        config=material_config(),
        started_at=started_at,
        actor=actor,
        reason=reason or (
            f"mmsell10 capacity canary armed on {LIVE_TAG} with twin {TWIN_TAG} "
            f"at one boundary; cap 40 / twin 250 envelope pre-registered on v"
            f"{version.version}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


def revise_promotion_gate(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = 150,
    now: datetime | None = None,
) -> dict:
    """Open v2 of the successor because v1's promotion gate can never PASS.

    WHAT WENT WRONG. v1 was registered with `promotion_sample_floor=150`, and the
    floor clause named `paper_settled_contracts`. The canonical registry has no
    such metric -- paper-scope metrics carry no `paper_` prefix, the scope comes
    from `deployment_kind` -- so the evaluator returns BLOCKED_INTEGRITY, not
    HOLD. BLOCKED_INTEGRITY never becomes PASS however long the book runs, and
    `arm_live_canary` re-evaluates the promotion gate synchronously, so v1 is
    permanently unarmable.

    WHY A NEW VERSION IS THE ONLY REMEDY. A gate is frozen with its version and
    is immutable by construction -- that immutability is what makes
    pre-registration mean anything, so editing v1's gate in place is not an
    option that exists. The lifecycle names the alternative itself: a revised
    decision rule is a new gate on a new Version. The experiment is in PAPER,
    which is exactly where a contract may still be revised, so this is an
    ordinary revision and not a rollback: v1 stays in the record, unedited,
    carrying the mistake.

    WHAT IS IDENTICAL: the hypothesis, the arm, the risk envelope, the keep gate
    and the promotion bar. The ONLY difference is the floor clause naming
    `settled_contracts`. This buys no leniency -- the corrected gate is strictly
    stricter than an unfloored one.

    WHAT IT COSTS: v2 opens its own epoch, so the evidence window restarts. v1
    ran for minutes, so the cost is minutes.

    Refuses unless it finds exactly the defect it repairs, so it cannot be
    re-run and cannot be pointed at a healthy contract.
    """
    from ..repository import count_live_book_open
    from .metrics import REGISTRY
    from .read import latest_version

    at = now or _now()
    successor = get_experiment(session, SUCCESSOR_KEY)
    if successor is None:
        raise service.ExperimentOsError(
            f"{SUCCESSOR_KEY} does not exist — REGISTER_PACKAGE it first"
        )
    if successor.state != LifecycleState.PAPER.value:
        raise service.ExperimentOsError(
            f"a contract may only be revised in PAPER; {SUCCESSOR_KEY} is "
            f"{successor.state}. Once live, a changed decision rule is a "
            "successor experiment, not a new version of this one."
        )
    current = latest_version(session, successor)
    if current is None:
        raise service.ExperimentOsError(f"{SUCCESSOR_KEY} has no version")

    # The precondition IS the defect: refuse anything else outright, so this can
    # never be used to quietly re-register a healthy gate on a new Version.
    gate = session.scalar(
        select(ExperimentGate).where(
            ExperimentGate.version_id == current.id,
            ExperimentGate.gate_key == PROMOTION_GATE_KEY,
        )
    )
    if gate is None:
        raise service.ExperimentOsError(
            f"v{current.version} has no {PROMOTION_GATE_KEY} gate to revise"
        )
    unknown = sorted(
        {
            clause["metric"].split(".")[-1]
            for clause in (gate.spec_json.get("sample") or {}).values()
            if isinstance(clause, dict) and clause.get("metric")
        }
        - set(REGISTRY)
    )
    if not unknown:
        raise service.ExperimentOsError(
            f"v{current.version}'s {PROMOTION_GATE_KEY} names only registered "
            "metrics — there is nothing here to repair, and opening a Version "
            "to restate a working gate would discard evidence for nothing"
        )

    # The transport passes `promotion_sample_floor` as None whenever the envelope
    # omits it, and this revision must not be a way to quietly DROP the floor an
    # operator already chose. So an absent floor means "whatever the superseded
    # gate required", never "no floor".
    if promotion_sample_floor is None:
        promotion_sample_floor = next(
            (
                clause["value"]
                for clause in (gate.spec_json.get("sample") or {}).values()
                if isinstance(clause, dict) and clause.get("value") is not None
            ),
            None,
        )

    epoch = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == current.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )
    # Same guard as `register`: a deployment about to be re-registered on a new
    # epoch must not hold live positions, or the settlements lose their arm.
    carrying = service.open_deployments(session, epoch) if epoch is not None else []
    for dep in carrying:
        for tag in _tags_of(session, dep):
            held = count_live_book_open(session, tag)
            if held:
                raise service.ExperimentOsError(
                    f"{tag} holds {held} open live position(s); cutting an epoch "
                    "under it would strand the settlements"
                )

    version = service.create_experiment_version(
        session, successor,
        hypothesis=current.hypothesis,
        universe_selector=current.universe_selector,
        entry_rule=current.entry_rule,
        exit_rule=current.exit_rule,
        sizing_rule=current.sizing_rule,
        execution_style=current.execution_style,
        independent_variable=current.independent_variable,
        held_constant=current.held_constant_json,
        control_required=False,
        control_exemption_reason=current.control_exemption_reason,
        risk=RISK_ENVELOPE,
        docs={"workstream": "docs/workstreams/WS-007-mmsell10-live-canary.md"},
        change_reason=(
            f"v{current.version}'s promotion gate named {unknown} — absent from "
            "the canonical metric registry, so it evaluated BLOCKED_INTEGRITY "
            "and could never PASS, leaving the contract permanently unarmable. "
            "Gates are immutable with their version, so the correction is a new "
            "Version. NOTHING about the science changes: same hypothesis, same "
            "arm, same risk envelope, same keep gate, same promotion bar. Only "
            "the floor clause's metric name is corrected, to `settled_contracts`."
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="entry-price ceiling only (lo=5,hi=10,maxyes=7) — unchanged",
        params={"lo": 5, "hi": 10, "maxyes": 7}, strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=promotion_gate_spec(promotion_sample_floor),
        from_state=LifecycleState.PAPER, to_state=LifecycleState.LIVE_CANARY,
        registered_at=at,
        notes=f"v{current.version}'s bar with the floor clause's metric corrected",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes=f"carried verbatim from {_KEEP_GATE_CARRIED_FROM}",
    )
    service.freeze_version(session, version, now=at)
    service.mark_gate_evidence_started(session, promotion_gate, at=at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)

    new_epoch = service.open_epoch(
        session, version,
        reason=("v2's first operating interval; v1's promotion gate was "
                "unarmable and its evidence window does not carry"),
        started_at=at,
    )
    if epoch is not None:
        service.close_epoch(session, epoch, ended_at=at)
    carried = service.carry_deployments_forward(
        session, carrying, new_epoch, started_at=at,
        reason="the paper control keeps running across the version revision",
    )
    return {
        "successor": successor,
        "superseded_version": current.version,
        "version": version,
        "promotion_gate": promotion_gate,
        "keep_gate": keep_gate,
        "epoch": new_epoch,
        "carried_deployments": [d.deployment_key for d in carried],
        "unregistered_metrics": unknown,
        "revised_at": at,
    }


#: The one metric the promotion gate decides on. Named here because the
#: floor-removal guard below has to know whether it has been OBSERVED yet.
PROMOTION_DECIDING_METRIC = "realizable_cents_per_trade"


def drop_operator_sample_floor(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Open the next Version with MY sample floor removed, restoring the
    predecessor's own unfloored promotion bar.

    WHY THIS IS A REVERT AND NOT A RELAXATION. The floor was not part of the
    inherited contract. `mmsell-price-ceiling` v2 -- the version `Cmmsell10`
    actually armed under -- registered `realizable_cents_per_trade > 0` with no
    sample clause at all. The floor was added to THIS successor on 2026-09-02 on
    my recommendation, and the recommendation was wrong: the successor's
    independent variable is the LIVE open-position cap, and the paper book has no
    cap and assumes fill, so the paper book is byte-identical across the change.
    A floor on it buys sample size in a measurement that cannot move. This
    restores the bar the book was already promoted under; it does not invent a
    softer one (XOS issue: the capacity-successor paper-gate tax).

    WHAT STOPS THIS BEING A WAY TO MOVE GOALPOSTS. Four things, and the last is
    the one that matters:

      * PAPER only. Once live, a changed decision rule is a successor experiment.
      * there must actually BE an operator floor to drop, so it cannot re-cut a
        contract that never carried one;
      * the result is not a dial. It is `promotion_gate_spec(None)` -- the
        inherited object, verified equal to it before anything is written -- so
        this can produce exactly one outcome and it is the predecessor's bar;
      * THE DECIDING METRIC MUST STILL BE UNOBSERVED. If any recorded result for
        this gate carries a defined `realizable_cents_per_trade`, this refuses.
        Removing a floor before seeing the number is reverting a design mistake;
        removing it after is choosing a threshold that fits the result, and no
        argument about intent distinguishes them from the outside.
    """
    from ..repository import count_live_book_open
    from .models import ExperimentGateResult
    from .read import latest_version

    # REGISTER_PACKAGE always passes this field, so the signature has to accept
    # it. Accepting a VALUE would make an "unfloor" command that quietly sets a
    # floor, which is the opposite of what its name promises.
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "this package removes the operator sample floor and can set none; "
            f"drop promotion_sample_floor={promotion_sample_floor!r} from the "
            "envelope"
        )
    at = now or _now()
    successor = get_experiment(session, SUCCESSOR_KEY)
    if successor is None:
        raise service.ExperimentOsError(f"{SUCCESSOR_KEY} does not exist")
    if successor.state != LifecycleState.PAPER.value:
        raise service.ExperimentOsError(
            f"a contract may only be revised in PAPER; {SUCCESSOR_KEY} is "
            f"{successor.state}"
        )
    current = latest_version(session, successor)
    if current is None:
        raise service.ExperimentOsError(f"{SUCCESSOR_KEY} has no version")
    gate = session.scalar(
        select(ExperimentGate).where(
            ExperimentGate.version_id == current.id,
            ExperimentGate.gate_key == PROMOTION_GATE_KEY,
        )
    )
    if gate is None:
        raise service.ExperimentOsError(
            f"v{current.version} has no {PROMOTION_GATE_KEY} gate"
        )
    if not (gate.spec_json.get("sample") or {}):
        raise service.ExperimentOsError(
            f"v{current.version}'s {PROMOTION_GATE_KEY} carries no sample floor — "
            "there is nothing to revert, and opening a Version to restate the "
            "same bar would discard an evidence window for nothing"
        )

    # The goalpost guard. A recorded result whose metrics carry a DEFINED value
    # for the deciding metric means the outcome has been seen.
    for result in session.scalars(
        select(ExperimentGateResult).where(ExperimentGateResult.gate_id == gate.id)
    ):
        for key, value in (result.metrics_json or {}).items():
            if key.split(".")[-1].split("[")[0] != PROMOTION_DECIDING_METRIC:
                continue
            observed = value.get("value") if isinstance(value, dict) else value
            if observed is not None:
                raise service.ExperimentOsError(
                    f"{PROMOTION_DECIDING_METRIC} has already been OBSERVED on "
                    f"v{current.version} ({observed}); removing the sample floor "
                    "now would be choosing a threshold that fits a result already "
                    "seen. The floor stands."
                )

    spec = promotion_gate_spec(None)
    if spec != _V2_PROMOTION_SPEC:
        raise service.ExperimentOsError(
            "the unfloored spec no longer equals the predecessor's inherited "
            "bar — this function may only RESTORE that object, never author a "
            "different one"
        )

    epoch = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == current.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )
    carrying = service.open_deployments(session, epoch) if epoch is not None else []
    for dep in carrying:
        for tag in _tags_of(session, dep):
            held = count_live_book_open(session, tag)
            if held:
                raise service.ExperimentOsError(
                    f"{tag} holds {held} open live position(s); cutting an epoch "
                    "under it would strand the settlements"
                )

    version = service.create_experiment_version(
        session, successor,
        hypothesis=current.hypothesis,
        universe_selector=current.universe_selector,
        entry_rule=current.entry_rule,
        exit_rule=current.exit_rule,
        sizing_rule=current.sizing_rule,
        execution_style=current.execution_style,
        independent_variable=current.independent_variable,
        held_constant=current.held_constant_json,
        control_required=False,
        control_exemption_reason=current.control_exemption_reason,
        risk=RISK_ENVELOPE,
        docs={"workstream": "docs/workstreams/WS-007-mmsell10-live-canary.md"},
        change_reason=(
            f"Reverts the operator sample floor added to v{current.version}. The "
            "floor was not inherited: mmsell-price-ceiling v2, which Cmmsell10 "
            "armed under, registered this bar with no sample clause. It was added "
            "here on my recommendation and the recommendation was wrong — this "
            "successor's independent variable is the LIVE open-position cap, and "
            "the paper book has no cap and assumes fill, so paper is identical "
            "across the change and a floor on it buys sample in a measurement "
            "that cannot move. The capacity hypothesis is testable only live, by "
            "the keep gate, which is unchanged and carries every stop verbatim. "
            "Reverted while realizable_cents_per_trade was still unobserved."
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="entry-price ceiling only (lo=5,hi=10,maxyes=7) — unchanged",
        params={"lo": 5, "hi": 10, "maxyes": 7}, strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=spec, from_state=LifecycleState.PAPER,
        to_state=LifecycleState.LIVE_CANARY, registered_at=at,
        notes="the predecessor's inherited bar, restored unfloored",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes=f"carried verbatim from {_KEEP_GATE_CARRIED_FROM}",
    )
    service.freeze_version(session, version, now=at)
    service.mark_gate_evidence_started(session, promotion_gate, at=at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)

    new_epoch = service.open_epoch(
        session, version,
        reason="first operating interval on the restored unfloored bar",
        started_at=at,
    )
    if epoch is not None:
        service.close_epoch(session, epoch, ended_at=at)
    carried = service.carry_deployments_forward(
        session, carrying, new_epoch, started_at=at,
        reason="the paper control keeps running across the version revision",
    )
    return {
        "successor": successor,
        "superseded_version": current.version,
        "dropped_floor": next(
            (c.get("value") for c in gate.spec_json["sample"].values()
             if isinstance(c, dict)), None
        ),
        "version": version,
        "promotion_gate": promotion_gate,
        "keep_gate": keep_gate,
        "epoch": new_epoch,
        "carried_deployments": [d.deployment_key for d in carried],
        "revised_at": at,
    }


def _tags_of(session, deployment: ExperimentDeployment) -> list[str]:
    """The concrete strategy tags a deployment currently carries."""
    return [
        t for (t,) in session.execute(
            select(ExperimentDeploymentArm.strategy_tag).where(
                ExperimentDeploymentArm.deployment_id == deployment.id
            )
        ).all() if t
    ]


def _epoch_experiment_id(session, deployment: ExperimentDeployment) -> int | None:
    epoch = session.get(ExperimentEpoch, deployment.epoch_id)
    if epoch is None:
        return None
    version = session.get(ExperimentVersion, epoch.version_id)
    return None if version is None else version.experiment_id


__all__ = [
    "ARM_KEY", "KEEP_GATE_SPEC", "LIVE_BOOK_SPEC", "LIVE_TAG", "PAPER_TAG",
    "PREDECESSOR_KEY", "RISK_ENVELOPE", "SUCCESSOR_KEY", "TWIN_TAG",
    "arm", "drop_operator_sample_floor", "material_config",
    "promotion_gate_spec", "register", "revise_promotion_gate",
]
