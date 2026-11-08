# LIMM programme-age study: does a programme pay more per contract in its first hours?

**Pre-registered 2026-11-08, before any read.** One read of existing history, run once
(`scripts/limm_programme_age.py`, ops-allowlisted). Owner approval: DEC-044.2. Build plan:
`docs/handoffs/HANDOFF-limm-paper-strategies.md` §3b. Lead 2 of
`docs/liquidity_incentive/HANDOFF_2026-11-08.md`; thesis §9.42.

**What it decides:** S4-burst's timing, recorded in `docs/LIMM_PAPER_STRATEGIES.md` before S4
records. It decides nothing about the live book.

## 1. Question

Does a liquidity programme pay more per contract in its first 24 hours than after 3 days, while
fills per resting market-hour are no higher?

## 2. Data

- **Hours:** `incentive_market_snapshots`, the last valid snapshot per market per clock hour,
  joined to its programme row, from **2026-09-08T00:00Z to the run time** (the collector's 60-day
  history; it was down 10-07 → 10-30, which simply has no hours).
- **A qualifying hour** is study 3's (`LIMM_PROGRAMME_OUTCOMES.md` §3, code imported unchanged):
  both sides at Target Size, liquidity programme active, ≥ 48 h before close, cheap side 1–10¢.
- **Programme** = (market ticker, programme start). **Age** = hour − programme start.
- **Buckets:** 0–6 h, 6–24 h, 1–3 d, 3 d+. **Early** = 0–24 h. **Late** = 3 d+.

## 3. Measures

- **Reward per contract-hour** for a **$10** bid at the cheap side's best bid:
  `pool/hour × q/(field + q) / 2 ÷ q`, with `q = min(1000, ⌊1000/price⌋)` — study 3's estimator.
  An hour where q would be over 50% of the side (study 3's thin rule) earns 0 and is counted.
- **Fill rate per resting market-hour:** the $10 bid placed on hour *h* rests through hour *h*+1
  and is replayed against that hour's public prints (`ShadowLeg`, the **conservative** model:
  depth at best is ahead of us). Rate = hours with any fill ÷ resting hours.
  - **Only hours before 2026-10-30T16:00Z are used for fills.** Study 5
    (`LIMM_FILL_HAZARD.md`) pre-registered that no simulated fill in 10-30 → 11-13 is read before
    its own read on 11-13. Reward per contract-hour is market data (book and pool), not a study
    outcome, and uses the whole window.
- **Paid ÷ estimated by age (descriptive).** For the live book's events with a confirmed payout
  (`reward_ledger.CONFIRMED_REWARD_PAYOUTS`), the calibration script's integration
  (`scripts/limm_estimate_calibration.py`) is split by the age of each resting segment. Events
  with ≥ 80% of their estimate in the early window are compared with those with ≥ 80% late. Shown,
  not part of the verdict: there are too few events to decide on.

## 4. The bar (fixed now)

- **Primary statistic:** over programmes observed in **both** the early and the late window
  (within-programme, so the mix of programmes cancels), `R = Σ mean_early ÷ Σ mean_late` of
  reward per contract-hour, each programme weighted equally.
- **Uncertainty:** event-clustered bootstrap, 2,000 resamples of events, seed 20261108, 95%
  percentile interval.
- **Floors:** at least **10 paired programmes in 5 events**. Below that: **INSUFFICIENT**.
- **EARLY-PAYS** if `R ≥ 2.0`, the interval's lower bound is above 1.0, **and** the early fill
  rate is no higher than the late fill rate (point estimates; equal counts as no higher).
- **NOT-EARLY** otherwise.
- **Late hours still pay** if the late bucket's mean calibrated reward per $10 bid-day
  (`reward per contract-hour × q × 24 × 1.34`, over all late hours) is **≥ $0.15** (the paper
  strategies' PROMISING bar).

## 5. What the verdict sets (S4, fixed now)

| Verdict | `s4_burst_window_hours` | After the burst |
|---|---|---|
| EARLY-PAYS | 24: S4 enters only programmes under 24 h old, bursts in their first 24 h | `flat` ($10) if late hours still pay, else `off` |
| NOT-EARLY or INSUFFICIENT | none: S4 bursts until the $1 threshold whenever it enters | `flat` if late hours still pay, else `off` |

## 6. Census (counts only, same script, `--census`)

Handoff §5 step 0. For programmes starting in the last 28 days that the collector observed:
the projected calibrated reward of a $10 bid to the step-aside (mean qualifying-hour reward ×
paying hours × 1.34). Counted per week: programmes, how many fall in the **$1-minimum band**
(≥ $0.30 and < $1.00), their series and median length. Also the count of all liquidity
programmes per week, watched or not, how many are short (< 72 h), and how many have a thin
total pool of $2–$50 per market, by series (the funnel S4's collector pins draw from, since the
collector's daily-pay ranking does not watch thin programmes). No outcome is computed.
If fewer than about 10 band programmes a week are visible, S4 runs on whatever qualifies and its
pre-registration says HOLD is likely. The band is not widened after seeing results.

## 7. Limits (stated now)

- The collector watched markets by total reward until 2026-11-08 (by daily pay after), so
  programmes whose first hours were not tracked are missing from the paired set.
- Hourly snapshots; the field score is the collector's full-book score at that hour.
- The fill comparison uses older history (Sep–early Oct) only, so it is thinner than the reward
  comparison.

## 8. Command

```
{"type":"script","name":"limm_programme_age","args":[],"id":"rl-age-run1-<date>"}
{"type":"script","name":"limm_programme_age","args":["--census"],"id":"rl-age-census-<date>"}
```

## 9. Result

*(Filled in once, after the single run. Never re-run to a different answer.)*
