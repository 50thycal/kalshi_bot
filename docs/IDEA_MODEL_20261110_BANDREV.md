# IDEA-MODEL 2026-11-10 — operator band-trading sketch (BANDREV): screened, 1 probe promoted

*Research Lab screen of the operator's hand-drawn strategy (2026-11-09 chat). Persisted because chat
is not durable state. Experiment truth stays in Experiment OS; this records the reasoning and the
offline numbers. Pre-registration: [`BANDREV_PREREG.md`](BANDREV_PREREG.md).*

## The idea, as drawn

1. Find a market at roughly 50/50 with a plain yes/no outcome (an NFL game winner, a mention market).
2. From studied peak volatility, rest orders at the top and bottom of the normal swing: a YES bid at
   `ref − d` and a NO bid at `ref + d`.
3. Once filled, rest a sell to exit: a take-profit at the far band, and a sell at (or just below) the
   entry price, so a swing back down gets us out flat.
4. After the exit, re-arm and repeat. Key requirement: markets we can collect good data on, and that
   show consistency.

## Screen

| check | read |
|---|---|
| **Duplicate?** | No. Control Tower `rl-ct-bandrev-1110`: nothing open on band, reversion or over-reaction. |
| **Revival?** | Yes, of a killed family. The scanner `reversion` TA book lost −5¢/contract over 1,000+ trades (graveyard `scanner-ta-books`). OFLOW: next-60s move +0.05¢ gross on 933k samples. MARKTANGLE-2: one-step reversion in sports totals is real but loses −3 to −10¢/trade after cost. Weather: price changes are a random walk (autocorrelation −0.03). The new premise: an **intra-market, in-play** read on **NFL game winners and mentions**, with a **maker** entry replayed on real bid/ask paths. Prior: very low. |
| **Mechanics** | **The downside exit cannot rest.** A sell limit at or below the bid executes at once, and our client places only limit orders (Kalshi's API has no stop order). So "sell at what we paid" is a bot-side stop that sends a taker sell: fee + spread + any gap. `mmsellA1–A3` measured stops on sports: they fired on 52% of positions and made the 5th-percentile tail worse (−19¢ vs +5¢); retired. |
| **Adverse selection** | A resting order is filled when someone with information sweeps through it. Measured live: `mmsell10b` rested 1¢ better (front of queue) and realized −3.29¢/contract vs its twin's +0.16¢, win rate −4.8pp. |
| **Mention markets** | The worst fit. The path is slow decay plus a jump to ~99¢ when the word is said; there is no swing band. On 11-09 the listener study measured contested words going from 50¢ to 95¢ within 2 s (10 of 12), with $464–641 of stale quotes lifted per show. The top order (NO bid, i.e. short YES) is exactly what gets lifted. |
| **Cost** | Taker fee `0.07·P·(1−P)` peaks at 50¢: 1.75¢/contract (1.8¢ at q=10, 2¢ at q=1). 50/50 is the most expensive place to pay taker. Maker ≈ 0 on our series (0.44¢ where a series charges maker fees). |
| **Testable now?** | Yes: public 1-minute candles carry bid/ask/trade OHLC for settled markets. |

## The math

**Theorem, not opinion.** If the price is the fair probability (a martingale), any rule of the form
"enter at a band, exit at a take-profit or a stop" has zero expected gross P&L (optional stopping).
The band only changes the shape. The chance of hitting take-profit is about `stop / (take-profit +
stop)`: with a 2¢ stop and a 10¢ take-profit that is ~17%. So the design wins rarely, loses small and
often, and ~85–90% of trips pay the taker stop.

**Offline model** ([`research/bandrev_sim_20261110/`](research/bandrev_sim_20261110/), `band_sim.py`,
output in `output.txt`): an NFL-like game whose price is the exact win probability under the model,
6-second steps, small play moves plus rare scoring jumps, 1¢ half-spread, taker stop at q=10, maker
entries free.

| design (d / take-profit / stop) | trips/game | win | EV/trip | per game, per contract |
|---|---|---|---|---|
| 3 / 3 / 1 | 20.6 | 11% | −3.95¢ | −81¢ |
| **5 / 10 / 2 (as drawn)** | 8.2 | 9% | **−4.83¢** | −40¢ |
| 8 / 16 / 3 | 3.9 | 10% | −5.84¢ | −23¢ |
| 10 / 20 / 5 | 2.5 | 14% | −6.64¢ | −16¢ |
| 5 / 10 / no stop | 2.0 | 72% | −6.13¢ | −12¢ |

- **Where the −4.83¢ goes:** about −2.1¢ fees and spread on stops; −1.3¢ jumps sweeping through the
  resting entry and the stop; −1.4¢ a discrete-sampling artifact (the no-jump control's frictionless
  loss, which falls from −3.2¢ to −1.4¢ as steps shrink from 30 s to 6 s and is ~0 under continuous
  monitoring). Realistic: **≈ −3.4¢ per trip, ≈ −28¢ per game per contract.**
- **Wider bands are worse per trip.** Only real information (scores, turnovers) moves price far enough
  to reach a wide band, so those fills are the informed ones (entry gap −2.1¢ at d=5, −3.7¢ at d=10).
- **What it would take.** Planting a transient AR(1) over-reaction on top of fair value:
  - with the drawn stop, it loses at every size (−4.3 to −4.9¢/trip even at 10¢), because the stop
    sells into the over-reaction it is trying to fade;
  - a no-stop fade with take-profit at the band breaks even only at **≥ 6¢ over-reactions with a
    ~1-minute half-life** (+0.47¢), +2.0¢ at 10¢.
  - The record's measurements (0.05¢, sub-cost one-step reversion) are far below that.

The model is stylized (assumed jump sizes and volatility); the zero-gross-edge result does not depend
on them. The real-tape question is what the probe answers.

## Pros and cons

**For:** maker entries are about free; high turnover (3–20 trips a game) makes it quick to falsify;
simple; near-50/50 has the most path variance, so the most fills; on liquidity-incentive markets
resting near the mid also earns rewards, which count toward the $100/month goal (DEC-039).

**Against:** no edge in a fair market; no resting stop, so most trips pay taker + spread; resting fills
lean toward informed flow (measured live on our own book); jumps gap through entry and stop; in-game
volatility is not stationary (late in a game a touchdown is 30–40¢); 50¢ is the fee peak; mention
markets are structurally adverse; every prior test of the family came back negative.

## Calls

| item | call | why |
|---|---|---|
| Build the book as drawn | **DISCARD** | Negative in the model; the stop is the problem, not the bands. |
| **BANDREV probe** (NFL + mention, real 1-minute candles) | **PROMOTE to probe** | Cheap, read-only, decides the premise on the operator's own markets once. Expected KILL. [Pre-registration](BANDREV_PREREG.md), `scripts/bandrev_probe.py`. |
| Two-sided resting quotes without a stop, earning incentive rewards | **Already running** as LIMM (`limm-paper-strategies`, read ≥ 2026-11-24). No new work. |
| Quote only in dead-ball windows (timeouts, halftime, reviews) | **PARK** | A mechanically new premise (fills there cannot be informed by play) but few fills; owner decision, not started. |

## Recorded in

Scorecard ledger row (2026-11-10, BANDREV), `RESEARCH_JOURNAL.md` entry of the same date.
