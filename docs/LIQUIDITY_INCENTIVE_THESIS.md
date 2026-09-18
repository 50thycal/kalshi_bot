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

### 9.3 The live smoke test placed (2026-09-17 16:00:47Z) — the pipe works

Operator signed off on the live test at ~15:20Z. Armed and activated the same hour; the first
real orders rested three minutes after the worker redeployed.

| ticker | side | price | qty | status | Kalshi order id |
|---|---|---|---|---|---|
| `KXHORMUZPEAK-26SEP20-T20` | yes | 3c | 1 | resting | `01a0b019-5b98-7a05-9e06-7f5490187b56` |
| `KXHORMUZPEAK-26SEP20-T30` | yes | 1c | 1 | resting | `01a0b019-5b98-7e81-93a0-41455ba9889a` |
| `KXHORMUZPEAK-26SEP20-T25` | yes | 1c | 1 | resting | `01a0b019-57b0-75f1-9044-6465d0d66290` |

**Total real money committed: $0.05.** Every declared cap held, verified against the database
rather than inferred: 1 contract per order, every price far under the 25c ceiling, exactly 3
open orders at `MAX_OPEN_ORDERS`, $0.05 against the $10 book ceiling, and **0 orders from any
other book on any of these tickers** — the strategy-agnostic dedup gate did its job, so the
MMSELL canary was never contested.

**The twin mirrored all three within ~43ms**, same ticker, side, price and size
(`Alimm1_pt3`). The one-to-one property the live/paper comparison rests on holds by
construction, not by reconciliation.

**What this establishes:** the decision layer selects a market, the executor's nine gates
admit it, a post-only order reaches Kalshi and rests, the twin records its counterfactual, and
the whole path stays inside a pre-registered envelope. That is the entire claim of §10 and it
is now evidenced. **It establishes nothing about the economics** — see §9.2, and see the
per-clip arithmetic: 5c of resting size cannot earn a measurable liquidity reward.

**Two honest observations, recorded rather than acted on:**

1. **All three orders landed on ONE event** (`KXHORMUZPEAK-26SEP20`) at three strikes. The
   envelope bounds contracts, price, order count and book exposure — it has **no event-level
   concentration cap**, where mmsell carries `max_event_rungs=3`. At $0.05 this is immaterial
   and the $10 ceiling bounds it absolutely, but it is a real property: this book will put its
   entire order budget on correlated strikes of a single event if that is where the cheapest
   touches are. A future version that sizes up needs an event cap; this one does not.
2. **The cheapest-touch rule selected deep out-of-the-money tails** (1c and 3c YES). That is
   the rule working as designed — cheapest touch is the safety lever — but it means the test
   is resting where fills are least likely, which is the conservative end of the very
   selection effect §9.1 recorded.

**Twin-suffix mismatch, found during activation and pinned.** `activation_env()` derives
`LIVE_PAPER_TWIN_SUFFIX` from this book's `TWIN_SUFFIX` (`_pt3`), but production carries
`_pt4` and that variable is **SHARED** — applying it would have re-cut `Fmmsell10`'s twin to an
unregistered tag and taken the running canary's twin dark under NEW_ONLY. It was not applied.
The twin is pinned instead with an explicit `LIVE_PAPER_TWINS=Alimm1:Alimm1_pt3`, which
overrides the suffix for this book alone and leaves every other book on `_pt4`.

### 9.4 Hour 4.5 — the headline turns positive and P(both | one) is still zero (2026-09-17 16:54Z)

Ops `limm-report-3`, 3h event window / 1d aggregate. **Observation span 0.18 days against a
pre-registered window of ≥ 14. Still HOLD; §6 is not retuned.** Two things moved, in opposite
directions, and the tension between them is the finding.

**1. The pair premise looks worse, on a 10x bigger sample.**

| model | one-sided fills | both filled | P(both \| one) |
|---|---|---|---|
| optimistic | 180 no_only + 85 yes_only = **265** | **0** | 0.000 |
| queue_aware | 50 | **0** | 0.000 |
| conservative | 35 | **0** | 0.000 |

§9.2 recorded this at n=25 (optimistic) and called it thin. It is now **n=265 under the model
most generous to the strategy**, and not one pair has completed in any model. §6 criterion 6
requires ≥ 0.25.

The nominal arithmetic against a 0.25 bar is overwhelming at this n, and it should not be
quoted that way: these outcomes are **not independent**. They are repeated quotes on a small
set of markets with overlapping lifetimes, so the effective sample is materially smaller than
265. What is fair to say is that the direction has survived a tenfold increase in sample and is
identical across three fill models — which is a good deal more than §9.2 could claim.

**2. The headline flipped positive — and every measured component of it is still zero or
negative.**

| policy A, conservative | reward (DERIVED) | paired | fees | single-leg MTM | net | net/day |
|---|---|---|---|---|---|---|
| $25 | 2.76 | 0.00 | 0.00 | −1.50 | +1.26 | +6.86 |
| $100 | 11.02 | 0.00 | 0.00 | −6.04 | +4.98 | +27.00 |
| $500 | 51.51 | 0.00 | 0.00 | −9.89 | +41.62 | +225.87 |

Read the columns before the total. `paired` is 0.0000 **because there are no pairs** — that is
the same fact as the table above, not an independent success. `fees` is 0.0000 for the same
reason. `settle` is n/a. So **100% of the positive net is `est_reward`**, which is the output
of the share model that §9.1 flagged as unvalidated and that day-one check 3 — comparing one
market's displayed projected reward against ours, signed in — has still not tested.

$225/day at the $500 tier is not a result; it is an estimate implying a return the board-wide
pool arithmetic does not support (§9.1: ~3%/day implied against ~0.62%/day available). §6
anticipated exactly this and says so: *"Do not promote merely because gross estimated rewards
exceed $1/day."*

**3. Single-leg tails deepened.** Conservative worst mark-to-bid at the 1s horizon is now
**−$20.00** (was −$2.89); optimistic worst is **−$50.50**. Both recover substantially by the
30s horizon (−$2.89 and −$15.15), which is consistent with these being marks taken into a
momentarily empty book rather than realised losses — but the instantaneous tail is real and it
is what a resting order would face.

**4. Collector.** Healthy but working harder: 38 discovery cycles with **0 errors**, 4,250
programmes, pool $553,931.67, 177 markets snapshotted, 18,317 book events. 17 sequence gaps and
3 disconnects were recorded and recovered (5 snapshot re-requests). Public trades on tracked
markets: **47 in three hours** — up from 9, still almost nothing across 177 markets, so §9.1's
finding that the reward ranking selects untraded books stands.

**What this means for the live smoke test (§9.3):** nothing changes. Its gates read instrument
health, not economics. But it reinforces what that test is measuring — a one-sided resting bid
is not a degraded version of the strategy, it is empirically the only version that has ever
occurred.

### 9.5 Hour 7.5 — the first completed pairs, and why they do not rescue the premise (2026-09-17 19:58Z)

Ops `limm-report-4`, span **0.31 days** against ≥ 14. Still HOLD. §9.2 and §9.4 both recorded
`P(both | one) = 0.000`; it has now moved, and the shape of the move is the finding.

| model | one-sided | both filled | P(both \| one) | n | lag between legs |
|---|---|---|---|---|---|
| optimistic | 495 | **30** | **0.057** | 525 | mean 887s, median **1,005s** |
| queue_aware | 75 | 0 | 0.000 | 75 | — |
| conservative | 45 | 0 | 0.000 | 45 | — |

**Three things have to be said together or the first one misleads.**

1. **Pairs complete only under the OPTIMISTIC model.** That is the model which assumes an order
   at a touched price fills — the one §4 registered precisely because it cannot be trusted, and
   the one §6 does **not** gate on. Under the conservative model, which §6 does gate on, the
   count is still exactly **zero** at n=45. Under queue-aware, zero at n=75.
2. **0.057 is not 0.25.** Even taking the optimistic model at face value, the observed rate is
   less than a quarter of the pre-registered bar.
3. **The lag is the real news, and it points the same way as the zero.** When an optimistic
   pair does complete, its two legs are **about 17 minutes apart** (median 1,005s). A quote
   whose second side fills a quarter of an hour after the first is not a two-sided market-making
   pair in any sense the thesis meant — it is two independent fills with a long, fully
   one-sided, adversely-selectable interval between them. §2's mechanism assumed the pair is
   what bounds the risk. At a 17-minute median lag it does not.

**The headline keeps climbing and is still entirely derived.** Policy A conservative: $25 tier
net/day **+$11.86**, $500 tier **+$279.53**. `paired` is 0.0000, `fees` 0.0000, `settle` n/a,
single-leg MTM negative and clamped at −$10.00 at the two largest tiers. So, exactly as in
§9.4, **100% of the positive net is `est_reward`** from the unvalidated share model. Day-one
check 3 remains unrun and remains the only external test of it.

**Collector: healthy, but the sequence-gap rate is rising and should be watched.** Gaps by
report: 4 (§9.2) → 17 (§9.4) → **28** now, each recovered by a snapshot re-request (6 so far),
with the most recent event at 19:55Z being a gap. Discovery is clean — 36 cycles, **0 errors**,
4,331 programmes, pool $559,466.67. Trades 66 in three hours across 176 markets, up from 47,
still almost nothing. A rising gap rate does not invalidate anything yet, but the tape is what
the fill models replay, so it degrades evidence quality quietly rather than loudly. If it keeps
climbing, it becomes a data-quality item under §6 criterion 7.

**Net effect on the thesis: unchanged, and slightly worse.** The one metric that moved moved in
the model that does not count, to a value still well under the bar, and brought with it a lag
that undermines the mechanism the pair was supposed to provide.

### 9.6 The lifecycle closed — timeout, cancel, re-quote (2026-09-17 20:08Z)

§9.3 recorded placement and could only *claim* the rest. Ops `limm-watch-2` closes it.

All three original bids left the book at the 4-hour boundary exactly as designed —
`status=canceled`, `cancel_reason=timeout`, ~20:00Z against a 16:00:47Z placement and
`LIVE_ORDER_TIMEOUT_SECONDS=14400`. **This book has no cancel branch of its own**; orders leave
by a fill, the shared per-order timeout, or a stand-down drain, and the timeout is what fired.
That is the genuine-liquidity guarantee of §10.4 observed rather than asserted.

Four minutes later the runner re-quoted, on **different markets**:

| ticker | side | price | qty |
|---|---|---|---|
| `KXYTVIEWSHIGH-POS26OCT-8.75M` | yes | 4c | 1 |
| `KXBIGGESTQUAKE-17SEP26-7.0` | yes | 1c | 1 |
| `KXBIGGESTQUAKE-17SEP26-6.8` | yes | 1c | 1 |

$0.06 committed, 3 open at the cap, every price far under 25c, 0 orders from any other book on
any of these tickers. **Place → rest → expire → re-place is now demonstrated end to end**, which
is the whole of §10's claim.

Two things the re-quote confirms rather than reveals:

1. **Selection is dynamic, not stuck.** A completely different market set (video views,
   earthquake magnitude) replaced the Hormuz strikes, so the ranking is re-running against the
   live programme list rather than latching.
2. **The event-concentration pattern from §9.3 repeats**: two of the three new orders sit on
   one event (`KXBIGGESTQUAKE-17SEP26` at 6.8 and 7.0). That is now a pattern rather than an
   incident. Still immaterial at $0.06 and still bounded absolutely by the $10 ceiling, and
   still **not** acted on — but it is the second observation of the same thing, and any sized-up
   version of this book needs an event cap before it runs.

### 9.7 Hour 12 — a two-sided fill outside the optimistic model, at a lag that would bound risk (2026-09-18 00:00Z)

> **QUALIFIED BY §9.11.** The 16.7s lag below rests on **n=1**. The next observation was
> 1102.8s. Do not carry the "would bound risk" reading forward; the lag is not yet estimable.

Ops `limm-report-5`, span **0.48 days** against ≥ 14. Still HOLD. Two of the things §9.5's
follow-up named as material have happened, and one of them shows a limitation in the
pre-registered metric that a reader must be told about.

| model | outcome mix (non-empty) | both_filled | P(both \| one) | n | lag |
|---|---|---|---|---|---|
| optimistic | 555 no_only, 300 yes_only | **80** | 0.086 | 935 | median **435s** |
| queue_aware | 109 no_only, 31 yes_only, 66 partial_no, 29 partial_yes, **10 partial_both** | 0 | **0.000** | 245 | **16.7s** |
| conservative | 54 no_only, 14 yes_only, 66 partial_no, 21 partial_yes | 0 | **0.000** | 155 | — |

**1. `P(both | one)` is still 0.000 in both gated models — and that number is now incomplete.**
The queue-aware model recorded **10 `partial_both` outcomes**: quotes where *both* legs took
some fill. `P(both | one)` counts only `both_filled` (both legs FULL), so a partially-completed
pair scores as though no pair happened at all. Reporting the 0.000 without this would be
technically true and misleading.

**This is not a reason to change the metric.** §6 criterion 6 is pre-registered on
`P(both | one)` and is not being retuned after seeing results — that rule exists for exactly
this moment, when a definition starts to look inconvenient. What is recorded instead is that
the bar, as written, will read 0.000 through any amount of partial two-sided filling, and any
future version of this document that wants to count partial pairs must say so **before** it
looks.

**2. The queue-aware lag is 16.7 seconds.** §9.5's follow-up named "the pair lag dropping to
something that would actually bound risk (tens of seconds, not ~17 minutes)" as a material
change. Under the most realistic of the three models it is now 16.7s, against the optimistic
model's 435s median. At that timescale the pair mechanism of §2 would genuinely bound the
one-sided exposure — which is the first evidence in this run that the premise is mechanically
possible rather than merely hoped for.

Held against it: 10 observations, none of them a *full* pair, on a 0.48-day span. This is the
weakest kind of positive signal and is recorded as a direction to watch, not a finding.

**3. The optimistic model's numbers moved but say no more than before.** P rose 0.057 → 0.086
(80 pairs / n=935) and its lag halved to a 435s median. It remains the model §4 registered
because it cannot be trusted and §6 does not gate on.

**4. The sequence-gap rise stopped.** 4 → 17 → 28 → **28**, with 3 snapshot re-requests. The
trend §9.5 flagged has plateaued; the tape is intact. Discovery clean: 36 cycles, **0 errors**,
4,449 programmes, pool $568,056.67. Trades **98** in three hours across 176 markets, up from 66
— rising, still thin.

**Where this leaves the thesis.** Marginally better than §9.5 and still nowhere near the bar.
The gated metric is zero, the sample is half a day against fourteen, the entire positive
headline is still `est_reward` from an unvalidated model, and the one encouraging number rests
on ten partial fills.

### 9.8 The first real fills — and a defect they exposed in the twin (2026-09-18 00:26Z)

§9.3 proved placement, §9.6 proved the expiry loop. The last unproven leg was a fill, and it
has happened twice.

| ticker | side | price | qty | status |
|---|---|---|---|---|
| `KXBIGGESTQUAKE-17SEP26-7.0` | yes | 1c | 1 | **filled** |
| `KXBIGGESTQUAKE-17SEP26-6.8` | yes | 1c | 1 | **filled** |
| `KXYTVIEWSHIGH-POS26OCT-8.75M` | yes | 4c | 1 | canceled (timeout) |
| `KXAAAGASDAZ-26SEP18-4.6800` | yes | 1c | 1 | resting (00:08:31Z) |

**We were the maker.** Post-only bids at 1c on deep-tail earthquake-magnitude markets; a
counterparty sold YES into them. That is the adverse-selection event this whole thesis is
about, occurring on real money for the first time. **At risk: 2c.** Kalshi's own position
snapshots confirm 1 contract in each at an average price of 1c, re-read every ~3 minutes by
reconcile, realized P&L 0.0000 (unsettled).

**A cap behaved in a way worth writing down.** Only ONE new order replaced the three, not
three: `MAX_OPEN_ORDERS` counts filled positions as open exposure, so two fills plus one
resting order is already at the cap of 3. The book cannot accumulate a fourth unit of exposure
while holding two. Committed: $0.03.

**And the fills exposed a real defect — in the twin, not the live path.**

The twin mirrored all seven orders faithfully. But the mirrors of the two orders that FILLED
are marked **`abandoned`**, while the live book still holds both positions:

| batch | live | twin |
|---|---|---|
| 16:00Z (3 orders) | canceled/timeout | `closed_timeout`, −$0.0102 to −$0.0105 (fees) |
| 20:04Z (3 orders) | 2 **filled**, 1 timeout | **`abandoned`** |

`repository.abandon_open_paper_trades` runs on every live worker start and closes open paper
trades whose strategy is not in `keep_prefixes`. In live mode those prefixes are FAMILY names
(`weather`, `mmsell`, `theta`, …). A twin tag carries its parent's generation letter —
`Alimm1_pt3` — so it matches none of them. mmsell's twins survive only through the `"mmsell"`
SUBSTRING special case in `strategy_is_kept`, which is an accident of that family's naming
rather than a rule, so **every non-mmsell book's twin was exposed**.

This is the Wmmsell6 failure of 2026-08-04 — documented in that same function's docstring — on
a new book. Its consequence here is precise and serious: `arm_live_canary` requires a paper
twin because the twin is the comparison instrument, and the twin's record of the only two
positions that matter had been wiped while live still held them.

**Fixed** by keeping every configured live/paper twin tag by exact tag, not family prefix
(`repository.keep_with_configured_twins`), which protects every book's twin rather than only
this one. The already-abandoned rows are historical and are not rewritten: the two live
positions from 20:04Z have no twin counterpart and that gap is part of the record.

**What this does and does not establish.** The pipe is now proven end to end: select, place,
rest, fill, hold, and expire. It establishes nothing economic — 2c of deep-tail YES is not a
test of the reward model, and §9.7's reading is unchanged.

### 9.9 The canary stopped quoting — two correct rules that compose into a filter admitting nothing (2026-09-18 05:04Z)

> **CORRECTED BY §9.10.** The mechanism below is real and the 05:04Z snapshot is accurate, but
> this entry's central claim — that the starvation is *deterministic* and the book "will never
> quote again" — is **false**. It quoted at 06:00:04Z and filled. The entry is kept unedited
> because the record of a wrong call is worth more than a tidy one; read §9.10 for what
> actually holds.

The book has not placed an order since **00:08:31Z**. It is not broken in any way that shows:
the worker cycles every ~2.5 minutes and logged 43 consecutive `incentive smoke cycle` lines
between 03:07Z and 05:00Z; `Fmmsell10` placed normally throughout (04:42:55Z, resting); the
caps held; nothing was rejected and no auth error occurred. The last order,
`KXAAAGASDAZ-26SEP18-4.6800`, left the book at ~04:00Z after 2,280 queue samples — about nine
minutes before its own 4-hour timeout would have fired, with no `cancel_reason`, which is the
exchange closing the market rather than us cancelling it. That part is normal and `Fmmsell10`
shows the same reasonless cancels.

**What is not normal is that nothing replaced it.** In §9.3 and §9.6 a replacement landed
about two minutes after the previous order left the book. Here, twenty-two cycles have placed
nothing.

The cause is the interaction of two rules, each correct on its own:

1. `runner._candidate_programs` orders current liquidity programmes **soonest-ending first**,
   and the runner fetches books for only the first `LIQUIDITY_INCENTIVE_LIVE_MAX_BOOK_FETCHES`
   (8) of them. §9 chose that ordering deliberately: Kalshi credits a liquidity reward only
   after a programme ends, so the soonest-ending programme is the one that can confirm the
   payout leg first.
2. `live.build_live_quote` refuses `program_ending` below `MIN_PROGRAM_HOURS_REMAINING = 2.0`.
   Also deliberate: a reward cannot be earned on a programme that is about to end.

Kalshi now runs a continuous class of **15-minute** liquidity programmes. The eight
soonest-ending programmes at 05:04Z:

| ends | market | series | reward | target size |
|---|---|---|---|---|
| 05:10Z | `KXTTELITEMATCH-26SEP180010JPRWBA-WBA` | `KXTTELITEMATCH` | $20 | 300 |
| 05:10Z | `KXTTELITEMATCH-26SEP180010PADJNO-JNO` | `KXTTELITEMATCH` | $20 | 300 |
| 05:10Z | `KXTTELITEMATCH-26SEP180010AGRJZA-JZA` | `KXTTELITEMATCH` | $20 | 300 |
| 05:15Z | `KXPLATINUM15M-26SEP180115-15` | `KXPLATINUM15M` | $20 | 300 |
| 05:15Z | `KXCRYPTOLEAD15M-26SEP180115-ETH` | `KXCRYPTOLEAD15M` | $20 | 1000 |
| 05:15Z | `KXNATGAS15M-26SEP180115-15` | `KXNATGAS15M` | $20 | 300 |
| 05:15Z | `KXCRYPTOLEAD15M-26SEP180115-HYPE` | `KXCRYPTOLEAD15M` | $20 | 1000 |
| 05:15Z | `KXGOLD15M-26SEP180115-15` | `KXGOLD15M` | $20 | 300 |

Every one has **≤0.2h** remaining and is refused by rule 2. A fresh batch replaces them every
fifteen minutes, so the eight-book window is permanently saturated by programmes the quote
policy will always refuse. This is **deterministic, not intermittent**: while 15-minute
programmes exist, the book can never reach the other ~3,913 current programmes, and it will
never quote again.

The earlier orders were placed at 16:00Z, 20:04Z and 00:08Z — times when the soonest-ending
set still contained day-scale programmes. Nothing about the strategy changed; the world did.

**Recorded, not fixed.** The fix is a change to which markets the book may quote — its
universe — on a **live, armed** arm. `CLAUDE.md` is explicit that a registered book may not
silently change rules or universe outside Experiment OS, and a changed world is an epoch, not
a patch. So this entry is the record and the operator decides. The candidate shapes, none of
them adopted here: raise the fetch window; order by soonest-ending **among programmes that
already clear the 2-hour bar**; or exclude sub-2-hour programme series at the candidate stage.
The second is the smallest change that preserves the original intent of the ordering.

**One thing was fixed**, because it is a read path and not a trading rule: this took a database
query and a code read to diagnose, because it was invisible in the logs. The runner already
computes a per-refusal-code breakdown for exactly this purpose — "a cycle that placed nothing
says which cap or which book stopped it" — and `scripts/railway_logs.py` rendered only `exc`,
so a starved cycle and a healthy one printed the identical line. `DETAIL_KEYS` now carries
`considered`, `fetched`, `placed` and `outcomes`, and no longer drops a field for being zero —
`placed=0` is the value worth reading.

**Exposure is unchanged and safe:** 2c, the two unsettled `KXBIGGESTQUAKE-17SEP26` contracts.
A book that quotes nothing takes no new risk. The cost is to the experiment, not the account:
the canary is accruing no evidence.

### 9.10 §9.9 was wrong: the starvation is intermittent, and the book is now at its cap (2026-09-18 08:04Z)

The `DETAIL_KEYS` fix shipped in §9.9 falsified §9.9 within ten minutes of reaching production.
The first `incentive smoke cycle` line to render its own counters, at 07:57:20Z:

```
considered| 0
fetched| 0
placed| 0
outcomes| {"no_slots":1}
```

`considered: 0` means the cycle never reached candidate selection at all. It returned at the
first guard — `slots = MAX_OPEN_ORDERS - count_live_book_open(...) <= 0` — which is a different
cause from the one §9.9 asserted, and a benign one.

The order book says why:

| created | market | side | price | status |
|---|---|---|---|---|
| **06:00:04Z** | **`KXUSLEI-26SEP18-T0.2`** | yes | **5c** | **filled** |
| 00:08:31Z | `KXAAAGASDAZ-26SEP18-4.6800` | yes | 1c | canceled |
| 20:04:27–28Z | `KXBIGGESTQUAKE-17SEP26-7.0` / `-6.8`, `KXYTVIEWSHIGH` | yes | 1c / 4c | filled ×2, canceled |

**The book quoted again at 06:00:04Z and filled**, about two hours after §9.9 declared that it
never would. Three filled, unsettled positions now count as open — `KXBIGGESTQUAKE` ×2 plus
`KXUSLEI` — which is exactly `MAX_OPEN_ORDERS = 3`, so the cap is holding the book quiet. That
is the design working, not a defect.

**Where §9.9's reasoning broke.** It observed that the eight soonest-ending programmes at 05:04Z
were all 15-minute ones and concluded the window is *permanently* saturated. That does not
follow: the count of concurrent sub-2-hour programmes varies, and whenever fewer than eight are
live, day-scale programmes enter the window and quote normally. The composition is real and can
starve the book for hours at a time — the 00:08Z→06:00Z gap is the observed instance — but it is
**intermittent**, not deterministic, and the book recovers without intervention.

**What this changes.** The §9.9 universe question stays open but is no longer urgent: it is a
throughput question (how often the window is wasted on programmes that will always refuse),
not a liveness one. It should be decided on measurement — the `outcomes` breakdown now in the
logs is that measurement — rather than on the false premise that the book is dead.

**What it does not change.** The refusal-code fix stands on its own merit and paid for itself
immediately: the instrument's first reading overturned the conclusion of the entry that shipped
it. That is the argument for fixing an instrument before trusting a diagnosis made without one.

**Exposure is now 7c** across three unsettled contracts (1c + 1c + 5c), against a $10 strategy
cap and a 3-order cap. Both caps hold and neither was approached.

### 9.11 The 16.7s lag was n=1, and §9.7 over-read it (2026-09-18 08:08Z)

Two of the pre-registered materiality criteria fired at this check. Span **0.82 days**.

**(b) `partial_both` now appears under the CONSERVATIVE model.** Both non-optimistic models now
show partially-filled two-sided outcomes:

| model | `partial_both` at 04:03Z | at 08:08Z |
|---|---|---|
| conservative | 0 | **10** |
| queue_aware | 10 | **20** |

The comparison is like-for-like despite the report's window widening from 1 day to 14: total
observation span is 0.82 days, so both windows cover all data. This also settles the
carry-forward worry from 04:03Z that the queue-aware rows had gone stale — they had not; they
doubled. Two independent fill models agreeing that two-sided fills occur strengthens §9.7's
direction.

**(c) The queue-aware lag moved off ~17s, by a factor of 66.**

| model | lag mean | lag median |
|---|---|---|
| queue_aware | 1102.8s | 1102.8s |
| conservative | 1879.2s | 1879.2s |

**Mean equals median in both**, which means each rests on a single observation. §9.7 reported
queue-aware `partial_both` at a **16.7s** lag and read it as a lag "that would bound risk". That
reading was drawn from **n=1**, and the next observation is 18 minutes rather than 17 seconds.
The honest statement is that the two-sided fill lag under either non-optimistic model is **not
yet estimable**, and §9.7's favourable gloss on it should not be carried forward. Ten and twenty
partial pairs do not make a lag distribution.

This cuts against the premise, which is exactly why it is recorded rather than smoothed.

**What has not moved.** `P(both | one)` is still **0.000** under both non-optimistic models
(n=350 conservative, n=600 queue_aware), because it counts only *full* pairs — the blind spot
§9.7 recorded now conceals thirty partial pairs. The metric is still **not being changed**: §6
is pre-registered, and redefining it while it disfavours the premise is precisely the move that
rule exists to prevent. The optimistic model reads 0.119 (n=1640), the same shape as before at a
larger n, which is not material.

**The adverse-selection cost still dwarfs the modelled reward.** Conservative single-leg
outcomes against deep competing size: n=340, mean single-leg MTM **−$1.3802**, against a mean
`est_reward` of **+$0.0692** — a factor of about twenty, in the wrong direction.

**The headline remains not a result.** Every measured component is zero or negative; the large
positive net is derived entirely from `est_reward`, which is still externally unvalidated. Day-
one check 3 is still the operator's and still the thing that would make the number mean
anything.

**Collector.** The counters are not comparable across this check because the report's window
also widened (3h → 72h): `seq_gap` reads 125 over 72h against 31 over 3h, which is a *lower*
rate, not a deterioration. Discovery is clean (246 cycles, 0 errors, pool $510,361.67). Fifteen
connects against ten thread starts spans two worker redeploys (#424 at 03:04Z, #425 at 07:54Z)
and is noted without being claimed as understood.

**Live book unchanged and correct:** `LIVE_STRATEGIES=Fmmsell10,Alimm1`,
`LIVE_PAPER_TWINS=Alimm1:Alimm1_pt3`, `LIQUIDITY_INCENTIVE_LIVE_ENABLED=true`,
`LIVE_PAPER_TWIN_SUFFIX=_pt4` untouched, `KILL_SWITCH=false`. Exposure 7c at the 3-order cap
(§9.10).

### 9.12 The first settlement: a full loss of premium, and the slot recycled (2026-09-18 15:47Z)

The last unobserved leg of the pipe has closed. `KXUSLEI-26SEP18-T0.2` **settled NO**, and the
YES contract we held expired worthless.

| time | side | qty | avg price | exposure | realized P&L |
|---|---|---|---|---|---|
| 14:44:40Z | yes | 1 | 5c | $0.0500 | 0.0000 |
| **14:47:14Z** | no | **0** | — | **$0.0000** | **−$0.0500** |

**Realized: −$0.0500.** The full premium, which is the maximum loss on a 1-contract YES bought
at 5c. Fee on the entry fill was **$0.0000** — we were the maker, as designed.

**The full lifecycle is now proven end to end**, every leg observed rather than inferred:
select → place → rest → **fill** → hold → **settle**. Precise fill times from the `fills` table,
which are later than the times §9.8 records (those were observation times, not fill times):

| market | order placed | filled | price | fee |
|---|---|---|---|---|
| `KXBIGGESTQUAKE-17SEP26-6.8` | 2026-09-17 20:04:27Z | 2026-09-17 23:35:47Z | 1c | $0.0000 |
| `KXBIGGESTQUAKE-17SEP26-7.0` | 2026-09-17 20:04:27Z | 2026-09-17 23:35:47Z | 1c | $0.0000 |
| `KXUSLEI-26SEP18-T0.2` | 2026-09-18 06:00:04Z | 2026-09-18 07:19:24Z | 5c | $0.0000 |
| `KXTRUMPAPPROVE-26SEP18-E39.4` | 2026-09-18 14:48:49Z | 2026-09-18 15:24:22Z | 1c | $0.0000 |

**§9.10's prediction held.** The settlement freed a slot at 14:47:14Z; the very next cycle
placed a new order at **14:48:49Z**, 95 seconds later, and it filled. The book was never
starved — it was full, exactly as §9.10 said, and it resumed the instant a slot opened. That is
the cap working as designed, and it is now observed rather than argued.

**What the loss does and does not mean.** It means nothing about the premise, in either
direction. A YES bought at 5c is a market-implied ~5% event; losing the premium is the modal
outcome and happens about nineteen times in twenty. One settlement is **n=1** and the sign of a
single deep-tail resolution carries no information. Reading it as evidence against the strategy
would be as wrong as reading a win as evidence for it.

**What it does make concrete is the asymmetry the whole thesis rests on.** The ledger so far:

| | amount |
|---|---|
| Realized P&L | **−$0.0500** |
| Open exposure | $0.03 (three contracts at 1c) |
| Total ever committed | $0.08 across four filled contracts |
| **Liquidity reward actually credited** | **none observed** |

The adverse-selection leg is now paying out in real money, on schedule, in the direction the
model expects. The reward leg — the entire reason the book exists — has produced **no observed
credit at all**. Kalshi credits a liquidity reward only after a programme ends, so an absence
this early is not yet a finding. But it converts `est_reward` from an unvalidated modelling
assumption into an unvalidated assumption that is now being *paid against*.

**Not a verdict, and not a gate read.** This is one settlement. `live_canary_keep` needs three
settled contracts and is still unreadable; no gate has been evaluated and nothing here
authorizes anything.

**Position now:** `KXBIGGESTQUAKE-17SEP26-7.0` and `-6.8` at 1c, `KXTRUMPAPPROVE-26SEP18-E39.4`
at 1c — three contracts, 3c open, back at `MAX_OPEN_ORDERS = 3`. Every cap held throughout.

### 9.13 The payout boundary check cannot be run — the instrument cannot see the payout leg (2026-09-18 15:55Z)

The scheduled payout-boundary check was meant to compare a realized liquidity reward against
`est_reward`. It cannot be run, for three independent reasons, none of which is "no reward was
paid".

**1. We stop observing a programme before it can pay.** `programs.run_discovery` polls
`iter_incentive_programs(status="active", ...)`. A programme leaves that listing when it ends,
so its row freezes at the last state observed *while it was still active*. Every programme the
live book rested in reads `paid_out = false`, and every one was last seen **before** its own end:

| market | programme end | last seen | gap | `paid_out` |
|---|---|---|---|---|
| `KXBIGGESTQUAKE-17SEP26-6.8` / `-7.0` | 17 Sep 23:59:59Z | 17 Sep 23:55:54Z | −4m | false |
| `KXAAAGASDAZ-26SEP18-4.6800` | 03:59:00Z | 03:55:02Z | −4m | false |
| `KXUSLEI-26SEP18-T0.2` | 13:59:00Z | 13:58:04Z | −56s | false |
| `KXTRUMPAPPROVE-26SEP18-E39.4` (prior terms) | 14:02:23Z | 13:58:04Z | −4m | false |
| `KXYTVIEWSHIGH-POS26OCT-8.75M` (prior terms) | 17 Sep 22:25:00Z | 17 Sep 22:20:42Z | −4m | false |

Each `false` means "false at the last moment we looked, which was minutes before it ended". It
does **not** mean the programme ended and paid nothing. We simply never look again.

**2. `paid_out` does not mean what the check assumed.** The flag is real and does flip — **23**
of 7,979 programme-terms rows carry it, **12** of 4,112 currently-live liquidity programmes. But
several of those have **not ended**: `KXVOTECLARITY-26SEP15-*` ends 20 Sep and reads true today;
`KXFEAR-26SEP11-*` ends at 20:00Z today and reads true; `KXEOWEEK-26SEP05-*` ends 19 Sep. So
`paid_out = true` is not "the end-of-programme reward has been distributed", and it cannot be
used as a payout signal until its actual semantics are established.

**3. The shadow instrument never covered a single market the live book traded.** Querying
`incentive_shadow_outcomes` for all nine tickers the live book has ever quoted —
`KXHORMUZPEAK` ×3, `KXYTVIEWSHIGH`, `KXBIGGESTQUAKE` ×2, `KXAAAGASDAZ`, `KXUSLEI`,
`KXTRUMPAPPROVE` — returns **zero rows**. The live runner ranks by soonest-ending programme; the
shadow collector snapshots a different, cap-limited subset (`market_cap_reached` fires on every
discovery cycle). So there is no `est_reward` for the markets we actually traded either.

**Both sides of the estimate-versus-realized comparison are missing for the live book.** That is
the finding.

**This is not evidence against the reward model.** It is evidence that the instrument cannot
observe the payout leg at all. Two further reasons a null here would have been uninformative
even with perfect observation: the live runner selects by cheapest downside and soonest end,
which steers systematically *away* from the big-pool programmes; and a 1-contract resting bid in
a 27,000–60,000 contract book is a ~0.5% share, so any credit would round to zero. The smoke
test was never sized to measure a reward.

**What would be needed, none of it taken here.** Poll ended programmes — by `program_id`, or a
status other than `active` — so the terminal `paid_out` state is captured rather than frozen
minutes early. Establish what `paid_out` means, since it is true on programmes that have not
ended. Make the shadow instrument cover the markets the live book selects, so estimate and
realized land on the same market. Each is a change to how a live experiment's data is collected,
and each is an **OWNER DECISION**, not a patch.

**This also explains §9.12's "no liquidity reward credited or observed".** That line is literally
true and now has a cause: we were never going to see one. It should not be read as the reward
leg having failed.

### 9.14 P(both | one) leaves zero — and the headline goes negative in the same reading (2026-09-18 16:18Z)

Span **1.16 days**. Three of the five pre-registered materiality criteria fired at once, and they
do not point the same way.

**(a) FULL `both_filled` appears under both non-optimistic models, for the first time.**

| model | `both_filled` | `partial_both` | P(both \| one) | n |
|---|---|---|---|---|
| conservative | **4** | 16 (was 10) | **0.007** (was 0.000) | 555 |
| queue_aware | **4** | 36 (was 20) | **0.004** (was 0.000) | 1005 |
| optimistic | 275 | — | 0.104 | 2634 |

The pre-registered §6 metric has moved off zero for the first time since §9.2. Genuine two-sided
fills exist outside the optimistic model. This is the mechanism the thesis needs, observed.

**(c) The queue-aware lag is now estimable, and it is not 17 seconds.** Mean **828.0s** against
median **553.2s** — the two have finally diverged, so this rests on several observations rather
than one. The two-sided fill lag under queue-aware is on the order of **nine to fourteen
minutes**. §9.7's 16.7s reading, already qualified by §9.11, is now conclusively dead and should
never be cited again. (Conservative still reads mean = median = 1212.5s, so that one is still
n=1.)

**And in the same reading, the headline flipped hard negative.** Under `A_break_even`, at every
tier and every fill model:

| policy / tier | model | reward | single-leg MTM | net | was (12:14Z) |
|---|---|---|---|---|---|
| A_break_even 25 | conservative | 26.48 | **−40.73** | **−13.95** | +5.74 |
| A_break_even 100 | conservative | 104.84 | **−154.16** | **−48.44** | +28.40 |
| A_break_even 500 | conservative | 462.11 | **−613.53** | **−150.53** | +222.77 |
| C_conservative 500 | conservative | 261.22 | −30.79 | **+230.43** | +124.99 |
| C_conservative 500 | queue_aware | 261.21 | −459.69 | **−198.48** | +124.86 |

**The driver is single-leg mark-to-market, not the reward.** Between 08:08Z and now, the
`A_break_even`/$500 modelled reward grew from 284 to 462 — about 1.6×. Its single-leg MTM grew
from **−61.63 to −613.53**, about **ten times**. The adverse-selection leg is outrunning the
reward leg by roughly a factor of six in rate of growth.

The per-placement figures say the same thing. Conservative single-leg marks against deep
competing size: **−$4.9808** mean (n=506) against a mean `est_reward` of **+$0.1005** — a factor
of about **fifty** the wrong way, up from twenty at 12:14Z.

**Of the eighteen policy × tier × model cells above, exactly one is still positive:**
`C_conservative` under the conservative fill model. The aggressive policy's single-leg risk has
overwhelmed its reward at every tier; the conservative policy's has not. That is a structural
observation about *policy choice*, not a verdict on the premise, and it is the first time the
two policies have separated this clearly.

**Two alternative explanations I cannot exclude, and will not paper over.** First, the tail is
heavy: `worst@bid` is **−$313.10** against a mean of −$5.08, so a handful of outcomes may be
driving the aggregate rather than a regime change. Second, the collector restarted twice today
(#425 at 07:54Z, #426 at 12:46Z) and this report carries a new event type, `throttled` (3), with
17 connects against 16 disconnects and 11 thread starts. An interrupted pair closed at a bad
mark would land exactly here. Four hours is a short window for a 10× move in one component.

**Not a verdict, and no gate re-interpretation.** §6 is pre-registered and reads the conservative
model; a negative reading is a reading, not a conclusion I am entitled to draw early, and I am
not redefining anything after seeing it. What this entry records is that the metric finally moved
off zero *and* that the economics deteriorated sharply in the same four hours, which is an
uncomfortable pair and is exactly why both belong in the record together.

**Collector:** discovery clean (345 cycles, 0 errors), `listed=4549`, pool **$559,521.67**,
`seq_gap` 137 over a 72h window — a lower rate than earlier. The new `throttled` counter is noted
and not yet understood.

### 9.15 Second settlement, and two safeguards not doing what they were meant to (2026-09-18 18:23Z)

**Second settlement.** `KXTRUMPAPPROVE-26SEP18-E39.4` settled at **17:32:14Z**: quantity 0,
realized **−$0.0100**. Another full loss of premium — a 1c YES expiring worthless, again the
modal outcome and again uninformative at this n. Running realized: **−$0.0600** across two
settled contracts.

Two defects surfaced in the same read. Neither risks meaningful money at current size, and both
are safeguards behaving differently from how they were written.

**Defect 1 — this book has no event-level cap, and it has now doubled up on an event another
live book already holds.**

| strategy | market | side | price | status | event |
|---|---|---|---|---|---|
| `Fmmsell10` | `KXRT-RES-97` | no | 93c | **filled** (14 Sep 20:31:44Z) | `KXRT-RES` |
| `Alimm1` | `KXRT-RES-93` | no | 3c | **resting** (17:33:50Z) | `KXRT-RES` |
| `Alimm1` | `KXRT-RES-94` | no | 10c | **resting** (18:21:03Z) | `KXRT-RES` |

`Fmmsell10` is short 1 NO at 93c on `KXRT-RES-97` — **$0.93** at risk, several times the
incentive book's entire committed capital. `Alimm1` is now resting two more NO bids on the same
event. All three are the same direction on one event.

`LIVE_ONE_POSITION_PER_EVENT=true` is set, and `repository.event_has_open_live_position` exists
for exactly this, taking an `exclude_strategy` so a book cannot block itself. **The incentive
package never calls it.** `live.build_live_quote`'s refusal set is `excluded_series`,
`open_order_cap`, `not_two_sided`, `post_only_cross`, `no_target_size`, `target_not_met`,
`program_ending`, `no_book`, `too_expensive`, `exposure_cap` — there is no event cap among them.
This is the "event-level concentration cap, if this book is ever sized up" that §9.8 parked as a
follow-up. It is no longer hypothetical.

**Defect 2 — the open-order cap is under-counting, and the book is holding four commitments
against a cap of three.**

`repository.count_live_book_open` skips any ticker whose latest position snapshot is flat
(`abs(qty) <= 0.01`), which is correct for a settled position. But `KXRT-RES-93` has a snapshot
at **quantity 0** with exposure $0.0003 while its order is still **resting and unfilled** — so a
live resting order is invisible to the cap. The function's own docstring says "A resting/unfilled
order (no snapshot yet) counts as open"; the failure is that once *any* snapshot exists for that
ticker at zero, it stops counting. It cannot distinguish "position closed" from "order not filled
yet".

Actual commitments right now: `KXBIGGESTQUAKE` ×2 filled, plus `KXRT-RES-93` and `-94` resting =
**four**, against `MAX_OPEN_ORDERS = 3`. The runner is behaving exactly as its code says; the
code does not implement the cap as intended.

**Also new, and worth recording:** these are the book's **first NO-side quotes** — every prior
order was YES at 1–5c — and **10c is the highest price it has ever placed**.

**What is and is not at risk.** Committed on the incentive book: 1c + 1c + 3c + 10c = **$0.15**,
against a $10 strategy cap. The caps that bound real loss — exposure, `qty = 1`, price ≤ 25c —
all hold, and the price cap is not close. The event concentration matters at *this* size only as
a demonstration; at any size worth trading it would be the live risk.

**Recorded, not patched.** Both fixes change the caps of a live, armed, real-money arm. That is
an **OWNER DECISION**, and fixing a safeguard is still a change to one. The two shapes:

1. Call `event_has_open_live_position(event_ticker, exclude_strategy=LIVE_TAG)` in
   `build_live_quote` and refuse with a new `event_cap` code — reusing the fleet's existing
   mechanism rather than inventing a second one.
2. Count a resting order as open regardless of a zero-quantity snapshot — for example by
   treating a non-terminal order status as open before consulting the snapshot at all, so only a
   *filled* position can be dismissed as flat.

Neither is taken here. Stand-down remains one step: remove `Alimm1` from `LIVE_STRATEGIES`.

### 9.16 The four fixes, and what the API can and cannot show us (2026-09-18 19:10Z)

Operator-authorised on 2026-09-18. All four change a live, armed arm, and every one of them
**tightens** a bound or **adds** an observation — none relaxes anything or expands exposure.

**Can the API show us rewards at all? Partly, and the split matters.**

`GET /incentive_programs` takes `status = all | active | upcoming | closed | paid_out`. So the
**programme-level payout state is fully visible and always was** — we simply never asked for it.
That also settles §9.13's second puzzle: `paid_out` is a programme lifecycle state with its own
listing, which is why it reads true on programmes that have not ended.

What the API does **not** appear to offer is **our own credited amount**. The programme object
carries the pool (`period_reward`), `target_size` and `discount_factor_bps` — terms, not
per-user credits — and the portfolio surface is positions, orders, fills, settlements and queue
positions. Nothing per-user for incentives, and the external client that mapped this API had no
reward reconciliation either. That is an absence of evidence, not proof of absence.

**The remaining route to our realized reward is arithmetic, not an endpoint.** A liquidity credit
is cash appearing that is not a fill and not a market settlement, so it is recoverable as the
residual of a balance change against fills and settlements over the same window. `get_balance`
and `get_settlements` both already exist. Not built here — it is a new capability, not one of the
four fixes — but it is the shape of the answer and it needs no new API.

**Fix 1 — the event cap (§9.15 defect 1).** `live.build_live_quote` gains
`REFUSE_EVENT_CAP` and refuses any candidate whose `event_ticker` is blocked, placed with the
other hard eligibility rule *before* any pricing. The runner computes the blocked set once per
cycle from two sources, and both are needed: events this book already holds (its own stacking,
which is what happened), and events **any** live book holds a position on, via the fleet's
existing `repository.event_has_open_live_position`. A placement also blocks its own event for the
rest of the cycle. `build_live_quote` stays a pure function — the flag arrives as a parameter,
exactly as `excluded_series` does.

**Fix 2 — the open-order cap (§9.15 defect 2).** Counting moved into
`repository._open_live_tickers`, which checks the **order status before the position snapshot**.
A non-terminal order is open, full stop; only a *filled* order can be dismissed as flat. That
ordering is the whole fix: a snapshot cannot tell "position closed" from "order not filled yet",
because both read quantity 0. `count_live_book_open` delegates, and `live_book_open_events` /
`live_book_open_tickers` share the definition so the caps cannot disagree. This is a shared
function — MMSELL uses it too — and it now counts **more** as open, which is strictly tighter.

**Fix 3 — poll the terminal listing (§9.13).** `programs.run_discovery` now also polls
`status="paid_out"`, listed second and deduplicated by programme id so a programme caught in both
is recorded in its terminal form, with `status_observed` carrying the real listing instead of a
hardcoded `"active"`. The extra poll is failure-tolerant: a 503 on it costs an error count and a
note, never the active listing the live book reads. `DiscoveryResult.errors` now also carries
`cycle.errors`, which it previously dropped on the success path.

**Fix 4 — pin the live book's markets into the shadow (§9.13).** The shadow ranks by reward size
and takes the top 150; the live runner ranks by soonest programme end and takes 8. Disjoint by
construction, which is why `incentive_shadow_outcomes` held zero rows for every ticker the live
book ever quoted. `refresh_programs` now passes the live book's open tickers to `_reconcile`, and
they survive the cap. The cap bounds WebSocket volume; it was never meant to decide what is worth
measuring, and what we are trading always is.

**A fixture told us the event cap works before any test did.** Three candidate markets in
`tests/test_liquidity_incentive_runner.py` shared one `event_ticker`, and the new cap immediately
refused two of them. The fixture was corrected to one event per market — the shape it had been
modelling is the shape the book now forbids — and three tests were added for the rule itself,
including the exact `KXRT-RES` production case.

**Verification:** `ruff` clean; **4,548 passed, 11 skipped**. Nine new tests across the event cap,
the open-order cap, the terminal listing and the pinning.

**Not done, and named rather than quietly skipped:** the universe rule (§9.9/§9.10) stays open —
it was not among the four. And `scripts/live_book_truth.py` computes its open set from *filled*
tickers only while its own docstring says a resting order counts as open: the same class of gap as
fix 2, in a read-only ops script rather than the enforcer. Recorded, not widened into.

### 9.17 Day-one check 3, at last: the units are exact, and lifetime rewards are $0 (2026-09-18 19:19Z)

The operator found the incentives page. It is the external reading this thesis has been missing
since §9.1, and it answers two different questions with two different answers.

**1. `period_reward_usd` is EXACT.** The page groups by event and shows the pool summed across
the event's markets. Our own field is marked DERIVED from `period_reward_raw` under a stated
*assumption* — centi-cents ÷ 10,000. Three independent events, different per-market values and
very different market counts:

| Kalshi's page | our rows | pool |
|---|---|---|
| GTA VI: The Album · Features — **$12,900**, 17 Sep 11:32 CDT → 24 Sep | `KXFEATURE`, **129** markets × $100.0000 | **$12,900.00** |
| Pro Baseball Playoff Qualifiers — **$9,000**, 17 Sep 15:46 CDT → 1 Oct | `KXMLBPLAYOFFS`, **18** × $500.0000 | **$9,000.00** |
| Washington aerospace employment — **$6,500**, 5 Sep 23:16 CDT → 2 Oct 10:46 CDT | `KXWAAEROEMP`, **13** × $500.0000 | **$6,500.00** |

Exact to the cent in all three, and the start/end timestamps match to the second once CDT is
converted (11:32 CDT = 16:32:33Z, 15:46 = 20:46:39Z, 23:16 = 04:16:31Z next day, 10:46 =
15:46:41Z). **The unit assumption is now a measurement.** That is day-one check 3 and it passes.

**2. Lifetime rewards: $0. September 2026: $0.** We have earned nothing.

**These do not cancel out, and neither is the headline.** What the page validates is the *pool* —
one input to `est_reward`. The headline's positive total is pool × **our modelled share of resting
size** × scoring, and the share model is untouched by this. A validated input to an unvalidated
model is still an unvalidated model.

**The $0 is consistent with §9.13 rather than a refutation of it.** A single 1-contract bid in a
27,000–60,000 contract book is roughly a 0.5% share of a per-period slice, which is fractions of
a cent; $0.00 is what that rounds to, and it is what §9.13 predicted before the page was seen.
So the zero tells us the smoke test was too small to measure a reward — which we already knew —
and *not* that the reward mechanism fails. It remains true that it is the only external reading
of realized reward we have, and it is zero.

**3. A selection dimension we cannot see at all.** The page carries a **Category** column reading
**Low / Medium / High**. It is not a field we drop: `extra_params_json` is empty for **every**
current programme, so the API gives us nothing we do not already type. And it is not derivable
from what we hold — the same three events are all `target_size = 1000` and
`discount_factor_bps = 5000`, yet the page calls them **Low**, **High** and **Medium**
respectively; per-market reward does not separate them either, since $500/market appears as both
High and Medium. Three counterexamples, so this is a checked claim rather than a guess.

If Category means what its name suggests — how hard the liquidity is to supply — then the best
target visible on that page is the **Low** category with the **largest** pool, which is
`KXFEATURE` at $12,900. Our runner ranks by soonest programme end and has never looked at it.
That is the §9.9/§9.10 universe question, now with a concrete cost attached.

**What would actually measure our reward.** Still the balance residual proposed in §9.16 — a
credit is cash that is neither a fill nor a settlement — since the page gives a lifetime total
rather than a per-programme attribution, and no per-user endpoint appears to exist.

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
   Read the running values first, then set what
   `liquidity_incentive_mm.activation_env(current_live_strategies=..., current_live_paper_twins=...)`
   returns, `LIVE_STRATEGIES` last. It takes those arguments so it cannot produce a value that
   drops a running book: `LIVE_STRATEGIES` is the **whole fleet's** allowlist, and setting it to
   this book alone stands every other live book down. It also deliberately does **not** emit
   `LIVE_PAPER_TWIN_SUFFIX`, which is the fleet's twin-epoch marker — this book pins its own
   twin through the per-book `LIVE_PAPER_TWINS` instead.

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
