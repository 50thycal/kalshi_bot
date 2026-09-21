# Worker protection deployment and remaining acceptance

## Scope

WS-021 / DEC-021. Existing keys and primary account remain in place. This document
records deployment evidence and the procedure; it is not a Platform Revision,
impact acceptance, execution attestation, or authorization to start the desks.

## Verified on 2026-09-21

- Main, evo and desk-service were running commit
  `66d2007c5a0cab1a40bcfb3559d30e1c55de94cf`.
- Main and evo had nonempty ownership URLs; all three services used namespace
  `kalshi-primary`. Independent read-only database identity checks agreed.
- The main worker was in live mode; evo remained disabled. No key changed.
- The ownership registry had no claims at inspection. No desk round was started.
- A real PostgreSQL contention probe used three independent clients in a fresh,
  temporary schema. Exactly one of main/ChatGPT/Claude won; all clients saw that
  winner. The schema was removed. No exchange request was made by the probe.
- Offline tests cover V1/V2 placement refusal, desk-order cancellation refusal,
  filtered portfolio reads, incumbent refusal and desk ledger attribution.
- Session-only alerts were deployed. Authenticated preflight still refused live
  execution, worker attestation and Claude session/research readiness.

## Why another transport change is required

The existing Platform Change Review transport only proved a loaded taxonomy.
Passing an unchanged taxonomy hash cannot prove deployment of an execution guard.
The pending change adds an execution proof to the existing `CUTOVER` action,
without expanding its lifecycle, exposure or order capabilities.

An execution cutover requires `expect_execution_fingerprint` plus
`expect_ownership_namespace`. The fingerprint covers installed main/config,
Kalshi client/ownership and both worker executor source files. No key, URL or
secret is included. Missing ownership configuration, a namespace mismatch or
source mismatch defers before claiming the command. The revision must belong to
`EXECUTION_ENGINE` and carry that exact immutable fingerprint. Taxonomy proof
cannot activate an execution revision. Existing activation and impact gates apply.

This check proves one worker's code/configuration, not all writers or database
identity. The independent deployment checks above remain necessary. In particular,
it does not assert that an unrestricted external key consumer is protected.

## Canonical completion sequence

1. Merge/deploy the transport change with desk execution disabled.
2. Through `EXPERIMENT_OS_PLATFORM_COMMAND`, register a pending execution revision
   carrying `execution_fingerprint()` from the reviewed deployment. Do not overwrite
   an unconsumed command belonging to another session.
3. Use `affected_experiments()` / `revision_review()` to discover dependencies from
   pinned snapshots, then record and accept a justified disposition for each.
   Live eligibility can change once desks claim markets; a blanket I0 is not
   justified. Consider I2 for affected live sampling and its relevant comparisons;
   classify paper-only consumers from their actual dependency paths.
4. Establish how already-deployed PRs #449/#452 affected prior evidence. A future
   restart is not proof of their historical activation time. Do not backdate or
   silently pool an unaccounted interval. If the old boundary cannot be measured,
   retain that uncertainty through the canonical recovery procedure.
5. Execute the reviewed prospective cutover at worker boot, applying all required
   epoch actions. Read its receipt and canonical revision review. A merge alone
   is not an impact record. This PR executes none of these writes.
6. Recheck all active account writers, registry/namespace identity and outstanding
   pre-upgrade operations. Only then set the desk worker-protection attestation.
7. Claude must complete its own app session. Live enable and the single common
   start remain separate operator actions; no setup probe submits a trade.

## Validation

58 focused tests passed; two database-only tests skipped in the local suite.
The deployed PostgreSQL contention probe passed separately as described above.
The new tests cover unconsumed deferral, invalid proof, retained impact blocking,
measured epoch boundary, idempotent replay and unrelated revision proof refusal.
