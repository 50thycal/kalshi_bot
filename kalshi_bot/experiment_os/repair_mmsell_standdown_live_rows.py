"""MMSELL close-out record repair: end the seven LIVE deployment rows of the
stood-down MMSELL canaries, so the experiments can be retired.

WHAT IS WRONG
-------------
Seven mmsell experiments are recorded as LIVE_CANARY holding an OPEN live
deployment, but none of their live tags is in `LIVE_STRATEGIES` — the runtime
allowlist, and the only thing that authorises a live order. Each has carried an
open `EXPERIMENT_EXECUTION_STOOD_DOWN` integrity event since its tag left the
allowlist. The books are stopped in reality and armed in the record:

  ==================================  ========================  ==========  ==========================
  experiment                          live deployment           live tag    last live order (measured)
  ==================================  ========================  ==========  ==========================
  mmsell-price-ceiling                mmsell-ceiling-live-1     Cmmsell10   2026-08-31 13:31:52Z
  mmsell-price-ceiling-capacity       mmsell-capacity-live-1    Dmmsell10   2026-09-04 06:20:58Z
  mmsell-price-ceiling-contest-cap    mmsell-contestcap-live-2  Fmmsell10   2026-10-04 02:15:04Z
  mmsell-price-ceiling-size-split     mmsell-sizesplit-live-2   Jmmsell10   2026-10-11 15:08:01Z
  mmsell-price-ceiling-cell-size      mmsell-cellsize-live-1    Kmmsell10   2026-10-30 13:32:14Z
  mmsell-win-set                      mmsell-winset-live-1      Nmmsell10   2026-10-27 07:34:58Z
  mmsell-win-set-2                    mmsell-winset2-live-1     Vmmsell10   2026-10-30 13:32:15Z
  ==================================  ========================  ==========  ==========================

(measured in production 2026-11-01 22:19Z, ops result `lo-mmclose-q1b`; the
allowlist read `Xmmsell10,Alimm1,Ymmsell10` in `lo-mmclose-env1`.)

The owner (Calvin, 2026-11-01) decided to retire all seven. `STAND_DOWN` refuses
any experiment holding an open LIVE deployment, deliberately, and nothing else in
the transport vocabulary ends one — the circle `repair_dark_live_canaries`
(XOS-000012) broke for two earlier books. This is the same repair for these seven,
and it does nothing else.

WHY A REPAIR AND NOT A LIFECYCLE VERB
-------------------------------------
Ending these rows moves no lifecycle state, writes no verdict, touches no gate and
closes no epoch — the definition of `REPAIR_LINEAGE`. It authorises nothing, and it
stops nothing that was not already stopped through the audited runtime path (the
allowlist). Retiring is a SEPARATE act (`STAND_DOWN` → RETIRED, attributed to the
owner); this package cannot do it.

Side effects, stated so nobody has to rediscover them:

  * **Each live tag loses its deployment arm**, so its paper entries (the live tags
    keep writing `paper_trades` while stood down) become `LineageBlocked` on the
    next cycle. The mmsell tracker drops a refused book for the cycle and the
    others continue (`mmsell/tracker.py`, XOS-000011). That is the intended end
    state of a retirement, reached one step early.
  * **No open position is stranded.** mmsell books are net-SHORT yes and
    `open_live_positions` returns net-long positions only, so `manage_exits` never
    places an exit for them; Kalshi cash-settles whatever is still open. Nothing
    here needs an admissible tag to unwind.
  * **No evidence is removed.** Metric scopes read every deployment in an epoch,
    ended or not, and P-SLOW v2 (DEC-040) reads `live_orders`/`fills` for
    `Kmmsell10` and `Vmmsell10` directly — those rows are untouched.
  * **The drift check stops judging these books.** `runtime_config_check` reads
    OPEN live deployments only, so once a row is ended its `MMSELL_VARIANTS` entry
    can be removed without recreating drift #28 (WS-027). Removing it is a separate
    Live Ops `env` act.
  * **The paper twins (`*_pt3`/`*_pt4`) stay open** — they are `paper_twin` rows,
    not `live` ones, and RETIRED's epoch close ends them with everything else.

WHAT IT REFUSES
---------------
Every precondition is CHECKED, never assumed:

  * the experiment exists and is LIVE_CANARY;
  * the named deployment exists, is `kind="live"`, is OPEN, and belongs to THAT
    experiment's latest version;
  * its epoch is OPEN (the XOS-000011 shape is a different repair);
  * **the runtime allowlist does not carry the book's tag** (prefix match, as the
    allowlist itself matches). If it does, the book may be trading again, its OPEN
    record is then correct, and the whole repair refuses;
  * **no live order exists for a tag after its own measured last order.** Per tag,
    not one shared cutoff: `Nmmsell10` stopped three days before `Kmmsell10`, and a
    shared cutoff would let a resumed `Nmmsell10` slip through.

It is all-or-nothing (any refusal raises before a row changes) and idempotent: a
deployment already ended is reported as `already_ended`, never re-ended.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from ..models import LiveOrder
from . import read, service
from .lifecycle import DeploymentKind, LifecycleState
from .models import Experiment, ExperimentDeployment, ExperimentEpoch, ExperimentVersion

UTC = timezone.utc

#: (experiment, live deployment, live tag, newest live order measured for that tag).
#: Literals, so an envelope can name only the PACKAGE and never point it at another
#: book. Each cutoff is the measured `max(live_orders.created_at)` rounded UP to the
#: next whole second.
TARGETS: tuple[tuple[str, str, str, datetime], ...] = (
    ("mmsell-price-ceiling", "mmsell-ceiling-live-1", "Cmmsell10",
     datetime(2026, 8, 31, 13, 31, 53, tzinfo=UTC)),
    ("mmsell-price-ceiling-capacity", "mmsell-capacity-live-1", "Dmmsell10",
     datetime(2026, 9, 4, 6, 20, 59, tzinfo=UTC)),
    ("mmsell-price-ceiling-contest-cap", "mmsell-contestcap-live-2", "Fmmsell10",
     datetime(2026, 10, 4, 2, 15, 5, tzinfo=UTC)),
    ("mmsell-price-ceiling-size-split", "mmsell-sizesplit-live-2", "Jmmsell10",
     datetime(2026, 10, 11, 15, 8, 2, tzinfo=UTC)),
    ("mmsell-price-ceiling-cell-size", "mmsell-cellsize-live-1", "Kmmsell10",
     datetime(2026, 10, 30, 13, 32, 15, tzinfo=UTC)),
    ("mmsell-win-set", "mmsell-winset-live-1", "Nmmsell10",
     datetime(2026, 10, 27, 7, 34, 59, tzinfo=UTC)),
    ("mmsell-win-set-2", "mmsell-winset2-live-1", "Vmmsell10",
     datetime(2026, 10, 30, 13, 32, 16, tzinfo=UTC)),
)

#: Books this package must never reach — the running successors and their partners.
#: Asserted against TARGETS at import so an edit cannot quietly add one.
NEVER_TOUCH_TAGS = frozenset({"Xmmsell10", "Ymmsell10", "Smmsell10", "Zmmsell10"})
assert not NEVER_TOUCH_TAGS & {t for _, _, t, _ in TARGETS}


def _refuse(msg: str):
    raise service.ExperimentOsError(f"mmsell stand-down live-row repair refused: {msg}")


def _assert_runtime_is_stopped(session) -> None:
    """Refuse unless BOTH the allowlist and the order history agree every book is
    stopped. Config is current but says nothing about what traded; history is
    measured but says nothing about what is armed right now."""
    try:
        from ..config import get_settings

        allowlist = [p.lower() for p in get_settings().live_strategy_list]
    except Exception as exc:  # the allowlist must be READABLE, or we know nothing
        _refuse(
            "could not read the runtime allowlist to verify the books are stopped: "
            f"{exc!r}"
        )

    for _, deployment_key, tag, _cutoff in TARGETS:
        hit = [p for p in allowlist if tag.lower().startswith(p)]
        if hit:
            _refuse(
                f"{tag!r} is armed by the runtime allowlist (matched {hit}); "
                f"{deployment_key!r} may be trading and its OPEN record is then "
                "correct — stand the book down through Live Ops first"
            )

    for _, _deployment_key, tag, cutoff in TARGETS:
        newest = session.scalar(
            select(func.max(LiveOrder.created_at)).where(LiveOrder.strategy == tag)
        )
        if newest is None:
            continue
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        if newest > cutoff:
            _refuse(
                f"a live order for {tag} exists at {newest.isoformat()}, after its "
                f"reviewed last order {cutoff.isoformat()} — the book resumed and "
                "this repair is out of date"
            )


def repair(session, *, actor: str, now: datetime | None = None) -> dict:
    """End the seven stood-down live deployment rows. Moves no lifecycle state,
    closes no epoch, touches no gate."""
    del actor
    at = now or datetime.now(UTC)

    _assert_runtime_is_stopped(session)

    # Validate EVERY target before changing ANY row: a repair that half-applies to
    # a state it does not recognise is worse than no repair.
    to_end: list[ExperimentDeployment] = []
    already: list[str] = []
    for experiment_key, deployment_key, _tag, _cutoff in TARGETS:
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
        to_end.append(deployment)

    for deployment in to_end:
        deployment.ended_at = at
    ended = sorted(d.deployment_key for d in to_end)

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
        "ended": ended,
        "already_ended": sorted(already),
        "ended_at": at.isoformat(),
    }
