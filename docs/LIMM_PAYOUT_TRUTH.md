# LIMM payout truth: can the balance ledger replace the Rewards screenshots?

**Status:** READ 2026-11-14 (§8): **FAIL** — recall 100%, precision 55%; screenshots stay. PRE-REGISTERED 2026-11-14 (Research Lab), before any residual row was read for
this study. Owner lock: 2026-11-14 ("do one and two" on
[`IDEA_MODEL_20261114_BRAINSTORM.md`](IDEA_MODEL_20261114_BRAINSTORM.md), idea #3 there).
Read once, as soon as this document is on the branch. Experiment OS is canonical; this study is
read-only, changes nothing live, and decides nothing on its own. A PASS is a **nomination**.

## 1. Question

Every incentive reward we count comes from an owner screenshot of Kalshi's Rewards page
(`reward_ledger.CONFIRMED_REWARD_PAYOUTS`, 45 rows, $113.86, through 2026-11-09). The S2 live test after
11-24 will rest on ladders whose pay we have never confirmed, and screenshots do not scale.

> Does a fixed, screenshot-free rule on the balance ledger (`incentive_balance_observations`)
> recover the confirmed payouts, without counting cash Kalshi never paid as a reward?

## 2. Data (fixed now)

- **Rows:** `incentive_balance_observations`, trusted only (`residual_cents` not null, and
  `notes_json.residual_untrustworthy` not set), from the first row to **2026-11-10T23:59:59Z**
  (the last screenshot covers payouts dated 11-09; ± 1 day).
- **Ground truth:** `CONFIRMED_REWARD_PAYOUTS` at this commit (45 rows). A residual is
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

**Read once 2026-11-14T23:40Z.** Ops `pt-res-1114a` (residuals ≥ 100¢ to 11-10) and
`pt-ends-1114a` (programme ends on markets `Alimm1` ordered on), code `290376d`; analysed by
`tools/review/limm/payout_truth.py` at this commit. No residual row was read before §1–§5 were
pushed (commit `110c5f2`). The only edit after that commit corrects the ground-truth count
from 41 to 45 rows (a counting slip; no rule or bar changed).

```
trusted residuals >= 100c to 2026-11-10: 25; programme ends: 593
confirmed payouts: 45 rows, $113.86; seen as confirmed steps: 16 steps, $113.86 -> coverage 100.0%

at                         cents  confirmed  R-200  R-25
2026-09-25 14:44:21Z      100          -      -      -
2026-09-25 15:52:28Z      100          -      -      -
2026-09-25 21:27:54Z     4999          -    yes      -
2026-09-26 02:07:12Z     1100          -      -      -
2026-09-26 02:23:15Z      400          -      -      -
2026-09-26 02:40:34Z     1201          -    yes    yes
2026-09-29 10:35:09Z      221        yes    yes    yes
2026-10-06 21:32:33Z      275          -    yes    yes
2026-10-10 10:26:26Z      209        yes    yes    yes
2026-10-11 10:38:52Z      330        yes    yes    yes
2026-10-14 10:29:41Z      331        yes    yes    yes
2026-10-14 15:52:36Z     2940          -    yes      -
2026-10-15 15:58:15Z    14700          -      -      -
2026-10-17 10:43:14Z      204        yes    yes    yes
2026-10-18 10:36:07Z      170        yes    yes    yes
2026-10-20 10:40:23Z     3173        yes    yes      -
2026-10-22 17:04:34Z      796        yes    yes    yes
2026-10-23 10:58:59Z     1115        yes    yes    yes
2026-10-25 10:41:05Z      182        yes    yes    yes
2026-10-26 10:34:44Z      440        yes    yes    yes
2026-10-28 10:29:43Z      736        yes    yes    yes
2026-10-29 10:43:20Z      685        yes    yes    yes
2026-11-03 11:46:34Z     1803        yes    yes    yes
2026-11-08 11:52:27Z      431        yes    yes    yes
2026-11-09 11:48:05Z      560        yes    yes    yes

R-200 (deciding): positive 20 steps $208.01; recall 100.0%; precision 54.7%
    counted, not paid: 2026-09-25 21:27Z 4999c
    counted, not paid: 2026-09-26 02:40Z 1201c
    counted, not paid: 2026-10-06 21:32Z 275c
    counted, not paid: 2026-10-14 15:52Z 2940c
    step 2026-09-29 10:35Z 221c: programmes ending in window: other
    step 2026-10-10 10:26Z 209c: programmes ending in window: mention,other
    step 2026-10-11 10:38Z 330c: programmes ending in window: mention,other
    step 2026-10-14 10:29Z 331c: programmes ending in window: mention,other
    step 2026-10-17 10:43Z 204c: programmes ending in window: other
    step 2026-10-18 10:36Z 170c: programmes ending in window: mention,other
    step 2026-10-20 10:40Z 3173c: programmes ending in window: mention,other
    step 2026-10-22 17:04Z 796c: programmes ending in window: other,youtube
    step 2026-10-23 10:58Z 1115c: programmes ending in window: mention,other,youtube
    step 2026-10-25 10:41Z 182c: programmes ending in window: mention,other
    step 2026-10-26 10:34Z 440c: programmes ending in window: mention,other
    step 2026-10-28 10:29Z 736c: programmes ending in window: mention,other
    step 2026-10-29 10:43Z 685c: programmes ending in window: mention,other
    step 2026-11-03 11:46Z 1803c: programmes ending in window: mention,other,youtube
    step 2026-11-08 11:52Z 431c: programmes ending in window: other,youtube
    step 2026-11-09 11:48Z 560c: programmes ending in window: youtube
    single-type steps: 3 of 16
R-25 (reference): positive 17 steps $96.89; recall 72.1%; precision 84.8%
    counted, not paid: 2026-09-26 02:40Z 1201c
    counted, not paid: 2026-10-06 21:32Z 275c
    paid, not counted: 2026-10-20 10:40Z 3173c

VERDICT (R-200): FAIL
```

**Verdict: `FAIL`. The screenshots stay the only source.**

- **The ledger sees every payout.** Coverage is 100%: all 45 confirmed payouts ($113.86) appear
  as 16 balance steps. Recall is also 100% under R-200.
- **The rule cannot tell a payout from other cash.** Four steps that Kalshi never paid as
  rewards pass it: $49.99 (09-25), $12.01 (09-26), $2.75 (10-06) and $29.40 (10-14). They are
  $94.15 of $208.01 counted, so precision is 54.7%. The $25 cap (R-25) removes two of them but
  misses the biggest real payout (10-20, $31.73), so it fails too (recall 72.1%).
- **Per-type calibration from the ledger alone is mostly impossible.** Only 3 of the 16 real
  steps follow programmes of a single market type, because Kalshi pays several programmes in
  one step.

**Observation only (post hoc, decides nothing):** 15 of the 16 real payout steps landed between
10:26Z and 11:52Z, and the other at 17:04Z. None of the four false steps did (21:27Z, 02:40Z,
21:32Z, 15:52Z). A time-of-day rule would score perfectly on this sample, but it was found by
looking at this sample. It can only be tested forward, under a fresh pre-registration, on the S2
live test's first screenshots.

**What it means.** For the S2 live test, the dashboard keeps counting a reward only when a
screenshot confirms it. The live test needs a screenshot after each payout day, or a way to tag
the non-reward cash. Nothing changes live.
