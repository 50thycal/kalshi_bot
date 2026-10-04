"""Re-cut the size-split canary's live epoch onto FRESH tags when the XOS-000038 fix goes on.

WHY THIS EXISTS
---------------
`Hmmsell10` (armed 2026-10-04T02:18:55Z, epoch 2 of `mmsell-price-ceiling-size-split`) lost
live-tradable entries to a paper-only position: its paper side held a `KXNFLSPREAD` position
(paused for real money), that position used the game's one contest slot, and the same game's
`KXNFLTOTAL` was refused before the mirror was asked — while the twin, which applies the live
bars, took it (XOS-000038). PR #531 shipped the fix behind
`MMSELL_LIVE_CAPS_COUNT_LIVE_ELIGIBLE_ONLY`, default off.

Turning that flag on changes which candidates the live book admits: a candidate-population
change, which `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md` classes I2 -> NEW_EPOCH. The system's
own precedent for a defect fix on a running canary is `recut_mmsell10_contest_cap.py`
(2026-09-07): reusing the tag recorded no boundary between pre-fix and post-fix live
evidence, and a new twin tag is the prescribed remedy. This package applies that remedy here,
and its activation turns the flag on in the SAME env write, so the new epoch and the new rule
start at one instant.

WHAT IT DOES
------------
Closes epoch 2 at one instant, opens its I2 successor, and registers a fresh live deployment
and its twin there on tags that have never been used — `Jmmsell10` / `Jmmsell10_pt4`. The paper
parent (`mmsell10`) is carried forward (XOS-000011). Book params and the risk envelope are
IMPORTED from the successor package, so the re-cut cannot carry a different contract.

WHAT IT IS NOT
--------------
**Not a promotion** — no transition, no gate evaluation, no verdict. **Not a widening** — same
book, same envelope, same keep gate. The flag it activates can only admit live-tradable
candidates that the registered `contestcap=1` contract already intends; every executor bound
(per-order $3, 40 open, total exposure, daily loss) is untouched.

WHY `J`
-------
`LIVE_STRATEGIES` matches by PREFIX. No existing tag begins with `Jmmsell10` and it begins with
none (C/D/E/F/H were the mmsell10 live generations, G the paper contest-cap arms, L the
2026-08-15 failure). `I` is skipped to avoid an `I`/`l` misreading in logs.

STILL PLACES NO ORDER
---------------------
`MMSELL_VARIANTS`, `LIVE_STRATEGIES` and the flag are a separate act (`activation_env`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import PaperTrade as PaperTradeRef
from . import enforcement, service
from .lifecycle import DeploymentKind, ImpactClass, LifecycleState
from .models import (
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
)
from .read import get_experiment, latest_version
from .successor_mmsell10_size_split import (
    ARM_KEY as ARM_KEY,
)
from .successor_mmsell10_size_split import (
    BOOK_PARAMS as BOOK_PARAMS,
)
from .successor_mmsell10_size_split import (
    LIVE_DEPLOYMENT_KEY as PRIOR_LIVE_DEPLOYMENT_KEY,
)
from .successor_mmsell10_size_split import (
    LIVE_TAG as PRIOR_LIVE_TAG,
)
from .successor_mmsell10_size_split import (
    RISK_ENVELOPE as RISK_ENVELOPE,
)
from .successor_mmsell10_size_split import (
    SUCCESSOR_KEY as EXPERIMENT_KEY,
)
from .successor_mmsell10_size_split import (
    TWIN_DEPLOYMENT_KEY as PRIOR_TWIN_DEPLOYMENT_KEY,
)
from .successor_mmsell10_size_split import (
    TWIN_TAG as PRIOR_TWIN_TAG,
)

#: The paper parent that must survive the boundary.
PAPER_TAG = "mmsell10"

LIVE_TAG = "Jmmsell10"
TWIN_TAG = "Jmmsell10_pt4"

LIVE_DEPLOYMENT_KEY = "mmsell-sizesplit-live-2"
TWIN_DEPLOYMENT_KEY = "mmsell-sizesplit-twin-2"

LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"

#: The runtime rule this boundary exists to separate, and its value from the boundary on.
FIX_FLAG = "MMSELL_LIVE_CAPS_COUNT_LIVE_ELIGIBLE_ONLY"
FIX_COMMIT = "2e45af6"
EPOCH_REASON = (
    "XOS-000038 cap-scope boundary: from this instant the live book's concentration caps count "
    f"only positions live could hold ({FIX_FLAG}=true, PR #531 / {FIX_COMMIT}); before it a "
    "paper-only position in a live-paused series could fill a contest slot and refuse a "
    "live-tradable candidate. The live candidate population changed (I2): pre-boundary "
    "Hmmsell10 evidence is NOT poolable with post-boundary evidence"
)

#: Everything the activation step sets, declared so CI asserts each name clears
#: `railway_env.ALLOWED_VARS`.
ACTIVATION_VARS: frozenset[str] = frozenset(
    {"MMSELL_VARIANTS", "LIVE_STRATEGIES", FIX_FLAG, *RISK_ENVELOPE["settings"]}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refuse(msg: str):
    raise service.ExperimentOsError(f"{EXPERIMENT_KEY} epoch re-cut refused: {msg}")


def material_config() -> dict:
    """The drift baseline, with only the tags moved: `book_params` still carries
    `contestcap=1,contestkey=split,sizes=1+3`, so editing any of them out of `MMSELL_VARIANTS`
    while this canary runs is EXPERIMENT_CONFIG_DRIFT."""
    return {
        "material": enforcement.live_material_block(
            books={LIVE_TAG: (TWIN_TAG, BOOK_PARAMS)},
        ),
        "book_spec": LIVE_BOOK_SPEC,
        "twin_tag": TWIN_TAG,
        "risk": RISK_ENVELOPE,
    }


def variants_for_recut(current: str) -> str:
    """`current` (the running `mmsell_variants`) with `Hmmsell10`'s book REPLACED by `Jmmsell10`.

    The removal matters: `Hmmsell10`'s deployment closes at the boundary, so a stale entry would
    define a book with no active deployment arm, refused on every scan cycle (XOS-000011).
    DERIVED rather than hand-written, because dropping a book from this one long string stops it
    silently. Idempotent; refuses rather than overwrites an entry whose spec is not the
    registered `BOOK_PARAMS`.
    """
    tokens = [t.strip() for t in (current or "").split(";") if t.strip()]
    kept: list[str] = []
    already = False
    for token in tokens:
        tag, _, body = token.partition(":")
        tag, body = tag.strip(), body.strip()
        if tag == PRIOR_LIVE_TAG:
            if body != BOOK_PARAMS:
                _refuse(
                    f"{PRIOR_LIVE_TAG} is defined as {body!r}, not the registered "
                    f"{BOOK_PARAMS!r} — reconcile the running config against the "
                    "deployment's book_params before retiring it"
                )
            continue  # dropped: its deployment closes at the boundary
        if tag == LIVE_TAG:
            if body != BOOK_PARAMS:
                _refuse(
                    f"{LIVE_TAG} is already defined as {body!r}, which is not this "
                    f"canary's registered {BOOK_PARAMS!r}; overwriting it here "
                    "would be an undetected parameter change"
                )
            already = True
        kept.append(token)
    return ";".join(kept if already else [*kept, LIVE_BOOK_SPEC])


def strategies_for_recut(current: str) -> str:
    """`current` (the running `LIVE_STRATEGIES`) with the retiring tag REPLACED by the fresh one.

    Every other entry is kept in place — `Alimm1` shares the allowlist and must not stop. A
    value that does not name the retiring tag is refused: a re-cut that cannot find the book
    it is replacing has the wrong picture of production."""
    tokens = [t.strip() for t in (current or "").split(",") if t.strip()]
    if LIVE_TAG in tokens:
        return ",".join(tokens)          # idempotent: already re-cut
    if PRIOR_LIVE_TAG not in tokens:
        _refuse(f"LIVE_STRATEGIES {current!r} does not name {PRIOR_LIVE_TAG}; refusing to "
                "guess which book this replaces")
    return ",".join(LIVE_TAG if t == PRIOR_LIVE_TAG else t for t in tokens)


def activation_env(settings) -> dict[str, str]:
    """The EXACT variables the activation step sets, and nothing else — ONE env write, so the
    new book, its pinned envelope, the XOS-000038 rule and the allowlist land on one boot.

    Composed, never applied here."""
    env: dict[str, str] = {
        "MMSELL_VARIANTS": variants_for_recut(settings.mmsell_variants),
    }
    env.update(RISK_ENVELOPE["settings"])
    env[FIX_FLAG] = "true"
    # Last, so the mapping reads in the order it takes effect.
    env["LIVE_STRATEGIES"] = strategies_for_recut(settings.live_strategies)
    return env


def _tags_of(session, deployment: ExperimentDeployment) -> list[str]:
    return sorted(
        t for (t,) in session.execute(
            select(ExperimentDeploymentArm.strategy_tag).where(
                ExperimentDeploymentArm.deployment_id == deployment.id
            )
        ).all() if t
    )


def _assert_fresh(session, tag: str, at: datetime) -> None:
    """`arm_live_canary`'s freshness rule, replicated because this path bypasses it.

    A tag with prior paper history hands live a book of tickers it can never trade
    (the live mirror fires from the paper-open branch) -- the 2026-08-15 Lmmsell
    failure. A tag already on an active deployment would resolve to two arms and
    suppress candidates.
    """
    clash = session.scalar(
        select(ExperimentDeploymentArm)
        .join(
            ExperimentDeployment,
            ExperimentDeployment.id == ExperimentDeploymentArm.deployment_id,
        )
        .where(
            ExperimentDeploymentArm.strategy_tag == tag,
            ExperimentDeployment.ended_at.is_(None),
        )
    )
    if clash is not None:
        _refuse(f"tag {tag!r} is already carried by an active deployment")
    prior = session.scalar(
        select(func.count()).select_from(PaperTradeRef).where(
            PaperTradeRef.strategy == tag, PaperTradeRef.created_at < at
        )
    )
    if prior:
        _refuse(
            f"tag {tag!r} has {prior} paper_trades rows before the boundary — a "
            "live canary starts on FRESH tags with no inherited paper state"
        )


def recut(
    session,
    *,
    approved_by: str,
    actor: str = "operator",
    started_at: datetime | None = None,
    reason: str | None = None,
) -> dict:
    """Cut the live epoch onto fresh tags. Expands no bound; promotes nothing."""
    del reason  # the boundary's reason is EPOCH_REASON, not caller-supplied prose
    if not (approved_by or "").strip():
        _refuse("approved_by must name the operator who authorized the re-cut")

    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        _refuse(f"experiment {EXPERIMENT_KEY!r} is not registered")
    if experiment.state != LifecycleState.LIVE_CANARY.value:
        _refuse(
            f"experiment is {experiment.state}, not LIVE_CANARY — this re-cuts an "
            "epoch under a canary that is already armed; it does not arm one"
        )
    version = latest_version(session, experiment)
    if version is None or version.frozen_at is None:
        _refuse("no frozen current version to operate")

    # Idempotence, checked against the rows rather than a flag.
    existing = session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == LIVE_DEPLOYMENT_KEY
        )
    )
    if existing is not None:
        epoch = session.get(ExperimentEpoch, existing.epoch_id)
        return {
            "kind": "recut",
            "experiment": EXPERIMENT_KEY,
            "already_recut": True,
            "epoch": epoch.epoch_number if epoch is not None else None,
            "live": LIVE_DEPLOYMENT_KEY,
            "live_open": existing.ended_at is None,
            "tags": _tags_of(session, existing),
        }

    live_epoch = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )
    if live_epoch is None:
        _refuse(
            f"v{version.version} has no open epoch — there is nothing to cut, and "
            "opening one over an absent predecessor would invent a boundary"
        )

    # The shape this was reviewed against: the first-generation live deployment and
    # its twin, both open, on the tags the successor package declared.
    running = {d.deployment_key: d for d in service.open_deployments(session, live_epoch)}
    for key, expected_tag in (
        (PRIOR_LIVE_DEPLOYMENT_KEY, PRIOR_LIVE_TAG),
        (PRIOR_TWIN_DEPLOYMENT_KEY, PRIOR_TWIN_TAG),
    ):
        dep = running.get(key)
        if dep is None:
            _refuse(
                f"expected {key!r} to be open on epoch {live_epoch.epoch_number}; "
                f"open deployments are {sorted(running)}"
            )
        if _tags_of(session, dep) != [expected_tag]:
            _refuse(
                f"expected {key!r} to carry [{expected_tag!r}], found "
                f"{_tags_of(session, dep)} — production does not match what this "
                "re-cut was reviewed against"
            )

    # The paper parent rides across the boundary. Captured BEFORE the close, and
    # its absence is a refusal: carrying it is what stops XOS-000011 recurring.
    continuing = [
        d for d in running.values() if d.kind == DeploymentKind.PAPER.value
    ]
    parent_tags = {t for d in continuing for t in _tags_of(session, d)}
    if PAPER_TAG not in parent_tags:
        _refuse(
            f"the paper parent {PAPER_TAG!r} is not on an open paper deployment in "
            f"this epoch (found {sorted(parent_tags)}) — cutting here would take "
            "the control book dark"
        )

    at = started_at or _now()
    _assert_fresh(session, LIVE_TAG, at)
    _assert_fresh(session, TWIN_TAG, at)
    if LIVE_TAG == TWIN_TAG:
        _refuse("live and twin tags must be distinct")

    closed_number = live_epoch.epoch_number
    service.close_epoch(session, live_epoch, ended_at=at)
    new_epoch = service.open_epoch(
        session, version,
        reason=EPOCH_REASON,
        impact_class=ImpactClass.I2_SAMPLE_BOUNDARY,
        started_at=at,
        notes=(
            f"approved_by={approved_by}; predecessor tags {PRIOR_LIVE_TAG}/"
            f"{PRIOR_TWIN_TAG} retired at this instant and never reused"
        ),
    )
    live_dep = service.register_deployment(
        session, new_epoch,
        deployment_key=LIVE_DEPLOYMENT_KEY,
        stage=LifecycleState.LIVE_CANARY,
        kind=DeploymentKind.LIVE,
        arms={ARM_KEY: LIVE_TAG},
        config=material_config(),
        started_at=at,
        notes=f"epoch re-cut approved by {approved_by}; envelope unchanged",
        _sanctioned_canary=True,
    )
    twin_dep = service.register_deployment(
        session, new_epoch,
        deployment_key=TWIN_DEPLOYMENT_KEY,
        stage=LifecycleState.LIVE_CANARY,
        kind=DeploymentKind.PAPER_TWIN,
        arms={ARM_KEY: TWIN_TAG},
        twin_of=live_dep,
        config={"parameterized_to": "live knobs (twin harness)"},
        started_at=at,
        _sanctioned_canary=True,
    )
    carried = service.carry_deployments_forward(
        session, continuing, new_epoch,
        started_at=at,
        reason=f"paper parent continues beside live canary {LIVE_DEPLOYMENT_KEY}",
    )
    session.flush()
    return {
        "kind": "recut",
        "experiment": EXPERIMENT_KEY,
        "already_recut": False,
        "approved_by": approved_by,
        "actor": actor,
        "closed_epoch": closed_number,
        "epoch": new_epoch.epoch_number,
        "boundary": str(at),
        "retired_tags": [PRIOR_LIVE_TAG, PRIOR_TWIN_TAG],
        "live": {"deployment": live_dep.deployment_key, "tag": LIVE_TAG},
        "twin": {"deployment": twin_dep.deployment_key, "tag": TWIN_TAG},
        "carried_paper": [d.deployment_key for d in carried],
        "book_spec": LIVE_BOOK_SPEC,
    }


def register(session, **kwargs):
    """This package registers no contract — the successor already did.

    Present so the package is well formed, and refusing rather than silently
    doing nothing: a REGISTER_PACKAGE aimed here is a mis-addressed command.
    """
    del session, kwargs
    raise service.ExperimentOsError(
        "mmsell-sizesplit-epoch3 registers no contract — it re-cuts the epoch of "
        f"{EXPERIMENT_KEY}, which register_package already created. Use ARM_CANARY."
    )


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "EPOCH_REASON", "EXPERIMENT_KEY",
    "FIX_COMMIT", "FIX_FLAG", "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG", "PAPER_TAG",
    "PRIOR_LIVE_DEPLOYMENT_KEY", "PRIOR_LIVE_TAG", "PRIOR_TWIN_DEPLOYMENT_KEY",
    "PRIOR_TWIN_TAG", "RISK_ENVELOPE", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG",
    "activation_env", "material_config", "recut", "register",
    "strategies_for_recut", "variants_for_recut",
]
