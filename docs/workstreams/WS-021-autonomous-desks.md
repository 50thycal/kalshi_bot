# WS-021 — Autonomous ChatGPT and Claude research desks

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-09-20
**Updated:** 2026-09-20
**Build OS:** v0.12

## Goal

Deliver an isolated two-desk service so ChatGPT and Claude can research, trade within equal
small limits, learn, and continue with minimal operator involvement. Prepare equivalent
fresh-session startup packets and one readiness-checked common start.

## Context

The owner explicitly requested two competing desks, automatic execution and scheduled
research, accepted the operating recommendations with autonomy and filled-pick amendments,
and requested implementation with breakout sessions. This is an explicit admission of new
work despite the already-over-limit board; no unrelated workstream is paused by inference.
Canonical Build OS v0.12 was checked by the coordinating implementation session.

## Current Mental Model

Separate service/database credentials and desk tables; two desk identities and restricted
Kalshi subaccounts; shared deterministic execution/accounting; durable research jobs and
publications; authenticated status dashboard and per-role API. Existing worker/XOS paths
and the historical manual ledger remain separate. The code starts disabled. DEC-019 adds app-driven sessions; scheduled mode still requires
a working runner or approved provider and must not be inferred from an inactive chat.

## Decisions Made

- Owner approved design/build in the 2026-09-20 ChatGPT thread; DEC-018 records the scope.
- $30 per desk, $10 committed, $1 fee-inclusive picks, at most three FILLED picks and ten
  attempts per America/Chicago day, IOC, hold to settlement, one event per desk.
- Identical common start and rules; shared research visibility with origin/provenance.
- Autonomous research and optional paper work belong to each desk; routine recovery does
  not need operator input. No paid model budget or replenishment approved.
- Existing XOS safeguards remain intact; verified financial isolation is a launch condition.

## Open Decisions

No unresolved product decision blocks the build. Deployment must provide verified isolated
subaccounts, dedicated credentials, private durable storage, operator alert delivery, and
working private app-session bridges (or scheduled bridges when that mode is selected). If an external bridge cannot run under existing resources,
the owner decides any new paid API allowance; no cost is inferred from build approval.

## Assumptions

- The account can provision distinct restricted non-primary subaccounts. If not, live launch
  remains blocked; database labels are not a substitute.
- Existing worker access can be proven not to aggregate desk orders or positions.
- Private app-to-service access is an external dependency. An idle chat performs no research.

## Non-Goals

- Modify or activate existing XOS books, lifecycle states, environment variables, or ops workflow.
- Reclassify legacy manual picks, merge this PR, deploy, fund subaccounts, or start live trading.
- Automatically expand stake, bankroll, model spend, or strategy eligibility.
- Claim statistical proof or a blinded comparison from a small shared-research experiment.

## Material Interrupt Risks

Unknown exchange order state; concurrent reservations exceeding caps; source/rule drift;
cross-desk or existing-worker interference; credential exposure; unknown provider billing.
Each requires fail-closed behavior or explicit disclosure, not a silent optimistic default.

## Acceptance Checks

Continuation checks: a configured research command completes a leased job unattended;
source requests are captured by the service; ambiguous completion retries do not rerun
research or trade submission; private local state survives a worker restart; launch checking
is read-only and reports missing dependencies without claiming readiness.

- Dedicated configuration/storage/service does not alter the existing worker execution path.
- Both sessions have equivalent startup packets; one durable start requires both ready.
- Fee-inclusive caps, committed exposure, daily attempts/filled picks, event concentration,
  idempotency, partial/unknown fills, and restart recovery have focused regression coverage.
- Decisions and source provenance precede orders; stale or invalid evidence is refused.
- Research jobs, candidate rejections, optional paper records, settlement review and next-action
  memory survive sessions. Missing scheduled runtime or budget is visible, never fabricated.
- Authentication enforces shared reads and own-desk writes; start/pause/resume are operator-only.
- Comparison separates cash performance, forecast grades, and research costs.
- The dashboard exposes health/readiness; missing isolation, credentials, funding or alert
  delivery cannot be described as live readiness.
- Validation results, material implementation limits, and launch guards are on the PR.

## Build Card

Build the isolated service in `kalshi_bot/desks/`, with deterministic spend/slot reservation,
exchange reconciliation, durable research supervision, authenticated API/dashboard, and
startup documentation. Ship a reviewable PR. The runtime and external resources are launch
guards, not assumed side effects of merge. Build spec: `docs/AUTONOMOUS_DESKS.md` and strict
contracts in `kalshi_bot/desks/contracts.py`/`research.py`.

## Implementation State

PR #443 merged at `d1736c7c`. The owner requested continued implementation after merge.
The continuation supplies the external research worker, saved-login Codex/Claude adapters,
safe completion acknowledgement replay, and a read-only launch check. It does not deploy
or activate trading. Model clients still require provisioned supported access and a host.

Continuation validation: repository lint and diff checks pass; 235 desk/session tests pass
locally with the PostgreSQL concurrency case reserved for CI. Real local HTTP and subprocess
integration covers both model adapters using fake native clients, service-captured evidence,
publication, lost acknowledgement, and restart without regenerating research. No real model
calls were made. Final CI results are recorded on [PR #445](https://github.com/50thycal/kalshi_bot/pull/445).

The initial implementation used five breakout sessions that built
storage/scoring, execution, research supervision, API/dashboard, and startup documentation.
The coordinating session integrated and checked the full offline research-to-settlement path.

Validation before PR:
- Repository lint passes; 141 desk/session tests pass; the PostgreSQL concurrency proof
  requires CI's PostgreSQL service and was skipped locally.
- Full repository run: 4,743 passed, 12 skipped, one pre-existing arbitrage fixture failure.
  Reproduced that failure on unchanged base `39a8aebc5865abfc65c4752817676566efa9b2de`.
  The fixture broke even after per-leg fees; corrected its prices to model positive edge,
  without changing scanner implementation. All 16 scanner tests now pass.
- No live orders, provider charges, alert messages, deployment or production configuration
  writes were used for verification.
- Browser authentication/status/sign-out pass with no page errors; tokens remain memory-only.
  Mobile rendering passes at 320, 390, 768 and 1280 pixels with long identifiers.
- Current final-head CI results, including PostgreSQL verification, are recorded on PR #443.
Historical initial-build checkpoint above excludes later setup: PR #447 provisioned the
separate service/database and subsequently stopped the deferred runners. No live activation
has occurred. Current app-session deployment dependencies are recorded below.

## Review State

**Verdict:** App-session PR #448 self-check complete, pending owner acceptance. No acceptance inferred from earlier merges.
**Accepted head:** —
**Related PR:** [#448](https://github.com/50thycal/kalshi_bot/pull/448) (app sessions); [#445](https://github.com/50thycal/kalshi_bot/pull/445); initial implementation [#443](https://github.com/50thycal/kalshi_bot/pull/443) merged.
**Finalization:** Pushed; no owner acceptance or merge inferred.

The repo remains in solo mode. Plan/build approval is recorded, not represented as acceptance
of a finished implementation, independent review, or a live activation verdict.

## Related Decisions

DEC-018; DEC-017 retained as historical manual-desk policy; DEC-001 authority boundary.

## Related PRs

[#443 — Isolated autonomous research desks](https://github.com/50thycal/kalshi_bot/pull/443) — merged.

[#445 — External runners and launch diagnostics](https://github.com/50thycal/kalshi_bot/pull/445) — merged.

[#447 — Hosted runner recipe](https://github.com/50thycal/kalshi_bot/pull/447) — merged; runtime use deferred.

[#448 — App-session research and bounded live submissions](https://github.com/50thycal/kalshi_bot/pull/448) — current continuation.

## Parked

None.

## Next Step

Review PR #448 after CI; then complete the private-access and
account/alert prerequisites in docs/desks/APP_SESSIONS.md before common start.


## App-session continuation — DEC-019

Owner request: implement Go / Continue research and bounded live submission in each app,
without CLI login or unattended cognition. Significant change to the dedicated desk launch
mode; no shared XOS/platform semantics changed. Build OS v0.12 checked 2026-09-20.

Build card/spec: add explicit session mode, preserve scheduled default, disable background
job/provider scheduling in session mode, require a completed own-session cycle from both
desks for common start, and bind new orders to exact accepted unexpired completions.
Preserve all financial and evidence guards and reconciliation. Expose mode/activity and
schema; provide one-time operator preflight and equivalent app startup packets.

Acceptance: offline end-to-end evidence -> common start -> IOC -> settlement -> lesson in
both modes; idle sessions do not schedule; same-hour continuations work; expired/replayed
completions cannot duplicate orders; mode cannot invoke paid providers; role isolation and
all original launch guards still pass their regression tests. No real orders during tests.

Deployment is separately blocked on Calvin providing private bridge access for both apps,
restricted funded Kalshi subaccounts and verifiable existing-worker isolation, plus an
operator alert destination and delivery test. Never report ready merely because code merged.
The selected software mode requires no new product decision. Hosted recipe PR #447 merged during implementation; runtime use stays
deferred. Implementation and validation results belong in this continuation PR.

App-session validation: 227 passed, 1 skipped in the focused desk suite; the skipped
PostgreSQL concurrency case requires CI’s database. Repository lint and diff checks pass.
All exchange/source/model actions in these tests are offline substitutes; no live order,
model billing, account funding or production activation was performed.


## DEC-020 continuation — existing worker primary scoping

Owner approved preserving existing keys and limiting main through software. Build card:
explicit primary-account portfolio requests and V2 placements; local rejection of foreign
selectors; foreign response rejection; preserve cancellation under kill and shard routing.
No strategy, XOS lifecycle, funds, keys, or live-start mutation. Acceptance checks cover
scoped lists excluding a second desk, rejected foreign requests without network traffic,
foreign response refusal, original payload immutability, and cancellation routing.

Implementation: primary client boundary and regression tests published in draft [PR #449](https://github.com/50thycal/kalshi_bot/pull/449). Finalization: pushed; owner acceptance not inferred.
Validation: 322 tests passed across client, desk, execution telemetry and liquidity-incentive
executor suites; one PostgreSQL-only test skipped locally. Repository lint and diff checks pass.
The legacy V1 route remains a deployment verification requirement; its undocumented payload
is not silently changed. Existing unrestricted consumers outside the audited workers are
not claimed isolated. No live readiness flag has been set.
Next step: review the primary-account scoping PR; satisfy the shared execution Platform
Revision/impact merge guard, then verify deployment before resuming launch preflight.
