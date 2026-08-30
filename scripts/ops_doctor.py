"""One request that establishes operating context, and one that opens an incident.

A session that has just started knows nothing: whether the runner is current,
whether the database answers, whether Railway answers, whether the kill switch is
down, whether real money is armed, what Experiment OS says. Establishing that has
cost five or six round trips of ~60s each, every time, and the sequence was
re-derived from prose on each new session — which is how sessions ended up with
DIFFERENT pictures of the same production system.

`doctor` answers all of it in one bounded request. `incident` answers the
follow-up question — *what is happening on this service right now* — with a
reproducible bundle instead of a call-by-call scavenger hunt.

Two rules hold this module honest:

  * **It reimplements nothing.** Experiment OS state comes from the canonical
    read CLI, exactly as a `{"type":"xos"}` request would get it; Railway state
    comes from the same helpers `logs`/`env` use; the database is read through
    the same read-only path `db` uses. This file composes canonical readers and
    is not allowed to become a second opinion about production.
  * **It cannot change anything.** Every call below is a read, the connection is
    the read-only one, and a section that fails is reported as a warning rather
    than being retried into something interesting.

A section that cannot answer is a WARNING, not a failure: a snapshot with one
unreachable subsystem is exactly the snapshot an operator most needs to see, so
these commands exit 0 whenever they produced a report at all.
"""

from __future__ import annotations

import io
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

#: Hard bounds. These commands are meant to be read by a person and committed to
#: a public branch, so every section is capped rather than "usually small".
MAX_LOG_LINES = 120
MAX_XOS_LINES = 60
MAX_EVENT_ROWS = 25
MAX_WINDOW_MINUTES = 24 * 60

#: The runtime configuration that decides whether real money can move. Read from
#: the allowlisted Railway variables — never invented here, and never a secret.
CRITICAL_VARS = (
    "BOT_MODE", "KILL_SWITCH", "LIVE_ENABLED", "LIVE_STRATEGIES",
    "LIVE_MAX_ORDER_DOLLARS", "LIVE_FRACTIONAL", "MAX_DAILY_LOSS",
    "MAX_TOTAL_EXPOSURE", "MAX_ORDER_SIZE", "LIVE_KILL_ON_DAILY_LOSS",
    "LIVE_PAPER_TWIN_ENABLED", "EXPERIMENT_OS_ENFORCEMENT_MODE",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Report:
    """A bounded, section-oriented report that collects warnings as it goes."""

    def __init__(self, title: str) -> None:
        self.lines: list[str] = []
        self.warnings: list[str] = []
        self.line("=" * 72)
        self.line(title)
        self.line("=" * 72)

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def section(self, name: str) -> None:
        self.line()
        self.line(name)
        self.line("-" * 72)

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        self.line(f"  WARNING: {text}")

    def note(self, text: str) -> None:
        self.line(f"  {text}")

    def block(self, text: str, *, max_lines: int) -> None:
        raw = [ln for ln in (text or "").splitlines()]
        for ln in raw[:max_lines]:
            self.line(f"  | {ln}")
        if len(raw) > max_lines:
            self.line(f"  | … {len(raw) - max_lines} more lines (bounded)")

    def render(self) -> str:
        out = list(self.lines)
        out.append("")
        out.append("WARNINGS")
        out.append("-" * 72)
        if self.warnings:
            out.extend(f"  ! {w}" for w in self.warnings)
        else:
            out.append("  none — every section answered")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Canonical readers, each wrapped so a failure is a warning
# ---------------------------------------------------------------------------


def xos_read(argv: list[str]) -> tuple[bool, str]:
    """Run the canonical Experiment OS read CLI and capture its output.

    The same code path a `{"type":"xos"}` request takes — deliberately, so
    `doctor` can never report a different enforcement mode than `xos enforcement`
    does. It is captured rather than printed so the report stays sectioned.
    """
    ro = os.environ.get("DATABASE_URL_RO")
    if ro:
        os.environ["DATABASE_URL"] = ro
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    buf, err = io.StringIO(), io.StringIO()
    try:
        from kalshi_bot.experiment_os.cli import main as xos_main

        with redirect_stdout(buf), redirect_stderr(err):
            status = xos_main(list(argv))
    except SystemExit as exc:                       # a CLI that argparse-exits
        status = int(exc.code or 0)
    except Exception as exc:                        # noqa: BLE001 — a section, not the run
        return False, f"{type(exc).__name__}: {exc}"
    text = buf.getvalue().strip() or err.getvalue().strip()
    return status == 0, text or "(no output)"


def db_rows(sql: str, params: tuple = (), *, limit: int = MAX_EVENT_ROWS):
    """One bounded read on the read-only connection, with the same guards `db` uses."""
    import db_query

    url = db_query._to_libpq_url(
        os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or ""
    )
    if not url:
        raise RuntimeError("DATABASE_URL_RO is not set")
    import psycopg

    options = (
        "-c default_transaction_read_only=on "
        f"-c statement_timeout={db_query.STATEMENT_TIMEOUT_MS} "
        "-c idle_in_transaction_session_timeout=60000"
    )
    with psycopg.connect(url, options=options, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return [], []
            columns = [d.name for d in cur.description]
            return columns, cur.fetchmany(limit)


def _point_at(service: str) -> None:
    """Aim the Railway helpers at one service, quietly.

    `_select_service` narrates to stdout, which is correct for a single-service
    request and wrong here: these reports are assembled and printed as a whole,
    so a stray line would land above the report it belongs to.
    """
    import ops_runner

    with redirect_stdout(io.StringIO()):
        err = ops_runner._select_service({"service": service})
    if err:
        raise RuntimeError(err)


def railway_vars(service: str) -> dict:
    """Allowlisted variables for one service, via the same helper `env` uses."""
    import railway_env

    _point_at(service)
    return railway_env.read_vars()


def railway_deployment(service: str) -> str:
    """The service's latest deployment, via the same helper `logs` uses."""
    import railway_logs

    _point_at(service)
    token = os.environ.get("RAILWAY_TOKEN", "").strip()
    if not token:
        raise RuntimeError("RAILWAY_TOKEN is not set")
    buf = io.StringIO()
    with redirect_stderr(buf):                      # the helper narrates to stderr
        dep = railway_logs._resolve_deployment_id(token)
    detail = buf.getvalue().strip().lstrip("# ")
    return detail or f"deployment {dep}"


def railway_logs_text(service: str, *, limit: int, log_filter: str = "") -> str:
    import railway_logs

    _point_at(service)
    os.environ["LOG_LIMIT"] = str(limit)
    os.environ["LOG_FILTER"] = log_filter
    os.environ.pop("RAILWAY_DEPLOYMENT_ID", None)
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(io.StringIO()):
        status = railway_logs.main()
    if status != 0:
        raise RuntimeError(f"railway_logs exited {status}")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Shared sections
# ---------------------------------------------------------------------------


def _runner_section(rep: Report) -> None:
    import ops_meta

    rep.section("RUNNER")
    rep.note(f"as of        {_now()}")
    rep.note(f"code sha     {os.environ.get('OPS_CODE_SHA') or '(unknown)'}")
    source = os.environ.get("OPS_RUNNER_CODE_SOURCE") or "(local)"
    rep.note(f"code source  {source}")
    if os.environ.get("GITHUB_ACTIONS") == "true" and source != "default-branch":
        rep.warn("the runner is not attesting default-branch code — it should have "
                 "refused to serve; see scripts/ops_runner.py")
    rep.note(f"run          {os.environ.get('OPS_RUN_URL') or '(none)'}")
    snap = ops_meta.capability_snapshot()
    configured = [n for n, s in snap["services"].items() if s["configured"]]
    missing = [n for n, s in snap["services"].items() if not s["configured"]]
    rep.note(f"services     configured: {', '.join(sorted(set(configured))) or 'none'}")
    if missing:
        rep.note(f"             unconfigured: {', '.join(sorted(set(missing)))}")


def _database_section(rep: Report) -> None:
    rep.section("DATABASE (read-only)")
    started = time.monotonic()
    try:
        _, rows = db_rows("select 1")
        rep.note(f"connectivity ok ({(time.monotonic() - started) * 1000:.0f} ms)")
    except Exception as exc:                        # noqa: BLE001
        rep.warn(f"database unreachable: {type(exc).__name__}: {exc}")
        return
    try:
        _, rows = db_rows(
            "select mode, status, started_at, finished_at, markets_scanned "
            "from bot_runs order by id desc limit 3", limit=3)
        for mode, status, started_at, finished_at, scanned in rows:
            rep.note(f"bot_run      {mode} {status} started={started_at} "
                     f"finished={finished_at} scanned={scanned}")
        if not rows:
            rep.warn("no bot_runs rows — the worker has never recorded a cycle")
    except Exception as exc:                        # noqa: BLE001
        rep.warn(f"bot_runs read failed: {type(exc).__name__}: {exc}")
    try:
        _, rows = db_rows(
            "select count(*) from system_events where created_at > now() - interval '60 minutes' "
            "and level in ('ERROR','CRITICAL')", limit=1)
        count = rows[0][0] if rows else 0
        rep.note(f"errors/60m   {count} ERROR+ system_events")
        if count:
            rep.warn(f"{count} ERROR+ events in the last 60 minutes — "
                     'run {"type":"incident","service":"main"}')
    except Exception as exc:                        # noqa: BLE001
        rep.warn(f"system_events read failed: {type(exc).__name__}: {exc}")


def _railway_section(rep: Report, services: list[str]) -> None:
    rep.section("RAILWAY SERVICES")
    for service in services:
        try:
            rep.note(f"{service:<10} {railway_deployment(service)}")
        except Exception as exc:                    # noqa: BLE001
            rep.warn(f"{service}: deployment unreachable: {type(exc).__name__}: {exc}")


def _runtime_config_section(rep: Report, service: str = "main") -> dict:
    import railway_env

    rep.section(f"RUNTIME CONFIG — {service} (allowlisted, non-secret)")
    try:
        allvars = railway_vars(service)
    except Exception as exc:                        # noqa: BLE001
        rep.warn(f"env read failed: {type(exc).__name__}: {exc}")
        return {}
    for name in CRITICAL_VARS:
        if name in allvars:
            rep.note(railway_env._echo(name, allvars[name], ""))
        else:
            rep.note(f"{name}=(unset — the code default applies)")
    kill = (allvars.get("KILL_SWITCH") or "").strip().lower()
    live = (allvars.get("LIVE_ENABLED") or "").strip().lower()
    if kill in ("1", "true", "yes"):
        rep.warn("KILL_SWITCH is ENGAGED — the worker is not trading")
    if live in ("1", "true", "yes"):
        strategies = allvars.get("LIVE_STRATEGIES") or "(none)"
        rep.note(f"REAL MONEY IS ARMED — LIVE_STRATEGIES={strategies}")
    return allvars


def _experiment_os_section(rep: Report) -> None:
    rep.section("EXPERIMENT OS (canonical CLI)")
    for command, argv in (
        ("enforcement", ["enforcement"]),
        ("readiness", ["readiness"]),
        ("issues", ["issue", "list"]),
    ):
        ok, text = xos_read(argv)
        rep.note(f"$ xos {command}")
        rep.block(text, max_lines=MAX_XOS_LINES)
        if not ok:
            rep.warn(f"xos {command} exited non-zero — see the block above")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def doctor(req: dict) -> int:
    import ops_meta

    rep = Report("OPS DOCTOR — production operating snapshot")
    _runner_section(rep)
    _database_section(rep)
    snap = ops_meta.capability_snapshot()
    services = sorted({
        name for name, svc in snap["services"].items()
        if svc["configured"] and name != "live"     # an alias of main
    })
    _railway_section(rep, services)
    _runtime_config_section(rep, "main")
    _experiment_os_section(rep)
    rep.line()
    rep.line("Next: {\"type\":\"capabilities\"} for the full request surface, "
             "{\"type\":\"incident\",\"service\":\"main\"} to open an investigation.")
    print(rep.render())
    return 0


# ---------------------------------------------------------------------------
# incident
# ---------------------------------------------------------------------------


def incident(req: dict) -> int:
    service = (req.get("service") or "main").strip().lower()
    try:
        window = int(req.get("window_minutes") or 30)
    except (TypeError, ValueError):
        window = 30
    window = max(1, min(window, MAX_WINDOW_MINUTES))

    rep = Report(f"OPS INCIDENT BUNDLE — {service}, last {window} minutes")
    _runner_section(rep)
    _railway_section(rep, [service])
    _runtime_config_section(rep, service)

    rep.section(f"RECENT LOGS — {service} (bounded to {MAX_LOG_LINES} lines)")
    try:
        rep.block(railway_logs_text(service, limit=MAX_LOG_LINES), max_lines=MAX_LOG_LINES)
    except Exception as exc:                        # noqa: BLE001
        rep.warn(f"logs unreachable: {type(exc).__name__}: {exc}")

    rep.section(f"ERRORS — system_events, last {window} minutes")
    try:
        _, rows = db_rows(
            "select created_at, level, component, left(message, 160) "
            "from system_events "
            "where created_at > now() - make_interval(mins => %s) "
            "and level in ('WARNING','ERROR','CRITICAL') "
            "order by id desc", (window,))
        for created, level, component, message in rows:
            rep.note(f"{created} {level:<8} {component:<20} {message}")
        if not rows:
            rep.note("(none)")
    except Exception as exc:                        # noqa: BLE001
        rep.warn(f"system_events read failed: {type(exc).__name__}: {exc}")

    rep.section(f"TRADING ACTIVITY — last {window} minutes")
    for label, sql in (
        ("live orders", "select coalesce(strategy,'(none)'), status, count(*) "
                        "from live_orders where created_at > now() - make_interval(mins => %s) "
                        "group by 1, 2 order by 3 desc"),
        ("paper trades", "select coalesce(strategy,'(none)'), status, count(*) "
                         "from paper_trades where created_at > now() - make_interval(mins => %s) "
                         "group by 1, 2 order by 3 desc"),
        ("risk refusals", "select approved, count(*) from risk_events "
                          "where created_at > now() - make_interval(mins => %s) group by 1"),
    ):
        try:
            _, rows = db_rows(sql, (window,))
            if rows:
                for row in rows:
                    rep.note(f"{label:<14} " + "  ".join(str(v) for v in row))
            else:
                rep.note(f"{label:<14} (none)")
        except Exception as exc:                    # noqa: BLE001
            rep.warn(f"{label} read failed: {type(exc).__name__}: {exc}")

    rep.section("EXPERIMENT OS (canonical CLI)")
    for command, argv in (("control-tower", ["control-tower"]),
                          ("issue-candidates", ["issue", "candidates"])):
        ok, text = xos_read(argv)
        rep.note(f"$ xos {command}")
        rep.block(text, max_lines=MAX_XOS_LINES)
        if not ok:
            rep.warn(f"xos {command} exited non-zero — see the block above")

    rep.line()
    rep.line("A finding here is not durable state. Anything real belongs in an "
             "Experiment OS issue (docs/EXPERIMENT_OS_ISSUES.md).")
    print(rep.render())
    return 0
