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

Stdlib only at import time — the one exception is the unconsumed-command guard
(`ops_command_guard`), which imports psycopg lazily inside its query and treats a
missing driver as "cannot check" like every other uncertainty. Resilient client: Railway's GraphQL API (backboard, behind Cloudflare) has
transient latency spikes, so every call retries with backoff on timeouts / 429 / 5xx, uses a
generous read timeout, and a batch set continues past a slow var (variableUpsert is idempotent,
so a retried — even a read-timed-out — upsert is safe). A browser UA clears Cloudflare's 1010.
"""

from __future__ import annotations

import hashlib
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
    # The one account-MUTATING switch on this list, and it is here on purpose: firing the free
    # permanent Kalshi ADVANCED grant (200/100 -> 300/300 tokens/sec) should not require a code
    # deploy, and the worker is the only process holding Kalshi credentials. It no-ops once the
    # account reads above `basic`, so setting it is a one-way door that cannot repeat.
    "KALSHI_UPGRADE_API_TIER",
    # The mmsell scan's coverage knobs. MMSELL_TOP_EVENTS is the per-cycle cap on events whose
    # orderbooks we fetch, and it is the single binding constraint on how much of Kalshi the
    # book can see — ~1,740 eligible events/cycle currently go unscanned because of it. It is
    # settable from here because the ceiling it should sit under is our Kalshi request budget,
    # which changed the moment the ADVANCED grant landed (20 -> 30 reads/sec) and will change
    # again; retuning it against a measured 429 rate should not need a code deploy. Raise it in
    # steps and watch the RATE LIMITED line in `mmsell quote parity`.
    "MMSELL_TOP_EVENTS", "MMSELL_EVENT_PAGES",
    # PERP-V1's tape collector (docs/PERP_V1_THESIS.md, Probe 1). Settable from
    # here because it is a read-only INSTRUMENT — it places no orders, holds no
    # position and registers no strategy tag — and because whether we are
    # collecting, and over which assets, is exactly the kind of thing that should
    # be adjustable against a measured request budget rather than a code deploy.
    #
    # PERPS_COLLECTOR_ENABLED is the on switch and it defaults OFF. Setting it
    # REDEPLOYS THE WORKER, which is also where the live books run, so it is an
    # operator act even though the collector itself risks nothing.
    "PERPS_COLLECTOR_ENABLED", "PERPS_ASSETS", "PERPS_INTERVAL_SECONDS",
    "PERPS_MARKET_LIMIT", "PERPS_ORDERBOOK_ENABLED", "PERPS_ORDERBOOK_MAX_MARKETS",
    "PERPS_FUNDING_ENABLED", "PERPS_FUNDING_INTERVAL_MINUTES",
    "PERPS_FUNDING_LOOKBACK_DAYS",
    # Experiment OS operational migration (docs/EXPERIMENT_OS_ENFORCEMENT.md). These are
    # here because the ops channel is deliberately READ-ONLY against Postgres: the worker
    # is the only process holding a writable DATABASE_URL, so the legacy import and the
    # enforcement cutover can only be executed by setting these and letting it boot.
    # Both hooks are idempotent, both refuse loudly rather than forcing, and neither can
    # stop trading. EXPERIMENT_OS_ENFORCEMENT_MODE only RECORDS the declared mode after
    # production_readiness() passes at that instant — a red checklist changes nothing,
    # and force stays a human decision made through a different door.
    # Same door, same reasoning, for the contract-findings import + reconciliation
    # (docs/EXPERIMENT_OS_ISSUES.md): bounded to that one operation, idempotent,
    # and previewable read-only first via the xos `issue-findings-plan` command.
    "EXPERIMENT_OS_RECONCILE_FINDINGS_ON_BOOT",
    # One bounded Experiment OS issue command, executed once at worker boot
    # (docs/EXPERIMENT_OS_ISSUES.md). Same door, same reasoning: ordinary ticket
    # writes have no other production path. Its VALUE is redacted from this
    # tool's output — see REDACTED_VARS — which is output hygiene, not privacy:
    # the envelope is committed in plaintext to ops/request.json on this public
    # branch, so payloads must be safe to disclose.
    "EXPERIMENT_OS_ISSUE_COMMAND",
    # One bounded PLATFORM CHANGE REVIEW command, executed once at worker boot
    # (docs/EXPERIMENT_OS_PLATFORM_IMPACT.md). A separate transport from the issue
    # command with a disjoint vocabulary and its own receipt ledger, because a
    # ticket must never be able to mutate a Platform Revision. Registering a
    # revision, accepting impact dispositions and the activation cutover are
    # writes with no other production path. Its VALUE is redacted from this tool's
    # output for the same output-hygiene reason, and with the same caveat: the
    # envelope is public.
    "EXPERIMENT_OS_PLATFORM_COMMAND",
    # One bounded EXPERIMENT LIFECYCLE command, executed once at worker boot
    # (kalshi_bot/experiment_os/experiment_commands.py). A third transport with a
    # vocabulary disjoint from both of the above, because a ticket must not be
    # able to arm a canary and a platform revision must not be able to freeze a
    # Version. Registering a successor contract and arming a live canary are
    # writes with no other production path. Its VALUE is redacted from this
    # tool's output for the same output-hygiene reason, and with the same caveat:
    # the envelope is public.
    "EXPERIMENT_OS_EXPERIMENT_COMMAND",
    "EXPERIMENT_OS_IMPORT_ON_BOOT", "EXPERIMENT_OS_ENFORCEMENT_MODE",
    "EXPERIMENT_OS_CUTOVER_ID", "EXPERIMENT_OS_CUTOVER_ACTOR",
    "EXPERIMENT_OS_CUTOVER_REASON",
    "EXPERIMENT_OS_EVALUATE_GATES", "EXPERIMENT_OS_EVALUATE_INTERVAL_MINUTES",
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
    # The book DEFINITIONS themselves. A live mmsell book is an ordinary entry in this
    # string (Lmmsell8 and Lmmsell10 both are), so registering an Experiment OS canary and
    # then being unable to CREATE its book is the same defect class as #266: an approved
    # procedure the sanctioned channel refuses halfway through. `LIVE_STRATEGIES` — the
    # switch deciding which of these books spends real money — has always been settable
    # from here, so this crosses no authority line it did not already cross; what it adds
    # is the ability to define the book that switch then names.
    #
    # It is also the safer direction for a REGISTERED book: Experiment OS recomputes
    # `book_params` for every registered live tag at boot, so editing one here is detected
    # as EXPERIMENT_CONFIG_DRIFT and takes that experiment's gate to BLOCKED_INTEGRITY.
    # Never hand-compose the value: it is one ~800-char string holding EVERY book, and
    # dropping a book by retyping it would silently stop it. Derive it instead —
    # `scripts/mmsell10_canary.py activate` prints the exact request.
    "MMSELL_VARIANTS",
    # mmsell's own concentration safeguards, and the quote pre-filter. These are here so a
    # pre-registered risk envelope can ASSERT them rather than inherit them: production
    # leaves all six unset, so they hold whatever config.py currently defaults to, and a
    # later change to a default would silently move a value an approved envelope declared.
    # Pinning them explicitly is what makes the envelope true of the running process.
    "MMSELL_EVENT_RUNG_CAP_ENABLED", "MMSELL_EVENT_RUNG_CAP",
    "MMSELL_SETTLEMENT_CAP_ENABLED", "MMSELL_SETTLEMENT_CAP_PCT",
    "MMSELL_SETTLEMENT_EVENT_CAP",
    # The pre-filter stays DISARMED for the price-ceiling books: the full order book is
    # authoritative for the maxyes decision (tests/test_mmsell_orderbook_authoritative.py).
    "MMSELL_PREFILTER_ENABLED",
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
    # The fleet-wide pause switch (docs/EVO_RUNBOOK.md "Pausing and emergencies",
    # docs/EVO_CONFIG.md). run_evo_cycle checks settings.enabled and returns early
    # with "infrastructure pause" when this is false — nothing is lost, the loop
    # resumes idempotently. Settable from here for the same reason as every other
    # EVO_* knob below: pausing for cost or infra reasons should not need a code
    # deploy. Omitted from this allowlist until 2026-09-06, which forced a workaround
    # (zeroing EVO_WEEKLY_LLM_CEILING_USD) that stops spend but not the service's
    # compute — a real gap from the documented pause semantics.
    "EVO_ENABLED",
    # Evo experiment tuning (live-adjustable so we can dial the population's iteration
    # speed + budget without a code deploy). Heartbeat cadence and per-agent weekly LLM
    # token / dollar budgets move together — raising cadence without budget just front-loads
    # spend into dormancy. ensure_budgets tops up in-cohort agents when a budget is raised.
    "EVO_ROUTINE_HEARTBEATS_PER_DAY", "EVO_DEEP_REFLECTIONS_PER_DAY",
    "EVO_STRATEGIC_REVIEW_HOURS",
    "EVO_WEEKLY_TOKEN_BUDGET", "EVO_WEEKLY_LLM_CEILING_USD", "EVO_MAX_ACTIVE_AGENTS",
    "EVO_MAX_GROWTH_PER_BOUNDARY",
    # Research ceilings. These bound how much EVIDENCE an agent can gather in a cohort,
    # and unlike the LLM budgets they cost CPU against our own DB rather than dollars.
    # Live-settable because exhausting them is invisible from the outside: the fleet's
    # backtest counters simply stop moving and read as "the agents lost interest" when
    # they are in fact blocked (observed: all three agents pinned at sandbox_runs 50/50
    # for days while an agent filed a ticket saying its evidence base had been
    # invalidated and it had no budget left to rebuild it).
    "EVO_WEEKLY_SANDBOX_RUNS", "EVO_WEEKLY_DATA_READS", "EVO_WEEKLY_MARKET_SCANS",
    # The maker-fill correction gates backtest entries on a calibration measured on ONE
    # book (mmsell). If it ever starts distorting a domain it was not measured on, this
    # puts the sandbox back on the optimistic path without a deploy. The realizable
    # projection keeps reporting either way — only the gate switches off.
    "EVO_SANDBOX_MAKER_FILL_MODEL",
    # External-signal staleness gate. A collector dying is invisible from the outside —
    # the metric just stops appearing — so this is the knob that decides whether a
    # slow feed still authorizes trades. Settable because a collector's cadence can
    # change without a deploy.
    "EVO_SIGNAL_MAX_AGE_MINUTES",
    # Which tier runs on which backend/model. Readable + settable so a bad model id
    # or a mis-set tier can be diagnosed and corrected without a deploy. The API KEY
    # is deliberately NOT here — it is a credential, so it stays UI-only (this tool
    # must never be able to read or rewrite secrets).
    "EVO_LOCAL_LLM_ENABLED", "EVO_LOCAL_LLM_BASE_URL", "EVO_LOCAL_LLM_ALIASES",
    "EVO_LOCAL_LLM_MODEL", "EVO_LOCAL_LLM_DEEP_MODEL",
    "EVO_LOCAL_LLM_INPUT_COST_PER_MTOK", "EVO_LOCAL_LLM_OUTPUT_COST_PER_MTOK",
    "EVO_LOCAL_LLM_DEEP_INPUT_COST_PER_MTOK", "EVO_LOCAL_LLM_DEEP_OUTPUT_COST_PER_MTOK",
})

# Allowlisted vars whose NAME may be printed but whose VALUE may not. Ops results
# are committed to a public repository and worker logs are shared, so a variable
# that carries a structured command body is echoed as a hash and a length instead
# of its contents. This is OUTPUT HYGIENE, NOT CONFIDENTIALITY: the same bytes are
# committed in plaintext to ops/request.json on the public `ops` branch and stay
# in Git history. A payload must therefore be safe for public disclosure — no
# secrets, credentials, personal data, private logs, account/order identifiers or
# sensitive raw evidence. If private content is genuinely required, stop and
# propose an encrypted transport; redaction cannot make this channel private.
REDACTED_VARS = frozenset(
    {"EXPERIMENT_OS_ISSUE_COMMAND", "EXPERIMENT_OS_PLATFORM_COMMAND",
     "EXPERIMENT_OS_EXPERIMENT_COMMAND"}
)

# Upper bound on a settable value, checked BEFORE the Railway API call so an
# oversized body is refused locally rather than uploaded and then rejected (or
# accepted) by an API whose own limits we do not control. Matches
# issue_commands.MAX_ENVELOPE_BYTES; deliberately duplicated rather than imported
# because this script is stdlib-only and runs without the package installed.
MAX_VALUE_BYTES = 8192


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _echo(name: str, value: str, prefix: str) -> str:
    """How a variable may be printed: in full, or as an identifier for a body."""
    if name in REDACTED_VARS:
        text = str(value)
        return (f"{prefix}{name}=<redacted {len(text.encode('utf-8'))} bytes, "
                f"sha256:{_digest(text)[:16]}>")
    return f"{prefix}{name}={value}"


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


def read_vars() -> dict:
    """Every variable Railway holds for the selected service, as a dict.

    Split out of `run_get` because three callers now need the VALUES rather than
    the printout: the read itself, the before/after readback around a mutation,
    and `ops_doctor`'s runtime-config section. Filtering to the allowlist is the
    caller's job — a mutation has to be able to see that a variable it is about
    to write is currently unset, and `_echo` is what decides what may be printed.
    """
    ctx = _ctx()
    if not ctx:
        raise RailwayError("RAILWAY_TOKEN/PROJECT_ID/ENVIRONMENT_ID/SERVICE_ID must all be set")
    token, project, env_id, svc = ctx
    data = _graphql(_QUERY, {"p": project, "e": env_id, "s": svc}, token)
    return dict(data.get("variables") or {})


def run_get() -> int:
    try:
        allvars = read_vars()
    except RailwayError as exc:
        print(f"env read failed: {exc}", file=sys.stderr)
        return 1
    print("# current allowlisted env vars (secrets hidden):")
    for k in sorted(ALLOWED_VARS):
        if k in allvars:
            print(_echo(k, allvars[k], "  "))
    return 0


def _unparseable(mapping: dict) -> list[tuple[str, str]]:
    """[(VAR, reason)] for values the Settings model would reject.

    Validates each field ALONE rather than building a whole Settings: this runs in
    the ops runner, which holds no Kalshi credentials or DATABASE_URL, so a full
    construction would fail on required fields that have nothing to do with the
    request. A var with no matching field is left to the allowlist to judge.

    Never let a validator failure block a legitimate set: if the model cannot be
    imported or introspected at all, say so and allow the write. This guard exists
    to catch an obvious type error, not to become a new way for ops to be down.
    """
    try:
        from pydantic import TypeAdapter, ValidationError

        from kalshi_bot.config import Settings
        fields = Settings.model_fields
    except Exception as exc:  # noqa: BLE001
        print(f"# note: value type-check skipped ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return []
    bad: list[tuple[str, str]] = []
    for name, value in mapping.items():
        field = fields.get(name.lower())          # case_sensitive=False
        if field is None:
            continue
        try:
            TypeAdapter(field.annotation).validate_python(value)
        except ValidationError as exc:
            reason = exc.errors()[0].get("msg", "invalid value")
            bad.append((name, f"{reason} (got {value!r})"))
        except Exception:  # noqa: BLE001
            continue                              # unintrospectable type: allow
    return bad


def run_set(mapping: dict, redeploy: bool = True, verify: bool = True) -> int:
    """Apply a bounded mutation. The thin wrapper: status only, for the CLI."""
    status, _receipt = apply_set(mapping, redeploy=redeploy, verify=verify)
    return status


def apply_set(mapping: dict, *, redeploy: bool = True,
              verify: bool = True, force_replace: bool = False) -> tuple[int, dict]:
    """Change allowlisted variables, then go and check what actually happened.

    The old contract ended at "set + redeploy requested", which is a statement
    about what this process ASKED FOR, not about the system. An operator reading
    it could not tell a landed change from an upsert that silently lost a race
    with a concurrent edit, or from a redeploy Railway declined. So the mutation
    now records pre-state, applies, records the redeploy outcome, reads the
    effective state back, and ends in one of three verdicts:

      VERIFIED               every target reads back as requested
      APPLIED_BUT_UNVERIFIED the writes were accepted but the readback could not
                             confirm them (Railway unreachable, or a value came
                             back different — say so rather than assume)
      FAILED                 at least one write was refused or failed

    Redacted variables stay redacted on both sides of the comparison: the
    receipt reports that a redacted value CHANGED, and its length and digest,
    never its contents.
    """
    receipt = {"verdict": "FAILED", "targets": sorted(mapping), "redeploy": None,
               "set_ok": 0, "set_failed": [], "changes": {}}
    # Validate names FIRST (pure check, no network/creds needed) — fail closed on any
    # variable outside the allowlist; never partially apply a request with a bad name.
    bad = [k for k in mapping if k not in ALLOWED_VARS]
    if bad:
        print(f"refusing to set non-allowlisted vars: {sorted(bad)}", file=sys.stderr)
        print(f"allowed: {sorted(ALLOWED_VARS)}", file=sys.stderr)
        receipt["error"] = "non-allowlisted variables"
        return 1, receipt
    if not mapping:
        print("env set request has no variables", file=sys.stderr)
        receipt["error"] = "no variables"
        return 1, receipt
    # Bound every value BEFORE touching the network — a body too large to be a
    # legitimate command should never leave this process, and fail-closed here
    # keeps the batch all-or-nothing the same way a bad name does.
    oversized = [
        k for k, v in mapping.items()
        if len(str(v).encode("utf-8")) > MAX_VALUE_BYTES
    ]
    if oversized:
        print(f"refusing to set oversized vars (limit {MAX_VALUE_BYTES} bytes): "
              f"{sorted(oversized)}", file=sys.stderr)
        receipt["error"] = "oversized values"
        return 1, receipt
    # Then the VALUE's type, against the field that will actually parse it. The
    # worker's config is fail-closed: a value pydantic cannot read makes it refuse
    # to start, and because setting a var redeploys, an unparseable value here is a
    # full outage that nothing downstream can catch. On 2026-08-30 clearing a bool
    # with "" (the documented way to clear a STRING transport var) crash-looped the
    # worker for 17 hours. Same shape as the name and size checks: pure, local,
    # all-or-nothing, before the network — and, like them, it leaves a receipt
    # saying which check refused the request.
    unparseable = _unparseable(mapping)
    if unparseable:
        print("refusing to set values the worker's config cannot parse — setting "
              "these would redeploy into a crash loop:", file=sys.stderr)
        for name, why in unparseable:
            print(f"  {name}: {why}", file=sys.stderr)
        receipt["error"] = "unparseable values"
        receipt["unparseable"] = {name: why for name, why in unparseable}
        return 1, receipt
    ctx = _ctx()
    if not ctx:
        receipt["error"] = "railway context incomplete"
        return 1, receipt
    token, project, env_id, svc = ctx

    # --- before -----------------------------------------------------------
    before: dict | None
    try:
        before = {k: v for k, v in read_vars().items() if k in mapping}
    except RailwayError as exc:
        before = None
        print(f"# pre-state unreadable ({exc}) — the change will be applied but "
              "cannot be compared", file=sys.stderr)
    if before is not None:
        print("# BEFORE:")
        for name in sorted(mapping):
            if name in before:
                print(_echo(name, before[name], "  "))
            else:
                print(f"  {name}=(unset)")

    # --- unconsumed-command guard -----------------------------------------
    # The three Experiment OS command transports are single-slot variables
    # consumed at the worker's next boot. Overwriting one that still holds an
    # envelope nobody has executed discards another session's work silently —
    # the ledger cannot catch it, because it is only ever asked after the fact.
    # This is the only check that needs the BEFORE value, so it sits here rather
    # than up with the pure ones. It fails OPEN and says so: see
    # ops_command_guard's docstring for why a closed failure would be worse.
    import ops_command_guard

    guard_notes = {}
    blocked = []
    for name in sorted(set(mapping) & set(ops_command_guard.TRANSPORT_LEDGERS)):
        result = ops_command_guard.check(
            name, None if before is None else before.get(name, "")
        )
        guard_notes[name] = result.as_receipt()
        if result.blocked and not force_replace:
            blocked.append(name)
        if result.blocked:
            print(f"# UNCONSUMED COMMAND: {result.reason}", file=sys.stderr)
        elif not result.conclusive:
            print(f"# guard INCONCLUSIVE: {result.reason}", file=sys.stderr)
    if guard_notes:
        receipt["command_guard"] = guard_notes
        if force_replace:
            receipt["force_replace"] = True
    if blocked:
        # Fail closed on a CONFIRMED collision only, and all-or-nothing like the
        # other refusals above: nothing in this request is applied.
        print(f"refusing to overwrite unconsumed command transport(s): {blocked}",
              file=sys.stderr)
        receipt["error"] = "unconsumed command transport"
        receipt["verdict"] = "REFUSED"
        return 1, receipt

    # --- apply ------------------------------------------------------------
    # Each upsert retries internally; a persistent failure on one var does NOT abort the
    # batch (upserts are idempotent, so a re-run safely re-applies any that didn't land).
    ok, failed = 0, []
    for name, value in mapping.items():
        inp = {"projectId": project, "environmentId": env_id, "serviceId": svc,
               "name": name, "value": str(value)}
        try:
            _graphql(_UPSERT, {"input": inp}, token)
            print(_echo(name, value, "  set "))
            ok += 1
        except RailwayError as exc:
            print(f"  FAILED {name}: {exc}", file=sys.stderr)
            failed.append(name)
    print(f"# {ok}/{len(mapping)} variables set")
    if failed:
        print(f"# NOT set (re-run to retry; idempotent): {failed}", file=sys.stderr)
    receipt["set_ok"] = ok
    receipt["set_failed"] = failed

    # --- redeploy ---------------------------------------------------------
    if ok and redeploy:
        try:
            _graphql(_REDEPLOY, {"e": env_id, "s": svc}, token)
            print("# redeploy triggered — the worker will restart with the new config")
            receipt["redeploy"] = "TRIGGERED"
        except RailwayError as exc:
            print(f"# redeploy failed (vars apply on the next deploy): {exc}", file=sys.stderr)
            receipt["redeploy"] = f"FAILED: {exc}"
    elif redeploy:
        receipt["redeploy"] = "SKIPPED (nothing was set)"
    else:
        receipt["redeploy"] = "NOT REQUESTED (vars apply on the next deploy)"

    # --- after ------------------------------------------------------------
    if not verify:
        receipt["verdict"] = "FAILED" if failed else "APPLIED_BUT_UNVERIFIED"
        print(f"# VERDICT: {receipt['verdict']} (verification not requested)")
        return (1 if failed else 0), receipt
    mismatched: list[str] = []
    try:
        after = {k: v for k, v in read_vars().items() if k in mapping}
    except RailwayError as exc:
        after = None
        print(f"# post-state unreadable: {exc}", file=sys.stderr)
    if after is not None:
        print("# AFTER:")
        for name in sorted(mapping):
            if name in after:
                print(_echo(name, after[name], "  "))
            else:
                print(f"  {name}=(unset)")
            if str(after.get(name)) != str(mapping[name]):
                mismatched.append(name)
            receipt["changes"][name] = {
                "before": _describe(name, before.get(name)) if before is not None else "(unread)",
                "after": _describe(name, after.get(name)),
                "requested": _describe(name, mapping[name]),
            }
    if failed:
        receipt["verdict"] = "FAILED"
    elif after is None or mismatched:
        receipt["verdict"] = "APPLIED_BUT_UNVERIFIED"
        if mismatched:
            print(f"# read back DIFFERENT from requested: {sorted(mismatched)}",
                  file=sys.stderr)
    else:
        receipt["verdict"] = "VERIFIED"
    print(f"# VERDICT: {receipt['verdict']}")
    if receipt["verdict"] != "VERIFIED":
        print("# the change is NOT confirmed — re-read with {\"type\":\"env\"} before "
              "acting on it", file=sys.stderr)
    return (1 if failed else 0), receipt


def _describe(name: str, value) -> str:
    """A value as it may appear in a RECEIPT — same redaction rule as output."""
    if value is None:
        return "(unset)"
    text = str(value)
    if name in REDACTED_VARS:
        return f"<redacted {len(text.encode('utf-8'))} bytes, sha256:{_digest(text)[:16]}>"
    return text


def main(argv: list[str] | None = None) -> int:
    """CLI: reads OPS_ENV_SET (JSON) to set, else prints current allowlisted vars."""
    raw = os.environ.get("OPS_ENV_SET", "").strip()
    if raw:
        return run_set(json.loads(raw))
    return run_get()


if __name__ == "__main__":
    raise SystemExit(main())
