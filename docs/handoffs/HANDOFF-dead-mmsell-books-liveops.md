# HANDOFF → Live Ops: the five dead `MMSELL_VARIANTS` tags, classified

**From:** Experiment Control Tower (READ ONLY), 2026-09-11
**Status of this document:** classification complete. **Nothing here is persisted in
Experiment OS yet** — the two tickets below are drafted, not filed. Filing is a write
and the Control Tower cannot do it.

## The finding, restated

Five tags are in production's `MMSELL_VARIANTS` but carry no ACTIVE Experiment OS
deployment arm, so under `NEW_ONLY` every entry they attempt is refused at the write
path. They are constructed every scan cycle and trade nothing.

Confirmed in production (`env`, 2026-09-11 15:22Z): all five are present in the
deployed `MMSELL_VARIANTS`. Confirmed against XOS: an inventory of every arm on an
open deployment in an open epoch yields 16 tags; the deployed variants string yields
21. The difference is **exactly these five, no more and no fewer** — so this is not a
broad config drift, it is two specific events.

## Real money: escalated first, and it is closed out

The brief said to escalate the live-prefixed tags ahead of the paper half if either
holds or has held real exposure. Both have.

| tag | filled live buys | contracts | window |
|---|---|---|---|
| `Lmmsell10` | 279 | 558 | 2026-08-15 12:56:55Z → 2026-08-19 18:48:44Z |
| `Lmmsell8` | 22 | 44 | 2026-08-15 23:43:02Z → 2026-08-19 17:14:17Z |

**Current exposure is nil, verified rather than assumed.** No `resting` live orders exist
for either tag — every row is `filled`, `canceled` or `rejected`, and the newest order on
either is 2026-08-19. Joining every ticker they filled against the newest `positions`
snapshot returns **0 rows with a non-zero quantity**: nothing is still held, and nothing is
still resting. `LIVE_STRATEGIES` is `Fmmsell10`, which does not prefix-match either tag, so
neither can be re-armed by the current allowlist.

The record is also already consistent for these two: `REPAIR_LINEAGE` receipt
`ts-repair-darkcanary-20260906` ended the dark `lmmsell-live-1` deployment at
2026-09-06 20:36:21Z, and the `STAND_DOWN` seven minutes later retired the experiment.
Nothing about the live half is an open real-money question — it is a config leftover.

## The classification: 4 deliberate, 1 accidental

They are not one problem. They split cleanly, and the two halves want opposite
responses.

### Deliberately retired — env entry nobody deleted (4 tags)

| tag | experiment | state | retired_at | deployment ended | authorizing act |
|---|---|---|---|---|---|
| `mmsellA4` | `mmsell-anchor-vol-entry` | RETIRED | 2026-09-06 13:22:41Z | `mmsellA4-paper-legacy-1` | `RETIRE_ON_GATE_FAIL`, receipt `rl-retire-volentry-20260906`, verdict FAIL (result 240) |
| `mmsellA5` | `mmsell-anchor-strangle` | RETIRED | 2026-09-06 14:11:46Z | `mmsellA5-paper-legacy-1` | `RETIRE_ON_GATE_FAIL`, receipt `rl-retire-strangle-20260906`, verdict FAIL (result 185) |
| `Lmmsell8` | `mmsell-scheduled-settle-live` | RETIRED | 2026-09-06 20:49:31Z | `lmmsell-live-1` (+ `lmmsell-twin-pt3`) | `STAND_DOWN`, receipt `ts-standdown-settlelive-20260906`, epoch 1 closed |
| `Lmmsell10` | `mmsell-scheduled-settle-live` | RETIRED | 2026-09-06 20:49:31Z | `lmmsell-live-1` (+ `lmmsell-twin-pt3`) | same |

Every one of these is a **recorded, authorized act by a named role**, with a committed
receipt and a closed epoch. Evidence corroborates intent: each tag's last `paper_trades`
row sits minutes before its own retirement (`mmsellA4` and `mmsellA5` both at
2026-09-06 13:19:50Z; `Lmmsell10` at 20:32:09Z, four minutes before its deployment
ended at 20:36:21Z). They stopped because someone stopped them.

**These four are behaving correctly. The books are dead on purpose.** The only residue
is the config entry, and its cost is a wasted construction plus one refusal per book
per cycle — plus the real hazard that `MMSELL_VARIANTS` reads as the list of live
books when it is not.

### Went dark without a recorded act (1 tag)

`mmsell9` does not fit the pattern and must not be treated as if it did.

| field | value |
|---|---|
| experiment | `mmsell-price-ceiling` |
| experiment state | **LIVE_CANARY** — alive, **`retired_at` is NULL** |
| epoch | v1 / e1, **`ended_at` NULL — still open** |
| deployment 1 | `mmsell-ceiling-paper-legacy-1` (grandfathered), 2026-07-18 → ended 2026-08-28 04:11:45.750998Z |
| deployment 2 | `mmsell-ceiling-paper-mmsell9-1` (native), started 2026-08-28 04:11:45.750998Z → **ended 2026-09-02 00:57:38.273309Z** |
| last `paper_trades` row | 2026-09-02 00:56:31.979599Z — **67 seconds before that closure** |
| lifecycle transitions on this experiment | **two, ever**: `import→PAPER` 2026-08-16, `PAPER→LIVE_CANARY` 2026-08-28. **Nothing on 2026-09-02.** |

Deployment 2 exists precisely to keep `mmsell9` admissible: v1 declares `mmsell9` and
`mmsell10` as its two arms, so arming the Stage-1 canary on v1 would have put `mmsell9`
on real money. The code closes the two-arm legacy deployment and opens a `mmsell9`-only
carrier instead (`canary_mmsell10.py`, `V1_MMSELL9_DEPLOYMENT_KEY`, `arms={"mmsell9": "mmsell9"}`).
The handoff worked — the timestamps are identical to the microsecond.

Five days later that carrier was closed, and **no lifecycle transition records it.**
The experiment is still LIVE_CANARY, its epoch is still open, and no retirement, stand-down
or epoch close exists for `mmsell9` anywhere. An arm of a live experiment's frozen v1
contract has been silently inadmissible for **9.6 days**.

Two honest limits on this read:
- The transitions query was complete for this experiment (ordered, 2 rows total), so
  "no transition" is established. The receipt ledger read reached back only to
  2026-09-02 16:09:33Z, and the closure is at 00:57:38Z — **below that window**. So
  "no receipt exists" is NOT established; pull the receipts around that instant first.
- The Control Tower's detectors cannot see this class. `experiment.silent_arm` requires
  an ACTIVE arm collecting nothing; a configured tag with no active arm has no arm to
  measure. That is the XOS-000011 shape, and it is why 9.6 days passed unnoticed — a
  fact, not a request for new work.

## Routing

Both tickets go to **LIVE_OPS**: the Tower detects the absence but cannot tell whether
runtime, config, admission or wiring explains it, and the playbook routes a registered
book that is not trading to Live Ops first. Research Lab owns the criteria question only
after Live Ops establishes the runtime is healthy.

`mmsell9`'s classification is **UNCLASSIFIED on purpose.** The mechanism is established
(the carrier deployment is closed, so admission refuses). The *cause of that closure* is
not, and the two live hypotheses — INTEGRITY (a contract/deployment mismatch on an open
epoch) and OPS (a deployment or admission failure) — route the same way but resolve
differently. Do not guess it into one.

## Hard boundaries carried from the brief

- **Never register an arm to unblock a tag.** Registration is not a fix for silence;
  it would manufacture lineage for a book nobody decided to run.
- **Never edit `MMSELL_VARIANTS` without explicit operator confirmation.**
- **Never replace that value wholesale.** Note that *removing* the four dead entries is
  not an append, so the append-only habit does not protect it: DERIVE the new string
  from the current one programmatically, the way `variants_for_recut()` does, and refuse
  rather than overwrite on any entry whose spec is not what is believed. The value is
  one ~1500-character string holding every mmsell book, and hand-composing it is how a
  running book gets dropped by a typo. Dropping a book stops it silently.
- `mmsell9`'s entry must NOT be removed alongside the four. Its experiment is alive; the
  question is why its arm closed, not whether to finish killing it.

## Drafted tickets — NOT FILED

No Control Tower candidate fingerprint exists for either (the detectors do not cover
this class), so both are `OPEN_MANUAL`, not `OPEN_CANDIDATE`. A Live Ops session submits
them through `EXPERIMENT_OS_ISSUE_COMMAND` as one array, then reads the receipts and
confirms through `issue-show`. Check `issue-command-list` for an unconsumed envelope
from another session first; a `REFUSED` verdict there is the guard working.

```jsonc
[
 {"command_id":"lo-open-mmsell9-dark-20260911","action":"OPEN_MANUAL",
  "actor":"cal","actor_role":"LIVE_OPS","schema_version":1,
  "payload":{
    "title":"mmsell9 went dark 2026-09-02: carrier deployment ended with no recorded transition",
    "problem_statement":"mmsell-ceiling-paper-mmsell9-1 (the native carrier opened 2026-08-28 to keep mmsell9 admissible when the Stage-1 canary armed off v1) ended 2026-09-02 00:57:38.273309Z. mmsell9's last paper_trades row is 2026-09-02 00:56:31.979599Z, 67s earlier. The experiment mmsell-price-ceiling is still LIVE_CANARY with retired_at NULL and epoch v1/e1 still open; the experiment has exactly two lifecycle transitions ever (import 2026-08-16, PAPER->LIVE_CANARY 2026-08-28) and neither is on 2026-09-02. mmsell9 remains in production MMSELL_VARIANTS, so under NEW_ONLY it is constructed and refused at the write path every scan cycle: 9.6 days of silent inadmissibility for an arm declared in the frozen v1 contract. Establish what ended the deployment (receipts around 2026-09-02 00:57Z were below the window read; the transitions read was complete). Do NOT register an arm to unblock the tag.",
    "classification":"UNCLASSIFIED",
    "owner_role":"LIVE_OPS",
    "severity":"HIGH",
    "priority":"P1",
    "experiment":"mmsell-price-ceiling",
    "version":1,
    "deployment":"mmsell-ceiling-paper-mmsell9-1",
    "reason":"Detected by Experiment Control Tower 2026-09-11 while classifying five configured-but-unarmed MMSELL_VARIANTS tags. Four were recorded retirements; this one is not. Cause of the deployment closure is not established, so classification stays UNCLASSIFIED and Live Ops looks first."}},

 {"command_id":"lo-open-variants-residue-20260911","action":"OPEN_MANUAL",
  "actor":"cal","actor_role":"LIVE_OPS","schema_version":1,
  "payload":{
    "title":"Four retired books remain in MMSELL_VARIANTS and are refused every cycle",
    "problem_statement":"mmsellA4, mmsellA5, Lmmsell8 and Lmmsell10 are all RETIRED in Experiment OS as of 2026-09-06 by recorded, authorized acts (RETIRE_ON_GATE_FAIL receipts rl-retire-volentry-20260906 and rl-retire-strangle-20260906; STAND_DOWN receipt ts-standdown-settlelive-20260906), with their deployments ended and epochs closed. Their MMSELL_VARIANTS entries were never removed, so production constructs four dead books every scan cycle and the write path refuses each one. No evidence is being lost - these books are dead on purpose. The cost is wasted work per cycle and a config surface that reads as the list of live books when it is not. Remedy needs explicit operator confirmation to change MMSELL_VARIANTS, and the new value must be DERIVED from the current one programmatically (the variants_for_recut pattern), never hand-composed: the string holds every mmsell book and a typo drops a running one silently. Do not touch mmsell9's entry - its experiment is alive and it is a separate ticket.",
    "classification":"OPS",
    "owner_role":"LIVE_OPS",
    "severity":"LOW",
    "priority":"P3",
    "reason":"Detected by Experiment Control Tower 2026-09-11. Retirement ends deployments and closes epochs but does not clean up the runtime config that defines the book, so a retired book keeps being constructed. Same family as XOS-000012 (config and lifecycle record disagreeing about whether a book is running)."}}
]
```

## What was read

| # | read | request id |
|---|---|---|
| 1 | `live_orders` by strategy/status — real-money exposure per tag | `dead-live-1` |
| 2 | `xos control-tower` — integrity, open investigations, retirements | `dead-ct-1` |
| 3 | XOS lineage join: arms → deployments → epochs → versions → experiments, for the five tags | `dead-lin-2` |
| 4 | `paper_trades` first/last/count per tag | `dead-ev-1` |
| 5 | `env` — deployed `MMSELL_VARIANTS`, `LIVE_STRATEGIES` | `dead-env-1` |
| 6 | every arm on an open deployment in an open epoch (scope check) | `dead-all-1` |
| 7 | `experiment_state_transitions` for `mmsell-price-ceiling` | `dead-tr-1` |
| 8 | `xos experiment-command-list` — authorizing receipts | `dead-xc-1` |

Ops channel was reset to `{"type":"noop"}` afterwards.
