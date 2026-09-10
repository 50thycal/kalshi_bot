# Handoff — make `Gmmsell2` actually trade

**Scope: one finite job.** `Gmmsell2` exists in `kalshi_bot/config.py` as a book spec and in
`regimes.SUBJECT_SPLIT_SERIES` as a mechanism (PR #371). It is **inert**. This handoff is the
whole of what turns it on, and nothing else.

Design + evidence: `docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md`. Predecessor contract:
`docs/MMSELL_CORRELATION_CAP.md`, `kalshi_bot/experiment_os/correlation_cap.py`.

> **No real money anywhere in this job.** Every arm named here is PAPER. Nothing below arms a
> canary, moves a lifecycle state, evaluates a gate or expands exposure. If a step starts to
> look like it does, it is the wrong step.

---

## 1. Two independent things are required, and each is invisible without the other

| | what | where | if missing |
|---|---|---|---|
| **A** | `Gmmsell2` carried by an **active deployment arm** | Experiment OS (Postgres) | Under `NEW_ONLY` the tag is **refused at the write path**. It cannot trade. |
| **B** | `Gmmsell2` present in production's **`MMSELL_VARIANTS`** | Railway env var | The worker never constructs the book. It is registered, ARMED, and silently trades nothing. |

**B is the failure that has already happened here.** On 2026-09-05 `mmsell-correlation-cap` was
registered *correctly* — PAPER, v1 frozen, two arms with tags, an active paper deployment, a
registered gate — and traded nothing for twelve hours, because `MMSELL_VARIANTS` is an env var
that **overrides the code default the books were added to**. Every object Experiment OS owns was
correct; the Tower reported PAPER and healthy at n=0, which is also what a legitimately new
experiment looks like. That incident is why `control_tower._silent_arms` exists. Read its
docstring before doing this — it is the postmortem.

**Registration is not configuration.** Do A and B in one sitting, **A first**: A-then-B leaves a
silent arm for minutes (the detector needs `SILENT_ARM_HOURS`), while B-then-A puts a tag the
write path refuses into a live worker's book list.

---

## 2. The XOS shape — `Gmmsell2` cannot be bolted onto v1

`service.add_arm` refuses a frozen version outright:

```
version {version} is frozen — a changed arm set is a new version
```

`mmsell-correlation-cap` v1 is frozen (`correlation_cap.register` freezes it before opening e1).
So there are three candidate shapes, and two of them are refused by the system's own rules:

| shape | verdict |
|---|---|
| **Epoch cut on v1**, carrying deployments forward | **Refused.** `service.carry_deployments_forward` re-registers *the same arms and tags*. An epoch is a changed **world**, not a changed **arm set**. No epoch cut can produce an arm that does not exist. |
| **New experiment**, `Gmmsell1` as an external control | **Refused, by v1's own reasoning.** `Gmmsell1` already carries an active deployment arm, and a tag carries one. Naming it an EXTERNAL control is precisely what has `mmsell-anchor-vol-entry` in `BLOCKED_PLATFORM`: a cross-snapshot delta pools incomparable evidence. See `correlation_cap`'s "WHY THE CONTROL IS `Gmmsell0` AND NOT `mmsell10`". |
| **v2 of `mmsell-correlation-cap`**, three arms in one epoch | **Recommended.** |

### Why v2 is cheap right now

v1 was registered **2026-09-05** against a **60-settlement-day** floor. Recutting today
discards roughly two settlement days of a sixty-day window. In a month this option is expensive;
today it costs almost nothing. **That is the reason to do it now rather than later.**

### What v2 must contain

A new package module (suggest `kalshi_bot/experiment_os/correlation_cap_v2.py`) plus an entry in
`experiment_commands._packages()`, doing exactly this and stopping:

1. `service.create_experiment_version` on the **existing** experiment — v2, changed question:
   *what is the right unit of correlation*, not merely *does capping one help*.
2. Three arms, the first two **imported** from `correlation_cap.ARMS` rather than retyped, so v2
   cannot silently carry a changed control:

   | arm | role | tag | book |
   |---|---|---|---|
   | `uncapped` | CONTROL | `Gmmsell0` | `lo=5,hi=10,maxyes=7` |
   | `contest_capped` | TREATMENT | `Gmmsell1` | `…,contestcap=1` |
   | `contest_capped_split` | TREATMENT | `Gmmsell2` | `…,contestcap=1,contestkey=split` |

3. `HELD_CONSTANT` gains one line: **the contest KEY is now an arm axis**, so no arm may change
   its key for the life of the version. (Under v1 the key was held constant implicitly, by there
   being only one.)
4. The keep gate, re-registered on the three-arm scope. Keep the **primary** as v1's
   `delta.daily_pnl_stability(contest_capped − uncapped)`. The new comparison,
   `contest_capped_split − contest_capped`, is a **pre-registered secondary read that isolates
   the key** — not a promotion criterion, and not a third bar the arm must clear. A gate spec
   that does not resolve against the frozen arm set is refused (`service.register_gate`).
5. `freeze_version`, `open_epoch` on the ACTIVE snapshot, and **one** paper deployment carrying
   all three tags. v1's paper deployment ends at the same instant — two active deployment arms
   on one tag is ambiguous and refused by the resolver.

**Evidence does not pool across the version boundary.** `Gmmsell0` and `Gmmsell1` keep their
tags and their tapes; the metric scope windows on the epoch, which is the entire point of
cutting one. Do not write anything that reads v1 and v2 evidence together.

### An inconsistency to fix while you are in there

`experiment_commands._packages()["mmsell-correlation-cap"].description` and
`correlation_cap.register`'s docstring both say **three arms**; `ARMS` has **two**. The
production experiment has two. v2 makes the description accidentally true — correct it
deliberately instead, so the next reader does not infer that an arm went missing.

---

## 3. Step A — send the envelope

`REGISTER_PACKAGE` is allowed for `RESEARCH_LAB`, `TASK_SPECIFIC` and `LIVE_OPS`. It registers a
contract and stops: it arms nothing and places no order.

```json
{"type":"env","set":{"EXPERIMENT_OS_EXPERIMENT_COMMAND":"{\"command_id\":\"gmmsell2-register-1\",\"action\":\"REGISTER_PACKAGE\",\"actor\":\"claude-code\",\"actor_role\":\"TASK_SPECIFIC\",\"payload\":{\"package\":\"mmsell-correlation-cap-v2\",\"approved_by\":\"<person>\",\"reason\":\"<why>\"},\"schema_version\":1}"}}
```

Constraints that will bite:

* **`command_id` is 8–64 chars of `[A-Za-z0-9._-]` and is the sole basis of the receipt.** Reuse
  is not a retry — it is a claim on someone else's record. Use a fresh one per attempt.
* `approved_by` must **name a person** and is validated against `_ACTOR_RE` after stripping;
  whitespace is not a name. `reason` is likewise required. Both are stripped before validation.
* **The transport is single-slot**, consumed at the worker's next boot. A `REFUSED` verdict
  because another session's envelope is unconsumed is the guard **working** — wait, do not
  re-send. Send a whole workflow as ONE array of envelopes.
* `promotion_sample_floor` may only **raise** the 60-day floor, and it is measured in
  **settlement days**, not trades. Passing a trade count by habit makes the gate unreachable
  rather than stricter, and `correlation_cap` refuses a value below the floor explicitly.
* A second `REGISTER_PACKAGE` at the same experiment **raises**; it is not a quiet no-op.

Then read the receipt for your own `command_id` (never the shared pointer), budgeting minutes:

```bash
echo '{"type":"xos","command":"show","args":["mmsell-correlation-cap"],"id":"gm2-show-1"}' > ops/request.json
```

---

## 4. Step B — the env change

Production's `MMSELL_VARIANTS` overrides the code default. Set it to the code default's current
value **plus** the one book, verbatim:

```
Gmmsell2:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split;
```

Read the live value first — `{"type":"env","id":"gm2-env-read"}` returns the allowlisted vars —
and append to **what production actually holds**, not to what `config.py` says. Appending to the
code default is the whole failure mode in §1.

```json
{"type":"env","action":"set","values":{"MMSELL_VARIANTS":"<live value>Gmmsell2:lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split;"},"id":"gm2-env-set"}
```

**Setting an env var redeploys the worker.** `MMSELL_VARIANTS` is in `ops_meta.AUDIT_WORTHY_VARS`
and matches `_XOS_SENSITIVE_PREFIXES`, so the runner owes an `enforcement` then `readiness`
readback after the set — let it run and read it; that is the receipt, not the set itself.

An unrecognised `contestkey=` value **rejects the whole book spec** rather than falling back
silently, so a typo here removes the book instead of quietly running the old key. That is
deliberate. It also means: check the reload, do not assume it.

---

## 5. Verify — the job is not done until all four are true

1. `xos show mmsell-correlation-cap` lists **three** arms on v2 with `Gmmsell2` on an ACTIVE
   deployment.
2. `MMSELL_VARIANTS` in production contains the `Gmmsell2` spec, post-redeploy.
3. Within one scan cycle, `Gmmsell2` appears in the worker's configured books (a `db` read of
   `paper_trades` by `strategy`, or the cycle summary).
4. `xos control-tower` reports **no silent arm** for `Gmmsell2` after `SILENT_ARM_HOURS`. This
   is the check that would have caught 2026-09-05, and it is the one worth waiting for.

`Gmmsell2` should also start **declining fewer entries than `Gmmsell1`** — it is the less strict
arm by construction. `MmSellCycleSummary.skipped_contest_cap` is persisted per cycle; if
`Gmmsell2`'s count is not below `Gmmsell1`'s once both have flow, the key is not being applied
and something in §4 did not land.

---

## 6. What this job is not

* **Not a promotion.** No lifecycle transition, no gate evaluation, no verdict.
* **Not a widening of the cap.** `Gmmsell2` can only ever *admit* entries `Gmmsell1` refuses on
  markets sharing a date but no outcome. Against the uncapped control `Gmmsell0` it is still a
  cap.
* **Not a change to the global switch.** `mmsell_contest_cap_enabled` stays off. The treatment
  opts in per book, so no other book's selection moves.
* **Not the sports grouping.** The XOS-000020 cross-series grouping is unchanged under both keys
  and tested to stay that way.
