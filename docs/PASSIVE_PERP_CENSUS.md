# PASSIVE-PERP — BTC/ETH price-return and testability census

**Status:** pre-registered and probed 2026-09-12; **HOLD — mixed / fee-and-control-negative price screen.** No paper promotion.
**Owner request:** Calvin selected passive BTC/ETH perp reversion and explicitly approved
the read-only probe allowlist change, testing and merge in this ChatGPT session on 2026-09-12.
**Build:** [WS-017](workstreams/WS-017-passive-perp-probe.md).

## Question and mechanism

Can fading an unusually rich/cheap perp earn an outright **price return**, with enough
room for passive execution costs to justify further measurement? Temporary inventory
pressure might move the perp away from its index. A passive trader could supply liquidity
against that pressure; alternatively, informed flow could select only losing fills.

This is a mechanically different execution premise from PERP-V1's taker arm: hypothetical
same-side maker entry and exit, restricted to BTC/ETH. It does not revive PERP-V1, modify its
scorer or write its registered metric keys. The 2026-09-12 Control Tower request
`rl-ct-20260912a` showed no open perp experiment (zero PROBE entries). Historical PERP-V1
was closed outside XOS; its disposition stays intact.

The cheap prerequisite matters: `perp_arm_scores.score_arm_a` computes a **change in
premium**, not an outright price return. Index 100 → 110 and perp 101 → 110.5 narrows
premium while a short loses 9.5 per unit. We measure both quantities separately.

## Frozen census specification (before first query)

- Script: `scripts/passive_perp_probe.py`; stdlib plus psycopg, SELECT-only connection
  requiring `DATABASE_URL_RO`. No orders, tables, env changes, gates or registration writes.
- Dataset: live-collected `perp_market_snapshots`; exact tickers `KXBTCPERP`, `KXETHPERP`;
  UTC `[2026-08-30 00:00, 2026-09-03 00:00)`. No backfill mixing. Full selected rows hashed
  with deterministic normalized JSON; ops envelope records the code SHA. Retained window
  is deliberately fixed because collection stopped September 2; a recent-hours query is empty.
- Both assets were selected after reviewing historical premium results. This is retrospective
  exploration, **not an untouched holdout**, despite freezing the new measurement before query.
- Prior 20 consecutive valid observations determine mean/sample SD; enter at `abs(z) >= 2.5`,
  short rich / long cheap. Quote bid/ask and positive mark/index must exist; crossed books,
  nonfinite fields and any timestamp gap outside `(0,600]` seconds make the window ineligible.
- One hypothetical position per asset. Freeze entry mean/SD for exits: first subsequent
  observed `abs(z) <= 0.5`, `abs(premium) <= 5 bps`, or observed holding time >= 60 minutes.
  Snapshot timing can overshoot the cap by up to 10 minutes; print actual duration. No claim
  that the observed exit quote was available at exactly 60 minutes.
- Missing/invalid quote or >10-minute gap while holding censors the path; end-of-tape
  positions are censored explicitly. Count and print every censored signal. No re-entry at
  the exit/censor row. Do not remove bad rows first and bridge gaps across them.
- Compute `direction * (exit_price / entry_price - 1) * 10,000` bps per entry notional.
  Mid-to-mid diagnostic; same-side entry/opposite-side exit **instant maker fill scenario**;
  opposite-side entry/same-side exit taker quote scenario. No mark-price substitution for fills.
- Historical fee sensitivities: maker 2 bps/leg and taker 12 bps/leg, each charged on its own
  leg's notional, expressed in entry-notional bps. They are not verified current account fees.
  Funding remains missing, never imputed zero; `net_pnl_bps` must remain null.
- Seed 20260912 gives a random-direction control at identical entry/exit timestamps and
  corresponding same-side quotes. Exit timing remains treatment-selected, so this is a
  paired attribution diagnostic, not an independently tradable control strategy.
- Print per-asset and entry-day means, counts, exclusions and nominal 60-second snapshot
  coverage over the full four-day window. Do not pool BTC/ETH into an apparent independent
  sample. Stored timestamps cannot prove upstream quotes were fresh.

## Pre-registered census decisions

These are **spending screens**, not significance tests or Experiment OS lifecycle gates.

| Check | Rule | Consequence |
|---|---|---|
| C1: interpretable paths | Each asset >=30 complete positions, >=3 UTC entry dates, <=10% of signals censored | Otherwise HOLD; thin evidence is not a negative result |
| C2: adverse price evidence | C1 met and zero-fee instant-maker mean <=0 in **both** assets | KILL_LEANING for this fixed candidate; stop building it, no wider family claim |
| C3: reason to investigate execution | C1 met; each asset's maker historical-fee sensitivity >0 and maker gross exceeds its random-direction control | HOLD with price screen surviving; funding/fees/fills must be measured next |
| Mixed evidence | C1 met, neither C2 nor C3 | HOLD; no post-hoc asset selection or threshold sweep |

There is **no PROMOTE/PASS path**. Instant fills are optimistic for these fixed signals,
but not a mathematical upper bound for every possible maker fill-selection policy. Three
days cannot establish statistical confidence, so no bootstrap confidence claim is made.

## If the price screen survives

Check actual market funding rates (the historical market-rate endpoint, not an empty
account-payment ledger), account fee tier and synchronized trade/book availability. Only
then freeze a new prospective Experiment OS contract with passive fill/queue rules,
cancel deadlines, adverse selection, emergency taker exits, exact funding assessment
cash flows, missing-data HOLD rules, and an independently executable matched control.
Size the prospective window from day-cluster variance and a power calculation; the earlier
28-day/300-position suggestion is a starting floor, not proof of adequate power.

Do not restart the old collector merely to repeat its cadence/coverage limitations. Do not
create a paper book until the new measurement is testable and properly registered.

## Cost, capacity and correlation

One bounded historical DB read; no new service, collector or paid data. Profitability and
$100/month capacity are unestablished. BTC/ETH share crypto beta and can lose together;
an outright reversion position is not delta-neutral. Per-leg fees, partial fills, funding,
liquidation/margin constraints and executable position sizes remain prospective questions.

## Run and result

Run only after the approved PR merges to default:

```json
{"type":"script","name":"passive_perp_probe","args":[],"id":"passive-perp-20260912-1"}
```

Read `ops/results/passive-perp-20260912-1.txt`. Record code SHA, dataset hash and census
verdict here and in the journal/scorecard. Reset only this session's own request to noop.

## Results — 2026-09-12

Executed at 19:12:28 UTC on merged code `f19e6d2620c32e911f1fba422fe8b10304b46ce0`
([implementation PR #397](https://github.com/50thycal/kalshi_bot/pull/397)).
[Immutable request-ID output](https://github.com/50thycal/kalshi_bot/blob/ops/ops/results/passive-perp-20260912-1.txt).
Dataset SHA-256: `59b6bc01f5c417b7985d967bf064f5634ece43e36cf7def4efdb85e69e824f3d`.

| Measurement (bps per entry notional unless noted) | BTC | ETH |
|---|---:|---:|
| Complete hypothetical positions | 49 | 44 |
| Censored positions | 0 | 0 |
| UTC entry dates | 4 | 4 |
| Retained snapshots | 1,404 | 1,404 |
| Nominal 60-second coverage over frozen four-day window | 24.375% | 24.375% |
| Premium convergence (not outright P&L) | +6.7618 | +8.6218 |
| Mid-price return | -1.3649 | +3.2972 |
| Instant-maker quote return, zero fees/funding | -0.8360 | +4.0482 |
| Same maker scenario, historical 2 bps/leg fee, **before funding** | **-4.8359** | **+0.0482** |
| Random-direction maker control, zero fees/funding | +0.9253 | +4.7067 |
| Taker quote return, historical 12 bps/leg fee, before funding | -25.8931 | -21.4536 |

C1 meets its descriptive path floor; 40 candidate windows were excluded for gaps.
C2 does not fire because ETH's zero-fee maker mean is positive. C3 fails: BTC loses
after the fee sensitivity and **both assets trail their matched control**. The frozen
verdict is therefore **HOLD**, not a family-wide KILL and not a surviving price screen.
No net P&L was reported: funding is unmeasured and maker fills remain unproven.

The premium/price distinction is material, not cosmetic: the positive premium changes
coexist with negative BTC outright returns. ETH's +0.0482 bps before funding is effectively
flat under an already optimistic fill scenario; it does not justify picking ETH alone.
Daily results in the raw output also show sensitivity to the first partial date. This is
context only, not a new exclusion or decision rule.

**Disposition:** bounded census complete. Do not build a collector/paper book, select a
winning subset or sweep parameters on this tape. Revisit only with a mechanically distinct
premise and a new pre-registration, or independently sourced prospective evidence with
funding and credible execution measurement. Historical PERP-V1 state is unchanged.
The session reset its own ops request to noop after reading the result.
