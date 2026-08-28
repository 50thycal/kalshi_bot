"""XOS-000011 data repair: restore `mmsell-type-tight` across its 2026-08-24 boundary.

WHAT BROKE
----------
The `MARKET_TAXONOMY:settlement_repair_2026_08_24` activation was applied to this
experiment as an I2 (sample boundary): v1/e1 closed and v1/e2 opened at
2026-08-24T14:21:17.571842Z. The engine cut the epoch and stopped there. It left
`tmmsell-paper-legacy-1` with `ended_at` NULL on the CLOSED epoch, and registered
nothing at all on the new one.

The admission resolver requires the deployment AND its epoch to be open, so
`Tmmsell1`, `Tmmsell2`, `Tmmsell5` and `Tmmsell6` resolved to no active deployment
arm from that instant. Under NEW_ONLY every entry they attempted was refused — and
the refusal escaped `MmSellTracker.run_once` into a single `session_scope`, taking
every OTHER mmsell book's entries down with it. Sixteen books, four days.

WHY THIS IS A PACKAGE AND NOT A MIGRATION
-----------------------------------------
It is a lifecycle write, and lifecycle writes go through the sanctioned transport
(`DEC-005`) so they leave a receipt naming the actor and the reason. A migration
would edit experiment state from outside the system that owns it, which is exactly
what Experiment OS exists to prevent — and it would run on every deploy of every
environment rather than once, deliberately, against the rows that are actually
broken.

WHAT IT DOES, AND WHAT IT REFUSES
---------------------------------
It ends the stranded deployment at the boundary and registers its successor on e2
with the SAME four tags. Nothing else: no gate, no verdict, no state transition, no
new arm. It is idempotent — a second run finds e2 already carrying the tags and
reports `already_repaired` rather than registering a duplicate, which would put each
tag on two active arms and stop the books a second way.

Every precondition is CHECKED, not assumed. If production does not look the way this
docstring says it does, the repair refuses and says which assumption failed, because
a repair that half-applies to a state it does not recognise is worse than no repair.

The engine fix that prevents recurrence is separate and already landed:
`close_epoch` now ends the deployments in its epoch, and `apply_new_epoch` carries
the open paper deployments onto the successor. This package only repairs the rows
that were broken before that existed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import service
from .models import (
    Experiment,
    ExperimentArm,
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
    ExperimentVersion,
)

EXPERIMENT_KEY = "mmsell-type-tight"
STRANDED_DEPLOYMENT_KEY = "tmmsell-paper-legacy-1"
#: The measured activation boundary of MARKET_TAXONOMY:settlement_repair_2026_08_24,
#: read off `experiment_epochs` in production on 2026-08-28. The repair uses THIS
#: instant rather than "now" so the ended deployment and the epoch it belonged to
#: agree, and so a gap never opens between them in the record.
BOUNDARY = datetime(2026, 8, 24, 14, 21, 17, 571842, tzinfo=timezone.utc)
EXPECTED_TAGS = ("Tmmsell1", "Tmmsell2", "Tmmsell5", "Tmmsell6")
SUCCESSOR_DEPLOYMENT_KEY = "tmmsell-paper-legacy-1-e2"


def _refuse(msg: str):
    raise service.ExperimentOsError(f"{EXPERIMENT_KEY} repair refused: {msg}")


def repair(session, *, actor: str, now: datetime | None = None) -> dict:
    """End the stranded deployment and re-register its books on the open epoch."""
    del now  # the boundary is measured, never "now" — see BOUNDARY
    exp = session.scalar(select(Experiment).where(Experiment.key == EXPERIMENT_KEY))
    if exp is None:
        _refuse(f"experiment {EXPERIMENT_KEY!r} is not registered")

    stranded = session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == STRANDED_DEPLOYMENT_KEY
        )
    )
    if stranded is None:
        _refuse(f"deployment {STRANDED_DEPLOYMENT_KEY!r} does not exist")
    old_epoch = session.get(ExperimentEpoch, stranded.epoch_id)
    version = session.get(ExperimentVersion, old_epoch.version_id)
    if version.experiment_id != exp.id:
        _refuse(
            f"{STRANDED_DEPLOYMENT_KEY!r} belongs to a different experiment — "
            "refusing to touch it"
        )

    open_epoch = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.ended_at.is_(None),
        )
    )
    if open_epoch is None:
        _refuse(
            f"v{version.version} has no open epoch to register the books on — the "
            "successor epoch this repair depends on is missing"
        )

    tags = {
        session.get(ExperimentArm, link.arm_id).arm_key: link.strategy_tag
        for link in session.scalars(
            select(ExperimentDeploymentArm).where(
                ExperimentDeploymentArm.deployment_id == stranded.id
            )
        )
    }
    if tuple(sorted(t for t in tags.values() if t)) != EXPECTED_TAGS:
        _refuse(
            f"expected the stranded deployment to carry {list(EXPECTED_TAGS)}, found "
            f"{sorted(t for t in tags.values() if t)} — production does not match "
            "what this repair was reviewed against"
        )

    # Idempotence, checked against the OPEN epoch rather than a flag: if the books
    # already live there, re-registering would put each tag on two active arms and
    # stop them a second way.
    already = [
        d.deployment_key
        for d in service.open_deployments(session, open_epoch)
    ]
    if already:
        return {
            "kind": "repair",
            "experiment": EXPERIMENT_KEY,
            "already_repaired": True,
            "open_epoch": open_epoch.epoch_number,
            "deployments_on_open_epoch": already,
            "stranded_ended_at": str(stranded.ended_at) if stranded.ended_at else None,
        }

    if old_epoch.ended_at is None:
        _refuse(
            f"epoch {old_epoch.epoch_number} is still OPEN — this repair exists for a "
            "deployment stranded on a CLOSED epoch, and the shape it was written for "
            "is not the shape production is in"
        )

    if stranded.ended_at is None:
        service.end_deployment(session, stranded, ended_at=old_epoch.ended_at)
    carried = service.carry_deployments_forward(
        session, [stranded], open_epoch,
        started_at=open_epoch.started_at,
        reason=(
            "XOS-000011: the I2 taxonomy boundary closed e1 without carrying these "
            "books onto e2, so all four tags were fail-closed from 2026-08-24"
        ),
    )
    session.flush()
    keys = [d.deployment_key for d in carried]
    if keys != [SUCCESSOR_DEPLOYMENT_KEY]:
        # The derived key is part of what was reviewed. If the derivation changes,
        # the repair should be re-read rather than silently register a new name.
        _refuse(
            f"expected to register {SUCCESSOR_DEPLOYMENT_KEY!r}, registered {keys} "
            "— the carry-forward key derivation changed under this repair"
        )
    return {
        "kind": "repair",
        "experiment": EXPERIMENT_KEY,
        "already_repaired": False,
        "open_epoch": open_epoch.epoch_number,
        "ended": STRANDED_DEPLOYMENT_KEY,
        "ended_at": str(stranded.ended_at),
        "registered": keys,
        "tags_restored": sorted(t for t in tags.values() if t),
    }


__all__ = ["BOUNDARY", "EXPECTED_TAGS", "EXPERIMENT_KEY", "repair"]
