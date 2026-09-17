# LIQUIDITY-INCENTIVE MM — Phase 0 shadow, pre-registered (`liquidity_incentive_mm`)

**Status:** **BUILT 2026-09-16, NOT YET RUNNING.** Phase 0 instrument shipped in `WS-020`; it
starts collecting when `LIQUIDITY_INCENTIVE_SHADOW_ENABLED=true` is set on exactly one worker
(env channel). **No orders. No real money. No XOS experiment yet** — this is a research
instrument like WS-017/WS-019; a live POC would be registered as its own experiment later.

Module: `kalshi_bot/liquidity_incentive/` · tables: `incentive_*` (11) · report:
`{"type":"script","name":"liquidity_incentive_report"}` · dashboard: livedash `/incentives`
· research record: `docs/LIQUIDITY_INCENTIVE_RESEARCH.md` · workstream:
`docs/workstreams/WS-020-liquidity-incentive-shadow.md`.

## 1. The question

Can a small Kalshi account generate **≥ ~$1/day of repeatable net value** by *genuinely*
providing two-sided resting liquidity to markets in Kalshi's Liquidity Incentive Program, after
fees and after the adverse selection of being filled on one side only — at no more than
~$250–$500 of capital?

    net = liquidity rewards + paired trading P&L + single-leg P&L − fees − capital cost

This is not directional prediction and it is **not MMSELL**: separate module, tables, thread,
dashboard section and (if it ever trades) tag, experiment, exposure accounting and risk limits.
Shared infrastructure only.

## 2. Mechanism, and why it might fail

Kalshi scores YES and NO resting liquidity separately, once per second, and pays each
program's pool pro rata to score share on snapshots where *both* sides hold at least Target
Size (`LIQUIDITY_INCENTIVE_RESEARCH.md` §4). A resting YES bid at *y* and NO bid at *n* with
*y + n ≤ 100* is a matched pair if both fill (settles to $1; paired edge *100 − y − n* minus
fees) and earns score on both sides while resting.

The risk is the single leg: a passive bid is hit when someone actively takes the other side,
and this repository has already measured that mechanism on MMSELL (`docs/MMSELL_FILL_MODEL.md`:
the fills a maker wins are the losers). If single-leg fills are frequent and adverse enough,
they eat the reward. Phase 0 exists to measure exactly that, under fill assumptions reported
separately.

## 3. Guardrail (binding on every policy)

Genuine liquidity provision only. A shadow pair is placed **only at prices we would accept a
fill at**, and it ends only for a legitimate reason, each recorded as `end_reason`: the market
moved (`move_ticks`), the program ended or vanished, the market closed/deactivated, the book
went invalid, the collector stopped, both legs filled under every model, or a bounded refresh
(`refresh_max_rest`, an accounting boundary, not a cancel). No self-trading, no artificial
volume, no quoting deep below the reference to farm score. The conservative fill model
deliberately ignores cancellations ahead of us.

## 4. Frozen design (the pre-registration)

**Universe.** Every program `GET /incentive_programs?status=active` lists with
`incentive_type=liquidity` and a Target Size, up to `max_markets` (150) by period reward.
Versioned terms in `incentive_programs`; a poll is a row in `incentive_discovery_cycles`.

**Quote policies** (`quotes.py`; prices native cents; all three enforce the guardrail):

| policy | rule |
|---|---|
| **A break-even** | join both best bids; if *y + n* exceeds 100 − maker fees, step the more expensive side down until it does not. Prefers *y + n < 100*. |
| **B reward-efficient** | join both best bids up to a **declared** paired loss of `max_pair_loss_cents` (1c, on the row as `pair_edge_cents`); beyond that, fall back to A. |
| **C conservative** | rest 1 tick behind each best bid, +1 per activity step (≥10 / ≥40 trades in 5 min; ≥3c / ≥8c price range), then enforce break-even like A. |

**Capital tiers** (per program): $25, $50, $100, $250, $500. `qty per side = ⌊tier·100 / (y+n)⌋`,
capped at Target Size; unused capital is recorded (`capital_unused_usd`).

**Fill models** (`fills.py`; each leg is simulated under all three at once, one row per model):

| model | fills when |
|---|---|
| optimistic | a trade prints at or through our level on our side |
| conservative | trades at our level have consumed the depth that was ahead of us **at placement** (cancels ahead of us ignored); partials allowed |
| queue-aware | as conservative, but the contracts ahead shrink when the level's resting quantity drops below them (delta stream) |

A YES bid at yes-price *y* is hit by `taker_outcome_side=no` at yes price ≤ *y*; a NO bid at
no-price *n* by `taker_outcome_side=yes` at yes price ≥ 100 − *n*.

**Outcomes** (`incentive_shadow_outcomes`, one row per pair × model): `neither_filled`,
`yes_only`, `no_only`, `both_filled`, `partial_yes`, `partial_no`, `partial_both`; plus the
`end_reason`. **Marks** at 1 s / 5 s / 30 s / 60 s / 5 min after a leg's first fill (at the bid
and at mid; `incentive_shadow_marks`), settlement stamped when the market resolves.

**Reward accrual** (`scoring.py`, version `lip-v1-2026-09-16`): our per-side share of the field
under R1–R5 with A1–A5, accrued per requote tick while the pair rests and the snapshot
qualifies; a filled leg's accrual is prorated to its resting time at outcome. **Every reward
number is an estimate** (`est_`), never a Kalshi statement.

**Fees.** Per-market `FeeRule` from the series `fee_type`/`fee_multiplier`, stored on the
program row; maker fees charged on every simulated fill at the clip size.

**Opportunity ranking** (`economics.py`; read-only, ranking only):
`score = (est reward/day + paired value/day − single-leg cost/day − fees/day − capital cost/day) / capital`,
every component surfaced; states `IGNORE / WATCH / SHADOW / POC_CANDIDATE` with stated
thresholds (net ≤ 0 → IGNORE; side under target, field share < 2% or single-leg cost > reward →
WATCH; POC_CANDIDATE needs net ≥ $0.25/day **and ≥ 50 conservative outcomes**). No state submits.

## 5. Research questions and where each is answered

| Q | question | instrument |
|---|---|---|
| Q1 | are meaningful pools consistently available? | `incentive_discovery_cycles`, `build_history.by_day` (programs/day, pool/day, medians) |
| Q2 | where is competition weak enough for a small account? | `est_yes_share`/`est_no_share` on every quote; ranking's `reward_per_capital_dollar_per_day` |
| Q3 | does two-sided quoting beat one-sided? | `est_reward_yes_usd` vs `est_reward_no_usd` per outcome; one-sided reward is the same model with one leg (test `test_one_sided_quote_earns_only_that_side`) |
| Q4 | how often do both sides fill? | `both_given_one` per model; `seconds_between_legs` |
| Q5 | how bad are single-leg fills? | marks by horizon (mean / worst at bid), `single_leg_max_adverse_usd`, settlement P&L |
| Q6 | are under-competed markets more toxic? | ops report §4: single-leg MTM by depth bucket (under / medium / deep vs Target Size) |
| Q7 | which capital tier is efficient? | headline table: net/day, net per capital-hour, `capital_unused_usd` per tier |

## 6. Phase 0 success criteria and the pre-registered gate for a live POC

Observation window: **≥ 14 days**, spanning at least two weekends (sports and macro programs
differ), before any read is treated as evidence. Do not promote merely because gross estimated
rewards exceed $1/day.

A recommendation to run a tiny live POC requires **all** of, under the **conservative** fill
model, at one tier ≤ $500:

1. ≥ 14 observation days and ≥ 2 distinct qualifying programs contributing;
2. net after settlement > 0 (reward + paired + single-leg + settlement − fees);
3. projected net ≥ $1/day at that tier (net/day from the headline table, span ≥ 14 d);
4. no single market contributing > 50% of net (`share_of_net_from_largest` ≤ 0.5);
5. single-leg drawdown bounded: worst 5-minute mark at bid ≥ −(tier × 10%) and settlement P&L
   on single legs not below −(tier × 20%) cumulatively;
6. P(both | one) ≥ 0.25 (otherwise the "pair" premise is wrong and this is a one-sided book);
7. no unresolved data-quality issue: collector alive ≥ 95% of the window, `seq_gap` and
   `throttled` events explainable, discovery errors 0 on ≥ 95% of polls, and the `period_reward`
   unit confirmed against the public incentives page.

If any fails: **HOLD.** The criteria are not retuned after seeing results; a changed criterion
is a new version of this document with the old one kept.

## 7. Phase 1 (NOT implemented; requires explicit operator authorization)

A tiny live POC, if approved after the Phase 0 review, would be a **new XOS experiment** armed
only through `arm_live_canary` (hard stop): $50–$100 max exposure, 5–10 contracts per side,
2–5 selected programs, post-only maker orders, per-market and global caps, no leverage, no
interaction with MMSELL positions. Its questions are reconciliation questions — does Kalshi
credit our orders and pay what the model estimated, does queue behaviour match the shadow's
"contracts ahead", what is the actual single-leg P&L — normalised to reward and net per
capital-hour, never "did $100 earn $1/day".

## 8. Operating the instrument

- Enable on **one** worker: `{"type":"env","action":"set","service":"<worker>","values":{"LIQUIDITY_INCENTIVE_SHADOW_ENABLED":"true"},"id":"limm-on-1"}` (redeploys that worker). Two workers would double-write the tape.
- Read: ops script above (§ COLLECTOR first — nothing else is trustworthy until it is alive and the tape is landing), or livedash `/incentives`.
- Kill: the same variable to `false`. Nothing else changes; the trading path never reads these tables.
- Budget: ≤150 markets × (`orderbook_delta` + `trade`) on one socket, raw rows capped at 3,000/min, discovery every 5 min (one paged GET + one `GET /markets/{t}` and one `GET /series/{s}` per *new* terms row), settlement pass every 10 min over closed markets only.

## 9. Results

Written from the ops reports as the run proceeds. This is the only part of this document that
changes after the run starts; §4–§6 stay as frozen on 2026-09-16.

### 9.1 Day 0 — the instrument is running (2026-09-17)

`LIQUIDITY_INCENTIVE_SHADOW_ENABLED=true` set on the **evo** service at 12:24:54Z
(ops `limm-on-1`, VERIFIED, redeploy triggered). First report: ops `limm-report-1`, 12:30:02Z,
code `dfe40b9d`. **Nothing below is evidence about the thesis** — no pair has ended, so there
are no outcomes, no fills and no marks. It is a coverage and sanity read only.

**The instrument is alive.** Collector events: `thread_started` 2, `connected` 1,
`subscribed` 3, `discovery` 1, `market_cap_reached` 1; zero errors, zero sequence gaps, zero
throttles. One discovery cycle at 12:25Z: **3,939 programs listed (3,938 liquidity, 1 volume),
0 errors**, total advertised period reward $523,781.67. 79 markets snapshotted, 1,155 open
shadow pairs (= 77 markets × 3 policies × 5 tiers).

**Day-one check 1 (the `period_reward` unit) PASSES.** Median program pays **$17.36/day**,
the largest **$500.00/day**, board-wide **$208,292.84/day**. Kalshi's own product copy says
liquidity pools are "$10–$1,000 per market" per day. The centi-cents reading
(`period_reward / 10,000` → USD) is therefore confirmed at both ends of the range; a
wrong unit would have been off by 100× or 10,000×.

**Day-one check 2 (program-term distributions) matches the external prior.** Median Target
Size **1,000** contracts and Discount Factor **5,000 bps = 0.50** across the board — the
same modes the `quantfirm` scan recorded independently (`LIQUIDITY_INCENTIVE_RESEARCH.md` §2).

**Two findings that are already actionable, both recorded rather than acted on:**

1. **The highest-reward programs are dead markets.** Across the tracked set (the 150
   highest-reward programs, of which ~77 are quoting), the tape recorded **184 book events
   and ZERO public trades in two hours**. The tickers the reward ranking selects —
   `KXBWAYATTENDANCE`, `KXCAFAIRPLAN`, `KXNYCASKRENT`, `KXILNUCLEAR`, `KXCABUILDPERMITS` —
   are long-dated, enormously deep (10k–180k contracts resting per side) and untraded.
   This cuts both ways and the shadow exists to measure which way: no trades means no fills,
   so no single-leg adverse selection (Q5) and no paired fills (Q4) — but also no evidence
   on the risk half of the thesis, because **selecting on reward selects away from the risk
   we came to measure**. `LIQUIDITY_INCENTIVE_MAX_MARKETS` and `_MIN_REWARD_USD` are the
   ops-settable knobs that could widen the tracked set toward traded markets; the quote
   policies and capital tiers are the pre-registration and are not touched.
2. **The top-ranked reward estimates are implausibly high and are NOT to be believed yet.**
   The best-ranked programs estimate ~$3.00/day of reward on $99.99 of committed capital —
   ~3%/day, against a board-wide rate on resting capital that the external scan measured at
   ~0.62%/day. Part of that gap is legitimate selection (the ranking picks the best of 3,938).
   Part may be assumption **A4**: our hypothetical size is added to the field, and with the
   Reference Price sitting at the touch, deep resting orders are discounted to near nothing
   by `0.50^ticks`, so the effective field score is far smaller than the displayed depth and
   our share (0.6%–8.5%) is correspondingly large. **Day-one check 3 is the discriminator and
   needs the operator:** compare our `est_reward_per_hour` for one named market against the
   projected-reward figure Kalshi shows a signed-in user on that same market. Until that
   check runs, every `est_` reward number here is an unvalidated model output.

No promotion criterion in §6 is engaged by any of this: the observation window has not
started accumulating outcomes, and the conservative fill model has produced nothing.

### 9.2 Hour 1.5 — the first fills, and P(both | one) = 0.000 (2026-09-17 13:49Z)

Ops `limm-report-2`, code `7a5f1e6d`, 3h event window / 1d aggregate window. **Observation
span 0.06 days against a pre-registered window of ≥ 14. Nothing here is a verdict, and §6
forbids reading one; the criteria are not retuned after seeing results.** What follows is
recorded because it is the first evidence of the kind the thesis is about, and because it
points hard enough that the direction should be on the record before it is confirmed or
overturned.

**The instrument is healthy and the tape is growing.** Last event 13:48:36Z. Discovery at
13:48Z: 3,959 listed, 3,958 liquidity, **0 errors**, pool $524,941.67, 12 new terms, 11 gone.
Tape over 3h: 5,307 book events, 5,469 open shadow pairs, 167 markets snapshotted. Four
sequence gaps and two disconnects were recorded and recovered (4 `snapshot_requested`); 77
`unsubscribed` and 19 `market_cap_reached` are the tracked set churning against the 150-market
bound as programmes are added and dropped, not a fault.

**Day-0 finding 1 is confirmed and has not improved: 9 public trades in three hours** across
167 tracked markets. The reward ranking still selects untraded books.

**Day-0 finding 2 (the share model) is now visible in the ranking and still unvalidated.** The
top entries are `KXMLBSEASONGAMES` at $500/day advertised, where our estimated share of the
pool is **0.5–0.6%** against fields of 27k–60k resting contracts per side — which is why they
read `WATCH(share)` rather than `SHADOW`. Day-one check 3 (comparing one market's displayed
projected reward against ours, signed in) has still not been run and remains the only external
calibration of these numbers.

**The first outcomes have ended, and every one of them was one-sided.**

| model | mix | P(both \| one) | n |
|---|---|---|---|
| conservative | 1,836 neither · 8 yes_only · 7 partial_yes | **0.000** | 15 |
| queue_aware | 1,836 neither · 13 yes_only · 2 partial_yes | **0.000** | 15 |
| optimistic | 1,826 neither · 15 yes_only · 10 no_only | **0.000** | 25 |

§6 criterion 6 requires **P(both | one) ≥ 0.25**, on the stated ground that below it "the
'pair' premise is wrong and this is a one-sided book". At n=15 the observed value is zero:
not one pair completed. The honest reading at this sample is HOLD, exactly as §6 says — but
it is worth naming what a hold on *this* number would mean if it survives the window, because
it is the criterion most likely to decide the thesis.

**Single-leg adverse selection is large relative to the reward, on the same tiny sample.**
Conservative single legs: mean mark-to-bid **−$1.96**, worst **−$2.89**, against a mean
estimated reward of **$0.0121** on those same legs — roughly 160× the reward. Every single-leg
outcome so far sat behind DEEP competing size at placement. The headline table is negative at
every policy and tier under every fill model, with one exception that is sampling noise at this
span (C_conservative at $500, conservative model, +$0.10).

**What this does and does not say about Phase 1a.** It does not authorize or forbid anything:
§10's gates read instrument health, not economics, and are untouched by this. But it is worth
stating plainly that the one-sided smoke test is, on this evidence, measuring the case that
actually occurs — no pair has yet completed — and that a live one-contract bid would be
exposed to the single-leg side of this, bounded by its 25c price cap.

## 10. Phase 1a — the ONE-SIDED live smoke test (separate from §6, and much smaller)

**§6 is frozen and is not what this section gates on.** §6 asks whether quoting incentivized
markets can earn $1/day net at $100–$500. §10 asks whether one $1 resting bid survives our own
plumbing. They are different questions with different bars, and a result under one is never
evidence for the other. Nothing here retunes §6.

Authorized by the operator on 2026-09-17: *"keep the trading below 10 dollars and only 3
trades max at a time per test with a limit of one dollar per trade."* Those numbers are taken
verbatim as the risk envelope, plus one cap the operator did not ask for and this section adds:
a **25c price ceiling**. The reason is that a per-*order* dollar limit bounds the order while
the price bounds the **loss** — at one contract the entire downside of a resting bid is the
price paid — so 25c makes a clip's worst case a quarter rather than a dollar.

### 10.1 Why it is one-sided

Two-sided quoting is the strategy; it is not what this test exercises. The live path refuses a
second resting order on a ticker that already carries one (`LiveExecutor` gate `dedup`, via
`live_buy_exists_for_ticker` / `live_open_order_exists`), and that gate is **strategy-agnostic
on purpose** — it is also what keeps this book from ever contesting a market the running MMSELL
canary is resting in. Teaching it to understand a two-sided quote changes shared risk semantics
that guard real money elsewhere: a Platform Change Review, not something to slip into a smoke
test.

Kalshi scores the YES and NO sides **separately** (§4, R5), so a single resting bid still earns
liquidity score on its own side. That is enough to prove the pipe end to end. It is **not**
enough to say anything about the strategy's economics, and at $1 it cannot be: the reward on
one contract is cents a day against the ~$3/day the §6 bar was written for.

### 10.2 What is placed, and how the side is chosen

One post-only bid, one contract, at the **cheaper side's touch price**. Cheapest-first is the
safety lever (the downside is the price paid), and resting *at* the touch also sits at or above
the Reference Price, so the distance multiplier is 1.0 — the cheap side is also the efficient
side. We never rest deep for safety: a deep order is discounted to nothing by
`DiscountFactor ** ticks` and would be liquidity nobody is paying for.

A market qualifies only if both sides already meet Target Size (R2 — otherwise no snapshot pays
anyone, and we are far too small to carry a side over the line), the book is two-sided and
uncrossed, the programme has ≥ 2h left, and the cheaper touch is ≤ 25c.

### 10.3 The envelope

| Cap | Value | Enforced by |
|---|---|---|
| contracts per order | 1 | `live.build_live_quote`, re-asserted at `gate:size` |
| dollars per order | $1.00 | same |
| price per contract | ≤ 25c | same |
| resting orders at once | 3 | `repo.count_live_book_open` (`gate:open_cap`) |
| book exposure | $10.00 | `repo.live_strategy_exposure` (`gate:strategy_exposure`) |
| per-market exposure | shared `MAX_MARKET_EXPOSURE` | `gate:exposure` |
| daily realized loss | shared $5.00 | `gate:daily_loss` |
| order lifetime | `LIVE_ORDER_TIMEOUT_SECONDS` | `reconcile` timeout-cancel |
| exits | none — hold to settlement | `manage_exits` skips this book's tags |

Every cap is a **module constant** in `liquidity_incentive/live.py`, not a runtime setting.
That is stronger than the config-drift detector gives a setting — a constant cannot change
without a pull request and a redeploy — but it also means the detector has nothing to compare,
so the deployment's `book_params` is registered as `None` rather than as a spec the runtime
cannot produce. `tests/test_liquidity_incentive_xos_package.py` asserts the registered envelope
equals the running constants.

**Hold to settlement is not a setting either.** Production runs `LIVE_EXIT_MODE=tp_sl` for the
YES/weather books, and a filled YES incentive bid is a net-long YES position, which
`open_live_positions` returns. Without an explicit skip, `manage_exits` would place exit orders
this envelope never declared. `LiveExecutor.manage_exits` therefore skips `limm.owns_tag`
strategies — an exact match on the two registered tags, never a prefix test.

### 10.4 Genuine liquidity

There is **no cancel branch in this book.** Orders leave the book by a fill, the shared
per-order timeout, or `drain_stood_down_books` when the allowlist drops the tag.
`tests/test_liquidity_incentive_runner.py::test_the_runner_has_no_cancel_path` asserts it.
No self-trading, no volume generation, no reward-metric manipulation: the book places one
resting bid and honours it.

### 10.5 The gates (pre-registered; separate from §6)

Registered by the Experiment OS package `liquidity-incentive-mm`, on experiment
`liquidity-incentive-mm` v1. All three freeze with the version.

- **`shadow_instrument_ready`** (PROBE→PAPER). Instrument health only: discovery is running
  and current, and the universe is real. HOLD below 12 discovery polls or 50 shadow quotes;
  FAIL above 75% poll errors; PASS at ≤ 25% poll errors and ≥ 10 programmes observed.
- **`paper_to_live_smoke`** (PAPER→LIVE_CANARY). The same health question over a thicker
  sample, because this is the act that spends money: HOLD below 200 quotes, 50 completed
  outcomes or 20 programmes; FAIL above 50% poll errors; PASS at ≤ 10%.
  It contains **no profitability clause, deliberately** — at $10 a P&L bar would be theatre.
- **`live_canary_keep`** (kill). Sample 3 settled contracts, horizon 25. FAIL at −$5.00
  realized (half the book budget) or any single settled market losing more than $1.00 (an
  envelope violation, not a market move). PASS means *the plumbing ran and the envelope held* —
  it authorizes nothing, and there is no promotion path out of this book.

Evidence clocks start at **registration**, not at the collector's first row. This session had
already read the day-0 shadow output, and a bar judged over a window whose data was already
seen is not a pre-registration. The cost is a day of waiting; that day is the whole value of
the gate.

### 10.6 Operator arming sequence

Four steps, each its own act. Steps 2 and 4 are hard stops requiring operator approval.

1. **Register the contract** (arms nothing, trades nothing, opens no exposure):
   `EXPERIMENT_OS_EXPERIMENT_COMMAND` = `[{"action":"REGISTER_PACKAGE","package":"liquidity-incentive-mm","actor":"<you>"}]`
   Leaves the experiment at PROBE with a tagless probe deployment.
2. **Wait** for the probe gate's evidence, then **arm** (HARD STOP — expands real-money
   capability): `[{"action":"ARM_CANARY","package":"liquidity-incentive-mm","approved_by":"<person>"}]`
   This re-evaluates `shadow_instrument_ready` and refuses anything but PASS, walks PROBE→PAPER
   on that result, then calls `arm_live_canary`, which re-evaluates `paper_to_live_smoke`
   itself. Two independent fresh PASSes stand between the envelope and an order. It still
   places nothing.
3. **Turn the runner on** (still places nothing — the allowlist is step 4):
   `{"type":"env","action":"set","service":"live","values":{"LIQUIDITY_INCENTIVE_LIVE_ENABLED":"true"},"id":"limm-live-on-1"}`
4. **Open the allowlist** (HARD STOP — this is the step at which an order can reach Kalshi).
   Set the variables `liquidity_incentive_mm.activation_env()` returns, `LIVE_STRATEGIES` last.
   Note `LIVE_STRATEGIES` matches by **prefix** and is currently `Fmmsell10`; the new value
   must name **both** books or the running canary stands down.

**What step 2 does to the shadow probe.** It ends it. `arm_live_canary` carries the epoch's
open deployments across the live boundary and `carry_deployments_forward` admits `paper` kinds
only, so an open PROBE deployment refuses the whole arming — observed in production on
2026-09-17 (`limm-arm-1`, REJECTED, rolled back cleanly with no live lineage created). The
engine is right to refuse: a probe is a validation instrument belonging to the PROBE stage, and
carrying one into a live epoch would claim the shadow collector is part of the live lineage. So
the package ends it at the moment its stage ends. The shadow collector itself keeps running —
it is a daemon thread governed by `LIQUIDITY_INCENTIVE_SHADOW_ENABLED`, not by this deployment
row, and the `incentive_*` metrics read its tables over a time window rather than by tag.

**Stand-down:** remove `Alimm1` from `LIVE_STRATEGIES`. New entries stop on the next cycle,
resting orders drain within a cycle, and any held contract settles normally — at most $10 in
total, and at these caps at most $0.25 per market.
