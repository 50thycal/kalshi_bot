"""Read-only CLI to inspect Experiment OS state.

    python -m kalshi_bot.experiment_os.cli list [--state PAPER] [--legacy/--native]
    python -m kalshi_bot.experiment_os.cli show <experiment-key>
    python -m kalshi_bot.experiment_os.cli transitions <experiment-key>
    python -m kalshi_bot.experiment_os.cli platform
    python -m kalshi_bot.experiment_os.cli tag <strategy-tag>

Connects with DATABASE_URL_RO when set (preferred), else DATABASE_URL. Every command
is a pure read — this tool can never move an experiment. For the ops channel there is
a self-contained equivalent: `{"type": "script", "name": "experiment_os_status"}`.
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

    p_plat = sub.add_parser("platform", help="platform components/revisions/snapshot")
    p_plat.set_defaults(fn=cmd_platform)

    p_tag = sub.add_parser("tag", help="strategy tag → experiment lineage")
    p_tag.add_argument("tag")
    p_tag.set_defaults(fn=cmd_tag)

    args = parser.parse_args(argv)
    session = _connect()
    try:
        return args.fn(session, args)
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
