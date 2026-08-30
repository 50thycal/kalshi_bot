"""Operator entry point: PAUSE the MARKTANGLE experiment.

Operator decision, 2026-08-30: close MARKTANGLE for now on the run-8 results,
with the intent of possibly picking it up later.

WHY A SCRIPT AND NOT AN OPS REQUEST. The ops channel is read-only against
Postgres by design, and the experiment-command transport's vocabulary is
deliberately narrow — REGISTER_PACKAGE, REPAIR_LINEAGE, ARM_CANARY. None of
them means "move a lifecycle state", and widening that vocabulary so a research
session could pause an experiment would hand every future session a way to move
lifecycle state from an environment variable. So this follows the same shape as
`scripts/mmsell10_canary.py`: reviewed code, run by an operator on their own
writable connection.

WHY PAUSED AND NOT RETIRED. RETIRED is terminal in the state machine, and the
decision was explicitly "for now". PAUSED records `paused_from`, so the
experiment can only ever resume to PROBE — the state it actually stopped in —
or be retired later. Retiring it now would throw away that provenance to save
one command.

WHAT THIS DOES NOT DO. It changes no verdict, no gate, no version and no epoch.
The recorded HOLD stands, the frozen contract stays frozen, and the five run
logs stay exactly as written. Pausing is a statement about our attention, not
about the evidence.

    # inspect, writing nothing (the default):
    DATABASE_URL=postgresql://... python scripts/marktangle_pause.py
    # execute, on a WRITABLE connection:
    DATABASE_URL=postgresql://... python scripts/marktangle_pause.py --execute
"""

from __future__ import annotations

import argparse
import os
import sys

REASON = (
    "Operator decision 2026-08-30: closed for now on the run-8 results. Verdict "
    "is the pre-registered HOLD — no family reached the 100-entry holdout floor. "
    "Daily crypto threshold families were refuted as a market TYPE (near-total "
    "persistence on a coin-flip marginal frequency); the coin-like families "
    "(sports totals, weather buckets) remain untested at n=42-88 with holdouts "
    "of 13-27. The binding constraint is Kalshi's history depth, which no "
    "engineering can change — grading those families needs months of forward "
    "collection. Paused rather than retired: the contract, arms, gates and five "
    "run logs stand, and resuming means collecting forward, not rebuilding."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="actually write; omit to inspect only")
    ap.add_argument("--actor", default="research-lab")
    args = ap.parse_args(argv)

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set — this needs an operator's writable "
              "connection.", file=sys.stderr)
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from kalshi_bot.experiment_os import marktangle, service
    from kalshi_bot.experiment_os.lifecycle import LifecycleState
    from kalshi_bot.experiment_os.read import get_experiment

    session = sessionmaker(bind=create_engine(os.environ["DATABASE_URL"]))()
    try:
        exp = get_experiment(session, marktangle.EXPERIMENT_KEY)
        if exp is None:
            print(f"experiment {marktangle.EXPERIMENT_KEY!r} is not registered — "
                  "nothing to pause. (It was never run through REGISTER_PACKAGE.)")
            return 0
        print(f"{exp.key}: currently {exp.state}")
        if exp.state == LifecycleState.PAUSED.value:
            print("already PAUSED — nothing to do.")
            return 0
        if not args.execute:
            print(f"\nwould transition {exp.state} -> PAUSED with reason:\n\n{REASON}\n")
            print("re-run with --execute on a writable connection to apply.")
            return 0
        service.transition_experiment(
            session, exp, LifecycleState.PAUSED, actor=args.actor, reason=REASON,
        )
        session.commit()
        print(f"{exp.key}: PAUSED (paused_from={exp.paused_from_state})")
        return 0
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
