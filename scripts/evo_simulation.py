"""Run the deterministic multi-generation evolutionary simulation locally.

    python scripts/evo_simulation.py --seed 42 --cohorts 5
    python scripts/evo_simulation.py --seed 42 --cohorts 5 --verify-determinism

Runs the REAL evo system (cohorts, budgets, listeners, strategies, paper engine,
fitness, retirement, reproduction, wildcards) against a synthetic seeded market
with scripted behavior profiles — including the adversarial roster — on an
in-memory sqlite database. Same seed => identical results. Costs $0 (no LLM)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalshi_bot.evo.simulation import Simulation  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260718)
    ap.add_argument("--cohorts", type=int, default=5)
    ap.add_argument("--cycles-per-day", type=int, default=4)
    ap.add_argument("--verify-determinism", action="store_true",
                    help="run twice and require identical outcomes")
    args = ap.parse_args()

    t0 = time.time()
    result = Simulation(seed=args.seed, cohorts=args.cohorts,
                        cycles_per_day=args.cycles_per_day).run()
    elapsed = time.time() - t0

    if args.verify_determinism:
        result2 = Simulation(seed=args.seed, cohorts=args.cohorts,
                             cycles_per_day=args.cycles_per_day).run()
        same = result["determinism_digest"] == result2["determinism_digest"]
        print(f"determinism check: {'IDENTICAL' if same else 'DIVERGED'}")
        if not same:
            return 1

    print(f"\nsimulation complete in {elapsed:.1f}s (seed {args.seed}, "
          f"{args.cohorts} cohorts)")
    printable = {k: v for k, v in result.items()
                 if k not in ("transitions", "determinism_digest")}
    print(json.dumps(printable, indent=2, default=str))
    print("\ntransition summary:")
    for t in result["transitions"]:
        print(f"  cohort {t['cohort']}: retired {len(t['retired'])}, "
              f"children {len(t['children'])}, "
              f"wildcard {'YES' if t['wildcard'] else 'no'}"
              f" -> cohort {t['next_cohort']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
