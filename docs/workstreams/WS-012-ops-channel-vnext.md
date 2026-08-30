# WS-012 — Ops channel vNext: reliability, introspection, verified operations

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-08-30
**Updated:** 2026-08-30

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

## Open questions

1. **Deploying the workflow change.** The executing copy of
   `.github/workflows/ops-runner.yml` lives on `ops` (Actions loads a workflow
   from the triggering branch). Merging this PR does **not** deploy the
   exit-status fix, the receipt publication or the audit archive — a
   fast-forward commit of the new workflow onto `ops` does, while the channel is
   idle, followed by a real round trip. That is an operator/Live Ops act, not a
   merge side effect. Everything else here (`ops_runner`, `ops_meta`,
   `ops_doctor`, `railway_env`) is live on merge, because runner code always
   comes from the default branch.
2. **`ops-audit` branch creation.** The first audit-worthy mutation creates it as
   an orphan branch, exactly as `digest-archive` was created. Until then the
   branch does not exist, which is correct and not a fault.
3. **Ruleset verification.** The spec asks for the `ops` branch protection to be
   confirmed and written down. The intended policy is already recorded in
   `docs/OPS_RUNBOOK.md` and checked by `tests/test_ops_branch_protection.py`
   against `.github/rulesets/ops-transport-guard.json`; retrieving the LIVE
   configuration still needs an admin-scoped token this session does not hold.

## Next step

Review and merge, then deploy the workflow file to `ops` in an idle window and
validate with a round trip: a `capabilities` request (green, receipt published),
then a deliberately bad request (red run, error readable in
`ops/results/<id>.txt`).
