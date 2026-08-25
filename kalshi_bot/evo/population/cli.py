"""`python -m kalshi_bot.evo.population.cli <command>` — drive and read a program.

Read commands (`tower`, `explain`, `lineage`, `findings`, `surface`) open a read-only
session and are safe against the ops channel's read-only database. Write commands
(`create`, `seed`, `advance`, `proving-run`) need a writable database and are refused
against the ops channel by the same mechanism that refuses every other writer: the ops
runner's connection has no write grant.

There is no `arm`, no `promote`, and no `live` — not omitted, but absent by design. A
candidate that earns advancement enters Experiment OS through a session with the
authority to register it.
"""

from __future__ import annotations

import argparse
import json
import sys

from ...db import session_scope
from ..config import EvoSettings
from . import control_tower, service
from . import findings as findings_mod
from . import genome as genome_mod


def _program_or_exit(session, key: str):
    program = service.get_program(session, key)
    if program is None:
        print(f"no program {key!r}", file=sys.stderr)
        raise SystemExit(2)
    return program


def cmd_tower(args) -> int:
    with session_scope() as session:
        program = _program_or_exit(session, args.program)
        data = control_tower.collect(
            session, program=program, generation_number=args.generation
        )
        print(json.dumps(data, indent=2, default=str) if args.json
              else control_tower.render(data, top=args.top))
    return 0


def cmd_explain(args) -> int:
    with session_scope() as session:
        program = _program_or_exit(session, args.program)
        print(
            control_tower.explain_candidate(
                session, program=program, label_or_uuid=args.candidate
            )
        )
    return 0


def cmd_lineage(args) -> int:
    with session_scope() as session:
        program = _program_or_exit(session, args.program)
        print(control_tower.lineage_tree(session, program=program))
    return 0


def cmd_findings(args) -> int:
    with session_scope() as session:
        program = _program_or_exit(session, args.program)
        rows = findings_mod.open_findings(session, program_id=program.id)
        if not rows:
            print("no open findings")
            return 0
        for row in rows:
            print(f"#{row.id} [{row.severity}] {row.kind}: {row.title}")
            print(f"      route: {row.route_to}   status: {row.status}")
            if row.detail:
                print(f"      {row.detail.splitlines()[0]}")
    return 0


def cmd_surface(_args) -> int:
    """Print the mutation surface — the complete list of what evolution may change."""
    for gene in genome_mod.surface_summary():
        rng = gene["range"]
        if gene["kind"] == "set":
            # A set gene has no vocabulary declared here — its members come from the
            # dataset, so only an explicit proposal can move it.
            span = "membership (explicit proposal only)"
        elif gene["kind"] in ("enum", "list_enum"):
            span = "|".join(str(v) for v in rng)
        else:
            span = f"{rng[0]}..{rng[1]} step {gene['step']}"
        flags = []
        if not gene["independent"]:
            flags.append("not blindly mutable")
        if gene["conditional"]:
            flags.append("conditional")
        if gene["optional"]:
            flags.append("optional")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"{gene['path']:<38} {gene['kind']:<6} {span}{suffix}")
    return 0


def cmd_create(args) -> int:
    with session_scope() as session:
        program = service.create_program(
            session,
            key=args.program,
            name=args.name or args.program,
            objective=args.objective,
            dataset=args.dataset,
            cohort_target=args.cohort_target,
            starting_capital_usd=args.capital,
            min_trades_for_evidence=args.min_trades,
            platform_snapshot=args.platform_snapshot,
            rng_seed=args.seed,
        )
        print(f"created program {program.key} (id {program.id})")
    return 0


def cmd_advance(args) -> int:
    settings = EvoSettings()
    with session_scope() as session:
        program = _program_or_exit(session, args.program)
        result = service.advance(
            session,
            settings,
            program=program,
            window_start=args.window_start,
            window_end=args.window_end,
            data_cutoff=args.data_cutoff,
            max_children=args.max_children,
        )
        print(
            f"generation {result.generation.number}: "
            f"{len(result.runs)} runs, {len(result.children)} children, "
            f"{len(result.findings)} findings"
        )
        print(json.dumps(result.summary(), indent=2, default=str))
    return 0


def cmd_proving(args) -> int:
    """Run the built-in proving program end to end against the synthetic corpus."""
    from .proving_run import run_proving

    with session_scope() as session:
        report = run_proving(
            session,
            program_key=args.program,
            generations=args.generations,
            cohort=args.cohort,
        )
    print(report["report"])
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evo-population", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_program(p):
        p.add_argument("--program", required=True, help="program key")
        return p

    p = add_program(sub.add_parser("tower", help="render the Evo Control Tower"))
    p.add_argument("--generation", type=int, default=None)
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tower)

    p = add_program(sub.add_parser("explain", help="explain one candidate's rank"))
    p.add_argument("candidate", help="label (agent-017) or uuid")
    p.set_defaults(func=cmd_explain)

    p = add_program(sub.add_parser("lineage", help="print the family tree"))
    p.set_defaults(func=cmd_lineage)

    p = add_program(sub.add_parser("findings", help="list open findings"))
    p.set_defaults(func=cmd_findings)

    p = sub.add_parser("surface", help="print the allowed mutation surface")
    p.set_defaults(func=cmd_surface)

    p = add_program(sub.add_parser("create", help="create a program"))
    p.add_argument("--name")
    p.add_argument("--objective", required=True)
    p.add_argument("--dataset", required=True)
    p.add_argument("--cohort-target", type=int, default=30)
    p.add_argument("--capital", type=float, default=500.0)
    p.add_argument("--min-trades", type=int, default=30)
    p.add_argument("--platform-snapshot", default=None)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_create)

    p = add_program(sub.add_parser("advance", help="run one generation"))
    p.add_argument("--window-start", required=True)
    p.add_argument("--window-end", required=True)
    p.add_argument("--data-cutoff", default=None)
    p.add_argument("--max-children", type=int, default=None)
    p.set_defaults(func=cmd_advance)

    p = sub.add_parser("proving-run", help="run the synthetic historical proving program")
    p.add_argument("--program", default="proving-1")
    p.add_argument("--generations", type=int, default=3)
    p.add_argument("--cohort", type=int, default=30)
    p.set_defaults(func=cmd_proving)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except service.EvoPopulationError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
