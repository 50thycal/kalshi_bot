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

### Run 1 — `rl-psbt-run1-1108`, 2026-11-08T20:57Z, window 09-08 → 10-30T16:00Z (data to 10-07)

```
LIMM paper strategies BACKTEST (Part A, descriptive; docs/LIMM_PAPER_STRATEGIES_BACKTEST.md)
Moves no forward bar. S1 is not replayed (no history for its universe).

window 2026-09-08T00:00 -> 2026-10-30T16:00; hourly snapshots 90157; prints 87177; arm-hours 31027; 24 h marks 508

arm         label          bid-days  events  progs  >=$1  net/$10-day [conservative]       whole-order net   paid c/h  fill c/h  thin%
S2-best     PROMISING         281.7     167    359   163  +2.165 (se 0.463, k=167)       +2.083 (se 0.442, k=167)      8.30     -0.72      0
S2-behind   PROMISING         281.7     167    359   143  +1.882 (se 0.364, k=167)       +1.800 (se 0.352, k=167)      7.21     -0.63      0
S3-aside    HOLD              157.5       8     61    11  +0.136 (se 0.039, k=8)         +0.136 (se 0.039, k=8)      0.56      0.00      0
S3-control  HOLD              178.4       8     68    14  +0.126 (se 0.061, k=8)         +0.112 (se 0.052, k=8)      0.66      0.13      0
S4-burst    NOT-PROMISING     813.1      89    197    83  +0.056 (se 0.126, k=89)        -0.039 (se 0.213, k=89)      3.83      2.87      0
S4-flat     NOT-PROMISING     196.8      89    197    21  +0.083 (se 0.180, k=89)        -0.001 (se 0.228, k=89)      1.35      1.00      0

fills and fill cost by model (24 h; settled-only at settlement):
  S2-best     conservative: 43 fills $-48.51/24h (settled 0: $0.00) | queue_aware: 43 fills $-48.51/24h (settled 0: $0.00) | optimistic: 212 fills $-259.61/24h (settled 0: $0.00) | whole_order: 44 fills $-25.27/24h (settled 0: $0.00)
  S2-behind   conservative: 20 fills $-42.49/24h (settled 0: $0.00) | queue_aware: 20 fills $-42.49/24h (settled 0: $0.00) | optimistic: 67 fills $-84.44/24h (settled 0: $0.00) | whole_order: 20 fills $-19.41/24h (settled 0: $0.00)
  S3-aside    conservative: 0 fills $0.00/24h (settled 0: $0.00) | queue_aware: 0 fills $0.00/24h (settled 0: $0.00) | optimistic: 7 fills $6.00/24h (settled 0: $0.00) | whole_order: 0 fills $0.00/24h (settled 0: $0.00)
  S3-control  conservative: 2 fills $5.74/24h (settled 0: $0.00) | queue_aware: 2 fills $5.74/24h (settled 0: $0.00) | optimistic: 18 fills $14.18/24h (settled 0: $0.00) | whole_order: 2 fills $8.18/24h (settled 0: $0.00)
  S4-burst    conservative: 11 fills $135.47/24h (settled 0: $0.00) | queue_aware: 11 fills $135.47/24h (settled 0: $0.00) | optimistic: 63 fills $811.81/24h (settled 0: $0.00) | whole_order: 11 fills $212.44/24h (settled 0: $0.00)
  S4-flat     conservative: 11 fills $47.27/24h (settled 0: $0.00) | queue_aware: 11 fills $47.27/24h (settled 0: $0.00) | optimistic: 63 fills $312.58/24h (settled 0: $0.00) | whole_order: 11 fills $63.80/24h (settled 0: $0.00)

pairwise (per event, over the control's $10 bid-days):
  S2-best - S2-behind [conservative]       events 167  +0.283 (se 0.168, k=167)       UNDECIDED
  S2-best - S2-behind [whole_order]        events 167  +0.283 (se 0.156, k=167)       UNDECIDED
  S3-aside - S3-control [conservative]     events   8  -0.006 (se 0.045, k=8)         UNDECIDED
  S3-aside - S3-control [whole_order]      events   8  +0.008 (se 0.036, k=8)         UNDECIDED
  S4-burst - S4-flat [conservative]        events  89  +0.147 (se 0.364, k=89)        UNDECIDED
  S4-burst - S4-flat [whole_order]         events  89  -0.160 (se 0.665, k=89)        UNDECIDED

S4 programmes clearing Kalshi's $1 minimum: {'S4-burst': {'programmes': 197, 'clearing_1usd': 83}, 'S4-flat': {'programmes': 197, 'clearing_1usd': 21}}
```

**Reading (descriptive; moves no forward bar):**

- **S2 leans strongly positive.** At the best bid the replay earns +$2.17 per $10 bid-day
  (SE 0.46, 167 events); one tick behind earns +$1.88. Fills did not eat it: 43 conservative
  fills, which on net *gained* at 24 h, and the whole-order model gives the same answer. At the
  touch versus behind, the difference is +$0.28 (SE 0.17): it leans toward the touch but is not
  decided.
- **S3 is HOLD** (8 events): about +$0.13 either way, and stepping aside changed nothing
  measurable. Too few ladders were watched in this window.
- **S4: the burst does what it was meant to, but does not pay.** $50 bursts cleared Kalshi's
  $1 minimum in **83 of 197** programmes against **21 of 197** for $10 flat. But the extra fill
  cost (2.9¢/h against 1.0¢/h) eats the gain: net ≈ $0 per $10 bid-day, the difference is
  UNDECIDED, and it turns negative under whole-order fills.
- **Read with care:**
  - The reward is the collector's **estimate** × 1.34. That calibration comes from mention and
    YouTube payouts, not from the gas, ski and data ladders S2 picks here, so S2's level is the
    least certain number.
  - Fill models: `queue_aware` equals `conservative` because the replay has no order-book
    deltas.
  - The window is about 3 weeks, on the older, smaller watched set.
