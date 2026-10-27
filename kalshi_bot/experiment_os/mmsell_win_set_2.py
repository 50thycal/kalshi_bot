"""The mmsell WIN-set canary, restarted with KXTRUMPMENTION — successor to `mmsell-win-set`.

Owner decision 2026-10-27 (session 01D3m7Af5FnpLDG5eDQ6JJRL): add the Trump word-mention
markets to the WIN-set book now, restarting it rather than waiting for its checkpoint.
Evidence: `docs/MMSELL_WIN_SET_EXPANSION_RESEARCH.md` §2 — full-history replay, 53 in-cell
entries over 43 days, no losses, +6.6c/contract (KXRAIN control reproduced).

WHY A SUCCESSOR, NOT A VERSION. The predecessor is LIVE_CANARY; its universe is an exact
`onlyx` list frozen in v2. A changed universe on a live book is a different question, and the
lifecycle only revises a contract in PAPER — so this is a new experiment with fresh tags, the
predecessor draining beside it. Everything except the universe and the tags is the
predecessor's object, imported, not restated: band, contest semantics, clip, risk envelope,
keep gate, and the owner-waiver promotion bar (`mmsell_win_set_canary.OWNER_WAIVER_*`).

THE UNIVERSE: the predecessor's seven series plus KXTRUMPMENTION, which this same change
classifies (`market_types.SERIES_TYPES`, mention/discrete), graduates
(`registry/series_manifest.json`) and subject-splits (`regimes.SUBJECT_SPLIT_SERIES`, so
`contestkey=split` counts each word as its own contest, as for KXTRUMPSAY). The global
event rung cap still holds it to 3 words per appearance (mention events are not mutually
exclusive).

TAGS, fresh and prefix-safe against every production tag (ops `tm-tags-1`, 2026-10-27: no tag
starts with Q or V):  live `Vmmsell10`, twin `Vmmsell10_pt4`, paper `Qmmsell10`.

THE PAIR. `Kmmsell10` keeps priority and the combined bound is unchanged (6 contracts /
$6.00): the env act swaps `Nmmsell10` for `Vmmsell10` in `MMSELL_LIVE_SHARED_MARKET_PAIRS`,
in `LIVE_STRATEGIES` and in `MMSELL_VARIANTS` (appended after `Kmmsell10`). The predecessor's
live and twin deployments stay open so its open positions settle on the record.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, service
from . import mmsell_win_set_canary as pred
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentGate
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-win-set-2"
PREDECESSOR_KEY = pred.EXPERIMENT_KEY

MONITOR_EXPECT = pred.MONITOR_EXPECT

ARM_KEY = pred.ARM_KEY
PAPER_TAG = "Qmmsell10"
LIVE_TAG = "Vmmsell10"
TWIN_TAG = "Vmmsell10_pt4"

PAPER_DEPLOYMENT_KEY = "mmsell-winset2-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-winset2-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-winset2-twin-1"

ADDED_SERIES = ("KXTRUMPMENTION",)
WIN_SERIES: tuple[str, ...] = tuple(sorted(pred.WIN_SERIES + ADDED_SERIES))
CATEGORY_ARTIFACTS = pred.CATEGORY_ARTIFACTS

CONTRACTS = pred.CONTRACTS
BOOK_PARAMS = (
    "lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,"
    f"size={CONTRACTS},universe=graduated,onlyx={'+'.join(WIN_SERIES)}"
)
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"
PAPER_BOOK_SPEC = f"{PAPER_TAG}:{BOOK_PARAMS}"

COMBINED_MAX_CONTRACTS = pred.COMBINED_MAX_CONTRACTS
COMBINED_MAX_USD = pred.COMBINED_MAX_USD
SHARED_PAIR_ENV = (f"{'+'.join(sorted((pred.INCUMBENT_LIVE_TAG, LIVE_TAG)))}:"
                   f"{COMBINED_MAX_CONTRACTS}:{COMBINED_MAX_USD:.2f}")

PROMOTION_GATE_KEY = pred.PROMOTION_GATE_KEY
KEEP_GATE_KEY = pred.KEEP_GATE_KEY
#: The predecessor's owner-waiver bar, the object itself (owner decision 2026-10-16, re-used
#: for the restart on 2026-10-27): passes while the paper book has not lost more than $3.00
#: since registration. The live keep gate is what can stop the canary.
PROMOTION_GATE_SPEC: dict = pred.OWNER_WAIVER_PROMOTION_SPEC
#: The predecessor's keep contract, verbatim (loss budget -$15, $3.00 per market, twin gaps).
KEEP_GATE_SPEC: dict = pred.KEEP_GATE_SPEC

RISK_ENVELOPE: dict = copy.deepcopy(pred.RISK_ENVELOPE)
RISK_ENVELOPE["stage"] = "canary_win_set_stage_2"
RISK_ENVELOPE["universe"] = (
    f"exact series allowlist {list(WIN_SERIES)} (WIN categories, graduated, live-eligible)"
)
RISK_ENVELOPE["shared_market"]["env"] = SHARED_PAIR_ENV
RISK_ENVELOPE["shared_market"]["queue_priority"] = (
    f"{pred.INCUMBENT_LIVE_TAG} precedes {LIVE_TAG} in MMSELL_VARIANTS"
)
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
    """Create the successor and walk it to PAPER on `Qmmsell10`. Arms nothing, places no
    order, ends no deployment — the predecessor keeps running until the env act swaps it."""
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
        title="mmsell WIN-set beside Kmmsell10, with the Trump word-mention markets added",
        family="maker",
        hypothesis=(
            "The predecessor's question on a wider WIN universe: restricted to slow-information "
            "categories (weather, index, Politics speech, mentions) now including "
            "KXTRUMPMENTION, the mmsell price-ceiling rule earns a positive per-contract live "
            "return."
        ),
        mechanism=(
            "As the predecessor: the paper-to-live fill gap is concentrated in liquid sports "
            "and crypto. KXTRUMPMENTION shares KXTRUMPSAY's contract (a named word at a named "
            "appearance), which has run positive live."
        ),
        falsification=(
            "The keep gate's stops trip, or at its sample floor (150 settled live contracts) "
            "the live per-contract realized return is <= 0, or the twin win-rate gap exceeds "
            "the bound."
        ),
        predecessor=predecessor,
        docs={"canary": "docs/MMSELL_WIN_SET_CANARY.md",
              "evidence": "docs/MMSELL_WIN_SET_EXPANSION_RESEARCH.md",
              "predecessor_plan": "docs/MMSELL_WIN_SET_CANARY.md"},
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis="WIN-set restriction (eight series) of the mmsell price-ceiling rule is "
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
            "the paper control of the same rule is Qmmsell10 (this experiment's paper arm); "
            "the execution control is the registered paper TWIN, armed at the same instant"
        ),
        independent_variable="market universe: the WIN set plus KXTRUMPMENTION",
        held_constant=[
            "band and ceiling (lo=5, hi=10, maxyes=7)",
            "contestcap=1, contestkey=split",
            "the 0c offset and the 4h order timeout",
            "hold-to-settlement exits",
            "the tier bar and the live series pauses",
            "the fee model",
            "the predecessor's risk envelope, keep gate and owner-waiver promotion bar",
            f"the incumbent {pred.INCUMBENT_LIVE_TAG}: no spec or Version change",
        ],
        risk=RISK_ENVELOPE,
        docs={"canary": "docs/MMSELL_WIN_SET_CANARY.md"},
        change_reason=(
            "successor of mmsell-win-set: KXTRUMPMENTION added to the universe (owner decision "
            "2026-10-27, restart now)"
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="mmsell price-ceiling on the exact eight-series WIN set, 3-lot",
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
                                  reason="contract registered; the probe is the expansion "
                                         "replay (docs/MMSELL_WIN_SET_EXPANSION_RESEARCH.md)")
    service.transition_experiment(session, experiment, LifecycleState.PAPER,
                                  actor=actor, occurred_at=at,
                                  reason="paper on Qmmsell10")
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
            f"mmsell WIN-set (with KXTRUMPMENTION) armed on {LIVE_TAG} with twin {TWIN_TAG}; "
            f"shares markets with {pred.INCUMBENT_LIVE_TAG} inside {SHARED_PAIR_ENV}; "
            f"successor of {PREDECESSOR_KEY}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


__all__ = [
    "ACTIVATION_VARS", "ADDED_SERIES", "ARM_KEY", "BOOK_PARAMS", "CATEGORY_ARTIFACTS",
    "COMBINED_MAX_CONTRACTS", "COMBINED_MAX_USD", "CONTRACTS", "EXPERIMENT_KEY",
    "KEEP_GATE_KEY", "KEEP_GATE_SPEC", "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG",
    "PAPER_BOOK_SPEC", "PAPER_DEPLOYMENT_KEY", "PAPER_TAG", "PREDECESSOR_KEY",
    "PROMOTION_GATE_KEY", "PROMOTION_GATE_SPEC", "RISK_ENVELOPE", "SHARED_PAIR_ENV",
    "TWIN_DEPLOYMENT_KEY", "TWIN_TAG", "WIN_SERIES", "arm", "material_config", "register",
]
