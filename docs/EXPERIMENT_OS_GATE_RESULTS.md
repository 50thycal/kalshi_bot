# Experiment OS — persisted gate evaluation

Code: `kalshi_bot/experiment_os/gate_runner.py`. Tests:
`tests/test_experiment_os_gate_runner.py`. Evaluator semantics (unchanged by
this layer): `docs/EXPERIMENT_OS_METRICS.md`.

Until now Experiment OS could say what the evidence *would* imply — the Control
Tower dry-runs every started gate on every render — but it had never recorded an
official verdict. A dry run is not a result: it is not immutable, not bound to an
epoch, snapshot and metric revision, and `service.transition_experiment` will not
accept it. So no promotion gate could authorize anything, and every "is this book
ready?" answer was a chat-window opinion that evaporated.

This closes that gap:

```
evidence → canonical evaluator → immutable GateResult → Control Tower reads
         → operator may choose to transition
```

## 1. The invariant

**Automatic evaluation is allowed. Automatic promotion is not.**

The writer never calls `transition_experiment`, `arm_live_canary`,
`pause_experiment`, `retire_experiment`, or any other lifecycle write. Recording
`PAPER→LIVE_CANARY = PASS` is a *fact about the evidence*; performing
`PAPER→LIVE_CANARY` is an *operator act*, still bound by every PR 4 rule
(approval, live-canary structure, risk envelope, real-money confirmation). A
recorded `FAIL` likewise retires nothing — it just means the next operator to
look has an official, dated, immutable reason to kill the book.

This is enforced, not merely intended:
`test_the_writer_never_calls_a_lifecycle_write` replaces every lifecycle entry
point with a raising stub and runs a full write cycle.

It is also not a second evaluator. Every verdict comes from
`evaluator.evaluate_gate` — the same function the Control Tower dry-runs and the
same one `transition_experiment` re-runs at authorization time. This module
decides *whether to write*, never *what the answer is*.

## 2. Authority — one designated writer

Evaluation reads canonical database state, so unlike the live-config drift check
its *correctness* does not depend on which process runs it. Its *write volume*
does: two workers evaluating the same gates would race and double-write. Exactly
two writers are designated, and `_may_write` refuses everything else:

| Caller | May persist? |
|---|---|
| Live trading worker (`BOT_MODE=live`) with `EXPERIMENT_OS_EVALUATE_GATES=true` | yes |
| Live trading worker with the flag off (the default) | no |
| Evo worker (`BOT_MODE=evo`), scanner, any other mode | no |
| `python -m kalshi_bot.experiment_os.cli evaluate-gates` with a writable `DATABASE_URL` | yes — explicit operator run |
| The ops channel (`{"type":"xos","command":"evaluate-gates"}`) | no — dry run only |

The ops channel is read-only against Postgres by design, and the CLI refuses to
persist when it resolves `DATABASE_URL_RO` (exit 2). Reads and dry runs are
always allowed from anywhere — the Control Tower depends on that.

**One designated writer is a role, not a process.** During a Railway redeploy the
old container drains while the new one boots, so two live workers can briefly
both qualify, and each runs its own first cycle. The semantic dedupe absorbs this
— the second process reads the first's committed result and skips — which is why
dedupe correctness matters beyond tidiness. Duplicates are not dangerous even if
one slips through (identical verdict, identical binding, no promotion power), so
there is deliberately **no unique index** on the fingerprint: a gate legitimately
returns to a previously seen state (HOLD → PASS → HOLD under the same binding),
and a uniqueness constraint would refuse to record that reversal.

`EXPERIMENT_OS_EVALUATE_GATES` defaults to **false**, so deploying this code
changes nothing until the flag is set on the live worker (allowlisted in
`scripts/railway_env.py`, so it is settable through the ops channel).

## 3. Trigger and cadence

The trigger is the **live worker's ordinary trading cycle**, throttled by
`EXPERIMENT_OS_EVALUATE_INTERVAL_MINUTES` (default 60). Plus the operator's
manual `evaluate-gates` run, which is unthrottled and can be narrowed with
`--experiment` / `--gate`.

Why the worker cycle and not a scheduled job or a promotion-time hook:

* **Not promotion-time only.** The gap being closed is precisely that no result
  exists *before* someone considers a promotion. A trail that starts when you ask
  for permission cannot tell you when the book became ready, or that it was ready
  and then died.
* **Not a new scheduler.** The live worker already runs continuously, already
  holds a writable session, and is already the only writer of experiment state.
  Adding a second scheduled process would create a second thing that can drift,
  and a second thing to notice is down.
* **Bounded, so it can never become a write storm.** Hourly cadence plus the
  semantic dedupe in §4 means a stable HOLD writes roughly one row per real
  change, not one per cycle.

Failure is contained at three levels: each gate's evaluation is wrapped
individually (one broken experiment cannot stop the others), each write is its
own transaction (a failed persist cannot roll back a sibling's committed row),
and the whole hook is wrapped in `main._run_cycle` so **evaluation can never stop
trading**.

## 4. Dedupe — decision points, not a heartbeat

A periodic evaluator would otherwise write an identical `HOLD` every hour and
bury the real transitions. A result is recorded only when the evaluation is
*semantically* different from the last one for that gate.

The fingerprint (`evaluation_fingerprint`, a `canonical_hash`) covers:

* `gate_id` and the gate's `spec_hash` — a re-registered gate is a new question;
* `epoch_id` — the same verdict under a different world is a different claim;
* the pinned **platform snapshot** fingerprint;
* `METRICS_ENGINE_REVISION` — a changed engine can mean a changed number;
* the **verdict** and the sorted **blocking reasons**;
* the **clause shape** — which clauses passed, and which were missing/undecidable.

It deliberately excludes:

* **raw metric values** — a HOLD whose ¢/trade moved by 0.01 is not a new
  decision; and
* **the evidence window** — it advances every cycle by construction.

Two escape hatches keep the trail honest despite that:

1. **Sample progress.** A long HOLD still records when the largest clause sample
   grows by `max(25, 25%)` since the last recorded result — so a book
   accumulating evidence shows a visible, dated trail without spamming.
   The prior's sample is read from its **own clause list**, the same quantity the
   current outcome reports. Measuring the prior from `sample_json` (floor clauses
   only) against a current maximum over *all* clauses compares two different
   things: the first production run hit exactly that on mmsell-anchor-vol-entry,
   whose floor counted 29 trades while its widest clause counted 77 — a fixed
   48-wide gap that looked like fresh progress on every cycle.
2. **Unfingerprinted priors.** Results recorded before this module existed (or by
   hand) have no fingerprint. Those re-record only on a real verdict change, so
   adopting the writer does not rewrite the entire history on first run.

Dedupe cannot stale a promotion. Promotion freshness does not come from this
writer: `transition_experiment` re-evaluates synchronously at authorization
(PR 4) and *that* fresh result is the authorizing one. This module records
history; the promotion path proves currency.

## 5. What is eligible

A gate is evaluated when all of these hold:

* the experiment is in `PROBE`, `PAPER`, `LIVE_CANARY` or `PRODUCTION`
  (`IDEA` has no evidence; `RETIRED` is terminal history; **`PAUSED` is
  deliberately excluded** — a paused experiment is not accumulating, so
  re-evaluating only rewrites the verdict that paused it);
* it is on its current **frozen** version;
* that version has an **open epoch** — no operating interval, nothing to
  evaluate over;
* the gate's `evidence_started_at` is set (pre-registered ≠ started); and
* the gate is not superseded.

## 6. Verdict semantics are unchanged

`PASS` / `FAIL` / `HOLD` / `BLOCKED_DATA` / `BLOCKED_INTEGRITY` /
`BLOCKED_PLATFORM` mean exactly what `docs/EXPERIMENT_OS_METRICS.md` says. In
particular an underpowered sample is still `HOLD`, missing is still not zero, and
a `BLOCKED_*` verdict is **recorded as blocked** — it is never laundered into
something promotable by having been written down. Each blocked case is pinned by
a test that records the row and then proves the promotion path still refuses it.

## 7. Reading the results

`control-tower` shows both, in one column:

```
GATE COLUMN: recorded/dry-run (* = they differ). Only a RECORDED evaluator
result can authorize a transition.
```

* `PASS` — recorded and current agree.
* `PASS/HOLD*` — an official PASS exists but the evidence has since moved; an
  official re-evaluation is due, and this is **not** a promotion signal.
* `none/PASS` — the dry run says PASS but nothing is recorded, so nothing can be
  authorized yet.

READY/DUE gains two entry kinds from this: `NEVER RECORDED` (dry run has a
verdict, no official result exists) and `DIVERGENCE` (recorded and dry run
disagree).

## 8. Running it

Dry run from anywhere, including the ops channel:

```bash
echo '{"type":"xos","command":"evaluate-gates","args":["--dry-run"],"id":"ev-1"}' \
  > ops/request.json
```

Real write, on a host with a writable `DATABASE_URL`:

```bash
python -m kalshi_bot.experiment_os.cli evaluate-gates            # all eligible
python -m kalshi_bot.experiment_os.cli evaluate-gates --experiment mmsell-... --dry-run
```

Continuous, on the live worker (ops channel, redeploys the service):

```bash
{"type":"env","set":{"EXPERIMENT_OS_EVALUATE_GATES":"true"},"id":"ev-on"}
```

Both paths print the same accounting: `considered`, `evaluated`, `written`,
`skipped_unchanged`, `errors`, and the verdict histogram.

---

## 9. The blocker the evaluator cannot see

A gate verdict names why *the evaluator* could not decide. That is the truth, and
it is not always the whole diagnosis.

The imported live canaries are the worked case. Their clauses omit
`deployment_kind`, so it defaults to `"paper"`; their epochs hold only `live` and
`paper_twin` deployments. Every clause therefore addresses a scope with no arms
in it. The evaluator does exactly the right thing — it reports `BLOCKED_DATA` and
names the providers its clause metrics lack — but implementing every one of those
providers would leave both gates precisely as unevaluable as before. The
addressing is what is wrong, and a well-formed contract that resolves to nothing
is indistinguishable, from inside the evaluator, from one whose evidence has not
arrived yet.

So a second, independent blocker class is recorded by hand:
`kalshi_bot/experiment_os/findings.py`.

**It is not lifecycle state.** Four properties keep that true:

1. **A finding never changes a verdict.** Nothing in it feeds the evaluator, the
   gate runner, enforcement, admission, or any transition. It is display-only, so
   a wrong entry can mislead a reader but cannot authorize or block anything.
2. **Findings are hand-registered** against a specific `(experiment, version)`,
   citing a merged research document (a test asserts the file exists). Nothing is
   inferred from the shape of a gate — a heuristic guessing "this looks
   malformed" would be a second, unreviewed opinion competing with the canonical
   contract, and would eventually be wrong about a contract that is merely
   unusual.
3. **Findings expire structurally.** Each binds to the version it was proven
   against, so a corrected successor Version drops it automatically. Nothing has
   to remember to delete it.
4. **`owner` names a role, not a schedule.** It says who *would* do the work; it
   does not queue or approve it.

The Control Tower renders the two classes separately under `BLOCKED EVIDENCE`,
with an explicit `ALSO: CONTRACT DEFECT` marker on the evaluator entry so the
first block read is never mistaken for the diagnosis. Registering a finding is
**Research Lab's**; the Control Tower reads and routes them.

This registry is deliberately an interim mechanism, pending the investigation /
issue workflow. When that exists, findings become tickets with real state and
this module goes away.
