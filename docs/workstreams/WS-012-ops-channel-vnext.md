# WS-012 — Ops channel vNext: reliability, introspection, verified operations

**Phase:** CLOSED
**Status:** Done
**Created:** 2026-08-30
**Updated:** 2026-09-02

## Goal

Make the ops channel's real authority discoverable, its failures visible, its
production changes explicit and verified, and its meaningful mutations durable —
without widening what it is allowed to do.

## Context

The `ops` branch stopped being a log/DB scratch channel some time ago. It is now
the only path by which a session reads production, runs canonical Experiment OS
reads, runs approved probes, and — within a tight allowlist — changes the running
configuration of a live trading worker. The machinery grew a capability at a time
and the surrounding contract did not keep up:

- a failed request published its error **and left the Actions run green**, so run
  status carried no information;
- `ops/README.md` still described logs, read-only DB and noop — three of the nine
  request families, and none of the mutating one;
- the only way to learn the current allowlists was to read the source or trust
  prose, which is the XOS-000005 failure mode that this channel has already had
  once in production;
- `{"type":"env"}` and `{"type":"env","set":{…}}` differed by one JSON key
  despite differing entirely in authority, and nothing in the result said which
  had happened;
- a mutation reported what it had ASKED FOR (`set + redeploy requested`), never
  what the system then said;
- establishing operating context cost five or six round trips, re-derived from
  prose each session — which is how two sessions ended up holding different
  pictures of the same production system.

The build specification is PR #292 (`chatgpt/ops-channel-vnext-spec`), audited
against the live code before implementation rather than taken as given.

## Current Mental Model

```text
  request (public transport branch)          runner (DEFAULT-branch code)
  ---------------------------------          ----------------------------
  ops/request.json  ──push──> workflow ──> ops_runner.serve()
        │                        │              │  header: READ | MUTATING
        │                        │              ├─ reads:  logs db script xos
        │                        │              │          capabilities doctor incident
        │                        │              └─ env:    read, or verified mutation
        │                        │                          ├─ before → apply → readback
        │                        │                          ├─ verdict VERIFIED / …
        │                        │                          └─ canonical enforcement+readiness
        │                        ▼
        │                 publish result + receipt ──> ops/results/<id>.{txt,receipt.json}
        │                 audit-worthy receipt     ──> ops-audit branch (append-only)
        └─ exit status ──> the run is RED when the request failed
```

`ops_meta` is the single source of truth for the three questions that used to be
answered in three places: what request types exist, whether a request is a READ
or MUTATING, and whether it needs the full dependency set. `capabilities` prints
it, the workflow calls it, and the parity tests assert against it.

## Decisions

- [DEC-009](../DECISIONS.md#dec-009) — an ops request declares its intent, and a
  production change is verified rather than assumed.

## Boundary

This workstream changes the OPERATING channel only. It registers nothing,
promotes nothing and evaluates nothing: Experiment OS remains canonical for
lifecycle, gates, evidence and enforcement, and `doctor`/`incident` read it
through the canonical CLI rather than forming a second opinion. The channel stays
read-only against Postgres; the worker remains the only writer.

## Outcome

Three PRs, all merged:

- **[#294](https://github.com/50thycal/kalshi_bot/pull/294)** — the full build:
  exit-status fix, `ops/README.md` rewrite, `capabilities`, `doctor`,
  `incident`, structured receipts + provenance, explicit mutation vocabulary
  with post-change verification verdicts, the `ops-audit` archive.
- **[#306](https://github.com/50thycal/kalshi_bot/pull/306)** — an unrecognised
  request type is `UNCLASSIFIED`, never `READ`. Found by the production round
  trip below, not by the 69 unit tests written for #294.
- **[#313](https://github.com/50thycal/kalshi_bot/pull/313)** — `doctor` was
  reading `main`'s Railway id back out of the same variable
  `_select_service` had just overwritten while walking `evo`/`livedash`/`main`
  in one process, so `main` inherited `livedash`'s deployment and empty
  variables. A live-armed trading worker was rendered as disarmed, with its own
  `REAL MONEY IS ARMED` banner suppressed — found by the same round trip.
  Re-confirmed against production after merge: `main` now reports its own
  deployment and full runtime config.

The workflow file was deployed to `ops` as a fast-forward commit in an idle
window and validated with a real round trip: `{"type":"capabilities"}` came
back green with a published receipt
([run #3448](https://github.com/50thycal/kalshi_bot/actions/runs/33581506036)),
and a deliberately unrecognised request turned the run **red**
([run #3449](https://github.com/50thycal/kalshi_bot/actions/runs/33581667899))
— the P1 bug, proven fixed in production rather than only in tests.

## Open questions carried past close

1. **Ruleset verification — still NOT done.** The intended policy is recorded
   in `docs/OPS_RUNBOOK.md` and checked locally by
   `tests/test_ops_branch_protection.py` against
   `.github/rulesets/ops-transport-guard.json`. Retrieving the LIVE
   configuration from GitHub needs an admin-scoped token no session has held.
   Pushes to `ops` this workstream made *behaved* consistently with the
   intended policy (ordinary fast-forwards succeeded; nothing attempted a
   force-push or deletion to test the block side) — that is corroborating
   behavior, not verification, and should not be read as one. Pick up when a
   session next holds admin-scoped GitHub access.
2. **XOS issue for the #313 defect — prepared, not sent.** Owner: LIVE_OPS.
   Sending it sets `EXPERIMENT_OS_ISSUE_COMMAND`, which redeploys the worker;
   held because the worker was live-armed on `Dmmsell10` with resting
   real-money orders at the time. The ready-to-send `OPEN_MANUAL` envelope is
   in #313's Follow-up Work section. This does not block closing this
   workstream — it is an Experiment OS follow-up, not an ops-channel one.

## Next step

None for this workstream — it is closed. The two items above are follow-ups
tracked by their owning role, not blockers on WS-012.
