"""XOS-000033: bring `mmsell9` back on a FRESH epoch of `mmsell-price-ceiling` v1.

WHAT BROKE
----------
`mmsell9` is a declared arm of v1 (`arm_key="mmsell9"`, role `secondary`). When
the Stage-1 canary armed off v1 on 2026-08-28, the canary package deliberately
split the two-arm legacy deployment so `mmsell9` kept its own carrier and stayed
admissible while `mmsell10` moved to v2 — `mmsell-ceiling-paper-mmsell9-1`.

Five days later `mmsell10-capacity-successor` ended that carrier as a side
effect. Its registration selects EVERY open deployment of the predecessor with
`kind == "paper"` and ends all of them, reasoning only about the `mmsell10` tag
it needs to reuse (`successor_mmsell10_capacity.py`, "two active deployment arms
on one tag is AMBIGUOUS"). The predecessor held two such deployments; only one
was about `mmsell10`. The successor re-opens a book on `mmsell10` alone, so
nothing took `mmsell9` over.

Measured, not inferred: receipt `mm10cap-register-1` reports
`epoch_started_at = 2026-09-02T00:57:38.273309Z`, byte-identical to
`mmsell-ceiling-paper-mmsell9-1.ended_at`. Same transaction. `mmsell9`'s last
`paper_trades` row is 67 seconds earlier, and the experiment's append-only
transition log has exactly two rows, neither on that date — nobody decided this.

From that instant `mmsell9` had lineage but no ACTIVE deployment arm, so under
`NEW_ONLY` every entry it attempted was refused at the write path while the book
was still constructed from `MMSELL_VARIANTS` every scan cycle. Silent for 9.6
days, on an epoch that was still open.

WHY A NEW EPOCH RATHER THAN RE-REGISTERING ON e1
------------------------------------------------
v1/e1 is still OPEN, so the cheap repair — register a replacement carrier on it,
as `tmmsell-epoch-repair` did — would work mechanically and is WRONG here.

e1's evidence stops on 2026-09-02 and would resume today in the same operating
interval, pooling two samples across a 9.6-day hole that no pre-registration
anticipated. Worse, the hole is not merely a gap in time: e1 is pinned to
platform snapshot 1, and a new epoch pins the snapshot active now. Those are
different worlds, and an epoch exists precisely to stop evidence gathered under
one being read as continuous with the other.

So the boundary is the point. e1 closes at the instant it actually stopped
(`DARK_SINCE`, measured) rather than at "now", so the record carries no interval
during which the epoch was open and nothing was running. e2 opens now and the
book resumes there. The gap between them is the outage, stated.

This is the operator's call, recorded on XOS-000033: keep `mmsell9` — it tests
the type filter AND the price ceiling together, and every successor in this line
(`capacity`, `contest-cap`) varies the price ceiling alone.

WHAT IT DOES, AND WHAT IT REFUSES
---------------------------------
Closes v1/e1 at `DARK_SINCE`, opens v1/e2 now, registers
`mmsell-ceiling-paper-mmsell9-2` there carrying `mmsell9` alone. Nothing else:
no version, no arm, no gate, no verdict, no lifecycle transition, no live
lineage. v1's `paper_to_live_canary` gate started accruing evidence on
2026-07-18 and is immutable; this package does not touch it, and the new epoch is
simply the scope its future reads resolve against.

Every precondition is CHECKED rather than assumed, because a package that
half-applies to a state it does not recognise is worse than none:

  * the experiment exists and is not RETIRED;
  * v1 exists and is FROZEN (an epoch operates an immutable contract);
  * v1 has exactly ONE open epoch and it holds NO open deployments — if something
    is running in e1, this package's whole premise (the book is dark) is false
    and closing the epoch would stop it;
  * the prior carrier exists and ended at EXACTLY `DARK_SINCE` — if production
    did not end it when we believe, the belief is what is wrong;
  * `mmsell9` resolves to no ACTIVE arm anywhere. Two active arms on one tag is
    the ambiguity the resolver refuses outright, so re-registering a tag that is
    somehow live again would stop the book a second way.

Idempotent: a second run finds `mmsell-ceiling-paper-mmsell9-2` already present
and reports `already_restored` rather than opening a third epoch.

It places no order. `mmsell9` is already in production's `MMSELL_VARIANTS` — it
never left — so nothing needs adding there, and nothing here touches that
variable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from . import service
from .lifecycle import LifecycleState
from .models import (
    Experiment,
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentEpoch,
    ExperimentVersion,
)

EXPERIMENT_KEY = "mmsell-price-ceiling"
VERSION_NUMBER = 1
ARM_KEY = "mmsell9"
PAPER_TAG = "mmsell9"
PRIOR_DEPLOYMENT_KEY = "mmsell-ceiling-paper-mmsell9-1"
NEW_DEPLOYMENT_KEY = "mmsell-ceiling-paper-mmsell9-2"

#: The instant `mmsell10-capacity-successor` ended the carrier, read off
#: `experiment_deployments.ended_at` in production on 2026-09-11 and corroborated
#: by receipt `mm10cap-register-1`. e1 is closed at THIS instant rather than at
#: "now" so the epoch and the deployment that was running in it agree, and so the
#: record never shows an open epoch with nothing in it.
DARK_SINCE = datetime(2026, 9, 2, 0, 57, 38, 273309, tzinfo=timezone.utc)

EPOCH_REASON = (
    "mmsell9 went dark 2026-09-02T00:57:38.273309Z when mmsell10-capacity-successor "
    "ended its carrier deployment as a side effect (XOS-000033), and was refused at "
    "the write path for 9.6 days. Evidence restarts here: the e1 sample ended at that "
    "instant, this interval pins the platform snapshot active now rather than e1's, "
    "and the two are not poolable across the gap."
)


def _refuse(msg: str):
    raise service.ExperimentOsError(f"{EXPERIMENT_KEY} mmsell9 restore refused: {msg}")


def _active_arm_rows(session, tag: str) -> list[ExperimentDeploymentArm]:
    """Every arm link for `tag` whose deployment AND epoch are both open.

    Both, because that pair is exactly what the admission resolver requires: a
    deployment left open on a closed epoch does not make a tag admissible, and is
    the XOS-000011 shape rather than a live book.
    """
    rows = []
    for link in session.scalars(
        select(ExperimentDeploymentArm).where(
            ExperimentDeploymentArm.strategy_tag == tag
        )
    ):
        dep = session.get(ExperimentDeployment, link.deployment_id)
        if dep is None or dep.ended_at is not None:
            continue
        epoch = session.get(ExperimentEpoch, dep.epoch_id)
        if epoch is None or epoch.ended_at is not None:
            continue
        rows.append(link)
    return rows


def register(session, *, actor: str, promotion_sample_floor: int | None = None) -> dict:
    """Cut v1/e2 and put `mmsell9` back on it. Registers no contract."""
    del actor  # the receipt records it; the package needs nothing from it
    if promotion_sample_floor is not None:
        _refuse(
            "promotion_sample_floor is meaningless here — this package registers no "
            "gate and may not touch v1's, which started accruing evidence 2026-07-18"
        )

    exp = session.scalar(select(Experiment).where(Experiment.key == EXPERIMENT_KEY))
    if exp is None:
        _refuse(f"experiment {EXPERIMENT_KEY!r} is not registered")
    if exp.state == LifecycleState.RETIRED.value:
        _refuse(f"{EXPERIMENT_KEY} is RETIRED — no new operating interval may open")

    version = session.scalar(
        select(ExperimentVersion).where(
            ExperimentVersion.experiment_id == exp.id,
            ExperimentVersion.version == VERSION_NUMBER,
        )
    )
    if version is None:
        _refuse(f"{EXPERIMENT_KEY} has no version {VERSION_NUMBER}")
    if version.frozen_at is None:
        _refuse(
            f"v{VERSION_NUMBER} is not frozen — an epoch operates an immutable contract"
        )

    # Idempotence BEFORE any precondition that the first run itself invalidates: a
    # completed run leaves e1 closed, which would otherwise read as "no open epoch"
    # and refuse instead of reporting the work already done.
    existing = session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == NEW_DEPLOYMENT_KEY
        )
    )
    if existing is not None:
        epoch = session.get(ExperimentEpoch, existing.epoch_id)
        return {
            "already_restored": True,
            "epoch_number": epoch.epoch_number if epoch else None,
            "deployment": NEW_DEPLOYMENT_KEY,
            "closed_epoch": None,
            "tag": PAPER_TAG,
        }

    prior = session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == PRIOR_DEPLOYMENT_KEY
        )
    )
    if prior is None:
        _refuse(f"the prior carrier {PRIOR_DEPLOYMENT_KEY!r} does not exist")
    if prior.ended_at is None:
        _refuse(
            f"{PRIOR_DEPLOYMENT_KEY!r} is still OPEN — mmsell9 is not dark and this "
            "package has nothing to restore"
        )
    # Normalised before comparing: Postgres hands back an aware datetime and
    # SQLite a naive one, and an aware/naive mismatch would refuse for a reason
    # that has nothing to do with whether the instant is right.
    prior_ended = prior.ended_at
    if prior_ended.tzinfo is None:
        prior_ended = prior_ended.replace(tzinfo=timezone.utc)
    if prior_ended != DARK_SINCE:
        _refuse(
            f"{PRIOR_DEPLOYMENT_KEY!r} ended at {prior_ended.isoformat()}, not the "
            f"reviewed {DARK_SINCE.isoformat()} — production is not the state this was "
            "reviewed against, so the boundary this package would record is wrong"
        )

    open_epochs = list(
        session.scalars(
            select(ExperimentEpoch).where(
                ExperimentEpoch.version_id == version.id,
                ExperimentEpoch.ended_at.is_(None),
            )
        )
    )
    if len(open_epochs) != 1:
        _refuse(
            f"v{VERSION_NUMBER} has {len(open_epochs)} open epochs; expected exactly "
            "one to close"
        )
    old_epoch = open_epochs[0]

    still_running = [
        d.deployment_key
        for d in session.scalars(
            select(ExperimentDeployment).where(
                ExperimentDeployment.epoch_id == old_epoch.id,
                ExperimentDeployment.ended_at.is_(None),
            )
        )
    ]
    if still_running:
        _refuse(
            f"epoch {old_epoch.epoch_number} still runs {sorted(still_running)} — "
            "closing it would end them too (close_epoch cascades), and this package "
            "was reviewed against an epoch where everything had already stopped"
        )

    live_elsewhere = _active_arm_rows(session, PAPER_TAG)
    if live_elsewhere:
        keys = sorted(
            session.get(ExperimentDeployment, r.deployment_id).deployment_key
            for r in live_elsewhere
        )
        _refuse(
            f"{PAPER_TAG!r} already resolves to an active arm on {keys} — registering "
            "a second would put one tag on two active arms, which the resolver refuses "
            "outright and which would stop the book a second way"
        )

    at = service._now()
    service.close_epoch(session, old_epoch, ended_at=DARK_SINCE)
    epoch = service.open_epoch(
        session, version, reason=EPOCH_REASON, started_at=at,
    )
    service.register_deployment(
        session, epoch,
        deployment_key=NEW_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER, kind="paper",
        arms={ARM_KEY: PAPER_TAG},
        started_at=at,
        notes=(
            "carries mmsell9 alone, restoring the arm mmsell10-capacity-successor "
            "ended as a side effect on 2026-09-02 (XOS-000033). Fresh epoch so the "
            "dark interval is a boundary rather than a hole inside one sample."
        ),
    )
    return {
        "already_restored": False,
        "epoch_number": epoch.epoch_number,
        "epoch_started_at": at.isoformat(),
        "deployment": NEW_DEPLOYMENT_KEY,
        "closed_epoch": old_epoch.epoch_number,
        "closed_epoch_ended_at": DARK_SINCE.isoformat(),
        "tag": PAPER_TAG,
    }


__all__ = [
    "ARM_KEY", "DARK_SINCE", "EPOCH_REASON", "EXPERIMENT_KEY", "NEW_DEPLOYMENT_KEY",
    "PAPER_TAG", "PRIOR_DEPLOYMENT_KEY", "VERSION_NUMBER", "register",
]
