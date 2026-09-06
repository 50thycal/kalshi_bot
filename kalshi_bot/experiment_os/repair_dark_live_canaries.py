"""XOS-000012 record repair: end two LIVE deployments that stopped trading on
2026-08-19 and were never closed in the record.

WHAT IS WRONG
-------------
`theta4-fat-tail` and `mmsell-scheduled-settle-live` are both recorded as
LIVE_CANARY holding an OPEN live deployment (`theta4-live-1`, `lmmsell-live-1`).
Neither has placed a live order since 2026-08-19, because `LIVE_STRATEGIES` — the
runtime allowlist, and the only thing that actually authorises a live order — is
EMPTY. The books are stopped in reality and armed in the record.

That gap is XOS-000012 ("standing a live book down by naming a different one in
LIVE_STRATEGIES is recorded as config drift, not stand-down"), and it is worse than
that title suggests. `_stand_down` refuses any experiment holding an open LIVE
deployment, deliberately, so neither book can be retired while the record says it is
armed. `RETIRE_ON_GATE_FAIL` cannot help either: both keep gates read BLOCKED_DATA,
never FAIL, because their clauses address `deployment_kind="paper"` while every
metric they name is defined only at `"live"` (XOS-000025, XOS-000026 — the evaluator
says so verbatim and names the remedy). And nothing else in the transport vocabulary
ends a live deployment. The ordering is circular: the record cannot be corrected
because the record is wrong.

This repair breaks that circle and does nothing else.

WHY A REPAIR AND NOT A LIFECYCLE VERB
-------------------------------------
Ending these rows moves no lifecycle state, writes no verdict and touches no gate —
the definition of `REPAIR_LINEAGE`. It is emphatically NOT a real-money act: it
authorises nothing, and it cannot stop anything either, because the thing that
stopped these books (an empty `LIVE_STRATEGIES`) already happened through the
audited runtime path. All it does is make the record say what the runtime has
believed for weeks. `close_epoch` removes no evidence — metric scopes read every
deployment in an epoch, ended or not — so the 628 live orders behind these two books
stay exactly as readable as they are today.

Retiring the experiments is a SEPARATE, later act (`STAND_DOWN` → RETIRED, which
carries an operator's recorded judgement and closes the epoch). This package
deliberately cannot do it.

WHAT IT REFUSES
---------------
Every precondition is CHECKED, never assumed, because a repair that half-applies to
a state it does not recognise is worse than no repair:

  * the experiment exists and is LIVE_CANARY;
  * the named deployment exists, is `kind="live"`, is OPEN, and belongs to THAT
    experiment's latest version;
  * its epoch is OPEN (a live deployment stranded on a closed epoch is the
    XOS-000011 shape and a different repair);
  * **the runtime allowlist does not carry the book's tags.** This is the load-
    bearing check. If `LIVE_STRATEGIES` has been repopulated since this was written,
    the book may be trading again and its record is then CORRECT — so the repair
    refuses rather than closing a deployment that is genuinely live;
  * **no live order exists after `LAST_LIVE_ORDER`.** Same reason, measured from the
    data rather than from config: a newer order means the book came back.

It is idempotent — a deployment already ended is reported as `already_repaired`
rather than re-ended, so a second run cannot move an `ended_at` that a first run set.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import LiveOrder
from . import read, service
from .lifecycle import DeploymentKind, LifecycleState
from .models import Experiment, ExperimentDeployment, ExperimentEpoch, ExperimentVersion

#: The live deployments this repair was reviewed against, and the tags each carries.
#: Named as literals so the repair can never be pointed at a different book by an
#: envelope — the transport names the PACKAGE, and the package names these.
TARGETS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("theta4-fat-tail", "theta4-live-1", ("theta4",)),
    ("mmsell-scheduled-settle-live", "lmmsell-live-1", ("Lmmsell10", "Lmmsell8")),
)

#: Measured in production 2026-09-06: the newest `live_orders.created_at` across
#: every tag above is 2026-08-19. A live order after this instant means a book
#: resumed and this repair must not run.
LAST_LIVE_ORDER = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)


def _refuse(msg: str):
    raise service.ExperimentOsError(f"dark-live-canary repair refused: {msg}")


def _assert_runtime_is_stopped(session) -> None:
    """Refuse unless BOTH the config allowlist and the order history agree the books
    are stopped. Either one alone can lie: config is current but says nothing about
    what already traded, and history is measured but says nothing about what is
    armed right now."""
    try:
        from ..config import Settings

        allowlist = [p.lower() for p in Settings().live_strategy_list]
    except Exception as exc:  # configuration must be READABLE, or we know nothing
        _refuse(
            "could not read the runtime allowlist to verify the books are stopped: "
            f"{exc!r}"
        )

    for _, deployment_key, tags in TARGETS:
        for tag in tags:
            # LIVE_STRATEGIES matches by PREFIX (config.py), so a prefix of the tag
            # arms it just as surely as the tag itself.
            hit = [p for p in allowlist if tag.lower().startswith(p)]
            if hit:
                _refuse(
                    f"{tag!r} is armed by the runtime allowlist (matched {hit}); "
                    f"{deployment_key!r} may be trading and its OPEN record is then "
                    "correct — stand the book down through Live Ops first"
                )

    newest = session.scalar(
        select(func.max(LiveOrder.created_at)).where(
            LiveOrder.strategy.in_([t for _, _, tags in TARGETS for t in tags])
        )
    )
    if newest is not None:
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        if newest > LAST_LIVE_ORDER:
            _refuse(
                f"a live order exists at {newest.isoformat()}, after the reviewed "
                f"cutoff {LAST_LIVE_ORDER.isoformat()} — a book resumed and this "
                "repair is out of date"
            )


def repair(session, *, actor: str, now: datetime | None = None) -> dict:
    """End the two dark live deployments. Moves no lifecycle state, touches no gate."""
    del actor
    at = now or datetime.now(timezone.utc)

    _assert_runtime_is_stopped(session)

    ended: list[str] = []
    already: list[str] = []
    for experiment_key, deployment_key, _tags in TARGETS:
        experiment = session.scalar(
            select(Experiment).where(Experiment.key == experiment_key)
        )
        if experiment is None:
            _refuse(f"experiment {experiment_key!r} is not registered")
        if experiment.state != LifecycleState.LIVE_CANARY.value:
            _refuse(
                f"{experiment_key} is {experiment.state!r}, not LIVE_CANARY — the "
                "shape this repair was reviewed against is not the shape production "
                "is in"
            )

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

        epoch = session.get(ExperimentEpoch, deployment.epoch_id)
        version = session.get(ExperimentVersion, epoch.version_id)
        if version.experiment_id != experiment.id:
            _refuse(
                f"{deployment_key!r} belongs to a different experiment — refusing "
                "to touch it"
            )
        latest = read.latest_version(session, experiment)
        if latest is None or latest.id != version.id:
            _refuse(
                f"{deployment_key!r} sits on v{version.version}, which is not "
                f"{experiment_key}'s latest version — refusing"
            )

        if deployment.ended_at is not None:
            already.append(deployment_key)
            continue

        if epoch.ended_at is not None:
            _refuse(
                f"epoch {epoch.epoch_number} of {experiment_key} is already CLOSED "
                f"while {deployment_key!r} is open — that is the XOS-000011 shape "
                "and a different repair"
            )

        deployment.ended_at = at
        ended.append(deployment_key)

    if not ended:
        return {
            "kind": "repair",
            "already_repaired": True,
            "ended": [],
            "already_ended": sorted(already),
        }
    return {
        "kind": "repair",
        "already_repaired": False,
        "ended": sorted(ended),
        "already_ended": sorted(already),
        "ended_at": at.isoformat(),
    }
