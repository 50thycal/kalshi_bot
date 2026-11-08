# LIMM paper strategies S2–S4: historical backtest (Part A, descriptive)

**Pre-registered 2026-11-08, before any backtest number was computed.** Owner request
(2026-11-08): "Yep good with all that recommendation". That recommendation was to pre-register
a backtest now, run it once now on the history study 5 does not own, and once more after study 5
reads. Forward study: `docs/LIMM_PAPER_STRATEGIES.md`. Script:
`scripts/limm_paper_strategies_backtest.py` (ops-allowlisted).

**What it decides: nothing.** It is descriptive. The forward bars, arms, calibration and read
date in `LIMM_PAPER_STRATEGIES.md` are fixed and this result does not move them. It tells us
early which way S2, S3 and S4 lean on history, so the Nov 24 read is easier to interpret.

## 1. Question

Replaying the forward arms' own rules hour by hour on past market data, do S2, S3 and S4 earn
more calibrated reward than their fills cost, and do the paired arms differ?

## 2. What is replayed (the forward code, unchanged)

- **Selection, placement, sizes and pulls** come from `kalshi_bot/liquidity_incentive/paper_arms.py`
  (`ArmSelector.select`, `observe`, `desired`), run hourly as in the collector. The only
  addition: the BLS release inside the window, **2026-09-18 14:00Z** (August State Employment,
  10:00 EDT; seen in the ranker's cycle 4), pulls `S3-aside` exactly like the forward 11-20
  release.
- **Scoring** comes from the read script's `analyse()`: calibration 1.34, the $1 minimum per
  market per programme, fill cost at 24 h (else half the price), net per $10 bid-day with an
  event-clustered SE, pairwise per event over the control's bid-days, and the same labels and
  floors.
- **S1 is not backtested.** Its universe, the weekly high-pay programmes, was not watched before
  2026-11-08, so there is no history for it.

## 3. Data and the approximations (stated now)

- **Hours:** `incentive_market_snapshots`, the last valid snapshot per market per clock hour,
  joined to its programme row.
- **Candidate per hour:** each side's best bid; a $10 bid's reward at the best bid by study 3's
  estimator (`pool/h × q/(field + q) / 2`), using the collector's full-book field score;
  qualifying when both sides meet Target Size (without our size); share `q/(field + q)`.
- **An arm-hour's reward:** the same estimator at the arm's price and size. One tick behind
  scores `q × discount` in place of `q` (the reference-price approximation the ranker
  validated). The thin rule applies: over 50% of the side earns 0.
- **Fills:** the bid placed on hour *h* rests through hour *h*+1 and is replayed against that
  hour's public prints (`ShadowLeg`, four models). The queue ahead is the resting size at our
  price in the snapshot's top-10 levels. A filled hour earns half its reward.
- **Markout:** our side's best bid in the snapshot 24 h after the resting hour (nearest within
  ±2 h); no snapshot means half the price is charged (study 5). Settlement is not used.
- **S4's burst** accrues each arm-hour's reward toward the $1.25 threshold, as the forward
  engine does.
- **Limits:**
  - hourly snapshots only;
  - before 2026-11-08 the collector watched the ~150 programmes with the largest total reward,
    so universes are smaller than forward;
  - the collector was down 10-07 → 10-30.

## 4. Runs (each once)

| Run | Window | When | Why |
|---|---|---|---|
| **1** | 2026-09-08 → **2026-10-30T16:00Z** (data to 10-07) | now | Study 5 owns 10-30 → 11-13 until its read; nothing in that window is replayed |
| **2** | 2026-09-08 → **2026-11-08T15:00Z** | after study 5 reads (2026-11-13 ~15:47Z), on **2026-11-14** | full history. It stops before the forward arms started (15:41Z), so it never reads the forward window |

The script refuses run 2's window before 2026-11-14T00:00Z, and any window past 2026-11-08T15:00Z.

## 5. Labels (descriptive; the forward bars, reused)

Per arm, with the conservative fill model and the whole-order stress:

- **PROMISING:** ≥ $0.15 net per $10 bid-day, with LCB > 0.
- **FRAGILE:** PROMISING, but whole-order turns it into a loss.
- **NOT-PROMISING** otherwise.
- **HOLD:** under 100 bid-days or 10 events.

Pairwise: A-BETTER / B-BETTER when the 95% CI excludes 0. **None of these is a verdict on the
forward study.**

## 6. Command

```
{"type":"script","name":"limm_paper_strategies_backtest","args":[],"id":"rl-psbt-run1-1108"}
{"type":"script","name":"limm_paper_strategies_backtest","args":["--until","2026-11-08T15:00:00+00:00"],"id":"rl-psbt-run2-1114"}
```

## 7. Results

*(Each run recorded once, here, after it runs.)*
