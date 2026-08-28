"""CLI to inspect Experiment OS state — and to record gate evaluations.

    python -m kalshi_bot.experiment_os.cli list [--state PAPER] [--legacy/--native]
    python -m kalshi_bot.experiment_os.cli show <experiment-key>
    python -m kalshi_bot.experiment_os.cli transitions <experiment-key>
    python -m kalshi_bot.experiment_os.cli platform [review <COMPONENT:version>]
    python -m kalshi_bot.experiment_os.cli tag <strategy-tag>
    python -m kalshi_bot.experiment_os.cli scoreboard [key] [--evaluate]
    python -m kalshi_bot.experiment_os.cli evaluate-gates [--dry-run]
    python -m kalshi_bot.experiment_os.cli issue list|show|candidates      (read)
    python -m kalshi_bot.experiment_os.cli issue open-candidate <fp>       (write)
    python -m kalshi_bot.experiment_os.cli issue create|triage|assign|...  (write)

Connects with DATABASE_URL_RO when set (preferred), else DATABASE_URL.

Every command here is a pure read EXCEPT `evaluate-gates` and the writing
`issue` subcommands, which persist gate results
(docs/EXPERIMENT_OS_GATE_RESULTS.md) and investigation state
(docs/EXPERIMENT_OS_ISSUES.md) and refuse to run against DATABASE_URL_RO.
No issue subcommand can transition an experiment, alter a gate, change a
verdict, create a Version or Epoch, register a Platform Revision or change
real-money exposure — `issue disposition` RECORDS that one is required. Even that one cannot move an experiment: it records verdicts and
never transitions, promotes or arms anything. `scoreboard --evaluate` runs the
evaluator in DRY-RUN mode (persist=False), so a verdict shown there writes
nothing. For the ops channel there is a self-contained equivalent:
`{"type": "script", "name": "experiment_os_status"}`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

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


def cmd_experiment_command(session: Session, args) -> int:
    """Receipts for the worker-side experiment-lifecycle transport.

    A READ, and only a read. It reports what a command DID; it cannot execute,
    retry or clear one. The executor runs on the worker and nowhere else, a
    terminal receipt is final, and a retry is a NEW `command_id` submitted through
    the ops branch. Output is metadata — the submitted payload is never printed,
    because this channel's own results are public.
    """
    from . import experiment_commands

    if args.action == "show":
        row = experiment_commands.receipt(session, args.command_id)
        if row is None:
            print(f"no experiment command {args.command_id!r}", file=sys.stderr)
            return 1
        print(json.dumps(row, indent=2, default=str))
        return 0
    rows = experiment_commands.receipts(session, limit=args.limit)
    if not rows:
        print("no experiment-command receipts")
        print(f"registered packages: {experiment_commands.package_names()}")
        return 0
    print(json.dumps(rows, indent=2, default=str))
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


def cmd_metric(session: Session, args) -> int:
    """Compute one canonical metric at an EXPLICIT scope, and show its provenance.

    A pure read, and the only way to exercise a provider against production before
    any gate depends on it. Without this a new provider can only be verified once
    some registered gate happens to use it — which is exactly backwards: the point
    of verifying is to find out whether a gate SHOULD depend on it.

    `deployment_kind` is required and never inferred. Asking for a live metric at
    `--kind paper` is a legitimate question with a real answer (MISSING, with the
    addressing mismatch named), and silently answering a different question would
    defeat the reason to run this at all.

    The window is the EPOCH's, `[epoch.started_at, now]`. A gate's window is
    narrower — `[max(epoch start, gate evidence_started_at), …]` — so a probe
    value is not automatically the number a gate would see, and the window is
    printed so the difference is visible rather than assumed. This command
    deliberately takes no gate: it answers "what does this provider compute over
    this scope", which is the question you ask *before* a gate exists."""
    from .evaluator import _arm_scope
    from .metrics import REGISTRY, compute_metric

    if args.metric not in REGISTRY:
        print(f"unknown metric {args.metric!r}", file=sys.stderr)
        return 1
    exp = read.get_experiment(session, args.key)
    if exp is None:
        print(f"no experiment {args.key!r}", file=sys.stderr)
        return 1
    ver = read.latest_version(session, exp)
    epoch = read.open_epoch_for(session, ver) if ver else None
    if ver is None or epoch is None:
        print("experiment has no version/open epoch", file=sys.stderr)
        return 1
    from .models import PlatformSnapshot

    snap = session.get(PlatformSnapshot, epoch.platform_snapshot_id)
    now = datetime.now(timezone.utc)
    end = min(now, epoch.ended_at) if epoch.ended_at is not None else now
    scope = _arm_scope(
        session, exp, ver, epoch, args.arm, args.kind,
        (epoch.started_at, end), snap.fingerprint if snap else "",
    )
    mv = compute_metric(session, args.metric, scope)
    print(f"{args.metric}  @  {scope.label()}  kind={args.kind}")
    print(f"  tags:        {', '.join(scope.strategy_tags) or '(none)'}")
    print(f"  deployments: {', '.join(scope.deployment_keys) or '(none)'}")
    print(f"  window:      {scope.window_start} -> {scope.window_end}")
    print(f"  value:       {mv.value}  ({mv.unit})   n={mv.n}"
          + ("   MISSING" if mv.missing else ""))
    if mv.reason:
        print(f"  reason:      {mv.reason}")
    for k, v in sorted((mv.provenance or {}).items()):
        print(f"    {k}: {v}")
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


def cmd_evaluate_gates(session: Session, args) -> int:
    """Evaluate eligible gates and PERSIST the results (unless --dry-run).

    This is the one operator-accessible write path. It records verdicts; it can
    never promote, transition, or modify a gate."""
    from .gate_runner import NotAuthorized, run_evaluation_cycle

    dry = args.dry_run
    if not dry:
        # _connect() prefers DATABASE_URL_RO, so if it is set this session is the
        # read-only one — including every ops-channel run. Fail loudly here rather
        # than deep in a Postgres permission error halfway through a cycle.
        if os.environ.get("DATABASE_URL_RO"):
            print(
                "refusing to persist over DATABASE_URL_RO: this is the read-only "
                "connection (the ops channel always is). Run where only a writable "
                "DATABASE_URL is set, or pass --dry-run.",
                file=sys.stderr,
            )
            return 2
        print("MODE: WRITE — persisting gate results (no promotion, ever)\n")
    else:
        print("MODE: DRY RUN — nothing will be written\n")
    try:
        summary = run_evaluation_cycle(
            session, settings=None, dry_run=dry,
            only_experiment=args.experiment, only_gate=args.gate,
        )
    except NotAuthorized as exc:
        print(f"not authorized: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, default=str))
    print(
        f"\nconsidered={summary['considered']} evaluated={summary['evaluated']} "
        f"{'would_write' if dry else 'written'}="
        f"{len(summary['written_rows']) if dry else summary['written']} "
        f"skipped_unchanged={summary['skipped_unchanged']} "
        f"errors={summary['errors']}"
    )
    print(f"verdicts: {summary['verdicts']}")
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


# ---------------------------------------------------------------------------
# Investigation / issue workflow (docs/EXPERIMENT_OS_ISSUES.md)
# ---------------------------------------------------------------------------
#
# `issue list|show|candidates` are pure reads and are allowlisted on the ops
# channel. Every other issue subcommand WRITES, and follows the same rule as
# `evaluate-gates`: it refuses to run when DATABASE_URL_RO is set, which is the
# read-only connection the ops channel always uses. The worker-only write path
# is unchanged — the ops channel does not gain a way to mutate anything.
#
# No issue subcommand can transition an experiment, alter a gate, change a
# verdict, open an epoch, create a Version, register a Platform Revision, arm a
# canary or change exposure. `issue disposition` RECORDS that one of those is
# required; performing it stays where it already lives.


def _issue_write_guard() -> int | None:
    """Refuse a write over the read-only connection, loudly and early."""
    if os.environ.get("DATABASE_URL_RO"):
        print(
            "refusing to write over DATABASE_URL_RO: this is the read-only "
            "connection (the ops channel always is). Issue writes run where only "
            "a writable DATABASE_URL is set — the worker-only write path is "
            "deliberate. Read commands: issue list | issue show | issue candidates.",
            file=sys.stderr,
        )
        return 2
    return None


def _issue_or_exit(session: Session, key: str):
    from . import issues as ix

    issue = ix.get_issue(session, key)
    if issue is None:
        print(f"no issue {key!r}", file=sys.stderr)
        raise SystemExit(1)
    return issue


def cmd_issue(session: Session, args) -> int:
    """Dispatch to the `issue` subcommand, guarding every write."""
    from . import issues as ix

    action = args.issue_action
    if action not in _ISSUE_READ_ACTIONS:
        refusal = _issue_write_guard()
        if refusal is not None:
            return refusal

    if action == "list":
        exp = read.get_experiment(session, args.experiment) if args.experiment else None
        if args.experiment and exp is None:
            print(f"no experiment {args.experiment!r}", file=sys.stderr)
            return 1
        rows = ix.list_issues(
            session,
            open_only=not args.all,
            status=args.status,
            owner_role=args.owner,
            classification=args.classification,
            experiment=exp,
        )
        if not rows:
            print("no issues match" if args.all else "no open issues")
            return 0
        print(_table(
            ["issue", "sev", "pri", "classification", "owner", "status", "scope",
             "title"],
            [[
                r.issue_key, r.severity, r.priority, r.classification,
                r.current_owner_role, r.status,
                read.issue_scope_label(session, r), r.title[:60],
            ] for r in rows],
        ))
        return 0

    if action == "show":
        issue = _issue_or_exit(session, args.key)
        print(json.dumps(read.issue_detail(session, issue), indent=2, default=str))
        return 0

    if action == "findings-plan":
        # A READ-ONLY preview of what import-findings would do. The ops channel
        # is read-only against Postgres, so a write-and-rollback dry run cannot
        # run there at all; this answers the same question with plain selects.
        from .issue_import import plan as findings_plan

        print(json.dumps(findings_plan(session), indent=2, default=str))
        return 0

    if action == "candidates":
        # Deterministic ticket candidates from the canonical Control Tower read.
        # Building the report writes nothing (the evaluator runs persist=False).
        from .control_tower import build_report

        rep = build_report(session, evaluate=not args.no_evaluate)
        if args.json:
            print(json.dumps(
                {"open_issues": rep.open_issues,
                 "issue_candidates": rep.issue_candidates},
                indent=2, default=str,
            ))
            return 0
        if not rep.issue_candidates:
            print("no unticketed anomalies — every detected anomaly is covered "
                  "by an open issue, or there are none")
            return 0
        print(_table(
            ["detector", "sev", "pri", "would-classify", "recommended owner",
             "scope", "anomaly"],
            [[c["detector"], c["severity"], c["priority"],
              c["recommended_classification"], c["recommended_owner"],
              c["scope"], c["title"][:60]] for c in rep.issue_candidates],
        ))
        print("\nThese are RECOMMENDATIONS. Opening one is a write, owned by the "
              "recommended role.")
        return 0

    if action in ("command-show", "command-list"):
        # Receipts only. This surface reports; it cannot execute or retry a
        # command — the executor runs on the worker and nowhere else. Output is
        # metadata (see read.issue_command_summary): the submitted payload is
        # never printed, because this channel's own results are public.
        if action == "command-show":
            row = read.issue_command(session, args.command_id)
            if row is None:
                print(f"no issue command {args.command_id!r}", file=sys.stderr)
                return 1
            print(json.dumps(
                read.issue_command_summary(row), indent=2, default=str
            ))
            return 0
        rows = read.issue_commands(
            session, limit=args.limit, status=args.status, issue_key=args.issue,
        )
        if not rows:
            print("no issue commands recorded")
            return 0
        print(_table(
            ["command", "action", "status", "issue", "actor", "role", "completed"],
            [[r.command_id, r.action, r.status, r.issue_key or "-", r.actor,
              r.actor_role, str(r.completed_at or "-")] for r in rows],
        ))
        return 0

    if action == "open-candidate":
        # Adopt a Control Tower candidate into a real issue with its exact
        # fingerprint and lineage, so the ticket actually covers the anomaly
        # that caused it. A write; the guard above already refused RO.
        try:
            issue = ix.open_issue_from_candidate(
                session, args.fingerprint, actor=args.actor,
                opened_by_role=args.opened_by_role,
                evaluate=not args.no_evaluate,
            )
        except ix.CandidateNotAdoptable as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        session.commit()
        print(f"{issue.issue_key}  [{issue.status}] {issue.classification}/"
              f"{issue.severity}/{issue.priority}  owner {issue.current_owner_role}")
        print(f"  detector:    {issue.detector}")
        print(f"  fingerprint: {issue.detector_fingerprint}")
        print(f"  scope:       {read.issue_scope_label(session, issue)}")
        print("\nThis anomaly is now COVERED — the next control-tower read shows "
              "it under OPEN INVESTIGATIONS,")
        print("not UNTICKETED ANOMALIES.")
        return 0

    if action == "create":
        issue = ix.create_issue(
            session,
            title=args.title,
            problem_statement=args.problem,
            opened_by_role=args.opened_by_role,
            opened_by_actor=args.actor,
            classification=args.classification,
            severity=args.severity,
            priority=args.priority,
            current_owner_role=args.owner,
            owner_rationale=args.reason,
            experiment=args.experiment,
            detector=args.detector,
            anomaly_kind=args.anomaly_kind,
            gate_verdict=args.gate_verdict,
        )
        session.commit()
        print(f"{issue.issue_key}  [{issue.status}] {issue.classification}/"
              f"{issue.severity}/{issue.priority}  owner {issue.current_owner_role}")
        return 0

    issue = _issue_or_exit(session, args.key)

    if action == "triage":
        ix.triage_issue(session, issue, actor=args.actor, actor_role=args.actor_role,
                        reason=args.reason, severity=args.severity,
                        priority=args.priority, classification=args.classification,
                        owner_role=args.owner)
    elif action == "classify":
        ix.classify_issue(session, issue, classification=args.classification,
                          actor=args.actor, actor_role=args.actor_role,
                          reason=args.reason)
    elif action == "assign":
        ix.assign_issue(session, issue, owner_role=args.owner, actor=args.actor,
                        actor_role=args.actor_role, reason=args.reason)
    elif action == "transfer":
        ix.transfer_issue(session, issue, to_owner_role=args.owner,
                          actor=args.actor, actor_role=args.actor_role,
                          reason=args.reason, classification=args.classification,
                          evidence_waiver_reason=args.evidence_waiver)
    elif action == "status":
        ix.set_issue_status(session, issue, status=args.status, actor=args.actor,
                            actor_role=args.actor_role, reason=args.reason)
    elif action == "evidence-add":
        ix.add_issue_evidence(session, issue, evidence_type=args.type,
                              summary=args.summary, source_ref=args.source,
                              captured_by=args.actor, actor_role=args.actor_role)
    elif action == "link-add":
        ix.add_issue_link(session, issue, link_type=args.type,
                          reference=args.reference, label=args.label,
                          created_by=args.actor, actor_role=args.actor_role)
    elif action == "propose-fix":
        ix.propose_issue_fix(session, issue, proposed_fix=args.fix,
                             actor=args.actor, actor_role=args.actor_role,
                             reason=args.reason)
    elif action == "disposition":
        ix.record_disposition(
            session, issue, disposition=args.disposition, actor=args.actor,
            actor_role=args.actor_role, reason=args.reason,
            requires_new_version=args.requires_new_version,
            requires_new_epoch=args.requires_new_epoch,
            requires_platform_revision=args.requires_platform_revision,
            requires_pause_or_stand_down=args.requires_pause,
            version_and_epoch_rationale=args.version_and_epoch_rationale,
        )
    elif action == "validate-plan":
        ix.record_validation_plan(session, issue, validation_plan=args.plan,
                                  actor=args.actor, actor_role=args.actor_role,
                                  reason=args.reason)
    elif action == "validate":
        ix.record_validation_result(session, issue, passed=not args.failed,
                                    summary=args.summary, actor=args.actor,
                                    actor_role=args.actor_role,
                                    source_ref=args.source)
    elif action == "resolve":
        ix.resolve_issue(session, issue, resolution_summary=args.summary,
                         actor=args.actor, actor_role=args.actor_role,
                         validation_waiver_reason=args.validation_waiver)
    elif action == "close":
        ix.close_no_action(session, issue, reason=args.reason, actor=args.actor,
                           actor_role=args.actor_role)
    elif action == "duplicate":
        ix.mark_duplicate(session, issue, duplicate_of=args.of, actor=args.actor,
                          actor_role=args.actor_role, reason=args.reason)
    elif action == "reopen":
        ix.reopen_issue(session, issue, reason=args.reason, actor=args.actor,
                        actor_role=args.actor_role, owner_role=args.owner)
    elif action == "recurrence":
        ix.record_recurrence(session, issue, actor=args.actor,
                             actor_role=args.actor_role, note=args.reason)
    else:  # pragma: no cover — argparse constrains the set
        print(f"unknown issue action {action!r}", file=sys.stderr)
        return 2

    session.commit()
    print(f"{issue.issue_key}  [{issue.status}] {issue.classification}  owner "
          f"{issue.current_owner_role}"
          + (f"  disposition {issue.disposition}" if issue.disposition else ""))
    return 0


def cmd_issue_import_findings(session: Session, args) -> int:
    """Migrate the retired contract-findings registry AND settle it.

    Two steps in one idempotent operation: import each historical finding as a
    durable, Version-bound issue with its original research evidence, then
    record the operator's later withdrawal decision and close it
    CLOSED_NO_ACTION / NO_ACTION. Running it twice reports `already_present` /
    `already_reconciled` and writes nothing — it cannot reopen a closed issue,
    reset its status, restore a stale disposition or duplicate an event.

    Creates issue rows only. No experiment state, gate, verdict, Epoch, Version,
    Platform Revision, exposure or live action."""
    refusal = _issue_write_guard()
    if refusal is not None:
        return refusal
    from .issue_import import import_and_reconcile

    report = import_and_reconcile(session)
    if args.dry_run:
        session.rollback()
        print("MODE: DRY RUN — rolled back\n")
    else:
        session.commit()
    print(json.dumps(report, indent=2, default=str))
    return 0


#: Subcommands that are pure reads, and therefore safe on the ops channel.
_ISSUE_READ_ACTIONS: frozenset[str] = frozenset(
    {"list", "show", "candidates", "findings-plan",
     # Receipts for the worker-side issue-command transport. Reads: they report
     # what a command DID and must never execute or retry one. The transport's
     # only trigger is EXPERIMENT_OS_ISSUE_COMMAND on the worker.
     "command-show", "command-list"}
)

#: The `experiment-command` surface, which is reads and ONLY reads — the
#: lifecycle executor runs on the worker and nowhere else. Named here, rather
#: than left implicit in the subparser's `choices`, so the ops channel can assert
#: its allowlist against the CLI's own classification instead of a frozen list
#: that would pass the day a write is added under a read-looking name.
_EXPERIMENT_COMMAND_READ_ACTIONS: frozenset[str] = frozenset({"show", "list"})


def build_parser() -> argparse.ArgumentParser:
    """The CLI's argument parser, separate from `main` so the WIRING is testable.

    Every subcommand is dispatched through `args.fn`. `metric` originally shipped
    with `set_defaults(func=...)`, which parses fine and then dies on
    AttributeError at dispatch — invisible to tests that call the command
    function directly. A test now walks every subparser and asserts it carries a
    callable `fn`."""
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

    # Receipts for the worker-side experiment-lifecycle transport. READS: they
    # report what a command did and can neither execute nor retry one. The
    # transport's only trigger is EXPERIMENT_OS_EXPERIMENT_COMMAND on the worker.
    p_xcmd = sub.add_parser(
        "experiment-command",
        help="experiment-lifecycle command receipts (metadata only)",
    )
    p_xcmd.add_argument("action", choices=("show", "list"))
    p_xcmd.add_argument("command_id", nargs="?", default=None)
    p_xcmd.add_argument("--limit", type=int, default=20)
    p_xcmd.set_defaults(fn=cmd_experiment_command)

    p_tag = sub.add_parser("tag", help="strategy tag → experiment lineage")
    p_tag.add_argument("tag")
    p_tag.set_defaults(fn=cmd_tag)

    p_metric = sub.add_parser(
        "metric", help="compute one canonical metric at an explicit scope (read-only)"
    )
    p_metric.add_argument("metric")
    p_metric.add_argument("--experiment", dest="key", required=True)
    p_metric.add_argument("--arm", default=None,
                          help="arm key; omit for an experiment-wide scope")
    p_metric.add_argument("--kind", default="paper",
                          choices=["paper", "live", "paper_twin"],
                          help="deployment kind — required semantics, never inferred")
    p_metric.set_defaults(fn=cmd_metric)

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

    p_ev = sub.add_parser(
        "evaluate-gates",
        help="evaluate eligible gates and PERSIST results (--dry-run to preview). "
        "Records verdicts only — never promotes or transitions anything.",
    )
    p_ev.add_argument("--dry-run", action="store_true",
                      help="evaluate and report, write nothing")
    p_ev.add_argument("--experiment", default=None, help="limit to one experiment key")
    p_ev.add_argument("--gate", default=None, help="limit to one gate key")
    p_ev.set_defaults(fn=cmd_evaluate_gates)

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

    # --- investigation / issue workflow -----------------------------------
    p_issue = sub.add_parser(
        "issue",
        help="durable investigations: list/show/candidates are reads; every "
        "other action writes and refuses DATABASE_URL_RO",
    )
    # The group carries a dispatchable default too, so the "every subcommand has
    # a callable fn" invariant holds at every level. A child's own default wins.
    p_issue.set_defaults(fn=cmd_issue)
    isub = p_issue.add_subparsers(dest="issue_action", required=True)

    def _actor(parser, *, role_required=True):
        parser.add_argument("--actor", required=True, help="who is doing this")
        parser.add_argument(
            "--actor-role", required=role_required, default=None,
            help="the session role acting (LIVE_OPS, RESEARCH_LAB, ...)",
        )
        return parser

    p_il = isub.add_parser("list", help="open issues (--all for every status)")
    p_il.add_argument("--all", action="store_true")
    p_il.add_argument("--status", default=None)
    p_il.add_argument("--owner", default=None)
    p_il.add_argument("--classification", default=None)
    p_il.add_argument("--experiment", default=None)

    p_is = isub.add_parser("show", help="one investigation, with full history")
    p_is.add_argument("key")

    p_ic = isub.add_parser(
        "candidates",
        help="anomalies no open issue covers, with the recommended owner",
    )
    p_ic.add_argument("--json", action="store_true")
    p_ic.add_argument("--no-evaluate", action="store_true")

    # Receipts for the worker-side command transport (docs/EXPERIMENT_OS_ISSUES.md).
    # READS. They report what a submitted command did; nothing here can execute,
    # retry or clear one — a terminal receipt is final, and a retry is a NEW
    # command_id submitted through the ops branch.
    p_icmd = isub.add_parser(
        "command-show", help="one issue-command receipt (metadata only)"
    )
    p_icmd.add_argument("command_id")

    p_icml = isub.add_parser(
        "command-list", help="recent issue-command receipts, newest first"
    )
    p_icml.add_argument("--limit", type=int, default=20)
    p_icml.add_argument("--status", default=None,
                        help="SUCCEEDED / REJECTED / FAILED / RUNNING")
    p_icml.add_argument("--issue", default=None, help="issue key")
    p_icmd.set_defaults(fn=cmd_issue)
    p_icml.set_defaults(fn=cmd_issue)

    p_icr = isub.add_parser("create", help="open a durable issue")
    p_icr.add_argument("--title", required=True)
    p_icr.add_argument("--problem", default=None)
    p_icr.add_argument("--opened-by-role", required=True,
                       help="detecting context; may be EXPERIMENT_CONTROL_TOWER")
    p_icr.add_argument("--actor", default=None)
    p_icr.add_argument("--owner", default=None,
                       help="owning role; omit to use the routing recommendation")
    p_icr.add_argument("--classification", default=None)
    p_icr.add_argument("--severity", default=None)
    p_icr.add_argument("--priority", default=None)
    p_icr.add_argument("--experiment", default=None, help="experiment key")
    p_icr.add_argument("--detector", default=None)
    p_icr.add_argument("--anomaly-kind", default=None)
    p_icr.add_argument("--gate-verdict", default=None)
    p_icr.add_argument("--reason", default=None,
                       help="rationale, required when overriding the recommended owner")

    p_ioc = isub.add_parser(
        "open-candidate",
        help="adopt a Control Tower candidate by fingerprint into a durable "
        "issue, carrying its exact detector/scope/routing",
    )
    p_ioc.add_argument("fingerprint", help="from `issue candidates --json`")
    p_ioc.add_argument("--actor", required=True)
    p_ioc.add_argument("--opened-by-role", required=True,
                       help="detecting context; may be EXPERIMENT_CONTROL_TOWER")
    p_ioc.add_argument("--no-evaluate", action="store_true",
                       help="skip the dry-run gate evaluation when detecting")

    p_it = _actor(isub.add_parser("triage", help="first look: severity/priority/owner"))
    p_it.add_argument("key")
    p_it.add_argument("--reason", required=True)
    p_it.add_argument("--severity", default=None)
    p_it.add_argument("--priority", default=None)
    p_it.add_argument("--classification", default=None)
    p_it.add_argument("--owner", default=None)

    p_icl = _actor(isub.add_parser("classify", help="change classification (reason required)"))
    p_icl.add_argument("key")
    p_icl.add_argument("--classification", required=True)
    p_icl.add_argument("--reason", required=True)

    p_ia = _actor(isub.add_parser("assign", help="set the owning role"))
    p_ia.add_argument("key")
    p_ia.add_argument("--owner", required=True)
    p_ia.add_argument("--reason", required=True)

    p_itr = _actor(isub.add_parser(
        "transfer", help="hand the same problem to another role (evidence required)"))
    p_itr.add_argument("key")
    p_itr.add_argument("--owner", required=True)
    p_itr.add_argument("--reason", required=True)
    p_itr.add_argument("--classification", default=None)
    p_itr.add_argument("--evidence-waiver", default=None)

    p_ist = _actor(isub.add_parser("status", help="move through the workflow"))
    p_ist.add_argument("key")
    p_ist.add_argument("--status", required=True)
    p_ist.add_argument("--reason", default=None)

    p_ie = _actor(isub.add_parser("evidence-add", help="cite evidence"))
    p_ie.add_argument("key")
    p_ie.add_argument("--type", required=True)
    p_ie.add_argument("--summary", required=True)
    p_ie.add_argument("--source", default=None, help="ops result id, doc path, PR, ...")

    p_ilk = _actor(isub.add_parser("link-add", help="attach a canonical/external reference"))
    p_ilk.add_argument("key")
    p_ilk.add_argument("--type", required=True)
    p_ilk.add_argument("--reference", required=True)
    p_ilk.add_argument("--label", default=None)

    p_ipf = _actor(isub.add_parser("propose-fix", help="record the proposed remedy"))
    p_ipf.add_argument("key")
    p_ipf.add_argument("--fix", required=True)
    p_ipf.add_argument("--reason", default=None)

    p_id = _actor(isub.add_parser(
        "disposition",
        help="RECORD what must happen (new Version/Epoch/Platform Revision/...). "
        "Records only — performs nothing",
    ))
    p_id.add_argument("key")
    p_id.add_argument("--disposition", required=True)
    p_id.add_argument("--reason", required=True)
    p_id.add_argument("--requires-new-version", action="store_true", default=None)
    p_id.add_argument("--requires-new-epoch", action="store_true", default=None)
    p_id.add_argument("--requires-platform-revision", action="store_true", default=None)
    p_id.add_argument("--requires-pause", action="store_true", default=None)
    p_id.add_argument("--version-and-epoch-rationale", default=None)

    p_ivp = _actor(isub.add_parser(
        "validate-plan", help="state what would demonstrate the fix worked"))
    p_ivp.add_argument("key")
    p_ivp.add_argument("--plan", required=True)
    p_ivp.add_argument("--reason", default=None)

    p_iv = _actor(isub.add_parser("validate", help="record what validation showed"))
    p_iv.add_argument("key")
    p_iv.add_argument("--summary", required=True)
    p_iv.add_argument("--failed", action="store_true",
                      help="record a FAILED validation (the default is passed)")
    p_iv.add_argument("--source", default=None)

    p_ir = _actor(isub.add_parser("resolve", help="close as fixed"))
    p_ir.add_argument("key")
    p_ir.add_argument("--summary", required=True)
    p_ir.add_argument("--validation-waiver", default=None,
                      help="why this remedy cannot be validated directly")

    p_icn = _actor(isub.add_parser("close", help="close with no action taken"))
    p_icn.add_argument("key")
    p_icn.add_argument("--reason", required=True)

    p_idup = _actor(isub.add_parser("duplicate", help="confirm a semantic duplicate"))
    p_idup.add_argument("key")
    p_idup.add_argument("--of", required=True, help="the surviving issue key")
    p_idup.add_argument("--reason", required=True)

    p_iro = _actor(isub.add_parser("reopen", help="reopen a resolved/closed issue"))
    p_iro.add_argument("key")
    p_iro.add_argument("--reason", required=True)
    p_iro.add_argument("--owner", default=None)

    p_irc = _actor(isub.add_parser(
        "recurrence", help="record that the same problem was observed again"))
    p_irc.add_argument("key")
    p_irc.add_argument("--reason", default=None)

    for parser_obj in (p_il, p_is, p_ic, p_icr, p_ioc, p_it, p_icl, p_ia, p_itr, p_ist,
                       p_ie, p_ilk, p_ipf, p_id, p_ivp, p_iv, p_ir, p_icn,
                       p_idup, p_iro, p_irc):
        parser_obj.set_defaults(fn=cmd_issue)

    p_ifp = isub.add_parser(
        "findings-plan",
        help="READ-ONLY: what import-findings would do right now (safe on the "
        "read-only ops channel, where a write-and-rollback dry run cannot run)",
    )
    p_ifp.set_defaults(fn=cmd_issue)

    p_if = isub.add_parser(
        "import-findings",
        help="migrate the retired contract-findings registry into durable issues "
        "(idempotent)",
    )
    p_if.add_argument("--dry-run", action="store_true")
    p_if.set_defaults(fn=cmd_issue_import_findings, issue_action="import-findings")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session = _connect()
    try:
        return args.fn(session, args)
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
