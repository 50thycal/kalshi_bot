"""Set / read Railway service env vars via the GraphQL API (the one WRITE-capable ops tool).

Runs on the Ops Runner workflow runner — the only place with open egress AND the RAILWAY_*
secrets. SAFETY: only an ALLOWLIST of operational / live-config vars can be set or read.
Secrets (KALSHI_*, DATABASE_URL, RAILWAY_*) are never settable and never printed, so this
tool cannot exfiltrate credentials or rewrite infrastructure — at most it toggles trading
config that the operator could change in the Railway UI anyway.

ops/request.json shapes (handled by ops_runner.py):
  {"type": "env"}                                          # read current allowlisted vars
  {"type": "env", "set": {"KILL_SWITCH": "false", "LIVE_ENABLED": "true"}}
  {"type": "env", "set": {...}, "redeploy": false}         # set without redeploying

Stdlib only; reuses railway_logs._gql (browser UA to clear Cloudflare).
"""

from __future__ import annotations

import json
import os
import sys

from railway_logs import _gql

# The ONLY vars this tool may set or print. Deliberately excludes every secret/infra var
# (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, DATABASE_URL, RAILWAY_*, NWS_USER_AGENT).
ALLOWED_VARS = frozenset({
    "BOT_MODE", "KILL_SWITCH", "RUN_ONCE", "SCAN_INTERVAL_SECONDS", "LOG_LEVEL",
    "MAX_ORDER_SIZE", "MAX_MARKET_EXPOSURE", "MAX_TOTAL_EXPOSURE", "MAX_DAILY_LOSS",
    "MAX_SPREAD_CENTS", "MIN_VOLUME", "MIN_OPEN_INTEREST", "MIN_HOURS_TO_CLOSE",
    "LIVE_ENABLED", "LIVE_STRATEGIES", "LIVE_CITIES", "LIVE_WINDOWS",
    "LIVE_ENTRY_STYLE", "LIVE_PASSIVE_OFFSET_CENTS", "LIVE_ORDER_TIMEOUT_SECONDS",
    "LIVE_MAX_ORDER_DOLLARS", "LIVE_EXIT_MODE", "LIVE_TAKE_PROFIT_CENTS",
    "LIVE_STOP_LOSS_CENTS", "LIVE_BREAK_EVEN_ARM_CENTS", "LIVE_KILL_ON_DAILY_LOSS",
    "LIVE_SHAPE_PROBE",
    "WEATHER_STRATEGIES", "WEATHER_ENTRY_HOURS", "WEATHER_TOP_N", "WEATHER_TRACK_LOWS",
    "WEATHER_DIST_ENABLED", "WEATHER_DIST_SIGMA", "WEATHER_DIST_MIN_EDGE_CENTS",
    "WEATHER_CITY_WINDOW_ENABLED", "WEATHER_OBS_ENTRY_ENABLED", "WEATHER_POLYMARKET_ENABLED",
})

_UPSERT = "mutation($input: VariableUpsertInput!){ variableUpsert(input: $input) }"
_QUERY = "query($p:String!,$e:String!,$s:String){ variables(projectId:$p, environmentId:$e, serviceId:$s) }"
_REDEPLOY = "mutation($e:String!,$s:String!){ serviceInstanceRedeploy(environmentId:$e, serviceId:$s) }"


def _ctx():
    token = os.environ.get("RAILWAY_TOKEN", "").strip()
    project = os.environ.get("RAILWAY_PROJECT_ID", "").strip()
    env_id = os.environ.get("RAILWAY_ENVIRONMENT_ID", "").strip()
    svc = os.environ.get("RAILWAY_SERVICE_ID", "").strip()
    if not (token and project and env_id and svc):
        print("RAILWAY_TOKEN/PROJECT_ID/ENVIRONMENT_ID/SERVICE_ID must all be set.",
              file=sys.stderr)
        return None
    return token, project, env_id, svc


def run_get() -> int:
    ctx = _ctx()
    if not ctx:
        return 1
    token, project, env_id, svc = ctx
    try:
        data = _gql(_QUERY, {"p": project, "e": env_id, "s": svc}, token)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    allvars = data.get("variables") or {}
    print("# current allowlisted env vars (secrets hidden):")
    for k in sorted(ALLOWED_VARS):
        if k in allvars:
            print(f"  {k}={allvars[k]}")
    return 0


def run_set(mapping: dict, redeploy: bool = True) -> int:
    # Validate names FIRST (pure check, no network/creds needed) — fail closed on any
    # variable outside the allowlist; never partially apply a request with a bad name.
    bad = [k for k in mapping if k not in ALLOWED_VARS]
    if bad:
        print(f"refusing to set non-allowlisted vars: {sorted(bad)}", file=sys.stderr)
        print(f"allowed: {sorted(ALLOWED_VARS)}", file=sys.stderr)
        return 1
    if not mapping:
        print("env set request has no variables", file=sys.stderr)
        return 1
    ctx = _ctx()
    if not ctx:
        return 1
    token, project, env_id, svc = ctx
    ok = 0
    for name, value in mapping.items():
        inp = {"projectId": project, "environmentId": env_id, "serviceId": svc,
               "name": name, "value": str(value)}
        try:
            _gql(_UPSERT, {"input": inp}, token)
            print(f"  set {name}={value}")
            ok += 1
        except SystemExit as exc:
            print(f"  FAILED {name}: {exc}", file=sys.stderr)
    print(f"# {ok}/{len(mapping)} variables set")
    if ok and redeploy:
        try:
            _gql(_REDEPLOY, {"e": env_id, "s": svc}, token)
            print("# redeploy triggered — the worker will restart with the new config")
        except SystemExit as exc:
            print(f"# redeploy failed (vars apply on the next deploy): {exc}", file=sys.stderr)
    return 0 if ok == len(mapping) else 1


def main(argv: list[str] | None = None) -> int:
    """CLI: reads OPS_ENV_SET (JSON) to set, else prints current allowlisted vars."""
    raw = os.environ.get("OPS_ENV_SET", "").strip()
    if raw:
        return run_set(json.loads(raw))
    return run_get()


if __name__ == "__main__":
    raise SystemExit(main())
