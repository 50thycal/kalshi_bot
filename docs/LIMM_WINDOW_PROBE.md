# LIMM window probe — "say X before <date>" markets, cheap YES side (study 8)

**Status:** PRE-REGISTERED 2026-11-06 (Research Lab), before any run. Read-only; authorizes nothing.
**Asked by the operator** (2026-11-06): "let's explore those again" — the windows thesis §9.55 dropped.
**Script:** `scripts/limm_window_probe.py` (ops allowlist `limm_window_probe`).

## 1. Question

§9.55 dropped KXTRUMPSAY / KXTRUMPSAYMONTH / KXTRUMPSAYCOMPANY because a word can be said at any
moment, so a bid cannot step aside first. That holds for a **NO** bid (the word lands, sellers dump
NO into it — the YouTube sweeps of §9.73). A **YES** bid on a word the market prices at 1–10c is the
other way round: when the word is said YES jumps and nobody sells YES into us; a YES bid fills when
holders give up, which concentrates near the deadline. Do these windows pay on the cheap YES side,
net of the fills that bid takes?

## 2. Method (frozen)

- **A. Now.** Every running liquidity programme in the three series; the live book (public REST);
  cheap side = YES best bid 1–10c; price = §9.72 rule (reference price, one tick behind when it is
  the touch); size $10 (≤500 contracts); scored with `scoring.estimate` (the book's own estimator).
  Output: qualifying markets and $/market-day.
- **B. History.** Settled markets in the three series closed in the last 60 days; hourly
  candlesticks. Entry at the first hour ≥6h after open whose YES bid is 1–10c (same price rule on
  the candle's YES bid). The bid rests until close − 48h (step-aside) or the market's own close.
  Fill = a later hour whose YES trade low ≤ our price (conservative). Filled P&L per contract =
  100·[YES] − price. Reward over the rest = rested market-days × A's mean $/market-day.

## 3. Verdict (frozen)

- **PROMISING:** A ≥ $0.50/market-day on ≥10 qualifying markets, AND B has ≥100 entries with fills
  costing ≤40% of the reward.
- **AVOID:** B has ≥100 entries and net (reward + fill P&L) < 0.
- **NOT-YET:** anything else.

PROMISING only nominates a live trial; any live change needs the operator's explicit go.

## 4. Known limits

A uses today's books for history's reward (books then may differ). Hourly candles miss fills inside
an hour that reverted; a trade at our price may not have reached us in the queue (conservative).
B ignores §9.70's partial-fill cancel (a whole $10 fill is the worst case).

## 5. Results

Not run yet. The operator review page shows the results when they land: `tools/review/limm/README.md`.
