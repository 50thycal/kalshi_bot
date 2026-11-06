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

## What a PASS would and would not authorize

A PASS on P1 and P2 authorizes **writing a paper-book package** for review. It authorizes no
trading. A single-asset, single-venue read with overlapping strikes inside an event is clustered;
the day-block bootstrap is the defence against that, and no per-row standard error is quoted.
