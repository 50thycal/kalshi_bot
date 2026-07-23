"""Dispatch an ops request to the logs or read-only DB runner.

Run by the `Ops Runner` workflow whenever `ops/request.json` changes on the
`ops` branch. This lets Claude self-trigger a logs fetch or a read-only query by
committing a one-line request file (no "Run workflow" click), then read the
result back from the job log.

ops/request.json shapes:
  {"type": "logs", "limit": 200, "filter": "", "deployment_id": ""}
  {"type": "db",   "sql": "select ...", "max_rows": 200}
  {"type": "script", "name": "weather_model_check", "args": ["--sigma", "1.5"]}
  {"type": "env"}                                       # read allowlisted Railway env vars
  {"type": "env", "set": {"KILL_SWITCH": "false"}}      # set allowlisted vars + redeploy
  {"type": "noop"}   # placeholder; do nothing

Reuses scripts/railway_logs.py and scripts/db_query.py by setting the env vars
they already read, so all the read-only guards there still apply. Script requests
are allowlisted to self-contained read-only analysis scripts in scripts/.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REQUEST_PATH = os.environ.get("OPS_REQUEST_PATH", "ops/request.json")

# Read-only analysis scripts (stdlib + psycopg only) runnable via the ops channel.
# Each connects with DATABASE_URL_RO and a read-only session, like db_query.py.
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
    "kalshi_market_survey",
    "xvenue_probe",
    "xvenue_leadlag",
    "xvenue_crypto",
    "xvenue_shock",
    "xvenue_game_probe",
    "kalshi_deribit",
    "kalshi_crypto_probe",
    "kalshi_arb",
    "kalshi_flb",
    "kalshi_mm",
    "kalshi_mm_exits",
    "xvenue_game",
    "weather_digest",
    "weather_validation",
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
    "kalshi_favbuy_study",
    "xmarket_wc",
    "xgame_tape_study",
    "xgame_match_debug",
    "kalshi_mlbwx",
    "kalshi_perps_survey",
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
    "evo_digest",
    "evo_tree",
    "evo_selftest",
    "evo_explore_probe",
)


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

    rtype = (req.get("type") or "").strip().lower()
    print(f"# ops request: type={rtype!r}")

    if rtype in ("", "noop"):
        print("(noop — nothing to do)")
        return 0

    if rtype == "logs":
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
        import railway_env

        if req.get("set"):
            return railway_env.run_set(dict(req["set"]), redeploy=req.get("redeploy", True))
        return railway_env.run_get()

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


if __name__ == "__main__":
    raise SystemExit(main())
