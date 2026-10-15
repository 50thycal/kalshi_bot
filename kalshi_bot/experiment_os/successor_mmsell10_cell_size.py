"""The mmsell10 CELL-SIZE successor — the size-split book with the random 1-vs-3 clip replaced
by a clip chosen by MARKET TYPE. Live tag `Kmmsell10`, twin `Kmmsell10_pt4`. Plan:
`docs/MMSELL_CELL_SIZE_CANARY.md`. Operator decision 2026-10-11 (`DEC-033`), the step `DEC-026`
named: size by edge instead of at random.

WHAT CHANGES AGAINST THE PREDECESSOR (`mmsell-price-ceiling-size-split`), and nothing else:

  1. THE TREATMENT — contracts per order by market type. The book spec carries
     `size=1,bigtypes=event_stat+mention+exact_score+outright+game_prop,bigsize=3`: a ticker
     whose series classifies into the SLOW-INFORMATION cell (`docs/MMSELL_REPLAY_PROBES_
     20261003.md` P-SLOW, the set frozen in `MMSELL_TYPE_RANK_THESIS.md` on 2026-10-01) rests a
     3-lot; every other ticker rests 1 contract. Recomputable from the ticker alone
     (`MmSellTracker._book_contracts`), so the readout needs no new column.

     What the random split established (read 2026-10-10, ops `edgesize-fills-1010`): 104 of 104
     3-lot orders with a fill filled completely, so the clip is executable; the 3-lot half ran
     -3.19c/contract against +1.03c for the 1-lot half, with S2 at about 95 of 150 settled per
     arm (unread, not a verdict). Entry price alone showed no edge (90-94c, win rate within
     -1.1 to +2.3pp of the price). The slow-information cell, across Fmmsell10/Hmmsell10/
     Jmmsell10 fills since 10-01, earned +7.05c/contract on 25 settled markets with no loss,
     against -2.80c on 239 for the rest. 25 is a quarter of P-SLOW's 100-fill floor: this
     successor is NOT a P-SLOW pass. It sizes up the one cell with a consistent positive sign,
     cuts every other market back to 1 contract, and keeps accruing the evidence that decides
     whether the cell goes larger.

  2. NOTHING ELSE. Band, ceiling, `contestcap=1`, `contestkey=split`, the 0c offset, the 4h
     timeout, hold-to-settlement, the 40-open cap, the twin cap, the tier bar and the live
     exposure pauses (KXNFLSPREAD, tennis, KXBTCD), the fee model, the promotion gate, the
     keep gate and the risk envelope's dollar bounds are the predecessor's. The keep gate is
     the predecessor's object, imported, not restated: its -$30 budget and $3.00 per-market
     bound were set for a 3-lot and still describe the largest clip this book can rest.

WHY A SUCCESSOR: `arm_live_canary` requires PAPER and LIVE_CANARY -> PAPER is illegal, so a
LIVE_CANARY experiment has no sanctioned way to re-arm with fresh tags and a new sizing rule.

THE OLD CANARY. Registering ends only the predecessor's PAPER deployment, handing `mmsell10`
over at one instant. `Jmmsell10`'s live and twin deployments stay OPEN so it drains with every
settlement recorded; it is shut down the way `Fmmsell10` was, by removal from `LIVE_STRATEGIES`.
The XOS-000040 gate (`mmsell_live_refuse_account_held`, PR #583) must be live before the
cutover, or `Kmmsell10` stacks a clip on markets `Jmmsell10` still holds.

GLOBALS, NAMED (live-paper-parallel §3b). None move. `LIVE_MAX_ORDER_DOLLARS` (3.0) and
`MAX_TOTAL_EXPOSURE` (125) are already at the predecessor's values, `MAX_MARKET_EXPOSURE` /
`MAX_DAILY_LOSS` are shared with `Alimm1` and not set here, and `LIVE_PAPER_TWIN_SUFFIX` stays
`_pt4`. The activation writes `MMSELL_VARIANTS` (append the `Kmmsell10` book) and
`LIVE_STRATEGIES` (`Jmmsell10` -> `Kmmsell10`, `Alimm1` kept), re-asserting the two dollar
settings at their current values.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, service
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentDeployment, ExperimentGate
from .read import get_experiment
from .successor_mmsell10_contest_cap import _epoch_experiment_id, _tags_of
from .successor_mmsell10_size_split import KEEP_GATE_SPEC as _PREDECESSOR_KEEP
from .successor_mmsell10_size_split import PROMOTION_GATE_SPEC as PROMOTION_GATE_SPEC
from .successor_mmsell10_size_split import RISK_ENVELOPE as _PREDECESSOR_ENVELOPE

PREDECESSOR_KEY = "mmsell-price-ceiling-size-split"
SUCCESSOR_KEY = "mmsell-price-ceiling-cell-size"

#: How quiet an armed arm may go before the global monitor (WS-026) reads it
#: STALE. Monitoring configuration only — never a gate, authorizes nothing.
#: See kalshi_bot/monitor/expectations.py.
MONITOR_EXPECT = {"max_silence_hours": 24}

ARM_KEY = "mmsell10"
PAPER_TAG = "mmsell10"
#: Fresh, per `arm_live_canary`'s no-inherited-state rule. `K` follows J (C ceiling, D
#: capacity, E/F contest cap, H/J size split; G and I skipped). No existing tag prefixes
#: `Kmmsell10` or is prefixed by it under LIVE_STRATEGIES' PREFIX match.
LIVE_TAG = "Kmmsell10"
TWIN_TAG = "Kmmsell10_pt4"

PAPER_DEPLOYMENT_KEY = "mmsell-cellsize-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-cellsize-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-cellsize-twin-1"

#: The slow-information cell, exactly the set P-SLOW froze on 2026-10-01.
SLOW_CELL_TYPES: tuple[str, ...] = ("event_stat", "mention", "exact_score", "outright",
                                    "game_prop")
BIG_CONTRACTS = 3
REST_CONTRACTS = 1

#: The predecessor's book with `sizes=1+3` replaced by the type rule.
BOOK_PARAMS = ("lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,"
               f"size={REST_CONTRACTS},bigtypes={'+'.join(SLOW_CELL_TYPES)},"
               f"bigsize={BIG_CONTRACTS}")
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"

PROMOTION_GATE_KEY = "paper_to_live_canary"
KEEP_GATE_KEY = "live_canary_keep"

#: The predecessor's envelope; only the sizing prose and the footprint estimate move. Every
#: dollar bound is unchanged (asserted in CI).
RISK_ENVELOPE: dict = copy.deepcopy(_PREDECESSOR_ENVELOPE)
RISK_ENVELOPE.update({
    "stage": "canary_cell_size_stage_1",
    "contracts_per_order": (
        f"{BIG_CONTRACTS} on the slow-information cell ({', '.join(SLOW_CELL_TYPES)}), "
        f"{REST_CONTRACTS} on every other market type (bigtypes/bigsize)"
    ),
    # ~10% of live flow has been slow-cell (25 of 264 settled since 10-01): 40 open at that mix
    # and ~94c is 4 x 2.82 + 36 x 0.94 = ~$45. The all-3-lot worst case is unchanged.
    "expected_book_exposure_usd": 45.12,
})

#: The predecessor's keep/stop contract, unchanged; only the prose names the new book.
KEEP_GATE_SPEC: dict = copy.deepcopy(_PREDECESSOR_KEEP)
KEEP_GATE_SPEC["description"] = (
    "Keep/stop for the mmsell10 cell-size canary (Kmmsell10): the size-split canary's contract "
    "unchanged — loss budget -$30, per-market loss bound $3.00 (one 3-lot clip). Every clause "
    "addresses deployment_kind='live'; twin comparisons resolve through the registered twin_of "
    "link."
)

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
    deployments stay open so `Jmmsell10` drains with every settlement recorded. Arming is a
    separate act and re-evaluates the promotion gate on paper settled inside this window."""
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "this package registers the predecessor's promotion bar UNCHANGED — drop "
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
        title="mmsell10 — 3-lot on the slow-information cell, 1 contract elsewhere",
        family="maker",
        hypothesis=(
            "The cheap-cell price-ceiling edge (lo=5, hi=10, maxyes=7) is concentrated in the "
            "slow-information market types (event_stat, mention, exact_score, outright, "
            "game_prop); sizing those at 3 contracts and every other type at 1 earns more per "
            "settled market than a type-blind clip, without the cell's per-contract edge "
            "eroding at the larger size."
        ),
        mechanism=(
            "Slow-information markets resolve on facts that are not revealed progressively in "
            "play, so a resting NO at 93-94c is rarely run over by faster-informed in-play "
            "flow; the in-play h2h/total/spread cells, where that flow lives, carried the "
            "losses in the size split."
        ),
        falsification=(
            "At P-SLOW's floor (100 settled slow-cell markets) the cell's per-contract "
            "realized is below +2.0c or its separation from the rest has a bootstrap 5th "
            "percentile <= 0; or this book's slow-cell per-contract realized is below 0 at "
            "50 settled slow-cell markets; or the keep gate's stops trip."
        ),
        predecessor=predecessor,
        docs={"canary": "docs/MMSELL_CELL_SIZE_CANARY.md",
              "evidence": "docs/MMSELL_REPLAY_PROBES_20261003.md",
              "predecessor_plan": "docs/MMSELL_SIZE_SPLIT_CANARY.md"},
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, successor,
        hypothesis=(
            "Same edge question as the predecessor; the clip is chosen by market type (3 on "
            "the slow-information cell, 1 elsewhere) instead of by a random per-ticker hash."
        ),
        universe_selector=(
            "cheap band lo=5,hi=10 with an entry-price ceiling maxyes=7 — the predecessor's "
            "universe verbatim, including the platform tier bar and the live exposure pauses"
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule=(
            f"{BIG_CONTRACTS} contracts when the ticker's series classifies "
            f"(mmsell/market_types.py) into {', '.join(SLOW_CELL_TYPES)}; {REST_CONTRACTS} "
            "otherwise (unclassified included); under a $3.00 per-order dollar cap"
        ),
        execution_style="maker",
        control_required=False,
        control_exemption_reason=(
            "the 1-contract rest of the book is the concurrent comparison for the slow cell, "
            "in one book, regime and scan; the execution control is the registered paper "
            "TWIN, armed at the same instant; the paper control is mmsell10, which trades both "
            "cells at one contract"
        ),
        independent_variable=(
            "contracts per order by market type (slow-information cell 3, rest 1)"
        ),
        held_constant=[
            "arm parameters (lo=5, hi=10, maxyes=7)",
            "market universe, the tier bar and the live exposure pauses",
            "entry timing and the 0c price offset",
            "the 4h order timeout",
            "hold-to-settlement exits (no TP/SL)",
            "the fee model",
            "contestcap=1, contestkey=split and max_event_rungs=3",
            "the open-position cap (40) and the twin cap (250)",
            "the promotion bar and the keep gate, both the predecessor's own frozen objects",
            "every dollar bound in the risk envelope",
        ],
        risk=RISK_ENVELOPE,
        docs={"canary": "docs/MMSELL_CELL_SIZE_CANARY.md"},
        change_reason=(
            "Replaces the random sizes=1+3 split with a type rule: 3 contracts on the "
            "slow-information cell, 1 elsewhere. Operator decision 2026-10-11 (DEC-033)."
        ),
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="entry-price ceiling (lo=5,hi=10,maxyes=7); the slow-cell 3-lot is a "
                    "per-type rule inside the live book, not a separate arm",
        params={"lo": 5, "hi": 10, "maxyes": 7, "size": REST_CONTRACTS,
                "bigtypes": list(SLOW_CELL_TYPES), "bigsize": BIG_CONTRACTS},
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
        notes="the size-split canary's keep contract, unchanged",
    )
    service.freeze_version(session, version, now=at)
    evidence_at = evidence_started_at or at
    service.mark_gate_evidence_started(session, promotion_gate, at=evidence_at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)
    service.transition_experiment(session, successor, LifecycleState.PROBE,
                                  actor=actor, occurred_at=at,
                                  reason="contract registered; the probe is the size split's "
                                         "live read and P-SLOW (docs/MMSELL_CELL_SIZE_CANARY.md)")
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
    """The drift baseline: `book_params` carries the type rule, so editing it out of
    MMSELL_VARIANTS mid-canary is EXPERIMENT_CONFIG_DRIFT, not a silent change."""
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

    Real-money capability is the predecessor's ($3.00 per order and per market); the expected
    footprint is LOWER (~$45 vs ~$75) because most of the book drops to one contract. Places no
    order by itself; `MMSELL_VARIANTS` + `LIVE_STRATEGIES` are a separate act."""
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
            f"mmsell10 cell-size canary armed on {LIVE_TAG} with twin {TWIN_TAG} at one "
            f"boundary; {BIG_CONTRACTS}-lot on the slow-information cell, {REST_CONTRACTS} "
            f"elsewhere, pre-registered on v{version.version}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BIG_CONTRACTS", "BOOK_PARAMS", "KEEP_GATE_KEY",
    "KEEP_GATE_SPEC", "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG",
    "PAPER_DEPLOYMENT_KEY", "PAPER_TAG", "PREDECESSOR_KEY", "PROMOTION_GATE_KEY",
    "PROMOTION_GATE_SPEC", "REST_CONTRACTS", "RISK_ENVELOPE", "SLOW_CELL_TYPES",
    "SUCCESSOR_KEY", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG", "arm", "material_config", "register",
]
