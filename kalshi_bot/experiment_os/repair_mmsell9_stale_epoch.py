"""MMSELL close-out, part 2: close `mmsell-price-ceiling`'s stale v1 epoch (which
carries the `mmsell9` paper book) so the experiment can be retired cleanly.

WHAT IS WRONG
-------------
`mmsell-price-ceiling` holds TWO open epochs: v2/e2 (its current contract) and
v1/e2, which carries one open paper deployment, `mmsell-ceiling-paper-mmsell9-2`
(tag `mmsell9`; the XOS-000033 restore). `STAND_DOWN` → RETIRED closes only the
LATEST version's open epoch (`read.open_epoch_for(latest_version)`), so retiring
as-is would leave v1/e2 and the mmsell9 deployment OPEN under a RETIRED
experiment — the record claiming a book runs under an experiment that is over,
the XOS-000011 shape. (Measured 2026-11-02, ops result `lo-mmclose-q10`.)

The owner (Calvin, 2026-11-02) decided to retire `mmsell-price-ceiling` and
`mmsell-price-ceiling-cell-size` and let `mmsell9` and `mmsell10` stop. This repair
closes v1/e2 — cascading its one deployment — and does nothing else. It moves no
lifecycle state, writes no verdict and touches no gate; retiring is the separate
`STAND_DOWN` that follows.

THE XOS-000033 GUARD
--------------------
Closing v1/e2 takes `mmsell9` out of the active tag set, so the package declares it
in `retires_tags`. The guard still refuses unless the running worker no longer
constructs `mmsell9`, so the envelope rides the same env request that removes the
`mmsell9` book from `MMSELL_VARIANTS` (the boot hook runs the command first).

WHAT IT REFUSES
---------------
  * the experiment is missing or not LIVE_CANARY;
  * version 1 is missing or is the LATEST version (then STAND_DOWN closes it itself
    and this repair is the wrong tool);
  * v1 has no open epoch numbered 2, or that epoch holds any deployment other than
    the one named below, or any live / twin deployment.

Idempotent: an already-closed v1/e2 is reported as `already_repaired`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import read, service
from .lifecycle import LifecycleState
from .models import Experiment, ExperimentDeployment, ExperimentEpoch, ExperimentVersion

EXPERIMENT_KEY = "mmsell-price-ceiling"
VERSION = 1
EPOCH_NUMBER = 2
DEPLOYMENT_KEY = "mmsell-ceiling-paper-mmsell9-2"
TAG = "mmsell9"
RETIRES_TAGS: tuple[str, ...] = (TAG,)


def _refuse(msg: str):
    raise service.ExperimentOsError(f"mmsell9 stale-epoch repair refused: {msg}")


def repair(session, *, actor: str, now: datetime | None = None) -> dict:
    del actor
    at = now or datetime.now(timezone.utc)

    experiment = session.scalar(select(Experiment).where(Experiment.key == EXPERIMENT_KEY))
    if experiment is None:
        _refuse(f"experiment {EXPERIMENT_KEY!r} is not registered")
    if experiment.state != LifecycleState.LIVE_CANARY.value:
        _refuse(f"{EXPERIMENT_KEY} is {experiment.state!r}, not LIVE_CANARY")

    version = session.scalar(
        select(ExperimentVersion).where(
            ExperimentVersion.experiment_id == experiment.id,
            ExperimentVersion.version == VERSION,
        )
    )
    if version is None:
        _refuse(f"{EXPERIMENT_KEY} has no v{VERSION}")
    latest = read.latest_version(session, experiment)
    if latest is None or latest.id == version.id:
        _refuse(f"v{VERSION} is the latest version — STAND_DOWN closes its epoch itself")

    epoch = session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == version.id,
            ExperimentEpoch.epoch_number == EPOCH_NUMBER,
        )
    )
    if epoch is None:
        _refuse(f"v{VERSION} has no epoch {EPOCH_NUMBER}")
    if epoch.ended_at is not None:
        return {"kind": "repair", "already_repaired": True, "closed_epoch": None}

    open_deps = session.scalars(
        select(ExperimentDeployment).where(
            ExperimentDeployment.epoch_id == epoch.id,
            ExperimentDeployment.ended_at.is_(None),
        )
    ).all()
    unexpected = [d.deployment_key for d in open_deps if d.deployment_key != DEPLOYMENT_KEY]
    if unexpected:
        _refuse(f"v{VERSION}/e{EPOCH_NUMBER} holds unexpected open deployments {unexpected}")
    for d in open_deps:
        if d.kind != "paper":
            _refuse(f"{d.deployment_key!r} is kind {d.kind!r}, not 'paper'")

    service.close_epoch(session, epoch, ended_at=at)
    return {
        "kind": "repair",
        "already_repaired": False,
        "closed_epoch": f"v{VERSION}/e{EPOCH_NUMBER}",
        "ended": [d.deployment_key for d in open_deps],
        "ended_at": at.isoformat(),
    }
