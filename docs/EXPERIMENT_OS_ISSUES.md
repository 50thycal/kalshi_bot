# Experiment OS — the investigation / issue workflow

Spec context: `docs/EXPERIMENT_OPERATING_SYSTEM_SPEC.md`, `_FOUNDATION`,
`_ENFORCEMENT`, `_PLATFORM_IMPACT`, `_GATE_RESULTS`.

## Why this exists

Before this layer, an anomaly the Control Tower detected had exactly three
possible fates: somebody fixed it in the same session, somebody wrote it into a
Markdown file that went stale, or it evaporated when the chat window closed.
The one durable mechanism — the hand-written contract-findings registry in
`experiment_os/findings.py` — had no state, no history, no evidence records and
no way to be worked, and said so in its own docstring.

An **issue** is the durable workflow object for a problem: what was observed, who
is investigating, what the evidence says, what remedy was decided, how it was
validated, and how it ended. Anomalies, suspected defects, operational incidents,
scientific questions and shared-platform problems all live here instead of in
session prose.

## What an issue is not

Experiment OS remains the sole authority for experiment identity, versions,
epochs, arms, deployments, lifecycle state, gates, gate results, platform
snapshots and revisions, integrity events and enforcement.

**Opening, routing, accepting or resolving an issue authorizes nothing.** No
service in `issues.py` transitions an experiment, evaluates or alters a gate,
changes a recorded verdict, opens an epoch, creates a Version, registers or
activates a Platform Revision, arms a live canary, expands real-money exposure,
or pauses or retires an experiment. It imports no helper that can.

That restriction is what makes the workflow usable. A ticket you can open on a
suspicion is worth opening; a ticket that could move an experiment would need the
same guards as the transition itself, and nobody would open one.

`record_disposition` writes down that one of those canonical actions is
*required*. `add_issue_link` attaches the canonical record **after** it exists.
The gap between those two is deliberate and is how you tell "we decided this"
apart from "this happened".

## The objects

| table | what it holds |
|---|---|
| `experiment_issues` | one investigation; caches current status/owner/classification for fast reads |
| `experiment_issue_events` | append-only workflow history — the authority behind the cached columns |
| `experiment_issue_evidence` | append-only cited evidence: referenced and summarized, never copied wholesale |
| `experiment_issue_links` | append-only supporting/canonical references (PR, commit, Version, revision, successor issue) |

Events, evidence and links are guarded against UPDATE/DELETE by the same
`service.py` flush guard that protects state transitions and gate results. An
investigation whose record can be tidied up afterwards documents nothing.

**Identity** is `issue_key` — `XOS-000123`. Stable, unique, zero-padded so
lexical order equals numeric order, and short enough to type. Never the title.

**Scope** is foreign keys, not free text: `experiment_id`, `version_id`,
`epoch_id`, `deployment_id`, `gate_id`, `gate_result_id`,
`platform_revision_id`, `integrity_event_id`. All nullable — a collector outage
belongs to no experiment — but present values are validated against each other,
and ancestors are derived (naming a gate determines its version and experiment).
An inconsistent combination is refused, because an issue with the wrong scope
points every later reader at the wrong contract.

## Classification vs ownership

These are two different questions and the schema keeps them apart.

`classification` — what KIND of problem this is:

| value | meaning |
|---|---|
| `STRATEGY` | hypothesis, selection rule, experiment design, frozen contract, gate basis, version semantics, scientific interpretation |
| `DATA` | missing/corrupt/insufficient evidence, missing metric provider, missing experiment-specific transform, a provenance problem **not yet proven** to be a shared semantic change |
| `INTEGRITY` | contamination, broken lineage, mixed snapshots, invalid twin boundary, contract/deployment mismatch — evidence cannot be trusted |
| `PLATFORM` | a **confirmed** shared-semantic change requiring Platform Change Review |
| `OPS` | runtime, collector, deployment, order, execution, configuration, worker, admission, real-money operational failure |
| `UNCLASSIFIED` | insufficient evidence to distinguish the above |

`current_owner_role` — who is working it: `LIVE_OPS`, `RESEARCH_LAB`,
`PLATFORM_CHANGE_REVIEW`, `LEGACY_MIGRATION`, `TASK_SPECIFIC`, `OPERATOR`.

`EXPERIMENT_CONTROL_TOWER` and `SYSTEM` may **open** an issue and may never own
one — the Tower is read-only, so it detects and hands off. There is no generic
`FIXER` or `INVESTIGATION` role, and the service refuses those names by name. An
issue routes work to the existing role that owns the problem; that is the whole
design.

A `DATA` issue can legitimately sit with Live Ops while it is being diagnosed.
Classification says what is wrong; ownership says who is looking.

### `UNCLASSIFIED` is a real answer

The worked example is **zero evidence**. A registered book has traded nothing.
That is consistent with a broken runtime, an enforcement rejection, filters
eliminating every candidate, or genuinely no qualifying opportunities — and
nothing the Control Tower can see distinguishes them. Guessing produces a
`STRATEGY` ticket Research Lab cannot act on, or an `OPS` ticket Live Ops closes
as working-as-intended.

So the ticket opens `UNCLASSIFIED`, owned by Live Ops for **operational
diagnosis first**. The owner there is who looks first, not a claim that the cause
is operational.

## Status

```
OPEN → TRIAGE → INVESTIGATING → ACTION_REQUIRED → VALIDATING → RESOLVED
OPEN/TRIAGE/INVESTIGATING            → CLOSED_NO_ACTION
OPEN/TRIAGE/INVESTIGATING/ACTION_REQUIRED → DUPLICATE
RESOLVED/CLOSED_NO_ACTION            → INVESTIGATING          (explicit reopen)
VALIDATING → ACTION_REQUIRED | INVESTIGATING                   (validation FAILED)
ACTION_REQUIRED → INVESTIGATING                                (the fix was wrong)
```

The three reverse arrows are deliberate additions to the compact machine. Without
them a **failed** validation is unrecordable: the ticket can neither be resolved
nor moved, so the only ways forward would be to misreport the outcome or open a
second ticket for the same problem. Both are worse than a documented back edge,
and every move writes an event, so the earlier proposal, disposition and failed
validation all stay visible.

Issue statuses are disjoint from lifecycle states (`PAPER`, `PAUSED`, `RETIRED`)
and from gate verdicts (`PASS`, `HOLD`, `BLOCKED_DATA`) — a test enforces it.
Sharing a word is how the two get confused in a report that prints both.

`CLOSED_NO_ACTION` is not `RESOLVED`. "We looked and there is nothing to do" and
"we fixed it" are different findings; collapsing them makes the second one
untrustworthy. Only `RESOLVED` sets `resolved_at`.

## Severity and priority are separate axes

Severity is present impact (`INFO`/`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`); priority is
work ordering (`P3`/`P2`/`P1`/`P0`). A high-impact problem nobody can act on yet
is not the next thing to do.

| situation | default |
|---|---|
| unexpected live exposure, or an inability to drain | `CRITICAL/P0` |
| live twin missing, lineage resolver degraded | `HIGH/P1` |
| stale collector blocking current evidence | `MEDIUM/P1` (`P2` for `EMPTY`/`UNAVAILABLE`) |
| paper experiment with no observed opportunities, runtime healthy | `LOW/P2` |
| historical documentation cleanup | `LOW/P3` |

These are defaults from `recommend_owner`. Any caller may override them with a
rationale, and the override is recorded.

## Routing

`issue_policy.recommend_owner(...)` is pure, side-effect free and testable. It
**recommends**; it never conceals uncertainty.

| observed condition | initial owner |
|---|---|
| unexpected live exposure, stuck orders, rejections, worker/runtime failure | Live Ops |
| collector `STALE`/`EMPTY`/`UNAVAILABLE` | Live Ops |
| registered experiment with zero evidence, cause unknown | Live Ops (`UNCLASSIFIED`) |
| live/paper twin missing or boundary mismatch | Live Ops |
| `BLOCKED_DATA` — missing canonical provider or experiment-specific transform | Research Lab, or task-specific metrics work |
| malformed gate/contract, wrong evidence basis, selection-rule question | Research Lab |
| new hypothesis or successor experiment | Research Lab |
| `BLOCKED_PLATFORM` | Platform Change Review |
| confirmed shared semantic change | Platform Change Review |
| `BLOCKED_INTEGRITY` caused by runtime/execution/collector | Live Ops |
| `BLOCKED_INTEGRITY` caused by experiment contract/evidence design | Research Lab |
| `BLOCKED_INTEGRITY` caused by shared semantics | Platform Change Review |
| unmapped or incorrect legacy history | Legacy Migration |
| still ambiguous | Live Ops for operational diagnosis, or `UNCLASSIFIED` with operator triage |

Precedence is strongest-evidence-first: real money outranks everything (being
wrong there is the expensive direction), a confirmed shared-semantic change
outranks the gate verdict (the verdict is a symptom of it), and every
"we do not know yet" path lands on Live Ops with `UNCLASSIFIED` intact.

### Platform Change Review is narrow

Route there **only** for a confirmed or concretely proposed change to shared
experiment semantics: fee model, fill model, market taxonomy, execution
semantics, settlement interpretation, risk semantics, shared data provenance,
Kalshi API semantic interpretation, shared metric definition, Experiment Engine
semantics.

A missing metric provider, a missing experiment-specific transform, a broken
collector, a wiring bug and a malformed experiment contract are **none of these**.
Touching Experiment OS code is not a shared-semantic change. Routing those to
Platform Change Review invites a Platform Revision that no evidence calls for.

### Transfer, don't re-open

A ticket may move between roles. A transfer requires a recorded reason **and**
recorded evidence (or an explicit, recorded `evidence_waiver_reason`) — a
transfer without evidence is just moving a problem nobody has looked at.

A transfer is not a duplicate and not a new ticket. One investigation follows the
problem: the original detector, the original classification and every
intermediate finding stay visible. On the Live Ops → Research Lab path the
diagnosis that cleared the runtime **is** the evidence the next owner needs, and
a fresh ticket would throw it away.

Split only when the investigation uncovers genuinely independent problems with
different scopes and remedies — `open_child_issue` records
`discovered_from_issue_id`:

```
Parent: live canary underperforming
  Child A: OPS      — worker rejected entries due to stale config
  Child B: STRATEGY — corrected runtime still shows toxic selection
```

## Disposition — recorded, never performed

`NONE`, `OPS_REPAIR`, `DATA_REPAIR`, `RESEARCH_ONLY`, `NEW_VERSION`,
`NEW_EPOCH`, `PLATFORM_REVISION`, `PAUSE_OR_STAND_DOWN`, `RETIRE`, `NO_ACTION`,
`DUPLICATE`.

Four nullable booleans back it: `requires_new_version`, `requires_new_epoch`,
`requires_platform_revision`, `requires_pause_or_stand_down`. They start **NULL**,
which is not the same as `false` — "nobody has looked" and "we determined it is
not needed" are different findings, and only one is a conclusion.

Validated:

- `NEW_VERSION` requires `requires_new_version = true`; likewise `NEW_EPOCH` and
  `PLATFORM_REVISION`;
- a new Version **and** a new Epoch as the single remedy needs an explicit
  `version_and_epoch_rationale` — a Version is a changed scientific contract, an
  Epoch is the same contract under a changed world, and asserting both usually
  means the two have been conflated;
- `PLATFORM_REVISION` must be owned by Platform Change Review — transfer first;
- `DUPLICATE` requires the surviving issue (use `mark_duplicate`).

Rules that follow from Experiment OS itself:

- fixing a **malformed frozen contract** is `NEW_VERSION`, never an in-place
  repair — a frozen Version is immutable;
- an unchanged scientific contract under a changed operating environment is
  `NEW_EPOCH`;
- a shared semantic change follows the existing **Platform Impact engine**, which
  may then produce per-experiment epochs/versions of its own.

Resolution should link the canonical record that actually implemented the
remedy — Version, Epoch, Platform Revision, integrity-event resolution, PR, or
operational repair.

## Evidence

Evidence is **referenced and summarized**, not copied. `source_ref` points at
something another session can go and check: an ops result id, a document path, a
gate result, a PR, a bounded query. `content_hash` optionally pins what was seen.

Types: `CONTROL_TOWER`, `GATE_RESULT`, `OPS_RESULT`, `LOG`, `DATABASE_QUERY`,
`RESEARCH_DOCUMENT`, `PR`, `COMMIT`, `INTEGRITY_EVENT`, `PLATFORM_REVIEW`,
`MANUAL_OBSERVATION`, `OTHER`.

Note what is absent: **chat**. Unresolved session prose is not durable, not
addressable and not reviewable, so it cannot be the basis of a finding.
`MANUAL_OBSERVATION` is the honest place for a human's direct observation, and it
still needs a summary and a checkable reference.

Resolving requires a resolution summary **and** either a passed validation result
or an explicit `validation_waiver_reason`. The waiver exists because some
remedies genuinely cannot be validated directly; making it explicit means the gap
is visible in the history rather than implied by its absence.

## Recurrence and duplicates

A **deterministic fingerprint** identifies exact recurrence of the same
detector/problem scope: sha256 over exactly `detector`, `experiment_id`,
`version_id`, `epoch_id`, `deployment_id`, `gate_id`, `anomaly_kind`. The helper
is keyword-only and closed over that field set, so no volatile value (current
P&L, an age in minutes, a row count) can enter it — one that did would rehash
every run and defeat recurrence detection entirely.

- no matching **open** issue → the anomaly is a ticket candidate;
- a matching open issue → the anomaly is shown as covered by it;
- `occurrence_count` and `last_observed_at` advance **only** through
  `record_recurrence`, an explicit write. A Control Tower read never touches
  them: a counter that moves when a report is rendered measures how often
  somebody ran the report;
- a **resolved** issue never suppresses a currently recurring anomaly. "We fixed
  that once" is not evidence that it is fixed now. Reopening on recurrence is an
  explicit command.

Anything softer than an exact fingerprint match is a **suggestion**.
`suggest_duplicates` returns candidates with the reason each was suggested;
confirming one is `mark_duplicate`, by an operator or the owning role. Automatic
fuzzy merging would silently destroy the investigation history of whichever
ticket lost. Self-duplicates and duplicate cycles are refused.

## Control Tower integration

The Tower renders two sections, after `SYSTEM / INTEGRITY` and **before** any
performance interpretation — same reason integrity precedes performance
everywhere else: an open investigation is a statement about whether the numbers
below can be trusted, and reading it afterwards is reading it too late.

```
=== OPEN INVESTIGATIONS ===
issue | severity | pri | classification | owner | status | scope | age | latest evidence

=== UNTICKETED ANOMALIES ===
detector | severity | pri | would-classify | recommended owner | scope | anomaly
```

The Tower stays **read-only, structurally**: `control_tower.py` imports `read`
(plain selects) and `issue_policy` (pure logic), never the issue service. It
cannot open, update or dedupe a ticket even by mistake. A test spies on every
flush and asserts the whole report writes nothing.

Candidates cover only anomalies the Tower already understands: resolver-degraded
alarm, post-cutover rows without lineage, unresolved integrity event, unresolved
platform impact, unsafe pending revision, collector `STALE`/`EMPTY`/
`UNAVAILABLE`, live deployment without a twin, zero-evidence experiment,
evaluator `BLOCKED_*`.

Two deliberate silences:

- `INACTIVE` collectors produce **no** candidate — that status means "not part of
  this deployment";
- a recorded **stand-down** (`EXPERIMENT_EXECUTION_STOOD_DOWN`) produces no
  candidate, and suppresses the zero-evidence candidate for that book. Its cause
  is already recorded, so there is nothing to diagnose; manufacturing a ticket is
  how a deliberate pause starts reading as an unexplained failure.

There is **no heuristic contract-defect detection**. A defect in a registered
contract is proven by research and recorded as an issue; a heuristic that guessed
"this gate looks malformed" would be a second, unreviewed opinion competing with
the canonical contract.

Routing in the report follows the ticket's **current** owner when one exists
(it may legitimately have been transferred) and the recommended initial owner
when none does.

## CLI and the ops channel

Reads — allowlisted on the ops channel:

```bash
python -m kalshi_bot.experiment_os.cli issue list [--all --status --owner --classification --experiment]
python -m kalshi_bot.experiment_os.cli issue show XOS-000123
python -m kalshi_bot.experiment_os.cli issue candidates [--json]
python -m kalshi_bot.experiment_os.cli issue findings-plan
```

```json
{"type":"xos","command":"issue-list","id":"iss-1"}
{"type":"xos","command":"issue-show","args":["XOS-000123"],"id":"iss-2"}
{"type":"xos","command":"issue-candidates","id":"iss-3"}
{"type":"xos","command":"issue-command-show","args":["lo-adopt-20260821"],"id":"iss-4"}
{"type":"xos","command":"issue-command-list","id":"iss-5"}
```

The last two read **receipts** for the worker command transport below. They
report; they cannot execute or retry anything.

Writes — these refuse to run when `DATABASE_URL_RO` is set, exactly like
`evaluate-gates`. The ops channel is read-only against Postgres by design and the
worker remains the only writer; these run where a writable `DATABASE_URL` is the
only one present.

```bash
issue open-candidate <fingerprint> --actor ... --opened-by-role ...
issue create --title ... --opened-by-role ... [--owner --classification --experiment --detector]
issue triage XOS-000123   --actor A --actor-role LIVE_OPS --reason ...
issue classify XOS-000123 --actor A --actor-role ... --classification STRATEGY --reason ...
issue assign|transfer XOS-000123 --owner RESEARCH_LAB --reason ...
issue status XOS-000123 --status INVESTIGATING --reason ...
issue evidence-add XOS-000123 --type OPS_RESULT --summary ... --source ops/results/lo-9.txt
issue link-add XOS-000123 --type VERSION --reference "book v2"
issue propose-fix XOS-000123 --fix ...
issue disposition XOS-000123 --disposition NEW_VERSION --requires-new-version --reason ...
issue validate-plan XOS-000123 --plan ...
issue validate XOS-000123 --summary ... [--failed]
issue resolve XOS-000123 --summary ... [--validation-waiver ...]
issue close|duplicate|reopen|recurrence XOS-000123 ...
issue import-findings [--dry-run]
```

## Running an issue write in production

A local CLI that cannot reach production is not an operational workflow. The ops
channel connects with `DATABASE_URL_RO`, every mutation refuses that connection,
and the Claude sandbox cannot reach Railway or Postgres — so until this existed,
a ticket could be adopted, triaged and resolved locally and never in production.

The worker executes **one** issue command per boot, from the environment variable
`EXPERIMENT_OS_ISSUE_COMMAND` (`kalshi_bot/experiment_os/issue_commands.py`, boot
hook 2b-iii). The same door as the two Experiment OS boot flags, and the same
reasoning: the worker is the only writer.

### ⚠ The transport is PUBLIC

The envelope reaches the worker by being committed **in plaintext** to
`ops/request.json` on the **public `ops` branch**, where it stays in Git history
forever. `scripts/railway_env.py` redacts the value from ops results and the
worker never logs it, but that is **output hygiene, not confidentiality**. It
narrows accidental copies; it does not make the channel private.

A payload must therefore be **safe for public disclosure**. Never put in one:

* secrets or credentials of any kind;
* personal information;
* private logs or raw operational captures;
* account, order, fill or position identifiers;
* sensitive raw evidence.

Prefer a **bounded summary and a public source reference** — a document path, a
PR number, an `xos` command someone else can run — over pasted content. That is
the same rule the workflow already applies to evidence, which is cited rather
than copied.

**If a ticket genuinely requires private content, STOP and propose an encrypted
transport.** Redaction cannot make this channel private, and using it anyway
publishes the content.

### The envelope

Exactly six top-level keys; unknown fields are refused, not ignored.

```json
{
  "command_id": "lo-adopt-20260821",
  "action": "OPEN_CANDIDATE",
  "actor": "cal",
  "actor_role": "LIVE_OPS",
  "schema_version": 1,
  "payload": {"fingerprint": "…"}
}
```

* `command_id` — 8–64 chars of `[A-Za-z0-9._-]`, globally unique and stable. It
  is the entire basis of exactly-once, so reusing one is a collision, not a retry.
* `action` — one of the eighteen below. Nothing else is reachable.
* `actor` / `actor_role` — **attribution, not authorization.** This transport
  cannot verify identity. The real authority is who can push to `ops` and who
  holds the Railway token, and this changes neither. The role is validated so the
  recorded history is well formed and then handed to the issue service, which
  applies its own routing rules — the Control Tower can still hand a candidate
  over and still cannot own the resulting ticket.
* `payload` — flat: strings and booleans only, no nesting, ≤ 4000 chars per
  string, ≤ 8 KB per envelope.

### The vocabulary

Two ways in, and choosing between them is a **rule, not a preference**:

> **`OPEN_CANDIDATE` is MANDATORY whenever the Control Tower has a matching
> candidate.** Only adoption carries the deterministic fingerprint and exact
> lineage the detector computed, so only adoption makes the ticket *cover* the
> anomaly. A hand-opened ticket for a detected problem leaves the Tower reporting
> it as UNTICKETED forever — the defect that made this workflow unusable end to
> end before PR #254. Run `issue-candidates` first; if the problem is listed
> there, adopt it.
>
> **`OPEN_MANUAL` is only for problems outside the candidate detector surface** —
> a Live Ops runtime observation, a Research Lab reading of the evidence, an
> integrity review, a person noticing something no detector watches for.

| action | required payload | optional |
|---|---|---|
| `OPEN_CANDIDATE` | `fingerprint` | — |
| `OPEN_MANUAL` | `title`, `problem_statement`, `classification`, `owner_role`, `reason` | `severity`, `priority`, `experiment`, `version`, `deployment`, `gate`, `platform_revision` |
| `TRIAGE` | `issue`, `reason` | `severity`, `priority`, `classification`, `owner_role` |
| `CLASSIFY` | `issue`, `classification`, `reason` | — |
| `ASSIGN` | `issue`, `owner_role`, `reason` | — |
| `TRANSFER` | `issue`, `to_owner_role`, `reason` | `classification`, `evidence_waiver_reason` |
| `STATUS` | `issue`, `status` | `reason`, `event_type` |
| `ADD_EVIDENCE` | `issue`, `evidence_type`, `summary` | `source_ref`, `content_hash` |
| `ADD_LINK` | `issue`, `link_type`, `reference` | `label` |
| `PROPOSE_FIX` | `issue`, `proposed_fix` | `reason`, `advance` |
| `RECORD_DISPOSITION` | `issue`, `disposition`, `reason` | `requires_new_version`, `requires_new_epoch`, `requires_platform_revision`, `requires_pause_or_stand_down`, `version_and_epoch_rationale` |
| `RECORD_VALIDATION_PLAN` | `issue`, `validation_plan` | `reason`, `advance` |
| `RECORD_VALIDATION_RESULT` | `issue`, `passed`, `summary` | `source_ref`, `evidence_type` |
| `RESOLVE` | `issue`, `resolution_summary` | `validation_waiver_reason` |
| `CLOSE_NO_ACTION` | `issue`, `reason` | — |
| `MARK_DUPLICATE` | `issue`, `duplicate_of`, `reason` | — |
| `REOPEN` | `issue`, `reason` | `owner_role` |
| `RECORD_RECURRENCE` | `issue` | `note` |

Each calls the matching function in `experiment_os/issues.py` and reimplements
none of its rules: an illegal transition, a transfer with no evidence, a stale
candidate fingerprint and a disposition without its rationale are all refused
exactly as they are locally.

`OPEN_MANUAL` is **not** a generic `create_issue(**payload)`. It names five
required fields and a fixed optional set, resolves every lineage reference
explicitly by human-legible key — an experiment key, a version *number* under
that experiment, a deployment key, a gate key under that version,
`COMPONENT:version` for a revision — and passes no `**payload` through. A
reference that cannot be resolved, or a version that does not belong to the named
experiment, is a refusal rather than a half-scoped ticket. Raw primary keys are
not accepted: a bare integer in a public envelope is unreadable in review and one
typo away from scoping a ticket to the wrong contract.

It records the fixed source `manual.reported` and **no fingerprint**. Fabricating
one would make a manual ticket look like it covers a candidate that was never
detected, which is the exact confusion adoption exists to prevent.

There is deliberately no generic `CREATE`, and no `OPEN_CHILD` — whose
parent-scoped keyword arguments are wide enough to be an escape hatch. It stays
on the local CLI, where a human sees the result. Free-form JSON blobs are
withheld for the same reason the section above gives.

The executor cannot run SQL, Python or shell; cannot name a module or function;
and cannot transition an experiment, evaluate or mutate a gate, create a Version
or Epoch, register a Platform Revision, or arm, configure or expose live trading.
Not by declining to — no code path exists, and a test asserts the module never
imports one.

### Exactly once

`railway.json` restarts the worker on failure up to ten times and every restart
re-reads the same variable, so "one-shot" cannot be a property of the transport.
It is a property of the ledger, `experiment_os_issue_commands`:

```
validate + canonicalize + hash the envelope        (no database work yet)
claim  INSERT … ON CONFLICT DO NOTHING RETURNING id
  no row  → read the receipt, compare hashes, return it; execute NOTHING
  row     → SAVEPOINT
              validate the payload, call the one issues.py function
            success  → receipt SUCCEEDED; COMMIT (mutation + receipt together)
            refusal  → ROLLBACK TO SAVEPOINT; receipt REJECTED; COMMIT
            failure  → ROLLBACK TO SAVEPOINT; receipt FAILED;   COMMIT
```

The claim row is inserted **outside** the savepoint, so rolling a failed mutation
back never rolls back the explanation, and no second transaction is needed to
record it. If the database is broken badly enough that even the receipt cannot
commit, nothing commits — no receipt, no mutation — and the next boot may
legitimately retry.

**A committed receipt is terminal.** `SUCCEEDED`, `REJECTED` and `FAILED` are all
final for that `command_id`. **Retrying means a NEW `command_id`.** The absence of
a receipt is the only state that permits another attempt, and it is exactly the
state in which the mutation cannot have committed either.

Same id, different envelope — including a different actor — is a **collision**:
it executes nothing and changes nothing, because the stored receipt belongs to
whatever really ran under that name.

### Doing it

```bash
# 1. Read what needs doing, and get the exact argument.
{"type":"xos","command":"control-tower","id":"ct-1"}          # prints `adopt: …`

# 2. Submit ONE command. The value is redacted in the result.
{"type":"env","set":{"EXPERIMENT_OS_ISSUE_COMMAND":
  "{\"command_id\":\"lo-adopt-20260821\",\"action\":\"OPEN_CANDIDATE\",\"actor\":\"cal\",\"actor_role\":\"LIVE_OPS\",\"schema_version\":1,\"payload\":{\"fingerprint\":\"…\"}}"},
  "id":"cmd-1"}

# 3. Setting it redeploys the worker; ~60-90s later, read the receipt.
{"type":"xos","command":"issue-command-show","args":["lo-adopt-20260821"],"id":"cmd-2"}

# 4. Confirm the effect through the canonical read, not the receipt alone.
{"type":"xos","command":"issue-show","args":["XOS-000123"],"id":"cmd-3"}

# 5. Clear the transport and reset ops.
{"type":"env","set":{"EXPERIMENT_OS_ISSUE_COMMAND":""},"id":"cmd-4"}
```

Step 5 is hygiene, not safety: leaving the variable set is inert, because the
receipt is terminal. Clear it anyway so the next reader is not left wondering
whether something is pending.

Receipts are **metadata only** on the read surface: identity, action, actor,
timing, outcome, its hash, and the payload's *recognised* field names plus a
count of the rest. The submitted payload is stored — a receipt that cannot prove
what it ran is not an audit record — but it is never printed, because this
channel's own results are public too. An unrecognised field name is counted and
never echoed: it came from the envelope, so it is as author-controlled as a value.

`issue-command-list --limit` is clamped to 1–100.

### What a failure is allowed to say

Success writes what the executor chose to write. **Failure writes whatever an
exception happened to say** — and exceptions quote things: the offending value,
the failing SQL statement, its bound parameters (`payload_json` among them), the
whole document. So every failure path is bounded:

* the error recorded on a receipt is sanitized — the canonical envelope, each
  submitted value **and each word inside it**, and every unrecognised key name
  are removed. Word-level matters: an exception usually quotes a *fragment* of a
  value, and stripping only whole values leaves every fragment intact;
* no failure path logs with `exc_info`. A traceback carries the original,
  unsanitized message past every redaction, so the FAILED path logs bounded
  fields and the receipt holds the account;
* a failure that produces **no receipt at all** — a malformed envelope, or a
  database error during the claim or commit — is reported by the boot hook as an
  exception class, a stable code, and the **hash and length** of the value that
  failed. Never `str(exc)`. That is enough to match a log line to a submitted
  envelope and nothing more;
* a driver exception around the claim, the receipt read or the final commit is
  re-raised as a sanitized `IssueCommandTransportError` with the original
  dropped rather than chained, because a chained `__cause__` puts the raw text
  back into any traceback that formats it.

The trade-off is accepted deliberately: a common word an author happened to use
is also blanked out of the framework's own message, so a refusal can read a
little redacted. The message is a debugging aid; the channel is public.

## Worked example — zero evidence, end to end

```
Control Tower: experiment has zero evidence
    → detected as a candidate; the Tower recommends but cannot open it
    → `issue open-candidate <fingerprint>` ADOPTS it, carrying the detector,
      fingerprint, anomaly kind, exact lineage, verdict and routing
XOS ticket opened — UNCLASSIFIED, owner LIVE_OPS, and the candidate is COVERED
    → Live Ops verifies runtime health and candidate production
       if broken   → classify OPS, repair, validate, resolve
       if healthy but zero qualifying opportunities
                   → classify STRATEGY, transfer to RESEARCH_LAB
                     (reason + the ops evidence that cleared the runtime)
    → Research Lab decides whether the criteria remain valid or the contract
      must change; a frozen contract means disposition NEW_VERSION
    → validation evidence recorded
    → resolved, linked to the PR / Version / decision
```

**Adopt the candidate — do not hand-open a ticket.** `issue create` cannot carry
a candidate's fingerprint or its exact Version/Epoch/Deployment/Gate scope, so an
issue opened that way never *covers* the candidate that caused it: the Tower goes
on reporting the same anomaly as UNTICKETED forever, and you end up with two
records of one problem, neither aware of the other. `open-candidate` copies the
detection verbatim.

```bash
# Read the current candidates and take the fingerprint of the one you mean.
xos issue candidates --json

xos issue open-candidate 9f2c…e41 \
    --actor cal --opened-by-role EXPERIMENT_CONTROL_TOWER
# → XOS-000042  [OPEN] UNCLASSIFIED/LOW/P2  owner LIVE_OPS
#     detector:    experiment.zero_evidence
#     fingerprint: 9f2c…e41
#     scope:       freeze-dark-window-pin · v1/e1
#
#   This anomaly is now COVERED — the next control-tower read shows it under
#   OPEN INVESTIGATIONS, not UNTICKETED ANOMALIES.

xos issue triage XOS-000042 --actor cal --actor-role LIVE_OPS \
    --reason "runtime diagnosis before any criteria question"
xos issue status XOS-000042 --actor cal --actor-role LIVE_OPS \
    --status INVESTIGATING --reason "checking worker + candidate production"
xos issue evidence-add XOS-000042 --actor cal --actor-role LIVE_OPS \
    --type OPS_RESULT --source ops/results/lo-9.txt \
    --summary "worker healthy; 412 candidates produced, 0 pass the strike filter"
xos issue transfer XOS-000042 --actor cal --actor-role LIVE_OPS \
    --owner RESEARCH_LAB --classification STRATEGY \
    --reason "runtime verified healthy; the question is now the selection rule"
xos issue propose-fix XOS-000042 --actor cal --actor-role RESEARCH_LAB \
    --fix "widen the strike band in a corrected successor Version"
xos issue disposition XOS-000042 --actor cal --actor-role RESEARCH_LAB \
    --disposition NEW_VERSION --requires-new-version \
    --reason "the contract is frozen; a changed selection rule is a new question"
# ...author and freeze v2 through the CANONICAL service; this ticket did not.
xos issue link-add XOS-000042 --actor cal --actor-role RESEARCH_LAB \
    --type VERSION --reference "freeze-dark-window-pin v2"
xos issue validate-plan XOS-000042 --actor cal --actor-role RESEARCH_LAB \
    --plan "v2 produces >=20 qualifying candidates in week 1"
xos issue validate XOS-000042 --actor cal --actor-role RESEARCH_LAB \
    --summary "34 qualifying candidates in week 1" --source ops/results/rl-3.txt
xos issue resolve XOS-000042 --actor cal --actor-role RESEARCH_LAB \
    --summary "corrected successor Version 2 registered and producing evidence"
```

## The two historical contract findings

`experiment_os/findings.py` is now a deprecation shim returning nothing. Its two
entries — the `mmsell-scheduled-settle-live` and `theta4-fat-tail` v1 contract
defects — become durable issues through an idempotent import that also **settles
them against the operator's later decision**.

### Why they are closed rather than actionable

The remedy recorded when the defects were proven was "a corrected native
successor Version". That is **stale**. Per merged research:

* both live canaries were stood down and remain stood down —
  `docs/RESEARCH_LIVE_FILL_SELECTION_STUDY.md` ("All three live books remain
  stood down");
* the proposed successor live-v2 contracts were **withdrawn** —
  `docs/RESEARCH_SUCCESSOR_GATE_DESIGN.md`, WITHDRAWN 2026-08-21 (merged in
  `50thycal/kalshi_bot#251`). MMSELL Design D is not to be frozen because
  treatment and control differ in universe, entry-price band and settle mode at
  once, which no sample size repairs
  (`docs/RESEARCH_MMSELL_UNIVERSE_DECONFOUNDING.md`); theta4 v2 is not to be
  created because the book carries two independent failures and needs research
  before another canary (`docs/RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md`).

The defect is **not** withdrawn — it remains true of the historical Version, and
the issue keeps its original evidence and Version binding. What changes is what
follows from it, which is nothing. So each closes as:

```text
status                       = CLOSED_NO_ACTION
disposition                  = NO_ACTION
requires_new_version         = false
requires_new_epoch           = false
requires_platform_revision   = false
requires_pause_or_stand_down = true     # the contract is stood down
```

with the closure explanation:

> The defect remains true of historical Version 1, but that live contract was
> stood down and its proposed successor live Version was withdrawn. It will not
> be repaired in place or replaced by the rejected live-v2 design. Subsequent
> MMSELL/theta work proceeds as separate paper research.

Importing them un-reconciled would put two tickets in front of the next reader
asking for work that has already been declined; deleting them would erase a
proven finding. Closing them with the decision recorded does neither.

The event history keeps the whole arc — `CREATED` → the migration's own
`NEW_VERSION` disposition → the withdrawal evidence → back to `INVESTIGATING`
(the documented back edge, whose purpose is exactly a proposed remedy that turned
out to be wrong) → `NO_ACTION` → `CLOSED_NO_ACTION`. A reader can see that a
successor Version *was* the plan and that it was withdrawn, not just the tidy end
state.

Neither closed issue is an active Control Tower blocker any more
(`contract_defect_findings` returns only OPEN issues), and both stay fully
queryable through `issue show`.

### Running it in production

The ops channel is read-only against Postgres and the Claude sandbox cannot
reach Railway, so "invoke the importer" needs a real door. It uses the
**established worker-write pattern** — the same one the legacy import and the
enforcement cutover already use: the worker is the only process holding a
writable `DATABASE_URL`, so a bounded, flag-gated boot hook executes it.

**1. Preview, read-only** (safe on the ops channel; a write-and-rollback dry run
cannot run there at all):

```json
{"type":"xos","command":"issue-findings-plan","id":"fp-1"}
```

Each finding reports one of `IMPORT_THEN_CLOSE_NO_ACTION`, `RECONCILE_ONLY`,
`NO_OP`, or `SKIP` with a reason.

**2. Execute once, on the worker:**

```json
{"type":"env","set":{"EXPERIMENT_OS_RECONCILE_FINDINGS_ON_BOOT":"true"},"id":"fr-1"}
```

Setting the var redeploys the worker; the boot hook runs the import and the
reconciliation, logs the result, and cannot stop trading (`main.py` guards it and
swallows its own errors). It is **bounded to this one operation** — not arbitrary
SQL, and the general database channel stays read-only.

**3. Verify and switch it back off:**

```json
{"type":"xos","command":"issue-list","args":["--all"],"id":"fv-1"}
{"type":"xos","command":"issue-show","args":["XOS-000001"],"id":"fv-2"}
{"type":"env","set":{"EXPERIMENT_OS_RECONCILE_FINDINGS_ON_BOOT":"false"},"id":"fr-2"}
```

Leaving the flag on is safe — every re-run is a no-op — just noisier. The
reconciliation is keyed on `RECONCILIATION_KEY`, so a repeat can never reopen a
closed issue, reset its status, restore `NEW_VERSION`, or duplicate an event.

The equivalent on a writable connection is
`python -m kalshi_bot.experiment_os.cli issue import-findings [--dry-run]`, which
refuses `DATABASE_URL_RO` like every other issue write.

## Registering a new contract defect

Research Lab owns this write. Open an issue with
`detector = "contract.defect"`, classification `STRATEGY`, scoped to the exact
experiment and version, with the merged research document attached as
`RESEARCH_DOCUMENT` evidence and `details_json` carrying `detail`,
`independent_of_evaluator`, `evidence_doc`, `proven_at`, `proven_by`. The Control
Tower renders it in `BLOCKED EVIDENCE` as a second, independent blocker class
beside the evaluator's own verdict.

A finding is not a fix. It never changes a verdict and never edits a frozen gate.
Its authority is the research document, and a citation that does not resolve is
an assertion.
