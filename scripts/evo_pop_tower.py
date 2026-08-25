"""Ops-channel Evo Control Tower for the population layer (read-only).

Runs through `scripts/ops_runner.py` against the production database:

    {"type": "script", "name": "evo_pop_tower", "args": ["--program", "proving-1"], "id": "..."}
    {"type": "script", "name": "evo_pop_tower", "args": ["--list"], "id": "..."}
    {"type": "script", "name": "evo_pop_tower",
     "args": ["--program", "proving-1", "--explain", "agent-017"], "id": "..."}

Every path here is a SELECT. The ops channel is read-only against Postgres by design and
this script does not change that: it renders `evo/population/control_tower.py`, which
imports no service and opens no transaction.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo.population import control_tower
from kalshi_bot.evo.population import findings as findings_mod
from kalshi_bot.evo.population.models import EvoProgram


def _session():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        raise SystemExit(2)
    engine = create_engine(url, future=True, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evo_pop_tower")
    parser.add_argument("--program", default=None, help="program key")
    parser.add_argument("--generation", type=int, default=None)
    parser.add_argument("--explain", default=None, help="candidate label or uuid")
    parser.add_argument("--lineage", action="store_true")
    parser.add_argument("--findings", action="store_true")
    parser.add_argument("--list", action="store_true", help="list programs")
    parser.add_argument("--top", type=int, default=8)
    args = parser.parse_args(argv)

    session = _session()
    try:
        programs = list(
            session.execute(select(EvoProgram).order_by(EvoProgram.id)).scalars()
        )
        if args.list or not args.program:
            if not programs:
                print("no evo population programs exist yet")
                return 0
            print(f"{'KEY':<24} {'STATUS':<10} {'MODE':<10} {'DATASET':<24} NAME")
            for p in programs:
                print(
                    f"{p.key:<24} {p.status:<10} {p.mode:<10} {p.dataset:<24} {p.name}"
                )
            return 0

        program = next((p for p in programs if p.key == args.program), None)
        if program is None:
            print(
                f"no program {args.program!r} (have: {[p.key for p in programs]})",
                file=sys.stderr,
            )
            return 2

        if args.explain:
            print(
                control_tower.explain_candidate(
                    session, program=program, label_or_uuid=args.explain
                )
            )
            return 0
        if args.lineage:
            print(control_tower.lineage_tree(session, program=program))
            return 0
        if args.findings:
            rows = findings_mod.open_findings(session, program_id=program.id)
            if not rows:
                print("no open findings")
                return 0
            for row in rows:
                print(f"#{row.id} [{row.severity}] {row.kind}: {row.title}")
                print(f"      route: {row.route_to}   status: {row.status}")
            return 0

        data = control_tower.collect(
            session, program=program, generation_number=args.generation
        )
        print(control_tower.render(data, top=args.top))
        return 0
    finally:
        session.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
