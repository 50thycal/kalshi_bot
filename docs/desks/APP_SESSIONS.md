# App-session desks: Go / Continue

DEC-019 / WS-021. Each app supplies its own cognition while the conversation is active.
No Codex/Claude CLI login or paid model API key is needed for this mode. The shared
service stays online for accounting, reconciliation, settlements and material alerts.
It cannot wake a chat or do research after a session stops.

## One-time deployment (operator)

Merge this PR into the default branch and deploy that revision to the existing **desk-service**
only. Do not deploy the existing main/evo workers or reactivate the dormant runners.
Set `DESKS_RESEARCH_MODE=session`, and keep both `DESKS_CHATGPT_PROVIDER=external` and
`DESKS_CLAUDE_PROVIDER=external`. Keep the paid research allowance zero.
`DESKS_EXTERNAL_RUNNERS_VERIFIED=false` is correct; do not falsely attest to a runner.
The default mode in code remains `scheduled` so existing deployments do not silently change.

The following access must be provisioned and verified before live activation:

- Dedicated persistent desk database and three distinct role tokens (already provisioned
  during hosted setup, but verify the target deployment).
- Two distinct non-primary Kalshi subaccounts, $30 each, no inherited orders or positions,
  distinct signing credentials restricted to their respective subaccounts. No funding is
  performed by this PR. Shared-account or unrestricted-key fallback is forbidden.
- DEC-020 permits the existing keys to remain unrestricted: verify deployed main/evo
  software explicitly scopes portfolio operations to primary account 0, including any
  legacy V1 route in use. Old keys retain broader permissions; unknown external consumers
  are not covered by this software boundary. Only after verifying active consumers set
  `DESKS_EXISTING_WORKERS_ISOLATED=true`. The desk service also probes own and forbidden
  subaccount reads. If the account cannot support restricted keys, launch is blocked.
- Operator HTTPS alert webhook, explicitly tested using `test-alerts` below.
- Each respective app's execution environment can reach the service over HTTPS and has
  **only its own** `DESK_SESSION_TOKEN` plus `DESK_SERVICE_URL` securely provisioned.
  GitHub access alone does not provide that bridge. Never put tokens in GitHub, chat,
  handoff text, URLs, or public ops. An app without private credentials/network tools
  cannot submit research or trades; report missing access instead of claiming readiness.

Start disabled. Each app completes a genuine source-backed research cycle without any
trade decisions, reads its published result, and marks only its own desk ready. This is
an access/research test, not an extra paper-trading promotion requirement.
After restricted credentials/funding/isolation are verified, set `DESKS_LIVE_ENABLED=true`
on the service. This enables the executor but does not start either book. Missing live
configuration fails service validation; do not set the flag before provisioning access.

The operator uses a private environment with `DESK_SERVICE_URL` and
`DESKS_OPERATOR_TOKEN`. From the repository root (with dependencies installed):

```sh
PYTHONPATH=. python scripts/desk_operator.py test-alerts
PYTHONPATH=. python scripts/desk_operator.py preflight
PYTHONPATH=. python scripts/desk_operator.py start
```

`preflight` refreshes read-only exchange checks, returns specific blockers, and exits 2
until ready. It neither sends an order nor starts the round. `start` is the deliberate
operator action after both sessions and all guards pass; both books receive the same
persistent timestamp. Check `status` after any lost response. The desk tokens cannot
preflight, start, pause or resume. Never give either research session the operator token.

**Deployment checkpoint:** the previous setup verified HTTPS/database and both idle
containers, but did not provision restricted exchange access, prove funding/isolation,
verify alert delivery, or establish both app bridges. This code PR cannot claim those
external requirements passed. Do not call the deployment ready until preflight passes.

## Each Go / Continue in the app

1. Read the startup packet, this guide, historical evidence and authenticated `status`.
   Resume the existing round and own desk; inspect prior publications, open/unknown orders,
   settlement-review backlog and resource limits. Do not reset or replenish the book.
2. Fetch `schema` for the actual `ResearchOutput` contract. Claim an own-desk research job
   with a stable worker ID (`chatgpt-app` or `claude-app`). A null claim means another
   active/completed job owns the work: inspect status, do not generate competing claims.
3. Save the claim locally with private permissions outside the repository. It includes a
   30-minute lease and captured market-board context. Research candidates with the app's
   tools. Capture any evidence used for a trade via the service's `source` operation;
   use returned IDs/hashes/timestamps verbatim, never invent them. At most eight additional
   source requests per job; unsupported domains are a recorded limitation.
4. Investigate the strongest candidates, settlement wording, base rates, uncertainty,
   contrary evidence and fee-adjusted price. Zero picks is valid. Develop useful own-desk
   research tools within available resources; no new paid allowance is implied.
5. Save the exact completion JSON before sending it. Include findings/rejections, lessons,
   settlement postmortems, next action and zero to three justified `decisions`. Set each
   decision's `origin=session`, the correct desk/round, and the same honest `author_model`
   as the completion's `model_id` (use an app label if the resolved version is unavailable).
   `source_requests` must be empty: capture sources before final completion.
6. Send `complete` before the lease expires. After common start, the service may execute
   accepted decisions immediately through the existing deterministic controls. A completed
   research job does **not** mean an order filled: inspect decision records and refusal
   publications. Read back the saved publication and preserve a concise handoff.
7. End with health, resources, filled picks, settled performance, latest lesson and next
   action. Wait for the next app continuation; never promise scheduled background work.

Use `umask 077` before writing private claim/result files. Commands (ChatGPT example):

```sh
python scripts/desk_client.py status
python scripts/desk_client.py schema
python scripts/desk_client.py --desk chatgpt claim --worker-id chatgpt-app
python scripts/desk_client.py --desk chatgpt source --file /private/path/source-request.json
python scripts/desk_client.py --desk chatgpt complete --file /private/path/research-result.json
python scripts/desk_client.py --desk chatgpt ready
```

`ready` is one-time own-desk setup after verifying a completed cycle; it is not a live start.
Use the Claude packet and `--desk claude` in the Claude app. The source file contains
`job_id`, `claim_token`, `url`; completion contains `job_id`, `claim_token`, `model_id`,
`payload`. `payload` follows the authenticated schema. Do not commit these private files.

If the completion acknowledgement is lost, resend the **identical saved completion**;
its durable result is idempotent, even after the lease expires. Do not create a new
trade/decision ID for an uncertain outcome. An expired unaccepted job cannot submit:
read status, preserve findings as a handoff, and obtain fresh evidence in a new job.
Long research should be split into bounded cycles. Publication recovery can preserve
notes after expiry, but cannot renew authority to place a new session order.

## Fixed controls

$30 initial capital per desk; $10 outstanding risk including pending orders; $1 per pick
including fees; at most three FILLED picks and ten new attempts per America/Chicago day;
one filled pick per event; capped IOC; hold to settlement; no replenishment, sizing
increase or paid-research expansion. Unknown orders retain reservations. Existing
workers/XOS and the legacy manual ledger remain outside this path.

The direct `decisions` endpoint cannot turn an old session's evidence into a new order:
new session orders must appear in the exact accepted completion under its original
unexpired lease. Continue never starts the round, unpauses a desk, changes budgets or
bypasses exchange/alert checks. Idle app sessions are normal, not missing-runner alerts;
repeated failures, unresolved billing, exhausted resources and learning backlogs remain
visible. Completed scheduled jobs do not establish app-session readiness.
