"""The mmsell10 cell-size book restarted WITHOUT politics — successor to
`mmsell-price-ceiling-cell-size-2` (live `Xmmsell10`).

Owner decision 2026-11-10 (session 01D3m7Af5FnpLDG5eDQ6JJRL), from the fifth 72h scorecard
(`docs/mmsell/scorecards/2026-11-10.md`): politics (KXTRUMPSAY, KXTRUTHSOCIAL) lost about one
market in five across every mmsell book's own settled markets (K, N on EPST; X, Y on UFO), far
past the ~1 in 14 a 7c win against a 93c loss allows. Drop it from both live books; keep the two
books sharing markets under the same pair bound (owner chose "politics only", not the split).

WHAT CHANGES AGAINST THE PREDECESSOR, and nothing else:

  1. THE UNIVERSE. `only=` loses the politics tokens KXTRUMP and KXTRUTHSOCIAL.
     KXTRUMPMENTION stays: it carries the MENTION token, and the scorecard files it under
     mentions.
  2. FRESH TAGS: live `Ummsell10`, twin `Ummsell10_pt4`, paper `Immsell10`.
  3. THE SHARED PAIR. `Ommsell10+Ummsell10:6:6.00`, with `mmsell_win_set_4`. The same bound.

Unchanged: band, ceiling, the slow-cell sizing rule, contest semantics, the promotion bar, the
keep gate (loss budget -$30, $3.00 per market) and every dollar bound in the envelope.

TAGS, fresh and prefix-safe against every production tag (ops `tags-1110`, `tags3-1110`: no
paper trade and no Experiment OS arm has a tag starting with I, M, O or U).

THE OLD BOOK. Registering ends nothing. `Xmmsell10` keeps trading until the arming env act
takes it out of `LIVE_STRATEGIES`; its live and twin deployments stay open so it drains on the
record, and its `MMSELL_VARIANTS` entry STAYS (a stood-down book's params are compared in full).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, service
from . import successor_mmsell10_cell_size as cs1
from . import successor_mmsell10_cell_size_2 as pred
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentGate
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-price-ceiling-cell-size-3"
PREDECESSOR_KEY = pred.EXPERIMENT_KEY

MONITOR_EXPECT = pred.MONITOR_EXPECT

ARM_KEY = pred.ARM_KEY
PAPER_TAG = "Immsell10"
LIVE_TAG = "Ummsell10"
TWIN_TAG = "Ummsell10_pt4"
#: The WIN-set successor this book shares markets with (`mmsell_win_set_4.LIVE_TAG`).
PARTNER_LIVE_TAG = "Ommsell10"

PAPER_DEPLOYMENT_KEY = "mmsell-cellsize3-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-cellsize3-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-cellsize3-twin-1"

DROPPED_CATEGORIES = ("politics",)
#: Kept categories (owner decision 2026-11-10), as series substrings.
KEPT_CATEGORIES: dict[str, tuple[str, ...]] = {
    k: v for k, v in pred.KEPT_CATEGORIES.items() if k not in DROPPED_CATEGORIES
}
ONLY_TOKENS: tuple[str, ...] = tuple(t for toks in KEPT_CATEGORIES.values() for t in toks)

BOOK_PARAMS = (f"{pred.BOOK_PARAMS.split(',only=')[0]},only={'+'.join(ONLY_TOKENS)}")
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"
PAPER_BOOK_SPEC = f"{PAPER_TAG}:{BOOK_PARAMS}"

COMBINED_MAX_CONTRACTS = pred.COMBINED_MAX_CONTRACTS
COMBINED_MAX_USD = pred.COMBINED_MAX_USD
SHARED_PAIR_ENV = (f"{'+'.join(sorted((LIVE_TAG, PARTNER_LIVE_TAG)))}:"
                   f"{COMBINED_MAX_CONTRACTS}:{COMBINED_MAX_USD:.2f}")

PROMOTION_GATE_KEY = pred.PROMOTION_GATE_KEY
KEEP_GATE_KEY = pred.KEEP_GATE_KEY
#: The predecessor's bar, the object itself. Read on `Immsell10`, so it needs about a day of
#: settled paper.
PROMOTION_GATE_SPEC: dict = pred.PROMOTION_GATE_SPEC
KEEP_GATE_SPEC: dict = copy.deepcopy(pred.KEEP_GATE_SPEC)
KEEP_GATE_SPEC["description"] = (
    "Keep/stop for the mmsell10 cell-size canary restarted on weather, index and mentions "
    "(Ummsell10): the predecessor's contract unchanged — loss budget -$30, per-market loss "
    "bound $3.00 (one 3-lot clip). Every clause addresses deployment_kind='live'; twin "
    "comparisons resolve through the registered twin_of link."
)

RISK_ENVELOPE: dict = copy.deepcopy(pred.RISK_ENVELOPE)
RISK_ENVELOPE["stage"] = "canary_cell_size_stage_3"
RISK_ENVELOPE["universe"] = (
    f"series containing any of {list(ONLY_TOKENS)} (weather, index, mentions), graduated; "
    "politics, sports and every other category excluded"
)
RISK_ENVELOPE["shared_market"] = {
    **pred.RISK_ENVELOPE["shared_market"],
    "partner_tag": PARTNER_LIVE_TAG,
    "partner_experiment": "mmsell-win-set-4",
    "env": SHARED_PAIR_ENV,
    "queue_priority": f"{LIVE_TAG} precedes {PARTNER_LIVE_TAG} in MMSELL_VARIANTS",
}
RISK_ENVELOPE["settings"] = {**pred.RISK_ENVELOPE["settings"],
                             "MMSELL_LIVE_SHARED_MARKET_PAIRS": SHARED_PAIR_ENV}

ACTIVATION_VARS: frozenset[str] = (
    frozenset(RISK_ENVELOPE["settings"]) | {"MMSELL_VARIANTS", "LIVE_STRATEGIES"}
)

EVIDENCE_DOC = "docs/mmsell/scorecards/2026-11-10.md"


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
    """Create the successor and walk it to PAPER on `Immsell10`. Arms nothing, places no
    order, ends no deployment — the predecessor keeps trading until the env act swaps it."""
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "this package registers the predecessor's promotion bar UNCHANGED — drop "
            f"promotion_sample_floor={promotion_sample_floor!r} from the envelope."
        )
    at = now or _now()
    predecessor = get_experiment(session, PREDECESSOR_KEY)
    if predecessor is None:
        raise service.ExperimentOsError(f"experiment {PREDECESSOR_KEY!r} not found")
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"{EXPERIMENT_KEY} already exists — this package refuses rather than re-running"
        )
    experiment = service.create_experiment(
        session, key=EXPERIMENT_KEY, origin="operator",
        title="mmsell10 cell-size book on weather, index and mentions only",
        family="maker",
        hypothesis=(
            "The predecessor's question without politics: the cheap-cell price-ceiling rule, "
            "sized 3 on the slow-information cell and 1 elsewhere, earns a positive live "
            "per-contract return on weather, index and mention series."
        ),
        mechanism=(
            "Weather, index closes and mention words resolve on facts revealed late, where a "
            "resting NO at the ceiling is rarely picked off. The weekly Trump-says sets did "
            "not: a word in the set is said often enough that the 93-94c NO is fairly priced "
            "or worse."
        ),
        falsification=(
            "The keep gate's stops trip, or at its sample floor the live per-contract realized "
            "return is <= 0, or the twin win-rate gap exceeds the bound."
        ),
        predecessor=predecessor,
        docs={"evidence": EVIDENCE_DOC,
              "predecessor_plan": "docs/MMSELL_CELL_SIZE_CANARY.md",
              "canary": "docs/MMSELL_WIN_SET_CANARY.md"},
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis="The cell-size rule restricted to weather, index and mentions is "
                   "live-positive.",
        universe_selector=(
            f"series containing any of {', '.join(ONLY_TOKENS)} (graduated); cheap band "
            "lo=5,hi=10, entry-price ceiling maxyes=7"
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule=(
            f"{cs1.BIG_CONTRACTS} contracts on the slow-information cell "
            f"({', '.join(cs1.SLOW_CELL_TYPES)}), {cs1.REST_CONTRACTS} otherwise; "
            "under a $3.00 per-order dollar cap"
        ),
        execution_style="maker",
        control_required=False,
        control_exemption_reason=(
            "the paper control of the same rule and universe is Immsell10 (this experiment's "
            "paper arm); the execution control is the registered paper TWIN, armed at the "
            "same instant"
        ),
        independent_variable="market universe: politics removed (weather, index, mentions)",
        held_constant=[
            "band and ceiling (lo=5, hi=10, maxyes=7)",
            "the slow-cell sizing rule (bigtypes/bigsize)",
            "contestcap=1, contestkey=split",
            "the 0c offset and the 4h order timeout",
            "hold-to-settlement exits",
            "the tier bar and the live series pauses",
            "the fee model",
            "the predecessor's promotion bar, keep gate and every dollar bound",
            "the combined per-market bound with the partner (6 contracts / $6.00)",
        ],
        risk=RISK_ENVELOPE,
        docs={"evidence": EVIDENCE_DOC},
        change_reason=(
            "successor of mmsell-price-ceiling-cell-size-2: politics removed (owner decision "
            "2026-11-10, restart with mmsell-win-set-4)"
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="cell-size rule on weather, index and mentions",
        params={"lo": 5, "hi": 10, "maxyes": 7, "size": cs1.REST_CONTRACTS,
                "bigtypes": list(cs1.SLOW_CELL_TYPES), "bigsize": cs1.BIG_CONTRACTS,
                "only": list(ONLY_TOKENS), "universe": "graduated"},
        strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=PROMOTION_GATE_SPEC,
        from_state=LifecycleState.PAPER, to_state=LifecycleState.LIVE_CANARY,
        registered_at=at, notes="the predecessor's bar, unchanged and unfloored",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes="the predecessor's keep contract, unchanged",
    )
    service.freeze_version(session, version, now=at)
    service.mark_gate_evidence_started(session, promotion_gate, at=evidence_started_at or at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)
    service.transition_experiment(session, experiment, LifecycleState.PROBE,
                                  actor=actor, occurred_at=at,
                                  reason=f"contract registered; the probe is the 72h scorecard "
                                         f"({EVIDENCE_DOC})")
    service.transition_experiment(session, experiment, LifecycleState.PAPER,
                                  actor=actor, occurred_at=at,
                                  reason=f"paper on {PAPER_TAG}")
    epoch = service.open_epoch(
        session, version,
        reason="first operating interval, pinned to the snapshot active at registration",
        started_at=at,
    )
    paper = service.register_deployment(
        session, epoch, deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER, kind="paper",
        arms={ARM_KEY: PAPER_TAG}, started_at=at,
        notes=f"paper book {PAPER_BOOK_SPEC}",
    )
    return {
        "predecessor": predecessor, "experiment": experiment, "version": version,
        "promotion_gate": promotion_gate, "keep_gate": keep_gate, "epoch": epoch,
        "paper_deployment": paper, "registered_at": at,
    }


def material_config() -> dict:
    """The drift baseline: the book's params AND the shared pair it was registered with."""
    return {
        "material": enforcement.live_material_block(
            books={LIVE_TAG: (TWIN_TAG, BOOK_PARAMS)},
            shared_market_pair=SHARED_PAIR_ENV,
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
    """Arm through `service.arm_live_canary`, which re-evaluates the promotion bar on
    `Immsell10`'s paper. Places no order; the env swap is a separate act."""
    from .read import latest_version

    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        raise service.ExperimentOsError(f"{EXPERIMENT_KEY} does not exist — REGISTER_PACKAGE "
                                        "first")
    version = latest_version(session, experiment)
    gate = session.scalar(
        select(ExperimentGate).where(
            ExperimentGate.version_id == version.id,
            ExperimentGate.gate_key == PROMOTION_GATE_KEY,
        )
    )
    if gate is None:
        raise service.ExperimentOsError(f"{EXPERIMENT_KEY} has no {PROMOTION_GATE_KEY} gate")
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
            f"mmsell10 cell-size book (weather, index, mentions) armed on {LIVE_TAG} with twin "
            f"{TWIN_TAG}; shares markets with {PARTNER_LIVE_TAG} inside {SHARED_PAIR_ENV}; "
            f"successor of {PREDECESSOR_KEY}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "COMBINED_MAX_CONTRACTS", "COMBINED_MAX_USD",
    "DROPPED_CATEGORIES", "EXPERIMENT_KEY", "KEEP_GATE_KEY", "KEEP_GATE_SPEC", "KEPT_CATEGORIES",
    "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG", "ONLY_TOKENS", "PAPER_BOOK_SPEC",
    "PAPER_DEPLOYMENT_KEY", "PAPER_TAG", "PARTNER_LIVE_TAG", "PREDECESSOR_KEY",
    "PROMOTION_GATE_KEY", "PROMOTION_GATE_SPEC", "RISK_ENVELOPE", "SHARED_PAIR_ENV",
    "TWIN_DEPLOYMENT_KEY", "TWIN_TAG", "arm", "material_config", "register",
]
