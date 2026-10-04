# MMSELL: cheaper quotes, reserved slots, and quote withdrawal

Written before the new outcome exports on 2026-10-04. Calvin authorized building and
running these three **read-only research probes** in the ChatGPT session. No trading book,
live parameter, collector, executor, risk control, gate, or ops-runner change is authorized.
Research Lab; Build OS v0.12 checked against the canonical VERSION.md on 2026-10-04.

## Scope and evidence contract

These are offline diagnostics, not registered trading experiments or evaluator verdicts.
Use the existing ops `db` request to export bounded, sanitized observations and run the
stdlib-only analyzer locally. This deliberately avoids modifying the ops allowlist/runner.
Keep `Fmmsell10` and `Hmmsell10` separate; do not pool epochs or promote from historical
evidence with unresolved Experiment OS platform impacts. The 2026-10-04 Control Tower read
is `cg-mm3-ct-20261004-1`; it identifies those existing comparability limitations.

Frozen retrospective window: orders/candidates from 2026-09-16T00:00:00Z through
2026-10-04T15:30:00Z (exclusive). Fmmsell10 also reports two creation-date slices at
2026-09-24T00:00:00Z. These are historical sensitivity slices, **not untouched validation**.
Any genuinely forward validation begins at 2026-10-04T15:30:00Z. Merely writing this does
not schedule work or enable any strategy. Already-known research motivated these ideas;
historical results can reject or prioritize research, never demonstrate prospective alpha.

Unit: one decided order/opportunity, normalized to at most one filled contract; duplicates
and retries are reported separately and inference clusters market outcomes by settlement UTC
date. Bootstrap 5,000 date-block resamples, seed 20261004. Report unsettled/unmapped markets
as missing, never wins or zero-P&L trades. Require at least 10 settlement-date blocks before
an interval-based positive screen. Mean effects below 0.5 cent per order are not an actionable
screen at this stage; thin or unidentified measurements remain HOLD.

## Q1 — INVERSE-OFFSET: a NO bid one cent below the incumbent

Mechanism: one cent improves entry economics but may lose volume and expose the quote to
deeper adverse sweeps. This is the opposite price treatment from the killed +1-cent priority
arm, not a revival of that arm. Frozen offset: -1 cent; no parameter sweep.

Measure on actual placed buy-NO orders with a known settlement:

* Baseline: actual first-contract economics, using actual fill price and allocated actual
  fee when available; a terminal unfilled order contributes zero.
* Touch proxy: first public YES-taker print at YES price >= 101 minus the original NO limit.
* Strict-through proxy: first such print **strictly above** that level.
* Match in the observed resting window, after acknowledgement/creation and before the
  original first fill or cancellation, market close, or four-hour timeout, whichever is first.
  Include a print at the original first-fill exchange millisecond, since the sweep is exactly
  what the alternative quote would encounter; exclude later prints.
* Hypothetical entry is original limit minus one, fee assumption 0.02 cent per contract;
  sensitivity fee 0.10 cent. These are assumptions, not measured alternative fills.

The lower quote could fill later than the original; that continuation is not identified
when the collector stops observing. Queue position at the alternative price is also unknown.
Both proxies are scenarios, neither is an executable-fill guarantee or a formal bound.
Report a control-price tape replay against actual fills to expose model mismatch and missing
tape. At least 80% order tape coverage, 100 settled proxy hits, 10 dates, positive net policy
value and >= +0.5 cent/order improvement with bootstrap 5th percentile > 0 would support a
future execution test. **HOLD(model)** remains mandatory without alternative-queue/fill
identification; positive scenario dollars are not a live-canary promotion. Negative scenarios
at the floors de-prioritize this version, not all possible offsets.

## Q2 — SLOT-PRIORITY: reserve capacity for a validated quality cell

Mechanism: an open-position allowance is scarce only when it actually refuses promising
candidates. Hold cap and clip constant; allocating slots differs from sizing up a cell.
First perform a capacity census, before building a policy replay. Use the already-frozen
slow-information types `{event_stat, mention, exact_score, outright, game_prop}` as a
candidate priority cell; this does **not** claim they have forward validated.

Deduplicate parity events by live tag and market over the window. Report each market's
ever-open-cap refusal, first refusal date, ever-placed status and other gate outcomes.
Explicit live `gate:open_cap` is the primary cap evidence; paper `skip_open_cap` is separate.
Markets refused at the cap but subsequently placed are delayed, not missed. Report contest,
tier, exposure and paused refusals separately: reserving open slots cannot fix those gates.
Paper outcomes of never-placed markets, priced at their first open-cap observation, are
an opportunity screen only; do not treat them as hypothetical maker fills.

At >= 200 distinct candidates and >= 7 creation-date days, fewer than 20 never-placed
priority-cell open-cap refusals **or** a refusal share below 5% of priority-cell candidates
means `UNSUPPORTED(capacity)` for this window. Enough blocked supply gives `HOLD(policy)`:
proceeding would still require a validated cell and an equal-budget occupancy replay with
fill/holding-time counterfactuals. Never print a profit PASS from this census. Analyze Hmmsell10
separately, because larger clips and the split contest key change its scarcity conditions.

## Q3 — WITHDRAWAL: broad NO-quote retreat without matching trade flow

Mechanically distinct from static queue-depth, pre-post flow veto, or cancellation after
ordinary prints at our level: this asks about liquidity disappearing rather than trading.
Low prior: failed price/volatility and cancellation studies are genuine contrary evidence.

Frozen trigger, evaluated only on readable telemetry while an order is still unfilled:
over a trailing 60-second interval, (a) best YES ask increases >= 1 cent (NO bid retreats),
(b) NO top-three depth falls >= 50% and by >= 20 contracts, (c) negative, non-own NO deltas
occur at >= 2 distinct YES-price levels, and (d) public YES-taker volume is <= 10% of those
removed contracts. Compare to the latest valid baseline tick 40-60 seconds before the
decision; require both endpoints valid. Counterfactual cancel latency: 2 seconds, so fills
sooner than trigger + 2 seconds are not avoidable. Do not sell existing holdings.

Price and depth ticks, deltas and trade records must have been received by the trigger time.
Use exact exchange fill timestamps (WS, then REST raw created_time), not reconcile time.
Missing fields, gaps, disconnected sessions, throttling, snapshot resets or incomplete
trade coverage make a window unusable. Aggregate depth subtraction is only a withdrawal
proxy; it does not identify an individual cancellation or its owner's information.

Primary policy delta = negative of actual net first-contract outcome for avoidable fills;
unfilled orders and fills preceding cancellation latency contribute zero. Report avoided
winner/loser counts, remaining profit, fill retention, and triggered-versus-untriggered
settlement outcomes. At >= 80% readable order coverage, >= 40 avoidable settled fills and
>= 10 dates, avoided fills with nonnegative mean => `UNSUPPORTED(policy)`; a positive
policy delta >= +0.5 cent/covered order with bootstrap 5th percentile > 0 and >= 40% actual
fill retention supports forward research. Otherwise HOLD. No hypothetical fills in freed
capacity are credited. If raw-event coverage cannot verify the mechanism, print
`HOLD(instrument)` even when a coarse depth/price signal can be described.

## Build card and acceptance

Goal: reproducible measurements and a result for each hypothesis, with assumptions visible.
Non-goals: live changes, paper books, new data collectors, XOS lifecycle changes, repairing
unrelated platform issues, or parameter search. Material risks: post-fill lookahead,
confusing YES/NO scales, charging taker fees to maker fills, ignoring censoring, multiplying
contracts into independent samples, and replacing unobserved executions with paper fills.

Acceptance: three runnable offline probes; SQL exports through the existing read-only
channel; meaningful tests of timing, price convention, missing/censored data and policy
accounting; durable results and source/run identifiers; no files on a trading import path
and no runner/workflow edits. Existing WS-007/WS-019 cover this bounded research extension;
no additional ongoing workstream or live experiment is created.

## Results

Completed 2026-10-04. The contract above was frozen at commit
`79969e0ee148b1a8cfbe069e56c0d5e9b103ece7`, before the new outcome exports.
These are diagnostic judgments, not Experiment OS gates or lifecycle decisions.

### Measurement and provenance

The three probes executed locally against sanitized exports from the existing read-only
production channel. SQL completed against code `a6c920f658770435e67e5c1e4764255bf6b80e6f`:

| Source | Run | Decoded observations |
|---|---|---:|
| Orders, fills, settlement mapping and tape | `cg-mm3-orders-20261004-2` | 670 orders: F 636, H 34 |
| Candidate/gate census | `cg-mm3-slots-20261004-1` | 6,765 tag/market candidates: F 6,435, H 330 |
| Paired book ticks and withdrawal traces | `cg-mm3-withdraw-20261004-3` | 670 order traces |

Immutable source snapshot: `f0c62dd546bb435c2e425ba03ed1e7fd383bab0f` on the transport
history. [Machine-readable results](research/MMSELL_CHATGPT_PROBE_RESULTS_20261004.json)
include source URLs, input/decoded-data/query hashes, column schemas and all sensitivity groups.
The first order export was superseded to bound fill timestamps to the cutoff, include exchange
cancellation events, and fall back to WS quantities when REST retained a zero quantity.
These are measurement corrections; prices, trigger thresholds and decision floors were unchanged.
The final withdrawal rerun removed an accidental extra 40-contract baseline minimum and
required nonmissing price/depth fields at both paired endpoints. Results were unchanged;
the final query now matches the original frozen depth-drop criteria exactly.

The initial census included 654 F orders. The analysis requires an exchange order identifier
and buy-NO action, leaving 636. Of these, 473 have readable normal-entry context; 435 have
mapped settlement and usable terminal/fee accounting. Those 435 orders, representing 401
markets with 34 repeated/retry orders, contain 328 observed filled orders and 17 losing fills.
Baseline is **+$4.454 at at most one contract per order**, with actual allocated fees.
Multi-fill orders use average observed fill price/fee, rather than pretending each contract
is independent. This is a normalized research cohort, not the full canary's account P&L.

H is HMmSell (`Hmmsell10`), never HFM. Its 34 observed orders contain 32 normal-context
orders, but only six mapped settled fills (one settlement date). Their normalized baseline
is +$0.39. Twenty-six normal-context orders have no settlement mapping by the cutoff.
This cannot establish the new strategy's long-run profitability or a one-versus-three size effect.

### 1. INVERSE-OFFSET — HOLD(model); low priority for money

| F normal-entry cohort | Proxy hits | Losing hits | Normalized net | Delta vs actual |
|---|---:|---:|---:|---:|
| Actual incumbent fills | 328 | 17 | +$4.454 | — |
| Current-price tape touch, 0.02¢ assumed fee | 265 | 13 | +$4.407 | −$0.047 |
| One-cent lower quote, touch, 0.02¢ assumed fee | 54 | 1 | +$3.119 | −$1.335 |
| One-cent lower quote, strict-through | 15 | 0 | +$1.157 | −$3.297 |

The lower-touch improvement interval is **−2.30 to +1.75¢ per decided order**
(bootstrap 5th/95th percentiles); mean −0.31¢. Raising the assumed alternative fee to
0.10¢ changes its modeled net to +$3.076. The early creation-date slice improves by
$1.855; the later slice deteriorates by $3.190. These are sensitivity slices, not forward tests.

The apparent selection improves win rate but sacrifices substantial execution opportunity.
Do not treat the scenario dollars as the outcome of an executable policy: the control tape
finds a matching print for only **243/328 = 74.1%** of actual fills. Orders with a YES-taker
print are 312/435; that is activity, not proof of complete coverage. A missing print can mean
quiet trading or missing telemetry. Alternative-price queue and later fills are unidentified.
There are only 54 target touches, below the 100-hit floor. H contributes only two lower-touch
hits with modeled +$0.150 versus its +$0.390 incumbent baseline, far too thin to interpret.

**Decision:** do not fund this quote change on these results. A rerun requires a tape coverage
audit and explicit treatment of the alternative queue/continuation. Keep the mandatory model
HOLD even if a future historical proxy happens to show positive dollars.

### 2. SLOT-PRIORITY — UNSUPPORTED(capacity) for F; HOLD(accrual) for H

F supplies 6,435 distinct candidates across 19 creation dates and 38,606 parity rows.
There are **zero recorded live `gate:open_cap` refusals** in the entire census, including
the 317 candidate slow-information markets. Forty-seven of those 317 were placed at least
once. In contrast, 254 had at least one parent/twin contest-cap observation and 75 had a
tier observation. These counts overlap; they are not an additive, mutually exclusive funnel,
and most candidates never reached a live placement attempt.

The pre-registered census clears its sample floors and fails the capacity premise: reserving
open slots cannot recover opportunities blocked by different gates. This does not prove the
universe has no good opportunities; it says this proposed allocation mechanism has no recorded
blocked supply to work on. No paper outcome is credited as a hypothetical live fill.

H has 330 candidates on one creation date, 22 slow-information candidates, ten of those
placed, six with a contest-cap observation, five with a tier observation and zero open-cap
refusals. The seven-day floor is not met. HMmSell already changes contest-key handling;
historical F gate counts therefore cannot be inherited as H scarcity or a clean causal effect.

**Decision:** park slot reservation. Rerun the H census after at least seven dates; a serious
slot proposal also needs at least 20 never-placed priority candidates explicitly refused by
the open cap and a refusal share of at least 5%, plus validated quality and occupancy accounting.

### 3. WITHDRAWAL — HOLD(instrument), with contrary economics on readable traces

Of the 435 decided F orders, 303 (69.7%) have valid paired book windows. They contain
199 observed filled orders, eight losers, and normalized actual net +$5.027. The frozen
raw-delta/low-trade trigger fires on 102 readable orders. With the assumed two-second cancel
latency it removes **57 filled orders: 57 winners, zero losers**, mean +6.43¢ discarded per
fill. Counterfactual delta **−$3.666**, leaving +$1.361 in the covered cohort and retaining
71.4% of observed fills. The date-block delta interval is −1.49 to −0.93¢ per covered order.

The coarse price/depth-only alternative discards 62 winners and zero losers: delta −$4.006.
It is descriptive only, not verification that liquidity was withdrawn without trade flow.
Both early and late historical slices lose money from cancellation. H supplies two readable
settled orders and would cancel one winner for −$0.06; no inference is warranted.

Coverage is below the 80% floor. The raw checks remove reported sequence gaps, throttles,
snapshot resets and session changes, but error-free windows do not independently certify a
complete trade channel. The full-rule label in the JSON means raw-delta checks passed;
it does not identify individual informed cancellations. Silent trade outages remain unknown.
This instrument limitation remains a HOLD even if a future paired-book census clears 80%.

A **post-hoc coverage audit**, not a new trading signal, found paired windows for only 8/17
losing fills versus 191/311 winners. Seven losers filled within 40 seconds of acknowledgement;
such orders cannot supply the rule's 40–60-second baseline. Nine of the 17 losses are outside
the readable replay. This is exactly why the observed negative economics can deprioritize
the rule but cannot certify performance across all live fills.

**Decision:** do not introduce this cancellation rule. Better coverage would make its estimate
more representative, but the observable sample currently shows it removing profitable fills.
No replacement trades or profits from freed slots are included.

### What the results suggest next

None of these three diagnostics supports a new live treatment. The most valuable next
research is the paper/live execution audit: reconcile every known actual fill to the public
tape and its decision-time book, quantify startup/fast-fill censoring and compare exact
matched opportunities. A 74%-recall model should not select live quote changes from modeled P&L.
This recommendation does not authorize new collection or instrumentation changes.

For money already being tested, let HMmSell's existing one-versus-three treatment accrue
settlements and assess it within its own epoch. Do not stack an offset, slot policy and cancel
rule on top: that would obscure the size experiment and introduce three unsupported mechanisms.
Retain the fixed probes for later snapshots; do not sweep alternative thresholds to rescue these
historical results. A prospective quote treatment, if later warranted, should randomize only
the quote offset at unchanged clip/cap/gates and score net per eligible opportunity alongside
fill rate and tail losses. No such treatment was created or started here.

### Validation and rerun

All repository Ruff checks passed. Twenty-three focused tests passed for actual-fee accounting,
contract normalization, missing/censored outcomes, hot-entry exclusion, cancel latency,
post-fill lookahead rejection, missed-versus-delayed slots, date clustering, truncated exports,
YES/NO conventions and the read-only query construction. The queries themselves ran on
production through its existing read-only transaction, without runner or allowlist edits.

Each entry point supports `--export-request <unique-id> --since <ISO> --until <ISO>` to
print a single read-only ops request. Execute requests sequentially through the existing
transport, retrieve their complete per-id results, then run:

```bash
python scripts/mmsell_inverse_offset_probe.py --input orders.txt
python scripts/mmsell_slot_priority_probe.py --input slots.txt
python scripts/mmsell_quote_withdrawal_probe.py --input withdrawal.txt --orders orders.txt
```

Pass the matching `--since` and `--until` to analyzers when changing export windows. Large
GitHub contents responses may omit file bodies; retrieve the recorded Git snapshot with
`git show <snapshot>:ops/results/<id>.txt`. Empty or truncated exports fail decoding rather
than producing a zero-fill result. The scripts are offline and rerunnable; they were not
scheduled, installed on the worker or put into the ops script allowlist. The research PR stays
unmerged because this session does not authorize a deployment. No live setting, lifecycle state,
trading code, collector or runner was changed.
