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

Stdlib only. Resilient client: Railway's GraphQL API (backboard, behind Cloudflare) has
transient latency spikes, so every call retries with backoff on timeouts / 429 / 5xx, uses a
generous read timeout, and a batch set continues past a slow var (variableUpsert is idempotent,
so a retried — even a read-timed-out — upsert is safe). A browser UA clears Cloudflare's 1010.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_URL = "https://backboard.railway.com/graphql/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_TIMEOUT = 45      # generous read timeout — Railway mutations can be slow under load
_ATTEMPTS = 4      # total tries per call before giving up

# The ONLY vars this tool may set or print. Deliberately excludes every secret/infra var
# (KALSHI_API_KEY_ID, KALSHI_PRIVATE_KEY, DATABASE_URL, RAILWAY_*, NWS_USER_AGENT).
ALLOWED_VARS = frozenset({
    "BOT_MODE", "KILL_SWITCH", "RUN_ONCE", "SCAN_INTERVAL_SECONDS", "LOG_LEVEL",
    "MAX_ORDER_SIZE", "MAX_MARKET_EXPOSURE", "MAX_TOTAL_EXPOSURE", "MAX_DAILY_LOSS",
    "MAX_SPREAD_CENTS", "MIN_VOLUME", "MIN_OPEN_INTEREST", "MIN_HOURS_TO_CLOSE",
    "LIVE_ENABLED", "LIVE_STRATEGIES", "LIVE_CITIES", "LIVE_WINDOWS",
    "LIVE_CELLS", "LIVE_ENTRY_GRACE_HOURS",
    "LIVE_ENTRY_STYLE", "LIVE_PASSIVE_OFFSET_CENTS", "LIVE_ORDER_TIMEOUT_SECONDS",
    "LIVE_MAX_ORDER_DOLLARS", "LIVE_EXIT_MODE", "LIVE_TAKE_PROFIT_CENTS",
    "LIVE_TAKE_PROFIT_BY_WINDOW", "LIVE_ONE_POSITION_PER_EVENT",
    "LIVE_STOP_LOSS_CENTS", "LIVE_BREAK_EVEN_ARM_CENTS", "LIVE_KILL_ON_DAILY_LOSS",
    "LIVE_SHAPE_PROBE", "LIVE_EXIT_SLIPPAGE_CENTS", "LIVE_EXIT_USE_MARKET_FALLBACK",
    "LIVE_EXIT_MAX_ATTEMPTS", "LIVE_USER_ID", "LIVE_FRACTIONAL", "LIVE_PROBE",
    # mmsell live maker entry (the resting BUY-NO path) — settable so a live test can be
    # armed, capped and re-tuned from the ops channel without a code deploy.
    "MMSELL_LIVE_MAX_OPEN_POSITIONS", "MMSELL_LIVE_PRICE_OFFSET_CENTS",
    "MMSELL_LIVE_MAX_SPREAD_CENTS",
    # Queue-position A/B (docs/MMSELL_OFFSET_AB.md) — arming/disarming the mmsell10a/mmsell10b
    # experiment is exactly the kind of mid-test tuning this allowlist exists for. Salt is
    # included for completeness but should almost never be touched: changing it mid-experiment
    # re-randomizes every ticker's arm, invalidating comparison with everything already collected.
    "MMSELL_LIVE_OFFSET_AB_ARMS", "MMSELL_LIVE_OFFSET_AB_SALT",
    # Hot-market defensive pricing + the entry-retry cap: both change how aggressively live
    # chases a fill, so they are the knobs most likely to need tuning mid-test from ops.
    "MMSELL_LIVE_HOT_MARKET_MOVE_CENTS", "MMSELL_LIVE_HOT_MARKET_LOOKBACK_MINUTES",
    "MMSELL_LIVE_HOT_MARKET_DEFENSIVE_OFFSET_CENTS",
    "MMSELL_LIVE_MAX_ATTEMPTS_PER_TICKER", "MMSELL_LIVE_RETRY_MAX_DRIFT_CENTS",
    # theta's own hot-market knobs (never shared with mmsell's above, per this book's rule).
    "THETA_LIVE_HOT_MARKET_MOVE_CENTS", "THETA_LIVE_HOT_MARKET_LOOKBACK_MINUTES",
    "THETA_LIVE_HOT_MARKET_DEFENSIVE_OFFSET_CENTS",
    # theta's own entry-retry cap (mirrors the mmsell pair above, same rationale).
    "THETA_LIVE_MAX_ATTEMPTS_PER_TICKER", "THETA_LIVE_RETRY_MAX_DRIFT_CENTS",
    # theta live maker entry — its OWN knobs (deliberately not shared with mmsell's above; see
    # docs/THETA_LIVE_PLAN.md), same rationale as the mmsell trio. Closeout knobs
    # (THETA_CLOSEOUT_*) are intentionally NOT here, mirroring mmsell's own closeout knobs,
    # which are also absent from this allowlist — that's a deliberate UI-only safety choice for
    # both books, not an oversight.
    "THETA_LIVE_MAX_OPEN_POSITIONS", "THETA_LIVE_PRICE_OFFSET_CENTS",
    "THETA_LIVE_MAX_SPREAD_CENTS", "THETA_LIVE_MAX_ORDER_DOLLARS", "THETA_LIVE_MAX_CONTRACTS",
    # Live/paper parallel twin books (docs/LIVE_PAPER_TWIN.md). Standing policy is one twin per
    # live strategy, auto-derived, so these normally stay at defaults; they are readable/settable
    # so a twin can be named explicitly or its bookkeeping bounded on a large live book.
    "LIVE_PAPER_TWIN_ENABLED", "LIVE_PAPER_TWIN_AUTO", "LIVE_PAPER_TWINS",
    "LIVE_PAPER_TWIN_SUFFIX", "LIVE_PAPER_TWIN_MAX_OPEN_POSITIONS",
    "LIVE_PAPER_TWIN_PARITY_EVENTS", "LIVE_PAPER_TWIN_PARITY_MAX",
    "WEATHER_STRATEGIES", "WEATHER_ENTRY_HOURS", "WEATHER_TOP_N", "WEATHER_TRACK_LOWS",
    "WEATHER_DIST_ENABLED", "WEATHER_DIST_SIGMA", "WEATHER_DIST_MIN_EDGE_CENTS",
    "WEATHER_CITY_WINDOW_ENABLED", "WEATHER_OBS_ENTRY_ENABLED", "WEATHER_POLYMARKET_ENABLED",
    # Evo experiment tuning (live-adjustable so we can dial the population's iteration
    # speed + budget without a code deploy). Heartbeat cadence and per-agent weekly LLM
    # token / dollar budgets move together — raising cadence without budget just front-loads
    # spend into dormancy. ensure_budgets tops up in-cohort agents when a budget is raised.
    "EVO_ROUTINE_HEARTBEATS_PER_DAY", "EVO_DEEP_REFLECTIONS_PER_DAY",
    "EVO_STRATEGIC_REVIEW_HOURS",
    "EVO_WEEKLY_TOKEN_BUDGET", "EVO_WEEKLY_LLM_CEILING_USD", "EVO_MAX_ACTIVE_AGENTS",
    # Which tier runs on which backend/model. Readable + settable so a bad model id
    # or a mis-set tier can be diagnosed and corrected without a deploy. The API KEY
    # is deliberately NOT here — it is a credential, so it stays UI-only (this tool
    # must never be able to read or rewrite secrets).
    "EVO_LOCAL_LLM_ENABLED", "EVO_LOCAL_LLM_BASE_URL", "EVO_LOCAL_LLM_ALIASES",
    "EVO_LOCAL_LLM_MODEL", "EVO_LOCAL_LLM_DEEP_MODEL",
    "EVO_LOCAL_LLM_INPUT_COST_PER_MTOK", "EVO_LOCAL_LLM_OUTPUT_COST_PER_MTOK",
    "EVO_LOCAL_LLM_DEEP_INPUT_COST_PER_MTOK", "EVO_LOCAL_LLM_DEEP_OUTPUT_COST_PER_MTOK",
})

_UPSERT = "mutation($input: VariableUpsertInput!){ variableUpsert(input: $input) }"
_QUERY = "query($p:String!,$e:String!,$s:String){ variables(projectId:$p, environmentId:$e, serviceId:$s) }"
_REDEPLOY = "mutation($e:String!,$s:String!){ serviceInstanceRedeploy(environmentId:$e, serviceId:$s) }"


class RailwayError(Exception):
    """A Railway GraphQL call failed (after retries) or returned a hard error."""


def _graphql(query: str, variables: dict, token: str,
             *, attempts: int = _ATTEMPTS, timeout: int = _TIMEOUT) -> dict:
    """POST a GraphQL request, retrying transient failures (timeout / 429 / 5xx / network)
    with exponential backoff. Raises RailwayError only after exhausting retries, or
    immediately on a non-retryable error (4xx, GraphQL errors)."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(API_URL, data=payload, method="POST", headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {token}",
            "User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 429 or exc.code >= 500:        # transient -> retry
                last = RailwayError(f"HTTP {exc.code}: {detail}")
                _backoff(attempt)
                continue
            raise RailwayError(f"HTTP {exc.code}: {detail}") from None  # 4xx -> hard
        except (urllib.error.URLError, TimeoutError, OSError) as exc:   # incl read timeout
            last = RailwayError(f"network/timeout: {exc}")
            _backoff(attempt)
            continue
        if body.get("errors"):
            raise RailwayError("GraphQL errors: " + json.dumps(body["errors"])[:300])
        return body.get("data") or {}
    raise last or RailwayError("request failed after retries")


def _backoff(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 8))


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
        data = _graphql(_QUERY, {"p": project, "e": env_id, "s": svc}, token)
    except RailwayError as exc:
        print(f"env read failed: {exc}", file=sys.stderr)
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
    # Each upsert retries internally; a persistent failure on one var does NOT abort the
    # batch (upserts are idempotent, so a re-run safely re-applies any that didn't land).
    ok, failed = 0, []
    for name, value in mapping.items():
        inp = {"projectId": project, "environmentId": env_id, "serviceId": svc,
               "name": name, "value": str(value)}
        try:
            _graphql(_UPSERT, {"input": inp}, token)
            print(f"  set {name}={value}")
            ok += 1
        except RailwayError as exc:
            print(f"  FAILED {name}: {exc}", file=sys.stderr)
            failed.append(name)
    print(f"# {ok}/{len(mapping)} variables set")
    if failed:
        print(f"# NOT set (re-run to retry; idempotent): {failed}", file=sys.stderr)
    if ok and redeploy:
        try:
            _graphql(_REDEPLOY, {"e": env_id, "s": svc}, token)
            print("# redeploy triggered — the worker will restart with the new config")
        except RailwayError as exc:
            print(f"# redeploy failed (vars apply on the next deploy): {exc}", file=sys.stderr)
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    """CLI: reads OPS_ENV_SET (JSON) to set, else prints current allowlisted vars."""
    raw = os.environ.get("OPS_ENV_SET", "").strip()
    if raw:
        return run_set(json.loads(raw))
    return run_get()


if __name__ == "__main__":
    raise SystemExit(main())
