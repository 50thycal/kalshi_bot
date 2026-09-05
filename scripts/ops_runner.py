"""Dispatch an ops request to the logs or read-only DB runner.

Run by the `Ops Runner` workflow whenever `ops/request.json` changes on the
`ops` branch. This lets Claude self-trigger a logs fetch or a read-only query by
committing a one-line request file (no "Run workflow" click), then read the
result back from the job log.

ops/request.json shapes:
  {"type": "logs", "limit": 200, "filter": "", "deployment_id": ""}
  {"type": "db",   "sql": "select ...", "max_rows": 200}
  {"type": "script", "name": "weather_model_check", "args": ["--sigma", "1.5"]}
  {"type": "xos", "command": "control-tower"}           # canonical Experiment OS read CLI
  {"type": "xos", "command": "issue-list"}              # open investigations
  {"type": "xos", "command": "issue-show", "args": ["XOS-000123"]}
  {"type": "xos", "command": "issue-candidates"}        # anomalies with no open issue
  {"type": "env"}                                       # read allowlisted Railway env vars
  {"type": "env", "action": "set", "values": {"KILL_SWITCH": "false"}}   # MUTATING
  {"type": "env", "set": {"KILL_SWITCH": "false"}}      # the same mutation, legacy spelling
  {"type": "capabilities"}                              # what this channel can do, generated
  {"type": "doctor"}                                    # one-request operating snapshot
  {"type": "incident", "service": "main", "window_minutes": 30}
  {"type": "noop"}   # placeholder; do nothing

Any request may carry public-safe provenance — "actor", "purpose", "workstream",
"issue" — which is echoed in the result header and the receipt and is never
interpreted as authority. Every run writes a machine-readable receipt (see
scripts/ops_meta.py) recording what was asked, by whom, against which code, and
what happened; the workflow publishes it beside the result.

`env` and `logs` requests may add "service" to pick which Railway service to act on:
  {"type": "logs", "service": "evo"}                    # logs from the evo worker
  {"type": "env",  "service": "evo", "set": {...}}      # read/set the evo worker's vars
"main"/"live" (default) is the trading worker; "evo" is the evolutionary-agent worker.
`db` requests are service-agnostic (both services share one Postgres via DATABASE_URL_RO).

Reuses scripts/railway_logs.py and scripts/db_query.py by setting the env vars
they already read, so all the read-only guards there still apply. Script requests
are allowlisted to self-contained read-only analysis scripts in scripts/.

`xos` requests run the Experiment OS read CLI itself (read-only subcommands only,
against DATABASE_URL_RO). This is how the Experiment Control Tower and the other
session roles read production: through the SAME canonical code the worker runs,
so the operating layer can never drift from Experiment OS the way the retired
status checkers drifted from each other. It needs the full dependency set, which
the workflow installs only for this request type.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REQUEST_PATH = os.environ.get("OPS_REQUEST_PATH", "ops/request.json")

# Read-only analysis scripts (stdlib + psycopg only) runnable via the ops channel.
# Each connects with DATABASE_URL_RO and a read-only session, like db_query.py.
# Read-only `issue` subcommands, exposed on the ops channel under flat names.
# Investigations are durable Experiment OS state (docs/EXPERIMENT_OS_ISSUES.md),
# so every role reads them through the canonical CLI like everything else. The
# WRITING subcommands are deliberately not here — this channel is read-only
# against Postgres by design and the worker remains the only writer.
XOS_ISSUE_READS: dict[str, list[str]] = {
    "issue-list": ["issue", "list"],
    "issue-show": ["issue", "show"],
    "issue-candidates": ["issue", "candidates"],
    # READ-ONLY preview of the findings import/reconciliation. The write itself
    # is NOT reachable from here — it runs on the worker via
    # EXPERIMENT_OS_RECONCILE_FINDINGS_ON_BOOT.
    "issue-findings-plan": ["issue", "findings-plan"],
    # Receipts for the worker-side issue-command transport. READS: they report
    # what a submitted command did and cannot execute or retry one. The executor
    # is reachable only by setting EXPERIMENT_OS_ISSUE_COMMAND on the worker,
    # which is an `env` request, not an `xos` one.
    "issue-command-show": ["issue", "command-show"],
    "issue-command-list": ["issue", "command-list"],
}

#: Receipts for the worker-side experiment-LIFECYCLE transport. A SEPARATE map
#: from XOS_ISSUE_READS, which is asserted to hold only `issue …` subcommands —
#: these are top-level ones. Same prohibition either way: they report what a
#: command DID and can neither execute nor retry one. Registering a contract or
#: arming a canary reaches production only by setting
#: EXPERIMENT_OS_EXPERIMENT_COMMAND on the worker, which is an `env` request and
#: not an `xos` one, so this channel stays read-only against Postgres.
XOS_EXPERIMENT_COMMAND_READS: dict[str, list[str]] = {
    "experiment-command-show": ["experiment-command", "show"],
    "experiment-command-list": ["experiment-command", "list"],
}

#: Top-level xos subcommands that take no alias — every one a read.
XOS_DIRECT_READS: frozenset[str] = frozenset({
    "control-tower", "list", "show", "transitions", "platform", "tag",
    "scoreboard", "enforcement", "readiness", "evaluate-gates", "metric",
})


def xos_allowlist() -> set[str]:
    """The effective `xos` allowlist: one function, one source of truth.

    A function rather than a set literal inside `run()` because the docs/runner
    parity test (XOS-000005 — a command the runbook advertised and the runner
    refused) has to read this from the runner itself. It used to do that with a
    regex over the source, which silently missed a second alias map the day one
    was added; now both the runner and the test call this, so a new map cannot be
    half-registered."""
    return set(XOS_DIRECT_READS) | set(XOS_ISSUE_READS) | set(
        XOS_EXPERIMENT_COMMAND_READS
    )

ALLOWED_SCRIPTS = (
    "weather_pnl",
    "weather_experiments",
    "weather_model_check",
    "weather_exit_sweep",
    "weather_entry_study",
    "weather_window_sweep",
    "weather_backfill_calib",
    "weather_backfill_edges",
    "weather_polymarket_align",
    "weather_strategy_compare",
    "kalshi_market_probe",
    "kalshi_quote_probe",
    "kalshi_market_survey",
    "evo_order_probe",
    "xvenue_probe",
    "xvenue_leadlag",
    "xvenue_crypto",
    "xvenue_shock",
    "xvenue_game_probe",
    "kalshi_deribit",
    "kalshi_crypto_probe",
    "kalshi_arb",
    "kalshi_xlock",
    "kalshi_widequote_census",
    "kalshi_flb",
    "kalshi_mm",
    "kalshi_mm_exits",
    "xvenue_game",
    "weather_digest",
    "weather_validation",
    "pm_divergence_study",
    "weather_consensus_study",
    "weather_ratchet_study",
    "weather_entry_timing_study",
    "weather_entry_timing_backfill",
    "weather_obs_backfill_test",
    "weather_calibration_map",
    "weather_calibration_validate",
    "weather_exit_backfill",
    "weather_maker_study",
    "weather_maker_fills",
    "kalshi_theta_study",
    "theta_fill_model",
    "kalshi_favbuy_study",
    "xmarket_wc",
    "xgame_tape_study",
    "xgame_match_debug",
    "kalshi_mlbwx",
    "kalshi_perps_survey",
    "perp_surface_survey",
    "perp_arm_scores",
    "kalshi_pinned_study",
    "kalshi_decay_study",
    "kalshi_pin15_study",
    "kalshi_freeze_study",
    "kalshi_freeze_listing_check",
    "kalshi_compin_study",
    "kalshi_art_survey",
    "kalshi_seasonpin_census",
    "econ_react_study",
    "fed_rv_study",
    "kalshi_stream_survey",
    "oflow_study",
    "port_study",
    "mmsell_live",
    "mmsell_fill_model",
    "mmsell_exit_study",
    "mmsell_fill_replay",
    "mmsell_ladder_probe",
    "mmsell_h2h_study",
    "mmsell_offset_ab",
    "live_paper_parity",
    "livedash_probe",
    "mmsell_canary_slices",
    "mmsell_crypto_study",
    "mmsell_supply_forecast",
    "mmsell_regime_backtest",
    "mmsell_history_status",
    "mmsell_market_types",
    "mmsell_universe_review",
    "mmsell_timing_study",
    "mmsell_fee_recon",
    "mmsell_scan_health",
    "mmsell_quote_parity",
    "mmsell_queue_position",
    "mmsell_depth_fill_model",
    "mmsell_deconfound_study",
    "mmsell_taxonomy_audit",
    "theta_tail_diagnosis",
    "theta_tail_refit",
    "theta_ab_replay",
    "theta_candle_backfill_probe",
    "theta_forward_path",
    "theta_settlement_labels",
    "evo_digest",
    "evo_tree",
    "evo_selftest",
    "evo_explore_probe",
    "evo_backtest_probe",
    "experiment_os_status",
    "marktangle_probe",
    "marktangle2_probe",
)


# env/logs requests can target any Railway service in the project. Each name maps to the
# secret holding that service's Railway service ID — never committed, since this repo is
# public. "main"/"live" is the trading worker (BOT_MODE=live); "evo" is the
# evolutionary-agent worker (BOT_MODE=evo); "livedash" is the read-only live-vs-paper
# dashboard. Absent -> "main" (backward compatible).
#
# The dashboard was added because it was the one deployed service no session could see:
# a failed deploy, a crash loop or a startup error on it produced no signal anywhere, so
# the only way anyone learned it was broken was by opening it and finding it broken
# (WS-009 D3). A service whose ID secret is unset still answers with the actionable
# message below rather than a lookup failure.
_SERVICE_ID_SECRET = {
    "main": "RAILWAY_SERVICE_ID",
    "live": "RAILWAY_SERVICE_ID",
    "evo": "RAILWAY_EVO_SERVICE_ID",
    "livedash": "RAILWAY_LIVEDASH_SERVICE_ID",
}


#: The variable `_select_service` WRITES to aim the Railway helpers at a service.
#: It is also the variable main/live's own service ID ARRIVES in, which makes
#: reading it back unsafe once anything has been selected.
_TARGET_VAR = "RAILWAY_SERVICE_ID"

#: The last value we wrote to `_TARGET_VAR`, and the pristine main/live ID as it
#: was before we ever wrote. See `_main_service_id`.
_last_written_service_id: str | None = None
_main_service_id_seen: str | None = None


def _main_service_id() -> str:
    """main/live's service ID, immune to our own writes to the variable it arrives in.

    `main` and `live` map to RAILWAY_SERVICE_ID — the same variable this module
    overwrites to point the Railway helpers at a service. A single-service request
    never noticed, because nothing had been selected yet. `doctor` walks several
    services in one process and did: by the time it reached `main`, the variable
    held `livedash`'s ID, so `main` silently inherited it and the report showed
    livedash's deployment and livedash's (empty) variables as main's — a live-armed
    trading worker rendered as disarmed, with no warning. Observed in production on
    2026-09-02, run 33630702928.

    So: remember what WE wrote, and treat any other value found in the variable as
    the real one. That re-reads the environment whenever something outside this
    module sets it (a fresh process, a test's monkeypatch) instead of trusting a
    snapshot taken at import.
    """
    global _main_service_id_seen

    current = os.environ.get(_TARGET_VAR, "").strip()
    if current != _last_written_service_id:
        _main_service_id_seen = current
    return _main_service_id_seen or ""


def _select_service(req: dict) -> str | None:
    """Point RAILWAY_SERVICE_ID at the requested service for this env/logs request
    (railway_env.py and railway_logs.py both read RAILWAY_SERVICE_ID). Returns None on
    success, or an error message if the name is unknown or its ID secret is missing."""
    global _last_written_service_id

    # Observe the target variable BEFORE this call can overwrite it, on every call
    # and not only when main is the one asked for: otherwise selecting another
    # service first destroys main's ID before anything has looked at it.
    main_id = _main_service_id()

    name = (req.get("service") or "main").strip().lower()
    secret = _SERVICE_ID_SECRET.get(name)
    if secret is None:
        return f"unknown service {name!r} (known: {sorted(set(_SERVICE_ID_SECRET))})"
    svc_id = main_id if secret == _TARGET_VAR else os.environ.get(secret, "").strip()
    if not svc_id:
        return (f"service {name!r} is not configured — add the {secret} secret "
                "(that service's Railway service ID) to the repo's Actions secrets")
    os.environ[_TARGET_VAR] = svc_id
    _last_written_service_id = svc_id
    print(f"# target service: {name}")
    return None


#: The value the Ops Runner workflow sets to attest that the code being executed
#: came from the DEFAULT-BRANCH checkout rather than from the ops transport.
CODE_SOURCE_ENV = "OPS_RUNNER_CODE_SOURCE"
EXPECTED_CODE_SOURCE = "default-branch"

STALE_RUNNER_MESSAGE = (
    "REFUSING TO SERVE: this runner cannot prove it is current.\n"
    "\n"
    "The Ops Runner workflow must check the repository out at the DEFAULT BRANCH "
    "and execute scripts/ops_runner.py from there, setting "
    f"{CODE_SOURCE_ENV}={EXPECTED_CODE_SOURCE}. Without that, the code serving this "
    "request comes from the long-lived `ops` transport branch, which is refreshed "
    "only by hand and drifts silently behind the default branch.\n"
    "\n"
    "That drift is not hypothetical: it is XOS-000005, where two documented `xos` "
    "commands were refused in production for weeks while the allowlist on the "
    "default branch had carried them the whole time. A stale allowlist fails by "
    "QUIETLY REFUSING valid work, which is indistinguishable from the command not "
    "existing — so this guard fails loudly instead.\n"
    "\n"
    "Fix: restore the default-branch code checkout in .github/workflows/ops-runner.yml "
    "on the `ops` branch (git checkout -B ops origin/<default> && git push -f origin ops)."
)


def refuse_if_stale() -> int | None:
    """Fail closed when the runner cannot prove its code is the default branch's.

    Called from `serve()` — the SCRIPT entry point — and deliberately not from
    `main()`. The question this guard asks is "am I serving a production ops
    request from possibly-stale code", and the first version answered a different
    one, "am I running under GitHub Actions". Those are not the same: the CI test
    job also runs under Actions, and several tests exercise `main()` directly with
    a temp request file, so the guard refused them and turned a real invariant
    into a broken build.

    Enforcement therefore keys on how the runner was INVOKED. `python
    scripts/ops_runner.py` under Actions is the served-request path and must
    attest; importing the module and calling `main()` is dispatch, which tests and
    other callers may do freely. The `GITHUB_ACTIONS` check remains so a developer
    running the script by hand against their own checkout is not nagged for an
    attestation that means nothing locally.
    """
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        return None
    if os.environ.get(CODE_SOURCE_ENV, "").strip() == EXPECTED_CODE_SOURCE:
        return None
    print(STALE_RUNNER_MESSAGE, file=sys.stderr)
    return 1


#: Where this run's machine-readable receipt is written. The workflow points it
#: at a temp file, completes it with facts only the workflow knows (publication
#: outcome), and publishes it beside the result. Unset — a developer running the
#: runner locally — means no receipt, not a failure.
RECEIPT_PATH_ENV = "OPS_RECEIPT_PATH"


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_receipt(receipt: dict) -> None:
    """Persist the receipt as it currently stands.

    Written BEFORE the request runs as well as after it, so a request that
    crashes the interpreter still leaves a record of what was attempted — which
    is precisely the case where "what did that session do?" is hardest to answer
    afterwards.
    """
    path = os.environ.get(RECEIPT_PATH_ENV, "").strip()
    if not path:
        return
    try:
        with open(path, "w") as fh:
            json.dump(receipt, fh, indent=2, sort_keys=True, default=str)
    except OSError as exc:                      # a receipt must never fail a request
        print(f"# receipt not written: {exc}", file=sys.stderr)


def _finish_receipt(receipt: dict, status: int) -> None:
    receipt["finished_at"] = _utcnow()
    receipt["exit_status"] = status
    _write_receipt(receipt)


def main() -> int:
    try:
        with open(REQUEST_PATH) as f:
            req = json.load(f)
    except FileNotFoundError:
        print(f"No request file at {REQUEST_PATH}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Invalid JSON in {REQUEST_PATH}: {e}", file=sys.stderr)
        return 1

    import ops_meta

    rtype = ops_meta.request_type(req)
    receipt = ops_meta.build_receipt(req, started_at=_utcnow())
    _write_receipt(receipt)
    # The classification is the FIRST thing in the result, above any output: a
    # reader must never have to infer that they are looking at a production
    # change from the presence of a JSON key.
    print(ops_meta.header(req, receipt))
    if receipt["class"] == "UNCLASSIFIED":
        # build_receipt could not tell a read from a mutation. Refuse rather
        # than dispatch: the ambiguity is in the REQUEST, and the runner is not
        # entitled to resolve it in the permissive direction.
        print(f"refusing an unserveable request: {ops_meta.unserveable_reason(req)}",
              file=sys.stderr)
        _finish_receipt(receipt, 1)
        return 1

    # Several of the reused helpers report a bad request by RAISING SystemExit
    # rather than returning (db_query's SQL validation, railway_logs' deployment
    # lookup). That produced the right exit status but skipped the receipt, so
    # the requests most worth having a record of — the refused ones — were the
    # ones without one. Catch it here, keep the message and the status, and
    # finish the receipt either way.
    status = 1
    try:
        status = _dispatch(rtype, req, receipt)
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, str):
            print(code, file=sys.stderr)
            status = 1
        else:
            status = int(code or 0)
    finally:
        _finish_receipt(receipt, status)
    return status


def _dispatch(rtype: str, req: dict, receipt: dict) -> int:
    import ops_meta

    if rtype in ("", "noop"):
        print("(noop — nothing to do)")
        return 0

    if rtype == "logs":
        err = _select_service(req)
        if err:
            print(err, file=sys.stderr)
            return 1
        import railway_logs

        if req.get("limit") is not None:
            os.environ["LOG_LIMIT"] = str(req["limit"])
        if req.get("filter"):
            os.environ["LOG_FILTER"] = str(req["filter"])
        if req.get("deployment_id"):
            os.environ["RAILWAY_DEPLOYMENT_ID"] = str(req["deployment_id"])
        return railway_logs.main()

    if rtype == "db":
        sql = req.get("sql")
        if not sql:
            print("db request missing 'sql'", file=sys.stderr)
            return 1
        os.environ["SQL"] = str(sql)
        os.environ["MAX_ROWS"] = str(req.get("max_rows", 200))
        import db_query

        return db_query.main()

    if rtype == "env":
        err = _select_service(req)
        if err:
            print(err, file=sys.stderr)
            return 1
        import railway_env

        mutation = ops_meta.env_mutation(req)
        if not mutation:
            return railway_env.run_get()
        status, env_receipt = railway_env.apply_set(
            mutation, redeploy=req.get("redeploy", True), verify=req.get("verify", True),
            # Deliberate, recorded override of the unconsumed-command guard. Only
            # meaningful for the Experiment OS command transports; see
            # scripts/ops_command_guard.py.
            force_replace=bool(req.get("force_replace", False)),
        )
        receipt["mutation"] = env_receipt
        # Post-change canonical checks. The runner does not own an opinion about
        # whether the system is healthy after a change — it asks the canonical
        # readers and prints what they said, exactly as an `xos` request would.
        # Which checks are owed is decided by the variable names, in ops_meta,
        # never by strategy-specific logic here.
        # Only when something actually changed: a refused or credential-less
        # mutation has nothing to verify, and running the canonical readers
        # anyway would print a health report next to a change that never landed.
        hooks = ops_meta.verification_hooks(mutation) if env_receipt.get("set_ok") else ()
        results = {}
        if hooks:
            import ops_doctor

            print(f"\n# post-change canonical checks: {', '.join(hooks)}")
            for command in hooks:
                ok, text = ops_doctor.xos_read([command])
                print(f"# $ xos {command}")
                for line in text.splitlines()[:ops_doctor.MAX_XOS_LINES]:
                    print(f"#   {line}")
                results[command] = "ok" if ok else "non-zero"
                if not ok:
                    print(f"# {command} reported a problem — read the block above",
                          file=sys.stderr)
        receipt["post_change_checks"] = results
        return status

    if rtype == "xos":
        # Run the CANONICAL Experiment OS read CLI (read-only subcommands only)
        # against DATABASE_URL_RO. This exists so the Experiment Control Tower and
        # the other session roles read production through the same code the worker
        # runs — not a SQL re-implementation that could drift from it. It needs the
        # full dependency set, which the workflow installs when it sees this type.
        allowed = xos_allowlist()
        command = (req.get("command") or "control-tower").strip()
        if command not in allowed:
            print(f"xos command {command!r} is not allowlisted (allowed: "
                  f"{sorted(allowed)})", file=sys.stderr)
            return 1
        # The issue READS are exposed under hyphenated names so the allowlist is
        # a flat set of exact strings. The writing `issue` subcommands are
        # deliberately absent and cannot be reached from here: they refuse to run
        # against DATABASE_URL_RO, which is the only URL this channel ever has.
        argv = list(
            XOS_ISSUE_READS.get(command)
            or XOS_EXPERIMENT_COMMAND_READS.get(command)
            or [command]
        )
        argv += [str(a) for a in (req.get("args") or [])]
        ro = os.environ.get("DATABASE_URL_RO")
        if ro:
            # The CLI prefers DATABASE_URL_RO already; set both so nothing can
            # accidentally resolve a writable URL in this process.
            os.environ["DATABASE_URL"] = ro
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from kalshi_bot.experiment_os.cli import main as xos_main

        return xos_main(argv)

    if rtype == "capabilities":
        snapshot = ops_meta.capability_snapshot()
        if (req.get("format") or "").strip().lower() == "json":
            print(json.dumps(snapshot, indent=2, sort_keys=True))
        else:
            print(ops_meta.render_capabilities(snapshot))
        return 0

    if rtype == "doctor":
        import ops_doctor

        return ops_doctor.doctor(req)

    if rtype == "incident":
        import ops_doctor

        return ops_doctor.incident(req)

    if rtype == "script":
        name = (req.get("name") or "").strip()
        if name not in ALLOWED_SCRIPTS:
            print(f"script {name!r} is not allowlisted (allowed: {ALLOWED_SCRIPTS})", file=sys.stderr)
            return 1
        args = [str(a) for a in (req.get("args") or [])]
        import importlib

        mod = importlib.import_module(name)
        return mod.main(args)

    print(f"Unknown request type: {rtype!r}", file=sys.stderr)
    return 1


def serve() -> int:
    """The production entry point: prove the code is current, then dispatch.

    Split from `main()` so that "serve a request" and "dispatch a request" are
    separately testable, and so the freshness attestation is demanded of exactly
    the caller that needs it.
    """
    stale = refuse_if_stale()
    if stale is not None:
        # Even a refusal leaves a receipt: "the runner refused to serve" is a
        # fact about a production request, and the receipt is where facts about
        # production requests live.
        now = _utcnow()
        _write_receipt({"type": "(refused)", "class": "UNCLASSIFIED", "id": "",
                        "started_at": now, "finished_at": now, "exit_status": stale,
                        "error": "the runner could not attest default-branch code"})
        return stale
    return main()


if __name__ == "__main__":
    raise SystemExit(serve())
