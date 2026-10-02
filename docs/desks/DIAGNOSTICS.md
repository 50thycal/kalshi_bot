# Desk-service diagnostics over the ops channel

A development session can diagnose **desk-service** (Railway project `kalshi-desks`,
`https://desk-service-production.up.railway.app`) without anyone copying Railway console
output or screenshots. It pushes one ops request and reads back its own sanitized report.

The path is read-only end to end and adds exactly one credential: a **diagnostic-role
token** that the desk service accepts on **one** route, `GET /api/diagnostics`. The operator
token and both desk tokens never reach GitHub Actions.

## What a session does

```bash
git fetch origin ops && git worktree add /tmp/ops ops        # once
cd /tmp/ops && git fetch origin ops -q && git reset --hard -q origin/ops
ID="desk-diag-$(date -u +%Y%m%dT%H%M%SZ)"                     # unique per request
echo "{\"type\":\"desks\",\"id\":\"$ID\"}" > ops/request.json
git add -A && git commit -q -m "ops: desk diagnostics $ID"
for i in 1 2 3 4 5 6; do git fetch origin ops -q
  git rebase origin/ops -q 2>/dev/null || git rebase --abort 2>/dev/null
  git push -q origin ops 2>/dev/null && break; sleep $((i * 3)); done
for i in $(seq 1 20); do sleep 15; git fetch origin ops -q
  git show "FETCH_HEAD:ops/results/$ID.txt" 2>/dev/null && break; done
```

Then reset `ops/request.json` to `{"type":"noop"}` as usual. Read **only** your own
`ops/results/<id>.txt` (and `<id>.receipt.json`); results are pruned after the newest 80.

### Request format

```jsonc
{"type": "desks", "id": "desk-diag-20261002T120000Z"}                       // default
{"type": "desks", "id": "desk-diag-…-2", "market_probe": false}            // skip the market GET
{"type": "desks", "id": "desk-diag-…-3", "format": "json"}                 // machine-readable
```

| field | rule |
|---|---|
| `type` | `desks` |
| `id` | **required**, 3–64 of `A-Za-z0-9._-`, and not already present under `ops/results/` (a reused id is refused so you never read someone else's report) |
| `market_probe` | optional boolean, default `true` |
| `format` | optional `text` (default) or `json` |
| `actor`, `purpose`, `workstream`, `issue` | optional public provenance, as on every ops request |

Any other field is refused before a network call. It is classified `READ`, runs on the
fast stdlib path, and makes **one** HTTPS GET with a 20 s timeout and no retry.

### Reading the result

The first lines decide what you are looking at:

| line | meaning |
|---|---|
| `DIAGNOSTIC : OK` + `VERDICT : HEALTHY` | the service answered fully and nothing needs attention; run exit 0 |
| `DIAGNOSTIC : OK` + `VERDICT : ATTENTION` | the diagnostic succeeded; the desk needs attention — see `ATTENTION`; run exit 0 |
| `DIAGNOSTIC : INCOMPLETE` | the service answered but some sections failed (`FAILED SECTIONS`); run red |
| `DIAGNOSTIC : FAILED — <code>` / `VERDICT : UNKNOWN` | **not a health result** — the desk was not assessed; run red |

Failure codes: `invalid_request`, `not_configured` (secret missing — see provisioning),
`invalid_configuration`, `authentication_refused` (401/403), `endpoint_not_deployed` (404:
the deployed revision predates this feature), `redirect_refused`, `request_rejected`,
`service_rate_limited`, `service_error` (5xx), `unexpected_status`, `timeout`,
`connection_failed`, `response_too_large`, `invalid_response`, `redaction_guard`.

`ATTENTION` codes include `monitor_stale`, `worker_error:<code>`, `<desk>_paused`,
`<desk>_research_<recovering|needs_operator>`, `<desk>_latest_job_failed:<category>`,
`public_market_rate_limited` and `public_market_probe_<timeout|connection_failure|http_error>`.
Launch-readiness blockers are reported separately and are **not** attention by themselves
(`live_execution_disabled` is normal while research runs).

### What is reported

| section | fields |
|---|---|
| service | research/alert/account mode, live-enabled flag, round id, round started, worker last tick + age, monitor fresh (≤180 s), worker error code |
| desks | per desk: health, activity, health reasons (codes), ready, paused, pause **category**, book status, completed cycles, pending jobs, recent failures, last completed cycle, unreviewed settlements |
| readiness | launch-ready flag and blocker codes from the **cached** checks (nothing refreshed) |
| jobs | the newest 8 research jobs per desk: job id, desk, state, error category, created/updated, lease active |
| board | claim board: pages seen, completed passes, cached markets, sample size, snapshot age, continuation pending, scan lease active + seconds left; browse index: built or not, age, TTL, events/pages/markets, complete, scan in progress |
| market probe | outcome, HTTP status, latency, sanitized `Retry-After` (seconds, or "present but invalid"), whether a request was sent now, cache/cooldown state |

**Never reported:** credentials, connection strings, claim tokens, worker IDs, raw API
bodies or headers, exception text, research payloads, publications, decisions, cash,
balances or P&L. An operator's free-text pause reason is shown only as
`operator_text_redacted`; any value that is not an enumerated code becomes `unrecognized`
(service) or `[redacted]` (runner). Both the service and the runner apply a fixed schema
projection, and the runner withholds any output that contains the configured token.

### The market probe

One unauthenticated `GET https://api.elections.kalshi.com/trade-api/v2/markets?limit=1&status=open`
**from the desk service's own egress** (that is the IP whose throttling matters). Single
attempt, 5 s timeout, no redirects, no environment proxies, body never read. Its result is
reused for 60 s; after an HTTP 429 no request is sent until `max(Retry-After, 120 s)` has
passed (capped at one hour) — the report then says `skipped_rate_limit_cooldown` with the
remaining seconds and the previous result. No order, portfolio or authenticated exchange
endpoint is touched. Use `"market_probe": false` while you already know the service is
throttled.

## Authority

Read-only throughout. The diagnostic role cannot read `/api/status`, `/api/context`,
decisions, the schema or the market browser, and every POST (Continue, claim, source,
complete, decisions, publications, ready, pause, resume, preflight, alert test, start) returns
`403 role_forbidden` before a body is read. The report itself is built from reads only: it
never refreshes isolation/preflight, never builds the market index, never claims or retries a
job, and writes nothing to the database. A diagnostic result authorizes nothing; recovery
remains the existing operator-authorized cycle in [APP_SESSIONS.md](APP_SESSIONS.md).

## Provisioning (operator, private — not done by any PR)

Until all three steps are done the request answers `FAILED — not_configured` (or
`endpoint_not_deployed` before the deploy). Each step is a private action; never paste the
token into chat, a commit, an ops request or a handoff.

1. **Deploy** this revision to `kalshi-desks / desk-service` and set a new random token
   (≥32 characters, different from the operator and desk tokens), e.g.
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`, as Railway variable
   `DESKS_DIAGNOSTIC_TOKEN` on desk-service. Setting it redeploys the service.
2. **GitHub secret:** repository → Settings → Secrets and variables → Actions → New
   repository secret `DESKS_DIAGNOSTIC_TOKEN`, same value.
3. **Ops workflow passthrough** (hard stop: the `ops` workflow file). Actions loads
   `ops-runner.yml` from the `ops` branch, so the default-branch copy changed by this PR is
   inert until the same one passthrough line is committed onto `ops` as an ordinary
   fast-forward, following the idle-channel and validation steps in
   [OPS_RUNBOOK.md](../OPS_RUNBOOK.md) ("changing the workflow file"):
   ```yaml
             DESKS_DIAGNOSTIC_TOKEN: ${{ secrets.DESKS_DIAGNOSTIC_TOKEN }}
   ```
   in the `Run ops request` step's `env`. No force push and no merge of `ops`.

Then validate with one `{"type":"capabilities"}` (the `desks` line should read
`configured`) and one `{"type":"desks","id":"…"}`. Rotating: change the Railway variable and
the GitHub secret together. Revoking: unset the Railway variable — the role then matches
nothing.

Optional: `DESKS_DIAGNOSTIC_URL` (a plain HTTPS base URL, no path/query/credentials) overrides
the default service URL for the runner; it is not needed for production.
