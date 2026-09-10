# MMSELL10 — Queue-aware cancellation (`mmsell10-queue-aware-cancel`)

**Status:** **REGISTERED and RUNNING IN SHADOW** as of 2026-09-07. The experiment is at **PROBE** in Experiment OS (`qac-register-20260907-2`, 11:30:57Z; v1 frozen, pre-registration hash `084214fc…`), and the worker runs `LIVE_QUEUE_CANCEL_MODE=shadow` on the live book — it **records** every decision to `live_order_queue_decisions` and **cancels nothing**. The first registration attempt (`qac-register-20260907-1`, 08:13:15Z) FAILED; see the incident note in §8. No order has been cancelled by this code path, and none can be until a separate Version, risk envelope and operator authorization put a tag on an active LIVE treatment arm.

Experiment OS is canonical for this experiment's state and gate verdicts (`DEC-001`) — read it with `xos show mmsell10-queue-aware-cancel`, not this line.

Package: `kalshi_bot/experiment_os/queue_aware_cancel.py` · rule: `kalshi_bot/live/queue_cancel.py`
· executor step: `LiveExecutor.evaluate_queue_cancellations` · audit table:
`live_order_queue_decisions` · report: `{"type":"script","name":"mmsell_queue_cancel_baseline"}`
· workstream: `docs/workstreams/WS-015-mmsell10-queue-aware-cancel.md`.

## 1. The question

mmsell10 rests a one-contract BUY-NO maker order at the no-bid and cancels it after four hours
if unfilled. While it rests it holds one of the book's 40 open slots — `count_live_book_open`
counts a resting order as open. Some resting orders sit thousands of contracts deep in Kalshi's
price-time queue and are unlikely to fill before the timeout. **Would cancelling those, and only
those, earlier release capacity for later candidates without buying a worse fill?**

This is an execution / capital-efficiency question. It changes no entry rule, no price offset
(0¢), no size, no risk cap and no exit (hold to settlement). Success is **more net dollars at
equal capital**, not a higher fill rate — the book's known problem is that the fills a maker wins
are the losers (`docs/MMSELL_FILL_MODEL.md`), and nothing here changes which fills it wins.

## 2. What already existed

Queue position has been sampled on every resting live order, every reconcile, since 2026-08-14
(`docs/LIVE_QUEUE_POSITION.md`; table `live_order_queue_ticks`). Kalshi's
`GET /portfolio/orders/queue_positions` (batch, filtered to our resting tickers) with a bounded
per-order fallback; the figure returned is `queue_position_fp`, a **contract quantity ahead of
us**, not an ordinal rank. So the historical telemetry the handoff asked for **does exist**, and
no queue value in this document is invented or inferred from displayed depth.

Paper books cannot express the treatment at all: paper assumes the resting order fills at entry
and has no queue. Queue telemetry is live-only, so the treatment's first stage is a **shadow**
over the live book's real resting orders (§6), not a paper book.

## 3. Baseline — read 2026-09-07, prior 336 h, read-only

Source: ops requests `qac-qp-1` (script `mmsell_queue_position --hours 336`) and `qac-b-2`…
`qac-b-6` (single read-only statements), reproduced by `mmsell_queue_cancel_baseline`. Live tags
in window: `Cmmsell10` (08-28→08-31, then stood down), `Dmmsell10` (09-02→09-04), `Emmsell10`
(09-06→09-07), `Fmmsell10` (09-07). Control-tower read `qac-ct-1` at 2026-09-06 22:15 CDT:
LIVE_CANARY = `mmsell-price-ceiling` (stood down), `mmsell-price-ceiling-capacity` (keep gate
PASS), `mmsell-price-ceiling-contest-cap` (HOLD); PROBE = none.

### Q1. Telemetry coverage

| | value |
|---|---|
| queue samples in window | 18,143 |
| distinct orders sampled | 408 |
| samples with a readable rank | 100.0% |
| samples with `contracts_ahead` | 100.0% |
| terminal orders with **no** sample at all | 34 of 408 (33 filled inside their first cycle, 1 cancelled) |

Sampling cadence ≈ 2.6 min (avg 39 ticks over a 103-min rest). **Coverage is not the
constraint.** An order without telemetry is almost always one that filled before the first
sample — the `no_tel` rows below — and the rule treats missing telemetry as keep anyway.

### Q2/Q3. Queue position, age and fill — the survival table

P(fill before the 4 h timeout | still resting at age A, contracts-ahead bucket at that instant),
each order read at its nearest sample (±3 min) to each checkpoint; terminal orders only
(`qac-b-3`). This is `live/queue_cancel.BASELINE_SURVIVAL_2026_09_07`, verbatim.

| age (min) | b0 | b1_10 | b11_100 | b101_1k | b1k_5k | b5k+ |
|---|---|---|---|---|---|---|
| 15  | 32.4% (148) | 27.3% (11) | 58.6% (29) | 31.1% (45) | 34.3% (35) | 31.3% (64) |
| 30  | 24.4% (127) | 25.0% (12) | 55.0% (20) | 26.3% (38) | 32.3% (31) | 26.3% (57) |
| 45  | 23.9% (117) | 25.0% (12) | 56.3% (16) | 22.2% (36) | 37.9% (29) | 23.1% (52) |
| 60  | 19.4% (103) | 25.0% (12) | 52.9% (17) | 21.2% (33) | 45.5% (33) | 15.2% (46) |
| 90  | 20.5% (78)  | 11.1% (9)  | 38.5% (13) | 19.4% (31) | 28.0% (25) | **10.0% (40)** |
| 120 | 16.1% (62)  | 0.0% (7)   | 33.3% (12) | 20.7% (29) | 25.9% (27) | **5.9% (34)** |
| 150 | 14.9% (47)  | 0.0% (7)   | 33.3% (12) | 23.1% (26) | 10.5% (19) | **6.1% (33)** |
| 180 | **4.9% (41)** | 0.0% (6) | 20.0% (10) | 16.7% (24) | 5.6% (18) | **3.8% (26)** |
| 210 | **0.0% (42)** | 0.0% (5) | 10.0% (10) | 10.5% (19) | 5.9% (17) | **0.0% (25)** |

(n in parentheses = orders still resting at that checkpoint. Bold = cells the frozen rule opens.)

Two readings. **Age dominates depth**: at every depth the conditional fill probability falls
with age, and by 180 min it is under 10% almost everywhere. **Depth discriminates only at the
extremes**: `b5k+` is the one bucket that is clearly unlikely from 90 min, and `b0` (front of the
queue, nobody crossing) only from 180 min. The middle buckets stay 20–45% far longer, partly
because queue depth *changes* while an order rests (`min_ahead` ≠ `first_ahead`); the rule
therefore reads the **current** depth, never the depth at placement.

Time-to-fill for orders that did fill (`qac-b-2`, by first-seen bucket): the p50 fill lands at
8 min for `b0`, 19–27 min for `b11_100`/`b101_1k`, 50–83 min for `b1k_5k`/`b5k+`; ≥95% of
eventual fills in every bucket had landed by 240 min. Fills are front-loaded.

### Q4. Capital-hours consumed by unfilled orders

Per book (`qac-b-4`): `Cmmsell10` 57 timeouts × 3.98 h avg = **211 $-hours**, plus 35
exchange-side cancels (empty reason, 1.5 h avg) = 47 $-hours; `Dmmsell10` 56 timeouts × 3.74 h
= **195 $-hours**, plus 43 exchange cancels = 68 $-hours. Filled orders consumed 61–78 $-hours
each book. At a **$1 clip the dollars are immaterial** (a freed order releases ≈ $0.93 for ≈ 2 h).
The scarce resource is the **slot**, not the capital.

Exchange-side cancels with no recorded reason are ~40% of cancels and average 1.5 h — almost
certainly market close. An order's real horizon is therefore `min(4 h, time to close)`; the
rule's remaining-timeout figure is recorded on every row so this can be refined in a later
Version, but v1 deliberately reads only the timeout the executor actually applies.

### Q5. Is capacity binding? (candidate flow after a slot is released)

`live_paper_parity_events` live outcomes per day (`qac-b-6`):

| day | tag | `gate:open_cap` refusals | placed |
|---|---|---|---|
| 08-28 | Cmmsell10 | 83 | 47 |
| 08-29 | Cmmsell10 | 145 | 79 |
| 08-30 | Cmmsell10 | 90 | 56 |
| 09-02 | Dmmsell10 | 93 | 98 |
| 09-03 | Dmmsell10 | 94 | 37 (+422 `gate:daily_loss`) |
| 09-04 | Dmmsell10 | 67 | 60 |

**Yes, the cap binds daily** on the capacity-stage book: refusals at the cap are 0.9–2.5× what
was placed. A freed slot has a queue of candidates waiting for it. (Whether those candidates
are as good as the ones already in the book is exactly the composition question the gates
carry — see §7.)

> **Correction, 2026-09-07 14:00Z — this evidence does not apply to the book the shadow
> observes, and clause (d) of §7 was written as though it did.**
>
> Every row above is `Cmmsell10` / `Dmmsell10` — **paper** capacity-stage books. The shadow
> instrument reads `Fmmsell10`, the **live** contest-cap canary, because queue telemetry
> exists only on live resting orders. Measured directly from the shadow's own rows over its
> first 2¼ hours: `Fmmsell10` sat at **11–13 open positions against a cap of 40**, and
> `cap_bound` was **false on 100% of decision rows** (519 rows, 12 distinct orders). No
> `gate:open_cap` refusal appears in the live logs at all.
>
> This is a scope error in the pre-registration, not a change in the world: the cap binds on
> books that have no queue, and the queue exists on a book where the cap does not bind. The
> consequence is stated in §7.


### The finding that cuts against the hypothesis — realized P&L by fill timing

Settled realized ¢/contract for filled orders, by age at fill × depth at the last sample before
the fill, uncontested markets only (`qac-b-5`):

| age at fill | fills | settled | ¢/contract | wins | losses |
|---|---|---|---|---|---|
| < 5 min (incl. 33 pre-sample) | 71 | 65 | **−7.8** | 55 | 10 |
| 5–30 min | 69 | 59 | +1.0 | 55 | 4 |
| 30–90 min | 40 | 37 | −3.5 | 33 | 4 |
| **90–180 min** | 27 | 22 | **+7.1** | 22 | **0** |
| **180+ min** | 10 | 7 | **+7.4** | 7 | **0** |

**Every fill that landed after 90 minutes was a winner** (29 settled, 0 losses, ≈ +7¢/contract,
i.e. the full maker premium). The losers are the *fast* fills — the adverse selection the fill
model describes lands in the first five minutes. A rule that cancels late-resting orders
therefore forgoes the book's *best* fills, not its worst. Within the cells the frozen rule
opens, the baseline shows 2 later fills in `b5k+` after 90 min and 4 in `b0` after 180 min, all
winners.

This does not kill the hypothesis — the value is the slot, and §Q5 says the slot is scarce — but
it fixes what the treatment must prove: **the profit forgone per cancel must be small relative
to what a recycled slot earns**, and the promotion bar is written that way (§7).

### Q6. Fields reliable enough to define a treatment without future information

Available at decision time, on every resting order, every cycle: `created_at` (age),
`limit_price`/`quantity` (capital), the current `contracts_ahead` with its `captured_at`, the
executor's timeout, and the book's open count against its cap. Not available: time to market
close on the order row (deferred, see Q4), the eventual outcome (never an input). The rule uses
the first set only.

## 4. Can the existing data support a frozen threshold? — **Yes, with a stated limit**

The survival table is measured (n = 25–148 per cell at the checkpoints the rule reads), coverage
is complete, and the cells the rule opens are stable across adjacent checkpoints. So a threshold
**can** be frozen from existing data, and one is (§5). The limit: it is fitted on 408 orders
across four short-lived canary tags, and the cells that qualify hold 25–42 orders each. The
shadow epoch (§6) exists to test that rule out of sample **before** it is allowed to cancel
anything. No telemetry-only epoch is needed — telemetry already accrues on every resting order
and will keep doing so under the shadow.

## 5. The frozen rule — `qac-v1-2026-09-07`

Written into `live/queue_cancel.FROZEN_RULE`, the `Settings` defaults, and the registered
Version's treatment arm; a test pins all three to one value. Cancel a resting order when **all**
of:

1. age ≥ **90 min** (`min_age_seconds = 5400`);
2. a queue observation exists, is `observed` (not missing/stale/malformed/error), and is
   ≤ **10 min** old;
3. the frozen table's P(fill before timeout | largest checkpoint reached, current depth bucket)
   ≤ **10%**, from a cell of ≥ **20** orders;
4. some timeout remains (an order past 4 h belongs to the ordinary timeout path).

With the baseline table that opens exactly: `b5k+` from 90 min (10.0%, n = 40; 5.9% at 120;
6.1% at 150; 3.8% at 180; 0% at 210) and `b0` from 180 min (4.9%, n = 41; 0% at 210). `b1_10`
never qualifies (every cell thinner than 20); `b1k_5k` at 150–210 is 5.6–10.5% but n = 17–19,
so it is *insufficient evidence*, not a cancel.

Why 10% / n ≥ 20: it is the loosest threshold at which every qualifying cell is also ≤ 10% at the
*next* checkpoint, so the decision does not depend on which side of a checkpoint an order sits.
Changing any number is a new `rule_version` and a new Version.

Never cancelled: any order whose telemetry is missing, stale, malformed or errored (each is a
named `telemetry_keep` row); any order on a tag with no active LIVE treatment arm in Experiment
OS (`refused_unregistered`); anything past the per-cycle bound (`deferred_cycle_cap`, default
10). Never cancelled-and-reposted: the executor's retry path (`_maybe_retry_live`) is unchanged
and its own drift and attempt caps still apply.

## 6. Experiment design (as registered by the package)

| | |
|---|---|
| key | `mmsell10-queue-aware-cancel` (family `maker`, origin `operator`) |
| v1 arms | `qac_control` (CONTROL, tagless): the observed book's 4 h timeout · `qac_treat` (TREATMENT, tag `qacshadow1`): the same plus the frozen rule |
| v1/e1 deployment | `mmsell10-qac-shadow-1`, kind **probe**, stage **PROBE** — the shadow |
| gates | `shadow_to_paper` (promotion PROBE→PAPER) · `shadow_kill` (kill) |
| exposure change | none; the package has no `arm` function |

**Why a probe.** The shadow reads the live book and sends nothing; it is an instrument. The
treatment arm's tag `qacshadow1` is a scope handle for the canonical evaluator (a tagless arm has
no scope); no book is configured with it and no order is placed under it. Decision rows are
stamped with this arm's lineage id, so gate metrics resolve through the deployment rather than
through any trading tag.

**Shadow mechanics.** With `LIVE_QUEUE_CANCEL_MODE=shadow` the executor, after the ordinary
timeout loop and the queue sampler, applies the rule to every resting order and writes one
`live_order_queue_decisions` row per order per cycle: tag, market, event, the queue experiment's
lineage id, submission time/price/quantity/side, partial fills before the decision, telemetry
status + queue position + contracts ahead + observation time, age and remaining timeout, whether
the book was at its cap, the decision code, whether anything was sent (never, in shadow), the
rule version and its inputs, and the reason. The order's final state is joined by
`kalshi_order_id` from `live_orders`, the canonical key.

**Live/paper parity.** Paper books have no queue and no resting orders; the vocabulary reserves
`unavailable_paper` and `simulated` so a paper value can never be mistaken for a Kalshi
observation, and the report shows telemetry coverage beside every number.

## 7. Pre-registered gates (frozen at registration; hashes on the receipt)

`shadow_to_paper` — sample floor **40 would-cancel orders**, horizon 400; pass **all** of:

| clause | threshold | why |
|---|---|---|
| `qac_telemetry_coverage_pct` | ≥ 90 | the rule must be evaluable |
| `qac_would_cancel_later_fill_pct` | ≤ 15 | selective: the orders it would cancel rarely fill anyway |
| `qac_forgone_cents_per_would_cancel` | ≤ 1.0 | cheap: settled profit of those later fills, spread over every would-cancel, ≤ 1¢ |
| `qac_would_cancel_cap_bound_pct` | ≥ 50 | it fires where a slot has value |

**Clause (d) will read 0 whatever the rule's quality**, because the observed book is not
capacity-constrained (see the correction in §3 Q5). The gate is frozen and is **not** being
re-read after seeing results — that is precisely what a pre-registration forbids.

### The shadow's first evaluable read — 2026-09-10 07:30Z

The sample floor was met (40 would-cancel orders) and the evaluator's clause values are:

| clause | threshold | observed | |
|---|---|---|---|
| `qac_telemetry_coverage_pct` | ≥ 90 | **100.0** (n=7,800) | pass |
| `qac_would_cancel_later_fill_pct` | ≤ 15 | **27.5** (n=40) | **fail** |
| `qac_forgone_cents_per_would_cancel` | ≤ 1.0 | **1.65** (n=40) | **fail** |
| `qac_would_cancel_cap_bound_pct` | ≥ 50 | **0.0** (n=40) | **fail** |

`shadow_kill` is not tripped: forgone 1.65¢ is below its 3¢ bar and coverage is 100%, so both
kill clauses read false. Verdict on both gates: **HOLD** (the promotion gate's 400-would-cancel
horizon is not yet reached).

**This falsifies more of the thesis than the capacity error alone did, and it corrects an
earlier claim in this document.** The prior revision predicted a failure on clause (d) only and
asserted the rule was "evaluable and selective". It is evaluable — telemetry is flawless — but
it is **not selective**: 27.5% of the orders the frozen rule would have cancelled went on to
fill, against a modelled fill probability of ≤10%. Nor is it cheap: 1.65¢ of realized profit
forgone per would-cancel, against a 1¢ bar. The survival table in §3 that produced the 10%
threshold does not hold out of sample on this book.

So three of the four promotion clauses fail, and only one of the three is explained by the
capacity scope error. Clause (a) transfers to a later Version; clauses (b) and (c) are evidence
*against* the rule as frozen, not merely against where it was measured. Any v2 must re-derive
the threshold — a book with a freed slot worth something does not repair a rule that cancels
orders which fill 27.5% of the time. That is a new question, so: a new Version, not an edit to
this one.

`shadow_kill` — fail **any**: forgone ≥ 3¢ per would-cancel (min evidence 40 would-cancels; the
baseline's late fills earn ~7¢ each, so catching many of them is net negative before any capacity
benefit); telemetry coverage ≤ 60% (min evidence 200 rows).

A PASS moves the experiment to PAPER, which for this experiment is **continued shadow**. It
authorizes no cancellation. A live canary is a separate Version carrying the rule verbatim, a
real risk envelope and the primary metrics the handoff names (net $/day, ¢/filled contract,
$/capital-hour, fill and partial-fill rates, entries after released capacity, daily and p5 daily
P&L, max daily loss/drawdown, cancellation and cancel-to-later-fill rates, composition of
markets entered), judged against the existing contest/event-rung/settlement-date/daily-loss
controls at equal capital — pre-registered then, not now, because its bar depends on what the
shadow measures.

## 8. Operator runbook

Nothing here expands exposure; each step is still a deliberate act.

1. **Merge** this PR (migration `a0bd7f9c48de` adds `live_order_queue_decisions`).
2. **Register** through the experiment-command transport (Task-specific / Research Lab role):
   `{"schema_version":1,"command_id":"qac-register-1","action":"REGISTER_PACKAGE","actor":"<you>","actor_role":"TASK_SPECIFIC","payload":{"package":"mmsell10-queue-aware-cancel"}}`
   and confirm the receipt: v1 frozen, `mmsell10-qac-shadow-1` open, both gate hashes present.
3. **Start the shadow** via the env channel: `LIVE_QUEUE_CANCEL_MODE=shadow` (allowlisted;
   redeploys the worker). Leave `LIVE_QUEUE_CANCEL_TAGS` empty. Verify within an hour with
   `mmsell_queue_cancel_baseline` §7 that rows land **with** a lineage id.
4. **Read** with `xos show mmsell10-queue-aware-cancel` / `xos evaluate-gates` (dry-run) and the
   Control Tower; the gates HOLD until 40 would-cancel orders exist.
5. A recorded `shadow_kill` FAIL → `RETIRE_ON_GATE_FAIL`. A recorded `shadow_to_paper` PASS
   authorizes the PAPER transition only.

Order of registration vs. shadow start matters only for stamping: rows written before
registration carry no lineage and no gate can read them (the report flags them).

### Incident — the first registration was refused by Postgres (2026-09-07)

`qac-register-20260907-1` returned **FAILED**:

```
DataError: (psycopg.errors.StringDataRightTruncation)
value too long for type character varying(16)
```

`ExperimentVersion.execution_style` is a **vocabulary** column (`maker|taker|mixed`) at
`VARCHAR(16)`; the package wrote a 54-character sentence into it. Nothing was registered —
the whole transaction rolled back, so there is no partial experiment to clean up, and the
FAILED receipt is the durable record.

**Why the tests missed it.** They run on SQLite, which ignores `VARCHAR` limits entirely;
Postgres enforces them. Every assertion was about the package's own values, and none about
those values *against the schema*. The fix is therefore two things, not one: the field now
carries `maker`, and `test_every_written_string_fits_its_declared_column_length` walks every
row the registration creates and checks each string against its declared column length —
generic, so it also covers the fields this Version does not name and the ones a later Version
adds. Reintroducing the old value makes that test fail with the production error.

Retry uses a **new command id**: receipts are exactly-once by `command_id`, so re-sending the
failed one executes nothing.

## 9. What could affect live orders

Only `LIVE_QUEUE_CANCEL_MODE=live` **and** a tag in `LIVE_QUEUE_CANCEL_TAGS` **and** an active
LIVE-kind deployment of this experiment whose treatment arm carries that tag — none of which
this PR, the package, or the shadow creates. In every mode the rule can only ever *cancel*; it
cannot place, re-price or re-size, and the 4 h timeout runs before it and independently of it.
