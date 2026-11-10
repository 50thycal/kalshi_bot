# FAVHELD: is the side spot already sits on underpriced on the hourly BTC ladder?

**Pre-registered 2026-11-10 ~02:30Z.** Every decision this test grades falls on an ET date of
**2026-11-10 or later**, so none of its data has been seen by anyone. Calvin asked for this on 2026-11-09 ("Sure do
that"), after the post-hoc observation in `LEVELHOLD_PREREG.md` Results. Session role: Research Lab.

Nothing here changes a lifecycle state, a gate, a verdict or a book. Experiment OS stays canonical.

## Where it comes from (two post-hoc sightings, both disclosed)

1. **LEVELHOLD run 1** (2026-08-06..11-08, 8,654 decisions): every arm *and the control* had a positive
   raw residual on the **held side** (the side of the strike that spot is on, 20–40 min before close):
   control +3.75¢, all arms +2.6 to +3.7¢. It was positive in 8 of 10 price deciles.
2. **VOLCLOCK run 1** (2026-11-06): the crossing side was "dear in the quiet hours", −4.45¢ in midday
   CONTROL. Buying the other side, which is the held side, netted **+2.71¢** after fee there (post hoc). In the scheduled-vol
   WINDOW hours it was about flat (−0.15¢).

**In-sample net estimate, derived arithmetically from LEVELHOLD's printed numbers with no new query:** a held-side taker
buy and a break-side taker buy together cost one spread plus two fees, about 4.4¢. LEVELHOLD's printed break-side EVs
(−4.7 to −5.7¢) therefore imply a held-side net of about **+0.8 to +1.3¢ per contract**. That is thin. This test is
built to find out whether it is real; it is not built to find it.

**Not a revival of TFAV** (KILLED 2026-07-09, −3.6¢ at n = 210). The differences are material:
- **Selection:** TFAV bought only *model-filtered* favourites (`100·P_model − ask ≥ 5¢`) in the 65–90¢ band, with a
  spot vol model. FAVHELD has **no model**: it asks whether the held side's quote is calibrated.
- **Label:** TFAV labelled by spot at close. FAVHELD uses the **official settlement result**, which settles on the CF
  Benchmarks 60-second average, not spot.
- **Sample:** TFAV was 210 July paper trades. The prior here is 8,654 decisions over three months.

The family is still favourite-buy (0-for-1), and the July journal concluded that the FLB "is not harvestable on
these hourly crypto ladders from either direction". **The prior is sceptical, and the bars below reflect that.**

## Data (existing collectors, no new collection)

- **Decision rows:** LEVELHOLD's rows exactly (`levelhold_probe.load_day`). `crypto_ladder_snapshots`, series
  `KXBTCD`, `strike_type = 'greater'`, per (event, market) the snapshot with `minutes_to_close` in [20, 40]
  closest to 30 and mid in [15, 85], with exact event tickers.
- **Held side:** YES if spot > K, NO if spot < K; dropped if equal. `mid_held` and `resid` as in LEVELHOLD.
- **Label:** the official Kalshi `result`, live tier then historical tier.
- **Window:** ET dates **from 2026-11-10**. Look 1 covers 2026-11-10..2027-01-08 (60 days) and runs on or after 2027-01-09.
  Look 2 covers 2026-11-10..2027-03-09 (120 days) and runs **only if look 1 is HOLD**. **There are no other looks.** Interim reads of
  counts only (rows and days, no outcome or EV) are allowed for scheduling.

## Statistics

- **R** = mean `resid` over all decisions, in ¢ (gross held-side calibration error).
- **E** = mean net EV of a **taker buy of the held side at the snapshot ask**, held to settlement, with fee
  `ceil(0.07·q·P·(1−P)·100)/q` and q = 10 (`levelhold_probe.held_leg_ev(r, buy_held=True)`).
- CIs come from a day-block bootstrap (2,000 resamples, seed 20261110). Adjacent strikes in one event share an outcome
  path, so the day is the resampling unit, as in MOMO-H, VOLCLOCK and LEVELHOLD.

## Predictions (fixed)

- **P0: data floor.** Look 1 needs ≥ 60 ET days and look 2 ≥ 120. Both need ≥ 4,000 decisions and label exclusion ≤ 10%.
  Failing either gives **HOLD (instrument)**, not a verdict, and the same look is re-run when the floor clears.
- **P1: the mechanism is present forward.** R ≥ **+2.0¢** and the 90% CI of R excludes 0.
- **P2: tradeable.** E ≥ **+1.0¢/contract** and the **2.5th percentile** of E's bootstrap > 0. The one-sided 97.5% bar
  is stricter than the usual 95% because two looks are allowed.
- **Look 1:** P1 and P2 → **PASS**. P1 holds and E > 0 but P2 fails → **HOLD → look 2**. Anything else → **KILL**.
- **Look 2 (final):** P1 and P2 → **PASS**. Anything else → **KILL**. There is no third look and no re-cut of the window.
- **KILL closes held-side favourite-buy on `KXBTCD` for a taker.** No band, hour, minutes-to-close or strike-distance
  variants may follow.
- **A PASS is not a book.** It authorizes only a **paper-package draft** through the normal Research Lab chain, where
  snapshot-ask fill realism gets tested on paper. Anything that touches real money stays an operator hard stop.

**Descriptive, never gating:** E and R are also reported for QUIET vs WINDOW hours (VOLCLOCK's definition: weekday
non-holiday close hours 09, 10, 16 ET = WINDOW; everything else = QUIET), by `mid_held` band (<65 / 65–75 / ≥75),
and per event (the mean of event means). None of these can turn a KILL into a PASS.

**Resolution, stated in advance.** This is extrapolated from LEVELHOLD's interval widths (90% half-width about 1.6¢ for a
difference at 95 days). The 97.5% one-sided bar should require roughly **E ≥ +1.5 to 2¢ at 60 days and E ≥ +1 to
1.4¢ at 120 days**. This is an estimate, not a measurement. If the true net is about +1¢, as the in-sample arithmetic
suggests, **the likeliest outcome is HOLD at look 1 and KILL ("unproven") at look 2**. That outcome would mean "not
provable at a size worth trading", not "absent". The cost of that error is small: +1¢/contract is about the floor a
taker book could tolerate.

## Cost, capacity, correlation

- Cost: 1.2–2.0¢ fee at 50–85¢ plus half a 1¢ spread. Both are inside E by construction.
- **Capacity is unmeasured.** The snapshot table stores no size at the ask. About 90 decisions/day, but the strikes in
  one hourly event are one shared bet ("BTC stays on this side"), so there are about 24 independent bets a day. Depth at
  the ask is the first thing a paper package would have to establish.
- Correlation: the same direction as the paused theta book and the paused MMSELL `KXBTCD` tail cell (all three
  lose on a large adverse BTC move). It is a different price band (50–85¢ near the money vs 93¢+ tails), but the
  same risk driver. If it is ever built, it shares a BTC-move risk budget with those, not a separate one.

## Probe

`scripts/favheld_probe.py`, read-only (`DATABASE_URL_RO` plus Kalshi public REST). It reuses LEVELHOLD's loaders and
MOMO-H's bootstrap and labels. Ops: `{"type":"script","name":"favheld_probe","args":["--start","2026-11-10","--end","2027-01-08","--look","1"]}`.
It needs an `ops_runner.py` allowlist entry, so the merge is an operator hard stop. The look 1 run is scheduled for
2027-01-09.

## Results

*(empty until look 1)*
