"""Re-cut the contest-cap canary's live epoch onto FRESH tags after the 09-06 fix.

WHY THIS EXISTS
---------------
The canary armed at 2026-09-06T18:18Z on `Emmsell10` / `Emmsell10_pt4`. At 19:24Z
the audit found a BREACH -- the cap was declining nothing on real money -- and the
book was stood down by clearing `LIVE_STRATEGIES`. Two defects were then fixed in
code (`kalshi_bot/mmsell/regimes.py`: the contest key kept a player-prop's fourth
segment, so one game keyed as several contests; `kalshi_bot/mmsell/tracker.py`: a
mutually-exclusive event was exempted from the CONTEST cap as well as the RUNG
cap). At 23:51Z the SAME tags were re-armed by setting `LIVE_STRATEGIES` again.

That re-arm is what this package corrects, and it went wrong in two ways:

1. **No epoch boundary.** `Emmsell10` now carries pre-fix and post-fix live orders
   in one bucket. The code under test changed between them; the evidence is
   therefore not poolable, and nothing in the record says so. Separating them by
   timestamp is a thing an operator did by hand once, not a property of the data.

2. **A dead twin epoch.** `repository.sync_twin_epoch` is get-or-create keyed on
   the twin tag: the stand-down closed `Emmsell10_pt4`'s `live_paper_twins` row at
   19:25Z, and the re-arm found that row already present and returned it untouched.
   There is no path in that function that reopens a closed epoch or starts a new
   one, so re-arming a tag pair after a stand-down can never produce an open
   epoch. The live dashboard reads that table, so the running canary was invisible
   on it -- indistinguishable from a retired book.

The system already prescribes the fix for both: a new twin tag is a new epoch.

WHAT IT DOES
------------
Closes the open live epoch at one instant, opens its I2 successor, and registers a
fresh live deployment and its twin there on tags that have never been used --
`Fmmsell10` and `Fmmsell10_pt4`. The paper parent is carried forward so it does not
go dark (XOS-000011). Nothing else moves.

WHAT IT IS NOT
--------------
**Not a promotion.** The experiment is already LIVE_CANARY and stays LIVE_CANARY:
no lifecycle transition, no gate evaluation, no verdict. An epoch boundary is a
statement about the world changing, not about the experiment earning anything.

**Not a widening.** The risk envelope, book params and contest cap are IMPORTED
from the successor package rather than retyped, so the re-cut cannot carry a
loosened bound. `tests/test_recut_mmsell10_contest_cap.py` asserts the identity.

WHY THE TWIN TAG IS `Fmmsell10_pt4` AND NOT `_pt5`
--------------------------------------------------
`LIVE_PAPER_TWIN_SUFFIX` is a PROCESS-WIDE setting and production holds `_pt4`.
The worker derives every twin tag as `<live_tag><suffix>`, so naming the twin
`Fmmsell10_pt5` here would register a tag the harness never writes to, and moving
the global suffix to match would re-scope every other live book's twin. The live
tag is what changes; the twin follows from it. `Fmmsell10_pt4` has never existed,
so it is fresh in exactly the sense that matters.

WHY `F`
-------
`LIVE_STRATEGIES` matches by PREFIX. No existing tag begins with `Fmmsell10`, and
`Fmmsell10` begins with no existing tag, so allowlisting it captures nothing else
(`C` was the ceiling canary, `D` the capacity successor, `E` this one's first
generation, `G` the paper contest-cap arms).

WHAT IT REFUSES
---------------
Every precondition is CHECKED, not assumed: the experiment must be LIVE_CANARY,
the live epoch must be open and must carry exactly the deployments and tags this
was reviewed against, and both new tags must be free of any prior `paper_trades`
row and of any active deployment arm -- the same freshness rule `arm_live_canary`
enforces, replicated here rather than inherited, because this path sets
`_sanctioned_canary=True` and so must earn it. If production does not look the way
this docstring says it does, the re-cut refuses and names the assumption that
failed.

It is idempotent: a second run finds the successor deployments already registered
and reports `already_recut` rather than cutting another epoch.

STILL PLACES NO ORDER
---------------------
`MMSELL_VARIANTS` (which defines the book) and `LIVE_STRATEGIES` (which decides
whether it spends real money) are separate switches and a separate act. Until they
name `Fmmsell10`, this changes the record and nothing else. `ACTIVATION_VARS`
declares them so CI can assert the env channel will not refuse halfway through.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import PaperTrade as PaperTradeRef
from . import service
from .lifecycle import DeploymentKind, ImpactClass, LifecycleState
from .models import (
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
)
from .read import get_experiment, latest_version
from .successor_mmsell10_contest_cap import (
    ARM_KEY as ARM_KEY,
)
from .successor_mmsell10_contest_cap import (
    BOOK_PARAMS as BOOK_PARAMS,
)
from .successor_mmsell10_contest_cap import (
    LIVE_DEPLOYMENT_KEY as PRIOR_LIVE_DEPLOYMENT_KEY,
)
from .successor_mmsell10_contest_cap import (
    LIVE_TAG as PRIOR_LIVE_TAG,
)
from .successor_mmsell10_contest_cap import (
    RISK_ENVELOPE as RISK_ENVELOPE,
)
from .successor_mmsell10_contest_cap import (
    SUCCESSOR_KEY as EXPERIMENT_KEY,
)
from .successor_mmsell10_contest_cap import (
    TWIN_DEPLOYMENT_KEY as PRIOR_TWIN_DEPLOYMENT_KEY,
)
from .successor_mmsell10_contest_cap import (
    TWIN_TAG as PRIOR_TWIN_TAG,
)

#: The paper parent that must survive the boundary. Named so its disappearance is
#: a refusal rather than a silent outage.
PAPER_TAG = "mmsell10"

LIVE_TAG = "Fmmsell10"
TWIN_TAG = "Fmmsell10_pt4"

LIVE_DEPLOYMENT_KEY = "mmsell-contestcap-live-2"
TWIN_DEPLOYMENT_KEY = "mmsell-contestcap-twin-2"

LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"

#: The two defect fixes this boundary exists to separate. Recorded on the epoch so
#: the reason the evidence does not pool is readable from the row itself.
FIX_COMMIT = "ebccd47"
EPOCH_REASON = (
    "contest-cap defect boundary: the cap's key function and its mutual-exclusion "
    f"exemption both changed in {FIX_COMMIT} (regimes.contest_key_of kept a "
    "player-prop's 4th segment; tracker exempted mutually-exclusive events from "
    "the CONTEST cap). Pre-fix live evidence on Emmsell10 is NOT poolable with "
    "post-fix evidence, and the re-arm at 23:51Z recorded no boundary between them"
)

#: Everything the activation step sets, declared so CI asserts each name clears
#: `railway_env.ALLOWED_VARS`. Identical in shape to the successor package's: the
#: six mmsell safeguards are pinned explicitly so the envelope is true of the
#: running process rather than merely equal to today's code defaults.
ACTIVATION_VARS: frozenset[str] = frozenset(
    {"MMSELL_VARIANTS", "LIVE_STRATEGIES", *RISK_ENVELOPE["settings"]}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refuse(msg: str):
    raise service.ExperimentOsError(f"{EXPERIMENT_KEY} epoch re-cut refused: {msg}")


def material_config() -> dict:
    """The parameters a drift check compares the running book against.

    The same three keys the first generation declared, with only the tags moved --
    `book_spec` still carries `contestcap=1`, so editing the cap out of
    `MMSELL_VARIANTS` while this canary runs is still recorded as
    EXPERIMENT_CONFIG_DRIFT and still takes the keep gate to BLOCKED_INTEGRITY.
    """
    return {
        "book_spec": LIVE_BOOK_SPEC,
        "twin_tag": TWIN_TAG,
        "risk": RISK_ENVELOPE,
    }


def variants_for_recut(current: str) -> str:
    """`current` (the running `mmsell_variants`) with generation 1's book REPLACED.

    Both halves matter and the removal is the half that is easy to forget.
    `Emmsell10`'s deployment closes at the boundary, so a stale entry left in
    `MMSELL_VARIANTS` defines a book with no active deployment arm — under
    NEW_ONLY every entry it attempts is refused, on every scan cycle, which is
    the XOS-000011 shape wearing a config file's clothes.

    DERIVED rather than written down: the value is one ~800-character string
    holding EVERY mmsell book, and hand-composing it to change one entry is how a
    running book gets dropped by a typo. Dropping a book stops it silently.

    Idempotent, and refuses rather than overwrites. A `Fmmsell10:` entry that is
    already present with THIS spec is left alone; one carrying a different spec is
    refused, because silently replacing it would be an undetected parameter change
    to a registered book. An `Emmsell10:` entry whose spec is not the one
    generation 1 registered is likewise refused — if the running config is not
    what we believe we are retiring, the belief is what is wrong.
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


def activation_env(settings) -> dict[str, str]:
    """The EXACT variables the activation step sets, and nothing else.

    Composed, never applied here. This is the only step at which an order can
    reach Kalshi, and it is deliberately not something the re-cut itself can do.
    """
    env: dict[str, str] = {
        "MMSELL_VARIANTS": variants_for_recut(settings.mmsell_variants),
    }
    env.update(RISK_ENVELOPE["settings"])
    # Last, so the mapping reads in the order it takes effect: the book exists
    # and its caps are pinned before the switch that lets it spend anything.
    env["LIVE_STRATEGIES"] = LIVE_TAG
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
        "mmsell-contestcap-epoch2 registers no contract — it re-cuts the epoch of "
        f"{EXPERIMENT_KEY}, which register_package already created. Use ARM_CANARY."
    )


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "EPOCH_REASON", "EXPERIMENT_KEY",
    "FIX_COMMIT", "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG", "PAPER_TAG",
    "PRIOR_LIVE_DEPLOYMENT_KEY", "PRIOR_LIVE_TAG", "PRIOR_TWIN_DEPLOYMENT_KEY",
    "PRIOR_TWIN_TAG", "RISK_ENVELOPE", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG",
    "activation_env", "material_config", "recut", "register",
    "variants_for_recut",
]
