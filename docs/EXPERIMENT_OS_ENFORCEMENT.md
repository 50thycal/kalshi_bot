# Experiment OS — enforcement, lineage and the recorded cutover (PR 4)

PR 4 makes Experiment OS *binding*: every new paper entry and live order resolves
its strategy tag against the registered deployment arms, gets its lineage stamped
on the row, and — once the recorded mode says so — is **refused** when no lineage
exists. It also closes the two operational holes that motivated the system: a
live canary can no longer arm on inherited paper state (the 2026-08-15
Lmmsell8/Lmmsell10 failure), and a hand-inserted PASS can no longer become a
real-money capability token.

Everything here ships with enforcement **OFF**. The blocker and the go-live path
are at the bottom — read that section before touching the mode.

## 1. Enforcement semantics (exact)

The runtime hook is `enforcement.stamp_or_block(session, tag, channel=...)`,
called by `repository.create_paper_trade` and `repository.create_live_order`. It
resolves the tag against the ACTIVE deployment-arm links (deployment not ended,
epoch not ended, experiment not RETIRED) from a per-cycle cached snapshot:

| mode     | tag resolves uniquely | unknown tag | ambiguous tag (≥2 active arms) |
|----------|----------------------|-------------|--------------------------------|
| OFF      | stamp lineage        | allow, unstamped | allow, unstamped          |
| WARN     | stamp                | allow + `system_events` warning (once per tag per boot) + counter | same |
| NEW_ONLY | stamp                | **`LineageBlocked`** — no row written | **blocked** |
| STRICT   | stamp, **unless** the deployment has an unresolved config-drift event or the experiment an unresolved material platform-impact disposition → blocked | **blocked** | **blocked** |

Notes that matter:

- **Stamping happens in every mode**, including OFF. Deploying this PR starts
  accumulating runtime lineage immediately; only *refusal* waits for the cutover.
- Blocking is **per-tag**. The cycle code already isolates each book's entry path
  (`main.py` wraps every tracker in its own try/except), so a blocked
  unregistered book can never interrupt a registered sibling — verified by test.
- `channel` is `"paper"` or `"live"`; live orders fail closed identically
  (a real-money write is exactly where lineage matters most).
- `tag_admissible(session, tag)` is a cheap pre-check for trackers that want to
  skip a blocked book without attempting an entry.

## 2. The recorded cutover (never inferred)

"When did enforcement begin?" has exactly one answer: the newest row of
**`experiment_os_enforcement`** — append-only (flush-guard enforced), carrying
`cutover_id` (unique), `mode`, `preceding_mode`, `effective_at`, `actor`,
`reason`, `system_version`, and the full `readiness_json` evidence. It is never
inferred from a deploy, a merge, the first Experiment OS row, or the importer's
run time. No row (or an unmigrated DB) ⇒ OFF.

`record_enforcement_change()` refuses NEW_ONLY/STRICT unless handed a
`production_readiness()` report with `ok=True`, or `force=True` — and a forced
override is recorded as such (readiness evidence with `ok=False` stays attached,
the system event says FORCED). An incomplete baseline cannot be enforced by
accident, and a bypassed checklist cannot be denied later.

## 3. Lineage propagation path

`paper_trades` and `live_orders` gained one nullable column:
`experiment_deployment_arm_id` (FK on Postgres; index everywhere). One id is
enough — the full chain

    Experiment → ExperimentVersion → ExperimentArm
              → ExperimentEpoch → ExperimentDeployment (+ PlatformSnapshot)

is derivable losslessly from the deployment-arm row, so a settled trade answers
"which experiment, which version of the question, which epoch of the world,
which platform" without tag archaeology. Strategy-tag reconstruction
(`read.strategy_tag_lineage`) remains as the *auditor* of the stamp, not the
mechanism.

The resolver (`enforcement.refresh(session)`) reloads mode + tag map once per
cycle at the top of `_run_cycle`; `stamp_or_block` then costs a dict lookup per
entry. Boot also refreshes and, when mode ≠ OFF, runs the config-drift check.

## 4. Legacy grandfather rules

Every deployment created by the legacy importer carries **`grandfathered=True`**
(a permanent marker, distinct from native registrations — the readiness and
observability surfaces count the two separately). Grandfathered books:

- **continue trading uninterrupted** under every mode — their tags resolve, so
  they stamp and pass;
- **cannot silently change**: arms live on frozen (immutable) versions; risk
  envelopes freeze with the version; stage changes only exist through
  `transition_experiment` (audited, gate-bound); and material config drift is
  detected against the registered deployment (§7 below) rather than absorbed;
- keep their *historical* asymmetries recorded, not repaired: e.g. theta4 armed
  live 2026-07-30 while its `_pt3` twin epoch starts at the 2026-08-11 fee
  re-baseline. The readiness check reports that as a grandfathered *note*, never
  invents an equal boundary, and never blocks on it — the strict same-instant
  rule binds native canaries, whose boundary we control.

## 5. Live-canary enforcement (the 2026-08-15 lesson, made structural)

`service.arm_live_canary()` is the **one sanctioned path** from PAPER to
LIVE_CANARY. Atomically it: validates approval + the authorizing PASS,
transitions the experiment, closes the paper epoch, opens a fresh **I2** live
epoch, and registers the live deployment *and its paper twin* at the same
instant with a first-class `twin_of` link. Structural refusals (each tested):

- **fresh identity** — any live/twin tag with `paper_trades` history before the
  arming instant is refused by name (mmsell10 armed on a tag carrying 87 paper
  positions is exactly what throttled the control ~29× harder than the
  treatment on 2026-08-15);
- **no tag reuse** — a tag carried by any active deployment arm is refused;
- **complete arm mapping** — live and twin tag sets must each equal the frozen
  version's declared arm set;
- **pre-registered risk envelope** — no `risk_json` on the version, no canary;
- **identical boundary** — live and twin share `started_at` and the new epoch.

Direct `register_deployment(kind="live")` outside this path is refused under
NEW_ONLY/STRICT and records a `LIVE_REGISTERED_OUTSIDE_CANARY_PATH` integrity
event under WARN. (The importer is exempt via a sanctioned flag — it records
history; it does not arm anything.)

## 6. Promotion hardening

With mode ≠ OFF, a money promotion (PAPER→LIVE_CANARY, LIVE_CANARY→PRODUCTION)
additionally requires, on top of the PR 3 strict binding (right gate, current
version, current epoch, active snapshot, latest result):

- the authorizing result's `computed_by` ∈ trusted evaluators (`system`) and its
  `metric_revision` ∈ the allowed set — **a manually inserted PASS is not a
  capability token**;
- a **fresh synchronous re-evaluation** of the promotion gate at authorization
  time, which must itself PASS and *becomes* the authorizing result. Freshness
  by construction, not by an arbitrary N-hour window: if the edge died after the
  recorded PASS, the re-run fails the promotion, by test.
- `migration_integrity` C/D (context-only / broken history) can never promote to
  money stages, in **any** mode.

`arm_live_canary` applies the same rule: under OFF a caller-supplied recorded
result is accepted (historical replays); under any enforcement it re-evaluates.

## 7. Config drift (v1 — detection; classification lands in the impact engine)

Live deployments registered from the manifest carry a `config_json["material"]`
dict of the facts that define the book (`live_strategies` membership, twin
pairs, `mmsell_variants` band params). At boot (mode ≠ OFF),
`runtime_config_check()` recomputes those facts from the running `Settings` and,
on mismatch, records an unresolved **`EXPERIMENT_CONFIG_DRIFT`** integrity event
(deduped while unresolved). Consequences, all pre-existing machinery:

- the PR 3 evaluator already refuses to evaluate gates over a deployment with
  unresolved integrity events → **BLOCKED_INTEGRITY**, so drifted evidence can't
  pass a gate;
- under STRICT the drifted deployment's tags are blocked outright;
- under WARN/NEW_ONLY the book keeps trading (continuity) but the drift is loud,
  and resolution requires the operator to *classify* it: harmless revision → new
  deployment record; changed question → new version; changed world → new epoch.
- when the drift turns out to be a platform-level semantic change, promote it
  into the change-impact workflow: `platform_impact.classify_drift()` resolves
  the event against a registered `PlatformRevision`, and the revision's impact
  records take over (see `docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`).

Silent drift — the registered experiment described one config while the runtime
ran another — is what this kills.

## 8. Failure behavior (outage ≠ shutdown, and never a permission window)

- `refresh()` failures keep the previous snapshot (stale-but-safe), mark the
  resolver **degraded**, log, and durably alarm (`system_events` error, once per
  outage) — never raise into the trading loop.
- **The invariant:** metadata degradation may preserve continuity for previously
  KNOWN lineage; it may never create permission for previously UNKNOWN lineage.
  Under NEW_ONLY/STRICT, known grandfathered and native tags continue stamped
  from the cached snapshot, while unknown/ambiguous tags STAY fail-closed — an
  outage is not a window in which new tags can accumulate exposure outside
  Experiment OS. Block messages note the stale snapshot (a tag registered
  during the outage is also blocked until refresh succeeds — fail-closed by
  design).
- Under **STRICT**, degradation additionally keeps the cached drift/impact
  blocks: integrity outranks continuity, by declaration.
- Residual honesty: if the very FIRST refresh of a process fails, there is no
  cached snapshot or mode — the recorded-mode contract ("no readable record ⇒
  OFF") applies, degraded and alarmed; in practice the write path shares the
  same database, so a total outage cannot accumulate rows either.
- Observability writes (`_log_once`, counters, the degraded alarm) can never
  break the entry path; the drift check and boot hooks are fully guarded.

## 9. Production readiness — the mechanical pre-cutover checklist

`production_readiness(session, settings=None)` returns `{ok, checks{...}}` and is
what `record_enforcement_change` demands for NEW_ONLY/STRICT:

1. `import_ran` — Experiment OS is populated (>0 experiments);
2. `coverage_complete` — the importer's migration report shows **no unmapped
   traded tags**;
3. `live_books_represented` — every configured `LIVE_STRATEGIES` tag maps to an
   active live deployment arm;
4. `platform_snapshot_complete` — an active snapshot covers every registered
   component;
5. `grandfathered_identity` — imported deployments are marked as such;
6. `live_twin_links` — every active live deployment has a twin in the same
   epoch (grandfathered boundary asymmetry = recorded note; native mismatch =
   failure);
7. `no_unresolved_integrity` — zero open integrity events;
8. `no_unresolved_platform_impact` — no impact dispositions awaiting
   acceptance/application (see the platform-impact doc);
9. `lineage_columns_present` — the migration actually ran on this DB, verified
   **independently on BOTH runtime tables** (`paper_trades` AND `live_orders`;
   the detail names any missing one — NEW_ONLY must not enforce blind on either
   write path);
10. `resolver_health` — no resolver-degraded alarms in the last 6 hours (do not
    cut over during or immediately after a metadata outage).

Surfaces: `python -m kalshi_bot.experiment_os.cli readiness` (exit 1 when not
ready), `... cli enforcement` (mode, cutover, coverage, canary links), ops
`{"type":"script","name":"experiment_os_status"}` §7 (mode + cutover +
grandfathered/native counts + post-cutover unstamped rows by tag + 7-day
rejections + 24h degraded-resolver alarms) and §8 (pending revisions +
unresolved impact dispositions + forced activations), and
`enforcement_report()` — the mechanical answer to *"is anything trading or
accumulating experimental evidence outside Experiment OS?"*

## 10. What blocked turning NEW_ONLY on (and the go-live path)

**The production import has never run.** Verified over the ops channel on
2026-08-15 (PR 4 precheck, `ops/results/xos-pr4-precheck.txt`): the Experiment
OS tables exist (PR 1 migration deployed) but hold **0 experiments** —
`EXPERIMENT_OS_IMPORT_ON_BOOT` has never been enabled on Railway. Readiness
check 1 fails, so `record_enforcement_change(mode=NEW_ONLY, ...)` refuses, as
designed. Enforcement therefore ships **OFF**; stamping is live, refusal is not.

Go-live, in order, once this PR is deployed:

1. `{"type":"env","set":{"EXPERIMENT_OS_IMPORT_ON_BOOT":"true"}}` via the ops
   channel (redeploys the worker; the import is idempotent and flag-gated);
2. confirm via `experiment_os_status`: experiments imported, migration report
   clean, §7 shows every live tag resolving;
3. run `readiness` — all eight checks green (theta4's twin-boundary note is
   expected and non-blocking);
4. record the cutover: `record_enforcement_change(session, mode="NEW_ONLY",
   actor=<who>, reason=<why now>, cutover_id=<slug>, readiness=<the report>)`;
5. watch §7 for a few days: post-cutover unstamped rows should be **zero**, and
   any `LineageBlocked` rejection names the tag that tried to trade outside the
   system. WARN-first (steps 1–3, then `mode="WARN"`) is a legitimate softer
   ramp if the first cutover attempt wants a dress rehearsal.

The impact engine (PR 5) has landed — see
`docs/EXPERIMENT_OS_PLATFORM_IMPACT.md`. STRICT still waits for a period of
clean NEW_ONLY operation after the production cutover.
