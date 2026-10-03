"""The mmsell10 SIZE-SPLIT successor — the contest-cap book, a randomized 1-vs-3 clip, and
the contest KEY corrected. Live tag `Hmmsell10`, twin `Hmmsell10_pt4`. Plan:
`docs/MMSELL_SIZE_SPLIT_CANARY.md`. Evidence: `docs/MMSELL_REPLAY_PROBES_20261003.md`.

WHAT CHANGES AGAINST THE PREDECESSOR (`mmsell-price-ceiling-contest-cap`), and nothing else:

  1. THE TREATMENT — contracts per order. The live book spec carries `sizes=1+3`: every
     ticker hashes (`live/sizing.py::ticker_size`) to ONE contract or THREE, for its whole
     life. One book, one contest cap, one twin, one paper control; the split is a coin flip per
     market, so the two arms share the regime, the universe and the scan instant. This is the
     operator's lever (2026-10-03: "to get to $100 a month we have to up position size"), and
     the canary measures what it actually does: whether the 2nd and 3rd contracts fill, what
     they earn per contract, and what a 3-lot loss does to the envelope.
     The prior (outcome-blind until 10-03, then read): on 401 Fmmsell10 fills matched to the
     trade tape, fills from takers of >= 3 contracts earned +3.23c (n=246) against -3.90c for
     1-2 lot takers (n=63) — the extra contracts of a 3-lot fill only on the larger sweeps, and
     those were not adversely selected. One window; it sets the prior and decides nothing.

  2. A BOUND CORRECTED — the contest KEY. `contestkey=split` makes the contest cap count a
     subject-split series (KXRAIN cities, KXTRUMPSAY / KXFEDMENTION words) one contest per
     MARKET instead of one per date. The shipped key refused entries sharing NO outcome: on
     Fmmsell10 9/7-9/30 it refused 125 distinct KXRAIN markets against 23 opened, on series
     whose 93 live fills across nine books lost nothing. Like `contestcap=1` before it, this is
     a correction to a bound, not a candidate: no gate clause references it. `max_event_rungs`
     stays at 3 and still binds per event ticker, so a KXRAIN day can hold at most 3 cities.

  3. THE ENVELOPE, restated where a 3-lot makes the old number wrong: $3.00 per order and per
     market, a $30 canary loss budget, and the keep gate's per-market loss bound at one 3-lot
     clip ($3.00). Every other keep/stop clause is the predecessor's, asserted equal in CI.

WHAT DOES NOT CHANGE: band, ceiling (lo=5,hi=10,maxyes=7), `contestcap=1`, the 0c offset, the
4h timeout, hold-to-settlement, the 40-open cap, the twin cap, the tier bar, the fee model, and
the PROMOTION gate, which is the predecessor's own frozen object (the edge question is
unchanged: the promotion bar is about the paper control's realizable edge, not the clip).

WHY A SUCCESSOR, again: `arm_live_canary` requires PAPER and LIVE_CANARY -> PAPER is illegal,
so a LIVE_CANARY experiment has no sanctioned way to re-arm with fresh tags and a new envelope.

THE OLD CANARY. Registering ends only the predecessor's PAPER deployment, handing the
`mmsell10` control tag over at one instant. Its live (`Fmmsell10`) and twin deployments are
LEFT OPEN so every open position settles with its evidence recorded. `Fmmsell10` is shut down
the way `Cmmsell10` and `Dmmsell10` were: removed from `LIVE_STRATEGIES` (no new entries) and
left in `MMSELL_VARIANTS`, which the control tower then reports as
EXPERIMENT_EXECUTION_STOOD_DOWN rather than drift.

GLOBALS, NAMED (live-paper-parallel §3b). `LIVE_MAX_ORDER_DOLLARS` is process-wide; it moves
$1.00 -> $3.00 so a 3-lot is not floored to one contract. Today it is read only by the mmsell
live mirror (theta and the incentive book carry their own knobs; weather is not live), and
every mmsell book other than this one declares `size=1` or is not in `LIVE_STRATEGIES`.
`MAX_TOTAL_EXPOSURE` is portfolio-wide and shared with `Alimm1`; it moves $100 -> $125 so the
breaker does not throttle this book's expected ~$55 footprint behind `Alimm1`'s ~$40 and the
draining `Fmmsell10` positions. Both are hard-stop writes. `MAX_MARKET_EXPOSURE` and
`MAX_DAILY_LOSS` are NOT set here: production holds 25 / 50, not the 1.0 / 5.0 the
predecessor's envelope declares, both are shared with `Alimm1`, and this package neither
re-asserts the predecessor's numbers nor changes the running ones.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, service
from .canary_mmsell10 import KEEP_GATE_SPEC as _PREDECESSOR_KEEP
from .canary_mmsell10 import PROMOTION_GATE_SPEC as PROMOTION_GATE_SPEC  # noqa: F401
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentDeployment, ExperimentGate
from .read import get_experiment
from .successor_mmsell10_contest_cap import RISK_ENVELOPE as _PREDECESSOR_ENVELOPE
from .successor_mmsell10_contest_cap import _epoch_experiment_id, _tags_of

PREDECESSOR_KEY = "mmsell-price-ceiling-contest-cap"
SUCCESSOR_KEY = "mmsell-price-ceiling-size-split"

#: Same arm key as every predecessor, so the imported promotion gate addresses it unchanged.
ARM_KEY = "mmsell10"
#: The long-running paper control, handed over at the instant the predecessor's PAPER
#: deployment ends (the XOS-000011 blackout shape is a tag losing its arm).
PAPER_TAG = "mmsell10"
#: Fresh, per `arm_live_canary`'s no-inherited-state rule. `H` is this generation's marker
#: (C ceiling, D capacity, E/F contest cap; G is skipped because `Gmmsell1` would prefix
#: `Gmmsell10` and LIVE_STRATEGIES matches by PREFIX). No existing tag prefixes these or is
#: prefixed by them; `scripts/mmsell_queue_cancel_baseline.py` already anticipates the name.
LIVE_TAG = "Hmmsell10"
#: Derived from the global `LIVE_PAPER_TWIN_SUFFIX` (`_pt4`), never chosen against it.
TWIN_TAG = "Hmmsell10_pt4"

PAPER_DEPLOYMENT_KEY = "mmsell-sizesplit-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-sizesplit-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-sizesplit-twin-1"

#: The predecessor's book (`lo=5,hi=10,maxyes=7,size=1,contestcap=1`) with `size=1` replaced
#: by the split and the contest key corrected. The parser refuses `size` and `sizes` together.
BOOK_PARAMS = "lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,sizes=1+3"
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"
SIZES = (1, 3)

PROMOTION_GATE_KEY = "paper_to_live_canary"
KEEP_GATE_KEY = "live_canary_keep"

#: The predecessor's envelope with exactly the keys a 3-lot makes wrong restated, plus the key
#: correction. `tests/test_successor_mmsell10_size_split.py` asserts every other key is equal.
RISK_ENVELOPE: dict = copy.deepcopy(_PREDECESSOR_ENVELOPE)
RISK_ENVELOPE.update({
    "stage": "canary_size_split_stage_1",
    "contracts_per_order": "1 or 3 — fixed per ticker by sha256 hash (sizes=1+3), ~50/50",
    "max_order_dollars": 3.00,
    "max_market_exposure_usd": 3.00,
    # Three rungs per event ticker at up to a 3-lot each. The contest cap (1) still binds
    # first everywhere except a subject-split series, where each market is its own contest.
    "max_event_exposure_usd": 9.00,
    "contest_key": "split — a subject-split series counts one contest per MARKET "
                   "(regimes.SUBJECT_SPLIT_SERIES); every other series is unchanged",
    # 40 open at the expected 50/50 mix and ~94c: 20 x 0.94 + 20 x 2.82 = ~$75; all-3-lot
    # worst case 40 x 2.91 = $116.40. MAX_TOTAL_EXPOSURE (portfolio) is the hard bound.
    "max_book_exposure_usd": 116.40,
    "expected_book_exposure_usd": 75.20,
    "total_canary_loss_budget_usd": 30.00,
    "settings": {
        **{k: v for k, v in _PREDECESSOR_ENVELOPE["settings"].items()
           if k not in ("MAX_MARKET_EXPOSURE", "MAX_DAILY_LOSS")},
        "LIVE_MAX_ORDER_DOLLARS": "3.0",
        "MAX_TOTAL_EXPOSURE": "125",
    },
    "portfolio_note": (
        "MAX_MARKET_EXPOSURE and MAX_DAILY_LOSS are portfolio-wide and shared with Alimm1. "
        "Production holds 25 / 50 (read 2026-10-03, ops rl-cn-env-1), not the 1.0 / 5.0 the "
        "predecessor's envelope declares; this package does not set either. The book's own "
        "loss stop is the keep gate's live_realized_pnl_usd <= -30 clause. MAX_DAILY_LOSS "
        "was raised 5 -> 25 -> 50 by explicit operator decisions for the incentive book "
        "(liquidity_incentive_mm `left_alone`); re-asserting 5.0 here would undo them."
    ),
})

#: The predecessor's keep/stop contract with exactly two thresholds restated for a 3-lot:
#: the canary loss budget (-15 -> -30: about twice the contracts per settled market) and the
#: single-market loss bound (1.0 -> 3.0: one 3-lot clip; at 1.0 the FIRST 3-lot loss would read
#: as "envelope not applied" and stop a canary that is working as designed). Everything else
#: — sample floor, horizon, twin-gap stops, win-rate stop, hold clauses, pass clauses — is the
#: predecessor's, asserted equal by the tests.
KEEP_GATE_SPEC: dict = copy.deepcopy(_PREDECESSOR_KEEP)
KEEP_GATE_SPEC["description"] = (
    "Keep/stop for the mmsell10 size-split canary (Hmmsell10). The predecessor's contract with "
    "the canary loss budget and the per-market loss bound restated for a 3-contract clip. "
    "Every clause addresses deployment_kind='live'; twin comparisons resolve through the "
    "registered twin_of link."
)
RESTATED_KEEP_THRESHOLDS = {"live_realized_pnl_usd": -30.0, "live_max_realized_loss_usd": 3.0}
for _clause in KEEP_GATE_SPEC["fail_any"]:
    if _clause["metric"] in RESTATED_KEEP_THRESHOLDS:
        _clause["value"] = RESTATED_KEEP_THRESHOLDS[_clause["metric"]]

#: Everything the runtime step sets. `MMSELL_VARIANTS` creates the book; `LIVE_STRATEGIES`
#: both arms it and stands `Fmmsell10` down (the new value names Hmmsell10 and Alimm1 only).
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

    Ends ONLY the predecessor's PAPER deployment (to hand `mmsell10` over); its live and twin
    deployments stay open so `Fmmsell10` drains with every settlement recorded.

    `promotion_sample_floor` is REFUSED if set: the promotion bar is the predecessor's,
    unfloored. Registering does not make the canary armable — `arm_live_canary` re-evaluates
    the promotion gate synchronously, so `mmsell10` must settle paper trades inside this
    successor's window first (about a day at its flow)."""
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "this package registers the predecessor's promotion bar UNCHANGED — the size "
            "split is measured on live fills, not promoted on paper. Drop "
            f"promotion_sample_floor={promotion_sample_floor!r} from the envelope."
        )
    at = now or _now()
    predecessor = get_experiment(session, PREDECESSOR_KEY)
    if predecessor is None:
        raise service.ExperimentOsError(f"experiment {PREDECESSOR_KEY!r} not found")
    if get_experiment(session, SUCCESSOR_KEY) is not None:
        raise service.ExperimentOsError(
            f"{SUCCESSOR_KEY} already exists — this package refuses rather than re-running, "
            "so a repeated command cannot fork the lineage"
        )

    from ..repository import count_live_book_open

    open_deps = [
        d for d in session.scalars(
            select(ExperimentDeployment).where(ExperimentDeployment.ended_at.is_(None))
        ).all()
        if _epoch_experiment_id(session, d) == predecessor.id
    ]
    ending, left_open = service.select_handover_deployments(
        open_deps, taking_over={PAPER_TAG}, tags_of=lambda d: _tags_of(session, d),
    )
    draining = [d for d in open_deps if d.kind != "paper"]
    for dep in ending:
        for tag in _tags_of(session, dep):
            held = count_live_book_open(session, tag)
            if held:
                raise service.ExperimentOsError(
                    f"{tag} still holds {held} open live position(s) and its deployment is "
                    "about to end — its settlements could not be RECORDED. Stand it down and "
                    "let it drain first."
                )
    for dep in ending:
        service.end_deployment(session, dep, ended_at=at)

    successor = service.create_experiment(
        session, key=SUCCESSOR_KEY, origin="operator",
        title="mmsell10 — the contest-cap book at a randomized 1-vs-3 contract clip",
        family="maker",
        hypothesis=(
            "The cheap-cell price-ceiling edge (lo=5, hi=10, maxyes=7) scales with the clip: "
            "the 2nd and 3rd contracts of a 3-lot fill on the same events as the first and "
            "earn at least as much per contract, so dollars per settled market roughly triple "
            "on the 3-lot half without the extra contracts being adversely selected."
        ),
        mechanism=(
            "The takers that fill this book sweep a median ~59 contracts; a 3-lot's extra "
            "contracts fill only on sweeps >= 2-3 contracts, which on the 10-03 tape read "
            "were the BETTER fills (+3.23c vs -3.90c for 1-2 lot takers). Separately, the "
            "contest key is corrected so subject-split series are not refused as one contest "
            "per date."
        ),
        falsification=(
            "Per-contract realized on the 3-lot arm falls more than 1.0c below the 1-lot arm "
            "at the pre-registered floors, or 3-lots fill partially (< 70% fully filled given "
            "any fill), or the restated keep gate's stops trip."
        ),
        predecessor=predecessor,
        docs={"canary": "docs/MMSELL_SIZE_SPLIT_CANARY.md",
              "evidence": "docs/MMSELL_REPLAY_PROBES_20261003.md",
              "studies": ["docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md",
                          "docs/LIVE_PAPER_TWIN.md"]},
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, successor,
        hypothesis=(
            "Same edge question as the predecessor; the clip is randomized per ticker between "
            "1 and 3 contracts so the size effect is measured inside one book, one regime and "
            "one scan."
        ),
        universe_selector=(
            "cheap band lo=5,hi=10 with an entry-price ceiling maxyes=7 — the predecessor's "
            "universe verbatim; `mmsell_live_min_tier=graduated` is a platform default"
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule=(
            "1 or 3 contracts per order, fixed per ticker by sha256(salt + ':sizes:' + ticker) "
            "% 2, under a $3.00 per-order dollar cap"
        ),
        execution_style="maker",
        control_required=False,
        control_exemption_reason=(
            "the size split is randomized INSIDE the one live book: the 1-contract half is "
            "the concurrent control for the 3-contract half, recomputable from the ticker "
            "alone (live/sizing.py::ticker_size). The execution control is the registered "
            "paper TWIN, armed at the same instant; the paper control is mmsell10."
        ),
        independent_variable=(
            "contracts per order (1 vs 3, per-ticker hash). The contest-key correction "
            "(shipped -> split) is a bound, attributed by series membership in "
            "regimes.SUBJECT_SPLIT_SERIES, not an arm"
        ),
        held_constant=[
            "arm parameters (lo=5, hi=10, maxyes=7)",
            "market universe and the tier bar",
            "entry timing and the 0c price offset",
            "the 4h order timeout",
            "hold-to-settlement exits (no TP/SL)",
            "the fee model",
            "contestcap=1 and max_event_rungs=3",
            "the open-position cap (40) and the twin cap (250)",
            "the promotion bar, which is the predecessor's own frozen object",
            "every keep/stop clause except the two thresholds restated for a 3-lot",
        ],
        risk=RISK_ENVELOPE,
        docs={"canary": "docs/MMSELL_SIZE_SPLIT_CANARY.md"},
        change_reason=(
            "Replaces size=1 with a randomized sizes=1+3 split, corrects the contest key to "
            "split, raises the per-order and per-market caps to one 3-lot ($3.00), and "
            "restates the canary loss budget (-$30) and the per-market loss bound ($3.00) for "
            "that clip. Operator direction 2026-10-03."
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="entry-price ceiling (lo=5,hi=10,maxyes=7); the 1-vs-3 clip is a "
                    "per-ticker split inside the live book, not a separate arm",
        params={"lo": 5, "hi": 10, "maxyes": 7, "sizes": list(SIZES)},
        strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=PROMOTION_GATE_SPEC,
        from_state=LifecycleState.PAPER, to_state=LifecycleState.LIVE_CANARY,
        registered_at=at,
        notes="the predecessor's bar, unchanged and unfloored",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes="the predecessor's contract; loss budget -30 and per-market bound 3.0 "
              "restated for the 3-contract clip",
    )
    service.freeze_version(session, version, now=at)
    evidence_at = evidence_started_at or at
    service.mark_gate_evidence_started(session, promotion_gate, at=evidence_at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)
    service.transition_experiment(session, successor, LifecycleState.PROBE,
                                  actor=actor, occurred_at=at,
                                  reason="contract registered; the predecessor's probe stage "
                                         "is inherited and the size prior is the 10-03 "
                                         "replay (docs/MMSELL_REPLAY_PROBES_20261003.md)")
    service.transition_experiment(session, successor, LifecycleState.PAPER,
                                  actor=actor, occurred_at=at,
                                  reason="paper control continues on mmsell10")
    epoch = service.open_epoch(
        session, version,
        reason="successor's first operating interval, pinned to the snapshot active at "
               "registration",
        started_at=at,
    )
    paper = service.register_deployment(
        session, epoch, deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER, kind="paper",
        arms={ARM_KEY: PAPER_TAG}, started_at=at,
        notes="the mmsell10 paper control, handed over from the predecessor at this instant",
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
        "left_open_deployments": [d.deployment_key for d in left_open],
        "still_draining": [d.deployment_key for d in draining],
        "registered_at": at,
        "evidence_started_at": evidence_at,
    }


def material_config() -> dict:
    """The drift baseline: `book_params` carries `sizes=1+3` and `contestkey=split`, so editing
    either out of MMSELL_VARIANTS mid-canary is EXPERIMENT_CONFIG_DRIFT, not a silent change."""
    return {
        "material": enforcement.live_material_block(
            books={LIVE_TAG: (TWIN_TAG, BOOK_PARAMS)},
        ),
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
    """Arm the successor's canary through `service.arm_live_canary`, the one sanctioned path.

    THIS EXPANDS REAL-MONEY CAPABILITY: up to $3.00 per order and ~$75 of expected book
    footprint (worst case $116.40, bounded by MAX_TOTAL_EXPOSURE). It still places no order by
    itself; `MMSELL_VARIANTS` + `LIVE_STRATEGIES` are a separate act."""
    from .read import latest_version

    experiment = get_experiment(session, SUCCESSOR_KEY)
    if experiment is None:
        raise service.ExperimentOsError(f"{SUCCESSOR_KEY} does not exist — REGISTER_PACKAGE "
                                        "first")
    version = latest_version(session, experiment)
    if version is None:
        raise service.ExperimentOsError(f"{SUCCESSOR_KEY} has no version — REGISTER_PACKAGE "
                                        "first")
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
            f"mmsell10 size-split canary armed on {LIVE_TAG} with twin {TWIN_TAG} at one "
            f"boundary; sizes=1+3 per ticker, contestkey=split, pre-registered on "
            f"v{version.version}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "KEEP_GATE_KEY", "KEEP_GATE_SPEC",
    "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG", "PAPER_DEPLOYMENT_KEY", "PAPER_TAG",
    "PREDECESSOR_KEY", "PROMOTION_GATE_KEY", "PROMOTION_GATE_SPEC", "RESTATED_KEEP_THRESHOLDS",
    "RISK_ENVELOPE", "SIZES", "SUCCESSOR_KEY", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG", "arm",
    "material_config", "register",
]
