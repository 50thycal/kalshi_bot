# LIMM S2 live test — pre-registration

**Pre-registered 2026-11-14, before any live data and before the 2026-11-24 paper read.** Owner
request: 2026-11-14 ("rest a $10 bid at the best bid", about 5 markets from S2's selection, 2
weeks, separate from `Alimm1`, judged on confirmed payouts minus fill losses). Rules in code:
`kalshi_bot/liquidity_incentive/s2_live.py`. Experiment OS contract:
`kalshi_bot/experiment_os/limm_s2_live.py` (`limm-s2-live`, not yet registered — §9). Live Ops
steps: `docs/handoffs/HANDOFF-limm-s2-live-test.md`.

**Nothing here is switched on.** The test starts only when **both**: (a) the 2026-11-24 paper
read (`LIMM_PAPER_STRATEGIES.md` §5–§6) labels `S2-best` **PROMISING**, and (b) the owner says
go, by name, for this test. Arming, `LIVE_STRATEGIES` and every limit going live are Live Ops
acts and hard stops.

## 1. Why, and what it measures first

- The paper arm `S2-best` and its backtest look strong: run 2 (descriptive) gave +$1.86 per $10
  bid-day (SE 0.38, 183 events); whole-order fills still +$1.75 (`LIMM_PAPER_STRATEGIES_BACKTEST.md` §7).
- That number rests on one assumption: Kalshi pays **1.34×** the collector's estimate. The 1.34
  was measured only on mention and YouTube payouts (17 events). S2 picks gas, ski and data
  ladders, where **no payout has ever been checked**.
- The old live book lost money overall: about $114 in rewards against $146 in fill losses.
  Study 5 found the live universe at −$0.11 per market-day, and KXRT at −$0.39
  (`LIMM_FILL_HAZARD.md` §8).

So the test's **first job is to measure real paid ÷ estimated on S2's markets**. Making money is
second.

## 2. Markets (selection rule)

Each hour, from the paper arm `S2-best`'s **open** quote rows (`incentive_shadow_quotes`,
`policy = 'S2-best'`, `ended_at` null):

1. Keep markets already selected while the paper arm still rests there (sticky; a better market
   never evicts one).
2. Fill free slots by the paper arm's reward rate, highest first, until **5 markets**:
   - **at most 1 market per event**;
   - a $10 bid must project **≥ $1.50** calibrated (rate × hours to step-aside × 1.34). Kalshi
     pays nothing under $1 per market per programme; $1.50 is the same floor `Alimm1` used
     (§9.77). The 1.34 here only ranks markets; the test measures the real ratio;
   - never a market the account holds under any tag;
   - never a market this book has already filled on;
   - never `KXYTVIEWSHIGH` (owner's YouTube pause, thesis §9.73, still binding).
3. A market leaves when the paper arm leaves it (48 h before close, programme end, side above
   10¢), or when it fills.

One market per event has a second use: Kalshi's Rewards page lists payouts by event, so each
payout maps to exactly one of our markets.

## 3. Size, placement, fills

| | |
|---|---|
| Bid | **$10** of contracts at the paper arm's own side and price (the best bid, cheap side 1–10¢; ≤ 1,000 contracts) |
| Markets | ≤ 5 resting at once; **≤ $50 resting** |
| Fills | **held to settlement**. No stop-loss, take-profit or resting sell (the paper arm's fill cost assumes a held fill) |
| Re-entry | a market that filled is not re-entered during the test |
| Duration | **14 days** from arming |
| Tags | fresh: live `As2best1`, twin `As2best1_pt1` (paper tag `s2best1`). Nothing shared with `Alimm1` |

## 4. Twin and comparison

- **Paper twin at the same instant:** the paper arm `S2-best` itself. It rests on exactly the
  same markets in the same hours (the live book reads its selection), with simulated fills under
  four models and the calibrated reward. Live vs paper on the same markets gives: live fills vs
  simulated fills, and real paid vs estimated.
- The mandatory Experiment OS twin (`As2best1_pt1`) is registered at the arming instant as the
  system requires. Its `paper_trades` P&L does not measure an incentive book (XOS-000042) and is
  not read for any bar.

## 5. Stop rules

- **Loss stop:** realized fill losses plus held fills marked at the best bid reach **$30**
  (gains do not offset it). Live Ops stands the book down (drop `As2best1` from
  `LIVE_STRATEGIES`) and tells the owner. Experiment OS's keep gate `s2_live_loss_stop` fails on
  the realized part at −$30 too.
- **Envelope breach:** one settled market losing more than **$10.50** means the $10 sizing is
  not being applied: stand down.
- **Duration:** stand down at 14 days.
- **Worst case before the stop acts:** $30 + all $50 resting filling at once and going to zero =
  **$80**. Fills so far have come one market at a time.
- The account-wide breakers (`MAX_DAILY_LOSS`, `MAX_TOTAL_EXPOSURE`) stay as they are and still
  apply.

## 6. How payouts are confirmed

Kalshi has no payout API. A payout counts **only** when it is on the owner's Rewards page:

1. During and after the test, the owner sends Rewards screenshots (at least weekly, and once 7
   days after the stop).
2. Each payout is appended to `reward_ledger.CONFIRMED_REWARD_PAYOUTS` (date, cents, event), as
   for `Alimm1`.
3. A payout is this book's when its event is one this book rested in during that programme.
   With one market per event, and `Alimm1` stood down, that match is one-to-one. Anything else
   is listed as unattributed and not counted.

**Estimate:** the runner logs the collector's uncalibrated `scoring.estimate` for each live bid
each hour it rests (as `Alimm1`'s runner does). "Estimated" below is the sum over the test, per
programme.

## 7. The read (once) and the bars

**When:** 7 days after the book stops (payouts land after programmes end). Held fills not yet
settled are marked at the best bid on that day.

**Primary — net:** confirmed payouts − fill losses (realized + marked), over the test.

**Secondary — ratio:** confirmed paid ÷ uncalibrated estimate, pooled and per programme.
- **HOLDS** ≥ 1.00 (at least 75% of the 1.34 the paper arms assume)
- **WEAK** 0.50–1.00
- **FAILS** < 0.50 (the paper reward level is not real on these markets)

**Label** (`s2_live.read_labels`):

| Label | Rule | What it means — every one is an owner decision |
|---|---|---|
| **STOP** | net ≤ $0, or the loss stop fired | Stop. S2's paper numbers are not trusted for live money. |
| **EXTEND** | net > $0 but under 40 live $10 bid-days or 4 programmes | Too little to judge. Owner may run 2 more weeks, same size. |
| **SCALE-CANDIDATE** | net ≥ **$20** and ratio **HOLDS** | Owner may scale (more markets, or $20 bids) as a new epoch. |
| **KEEP** | anything else (net > $0) | Keep the same size for another 2 weeks; do not scale. |

The per-programme ratios are reported with the label. They also say whether 1.34 should be
replaced for gas, ski and data ladders in the paper arms, which is a new Version there, not an
edit.

For scale: if the paper arm's +$1.86 per $10 bid-day were real, 5 markets × 14 days = 70
bid-days ≈ $130. The $20 bar is deliberately far below that.

## 8. No re-scoping

No bar, size, market rule or stop changes after the test starts. A change is a new Version or
epoch in Experiment OS. The 1.34 used to rank markets stays 1.34 during the test whatever the
measured ratio says.

## 9. Experiment OS

- Package `limm-s2-live`: one arm (`s2best_live`), envelope from `s2_live.S2LiveRules`, three
  gates: `s2_instrument_ready` (PROBE→PAPER), `s2_paper_to_live` (PAPER→LIVE_CANARY), both
  instrument health only, and `s2_live_loss_stop` (kill, live, the loss side).
- **No `arm` verb in this package.** It ships with the runner change (handoff Build B), which the
  owner approves at merge.
- **Not registered yet (decision, 2026-11-14).** `package-preflight` was run dry after the merge:
  ops `rl-s2live-pf-1114` (code `79f0050`), **GO** on every check (package known, fresh
  registration, complete snapshot, no tag collisions, transport idle). The envelope was **not**
  sent. Registration
  waits for the 11-24 read: if S2-best is not PROMISING, nothing should exist to retire. Also,
  two pending Platform Revisions already list `limm-paper-strategies` as unaccounted (Control
  Tower `rl-s2live-ct-1114`), and a new experiment would add one more. The probe gate needs about
  an hour of evidence, so registering after the read costs nothing.

## 10. Result

*(Filled in once, at the read.)*
