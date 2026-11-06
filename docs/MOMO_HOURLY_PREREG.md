# MOMO-H — does the hourly BTC ladder under- or over-react to recent price action?

*Pre-registered 2026-11-06 (Research Lab, idea-model run `docs/IDEA_MODEL_20261106.md`),
before any result query was run. Written and committed first so the test cannot be re-scoped
after the numbers are seen.*

## Why this question, and why it is not a revival

The user's idea is "price action" on short windows. The naive version (predict BTC's direction from
its recent candles) is dead on priors: crypto minute returns are close to a random walk, and the
07-11 15-min run killed MOM15 **by argument**. The version tested here asks something different:
**is the Kalshi QUOTE miscalibrated conditional on the recent spot path?** Its target is the market
maker's pricing, not BTC's direction. A maker who prices strikes from a constant or slow volatility
would misprice strikes after a sharp move. The theta tail diagnosis
(`RESEARCH_THETA_TAIL_MODEL_DIAGNOSIS.md` §2.5) lists this as **the one mechanism it could not test**
(“remains untested”, `RESEARCH_THETA_REMEDIATION.md` §7.1). MARKTANGLE's daily crypto
persistence finding did not test it either: that was outcome-on-outcome persistence, not quote calibration.

## Data (existing, no new collection)

`crypto_ladder_snapshots`, series `KXBTCD` (hourly BTC "above strike" ladder), 2026-08-01 to the run
date. That is roughly 24 events per day and about 8 captures per hour. ETH is not captured hourly, so this read is BTC-only.

- **Decision row:** for each (market, event), the snapshot whose `minutes_to_close` is in [20, 40] and
  closest to 30, with mid in [15, 85] (strikes the next 30 minutes can actually decide).
- **Momentum m15:** ln(spot at decision / event spot captured 12–18 min earlier), rebuilt from the
  event's own snapshots, so the whole history counts and not only the 9 days of native
  `trailing_move_15m` telemetry. The native column is used as a cross-check on its window.
- **Outcome Y:** the market's last snapshot with `minutes_to_close` ≤ 2. YES if mid ≥ 97, NO if ≤ 3,
  otherwise excluded as ambiguous. The exclusion rate is reported.
- Quintiles of m15 over the full decision set. The decision instant uses no information from
  after it; the outcome is used only as the label.

## Predictions (fixed)

- **P0 — data floor.** At least 40 distinct days, at least 1,000 decisions, and an ambiguous-outcome
  exclusion of at most 10%. Otherwise **HOLD (instrument)**, not a verdict.
- **P1 — signal.** Define residual = 100·Y − mid. Separation S = mean residual(top m15 quintile) −
  mean residual(bottom quintile). **Signal if |S| ≥ 3.0¢ and the day-block bootstrap 90% CI
  (2,000 resamples) excludes 0.** A positive S means the quote under-reacts (follow momentum); a
  negative S means it over-reacts (fade it). The test is two-sided because both are tradeable, and that is stated here
  in advance.
- **P2 — tradeable.** Implied taker trade, signed by S. For S > 0: buy YES at ask in the top quintile and buy
  NO at (100 − bid) in the bottom quintile. For S < 0, the reverse. **Net EV after fee
  (`ceil(0.07·q·P·(1−P)·100)`, q = 10, per contract) ≥ +1.5¢/contract, pooled across both legs,
  with day-block bootstrap 5th percentile > 0.**
- **KILL** if |S| < 1.5¢, or the CI includes 0. Momentum is then priced on the hourly ladder; close
  the price-action-follow/fade family for this venue and do not spawn threshold variants.
- Between KILL and P1 (1.5¢ ≤ |S| < 3¢ with the CI excluding 0) → **HOLD**, re-read on the native-telemetry
  window once it reaches 40 days (~2026-12-07).

## Amendment A1 — 2026-11-06 ~03:05Z, before any statistic was computed

A plumbing check on one day (`rl-momo-d1-1106g`, 2026-10-15, 101 decision rows) showed that the
final-mid label is often **missing** (no capture with `minutes_to_close` ≤ 2), and a whole-month
SQL read exceeds the ops channel's 30 s statement timeout. No momentum bucket, residual or EV was
computed from that day. Two changes follow, both about the instrument and not the hypothesis:

1. **Label:** the primary label is now the **official Kalshi settlement `result`** for each market
   (public REST, `/markets?event_ticker=`). That is strictly better than a quote proxy. The final-mid rule
   above is kept as a **sensitivity** read and reported beside the primary; it decides nothing.
   P0's "≤ 10% exclusion" applies to the official label (unsettled or unreadable markets).
2. **Instrument:** the read runs as an allowlisted probe, `scripts/momo_hourly_probe.py`, one day
   per query. Decision rows, momentum construction, quintiles, thresholds, bootstrap and fee are
   unchanged. As a reconstruction check, the probe also reports the correlation between the rebuilt m15
   and the native `trailing_move_15m` column where both exist. A correlation below 0.9 marks the read
   **HOLD (instrument)**.

## Amendment A2 — 2026-11-06 ~03:40Z, instrument fix; still no statistic computed

The first ops run (`rl-momo-run-1106m`, code `7734679`) read **0 decision rows on every day** and
returned `HOLD (instrument: P0 data floor)`. Cause, confirmed on production (`rl-momo-coll-1106o`):
the database collates `en_US.utf8`, which ignores punctuation at the first comparison level, so the
day selector `event_ticker >= 'KXBTCD-26OCT15' AND < 'KXBTCD-26OCT15~'` matched nothing (0 rows,
against 5,264 rows for three exact hourly tickers). The local end-to-end check had used a C-collated
database and could not see this. Reproduced locally under an ICU `en-US` database, then fixed by
selecting the day's **24 exact hourly event tickers** (`= ANY(...)`). Verified under both collations.
Nothing about the hypothesis, rows, thresholds, label or bootstrap changed. The 0-row run produced
no statistic, so this amendment is not informed by results.

## Run 2 and amendment A3 — 2026-11-06 ~04:00Z (results-AWARE; disclosed)

**Run 2** (`rl-momo-run2-1106p`, code `9d474c1`, 08-01..11-05) read 8,815 decisions. 2,612 (29.6%)
had **no official label** while the API returned **0 fetch errors**. Verdict per this contract:
**HOLD (instrument: P0)**, not a verdict. Because usable rows exceeded 50, the probe still printed its
statistics, and **they have been seen**. Descriptive only, non-decisive: S = −0.44¢ (90% CI −5.67 to
+4.58), implied fade-trade EV −1.75¢ (p5 −4.24), every quintile residual within ±1.4¢, every taker
leg negative after fee, reconstruction corr 0.957, sensitivity agreement 99.9% (n = 1,594).

**Hypothesis for the missing labels:** markets settled before Kalshi's historical cutoff (about August)
no longer carry `result` on the live `/markets` endpoint. **A3:** re-run on **2026-09-01..11-05** to
(a) test that hypothesis (exclusion should fall to about 0%) and (b) apply the unchanged P0–P2 rule to
the window where labels exist. A3 was written **after** run 2's statistics were seen. It restricts the
window for a data-availability reason, and the seen estimate is already in the KILL zone, so it cannot
rescue a pass. A pass on the restricted window would therefore be treated as suspect. Rows, thresholds,
label and bootstrap are unchanged.

## Run 3: VERDICT KILL (2026-11-06 04:04Z)

`rl-momo-run3-1106q`, code `b7ef1b4`, window 2026-09-01..11-05 (A3). The label hypothesis is
**confirmed**: exclusion fell from 29.6% to **0.16%** (10 of 6,134). Kalshi's docs say live `/markets`
stops returning settled markets older than a moving cutoff (target about 3 months); they move to
`/historical/markets`.

| measure | value | pre-registered bar |
|---|---|---|
| days / decisions | 66 / 5,610 | ≥ 40 / ≥ 1,000 (P0 pass) |
| label exclusion | 0.16% | ≤ 10% (pass) |
| reconstruction corr | 0.957 (n = 5,610) | ≥ 0.9 (pass) |
| **S** (Q5 − Q1 residual) | **−0.67¢**, 90% CI **−5.76 to +4.44** | \|S\| ≥ 3 with CI excluding 0; KILL if \|S\| < 1.5 or CI includes 0 |
| implied fade-trade net EV | −1.63¢ (p5 −4.24) | ≥ +1.5¢ with p5 > 0 |
| sensitivity label | 99.9% agreement (n = 1,571); S = −0.44¢ | decides nothing |

| quintile | n | avg 15-min move | avg quote | YES won | won − quote | buy YES net | buy NO net |
|---|---|---|---|---|---|---|---|
| Q1 (fell most) | 1,122 | −27.6 bp | 53.1¢ | 54.4% | +1.27¢ | −0.67¢ | −3.22¢ |
| Q2 | 1,122 | −7.4 bp | 50.6¢ | 49.9% | −0.70¢ | −2.68¢ | −1.28¢ |
| Q3 | 1,121 | +0.5 bp | 50.4¢ | 52.0% | +1.56¢ | −0.39¢ | −3.52¢ |
| Q4 | 1,123 | +9.7 bp | 48.1¢ | 47.6% | −0.50¢ | −2.49¢ | −1.49¢ |
| Q5 (rose most) | 1,122 | +34.3 bp | 47.4¢ | 48.0% | +0.60¢ | −1.39¢ | −2.58¢ |

**KILL: momentum over the last 15 minutes is priced on the hourly BTC ladder.** All ten taker legs are
negative after fee. Limit of the read: the interval is about ±5¢ wide, so it rules out large
mispricings, not a 1–2¢ one. A 1–2¢ mispricing could not clear the ~2¢ take-and-hold cost anyway.
Per the contract, do not spawn threshold variants. MOMO-15 (conditional on a signal here) is closed.
A3's results-aware disclosure stands: run 2's pooled statistics (S = −0.44¢) had been seen, and they
were in the KILL zone too.

## What a PASS would and would not authorize

A PASS on P1 and P2 authorizes **writing a paper-book package** for review. It authorizes no
trading. A single-asset, single-venue read with overlapping strikes inside an event is clustered;
the day-block bootstrap is the defence against that, and no per-row standard error is quoted.
