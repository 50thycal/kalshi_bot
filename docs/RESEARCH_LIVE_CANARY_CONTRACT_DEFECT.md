# Research Lab finding — both imported live-canary contracts are malformed

**Session:** Research Lab, 2026-08-17. **Status:** analysis + proposed correction.
**No production lifecycle state was changed.** Every action below needs operator
approval before it happens.

Two imported LIVE_CANARY experiments carry registered gates that cannot evaluate
the evidence they exist to evaluate:

* `mmsell-scheduled-settle-live`
* `theta4-fat-tail`

Both are `legacy_class=ACTIVE_LIVE`, `migration_integrity=B`, imported at the
2026-08-16 migration. Both hold **real money right now**.

---

## 1. Current canonical state

Read from Experiment OS (`xos show`), not from status docs.

### `mmsell-scheduled-settle-live`

| field | value |
|---|---|
| lifecycle state | `LIVE_CANARY` |
| version | **v1**, frozen `2026-08-16 14:14:43.720928+00` |
| epoch | **e1**, started `2026-08-16 14:14:43.720928+00`, open |
| platform snapshot | migration baseline (10/10 components; `FEE_MODEL=maker_rate_2026_08_11`, `FILL_MODEL=assumed_fill_plus_mmsell3_calibration`) |
| arms | `price_ceiling` (control, `Lmmsell10`), `scheduled_settle` (treatment, `Lmmsell8`) |
| deployments | `lmmsell-live-1` **kind=live** @ `2026-08-15 12:56:54.393462+00`; `lmmsell-twin-pt3` **kind=paper_twin** @ **same instant** |
| twin boundary | **exact** — armed at the same instant |
| gate | `live_canary_keep`, promotion, `LIVE_CANARY → PRODUCTION`, evidence from `2026-08-15` |
| recorded result | `BLOCKED_DATA` |
| dry-run result | `BLOCKED_DATA` (agrees — no divergence) |
| integrity | B, `ACTIVE_LIVE`, predecessor experiment id 5 |

**Live evidence now:** `Lmmsell10` 241 filled orders / 482 filled contracts /
227 settled positions / +$5.49; `Lmmsell8` 15 filled / 30 contracts / 7 settled /
−$4.20. **Open live exposure: `Lmmsell10` 17 positions $33.49 + `Lmmsell8` 8
positions $16.42 ≈ $49.91.** Twins settled: `Lmmsell10_pt3` 112,
`Lmmsell8_pt3` 13.

### `theta4-fat-tail`

| field | value |
|---|---|
| lifecycle state | `LIVE_CANARY` |
| version | **v1**, frozen `2026-08-16 14:14:43.720928+00` |
| epoch | **e1**, started `2026-08-16 14:14:43.720928+00`, open |
| platform snapshot | same migration baseline |
| arms | `theta4` (treatment, tag `theta4`) — **single arm, no control** |
| deployments | `theta4-live-1` **kind=live** @ `2026-07-30 00:00:00+00`; `theta4-twin-pt3` **kind=paper_twin** @ `2026-08-12 00:15:08+00` |
| twin boundary | **differs by 13 days** — recorded grandfathered asymmetry |
| gate | `live_canary_keep`, promotion, `LIVE_CANARY → PRODUCTION`, evidence from `2026-07-30` |
| recorded result | `BLOCKED_DATA` |
| integrity | B, `ACTIVE_LIVE`, predecessor experiment id 10 |
| legacy evidence | `context_only` — "paper gate cleared 2026-07-28", n=95, $0.392/trade |

**Live evidence now:** 86 filled orders / 260 filled contracts / 85 settled
positions / +$2.24. **Open live exposure: 1 position, $2.88.** Twin settled:
`theta4_pt3` 41.

---

## 2. The defect, proven

Every clause on both gates omits `deployment_kind`, so each defaults to
`"paper"`. Neither epoch contains a `paper` deployment.

Run against the **canonical resolver** (`evaluator._arm_scope`) on a faithful
local mirror of the registered shape:

```
mmsell-scheduled-settle-live
  epoch deployments: [('lmmsell-live-1', 'live'), ('lmmsell-twin-pt3', 'paper_twin')]
    arm=scheduled_settle  kind=paper       -> ()   <-- EMPTY SCOPE   [registered clauses resolve here]
    arm=scheduled_settle  kind=live        -> ('Lmmsell8',)
    arm=scheduled_settle  kind=paper_twin  -> ('Lmmsell8_pt3',)
    arm=price_ceiling     kind=paper       -> ()   <-- EMPTY SCOPE   [registered clauses resolve here]
    arm=price_ceiling     kind=live        -> ('Lmmsell10',)
    arm=price_ceiling     kind=paper_twin  -> ('Lmmsell10_pt3',)

theta4-fat-tail
  epoch deployments: [('theta4-live-1', 'live'), ('theta4-twin-pt3', 'paper_twin')]
    arm=theta4            kind=paper       -> ()   <-- EMPTY SCOPE   [registered clauses resolve here]
    arm=theta4            kind=live        -> ('theta4',)
    arm=theta4            kind=paper_twin  -> ('theta4_pt3',)
```

### Clause-by-clause

**`mmsell-scheduled-settle-live / live_canary_keep`** — 4 clauses, all malformed:

| clause | metric | implicit kind | resolves to | also |
|---|---|---|---|---|
| sample `price_ceiling` | `live_settled_contracts` | paper | **empty** | provider unprovided |
| sample `scheduled_settle` | `live_settled_contracts` | paper | **empty** | provider unprovided |
| pass_all | `twin_live_winrate_gap_pp` | paper | **empty** | provider unprovided |
| pass_all | `delta.live_cents_per_contract` | paper | **empty** | provider unprovided |

**`theta4-fat-tail / live_canary_keep`** — 3 clauses, all malformed:

| clause | metric | implicit kind | resolves to | also |
|---|---|---|---|---|
| sample `theta4` | `settled_trades` | paper | **empty** | provider EXISTS (universal) |
| pass_all | `pnl_cents_per_trade` | paper | **empty** | provider EXISTS (universal) |
| pass_all | `realized_tail_hit_ratio_vs_modeled` | paper | **empty** | provider unprovided |

**The two defects are independent and currently one masks the other.** The
evaluator checks "is any required metric missing?" before it reports empty
scopes, so today both gates report `BLOCKED_DATA — no canonical provider`. Ship
the live providers and `mmsell-scheduled-settle-live` would move from
"blocked on providers" to "computing over nothing" — still not evaluating its
live evidence, but with a less obvious symptom. theta4 shows the pure form
already: two of its three clauses have working providers and still measure
nothing.

### The trap this creates

`Lmmsell10` has **754 settled paper rows** and `theta4` has **214** under the
same tag names (their pre-live paper history and shadows). If anyone "fixed"
these gates by registering a `paper`-kind deployment for those tags, the clauses
would start resolving — to a live book's **paper shadow** — and a
`LIVE_CANARY → PRODUCTION` decision would be made on evidence that never touched
real execution. That is worse than the current honest block. **The metric layer
must not be asked to compensate for the addressing.**

---

## 3. Proposed corrected native successors

Thresholds are carried across unchanged wherever a threshold was genuinely
pre-registered for that decision. Where none was, this document says so instead
of inventing one.

### 3a. `mmsell-scheduled-settle-live` v2

Everything about the science is unchanged; only the addressing is corrected.

* **Hypothesis (verbatim carry-forward):** scheduled-settle series (no in-play
  informed flow) suffer less maker adverse selection, so `Lmmsell8`'s allowlist
  realizes MORE of its paper edge live than the unfiltered price-ceiling book —
  a FILL claim paper cannot evaluate.
* **Independent variable:** the scheduled-settle series allowlist.
* **Held constant:** clip size ($2 / 2 contracts), arming instant, band mechanics
  and engine, risk envelope, platform snapshot.
* **Arms:** `scheduled_settle` (treatment) / `price_ceiling` (control) — same
  roles, **fresh tags** (see §6).
* **Platform snapshot binding:** current active snapshot at arming.

Corrected `live_canary_keep` (`LIVE_CANARY → PRODUCTION`):

```jsonc
{
  "sample": {
    "scheduled_settle": {"metric": "live_settled_contracts",
                         "deployment_kind": "live", "op": ">=", "value": 150},
    "price_ceiling":    {"metric": "live_settled_contracts",
                         "deployment_kind": "live", "op": ">=", "value": 150}
  },
  "pass_all": [
    {"metric": "twin_live_winrate_gap_pp", "arm": "scheduled_settle",
     "deployment_kind": "live", "op": "<=", "value": 1.0},
    {"metric": "delta.live_cents_per_contract",
     "treatment": "scheduled_settle", "control": "price_ceiling",
     "deployment_kind": "live", "op": ">=", "value": 1.0}
  ]
}
```

All three thresholds (150 contracts, 1.0pp, 1.0¢) are **unchanged** from v1.
The only edit is the explicit `deployment_kind: "live"`.

`twin_live_winrate_gap_pp` spans two kinds by definition. Design decision: the
clause addresses the **live** scope, and the provider resolves that deployment's
**registered twin** through `twin_of_deployment_id` — the identity model, not a
strategy-name convention, and not a second clause the gate author must remember.

### 3b. `theta4-fat-tail` v2 — a harder case, and not a mechanical fix

theta4's imported clauses are not merely mis-addressed; they are **paper metrics
on a live-canary keep gate**. `settled_trades` and `pnl_cents_per_trade` measure
the paper book. Even had a paper deployment existed, they would have answered the
wrong question for a `LIVE_CANARY → PRODUCTION` decision.

The experiment's own hypothesis settles the intent: *"…is profitable **after real
maker execution**."* And its imported gate note says: *"Also gate on the theta
fill model's realizable ¢/trade, not paper alone."* Both point at a live-basis
decision that the structured contract never expressed.

**Consequence: theta4 has no pre-registered LIVE threshold at all.** The `>= 80`
and `> 0` bars were registered against paper. Carrying "80" into a live unit
(contracts, or settled live positions) is **not the same threshold** — it is a new
one wearing an old number. theta4 currently has **85 settled live positions**, so
a floor of 80 would be satisfied on arrival; adopting it would be fitting the bar
to the data.

**This document therefore does not set theta4's live floor.** Proposed shape,
with the floor left explicitly open:

```jsonc
{
  "sample": {
    "theta4": {"metric": "live_settled_contracts",
               "deployment_kind": "live", "op": ">=", "value": "<<OPERATOR TO PRE-REGISTER>>"}
  },
  "pass_all": [
    {"metric": "live_cents_per_contract", "arm": "theta4",
     "deployment_kind": "live", "op": ">", "value": 0},
    {"metric": "realized_tail_hit_ratio_vs_modeled", "arm": "theta4",
     "deployment_kind": "live", "op": "<=", "value": 1.25}
  ]
}
```

Notes carried into the contract rather than left in prose:

* `> 0` carries the **intent** of the registered bar (profitable after real
  execution) into the live unit. The v1 note's pre-2026-08-11 fee re-baseline
  caveat (*"read as > +0.87¢ on pre-boundary trades"*) **no longer applies**: any
  v2 epoch starts well after that boundary, so `> 0` is read as written. This
  caveat existing only in a note is exactly why it belongs in the contract.
* `<= 1.25` on `realized_tail_hit_ratio_vs_modeled` is genuinely pre-registered
  for this decision and carries across unchanged.
* **No twin clause is proposed.** theta4's twin was armed 13 days after live, so
  no twin-vs-live comparison over v1 is sound. If v2 wants one, it needs a fresh
  twin at the same instant (§6) and a freshly pre-registered bar.
* theta4 has **no control arm**. v1 carried a `control_exemption_reason`
  ("single-arm live pilot: the paper twin is the execution control"). With a
  same-instant twin, that exemption becomes defensible again; without one it is
  not. Flagged for the operator.

---

## 4. Version vs Epoch

**Both need a new Version.** The evidence contract — what the gate measures and
over which deployments — is part of the scientific question. A gate that
addresses the wrong deployment kind is a different (and unanswerable) question,
not a typo in an answer. Per Experiment OS semantics, a changed question is a new
**Version**; a changed world is a new **Epoch**.

A new Version structurally gets its own epoch (e1 of v2), so "new epoch" is
implied. The substantive question is whether v2's epoch may **back-date** to
absorb v1's live evidence. Analysed in §5: **no.**

The old v1 stays exactly as it is — frozen, `LIVE_CANARY`, with its recorded
`BLOCKED_DATA` results intact as the honest record that this contract could not
evaluate.

---

## 5. Existing evidence: reusable / reference-only / restart

### Class B — reference-only (both experiments, all existing live + twin evidence)

Every live row and twin row now in production is lineage-bound to **v1's**
deployment arms via `experiment_deployment_arm_id`. Counting it toward a v2 gate
would require re-pointing those rows at v2 — retroactively attaching evidence to
a contract it was not produced under. That is falsifying lineage, and it is
precisely what the lineage column exists to prevent.

This is a real cost and worth stating plainly: `Lmmsell10`'s **227 settled live
positions** and theta4's **85** do not carry forward as gate evidence. They
remain fully valid as **context** — for sizing expectations, for judging whether
v2 is behaving like v1, and as the historical record of what the money did.

### Class A — legitimately poolable: **none**

Tempting argument: for mmsell nothing about the science changed — same
hypothesis, same arms, same tags, same thresholds, same snapshot, same
same-instant twin — so the evidence is comparable and should pool. The
comparability argument is sound; the **lineage** argument defeats it. Experiment
OS binds evidence to the deployment arm that produced it, and v2's arms will not
be those arms. Sample size is not a reason to break that.

### Class C — must start fresh

Everything the v2 gates will actually count. Both successors begin at n=0 on live
evidence.

**theta4 has an additional, independent reason.** Its twin is 13 days late, so
even as reference evidence any twin-vs-live read is only meaningful from
2026-08-12 onward. That is a measurement defect in the evidence itself, not just
a lineage constraint.

---

## 6. Existing live deployments

**Can the current live deployment safely continue while v2 is prepared?**
Yes. It is trading under a registered, admitted lineage; enforcement is
satisfied; the only thing broken is that its gate cannot render a verdict. A
canary that cannot be judged should not be *promoted*, but it is not unsafe. Real
exposure is small (~$49.91 mmsell, ~$2.88 theta4). **No action is proposed here;
stopping it is a separate operator decision.**

**Can the current live deployment ever legally belong to v2?**
No. Deployments belong to epochs; epochs belong to versions. Moving
`lmmsell-live-1` under v2 would rewrite its lineage and orphan the rows already
written against it.

**Would attaching it retroactively falsify lineage?** Yes — directly. Not proposed.

**Does a new native live deployment need to be created?** Yes, for each successor.

**Does the twin need to be recreated?** Yes. A twin belongs to its live
deployment; a new live deployment needs its own twin.

**Must the live/twin pair start on the same boundary?** Yes —
`service.arm_live_canary` enforces it, and the 2026-08-15 Lmmsell failure is why
that is structural. theta4's 13-day-late twin is exactly the defect a
same-instant pair prevents.

**Is a new Epoch mandatory because the deployment boundary changes?** Yes: v2's
epoch is new by construction, and the new deployment's boundary defines its
evidence floor.

### The tag constraint — load-bearing

A strategy tag resolving to **more than one active deployment arm is refused as
ambiguous** by the enforcement resolver. So v1's and v2's live deployments
**cannot both be active on the same tags**. That leaves exactly two orderings:

1. **Fresh tags for v2** (e.g. `Lmmsell8b`/`Lmmsell10b`) — v1 may keep running
   during changeover; the two coexist cleanly; v2 starts from a clean tag with no
   inherited paper state, which is what `arm_live_canary` requires anyway.
2. **Reuse the same tags** — v1's deployment must be closed *before* v2's is
   armed, creating a gap in live coverage and (briefly) an unmanaged position
   book.

**Recommendation: option 1, fresh tags.** It is the only ordering that never
produces an ambiguous tag, never needs a coverage gap, and satisfies the
fresh-tags rule for arming a canary.

**Conclusion: fresh live + twin pair, under v2, on fresh tags.** Continuity of
the *number* is not preserved. That is the correct outcome, not a regrettable
one — the alternative is a promotion decision resting on rewritten lineage.

---

## 7. Provider dependency matrix

| provider | required by | status |
|---|---|---|
| `live_settled_contracts` | mmsell v2 (both arms), theta4 v2 | **unprovided** — reference `scripts/mmsell_live.py` |
| `live_cents_per_contract` | mmsell v2 (as `delta.`), theta4 v2 | **unprovided** — reference `scripts/mmsell_live.py` |
| `twin_live_winrate_gap_pp` | mmsell v2 | **unprovided** — reference `scripts/live_paper_parity.py` |
| `realized_tail_hit_ratio_vs_modeled` | theta4 v2 | **unprovided** — reference `scripts/theta_fill_model.py` |
| `settled_trades`, `pnl_cents_per_trade` | v1 only (not carried into v2) | canonical (`universal_v1`) |
| `realizable_cents_per_trade` | not gate-required; relevant to theta4's "not paper alone" note | canonical (`fill_model_v1`) |

**All four v2-required providers are currently unprovided.** Three have trusted
reference implementations; `realized_tail_hit_ratio_vs_modeled` has
`scripts/theta_fill_model.py` as reference but its trustworthiness for a
*promotion* bar has not been assessed in this session.

Additional scoping requirement for the new providers: they must honour explicit
`deployment_kind` and **return MISSING when the requested scope is structurally
impossible** — never fall back to another kind. A provider that quietly read
paper when asked for live would hide exactly the defect this document is about.

---

## 8. Decision packages

```text
EXPERIMENT: mmsell-scheduled-settle-live

CURRENT:
  Version v1 (frozen 2026-08-16 14:14:43Z), Epoch e1 (open), LIVE_CANARY
  malformed gate: all 4 clauses omit deployment_kind -> default "paper";
  epoch holds only kind=live + kind=paper_twin, so every clause resolves to an
  EMPTY scope. Recorded verdict BLOCKED_DATA (currently attributed to missing
  providers, which masks the addressing defect).

PROPOSED:
  new native Version v2 (corrected addressing only; science unchanged)
  new Epoch required: YES (structural — v2 gets e1; no back-dating)
  existing evidence:
    reusable:       NONE
    reference-only: all live + twin evidence under v1
                    (Lmmsell10 227 settled/+$5.49, Lmmsell8 7 settled/-$4.20,
                     twins 112/13)
    restart:        all gate-counting evidence — v2 begins at n=0
  existing deployment:
    may continue temporarily: YES (safe, registered, admitted; just unjudgeable)
  successor deployment:
    new live/twin pair required: YES, on FRESH TAGS, armed at the same instant
    via service.arm_live_canary

CORRECTED GATE (thresholds unchanged; only deployment_kind added):
  sample:   live_settled_contracts >= 150   [kind=live]  on BOTH arms
  pass_all: twin_live_winrate_gap_pp <= 1.0 [kind=live]  arm=scheduled_settle
            delta.live_cents_per_contract >= 1.0 [kind=live]
                                            treatment=scheduled_settle
                                            control=price_ceiling

REQUIRED PROVIDERS (all currently unprovided):
  live_settled_contracts, live_cents_per_contract, twin_live_winrate_gap_pp

PROPOSED LIFECYCLE:
  1. implement the three live providers (canonical, kind-aware, MISSING on
     impossible scope)
  2. create v2 with corrected clauses; freeze
  3. open v2/e1 bound to the then-active platform snapshot
  4. arm a fresh live+twin pair on fresh tags via arm_live_canary
  5. decide v1's disposition separately (continue / stop / supersede)
  6. let the gate runner evaluate v2 normally

RISK / TRADEOFF:
  Loses 227 settled live positions of accumulated gate evidence; v2 restarts at
  n=0 against a 150-contract floor per arm. At Lmmsell8's observed rate (30
  filled contracts since 2026-08-15) that floor is a long way off — the treatment
  arm is flow-starved, which is itself a finding the operator should weigh before
  re-arming an experiment that may never reach its own floor.

REQUIRES OPERATOR APPROVAL BEFORE:
  creating v2, arming any deployment, stopping/superseding/retiring v1, and any
  change to live exposure.
```

```text
EXPERIMENT: theta4-fat-tail

CURRENT:
  Version v1 (frozen 2026-08-16 14:14:43Z), Epoch e1 (open), LIVE_CANARY
  malformed gate: all 3 clauses omit deployment_kind -> default "paper"; epoch
  holds only kind=live + kind=paper_twin -> EMPTY scope. Additionally two of the
  three clauses are PAPER metrics (settled_trades, pnl_cents_per_trade) on a
  LIVE_CANARY -> PRODUCTION gate, so they would answer the wrong question even
  with a paper deployment present.
  Twin armed 13 days after live (2026-08-12 vs 2026-07-30) — no sound
  twin-vs-live comparison exists over v1.

PROPOSED:
  new native Version v2 (addressing AND basis correction — not mechanical)
  new Epoch required: YES
  existing evidence:
    reusable:       NONE
    reference-only: 85 settled live positions / +$2.24; twin 41 settled but only
                    meaningful from 2026-08-12; legacy paper note (context_only,
                    n=95, $0.392/trade) stays context_only
    restart:        all gate-counting evidence
  existing deployment:
    may continue temporarily: YES (exposure is $2.88, 1 open position)
  successor deployment:
    new live/twin pair required: YES — and here the twin MUST be same-instant,
    since the missing same-instant twin is a second, independent defect

CORRECTED GATE (basis corrected to live; one threshold NOT inherited):
  sample:   live_settled_contracts >= <<OPERATOR TO PRE-REGISTER>>  [kind=live]
  pass_all: live_cents_per_contract > 0                             [kind=live]
            realized_tail_hit_ratio_vs_modeled <= 1.25              [kind=live]

  NOTE: theta4 has NO pre-registered live floor. The v1 ">= 80" was registered
  against PAPER trades. theta4 already has 85 settled live positions, so reusing
  "80" would set a bar the book has already cleared — fitting the threshold to
  the data. This must be pre-registered afresh by the operator.
  The v1 fee-re-baseline caveat ("read > 0 as > +0.87c pre-2026-08-11") does not
  apply to any v2 epoch and is retired into the contract explicitly.

REQUIRED PROVIDERS (all currently unprovided):
  live_settled_contracts, live_cents_per_contract,
  realized_tail_hit_ratio_vs_modeled

PROPOSED LIFECYCLE:
  1. implement live providers (shared with mmsell) + the theta tail-ratio
     provider, or decide the tail clause is not trustworthy enough to gate on
  2. OPERATOR pre-registers the live sample floor and decides the control
     question (same-instant twin as execution control, or a real control arm)
  3. create v2; freeze; open v2/e1
  4. arm a fresh live+twin pair, same instant, fresh tags
  5. decide v1's disposition separately
  6. normal gate-runner evaluation

RISK / TRADEOFF:
  This is the more invasive correction: it changes the DECISION BASIS from paper
  to live, which is a genuine scientific change, not a repair. Doing nothing is
  also a choice with a cost — theta4 currently holds real money under a gate that
  can never render a verdict, so it can neither graduate nor be killed on
  evidence.

REQUIRES OPERATOR APPROVAL BEFORE:
  creating v2, setting the live sample floor, resolving the control/twin
  question, arming any deployment, stopping/superseding/retiring v1, and any
  change to live exposure.
```

---

## 9. What was deliberately NOT done

No frozen gate edited. No imported history rewritten. No migration integrity
upgraded. No rows re-pointed at a new Version. No Version retired. No live
exposure changed. No successor armed. No lifecycle transition. This document is
the deliverable; every write above waits on an explicit operator decision.

---

## 10. Architectural note — these two are the design examples

Both findings arrived the same way: the Control Tower surfaced a symptom
(`BLOCKED_DATA`), and turning it into durable work required a human-driven hop
across roles — Control Tower → Research Lab → (metrics implementation) →
operator approval → Live Ops.

Nothing carried that thread but chat. The same shape has now recurred for:
malformed experiment contract (×2), FREEZE zero evidence, missing provider,
runtime/config drift, collector problems, and platform-impact blockers.

For the future investigation-ticket design, these two are the useful worked
examples because they exercise the hard parts: a finding that **spans several
roles**, that **must not be auto-fixed** (a ticket that "repairs" a frozen gate
would be a catastrophe), that **blocks on a prerequisite** (providers) before it
can even be verified, and that **ends in an operator approval gate** rather than
a merge. A ticket system that handles these two correctly handles the easy ones
for free. Not built in this session, by instruction.
