# LIMM paper strategies S1–S4: pre-registration

**Pre-registered 2026-11-08, before the arms record a single hour.** Owner approval: DEC-043
and DEC-044 (`docs/DECISIONS.md`). Build plan: `docs/handoffs/HANDOFF-limm-paper-strategies.md`.
Experiment OS: `limm-paper-strategies` (PROBE, tagless; `kalshi_bot/experiment_os/limm_paper_strategies.py`).

**What it decides:** where and how an incentive bid should rest, as up to four **nominations**
to the owner. It changes nothing live. `Alimm1` stays stood down (thesis §9.78); a restart, a
rule change and any live bid above $10 are owner decisions and hard stops.

## 1. Instrument

- The shadow collector (`kalshi_bot/liquidity_incentive/collector.py`) records the arms
  (`paper_arms.py`): one-sided simulated bids, GET-only client, `incentive_*` tables only.
- One quote row is about one bid-hour (refreshed hourly; re-placed on a price move; ended with a
  reason when pulled). Each row has outcomes under four fill models — `conservative`,
  `queue_aware`, `optimistic` and `whole_order` (queue-aware timing, the first trade that reaches
  us fills the whole order; 24 of 30 large live fills took 100%) — and an `arm_hour` event with
  the hour's share of the field, qualifying seconds and thin-rule seconds.
- Fills get 1 h and 24 h markouts and settlement.
- **Switch:** `LIQUIDITY_INCENTIVE_PAPER_ARMS_ENABLED` (paper only, ops-allowlisted). Off by
  default; set on only after this document is merged and the Experiment OS receipt is in.

## 2. Arms (frozen in `paper_arms.ArmRules`)

| Strategy | Arm | Universe | Placement and size |
|---|---|---|---|
| S1 money-finder | `S1` | the highest pool-per-day programmes | best bid; $20 where the pool is ≥ $50/day, else $10; ≤ 2,000 contracts |
| S2 at-best vs behind | `S2-best` | the ranker's measure: highest $10 reward per hour | best bid; $10; ≤ 1,000 contracts |
| | `S2-behind` (control) | same markets, same hours | one tick behind (1¢ stays 1¢); $10 |
| S3 release-aware | `S3-aside` | series ending `EMP` (BLS), starting `KXAAAGAS`, and `KXASPENMTNOPEN`, `KXBIGSKYOPEN` | best bid; $10; pulled from 2 h before to 48 h after the BLS release of **2026-11-20 15:00Z**; pulled 12–16Z daily on gas; on a one-event ladder pulled for good once our side falls to 1¢ after being higher, and in the last 3 days |
| | `S3-control` (control) | same markets, same hours | best bid; $10; never pulled |
| S4 burst | `S4-burst` | the $1-minimum band: a $10 bid's projected calibrated reward to the step-aside is ≥ $0.30 and < $1.00 | best bid; **$50** (≤ 5,000 contracts) until the arm's accrued calibrated reward in that programme reaches **$1.25** ($1 + $0.25 margin); timing and after-burst in §3 |
| | `S4-flat` (control) | same markets, same hours | best bid; $10 for the whole programme |

Common to every arm:

- **Side:** the cheap side (best bid 1–10¢) that qualifies with our $10 in and earns the most;
  kept while it stays 1–10¢.
- **Selection:** hourly and sticky; a better market never evicts a selected one. Caps: S1 20
  markets, S2/S3/S4 10 each, at most 3 per event. Paired arms share one selection.
- **Step-aside:** every arm leaves 48 h before close or at the programme's end.
- **Thin rule (study 3):** an hour where we would be more than 50% of our side earns nothing; it
  is logged (`seconds_thin`) and reported, never hidden.
- **Watch pins:** while the arms are on, the collector also keeps every selected market, the
  top 40 S3-universe markets by daily pay, and the 30 newest thin programmes (total pool
  $2–$50 per market, ≥ 24 h of pay left) beyond its 150-market daily-pay cap. Without them
  S3's ladders and S4's band are not watched at all (checked after DEC-043.2's deploy).

## 3. S4 timing (from the programme-age study, fixed before S4 records)

The programme-age study ran once (`rl-age-run1-1108`): **NOT-EARLY**. Within the same
programmes, the first 24 h paid 1.23× the 3 d+ hours per contract (95% CI 0.85–1.63; bar 2×).
Late hours still pay $0.31 per $10 bid-day.

- **Burst window: none.** S4-burst bids $50 from the hour S4 selects the programme until its
  accrued calibrated reward reaches $1.25.
- **After the burst: `flat`.** It drops to $10 for the rest of the programme, so after the
  threshold the two S4 arms rest the same bid. The S4 comparison is therefore the burst's own
  contribution: the $1 minimum cleared earlier and more often, against the extra fill risk of
  $50 resting.

## 4. Calibration (fixed now)

Reward = `scoring.estimate` × **1.34**, the global median paid ÷ estimated
(`scripts/limm_estimate_calibration.py`, run `rl-ps-calib-1108`, 17 well-covered paid events),
for **every arm**. **It is never changed after data.**

*Correction, 2026-11-08 15:50Z (arms recording since 15:41Z, the read window opens 11-09; no
outcome had been read).* This section first said no market type had 10 well-covered paid events
("mention 9"). That miscount came from a truncated listing. Mention events have exactly 10, with
a median of 2.78 (ranker run `rl-rank-c8-1108`). The pre-registered number does not change. The
handoff allows "the global 1.34, or the per-type factor known on the day". The global factor is
kept for every arm because it is the conservative reading: mention ratios run from 0.8 to 14.4×,
and one figure for all arms keeps the arms comparable. Only the reason was wrong.

## 5. Read (once)

- **Window:** quotes placed from **2026-11-09T00:00Z**. **Read not before 2026-11-24T00:00Z**
  (the later of 11-24 and 14 days after recording starts). The read script refuses earlier.
- **Command:** `{"type":"script","name":"limm_paper_strategies_read","args":[],"id":"rl-ps-read-<date>"}`.
  Before then only `--counts` (rows, markets, bid-hours per arm).
- **Per arm and fill model:** calibrated reward with **Kalshi's $1 minimum per market per
  programme** (a programme under $1 counts $0; programmes still running at the read are counted
  as accrued); fill rate per bid-hour; fill cost at 24 h (the 24 h mark, else the settlement,
  else half the price — study 5's rule) and at settlement; **net per $10 bid-day** (an arm's
  size ÷ $10 × hours rested ÷ 24) and per $ of capital-day, event-clustered SE
  (`ratio_cluster`); paid ¢/h against fill ¢/h (ranker cycle 8's yardstick).
- **Pairwise:** `S2-best − S2-behind`, `S3-aside − S3-control`, `S4-burst − S4-flat`, per
  event, over the **control's** bid-days (a pull arm rests fewer hours; its pulled hours earn
  zero). For S4 also the share of programmes that clear $1.

## 6. Bars (fixed now)

- **PROMISING:** net ≥ **$0.15 per $10 bid-day** under the **conservative** fill model, with a
  95% lower bound above 0, **and** the **whole-order** model does not turn it into a loss
  (else **FRAGILE**).
- **NOT-PROMISING:** the floors are met and PROMISING is not.
- **Floors:** 100 $10-bid-days and 10 events per arm, else **HOLD** (accrual) with one re-read
  two weeks later (~2026-12-08).
- **Pairwise DECIDES** when its 95% interval excludes 0 (A-BETTER / B-BETTER), else UNDECIDED.
- Study 5 (reads 11-13) and these arms are separate questions: its result fills the ranker's
  fill-cost column and moves none of these bars.

## 7. No peeking

No arm's reward, fill cost, net or P&L is computed before the read date. Counts (rows, markets,
bid-hours, selections) are fine and are what the check-ins look at.

## 8. What a result does

- PROMISING → one plain question to the owner naming the arm, its net and its risk. Any live use
  is a new experiment or epoch, sized at most $10 a bid unless the owner says otherwise (hard
  stop).
- NOT-PROMISING → recorded; that placement or universe is not worth live money.
- A pairwise A-BETTER/B-BETTER → recorded as the answer to that arm pair's question.

## 9. Census (handoff §5 step 0)

Run `rl-age-census-1108` (2026-11-08, counts only; `LIMM_PROGRAMME_AGE.md` §6). Programmes
the collector observed whose $10 projected calibrated reward lands in the $0.30–$1.00 band, by
start week: **W42 15, W43 1, W44 31, W45 45** (W45 partial), median length 168 h, mostly
weekly and monthly data ladders. About 10 or more a week are visible in most weeks, so S4 is
not expected to HOLD for lack of a universe. The thin-pool funnel ($2–$50 per market) is
dominated by 15-minute and hourly programmes, which the pins' "≥ 24 h of pay left" rule leaves
out. The band is not widened after seeing results.

## 10. Result

*(Filled in once, at the read.)*
