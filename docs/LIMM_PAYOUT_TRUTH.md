# LIMM payout truth: can the balance ledger replace the Rewards screenshots?

**Status:** PRE-REGISTERED 2026-11-14 (Research Lab), before any residual row was read for
this study. Owner lock: 2026-11-14 ("do one and two" on
[`IDEA_MODEL_20261114_BRAINSTORM.md`](IDEA_MODEL_20261114_BRAINSTORM.md), idea #3 there).
Read once, as soon as this document is on the branch. Experiment OS is canonical; this study is
read-only, changes nothing live, and decides nothing on its own. A PASS is a **nomination**.

## 1. Question

Every incentive reward we count comes from an owner screenshot of Kalshi's Rewards page
(`reward_ledger.CONFIRMED_REWARD_PAYOUTS`, 41 rows through 2026-11-09). The S2 live test after
11-24 will rest on ladders whose pay we have never confirmed, and screenshots do not scale.

> Does a fixed, screenshot-free rule on the balance ledger (`incentive_balance_observations`)
> recover the confirmed payouts, without counting cash Kalshi never paid as a reward?

## 2. Data (fixed now)

- **Rows:** `incentive_balance_observations`, trusted only (`residual_cents` not null, and
  `notes_json.residual_untrustworthy` not set), from the first row to **2026-11-10T23:59:59Z**
  (the last screenshot covers payouts dated 11-09; ± 1 day).
- **Ground truth:** `CONFIRMED_REWARD_PAYOUTS` at this commit (41 rows). A residual is
  **confirmed** when `reward_ledger.confirmed_reward(cents, at)` is true (exact cents, UTC date
  ± 1 day, or a same-date total), the dashboard's own test.
- **Programme ends:** end times of liquidity programmes (`incentive_programs`, type
  `liquidity`) on every market `Alimm1` placed a live order on.

## 3. Rules (frozen)

- **R-200 (deciding):** a residual is a reward when it is ≥ 100¢ and ≤ 20,000¢, is not a whole
  number of dollars, and lands 0–24 h after the end of a programme in §2. This is the §9.49
  rule with the cap raised from $25 to $200, because a ten-bid S2 book can be paid more than
  $25 in one step.
- **R-25 (reference only):** the §9.49 rule as coded today (`attributable_reward`, cap $25).

## 4. Measures

- **Coverage:** confirmed dollars that appear as a confirmed residual ÷ all confirmed dollars.
  It says whether the ledger saw the payouts at all.
- **Recall:** confirmed dollars on rule-positive residuals ÷ confirmed dollars on residuals.
- **Precision:** dollars on rule-positive residuals that are confirmed ÷ all rule-positive
  dollars.
- **Descriptive:** for each confirmed rule-positive step, the market types (mention, YouTube,
  other) of the programmes that ended in its 24 h window, and how many steps are single-type.
  Only a single-type step can calibrate one market type without a screenshot.

## 5. Verdicts (R-200 decides)

| verdict | rule | meaning |
|---|---|---|
| `AUTOMATE` | coverage ≥ 90%, recall ≥ 90% and precision ≥ 90% | nominate: the S2 live test and the dashboard count rule-positive residuals as rewards, and screenshots become a periodic audit (owner decision) |
| `ESTIMATE` | recall ≥ 75% and precision ≥ 75%, not `AUTOMATE` | the rule is a running estimate; screenshots still confirm |
| `FAIL` | otherwise | screenshots stay the only source |

## 6. Known limits (fixed now)

- The rule's $1 floor, whole-dollar test and 24 h window were set in October on part of this
  history (§9.49), and the 2026-10-06 $2.75 false positive is in the sample. In-sample
  precision is therefore optimistic. A PASS is read with that caveat and re-checked on the S2
  live test's first screenshots.
- Kalshi pays per programme but the balance moves in steps that can merge several payouts, so
  per-event attribution is not attempted; only steps are scored.
- The history is mostly mention and YouTube programmes. A PASS says nothing yet about how often
  ladders pay, only that the instrument can see it.

## 7. Command

Read-only `db` requests through the ops channel (no runner change), analysed by
`tools/review/limm/payout_truth.py`, which imports the dashboard's own `confirmed_reward`.

## 8. Results

(Filled in once by the run.)
