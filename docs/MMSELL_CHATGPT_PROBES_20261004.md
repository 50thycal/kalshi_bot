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

Pending the frozen exports and probe runs.
