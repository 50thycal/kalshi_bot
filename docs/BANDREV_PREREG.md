# BANDREV — do near-50/50 in-play binaries over-react far enough to trade a band?

*Pre-registered 2026-11-10 (Research Lab), from the operator's band-trading sketch screened in
[`IDEA_MODEL_20261110_BANDREV.md`](IDEA_MODEL_20261110_BANDREV.md). Written and committed **before
any BANDREV query was run against Kalshi**, so the classes, window, thresholds and decision rule cannot
be re-scoped after the numbers are seen.*

**Status:** run 1 (2026-11-10, ops `rl-bandrev-run-1`, code `b92ee1e`): **NFL KILL** (P0 pass; P1, P2 fail; P4 closes the drawn design). **MENTION HOLD (instrument)** (P0 quote coverage 42.7% < 80%). Recommended disposition for MENTION: **close; do not amend or re-run** (see Results).

## One-liner

On a near-50/50 binary that is trading live (an NFL game, a speech), rest a YES bid below the price
and a NO bid above it; when one fills, rest a take-profit on the far side and protect the entry with a
stop near the entry price; re-arm. That only makes money if prices **over-react and snap back** within
minutes by more than the round trip costs. This probe measures the snap-back directly.

## Why it is not a duplicate or a revival

- **Not open.** Control Tower read `rl-ct-bandrev-1110` (2026-11-10): no band, reversion or
  over-reaction experiment is open. The only PROBE is `limm-paper-strategies` (one-sided incentive
  bids); `mmsell-mentions-forward` sells cheap tails, a different mechanism.
- **The family has been killed four times, and this does not reopen it on a re-read.** The scanner
  `reversion` book (−5¢/contract, n>1,000, scanner markets, taker), weather price changes (autocorrelation
  −0.03), OFLOW (World Cup in-play flow → next-60s move +0.05¢ gross), MARKTANGLE-2 (series-to-series
  streak reversion in sports totals, −3 to −10¢/trade). None of them measured **intra-market, in-play
  post-move reversion on NFL game winners or mention markets**, and none replayed a **maker** band entry
  on real bid/ask paths. That is the new premise. It is narrow on purpose: one read on the operator's
  own markets, decided once.
- **Prior: very low.** A KILL is the expected and useful result. It closes the band idea on the two
  market classes the operator named.

## Data (public, no collection)

Kalshi public REST, read-only: settled markets listed through `scripts/kalshi_settled.py` (live tier,
then the historical tier); 1-hour and 1-minute candlesticks (`yes_bid`, `yes_ask`, `price` OHLC) from
the live tier, else the historical tier.

| class | series | one market per | min volume |
|---|---|---|---|
| **NFL** | `KXNFLGAME` | event (the two team markets are mirrors; the first ticker is kept) | 5,000 |
| **MENTION** | `KXTRUMPMENTION`, `KXTRUMPMENTIONB`, `KXFEDMENTION`, `KXMAMDANIMENTION`, `KXWORLDNEWSMENTION`, `KXEARNINGSMENTIONNVDA`, `KXEARNINGSMENTIONMSFT`, `KXEARNINGSMENTIONMETA` | market | 1,000 |

- **Window:** settled markets with `close_time` in **[2026-09-01, 2026-11-10)**. **At most 400 markets
  per class**, the top by volume (ties by ticker), so the API cost is bounded and the sample is the
  liquid end a bot would actually trade.
- **1-minute window per market:** the busiest hour of the market's last 14 days (hourly volume), from 3
  hours before it to 4 hours after. Chosen by volume, never by price. Quotes are forward-filled between
  candles; trade low/high exist only where a trade printed. A market needs ≥ 120 minutes to be usable.

## Measurements

- **A — reversion (primary).** A trigger is a minute *t* where the mid moved ≥ **5¢** over the previous
  **3 minutes**, the mid at the start of the move was **30–70¢**, the spread at *t* is ≤ **6¢**, and
  every horizon has a quote. Reversion `R_H = −sign(move) × (mid[t+H] − mid[t])`, H = 1, 3, 5, 10, 15
  minutes; **H = 5 is primary**. Triggers do not overlap (the next may start 15 minutes later).
- **C — control.** The same statistic on small moves, 1¢ ≤ |move| < 5¢, same filters, non-overlapping.
- **B — taker fade.** At each trigger buy the faded side at its ask, sell at its bid 5 minutes later,
  taker fee `ceil(0.07·q·P·(1−P)·100)/q`, q = 10, both legs.
- **D — the operator's design, replayed.** d = 5¢, take-profit 10¢, stop 2¢. Arm at a minute with mid
  30–70 and spread ≤ 6; rest a YES bid at ref − 5 and a NO bid at 100 − (ref + 5). **Fill only on a trade
  through the price** (conservative; a touch-fill run is reported). Stop: a minute close with our side's
  bid ≤ entry − 2 sells at that bid, taker fee. Take-profit and stop in the same minute counts as a stop.
  A position open when the window ends is marked at settlement. Maker fee 0 (measured on our series);
  a 0.0175-coefficient maker run is reported. Re-arm after every exit.
- **Statistics:** pooled means with a **market-block bootstrap** (2,000 resamples, seed 20261110),
  90% interval.

## Predictions (fixed, per class)

- **P0 — instrument.** ≥ **40** usable markets and raw two-sided quote coverage ≥ **80%** of window
  minutes. Fail → **HOLD (instrument)**, not a verdict.
- **P1 — the over-reaction is large enough to pay for a round trip.** At **n ≥ 500** triggers: mean
  `R_5` ≥ **+3.5¢** and the 90% lower bound > 0 → PASS; otherwise **FAIL**. n < 500 → **HOLD
  (accrual)**: re-read once, when the 2026 NFL season has settled (≈ 2027-02-15), with the window end
  moved to that date and nothing else changed.
- **P2 — it is specific to big moves.** Mean `R_5`(trigger) − mean `R_5`(control) ≥ **+2.0¢**. Fail
  means any P1 pass is a generic bid/ask bounce, not over-reaction.
- **P3 — reported, decides nothing:** the taker fade's net mean and lower bound.
- **P4 — the as-drawn design.** At ≥ **300** round trips, a conservative-fill EV per trip ≤ 0 **closes
  the operator's design (d 5 / take-profit 10 / stop 2)** in that class, whatever P1 says. The other
  grid rows are descriptive.

**Decision rule.** A class **PASSES** only on P0 + P1 + P2. A PASS authorizes writing a **new
pre-registration for a paper package** in that class (maker fade, a control arm, fresh tags through
Experiment OS). It authorizes nothing live and does not by itself pick the exit rule. P1 or P2 failing
at n ≥ 500 is **KILL** for that class. KILL in both classes closes band and over-reaction trading on NFL
game winners and mention markets. Revival needs a mechanically different entry, such as quoting only
in dead-ball windows (owner decision; not started here).

## What the offline model already says (not evidence about Kalshi)

[`research/bandrev_sim_20261110/`](research/bandrev_sim_20261110/) models a fair NFL-like market. With
fees and a 1¢ half-spread the operator's design loses **−4.83¢ per trip** (≈ −3.4¢ after removing a
sampling artifact), 8.2 trips per game, 9% winners, 91% stopped out. A fair market gives every band
rule zero gross edge (optional stopping). Even with planted 10¢ over-reactions the stop design still
loses (−4.3 to −4.9¢), because the stop sells into the over-reaction. Only a no-stop fade breaks even,
and only at ≥ 6¢ over-reactions with a ~1-minute half-life.

## Cost and capacity

Taker fee peaks at 50¢: 1.75¢/contract (1.8¢ at q = 10, 2¢ at q = 1). A round trip that ends in a
taker exit costs ≈ 3.5¢ before spread. Maker entries are about free on our series. Capacity is not
read here; a PASS would need a fill-rate and queue read before any sizing.

## Correlation

None with the live book (`mmsell` sells cheap tails and holds to settlement; this trades the 30–70¢
middle intraday). The closest neighbour is LIMM (resting incentive bids), which earns rewards rather
than reversion.

## Run

`{"type":"script","name":"bandrev_probe","args":[],"id":"rl-bandrev-run-1"}` after the merge. Any run
with a non-default `--max-markets` prints that it is descriptive and decides nothing.

## Results — run 1, 2026-11-10 (as printed; nothing re-scoped)

Ops `rl-bandrev-run-1`, code `b92ee1e`. Full output: [`research/bandrev_run1_20261110.txt`](research/bandrev_run1_20261110.txt).

| | NFL (`KXNFLGAME`) | MENTION |
|---|---|---|
| markets (eligible → kept → usable) | 134 → 134 → 134 | 3,077 → 400 → 398 |
| P0 quote coverage | 94.6% — **PASS** | 42.7% — **FAIL → HOLD (instrument)** |
| triggers / control | 667 / 739 | 373 / 856 |
| **R5** trigger (90% CI) | **+0.42¢** (−0.11..+0.99) | −0.27¢ (−1.09..+0.52) |
| R5 control | −0.09¢ | −0.24¢ |
| P1 (≥ 3.5¢, LB > 0, n ≥ 500) | **FAIL** | (n < 500; not reached) |
| P2 (trigger − control ≥ 2¢) | **FAIL** (+0.51¢) | (−0.03¢) |
| P3 taker fade, net | −3.86¢ | −7.86¢ |
| **P4 drawn design 5/10/2, trade-through fill** | **−3.40¢/trip, n = 1,259 → CLOSED** | −4.80¢/trip, n = 818 |
| same, touch fill / maker 0.0175 | −2.71¢ / −3.90¢ | −3.97¢ / −5.28¢ |

**NFL: KILL.** Over-reaction exists only at the noise level: the best horizon (10 min) is +0.82¢,
a quarter of one round trip, and big moves revert no more than small ones beyond 0.5¢. The drawn
design lost −3.40¢ per trip at 9.4 trips per game, 76% stopped out. That lands on the offline model's
"realistic" estimate (≈ −3.4¢), so the model's account of where the money goes (stop fees and spread,
plus jump gaps) is the account on the real tape.

**MENTION: HOLD (instrument), recommended close.** Mention markets are two-sided only around the
live event, so most of each 7-hour window is unquoted and P0's 80% floor fails. Under the contract
that is not a verdict. No instrument repair can produce a PASS, though: on the 373 triggers that
were measured, R5 is −0.27¢ against a 3.5¢ bar, the taker fade loses 7.86¢, and the drawn design
loses 4.80¢ per trip over 818 trips. Together with 11-09's listener measurements (contested words
50¢ → 95¢ within 2 s), the recommended disposition is **close; do not amend the window and re-run.**

**Observation only (decides nothing; the grid rows were descriptive by pre-registration):** both
no-stop rows were less negative than the stop rows in both classes (NFL −2.10¢ / −1.93¢; MENTION
+0.65¢ / +0.50¢ with intervals spanning 0). That matches the offline model: the stop is the largest
single cost. It is not a lead. The mention rows hold to settlement on fewer than one trip per market,
their intervals include zero, and testing them would need a fresh pre-registration on post-11-10 data.
