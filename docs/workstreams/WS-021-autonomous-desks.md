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
and the historical manual ledger remain separate. The code starts disabled and cannot
be considered unattended until a scheduled runner or approved paid provider is connected.

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
working scheduled session bridges. If an external bridge cannot run under existing resources,
the owner decides any new paid API allowance; no cost is inferred from build approval.

## Assumptions

- The account can provision distinct restricted non-primary subaccounts. If not, live launch
  remains blocked; database labels are not a substitute.
- Existing worker access can be proven not to aggregate desk orders or positions.
- Scheduled model access is an external dependency, not a capability of an idle chat.

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

Implementation complete; review and CI verification pending. Five breakout sessions built
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
  writes were used for verification. Current CI and browser results belong on the PR.
No deployment or live activation performed by this workstream.

## Review State

**Verdict:** Implementation self-check complete; owner review pending
**Accepted head:** —
**Related PR:** —
**Finalization:** —

The repo remains in solo mode. Plan/build approval is recorded, not represented as acceptance
of a finished implementation, independent review, or a live activation verdict.

## Related Decisions

DEC-018; DEC-017 retained as historical manual-desk policy; DEC-001 authority boundary.

## Related PRs

Pending coordinating session.

## Parked

None.

## Next Step

Open the implementation PR, verify CI including PostgreSQL concurrency, and hand off for owner review.
