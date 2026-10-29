"""The mmsell10 cell-size book restarted WITHOUT sports and "other" — successor to
`mmsell-price-ceiling-cell-size` (live `Kmmsell10`).

Owner decision 2026-10-29 (session 01D3m7Af5FnpLDG5eDQ6JJRL), from the first 72h scorecard
(`docs/mmsell/scorecards/2026-10-29.md`): sports was 204 of Kmmsell10's 293 settled markets at
about 1c a contract and held 14 of its 20 losses; "other" (oil, bitcoin, rankings, CPI) ran
-3.6c a contract. Weather, index, Trump/politics and mention series carried the profit. Restart
the book on those four categories only, together with the WIN-set book (`mmsell_win_set_3`),
which shares its markets.

WHAT CHANGES AGAINST THE PREDECESSOR, and nothing else:

  1. THE UNIVERSE. `only=` the substrings of the scorecard's four kept categories
     (`scripts/mmsell_scorecard.py` `category`): weather KXRAIN/KXHIGH/KXLOW/KXSNOW, index
     KXINX/KXNASDAQ/KXDOW/KXSPX, politics KXTRUMP + KXTRUTHSOCIAL, mentions MENTION. Plus
     `universe=graduated`, which live already applies globally (`MMSELL_LIVE_MIN_TIER`); on the
     book it makes the paper arm trade the universe live can.
  2. A FRESH PAPER ARM, `Smmsell10`, on that universe. The predecessor's paper control
     (`mmsell10`) trades every category; a promotion bar read on it would not be about this
     book. The bar itself is the predecessor's object, unchanged.
  3. THE SHARED PAIR. `Xmmsell10+Ymmsell10:6:6.00`, with the WIN-set successor. The same
     combined bound as before (largest clip 3 + 3, $3.00 + $3.00).

Unchanged: band, ceiling, the slow-cell sizing rule (3 contracts on event_stat / mention /
exact_score / outright / game_prop, 1 elsewhere), `contestcap=1`, `contestkey=split`, the keep
gate (loss budget -$30, $3.00 per market), every dollar bound in the envelope.

TAGS, fresh and prefix-safe against every production tag (ops `rs-tags-1029`: no tag starts
with S, X, Y or Z; `Wmmsell1..8` rule out W): live `Xmmsell10`, twin `Xmmsell10_pt4`, paper
`Smmsell10`.

THE OLD BOOK. Registering ends nothing. `Kmmsell10` keeps trading until the arming env act
takes it out of `LIVE_STRATEGIES`; its live and twin deployments stay open so it drains on the
record, and its `MMSELL_VARIANTS` entry STAYS (a stood-down book's params are compared in full,
WS-027 2026-10-27).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, service
from . import successor_mmsell10_cell_size as pred
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentGate
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-price-ceiling-cell-size-2"
PREDECESSOR_KEY = pred.SUCCESSOR_KEY

MONITOR_EXPECT = pred.MONITOR_EXPECT

ARM_KEY = pred.ARM_KEY
PAPER_TAG = "Smmsell10"
LIVE_TAG = "Xmmsell10"
TWIN_TAG = "Xmmsell10_pt4"
#: The WIN-set successor this book shares markets with (`mmsell_win_set_3.LIVE_TAG`).
PARTNER_LIVE_TAG = "Ymmsell10"

PAPER_DEPLOYMENT_KEY = "mmsell-cellsize2-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-cellsize2-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-cellsize2-twin-1"

#: Kept categories (owner decision 2026-10-29), as series substrings.
KEPT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "weather": ("KXRAIN", "KXHIGH", "KXLOW", "KXSNOW"),
    "index": ("KXINX", "KXNASDAQ", "KXDOW", "KXSPX"),
    "politics": ("KXTRUMP", "KXTRUTHSOCIAL"),
    "mentions": ("MENTION",),
}
ONLY_TOKENS: tuple[str, ...] = tuple(t for toks in KEPT_CATEGORIES.values() for t in toks)

BOOK_PARAMS = f"{pred.BOOK_PARAMS},universe=graduated,only={'+'.join(ONLY_TOKENS)}"
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"
PAPER_BOOK_SPEC = f"{PAPER_TAG}:{BOOK_PARAMS}"

COMBINED_MAX_CONTRACTS = 6
COMBINED_MAX_USD = 6.0
SHARED_PAIR_ENV = (f"{'+'.join(sorted((LIVE_TAG, PARTNER_LIVE_TAG)))}:"
                   f"{COMBINED_MAX_CONTRACTS}:{COMBINED_MAX_USD:.2f}")

PROMOTION_GATE_KEY = pred.PROMOTION_GATE_KEY
KEEP_GATE_KEY = pred.KEEP_GATE_KEY
#: The predecessor's bar, the object itself: the calibrated live-fill projection of the paper
#: arm must be positive. Read on `Smmsell10`, so it needs about a day of settled paper.
PROMOTION_GATE_SPEC: dict = pred.PROMOTION_GATE_SPEC
KEEP_GATE_SPEC: dict = copy.deepcopy(pred.KEEP_GATE_SPEC)
KEEP_GATE_SPEC["description"] = (
    "Keep/stop for the mmsell10 cell-size canary restarted on weather, index, politics and "
    "mentions (Xmmsell10): the predecessor's contract unchanged — loss budget -$30, "
    "per-market loss bound $3.00 (one 3-lot clip). Every clause addresses "
    "deployment_kind='live'; twin comparisons resolve through the registered twin_of link."
)

RISK_ENVELOPE: dict = copy.deepcopy(pred.RISK_ENVELOPE)
RISK_ENVELOPE["stage"] = "canary_cell_size_stage_2"
RISK_ENVELOPE["universe"] = (
    f"series containing any of {list(ONLY_TOKENS)} (weather, index, politics, mentions), "
    "graduated; sports and every other category excluded"
)
RISK_ENVELOPE["shared_market"] = {
    "partner_tag": PARTNER_LIVE_TAG,
    "partner_experiment": "mmsell-win-set-3",
    "partner_max_market_exposure_usd": 3.0,
    "max_combined_contracts": COMBINED_MAX_CONTRACTS,
    "max_combined_usd": COMBINED_MAX_USD,
    "env": SHARED_PAIR_ENV,
    "queue_priority": f"{LIVE_TAG} precedes {PARTNER_LIVE_TAG} in MMSELL_VARIANTS",
}
RISK_ENVELOPE["settings"] = {**pred.RISK_ENVELOPE["settings"],
                             "MMSELL_LIVE_SHARED_MARKET_PAIRS": SHARED_PAIR_ENV}

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
    """Create the successor and walk it to PAPER on `Smmsell10`. Arms nothing, places no
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
        title="mmsell10 cell-size book on weather, index, politics and mentions only",
        family="maker",
        hypothesis=(
            "The predecessor's question on its profitable categories only: the cheap-cell "
            "price-ceiling rule, sized 3 on the slow-information cell and 1 elsewhere, earns a "
            "positive live per-contract return on weather, index, Trump/politics and mention "
            "series, without the sports and other markets that carried its losses."
        ),
        mechanism=(
            "Sports markets are run over by faster-informed in-play flow, and the 'other' "
            "categories (oil, crypto, rankings, CPI) priced the 93-94c NO fairly; the kept "
            "categories resolve on facts revealed late (a word said, a day's rain, an index "
            "close), where a resting NO at the ceiling is rarely picked off."
        ),
        falsification=(
            "The keep gate's stops trip, or at its sample floor the live per-contract realized "
            "return is <= 0, or the twin win-rate gap exceeds the bound."
        ),
        predecessor=predecessor,
        docs={"evidence": "docs/mmsell/scorecards/2026-10-29.md",
              "predecessor_plan": "docs/MMSELL_CELL_SIZE_CANARY.md",
              "canary": "docs/MMSELL_WIN_SET_CANARY.md"},
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis="The cell-size rule restricted to weather, index, politics and mentions is "
                   "live-positive.",
        universe_selector=(
            f"series containing any of {', '.join(ONLY_TOKENS)} (graduated); cheap band "
            "lo=5,hi=10, entry-price ceiling maxyes=7"
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule=(
            f"{pred.BIG_CONTRACTS} contracts on the slow-information cell "
            f"({', '.join(pred.SLOW_CELL_TYPES)}), {pred.REST_CONTRACTS} otherwise; under a "
            "$3.00 per-order dollar cap"
        ),
        execution_style="maker",
        control_required=False,
        control_exemption_reason=(
            "the paper control of the same rule and universe is Smmsell10 (this experiment's "
            "paper arm); the execution control is the registered paper TWIN, armed at the "
            "same instant"
        ),
        independent_variable="market universe: weather, index, politics and mentions only",
        held_constant=[
            "band and ceiling (lo=5, hi=10, maxyes=7)",
            "the slow-cell sizing rule (bigtypes/bigsize)",
            "contestcap=1, contestkey=split",
            "the 0c offset and the 4h order timeout",
            "hold-to-settlement exits",
            "the tier bar and the live series pauses",
            "the fee model",
            "the predecessor's promotion bar, keep gate and every dollar bound",
        ],
        risk=RISK_ENVELOPE,
        docs={"evidence": "docs/mmsell/scorecards/2026-10-29.md"},
        change_reason=(
            "successor of mmsell-price-ceiling-cell-size: sports and other categories removed "
            "(owner decision 2026-10-29, restart with mmsell-win-set-3)"
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="cell-size rule on weather, index, politics and mentions",
        params={"lo": 5, "hi": 10, "maxyes": 7, "size": pred.REST_CONTRACTS,
                "bigtypes": list(pred.SLOW_CELL_TYPES), "bigsize": pred.BIG_CONTRACTS,
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
                                  reason="contract registered; the probe is the 72h scorecard "
                                         "(docs/mmsell/scorecards/2026-10-29.md)")
    service.transition_experiment(session, experiment, LifecycleState.PAPER,
                                  actor=actor, occurred_at=at,
                                  reason="paper on Smmsell10")
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
    `Smmsell10`'s paper. Places no order; the env swap is a separate act."""
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
            f"mmsell10 cell-size book (weather, index, politics, mentions) armed on {LIVE_TAG} "
            f"with twin {TWIN_TAG}; shares markets with {PARTNER_LIVE_TAG} inside "
            f"{SHARED_PAIR_ENV}; successor of {PREDECESSOR_KEY}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "COMBINED_MAX_CONTRACTS", "COMBINED_MAX_USD",
    "EXPERIMENT_KEY", "KEEP_GATE_KEY", "KEEP_GATE_SPEC", "KEPT_CATEGORIES", "LIVE_BOOK_SPEC",
    "LIVE_DEPLOYMENT_KEY", "LIVE_TAG", "ONLY_TOKENS", "PAPER_BOOK_SPEC", "PAPER_DEPLOYMENT_KEY",
    "PAPER_TAG", "PARTNER_LIVE_TAG", "PREDECESSOR_KEY", "PROMOTION_GATE_KEY",
    "PROMOTION_GATE_SPEC", "RISK_ENVELOPE", "SHARED_PAIR_ENV", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG",
    "arm", "material_config", "register",
]
