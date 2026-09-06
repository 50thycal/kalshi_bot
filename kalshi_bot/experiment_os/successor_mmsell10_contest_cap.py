"""The mmsell10 CONTEST-CAP successor — the same book, one bound corrected.

READ THIS FIRST: THIS IS NOT A PROMOTION AND `contestcap=1` IS NOT A TREATMENT.

The live risk envelope this successor inherits ALREADY carries `max_event_rungs:
3`. Nobody A/B tested that number; it is a bound, pre-registered as one. What
XOS-000020 established is that it counts `event_ticker`, which is series ×
contest -- so it caps 3 rungs per LISTING, not per GAME. One MLB game is up to
five listings (`KXMLBTOTAL`, `KXMLBTEAMTOTAL`, `KXMLBSPREAD`, `KXMLBHR`,
`KXMLBF5TOTAL`), so a book may legally hold ~15 correlated positions on nine
innings and no cap notices.

`max_contest_positions: 1` is the CORRECTION to that existing bound. It belongs
in the risk envelope beside the $1/order cap and the 40-open cap, and it is
gated exactly the way those are: not at all. It can only ever REFUSE an entry,
never add one, so it moves real-money exposure in the safe direction only.

Consequently this package registers NO new promotion criterion. The promotion
gate and the keep gate are the predecessor's own objects, imported rather than
retyped. Designing a bar the cap has to "win" would be inventing a promotion
criterion for a risk limit, which is a category error -- and a gate authored
after seeing the evidence below would not be a pre-registration at all.

WHAT THE EVIDENCE ACTUALLY SUPPORTS (the prior, not a result)

  * Live, replayed on `Dmmsell10`'s own 97 fills, keeping the first N per
    contest: actual -3.87 | cap4 -3.22 | cap3 -0.49 | cap2 -0.09 | cap1 +0.43.
  * Paper replay over `mmsell10`, n=3,296 across 35 settlement days:
      - contest GROUPING alone: +0.09c/trade, worst day UNCHANGED
        (-9.49 -> -9.56), and WORSE than its control risk-adjusted
        (daily mean/sd 0.218 -> 0.153);
      - capping EVERY correlation unit at 1: +0.61c/trade, worst day -76%
        (-> -2.31), daily volatility -48%, mean/sd -> 0.326.

  READ THE SECOND BULLET CAREFULLY. The cross-series sports grouping the
  mechanism was NAMED for is the half that does nothing. The value comes from
  `regimes.contest_key_of` falling back to the event ticker outside
  `CONTEST_GROUPED_REGIMES`, which tightens ladders from 3 rungs to 1. Both
  halves are delivered by `contestcap=1` at once. DO NOT "improve" this by
  restricting the grouping to sports: that keeps the half that measured nothing
  and discards the half carrying the effect.

  All of it is post-hoc replay on PAPER fills. It sets the prior. It is not a
  result, and nothing here is gated on it.

WHY A SUCCESSOR EXPERIMENT AND NOT A RE-ARM. `service.arm_live_canary` requires
state PAPER, and LIVE_CANARY -> PAPER is an illegal rollback. Its predecessor
`mmsell-price-ceiling-capacity` is already LIVE_CANARY, so it has no sanctioned
path to re-arm with fresh tags and a corrected envelope. Neither rule may be
worked around: the PAPER guard is what stopped the 2026-08-15 inherited-state
failure, and the rollback ban is what keeps history honest. The lifecycle names
the way out itself -- a revived concept creates a successor referencing its
predecessor -- so that is what this is. This is the same argument, and the same
shape, as `successor_mmsell10_capacity`, on which this module is modelled.

WHAT CHANGES: exactly one thing. `max_contest_positions: 1` is added to the
envelope, and the live book spec gains `contestcap=1`. Size, open cap, band,
price ceiling, entry timing, the 0c offset, the 4h timeout, hold-to-settlement,
the fee model, the twin cap and EVERY keep/stop threshold are the predecessor's,
unchanged. `max_event_rungs` STAYS AT 3 and is not swapped out: the contest cap
is the tighter bound, and removing the looser one would be a second change in a
step that is supposed to carry one.

WHAT THIS PACKAGE DELIBERATELY DOES NOT TOUCH: `MMSELL_CONTEST_CAP_ENABLED`, the
GLOBAL switch. `tracker.py` is shared by every mmsell book, so turning the global
on would re-scope `mmsell`, `mmsell5`-`10`, the `Tmmsell` family, `Lmmsell` and
the running `Gmmsell` control at one instant -- a shared-semantic change that
belongs to Platform Change Review, and under NEW_ONLY a contract change nobody
registered. The per-book `contestcap=` override in `mmsell_variants` wins over
the global pair (`tracker.py`: `cap_n = contest_cap if contest_cap is not None
else ...`), so this book opts in alone and no other book's selection moves. The
global default stays `false`. `LIVE_PAPER_TWIN_SUFFIX` stays `_pt4` for the same
reason: it is global, and changing it would orphan every other live book's twin
tag (the XOS-000011 shape).

THE PREREQUISITE THAT MAKES THE CAP REAL. `c4b2ce1` (PR #335) gave the contest
cap its own whole-open-book read, `repo.open_positions_contest_summary`, which is
deliberately NOT settlement-date scoped. Before that fix, an MLB game starting
after ~18:30 ET had its F5 legs before UTC midnight and its full-game legs after,
so they counted against two different days' budgets and the cap did not fire --
silently, because `skipped_contest_cap` simply stayed 0, which reads as "nothing
to refuse". Arming this envelope on code without that fix would ship a cap that
cannot bind on exactly the late games the drawdown came from. The operator
preflight confirms the deployed sha contains it.
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

PREDECESSOR_KEY = "mmsell-price-ceiling-capacity"
SUCCESSOR_KEY = "mmsell-price-ceiling-contest-cap"

ARM_KEY = "mmsell10"
#: The long-running paper control, handed over from the predecessor at the same
#: instant its PAPER deployment ends. History on it is WANTED -- it is the
#: control, not the canary -- and handing it over rather than dropping it is what
#: stops the tag losing its arm, the XOS-000011 blackout shape.
#:
#: `Gmmsell1` is deliberately NOT used here even though it is the contest-capped
#: paper book: it carries the active treatment arm of `mmsell-correlation-cap`,
#: a running paper experiment with a 60-settlement-day floor, and claiming its
#: tag would end that experiment to start this one. It keeps running, untouched,
#: as the paper prior this canary rests on.
PAPER_TAG = "mmsell10"
#: Fresh, per `arm_live_canary`'s no-inherited-state rule. Never reused. `E` is
#: this generation's marker (`C` was the ceiling canary's, `D` the capacity
#: successor's, `G` the paper contest-cap arms'). `LIVE_STRATEGIES` matches by
#: PREFIX, so a tag beginning `mmsell10` would be captured by an allowlist entry
#: naming the paper parent, and no existing tag is a prefix of these or prefixed
#: by them.
LIVE_TAG = "Emmsell10"
#: `LIVE_PAPER_TWIN_SUFFIX` is `_pt4` in production and this package does not
#: change it -- it is global, and moving it would orphan every other live book's
#: twin. So the twin tag is the live tag plus that suffix, derived, not chosen.
TWIN_TAG = "Emmsell10_pt4"

PAPER_DEPLOYMENT_KEY = "mmsell-contestcap-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-contestcap-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-contestcap-twin-1"

#: The predecessor's book spec plus `contestcap=1`, and nothing else. The twin is
#: built by the tracker as `dict(parent)` with only the tag replaced, so it
#: inherits the cap and mirrors live's decisions -- which is what an execution
#: control is for. The UNCAPPED comparison is `mmsell10` / `Gmmsell0`, not the
#: twin.
BASE_BOOK_PARAMS = "lo=5,hi=10,maxyes=7,size=1"
BOOK_PARAMS = f"{BASE_BOOK_PARAMS},contestcap=1"
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"

PROMOTION_GATE_KEY = "paper_to_live_canary"
KEEP_GATE_KEY = "live_canary_keep"

#: The predecessor's Stage-2 envelope with ONE addition. Every other value is
#: asserted equal to it by `tests/test_successor_mmsell10_contest_cap.py`, so a
#: second change smuggled in beside the correction fails CI rather than review.
RISK_ENVELOPE: dict = {
    "stage": "canary_contest_cap_stage_1",
    "contracts_per_order": 1,
    "max_order_dollars": 1.00,
    "max_market_exposure_usd": 1.00,
    # LEFT AT 3, deliberately. The contest cap is the TIGHTER bound, not a
    # replacement: `max_event_rungs` still binds per event ticker for anything
    # the contest key does not group, and removing it would be a second change.
    "max_event_rungs": 3,
    "max_event_exposure_usd": 3.00,
    # THE CORRECTION. One open position per unit of correlation, keyed by
    # `regimes.contest_key_of`: sports group ACROSS series so an MLB game's
    # TOTAL/TEAMTOTAL/SPREAD/HR share one budget; everywhere else the key falls
    # back to the event ticker, which tightens ladders from 3 rungs to 1. Both
    # halves come from the one knob and the second is where the measured effect
    # is. Delivered by the per-book `contestcap=1` override, NOT by the global.
    "max_contest_positions": 1,
    "max_open_positions": 40,
    "max_book_exposure_usd": 39.60,
    "max_events_per_settlement_date": 5,
    "settlement_date_concentration_pct": 25,
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
        # NOT settable: the contest cap rides in `MMSELL_VARIANTS` as this
        # book's own `contestcap=1`, inside the drift-checked `book_params`, so
        # a later edit to it is detected as EXPERIMENT_CONFIG_DRIFT rather than
        # applied silently. `MMSELL_CONTEST_CAP_ENABLED` (global) stays off and
        # is deliberately absent from this envelope and from the ops allowlist.
    },
    "live_tier_note": (
        "`mmsell_live_min_tier` defaults to `graduated` (PR #338) and is NOT in "
        "the ops allowlist, so this canary runs with that bar on and it cannot "
        "be turned off through the channel. It only ever REFUSES a live entry. "
        "It is NOT a substitute for this cap and does not bound this book's "
        "worst case: KXNFLSPREAD is IN the graduated manifest and has lost "
        "$166.55 over n=382. The contest cap is what limits how many correlated "
        "rungs one bad contest can take, and the tier bar is what limits which "
        "series are eligible at all; they are orthogonal."
    ),
    "shard_note": (
        "Kalshi shards its exchange by category and collateral is held per shard "
        "(XOS-000014). Shards 1-3 were funded 2026-08-31. The book cap of 40 "
        "spans shards; the contest cap makes a single-shard pile-up strictly "
        "less likely than under the predecessor, never more."
    ),
}

#: Carried from the predecessor VERBATIM. A successor that quietly relaxed its
#: own stops would defeat pre-registration entirely.
_GATES_CARRIED_FROM = "mmsell-price-ceiling v2 (via mmsell-price-ceiling-capacity)"

# The keep AND promotion specs are IMPORTED, not retyped, so "carried verbatim"
# is a property of the code rather than a claim in a docstring -- a loosened
# threshold is not a one-character edit away, it is impossible. `is`-identity is
# asserted in the tests.
from .canary_mmsell10 import KEEP_GATE_SPEC as KEEP_GATE_SPEC  # noqa: E402
from .canary_mmsell10 import PROMOTION_GATE_SPEC as PROMOTION_GATE_SPEC  # noqa: E402

#: Everything the runtime-allowlist step sets. Declared so CI asserts each name
#: clears `railway_env.ALLOWED_VARS`: a package whose activation the env channel
#: refuses halfway through leaves an operator with a write already submitted.
#: `MMSELL_VARIANTS` is named explicitly because the live book DOES NOT EXIST
#: until it has an entry there -- `LIVE_STRATEGIES=Emmsell10` alone would name a
#: book that does not exist, and `book_params[Emmsell10]` absent against a
#: declared value is recorded as EXPERIMENT_CONFIG_DRIFT, which takes the keep
#: gate to BLOCKED_INTEGRITY.
ACTIVATION_VARS: frozenset[str] = (
    frozenset(RISK_ENVELOPE["settings"]) | {"MMSELL_VARIANTS", "LIVE_STRATEGIES"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(
    session,
    *,
    actor: str,
    evidence_started_at: datetime | None = None,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Open the successor and walk it to PAPER. Arms nothing, places no orders.

    Registering a contract and putting real money behind it are two acts with two
    approvals; this is the first.

    WHAT IT ENDS, AND WHAT IT DELIBERATELY LEAVES RUNNING. Only the predecessor's
    PAPER deployment ends, because the successor's paper control needs the
    `mmsell10` tag and two active deployment arms on one tag is AMBIGUOUS and
    refused outright by the resolver. It is replaced at the same instant, exactly
    as the capacity successor's own handover did. The predecessor's LIVE and TWIN
    deployments are LEFT OPEN: ending a live deployment leaves its tag without an
    arm, so its settlements could not be RECORDED and the predecessor's final
    evidence would be wrong. Keeping them open costs nothing and lets the old
    book wind down beside the new one on entirely separate tags and epochs.

    `promotion_sample_floor` IS REFUSED IF SET. The transport always passes the
    field, so the signature must accept it, but accepting a VALUE would author a
    promotion criterion this package has no business authoring: the bar is the
    predecessor's, unfloored, and the cap is a bound rather than a candidate.

    `evidence_started_at` defaults to registration time and cannot usefully be
    earlier: the evaluator floors every window at `max(epoch.started_at,
    gate.evidence_started_at)` and the successor's epoch opens here. Evidence
    cannot precede the epoch it is attributed to.

    THE CONSEQUENCE, PLANNED FOR RATHER THAN DISCOVERED: registering does not
    make the canary armable. `arm_live_canary` re-evaluates the promotion gate
    synchronously, and immediately after registration `realizable_cents_per_trade`
    is undefined ("no settled trades in window"), so arming is REFUSED. The
    successor earns its own paper evidence in its own window first. Register,
    wait for `mmsell10` to settle trades inside it, then arm.
    """
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "this package registers the predecessor's promotion bar UNCHANGED "
            "and authors no criterion of its own — the contest cap is a risk "
            f"BOUND, not a candidate. Drop promotion_sample_floor="
            f"{promotion_sample_floor!r} from the envelope."
        )
    at = now or _now()
    predecessor = get_experiment(session, PREDECESSOR_KEY)
    if predecessor is None:
        raise service.ExperimentOsError(f"experiment {PREDECESSOR_KEY!r} not found")
    if get_experiment(session, SUCCESSOR_KEY) is not None:
        raise service.ExperimentOsError(
            f"{SUCCESSOR_KEY} already exists — this package refuses rather than "
            "re-running, so a repeated command cannot fork the lineage"
        )

    from ..repository import count_live_book_open

    open_deps = [
        d for d in session.scalars(
            select(ExperimentDeployment).where(ExperimentDeployment.ended_at.is_(None))
        ).all()
        if _epoch_experiment_id(session, d) == predecessor.id
    ]
    ending = [d for d in open_deps if d.kind == "paper"]
    draining = [d for d in open_deps if d.kind != "paper"]

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

    successor = service.create_experiment(
        session, key=SUCCESSOR_KEY, origin="operator",
        title="mmsell10 — the price-ceiling book with the contest bound corrected",
        family="maker",
        hypothesis=(
            "The cheap-cell price-ceiling edge (lo=5, hi=10, maxyes=7) is "
            "unchanged; what changes is the concentration BOUND around it. "
            "`max_event_rungs: 3` counts event_ticker, which is series x "
            "contest, so one game can legally carry ~15 correlated positions. "
            "Capping open positions per unit of CORRELATION at 1 is the "
            "correction to that bound."
        ),
        mechanism=(
            "`regimes.contest_key_of` groups sports across series and falls back "
            "to the event ticker everywhere else, so `contestcap=1` both merges a "
            "game's listings into one budget AND tightens every other ladder from "
            "3 rungs to 1. The paper replay says the second half carries almost "
            "all of the measured effect; both arrive from the one knob."
        ),
        falsification=(
            "A concentration bound is falsified by being unnecessary or by being "
            "harmful: `skipped_contest_cap` never fires on real money while an "
            "uncapped book holds 2+ positions on the same contest (the cap is "
            "broken), or the keep gate's own stops trip. Nothing here is gated on "
            "the cap out-earning its predecessor — it is a limit, not a strategy."
        ),
        predecessor=predecessor,
        docs={"thesis": "docs/MMSELL_CORRELATION_CAP.md",
              "canary": "docs/MMSELL_CONTEST_CAP_CANARY.md",
              "studies": ["docs/MMSELL10_CANARY_PLAN.md",
                          "docs/LIVE_PAPER_TWIN.md"]},
        actor=actor, now=at,
    )

    version = service.create_experiment_version(
        session, successor,
        hypothesis=(
            "The cheap-cell price-ceiling edge (lo=5, hi=10, maxyes=7) survives "
            "with open positions bounded at ONE per unit of correlation. "
            "Unchanged from the predecessor as a question about the EDGE — this "
            "successor corrects a concentration bound, and a bound is not a "
            "treatment."
        ),
        universe_selector=(
            "cheap band lo=5,hi=10 with an entry-price ceiling maxyes=7 — the "
            "predecessor's universe verbatim. No market class is excluded here; "
            "`mmsell_live_min_tier=graduated` narrows the LIVE mirror to reviewed "
            "series and is a platform default, not this version's selector."
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule="one contract per order under a $1.00 per-order dollar cap",
        execution_style="maker",
        control_required=False,
        control_exemption_reason=(
            "gated on absolute realizable per-trade via the live-calibrated fill "
            "model, as v1, v2 and the capacity successor were; the execution "
            "control is the registered paper TWIN, armed at the same instant, "
            "not a second live arm. The UNCAPPED comparison is the running "
            "`mmsell10` paper control and `Gmmsell0`, on paper, where it costs "
            "no real money"
        ),
        independent_variable=(
            "open positions per unit of correlation (event_ticker at 3 rungs -> "
            "contest_key_of at 1) — a RISK BOUND, not an edge parameter"
        ),
        held_constant=[
            "arm parameters (lo=5, hi=10, maxyes=7) and the 1-contract clip",
            "market universe — no market class is excluded by this version",
            "entry timing and the 0c price offset",
            "the 4h order timeout",
            "hold-to-settlement exits (no TP/SL)",
            "the fee model",
            "the open-position cap (40) and the twin cap (250)",
            "max_event_rungs stays at 3 — the contest cap is the tighter bound, "
            "not a swap",
            "every keep/stop threshold, and the promotion bar, which are the "
            "predecessor's own frozen objects",
            "the GLOBAL mmsell_contest_cap_enabled stays false for the life of "
            "this version; this book opts in through its own registered envelope "
            "so no other book's selection moves",
        ],
        risk=RISK_ENVELOPE,
        docs={"canary": "docs/MMSELL_CONTEST_CAP_CANARY.md"},
        change_reason=(
            "Adds `max_contest_positions: 1` to the predecessor's Stage-2 "
            "envelope and `contestcap=1` to the live book spec. Nothing else "
            "moves: same arm, same universe, same sizing, same open cap, same "
            "twin cap, same loss budgets, same gates. `max_event_rungs` stays at "
            "3 rather than being replaced, because the contest cap is the tighter "
            "bound and swapping it out would be a second change."
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="entry-price ceiling only (lo=5,hi=10,maxyes=7) — unchanged; "
                    "the contest cap is an envelope bound, not an arm parameter",
        params={"lo": 5, "hi": 10, "maxyes": 7}, strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=PROMOTION_GATE_SPEC,
        from_state=LifecycleState.PAPER, to_state=LifecycleState.LIVE_CANARY,
        registered_at=at,
        notes=f"the predecessor's bar, unchanged and unfloored, from "
              f"{_GATES_CARRIED_FROM}",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes=f"carried verbatim from {_GATES_CARRIED_FROM}",
    )
    service.freeze_version(session, version, now=at)
    evidence_at = evidence_started_at or at
    service.mark_gate_evidence_started(session, promotion_gate, at=evidence_at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)

    service.transition_experiment(session, successor, LifecycleState.PROBE,
                                  actor=actor, occurred_at=at,
                                  reason="contract registered; no probe stage is "
                                         "needed, the predecessor's is inherited "
                                         "and the cap's paper prior is carried by "
                                         "the running mmsell-correlation-cap arms")
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
        notes=("the mmsell10 paper control, handed over from the predecessor at "
               "this same instant so the tag never loses its arm"),
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
    """The parameters a drift check compares the running book against.

    `book_spec` carries `contestcap=1`, so the cap is inside the drift-checked
    `book_params`: editing it out of `MMSELL_VARIANTS` while the canary runs is
    recorded as EXPERIMENT_CONFIG_DRIFT and takes the keep gate to
    BLOCKED_INTEGRITY, rather than silently removing a real-money bound.
    """
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

    THIS EXPANDS REAL-MONEY CAPABILITY — up to $39.60 of book ceiling, bounded
    per contest at one clip. It still places no order by itself; the runtime
    allowlist (`LIVE_STRATEGIES`) is a separate switch and a separate act.
    `approved_by` records the operator who authorized it, and the service refuses
    without it.
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
            f"mmsell10 contest-cap canary armed on {LIVE_TAG} with twin "
            f"{TWIN_TAG} at one boundary; the predecessor's envelope with "
            f"max_contest_positions=1 added, pre-registered on v{version.version}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


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
    "ACTIVATION_VARS", "ARM_KEY", "BASE_BOOK_PARAMS", "BOOK_PARAMS",
    "KEEP_GATE_KEY", "KEEP_GATE_SPEC", "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY",
    "LIVE_TAG", "PAPER_DEPLOYMENT_KEY", "PAPER_TAG", "PREDECESSOR_KEY",
    "PROMOTION_GATE_KEY", "PROMOTION_GATE_SPEC", "RISK_ENVELOPE",
    "SUCCESSOR_KEY", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG", "arm",
    "material_config", "register",
]
