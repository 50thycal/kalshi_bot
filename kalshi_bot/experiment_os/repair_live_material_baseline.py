"""XOS-000036 record repair: backfill the drift-check baseline on the two OPEN
live deployments that never carried one.

WHAT IS WRONG
-------------
`enforcement.runtime_config_check` compares a live deployment against the running
configuration using `config_json['material']`. Three canary packages each wrote
their own flat shape instead — `book_spec` / `twin_tag` / `risk` — so the check
found no `material` key and, until this ticket, skipped those deployments in
silence. Measured in production 2026-09-12 (ops `pcr-depcfg-20260912`), of the
three OPEN live deployments:

    mmsell-ceiling-live-1     material ✓   (drift detected — integrity event #16)
    mmsell-capacity-live-1    material ✗   armed 2026-09-02, Dmmsell10
    mmsell-contestcap-live-2  material ✗   armed 2026-09-07, Fmmsell10

The two without it have never been compared against anything. That is what the
Experiment Control Tower actually found when it asked why `Dmmsell10` left
`LIVE_STRATEGIES` with no integrity event and no stand-down record: there was
nothing to record from, because the book was outside the check.

The code fix stops it recurring — one builder (`enforcement.live_material_block`),
and `arm_live_canary` refuses a live deployment without a baseline. Neither
touches the two rows already armed, and re-arming to fix a record would be a
real-money act to repair paperwork. Hence a repair.

WHY A REPAIR AND NOT A LIFECYCLE VERB
-------------------------------------
It writes one key onto two deployment rows. It moves no lifecycle state, touches
no gate, records no verdict, opens no epoch, and cannot arm or disarm anything —
the definition of `REPAIR_LINEAGE`. It expands no exposure: `LIVE_STRATEGIES` is
the only thing that authorizes a live order and this cannot reach it.

What it DOES do is restore a safeguard, so the effects are real and are the point:

  * `Fmmsell10` is in the runtime allowlist, so its comparison starts running in
    full. If its running config has drifted from what was registered, the next
    live-worker boot records `EXPERIMENT_CONFIG_DRIFT` and its keep gate goes
    BLOCKED_INTEGRITY. That is the safeguard working, on a book where it has
    never run;
  * `Dmmsell10` is not in the allowlist, so the same boot records the per-book
    `EXPERIMENT_EXECUTION_STOOD_DOWN` the Control Tower found missing;
  * any open `EXPERIMENT_CONFIG_UNVERIFIABLE` event resolves itself, because the
    deployment is comparable again.

All three happen through the audited runtime path on the live worker, not here.

WHAT IT WRITES, AND FROM WHERE
------------------------------
The baseline is the package's own reviewed literals — the same
`material_config()` the fixed package now produces at arming time — NEVER
anything read back from the running configuration. A baseline recomputed from
the runtime would match it by construction and check nothing, which is worse
than no baseline at all: it would look like coverage.

Because the literals must describe THIS row, every one is checked against what
the row already stores, and a mismatch is a refusal rather than an overwrite:
`book_spec` must equal the package's, `twin_tag` must equal the package's, and
the deployment's own registered arm tag must be the package's live tag.

WHAT IT REFUSES
---------------
  * a deployment that is missing, not `kind='live'`, or already ended;
  * a deployment whose epoch is closed (the XOS-000011 shape — a different
    repair);
  * a deployment that already carries a usable `material` block — reported as
    `already_repaired`, never re-written, so a second run cannot overwrite a
    baseline a first run (or a later arming) established;
  * any disagreement between the package's literals and the row's stored facts.

Idempotent, and it does nothing at all if production is not the shape described
above.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, recut_mmsell10_contest_cap, service
from . import successor_mmsell10_capacity as capacity
from .lifecycle import DeploymentKind, LifecycleState
from .models import (
    Experiment,
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
    ExperimentVersion,
)

#: The live deployments this repair was reviewed against. Each names the package
#: that armed it, so the baseline written is the one that package registers —
#: literals in reviewed code, not something an envelope can supply.
TARGETS: tuple[tuple[str, object], ...] = (
    (capacity.LIVE_DEPLOYMENT_KEY, capacity),
    (recut_mmsell10_contest_cap.LIVE_DEPLOYMENT_KEY, recut_mmsell10_contest_cap),
)


def _refuse(msg: str):
    raise service.ExperimentOsError(f"live-material-baseline repair refused: {msg}")


def _arm_tags(session, deployment: ExperimentDeployment) -> list[str]:
    return sorted(
        t for t in session.scalars(
            select(ExperimentDeploymentArm.strategy_tag).where(
                ExperimentDeploymentArm.deployment_id == deployment.id
            )
        ).all() if t
    )


def repair(session, *, actor: str, now: datetime | None = None) -> dict:
    """Backfill `config_json['material']` on the two reviewed live deployments."""
    del actor
    at = now or datetime.now(timezone.utc)
    del at  # nothing here is time-stamped: the record gains a fact, not an event

    repaired: list[dict] = []
    already: list[str] = []
    for deployment_key, package in TARGETS:
        deployment = session.scalar(
            select(ExperimentDeployment).where(
                ExperimentDeployment.deployment_key == deployment_key
            )
        )
        if deployment is None:
            _refuse(f"deployment {deployment_key!r} does not exist")
        if deployment.kind != DeploymentKind.LIVE.value:
            _refuse(
                f"{deployment_key!r} is kind {deployment.kind!r}, not 'live' — "
                "refusing to touch a deployment this repair does not describe"
            )
        if deployment.ended_at is not None:
            _refuse(
                f"{deployment_key!r} has ended; a closed deployment's registered "
                "config is history and is not repaired in place"
            )

        epoch = session.get(ExperimentEpoch, deployment.epoch_id)
        version = session.get(ExperimentVersion, epoch.version_id)
        experiment = session.get(Experiment, version.experiment_id)
        if epoch.ended_at is not None:
            _refuse(
                f"epoch {epoch.epoch_number} of {experiment.key} is CLOSED while "
                f"{deployment_key!r} is open — that is the XOS-000011 shape and a "
                "different repair"
            )
        if experiment.state != LifecycleState.LIVE_CANARY.value:
            _refuse(
                f"{experiment.key} is {experiment.state!r}, not LIVE_CANARY — the "
                "shape this repair was reviewed against is not the shape "
                "production is in"
            )

        config = dict(deployment.config_json or {})
        if enforcement.live_material_or_none(config) is not None:
            already.append(deployment_key)
            continue

        # The literals must describe THIS row. Each check is against a fact the
        # row already stores, so agreeing is evidence and disagreeing is a stop.
        expected_config = package.material_config()
        for key in ("book_spec", "twin_tag"):
            stored, expected = config.get(key), expected_config.get(key)
            if stored != expected:
                _refuse(
                    f"{deployment_key!r} stores {key}={stored!r} but "
                    f"{package.__name__.rsplit('.', 1)[-1]} registers {expected!r} "
                    "— the row is not the one this repair was reviewed against"
                )
        tags = _arm_tags(session, deployment)
        if tags != [package.LIVE_TAG]:
            _refuse(
                f"{deployment_key!r} carries arm tags {tags}, not "
                f"[{package.LIVE_TAG!r}] — refusing to write a baseline naming a "
                "tag this deployment does not run"
            )

        material = expected_config["material"]
        # Reassign the whole dict: an in-place mutation of a JSON column is not
        # seen by the ORM's change detection and would commit nothing.
        deployment.config_json = {**config, "material": material}
        repaired.append({"deployment": deployment_key, "material": material})

    session.flush()
    return {
        "repaired": repaired,
        "already_repaired": already,
        "note": (
            "The record now carries a comparable baseline. The stand-down, drift "
            "or resolution that follows is recorded by the LIVE worker's next "
            "runtime_config_check, through the audited path — not by this repair."
        ),
    }
