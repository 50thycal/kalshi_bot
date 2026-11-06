# VOLCLOCK — does the hourly BTC ladder under-price moves in scheduled US volatility windows?

*Pre-registered 2026-11-06 (Research Lab), hold A2 of `docs/IDEA_MODEL_20261106.md`, whose trigger
(MOMO-H's verdict recorded) fired with MOMO-H's KILL. Written and committed **before any VOLCLOCK
result query was run**, so the windows, rows, thresholds and decision rule cannot be re-scoped after
the numbers are seen.*

**Status:** run 1 (2026-11-06, `rl-volclock-run-1106`): **HOLD (instrument: P0 close-spot coverage)** under the contract. P3, which does not use the close spot, **fails**, so no instrument repair can produce a PASS. Recommended disposition: **close; do not amend or re-run** (see Run 1).

## One-liner

The hourly "BTC above strike" ladder (`KXBTCD`) is quoted by makers who, if they price strikes off a
trailing or flat volatility estimate, will under-price the chance that BTC crosses a strike in an
hour that contains a **scheduled** US volatility event: the 08:30 ET macro release slot, the 09:30 ET
equity open, and the 16:00 ET cash close. If so, the side that wins only if price **crosses** the strike
is cheap in those hours, relative to the same quote in a quiet midday hour.

## Why it is not a duplicate, a revival, or a rescue of MOMO-H

- **Not open.** No VOLCLOCK experiment, version or probe exists in Experiment OS or the scorecard
  (Control Tower read `rl-ct-volclock-1106a`, this session).
- **Not a revival.** No killed family conditions the crypto ladder's calibration on the **clock**.
  MOMO-H (KILL 11-06) conditioned on the **last 15 minutes of spot** (a path signal); VOLCLOCK
  conditions on a **calendar** fact known days in advance, and on the strike's distance from spot
  (crossing vs not), which MOMO-H never split on. The early TA books forecast direction; VOLCLOCK is
  direction-free.
- **Not a rescue.** MOMO-H's verdict text already says this; the test below would pass or fail
  identically had MOMO-H passed.

### Data already seen (disclosed)

MOMO-H runs 2 and 3 read overlapping rows of the same table (`KXBTCD`, 2026-08-01..11-05 and
09-01..11-05, decision at T−30 min, mid 15–85, **all 24 hours pooled**). What was printed: the YES-side
residual (100·Y − mid) by **momentum quintile**, all within ±1.6¢, and every taker leg negative after
fee. **Not computed or seen:** anything by hour of day, by weekday, by trading-day status, by
crossing vs non-crossing side, or any realized-move statistic. The pooled YES-side residual is weakly
informative about the population mean of the crossing-side residual (about half the rows have YES as
the crossing side); it says nothing about the window-vs-control difference this test decides on.

## Mechanism

- **Who is on the other side:** the ladder's market makers (the depth on `KXBTCD` is maker-supplied;
  our scan is a 1–3 minute bot, so we can only be a taker or a slow passive quoter).
- **Why they could misprice:** a maker quoting 10–20 strikes per hour, every hour, 24/7, is likely to
  run one vol surface with a slow update. Scheduled US events concentrate BTC variance into known
  minutes. A vol estimate that is not clock-aware is too low going into 08:30/09:30/16:00 on trading
  days and too high after them.
- **Why it might persist:** the hourly ladder is small; the edge, if any, is a few cents on a few
  hundred dollars of depth per hour, below the attention of firms with a clock-aware surface.
- **Prior:** sceptical. Crypto-option desks do model event vol, and Kalshi's BTC makers are likely
  Deribit-informed (D2 in the idea model: the crypto options replication is efficient). A clean KILL is
  the expected and useful result: it also tells theta-style tail selling that the clock is priced.

## Data (existing, no new collection)

`crypto_ladder_snapshots`, `series = KXBTCD`, `strike_type = 'greater'`, **2026-09-01 to 2026-11-05**
(ET dates of the event). The start is fixed by label availability, not by results: Kalshi's live
`/markets` stops returning settled markets older than a moving cutoff (MOMO-H run 3 confirmed
0.16% label exclusion from 09-01, 29.6% from 08-01). The tracker snapshots each ladder every ~5 min
while it is within 90 min of close (`theta_snapshot_max_minutes`), so the decision window below
always has captures.

### Hour classes (fixed)

An event is classified by its **ET close hour** (the last two digits of its ticker, e.g.
`KXBTCD-26OCT1509` closes 09:00 ET on 2026-10-15).

| class | ET close hours | the hour covers | role |
|---|---|---|---|
| **WINDOW** | 09:00, 10:00, 16:00, **on US equity trading days** | 08:30 release slot; 09:30 open; 15:00–16:00 into the cash close | test |
| **CONTROL** | 12:00, 13:00, 14:00, **on US equity trading days** | midday, no standard scheduled release (10:00 ET releases print before these hours open; an FOMC statement at 14:00:00 prints after the 14:00 event's settlement average) | comparison |
| WEEKEND-SAME-CLOCK | the same six hours on Saturdays and Sundays | the same clock, no scheduled event | **descriptive only** |

A US equity trading day is a weekday that is not an NYSE holiday. Holidays in or near the window:
2026-09-07 (Labor Day), 2026-11-26 (Thanksgiving), 2026-12-25, 2027-01-01. 2026-10-12 (Columbus Day)
is a trading day. Events on the 2026-11-27 half day would be excluded (outside this window anyway).

### Rows

- **Decision row:** for each (market, event) in a WINDOW or CONTROL event, the snapshot with
  `minutes_to_close` in **[35, 50]**, closest to **40**, with a two-sided quote (bid and ask present)
  and mid in **[10, 90]**. T−40 is fixed so that every WINDOW decision is taken **before** its event
  (08:10–08:25 for the 08:30 slot, 09:10–09:25 for the open, 15:10–15:25 for the close hour); the same
  offset is used for CONTROL.
- **Strike:** `floor_strike`. **Spot at decision:** the capture's `spot` (the tracker's underlying).
  Rows with no spot or spot exactly at the strike are dropped (counted).
- **Crossing side:** the side that wins only if BTC finishes on the other side of the strike from
  where it is now. Spot < strike → crossing side is YES, price = mid, outcome Yx = Y. Spot > strike →
  crossing side is NO, price = 100 − mid, outcome Yx = 1 − Y.
- **Crossing residual:** r = 100·Yx − (crossing-side mid), in cents. Positive r means the crossing
  side was cheap.
- **Label Y:** the official Kalshi settlement `result` of the market (public REST), as in MOMO-H
  amendment A1. It is read through `scripts/kalshi_settled.py`, live `/markets` first and then Kalshi's
  historical tier, so markets past the live cutoff are not silently lost (XOS-000048). The probe
  prints the label source line (live / historical / not found). The quote never prices a point after
  the decision; the label only scores it.
- **Realized move (one per event):** 10,000·|ln(spot at close / spot at decision)| in bp, where spot
  at close is the nearest `KXBTCD` capture (any event) within ±3 min of the event's close time.

## Predictions (fixed)

- **P0 — data floor and instrument.** At least **35 trading days**, at least **300 WINDOW** and **300
  CONTROL** decision rows, label exclusion **≤ 5%**, close-spot coverage **≥ 90%** of events, and the
  ET hour derived from `captured_at + minutes_to_close` agrees with the ticker's hour on **≥ 99%** of
  decision rows (this checks the hour classes, including the 11-01 DST change). Any failure →
  **HOLD (instrument)**, not a verdict.
- **P1 — the mechanism exists.** R = mean realized |move| over WINDOW events ÷ the same over CONTROL
  events. **Present iff R ≥ 1.15 and the day-block bootstrap 90% CI lower bound > 1.0.** Otherwise
  **KILL (mechanism absent)**: BTC is not measurably more volatile from T−40 to close in these hours,
  so there is nothing for a clock-blind maker to miss.
- **P2 — mispricing.** D = mean r(WINDOW rows) − mean r(CONTROL rows). Day-block bootstrap (2,000
  resamples, seed 20261106, resampling trading days with all their rows). Two-sided, because an
  over-correcting maker (D < 0: the crossing side is dear in the windows) is equally tradeable, and
  that is stated here in advance.
  - |D| < 1.5¢ → **KILL (the clock is priced)**.
  - 90% CI includes 0 and |D| < 3.0¢ → **KILL**.
  - 90% CI includes 0 and |D| ≥ 3.0¢ → **HOLD (under-powered)**: re-read once trading days double
    (about 2027-01-15); the historical-tier label read makes that re-read possible.
  - CI excludes 0 and 1.5¢ ≤ |D| < 3.0¢ → **HOLD (weak)**, same re-read.
  - CI excludes 0 and |D| ≥ 3.0¢ → **P2 signal**, go to P3.
- **P3 — tradeable.** In WINDOW rows only, a taker buy held to settlement, signed by D: D > 0 buys the
  crossing side at its ask (YES at ask, or NO at 100 − bid); D < 0 buys the non-crossing side at its
  ask. Fee `ceil(0.07·q·P·(1−P)·100)/q`, q = 10, per contract. **PASS iff pooled net EV ≥ +1.5¢ per
  contract and the day-block bootstrap 5th percentile > 0.** Otherwise **P2 SIGNAL / P3 FAIL (real but
  not tradeable after cost)**.

**Decision rule.** Only P0 + P1 + P2 signal + P3 is a PASS. The three windows are **pooled**; the
per-window D and EV, the Thursday-vs-other-weekday split of the 09:00 class (weekly jobless claims
print every Thursday at 08:30), and the weekend-same-clock D are reported **descriptively and decide
nothing**. A pooled PASS authorizes a paper package that trades **all three** windows; a per-window
read cannot select a subset after the fact (that would be a new pre-registration on fresh data).

**Power, stated in advance.** Strikes inside one event share one outcome path, so the effective sample
is closer to the event count (~140 WINDOW, ~140 CONTROL) than to the row count. The day-block 90% CI
on D is expected to be several cents wide. The read is built to rule out a mispricing large enough to
clear the ~2¢ take-and-hold cost, not to measure a 1¢ one; the under-powered HOLD branch exists so a
large but noisy D is neither killed nor promoted.

## Probe plan

- **Script:** `scripts/volclock_probe.py` (allowlisted in `scripts/ops_runner.py`). Read-only:
  `DATABASE_URL_RO` with `default_transaction_read_only=on`, one ET day per query, exact event
  tickers (`= ANY(...)`, never a string range: production collates `en_US.utf8`, MOMO-H amendment
  A2), plus Kalshi public REST for labels (live then historical tier). No orders, no writes.
- **Run:** `{"type":"script","name":"volclock_probe","args":["--start","2026-09-01","--end","2026-11-05"]}`.
- **Output:** P0 counts, R with CI, D with CI, P3 EV with p5, per-class and per-window tables, the
  descriptive splits, and one `VERDICT:` line from the rule above.
- **Tests:** `tests/test_volclock_probe.py` pins every constant above and checks that the analysis
  kills a synthetic efficient market and detects an injected window under-pricing.

## Cost and capacity

- Taker round trip is one leg held to settlement: half-spread (~0.5–1¢ on the hourly ladder) plus a
  fee of 0.7–1.8¢ per contract across the 10–50¢ crossing-side band. Hence the P3 bar of +1.5¢ net.
- Three windows × ~4 qualifying strikes × ~250 trading days a year; depth at the touch is tens of
  contracts. Even a PASS is a few dollars a month at most toward the $100/month north star, so a PASS
  is worth more as information (clock-aware pricing for theta-style ladder books) than as a book.

## Correlation

Same venue and series as theta's ladder research, opposite sign in spirit: a PASS means **buying**
crossing risk in three hours, which would also tell any tail-selling book to stand aside in exactly
those hours. A KILL closes "scheduled-vol mispricing on the hourly crypto ladder" and, with MOMO-H,
leaves no open calibration question on this ladder for a 1–3 minute bot.

## Run 1: VERDICT HOLD (instrument), and P3 fails regardless (2026-11-06 13:51Z)

`rl-volclock-run-1106`, code `eaa3700` (PR #745 merged), window 2026-09-01..11-05, as pre-registered.
Labels: 2,703 live, 0 historical, 0 not found. Kalshi reported a settled-markets cutoff of 2026-09-07
at run time. 1 row was dropped (spot on the strike).

| measure | value | pre-registered bar | |
|---|---|---|---|
| trading days / rows WINDOW / CONTROL | 44 / 1,122 / 1,189 | ≥ 35 / ≥ 300 / ≥ 300 | pass |
| label exclusion | 0.0% | ≤ 5% | pass |
| ET hour match | 1.0000 | ≥ 0.99 | pass |
| **close-spot coverage** | **0.860** | ≥ 0.90 | **FAIL → HOLD (instrument)** |
| P1: R (WINDOW ÷ CONTROL realized move) | 1.386, 90% CI 1.105–1.725 | ≥ 1.15 with CI lower bound > 1.0 | would pass |
| P2: D (crossing residual, WINDOW − CONTROL) | **+4.31¢**, 90% CI +0.86 to +7.65 | \|D\| ≥ 3 with CI excluding 0 | would signal |
| **P3: WINDOW taker, buy crossing side** | **−1.96¢**, p5 −4.30 | ≥ +1.5¢ with p5 > 0 | **FAIL** |

**The verdict is HOLD (instrument), as the contract requires.** Had P0 passed, the ladder would have read
**P2 SIGNAL / P3 FAIL (real but not tradeable after cost)**. The P0 failure cannot change that. Close-spot
coverage feeds only R (P1). P3 is computed from the decision quote and the official label alone, so any
repair of the close-spot read leaves P3 exactly where it is. Hence the recommendation: close VOLCLOCK and
do not write an instrument amendment. An amendment written now would be results-aware and could only
move the verdict between HOLD and P3 FAIL.

**What the numbers say (descriptive; decides nothing):**

| slice | rows | events | avg move (bp) | crossing-side quote | crossing side won | won − quote | buy crossing | buy other side |
|---|---|---|---|---|---|---|---|---|
| WINDOW | 1,122 | 132 | 35.2 | 25.2¢ | 25.0% | −0.15¢ | −1.96¢ | −1.63¢ |
| CONTROL | 1,189 | 132 | 25.4 | 25.0¢ | 20.5% | −4.45¢ | −6.23¢ | +2.71¢ |
| weekend, window clock | 176 | 53 | 11.0 | 24.6¢ | 19.3% | −5.30¢ | −7.36¢ | +3.30¢ |
| weekend, control clock | 215 | 53 | 13.1 | 25.1¢ | 24.2% | −0.95¢ | −2.74¢ | −0.80¢ |
| WINDOW 09:00 / 10:00 / 16:00 | 338 / 472 / 312 | 44 each | 30.8 / 56.8 / 22.1 | ~25¢ | 24.3 / 28.0 / 21.5% | −0.89 / +2.71 / −3.67¢ | −2.74 / +0.92 / −5.47¢ | |

- **The mechanism is half there.** BTC really is about 1.4× more volatile from T−40 to close in the
  window hours, mostly at the 09:30 open (10:00 events move 57 bp against 21–33 bp at midday). The
  crossing-side quote is flat across classes (25.2¢ vs 25.0¢), so the ladder looks **clock-blind**, as
  the thesis guessed.
- **The mispricing points the other way.** A clock-blind maker whose level is calibrated to the busy
  hours is not cheap in the windows, where won − quote is −0.15¢. It is **dear in the quiet hours**, at
  −4.45¢ for CONTROL and −5.30¢ on weekend mornings. D > 0 comes from the control side, not the window
  side. Buying the crossing side in the windows loses after cost; only the 10:00 class is above zero
  (+0.92¢, one of three pooled windows, post hoc).
- **Not a new idea from this read.** "Sell the crossing side in quiet hours" (buy the other side for
  +2.71¢ in CONTROL) is a different hypothesis. It sits in the tail-selling family (THETA was shelved
  for a distribution-shape error, and `theta-tail-sell` exists in Experiment OS) and was found
  post hoc on these rows. It is recorded here as an observation only. A test would need its own
  pre-registration on data from 2026-11-06 onward.
- **Close-spot gaps:** 14% of classed events had no `KXBTCD` capture within ±3 min of close. Likely
  causes are collector gaps (the Control Tower showed the ladder collector briefly stale on 11-06) or
  hours where the next event was not yet within the 90-minute capture window. Not diagnosed; it affects
  only R.

## What a PASS would and would not authorize

A PASS authorizes **writing a paper-book package** (an Experiment OS experiment with a control arm)
for review. It authorizes no trading and no live exposure.
