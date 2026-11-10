# BANDREV — do near-50/50 in-play binaries over-react far enough to trade a band?

*Pre-registered 2026-11-10 (Research Lab), from the operator's band-trading sketch screened in
[`IDEA_MODEL_20261110_BANDREV.md`](IDEA_MODEL_20261110_BANDREV.md). Written and committed **before
any BANDREV query was run against Kalshi**, so the classes, window, thresholds and decision rule cannot
be re-scoped after the numbers are seen.*

**Status:** pre-registered, probe built and tested (`scripts/bandrev_probe.py`), **not run**. The
allowlist entry touches `scripts/ops_runner.py`, so the merge is an operator hard stop
(`docs/STANDING_AUTHORIZATIONS.md`).

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
