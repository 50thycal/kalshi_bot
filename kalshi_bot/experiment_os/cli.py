"""Read-only CLI to inspect Experiment OS state.

    python -m kalshi_bot.experiment_os.cli list [--state PAPER] [--legacy/--native]
    python -m kalshi_bot.experiment_os.cli show <experiment-key>
    python -m kalshi_bot.experiment_os.cli transitions <experiment-key>
    python -m kalshi_bot.experiment_os.cli platform [review <COMPONENT:version>]
    python -m kalshi_bot.experiment_os.cli tag <strategy-tag>
    python -m kalshi_bot.experiment_os.cli scoreboard [key] [--evaluate]

Connects with DATABASE_URL_RO when set (preferred), else DATABASE_URL. Every command
is a pure read — this tool can never move an experiment; `scoreboard --evaluate`
runs the gate evaluator in DRY-RUN mode (persist=False), so even a verdict shown
here writes nothing. For the ops channel there is a self-contained equivalent:
`{"type": "script", "name": "experiment_os_status"}`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..config import normalize_database_url
from . import read
from .models import PlatformComponent, PlatformRevision
from .service import get_active_platform_snapshot


def _connect() -> Session:
    url = normalize_database_url(
        os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL")
    )
    if not url:
        print("set DATABASE_URL_RO or DATABASE_URL", file=sys.stderr)
        raise SystemExit(2)
    return Session(create_engine(url, future=True))


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _table(headers: list[str], rows: list[list]) -> str:
    cells = [[_fmt(c) for c in row] for row in rows]
    widths = [
        max(len(h), *(len(r[i]) for r in cells)) if cells else len(h)
        for i, h in enumerate(headers)
    ]
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=False))]
    out.append("  ".join("-" * w for w in widths))
    out.extend("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=False)) for row in cells)
    return "\n".join(out)


def cmd_list(session: Session, args) -> int:
    legacy = True if args.legacy else (False if args.native else None)
    exps = read.list_experiments(session, state=args.state, legacy=legacy)
    if not exps:
        print("no experiments recorded")
        return 0
    rows = []
    for e in exps:
        ver = read.latest_version(session, e)
        rows.append(
            [
                e.key,
                e.state,
                e.origin,
                e.family,
                f"v{ver.version}" if ver else "-",
                e.legacy_class or "native",
                e.migration_integrity,
            ]
        )
    print(_table(["key", "state", "origin", "family", "version", "class", "integrity"], rows))
    return 0


def cmd_show(session: Session, args) -> int:
    exp = read.get_experiment(session, args.key)
    if exp is None:
        print(f"no experiment {args.key!r}", file=sys.stderr)
        return 1
    print(json.dumps(read.experiment_tree(session, exp), indent=2, default=str))
    return 0


def cmd_transitions(session: Session, args) -> int:
    exp = read.get_experiment(session, args.key)
    if exp is None:
        print(f"no experiment {args.key!r}", file=sys.stderr)
        return 1
    rows = [
        [t.occurred_at, t.from_state or "(created)", t.to_state, t.actor, t.approved_by, t.reason]
        for t in read.transitions_for(session, exp)
    ]
    print(_table(["occurred_at", "from", "to", "actor", "approved_by", "reason"], rows))
    return 0


def cmd_platform(session: Session, args) -> int:
    # `platform review <COMPONENT:version|id>` (or just `platform <ref>`) prints
    # the one canonical change-impact review for a revision.
    ref = [a for a in (args.args or []) if a != "review"]
    if ref:
        from . import platform_impact

        revision = platform_impact.get_revision(session, ref[0])
        if revision is None:
            print(f"no platform revision {ref[0]!r} (use COMPONENT:version or id)",
                  file=sys.stderr)
            return 1
        print(json.dumps(platform_impact.revision_review(session, revision),
                         indent=2, default=str))
        return 0
    comps = session.scalars(
        select(PlatformComponent).order_by(PlatformComponent.key)
    ).all()
    if not comps:
        print("no platform components registered")
        return 0
    rows = []
    for comp in comps:
        revs = session.scalars(
            select(PlatformRevision)
            .where(PlatformRevision.component_id == comp.id)
            .order_by(PlatformRevision.created_at)
        ).all()
        active = next((r for r in revs if r.status == "active"), None)
        rows.append(
            [
                comp.key,
                active.version if active else "(none active)",
                active.activated_at if active else None,
                len(revs),
            ]
        )
    print(_table(["component", "active revision", "activated_at", "revisions"], rows))
    snap = get_active_platform_snapshot(session)
    if snap is None:
        print("\nactive snapshot: none (incomplete registry or not yet snapshotted)")
    else:
        print(f"\nactive snapshot: id={snap.id} fingerprint={snap.fingerprint[:16]}…")
    return 0


def cmd_tag(session: Session, args) -> int:
    rows = read.strategy_tag_lineage(session, args.tag)
    if not rows:
        print(f"strategy tag {args.tag!r} is not mapped to any experiment deployment")
        return 0
    print(
        _table(
            ["experiment", "state", "ver", "arm", "role", "epoch", "deployment", "stage", "kind"],
            [
                [
                    r["experiment_key"],
                    r["experiment_state"],
                    r["version"],
                    r["arm_key"],
                    r["arm_role"],
                    r["epoch_number"],
                    r["deployment_key"],
                    r["deployment_stage"],
                    r["deployment_kind"],
                ]
                for r in rows
            ],
        )
    )
    return 0


def cmd_scoreboard(session: Session, args) -> int:
    if args.key:
        exp = read.get_experiment(session, args.key)
        if exp is None:
            print(f"no experiment {args.key!r}", file=sys.stderr)
            return 1
        exps = [exp]
    else:
        exps = read.active_experiments(session)
    if not exps:
        print("no active experiments recorded")
        return 0
    for exp in exps:
        board = read.experiment_scoreboard(session, exp)
        head = f"{board['key']}  [{board['state']}]"
        if board.get("version") is not None:
            head += (
                f"  v{board['version']} epoch {board['epoch']}  "
                f"snapshot {board['platform_snapshot']}…  window {board['window'][0]} →"
            )
        print(head)
        if board.get("note"):
            print(f"  ({board['note']})")
        if board["arms"]:
            print(
                _table(
                    ["arm", "role", "tags", "settled", "¢/trade", "win%", "open", "entries"],
                    [
                        [
                            a["arm"], a["role"], ",".join(a["tags"]) or "-",
                            a["settled_trades"], a["pnl_cents_per_trade"],
                            a["win_rate_pct"], a["open_trades"], a["entries"],
                        ]
                        for a in board["arms"]
                    ],
                )
            )
        for g in board["gates"]:
            latest = g["latest_result"]
            line = (
                f"  gate {g['gate_key']} [{g['kind']}"
                + (f" {g['from_state']}→{g['to_state']}" if g["from_state"] else "")
                + "]"
            )
            line += (
                f"  latest: {latest['verdict']} @ {latest['computed_at']}"
                if latest
                else "  latest: (never evaluated)"
            )
            print(line)
            if latest and latest.get("explanation"):
                print(f"    {latest['explanation'][:160]}")
        if args.evaluate:
            from .evaluator import evaluate_gate  # dry-run only — never persists here

            ver = read.latest_version(session, exp)
            for gate in read.gates_for(session, ver) if ver else []:
                if gate.evidence_started_at is None:
                    continue
                try:
                    outcome = evaluate_gate(session, gate, persist=False)
                except Exception as exc:  # noqa: BLE001 — a dry-run must not abort the report
                    print(f"  dry-run {gate.gate_key}: evaluation error: {exc}")
                    continue
                print(f"  dry-run {gate.gate_key}: {outcome.verdict} — "
                      f"{outcome.explanation[:200]}")
        print()
    return 0


def cmd_control_tower(session: Session, args) -> int:
    """The Experiment Control Tower report — read-only by construction."""
    from .control_tower import build_report, render, report_text

    if args.json:
        import dataclasses

        rep = build_report(session, evaluate=not args.no_evaluate)
        print(json.dumps(dataclasses.asdict(rep), indent=2, default=str))
        return 0
    _ = (build_report, render)
    print(report_text(session, evaluate=not args.no_evaluate))
    return 0


def cmd_enforcement(session: Session, args) -> int:
    from .enforcement import enforcement_report

    print(json.dumps(enforcement_report(session), indent=2, default=str))
    return 0


def cmd_readiness(session: Session, args) -> int:
    from .enforcement import production_readiness

    report = production_readiness(session)
    print(json.dumps(report, indent=2, default=str))
    print(
        "\nREADY for NEW_ONLY"
        if report["ok"]
        else "\nNOT READY — fix the failing checks before recording a cutover"
    )
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="experiment_os", description="Inspect Experiment OS state (read-only)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list experiments")
    p_list.add_argument("--state", default=None, help="filter by lifecycle state")
    p_list.add_argument("--legacy", action="store_true", help="imported legacy only")
    p_list.add_argument("--native", action="store_true", help="native new-system only")
    p_list.set_defaults(fn=cmd_list)

    p_show = sub.add_parser("show", help="full experiment tree as JSON")
    p_show.add_argument("key")
    p_show.set_defaults(fn=cmd_show)

    p_tr = sub.add_parser("transitions", help="lifecycle audit trail")
    p_tr.add_argument("key")
    p_tr.set_defaults(fn=cmd_transitions)

    p_plat = sub.add_parser(
        "platform",
        help="platform components/revisions/snapshot; "
        "`platform review <COMPONENT:version>` prints the change-impact review",
    )
    p_plat.add_argument("args", nargs="*", metavar="[review] [revision]")
    p_plat.set_defaults(fn=cmd_platform)

    p_tag = sub.add_parser("tag", help="strategy tag → experiment lineage")
    p_tag.add_argument("tag")
    p_tag.set_defaults(fn=cmd_tag)

    p_ct = sub.add_parser(
        "control-tower",
        help="the Experiment Control Tower report: every non-terminal experiment "
        "grouped by lifecycle state, integrity first (read-only)",
    )
    p_ct.add_argument("--json", action="store_true", help="structured output")
    p_ct.add_argument(
        "--no-evaluate", action="store_true",
        help="skip the DRY-RUN gate evaluation (faster; shows recorded verdicts only)",
    )
    p_ct.set_defaults(fn=cmd_control_tower)

    p_enf = sub.add_parser(
        "enforcement", help="current mode, cutover, lineage coverage, canary links"
    )
    p_enf.set_defaults(fn=cmd_enforcement)

    p_rdy = sub.add_parser(
        "readiness", help="the mechanical pre-cutover checklist (exit 1 when not ready)"
    )
    p_rdy.set_defaults(fn=cmd_readiness)

    p_sb = sub.add_parser(
        "scoreboard", help="current metrics + gate standing per active experiment"
    )
    p_sb.add_argument("key", nargs="?", default=None)
    p_sb.add_argument(
        "--evaluate", action="store_true",
        help="also run each started gate through the evaluator in dry-run "
        "(nothing is persisted)",
    )
    p_sb.set_defaults(fn=cmd_scoreboard)

    args = parser.parse_args(argv)
    session = _connect()
    try:
        return args.fn(session, args)
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
