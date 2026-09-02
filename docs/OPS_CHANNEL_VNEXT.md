# Ops Channel vNext

## Purpose

The `ops` branch has evolved from a Claude self-service log/DB bridge into the machine-operable control plane for the running Kalshi system. This document captures the next improvement pass. It is intentionally a specification/review artifact: implementation should follow in a subsequent build after independent review of the findings below.

The direction is to keep Ops narrow, typed, auditable, and safer than giving an AI session broad shell/Railway/database access.

## Current model

A producer writes `ops/request.json` on the dedicated `ops` transport branch. The `Ops Runner` workflow triggers, checks out application code from the repository default branch, executes the allowlisted operation with GitHub-held credentials, and commits results back to `ops/result.txt` plus a durable request-specific `ops/results/<id>.txt` file.

Important existing properties to preserve:

- `ops` is transport, not an application-code source.
- Default-branch code is authoritative for request semantics and allowlists.
- The production serve path fails closed if it cannot attest that it is executing default-branch code.
- DB reads use the read-only database URL plus transaction-level and lexical read-only guards.
- Secrets are not exposed through the Ops env interface.
- Multiple producers can use the channel concurrently through request IDs and durable per-run results.
- Canonical Experiment OS reads run through the Experiment OS CLI rather than duplicated SQL/status logic.
- Mutations are bounded to explicitly allowlisted Railway environment operations and worker-side command transports.

## Mandatory fixes

### P1 — Preserve failing command status after publishing the result

Current workflow execution uses a pattern equivalent to:

```bash
python .ops-runner-code/scripts/ops_runner.py 2>&1 | tee ops/result.txt || true
```

The reason for swallowing the immediate failure is valid: the result/error must still be committed back to the requester. The side effect is not valid: a failed Ops command can leave the workflow green.

Required behavior:

1. Execute the command and capture the real runner exit status (`PIPESTATUS[0]` or equivalent).
2. Always snapshot and publish `ops/result.txt` / `ops/results/<id>.txt`.
3. Preserve publication retries exactly as today.
4. After publication, fail the workflow with the original runner status when the request failed.
5. Distinguish runner failure from result-publication failure in logs/metadata.

Acceptance criteria:

- Invalid/failed requests produce a durable readable result.
- The GitHub Actions run is red when the underlying request failed.
- Successful requests remain green.
- A publication failure still fails loudly even when the underlying request succeeded.

### P1 — Rewrite `ops/README.md` to match actual authority

The current README still describes only logs, read-only DB, and noop. That is materially stale.

The updated document must describe:

- request/result transport and per-request IDs;
- default-branch code checkout/freshness attestation;
- supported service targeting (`main`/`live`, `evo`, `livedash` where configured);
- request families: logs, DB, script, XOS, env read, env mutation, noop;
- Experiment OS canonical-read role;
- bounded mutation authority and explicit warning that env-set requests can alter live runtime behavior/redeploy;
- public-branch disclosure rule: never place credentials/private secrets in a request payload;
- concurrency/durable-results behavior;
- how to discover current capabilities once the capability command below exists.

## P1 feature — first-class `capabilities`

Add a request such as:

```json
{"type":"capabilities","id":"..."}
```

It should derive its answer from the actual runner data structures rather than duplicated documentation.

Minimum output:

- runner/application commit SHA used for execution;
- known Railway service aliases and whether each service ID is configured (never print secret IDs);
- supported request types with READ vs MUTATING classification;
- XOS read commands from the canonical allowlist;
- analysis-script allowlist;
- count/list of readable/settable env variables, with redacted variables marked;
- relevant hard limits (DB max default, timeout, result retention, etc.).

Tests must fail if docs advertise a capability that the generated capability surface does not expose, or if a new targetable service lacks the required workflow secret passthrough.

## P1 feature — `doctor` production health snapshot

Add a bounded, read-oriented diagnostic request:

```json
{"type":"doctor","id":"..."}
```

Goal: a fresh Claude/ChatGPT/Control Tower session should be able to establish operating context with one command instead of several exploratory calls.

Suggested sections:

- Ops runner freshness / execution SHA;
- DB read-only connectivity;
- Railway API connectivity;
- per-service deployment/log reachability for configured services;
- current non-secret critical runtime state (kill switch, live enabled, live strategies, bot mode);
- Experiment OS enforcement/readiness summary;
- active live canaries / paper twins where canonical APIs expose them;
- open/blocked operational findings or issue counts;
- clearly labeled warnings, not raw log dumps.

`doctor` should call canonical helpers/providers wherever available rather than reimplementing Experiment OS or trading logic.

It must remain bounded and should not mutate production.

## P2 — structured request/result provenance

Request IDs solved correlation but not intent provenance. Add optional metadata fields that are safe to commit publicly, for example:

```json
{
  "id":"...",
  "type":"xos",
  "actor":"claude-live-ops",
  "purpose":"verify mmsell10 canary parity",
  "workstream":"WS-..."
}
```

Do not require all metadata for backward compatibility.

Persist a small machine-readable receipt per run (JSON sidecar or a structured header) containing at least:

- request ID;
- request type;
- actor/purpose/workstream if supplied;
- start/end timestamps;
- runner/default-branch SHA;
- target service when applicable;
- READ vs MUTATING classification;
- underlying command exit status;
- publication status;
- result filename.

Never copy secret values into provenance.

## P2 — make mutation visually and structurally explicit

Today `{"type":"env"}` and `{"type":"env","set":{...}}` differ only by the presence of `set`, despite radically different authority.

Refactor or layer the API so a mutation is unmistakable. Backward compatibility may be retained, but new callers should use a distinct mutation vocabulary or explicit action field.

Requirements:

- capability output labels mutations prominently;
- result header labels a request `MUTATING` before showing output;
- env mutations show allowlisted before/after values where safe;
- values marked redacted stay redacted;
- unknown/unsettable variables remain fail-closed;
- mutation commands never gain arbitrary shell, arbitrary Railway GraphQL, arbitrary secrets, or writable DB access.

## P2 — automatic post-change verification receipts

For production-changing Ops actions, evolve the contract from `set + redeploy requested` to `change + verify` where practical.

At minimum, after a mutation/redeploy:

1. record pre-state for safe/readable variables;
2. apply the requested bounded mutation;
3. record Railway mutation/redeploy outcome;
4. re-read the effective safe environment after deployment or variable propagation;
5. verify the targeted service is reachable;
6. run relevant canonical integrity/readiness checks when the mutation touches Experiment OS/live strategy state;
7. produce a clear VERIFIED / APPLIED_BUT_UNVERIFIED / FAILED receipt.

Do not invent strategy-specific checks inside the generic runner. Prefer hooks to canonical Experiment OS/readiness providers or named verification scripts.

## P2 — incident bundle

Add a bounded diagnostic command for investigation startup, e.g.:

```json
{"type":"incident","service":"main","window_minutes":30,"id":"..."}
```

The bundle should gather a reproducible snapshot rather than forcing the requester to discover context call-by-call. Candidate contents:

- runner/deployment identity;
- service reachability;
- bounded recent logs;
- safe critical runtime configuration;
- current Experiment OS summary;
- recent operational/trading error summaries where canonical readers exist;
- recent exposure/order/trade summaries where read-only code already exists;
- issue candidates/open issue references.

Keep output size bounded and prefer summaries plus identifiers over enormous raw dumps.

## P2/P3 — promote repeated raw SQL into typed operations

Keep ad-hoc read-only SQL as an escape hatch. Do not remove it.

When the same production question is repeatedly answered by handcrafted SQL, promote that question into, in preference order:

1. a canonical Experiment OS metric/provider when it describes experiment state;
2. a named read-only Ops/script capability when it is operational/research state;
3. `doctor`/incident summary fields when it is standard operating context.

This reduces schema-coupling and semantic drift between AI sessions.

## P3 — durable mutation audit archive

`ops/results` is deliberately bounded scratch history. Add durable archival for meaningful production-changing receipts only, analogous in spirit to the existing digest archive.

Archive candidates:

- live arm/disarm;
- kill-switch/live-enabled changes;
- risk/exposure-limit changes;
- Experiment OS command transports/cutovers;
- canary activation/retirement;
- other mutations explicitly classified as audit-worthy.

Do not archive every exploratory SELECT/log fetch.

The archive should be append-oriented, separate from the deployable branch, and contain receipts rather than secret-bearing request payloads.

## Security / authority boundary

Preserve these non-goals:

- no arbitrary shell execution;
- no arbitrary script/module execution outside an allowlist;
- no direct Kalshi credential exposure;
- no secret enumeration/printing;
- no arbitrary Railway variable access;
- no writable DB credential in Ops;
- no generic Experiment OS write CLI against Postgres;
- no automatic expansion of mutation authority merely because a variable/script exists in code.

The repository/ops transport is public. Treat every request payload committed to the branch as public disclosure. Redacting a value from runner output does not make the request payload private.

## Branch/ruleset verification

During implementation, explicitly verify and document the actual `ops` branch ruleset/protection behavior. Prior inspection could confirm the branch is reported as protected but could not retrieve the full protection configuration through the available integration.

The intended policy should be written down and tested/checked where GitHub permits it. The channel needs to remain writable by its sanctioned producers and the `ops-runner` result publisher without accidentally opening broader code/deploy authority.

## Suggested implementation order

1. Fix failure exit-status propagation.
2. Update README/current authority documentation.
3. Add capability introspection generated from live allowlists.
4. Add `doctor`.
5. Add structured receipts/provenance.
6. Make mutation classification explicit and add verification receipts.
7. Add incident bundles.
8. Add durable mutation archive.
9. Continue promoting repeated SQL questions into canonical metrics/readers.

## Review requirement

Before implementation, independently re-check this document against the current runner/workflow and call out any recommendation that has already been implemented, conflicts with current Experiment OS authority boundaries, or creates unnecessary duplicate machinery.

The implementation should prefer extending existing canonical helpers over creating parallel status logic.
