"""The mmsell WIN-set canary, restarted beside the cell-size book's restart — successor to
`mmsell-win-set-2` (live `Vmmsell10`).

Owner decision 2026-10-29 (session 01D3m7Af5FnpLDG5eDQ6JJRL): restart Kmmsell10 without sports
and "other" (`successor_mmsell10_cell_size_2`), and restart this book with it, so the pair the
two share is registered in both. A book's drift baseline names its partner tag, so a partner on
a fresh tag is a new book here too.

WHAT CHANGES AGAINST THE PREDECESSOR, and nothing else:

  1. THE PARTNER. `Xmmsell10+Ymmsell10:6:6.00` (was `Kmmsell10+Vmmsell10`), the same bound.
  2. THE UNIVERSE drops KXWCMENTION, which has had no market since 2026-07-20 (the World Cup
     final). The seven other series are the predecessor's.

Unchanged: band, contest semantics, 3-lot, risk envelope, keep gate and the owner-waiver
promotion bar (`mmsell_win_set_canary.OWNER_WAIVER_*`).

TAGS, fresh and prefix-safe against every production tag (ops `rs-tags-1029`): live
`Ymmsell10`, twin `Ymmsell10_pt4`, paper `Zmmsell10`.

THE OLD BOOK. Registering ends nothing. `Vmmsell10` drains on the record after the arming env
act, its `MMSELL_VARIANTS` entry kept.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, service
from . import mmsell_win_set_2 as pred
from . import successor_mmsell10_cell_size_2 as partner
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentGate
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-win-set-3"
PREDECESSOR_KEY = pred.EXPERIMENT_KEY

MONITOR_EXPECT = pred.MONITOR_EXPECT

ARM_KEY = pred.ARM_KEY
PAPER_TAG = "Zmmsell10"
LIVE_TAG = partner.PARTNER_LIVE_TAG
TWIN_TAG = f"{LIVE_TAG}_pt4"

PAPER_DEPLOYMENT_KEY = "mmsell-winset3-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-winset3-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-winset3-twin-1"

DROPPED_SERIES = ("KXWCMENTION",)
WIN_SERIES: tuple[str, ...] = tuple(s for s in pred.WIN_SERIES if s not in DROPPED_SERIES)

CONTRACTS = pred.CONTRACTS
BOOK_PARAMS = (
    "lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,"
    f"size={CONTRACTS},universe=graduated,onlyx={'+'.join(WIN_SERIES)}"
)
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"
PAPER_BOOK_SPEC = f"{PAPER_TAG}:{BOOK_PARAMS}"

SHARED_PAIR_ENV = partner.SHARED_PAIR_ENV

PROMOTION_GATE_KEY = pred.PROMOTION_GATE_KEY
KEEP_GATE_KEY = pred.KEEP_GATE_KEY
#: The predecessor's objects, verbatim (owner waiver bar; keep: -$15, $3.00 per market).
PROMOTION_GATE_SPEC: dict = pred.PROMOTION_GATE_SPEC
KEEP_GATE_SPEC: dict = pred.KEEP_GATE_SPEC

RISK_ENVELOPE: dict = copy.deepcopy(pred.RISK_ENVELOPE)
RISK_ENVELOPE["stage"] = "canary_win_set_stage_3"
RISK_ENVELOPE["universe"] = (
    f"exact series allowlist {list(WIN_SERIES)} (WIN categories, graduated, live-eligible)"
)
RISK_ENVELOPE["shared_market"].update({
    "partner_tag": partner.LIVE_TAG,
    "partner_experiment": partner.EXPERIMENT_KEY,
    "env": SHARED_PAIR_ENV,
    "queue_priority": f"{partner.LIVE_TAG} precedes {LIVE_TAG} in MMSELL_VARIANTS",
})
RISK_ENVELOPE["settings"] = {"MMSELL_LIVE_SHARED_MARKET_PAIRS": SHARED_PAIR_ENV}

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
    """Create the successor and walk it to PAPER on `Zmmsell10`. Arms nothing, places no
    order, ends no deployment — the predecessor keeps trading until the env act swaps it."""
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "this package registers the predecessor's owner-waiver bar and takes no sample "
            f"floor — drop promotion_sample_floor={promotion_sample_floor!r} from the envelope."
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
        title="mmsell WIN-set beside the restarted cell-size book",
        family="maker",
        hypothesis=(
            "The predecessor's question, unchanged: restricted to slow-information WIN "
            "categories (weather, index, Politics speech, mentions), the mmsell price-ceiling "
            "rule earns a positive per-contract live return."
        ),
        mechanism=(
            "As the predecessor: the paper-to-live fill gap is concentrated in liquid sports "
            "and crypto, which this universe excludes."
        ),
        falsification=(
            "The keep gate's stops trip, or at its sample floor (150 settled live contracts) "
            "the live per-contract realized return is <= 0, or the twin win-rate gap exceeds "
            "the bound."
        ),
        predecessor=predecessor,
        docs={"canary": "docs/MMSELL_WIN_SET_CANARY.md",
              "evidence": "docs/mmsell/scorecards/2026-10-29.md",
              "predecessor_plan": "docs/MMSELL_WIN_SET_CANARY.md"},
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis="WIN-set restriction (seven series) of the mmsell price-ceiling rule is "
                   "live-positive.",
        universe_selector=(
            f"exact series {', '.join(WIN_SERIES)} (WIN categories, graduated, not paused); "
            "cheap band lo=5,hi=10, entry-price ceiling maxyes=7"
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule=f"{CONTRACTS} contracts under a $3.00 per-order dollar cap",
        execution_style="maker",
        control_required=False,
        control_exemption_reason=(
            "the paper control of the same rule is Zmmsell10 (this experiment's paper arm); "
            "the execution control is the registered paper TWIN, armed at the same instant"
        ),
        independent_variable="the shared-market partner (the restarted cell-size book)",
        held_constant=[
            "band and ceiling (lo=5, hi=10, maxyes=7)",
            "contestcap=1, contestkey=split",
            "the 0c offset and the 4h order timeout",
            "hold-to-settlement exits",
            "the tier bar and the live series pauses",
            "the fee model",
            "the predecessor's risk envelope, keep gate and owner-waiver promotion bar",
            "the combined per-market bound with the partner (6 contracts / $6.00)",
        ],
        risk=RISK_ENVELOPE,
        docs={"canary": "docs/MMSELL_WIN_SET_CANARY.md"},
        change_reason=(
            "successor of mmsell-win-set-2: restarted with the cell-size book's restart "
            f"(partner {partner.LIVE_TAG}); KXWCMENTION dropped (owner decision 2026-10-29)"
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="mmsell price-ceiling on the exact seven-series WIN set, 3-lot",
        params={"lo": 5, "hi": 10, "maxyes": 7, "size": CONTRACTS,
                "onlyx": list(WIN_SERIES), "universe": "graduated"},
        strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=PROMOTION_GATE_SPEC,
        from_state=LifecycleState.PAPER, to_state=LifecycleState.LIVE_CANARY,
        registered_at=at, notes="the predecessor's owner-waiver bar, unchanged",
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
                                  reason="contract registered; the probe is the predecessor's "
                                         "live record")
    service.transition_experiment(session, experiment, LifecycleState.PAPER,
                                  actor=actor, occurred_at=at,
                                  reason="paper on Zmmsell10")
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
    """Arm through `service.arm_live_canary`: fresh tags, the twin at the same instant, the
    envelope pre-registered. Places no order; the env swap is a separate act."""
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
            f"mmsell WIN-set armed on {LIVE_TAG} with twin {TWIN_TAG}; shares markets with "
            f"{partner.LIVE_TAG} inside {SHARED_PAIR_ENV}; successor of {PREDECESSOR_KEY}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "CONTRACTS", "DROPPED_SERIES", "EXPERIMENT_KEY",
    "KEEP_GATE_KEY", "KEEP_GATE_SPEC", "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG",
    "PAPER_BOOK_SPEC", "PAPER_DEPLOYMENT_KEY", "PAPER_TAG", "PREDECESSOR_KEY",
    "PROMOTION_GATE_KEY", "PROMOTION_GATE_SPEC", "RISK_ENVELOPE", "SHARED_PAIR_ENV",
    "TWIN_DEPLOYMENT_KEY", "TWIN_TAG", "WIN_SERIES", "arm", "material_config", "register",
]
