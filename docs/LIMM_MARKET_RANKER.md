# LIMM market ranker: which incentive markets pay most right now, and how we measure it

**What this is:** a read-only screen, `scripts/limm_market_ranker.py` (ops-allowlisted), that the
owner asked for on 2026-11-08:

> "We should always be seeking the best possible place to do this strategy. So what are the best
> ones right now? And how do we measure which ones are the best? … find when data is posted, and
> leave right before that data is posted."

It **ranks; it never decides.** Moving the live book to new markets is an owner decision and a new
epoch. Run it any time:

```
{"type":"script","name":"limm_market_ranker","args":[],"id":"rl-rank-<date>"}
```

## 1. Success, from the owner's side

| # | Goal | How it is checked |
|---|---|---|
| G1 | A ranked list of the best markets now, in $/day per $10 bid | A real run on live data, compared with an independent census |
| G2 | A sound measure: per-contract reward, crowding and repricing risk, with no early read of a pre-registered study | Unit tests, hand-checked arithmetic, and the data cut-offs (§4) |
| G3 | When each market reprices (data releases), with a suggested step-aside | The detected clock compared with known publication schedules |
| G4 | Numbers that can be trusted | Cross-checked against the collector's own scoring code, and limits stated |
| G5 | Easy to read and act on | A one-page view and a plain summary |

## 2. The measure

- **Reward per $10 bid per day:**
  - pool per day × share ÷ 2 (study 3's estimator), where share = q ÷ (field + q).
  - q is the number of contracts $10 buys at that side's best bid (at most 1,000 at 1¢).
  - The field is the collector's weighted score of every other resting order on that side.
- **Why per contract matters.** Kalshi pays by contracts resting near the best bid (LIP rules
  R4–R6, `kalshi_bot/liquidity_incentive/scoring.py`). $10 at 1¢ is 1,000 contracts; $10 at 50¢ is
  20. That is why the cheap side wins whenever its field is not crowded (study 9,
  `LIMM_BAND_PROBE.md`).
- **One tick behind.** The live book rests one tick behind the best bid (§9.72). The "behind" column
  multiplies the reward by the programme's discount factor; at a 1¢ best bid there is no tick behind.
- **What is not ranked:**
  - markets whose snapshot does not qualify (a side below target pays nobody);
  - markets inside the 48 h step-aside before close.
- **Repricing risk (fills).** A resting bid gets filled when the market reprices.
  - A **jump** is an hourly mid move of 8¢ or more. The risk grade is jumps per 30 market-days over
    the last 60 days: LOW under 1.5, MED under 4, HIGH otherwise.
  - Contracts traded per market-day are shown alongside.
- **Repricing clock.** This is when jumps happen: the commonest UTC hour (±1 h), with the commonest
  weekday. It counts as clear when at least 40% of 3 or more jumps fall in one hour window. The
  suggested step-aside is to pull bids from 2 h before to 2 h after that hour.

## 3. What decides, and what does not

The ranker shows estimated **reward** and repricing **risk** side by side. It does not net them,
because the per-series **fill cost** is exactly what study 5 (`LIMM_FILL_HAZARD.md`) measures and
reads once on 2026-11-13. After that read, the ranker can take study 5's per-series fill cost and
rank by net.

## 4. No early reads

- The ranker simulates no fills and computes no P&L.
- It reads book snapshots (prices and field scores) and trade counts only. That is market data, not
  any pre-registered outcome: studies 5, 6, 8 and 9 measure simulated fills and markouts.

## 5. Known limits

- **The reward estimate has never been checked against Kalshi's actual payouts** (thesis "day-one
  check 3"). It is internally consistent (§6, cycle 2), but external calibration is still open.
- **Hourly snapshots** miss repricings that revert within the hour.
- **History is short for monthly data.** The collector was down from 10-07 to 10-30, so a monthly
  release may appear only once or twice.

## 6. Build log (cycles)

*(filled in below as each cycle runs)*
