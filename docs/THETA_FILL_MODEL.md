# theta maker-fill model — the same realizable-P&L check mmsell needed, built for theta

**Status (introduced 2026-07-29): no theta-specific live data exists yet.** `theta4` (the one live
paper variant) has never traded real money — `live_orders` has zero rows for any `theta*` strategy.
This doc records why the check is needed, what it reads today with no theta ground truth of its
own, and what changes once theta gets a live pilot.

## Why theta needs this at all

`kalshi_bot/theta/tracker.py` uses the identical maker-sell convention as mmsell: "sell YES at the
ask == buy NO at no-bid," a resting order sized to the visible depth. `docs/MMSELL_FILL_MODEL.md`
proved that convention's paper P&L overstates the real edge — a resting order only fills when
someone actively takes the other side, so paper books the quiet cheap winners a maker can never
capture live. mmsell3 passed its own paper gate at a healthy positive number and then came back
~breakeven live; the fill model is what explains and predicts that gap. **theta4 crossed its own
pre-registered gate on 2026-07-28 (run #74 of the strategy loop: n=95≥80, +38.65¢/trade paper, 92.6%
win) purely on the paper number** — exactly the situation that made mmsell3's gate pass misleading.
Since theta shares mmsell's execution mechanism, its paper gate is exposed to the same risk and
needed the same check before treating the gate crossing as a live-ready result.

## What the script does (`scripts/theta_fill_model.py`, `{"type":"script","name":"theta_fill_model"}`)

Same shape as `mmsell_fill_model.py` — `project_realizable()` is the identical pure function,
trade-weighting realizable P&L and fill rate over price cells with enough trusted live fills
(`MIN_CELL_FILLS = 8`), with the rest of the book's trades excluded from the estimate and surfaced
as a coverage percentage. The one structural difference is the calibration source:

1. **Own calibration, when it exists.** `_load_own_calibration()` looks for live order history
   across every `theta*` strategy. The moment any theta variant trades live, this activates
   automatically — no code change needed — and the report reads exactly like mmsell's: theta's own
   fills calibrate theta's own paper books.
2. **Borrowed calibration, until then.** With zero theta live orders, the script falls back to
   mmsell3's live calibration (the only maker-sell book with live fill history) and labels every
   output line clearly as BORROWED. This is a materially weaker claim than mmsell's self-calibration
   — it assumes maker adverse selection is driven by price cell in a way that transfers across
   market series (mmsell's sports h2h/longshot markets vs theta's hourly BTC/ETH ladders), which has
   never been validated for theta. Read the borrowed numbers as a caution flag, not a verdict.

## First read (borrowed calibration, run #74/#75 theta4 data, n=95-112)

Manually reproduced against the live DB before the script existed (the numbers the script now
computes automatically):

| | value |
|---|---|
| optimistic paper (theta4, n=112) | +38.62¢/trade |
| coverage (share of trades in an mmsell-trusted price cell, yes 6-12¢) | 27.7% (31/112) |
| realizable, projected through mmsell3's calibration | +0.51¢/trade |

The covered slice collapses from +38.62¢ optimistic to +0.51¢ realizable — the same order of
magnitude mirage that hit mmsell3. But **72% of theta4's trades sit at yes-prices (13-32¢) mmsell3
never got live fills in**, so most of the book is simply unpriced by this model, not confirmed good.
theta4's average entry (~85¢ NO / ~15¢ YES) sits in that uncovered region more often than not.

**Read this as: the one piece of real evidence available suggests theta4's paper edge may not
survive maker adverse selection either, but the borrowed calibration cannot confirm or refute the
majority of the book.** It does not overturn theta4's paper gate pass, but it means that pass alone
should not be read as "ready to go live" — see the recommendation below.

## What to do with this

1. **Don't record a plain KEEP verdict off theta4's paper gate alone.** The pre-registered
   `THETA_THESIS.md` gate (per-trade positive AND realized tail-hit <= 1.25x modeled) never
   accounted for maker-fill adverse selection — same blind spot mmsell3's gate had.
2. **The real fix is theta-specific live data.** A small live pilot on theta4 (mirroring mmsell10's
   re-live-test pattern: cheap/fillable band first) would populate `_load_own_calibration()` and
   replace the borrowed read with theta's own ground truth — at that point this script's output
   becomes a real verdict, not a caution.
3. **Until then, treat the borrowed read as a standing caution alongside theta4's gate-cleared
   status** in the strategy loop's suggestion list — not a kill, not a promote, a flag that the
   paper gate and the live-realizable question are two different tests and only one has been run.
