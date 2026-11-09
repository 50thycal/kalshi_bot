# LEVELHOLD: does the hourly BTC ladder misprice strikes that sit on a technical level?

**Pre-registered 2026-11-09 ~19:20Z, before any level statistic was computed.** The only data
seen before this was written is the strike-count census below (`rl-levelhold-census-1109a`), which
counts strikes by their price grid and counts spot candles. It computes no residual, outcome or
level feature. Session role: Research Lab. Idea-model run: `docs/IDEA_MODEL_20261109_TUBE.md`.

Nothing here changes a lifecycle state, a gate, a verdict or a book. Experiment OS stays canonical.

## Where the idea comes from, and how it differs from what was killed

Source: tube_bot video `z97PnFIPoFg` ("Claude Tested 100,000 Price Action Strategies", AI
Pathways). On 5 years of NQ/ES 1-minute data the video reports that real horizontal levels held
51.1% of touches against 42.4% for random lines (+8.7 pts NQ, +8.5 pts ES). The strongest level rule was the
**flip zone** (a broken level retested from the other side), at +11.5 pts NQ and +10.9 pts ES. Touch count added nothing. Level =
2+ swings within 0.25 ATR; touch = within 0.25 ATR. These are futures numbers, not Kalshi numbers.
They are the reason to ask the question, not evidence for the answer.

**The Kalshi question.** The `KXBTCD` hourly ladder prices each strike as a smooth function of
distance from spot and volatility. If price really respects levels, a strike that sits on a recent
swing high/low (or a round $1,000) should settle on its **current side** more often than its quote
says. The quote does not model level structure, so it would underprice "the level holds".

**Not a revival of MOMO-H** (`MOMO_HOURLY_PREREG.md`, KILL 2026-11-06), whose pre-commitment
forbids threshold variants of follow/fade-recent-movement. MOMO-H conditioned on the **direction of
the last 15 minutes of movement**. LEVELHOLD conditions on **where the strike sits relative to
price structure built over the previous five days**. It asks hold-vs-break at a fixed price,
not continuation-vs-reversal. The two features are different, so a KILL there says nothing here.
Neither is a revival of 07-11 **ROUNDPIN**, which was killed because the 15-minute contract's target
is the open price, not a round strike. On `KXBTCD` the strikes *are* fixed prices.

**Multiple testing, disclosed.** This is the third pre-registered question on the same
`crypto_ladder_snapshots` decision rows (after MOMO-H and VOLCLOCK). Neither earlier probe computed
a level feature. Any PASS here therefore authorizes only a **forward confirmation**, not a paper book (see
P3).

## Data (existing, no new collection)

- **Decision rows:** `crypto_ladder_snapshots`, series `KXBTCD`, `strike_type = 'greater'`. For each
  (event, market), the snapshot with `minutes_to_close` in [20, 40] closest to 30, with mid in
  [15, 85]. These are MOMO-H's rows, one ET day per query, with exact event tickers (the
  `en_US.utf8` collation lesson, MOMO-H amendment A2). Strike K = `floor_strike`, rounded to the dollar.
- **Spot path:** `crypto_spot_candles`, product `BTC-USD`, 1-minute closes. Census: 143,997 rows since
  2026-08-01 19:13 UTC, about 100% minute coverage.
- **Label:** the official Kalshi settlement `result` per market, live tier then historical tier
  (`scripts/kalshi_settled.py`, the XOS-000048 fix).
- **Window:** 2026-08-06 (five days of spot lookback after the candle table starts) to 2026-11-08,
  ET dates.

## Features (no lookahead: only candles with `minute_ts` < decision time t)

- **4-hour bars** aligned to UTC 00/04/08/12/16/20, built from the 1-minute closes: high = max close,
  low = min close, close = last close. Only bars that have **completed** before t are used.
- **ATR:** Wilder 14 on those bars (true range from the close-built high/low/prev-close).
- **Swing levels:** a completed bar is a swing high if its high exceeds the highs of the 2 bars on each
  side, and a swing low if its low is below the lows of the 2 bars on each side (fractal, N = 2). A pivot only counts once both
  confirming bars on its right have completed before t. The lookback is the last 30 completed bars
  (five days).
- **Flip zone:** a swing level that at least one completed 4h close has crossed since the pivot formed.
- **Arm SWING (primary):** |K − L| ≤ 0.25·ATR for some swing level L.
- **Arm FLIP (secondary):** the SWING condition holds for at least one *flipped* level.
- **Arm ROUND (secondary):** K is a multiple of $1,000.
- **Control (the "random lines" null):** decision rows in no arm (not SWING and not ROUND).
- **Held side:** the side of K where spot is at t (YES if spot > K, NO if spot < K; spot == K is
  dropped). `mid_held` = mid if YES is held, else 100 − mid. `resid` = 100·[settled on held side] −
  `mid_held`.

## Predictions (fixed)

- **P0: data floor.** At least 40 distinct days, at least **600** SWING decisions, label exclusion at most 10%,
  and at least 95% of decisions with a full 30-bar lookback (otherwise the row is dropped and counted). Failing
  any of these gives **HOLD (instrument)**, not a verdict.
- **Statistic L (per arm).** Stratify by `mid_held` decile (edges from the pooled decision set).
  L = Σ_d w_d·(mean resid of arm rows in d − mean resid of control rows in d), with w_d = the arm's share of its rows in d (deciles with no
  control rows are dropped and counted). A positive L means levels hold more than the quote prices.
  The CI is a day-block bootstrap with 2,000 resamples and seed 20261109.
- **P1: signal.** Primary SWING: **|L| ≥ 3.0¢ with the 90% CI excluding 0.** Secondary FLIP and ROUND
  arms can signal only with |L| ≥ 3.0¢ **and the 95% CI excluding 0** (a stricter bar for the extra looks).
- **P2: tradeable.** For each signalling arm, take the implied taker trade on its rows. If L > 0, buy
  the held side at its ask (YES: `yes_ask`; NO: 100 − `yes_bid`). If L < 0, buy the break side. Pass
  if **net EV after fee** (`ceil(0.07·q·P·(1−P)·100)/q`, q = 10) is **≥ +1.5¢/contract**, with the
  day-block bootstrap 5th percentile > 0.
- **KILL:** primary |L| < 1.5¢ or its 90% CI includes 0, **and** no secondary arm signals. That
  closes technical-level conditioning for this venue. **No re-runs on other ATR bands, pivot widths,
  lookbacks or round-number grids.**
- **HOLD:** the primary arm has 1.5¢ ≤ |L| < 3¢ with the CI excluding 0. Re-read once, on the
  forward window, at ≥ 600 new SWING decisions.
- **P3: a PASS is not a book.** P1 + P2 authorize only a **forward confirmation**: the identical code,
  run on decisions after 2026-11-09, must reach ≥ 600 arm decisions with the same sign and |L| ≥ 1.5¢
  before any paper-package draft.

**Resolution, stated in advance.** Resid has an SD of about 45¢. At about 1,000–2,000 arm
decisions, the 90% CI half-width on L is about 3¢, so the probe detects effects of roughly **4¢ or
more**. An effect smaller than that would not be tradeable anyway: the taker cost at mid is
about 2.3¢. A KILL therefore means "no tradeable level effect", not "no level effect at all".

**Note A0 (pre-data, 2026-11-09 ~19:40Z, written before any real level statistic existed).** The
probe was run on 60 synthetic null datasets (no planted effect; 50 days × 40 decisions, SWING ≈ 700
and ROUND ≈ 200 rows each). L's SD was 1.9¢. The primary 90% CI excluded 0 in 4/60 runs (nominal
10%, so it is calibrated). Across all three arms, a **false P1+P2 appeared in 3/60 runs, all on the
small ROUND arm**, and false P1-only signals appeared on SWING 3, FLIP 4 and ROUND 2. The real ROUND
arm is about 5× larger, so its rate should be lower. This is the reason a PASS buys only a forward
confirmation (P3). The design is unchanged.

## Cost and capacity

Hold-to-settle taker cost at 30–70¢ is 2.0–2.3¢/contract (fee plus half of a ~1¢ spread). `KXBTCD`
clears ≥ 8.6 M contracts/day (`rl-swc-1106n`). At an illustrative 2¢ net on 10 contracts × ~20 level
decisions/day, the book would make about $4/day, which is the scale of the $100/month north star. That is the only
reason a low-prior question is worth one probe.

## Correlation

The question has no exposure to the sports, mentions or liquidity-incentive books. It does share the
underlying with the MMSELL cell on `KXBTCD` tails (real money is paused there, XOS-000041) and with
theta (paused). LEVELHOLD trades mid-ladder strikes held about 30 minutes, not tails. The shared driver
is BTC volatility, not the same mispricing.

## Census (counts only, 2026-11-09)

`rl-levelhold-census-1109a`, one day (2026-11-03): 216 decision snapshots on **106 markets**. Strikes
sit on a **$100 grid**. Round-$1,000 strikes are 12 of the 106 (about 11%), which projects to about 1,100
ROUND decisions over the window. Spot candles: 143,997 1-minute rows over 99 days. The SWING-arm count
is estimated, not measured; P0 measures it.

## Probe

`scripts/levelhold_probe.py` (read-only; `DATABASE_URL_RO` plus Kalshi public REST). Ops:
`{"type":"script","name":"levelhold_probe","args":["--start","2026-08-06","--end","2026-11-08"]}`.
The probe needs an `ops_runner.py` allowlist entry, and a merge that touches the ops runner is an **operator hard stop**
(`docs/STANDING_AUTHORIZATIONS.md`). The probe has not run.

## Results

*(empty until the run; any amendment is written here with its timestamp, before or after results, and says which)*
