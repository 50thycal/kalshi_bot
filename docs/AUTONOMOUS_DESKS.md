# Autonomous ChatGPT and Claude desks

**Implementation contract:** DEC-018 · WS-021 · default off. This document describes the
new isolated service and its launch requirements, not a deployed or funded service.
The owner approved this design and implementation on 2026-09-20. Both new sessions must
be ready before one common live start. The existing manual desk and its ledger remain
legacy history; no existing picks count toward either new book.

## Purpose and authority

Compare two operating research desks: ChatGPT and Claude. Each searches for information
edges, places small researched orders, grades forecasts and actual fills, learns, and
chooses its next investigation. Both may read each other's work and historical repo
research. Borrowed ideas identify their origin; this is an operating-desk comparison,
not a blinded or controlled model benchmark.

The service lives in `kalshi_bot/desks/`. Its configuration uses only `DESKS_` variables,
its database credentials and tables are separate, and its Kalshi keys are restricted to
distinct non-primary subaccounts. It does not import the worker's execution path, create
XOS tags, change Experiment OS variables, or write the manual ledger. DEC-018 is a
prospective exception for this specifically isolated service; all existing XOS rules
continue to bind existing workers and books.

A desk can own research code, collectors, source snapshots, optional paper observations,
and publications inside its allocation. It cannot change trading caps, shared executor
code, scoring definitions, another desk's records, funding, or paid-service allowances.
The operator credential controls common start and pause/resume. Desk credentials permit
own-desk submissions and shared reads. Do not put credentials, orders, balances, or
private API responses on the public `ops` branch.

## Fixed initial operating envelope

| Rule | Both desks |
|---|---|
| Starting bankroll | $30 each; $60 combined; no automatic replenishment |
| Per-pick ceiling | $1 including fees; actual supported quantity may spend less |
| Outstanding committed risk | $10 per desk including pending reservations |
| Daily filled picks | At most 3; zero is valid; America/Chicago day |
| Daily new attempts | At most 10; retries preserve the decision identity |
| Fill counting | Any partial fill consumes a pick; confirmed zero fill releases its slot |
| Event concentration | One filled pick per underlying event per desk |
| Initial orders | Price-limited immediate-or-cancel; no price chasing |
| Exit | Hold to settlement |
| Initial comparison | 30 calendar days after the common start; unsettled exposure shown separately |
| Promotion | Never automatic; no sizing increase from a streak or sample milestone |
| Extra paid research | $0 authorized by default; no automatic paid API calls |

A pending or unknown order reserves its slot and budget until its state is reconciled.
Timeout does not prove a zero fill. The service records intent before exchange submission,
uses stable client-order identity, and pauses affected trading when reconciliation is
uncertain. No duplicate POST should be used to discover whether the first POST succeeded.
Exchange state, not an AI's text, supplies fills, fees, and settlement cash flows.

## Research and evidence

A useful cycle narrows the available board to roughly ten candidates, investigates three
to five, and submits zero to three decisions. Those are research guidelines, not quotas.
A well-supported no-pick is successful research. A large note count is not progress.
The shared public board reader progressively scans two bounded pages per interval and
persists its cursor and market cache. Both desks receive the same interval snapshot;
coverage reports pages, cached markets, and completed passes. Binary-market shortlists
are diversified across events/series; combinations are excluded. Cached quote timestamps
remain visible. Do not describe an incomplete pass as the entire exchange, and fetch fresh
full market rules and settlement-source evidence before a trading decision.

Every decision uses the strict `Decision` schema in `contracts.py`: desk/round identity,
market and event, side, quote timestamp, observed and maximum prices, fee-inclusive spend,
probability and uncertainty range, expected net profit, settlement source and rules hash,
evidence snapshots and hashes, thesis, strongest counterargument, invalidation condition,
expiration, author model, session/scheduled origin, and borrowed references. Prices and
money are decimal dollars; probabilities are 0–1. Evidence must predate the decision.
The executor independently refreshes market and fee data before placing an order.

Publications include candidates, rejections, paper observations, sources, lessons,
postmortems, and handoffs. Keep paper fill assumptions explicit and never combine paper
P&L with actual money. Every settlement gets a brief review; losses and process failures
get a deeper postmortem. Link the publication's `decision_id` to the decision reviewed.
Preserve original pre-outcome predictions and publish revisions rather than overwriting
what was known. Do not treat unfilled hypothetical profits as cash profits.

## Persistent supervision and minimum operator effort

`serve` runs recurring collection/research scheduling, reconciliation, and status refresh.
Jobs, claims, results, and budgets are durable. A new chat resumes its desk rather than
creating a third desk or resetting the book. Research failure does not prevent order
reconciliation. A research-only Continue action cannot start a round, unpause trading,
increase budgets, or override a refusal.

Two cognition transports are implemented:

- **External scheduled runner:** a separately supported session/agent runtime claims jobs,
  researches, and returns structured results. This is the default zero-additional-budget
  route. The API is the bridge, not proof a scheduled ChatGPT/Claude session exists.
  Each bridge must be connected and verified before claiming unattended readiness.
- **Paid model provider:** explicit OpenAI/Anthropic model and dedicated API key, explicit
  current input/output prices, and a positive operator-approved equal monthly allowance.
  Reservations precede calls; uncertain billing keeps its reservation until reconciled.
  No paid allowance was granted by the implementation request.

The service does not keep an ordinary chat alive, install a scheduled session on the user's
behalf, or provide unlimited browsing/code execution to a model API. Provider cycles use
bounded market context and allowlisted source fetching. External sessions can develop
additional desk-owned research tools within their allowed environment. Tool or network
limitations are recorded, never bypassed by quietly changing another worker's permissions.

Health reports distinguish healthy, recovering, and needs-operator states, with reasons.
The current implementation monitors completed cycles, recent failures, research-job and
settlement-review backlogs, capital, and research budgets. Defaults include a 24-hour
completed-cycle freshness check, repeated research failures, and three unreviewed
settlements. These are operational checks, not tests that a strategy is profitable.
Research output includes its next action so stagnation can be inspected; proving that a
model learned correctly still requires reviewing its evidence.

Recoverable errors get bounded recovery and a durable note. Unknown orders, accounting
problems, exhausted resources, and persistent blockers require attention. Pausing new
trades does not discard open positions or stop reconciliation. An operator HTTPS alert webhook must be configured and successfully tested through
operator-only `POST /api/alerts/test` before live readiness. Delivery state is durable;
material conditions are deduplicated and failures receive bounded retries. The public
HTTPS destination is validated and no secret URL appears in persisted error strings.
Alerts report affected desk and reason; inspect authenticated state for recovery detail
and the action required. Routine minor decisions belong in desk state rather than questions.

## Comparison

The authenticated dashboard and status API expose each book's resources, decisions,
research health, and readiness. The initial comparison computes realized P&L after fees,
actual dollars deployed, return on deployed dollars, fill rate, and binary-forecast Brier
score, mean forecast, and realized win rate. Settled forecasts include unfilled decisions;
void/scalar settlements are excluded from binary forecast scores. Provider resource costs
are separately visible; do not call trading P&L all-in profitability.

The initial metrics do not establish statistically proven edge. Shared ideas and correlated
events limit independence; report provenance and event counts when analyzing results.
Before expanding the scoreboard, retain stable definitions and register changes prospectively.

## Setup and launch runbook

Use the repository's Python environment. Provision a separate database/database role with
access only to desk-owned tables; production state requires durable Postgres. A scratch
SQLite database is for development tests only. Do not reuse `DATABASE_URL`, the main worker
credentials, Evo credentials, or the public ops transport. Deployment configuration remains
an operator-owned launch step after code integration.

```bash
python -m kalshi_bot.desks init
python -m kalshi_bot.desks status
python -m kalshi_bot.desks serve
```

Use `.env.desks.example` as the configuration reference; the service does not automatically
load that file. Set the dedicated environment through the hosting platform's secret manager.
`railway.desks.json` is a separate-service deployment configuration, not an edit to existing
workers. For deployment use `DESKS_HOST=0.0.0.0` and match `DESKS_PORT` to the platform's
assigned `PORT` (or omit the explicit port to use its fallback). Core settings
are `DESKS_DATABASE_URL`, three distinct cryptographically random tokens of at least 32 characters:
`DESKS_OPERATOR_TOKEN`, `DESKS_CHATGPT_TOKEN`, and `DESKS_CLAUDE_TOKEN`, plus `DESKS_ROUND_ID`. `DESKS_LIVE_ENABLED` defaults false. Bind the
service behind an authenticated TLS reverse proxy; the local default host is loopback.
The public `/healthz` only proves the HTTP process responds. It does not prove desks are
ready, funded, conducting research, or allowed to trade.

Before live start, verify all of the following with concrete evidence:

1. Two distinct non-primary Kalshi subaccounts with $30 each, dedicated restricted API
   keys, and no inherited positions/orders. Account eligibility must support this.
2. Existing main/evo workers' credentials and portfolio reads cannot aggregate or manage
   these subaccounts. Distinct labels in a database are insufficient. Setting
   `DESKS_EXISTING_WORKERS_ISOLATED` records an actual verified fact, not permission to
   skip the check. If existing workers cannot be isolated, live launch remains blocked.
3. Each desk's scheduled external runner works, or explicit paid model configuration and
   approved spending are available. No paid key/allowance is inferred from a chat subscription.
4. Private durable storage, HTTPS, appropriate desk/operator tokens, operator alert delivery,
   and restart/reconciliation behavior are verified.
5. Both fresh sessions read their startup packets and mark only their own desk ready.
6. The operator starts the common round through the authenticated endpoint after readiness
   is clean. Both books receive the same start timestamp; restart never resets it.

This build does not create/fund subaccounts, change production environment variables,
start a competition, or place a live order. The startup sessions complete preparation before
that common launch. Do not rewrite existing legacy picks into the new books.

## Authenticated API contract

Send `Authorization: Bearer <dedicated-role-token>`. POST bodies are JSON; do not place
tokens in URLs. The UI shell is public but data APIs require authentication.

| Method/path | Role and purpose |
|---|---|
| GET `/api/status` or `/api/context` | Any authenticated role; shared desk state and readiness |
| GET `/api/decisions/{decision_id}` | Any authenticated role; immutable decision and execution state |
| POST `/api/desks/{desk}/ready` | Own desk or operator; `{}`; session setup complete |
| POST `/api/desks/{desk}/continue` | Own desk or operator; `{}`; request research, not activation |
| POST `/api/desks/{desk}/decisions` | Own desk or operator; strict `Decision` JSON |
| POST `/api/desks/{desk}/publications` | Own desk or operator; `kind`, object `payload`, optional `record_id` |
| POST `/api/desks/{desk}/claim` | Own desk or operator; `worker_id`; lease next external job |
| POST `/api/desks/{desk}/source` | Own desk or operator; `job_id`, `claim_token`, `url`; capture allowlisted source evidence |
| POST `/api/desks/{desk}/complete` | Own desk or operator; `job_id`, `claim_token`, `model_id`, `payload` |
| POST `/api/desks/{desk}/pause` | Operator only; `reason` |
| POST `/api/desks/{desk}/resume` | Operator only; `{}`; never fixes underlying readiness failures |
| POST `/api/alerts/test` | Operator only; `{}`; test and durably verify the configured alert destination |
| POST `/api/round/start` | Operator only; `{}`; readiness-checked common start |

`desk` is exactly `chatgpt` or `claude`. Research completion payloads follow `ResearchOutput`
in `research.py`, including summary, candidates/rejections, optional paper observations,
lessons, decisions with source references, and next action. The job claim token belongs to
one desk/job and expires with its lease; do not share it or invent source IDs. API schema
and refusal codes are authoritative; scripts should not scrape dashboard HTML.

## Session bridge CLI

`scripts/desk_client.py` reads `DESK_SERVICE_URL` and `DESK_SESSION_TOKEN` from the session
runner's environment, separately from the service's `DESKS_*` configuration. Use the
appropriate desk token, not the operator token, for each scheduled research runner.
The URL must use HTTPS; loopback HTTP is allowed only for development. Keep payload and
result files private because they may contain account state or temporary claim tokens.

```bash
python scripts/desk_client.py status
python scripts/desk_client.py --desk chatgpt ready
python scripts/desk_client.py --desk chatgpt claim --worker-id scheduled-chatgpt
python scripts/desk_client.py --desk chatgpt source --file source-request.json
python scripts/desk_client.py --desk chatgpt complete --file research-result.json
```

The source request file contains `job_id`, `claim_token`, and `url`. The completion file
contains `job_id`, `claim_token`, `model_id`, and a strict `ResearchOutput` in `payload`.
Other supported commands are `continue`, `publications`, and `decisions`; pass their JSON
body through `--file` when needed. Replace `chatgpt` with `claude` for that runner. This
CLI does not create or schedule either external session and cannot start the round.

Startup packets: [ChatGPT](desks/CHATGPT_START.md) · [Claude](desks/CLAUDE_START.md).
