# mmsell cell-size live canary (`Kmmsell10`) — the pre-registered plan

Written 2026-10-11, **before** registration or arming, so nothing here can be chosen after a
result. Operator decision `DEC-033` (Calvin, 2026-10-11): size MMSELL by edge instead of at
random, as `DEC-026` planned. Experiment OS is canonical for every standing this document
describes; where they disagree, Experiment OS wins.

Code: `kalshi_bot/experiment_os/successor_mmsell10_cell_size.py` (package
`mmsell-cell-size-canary`), the `bigtypes=`/`bigsize=` book keys (`config.py`),
`MmSellTracker._book_contracts`. Tests: `tests/test_mmsell_cell_size.py`,
`tests/test_successor_mmsell10_cell_size.py`, `tests/test_livedash_overview.py`.
Dashboard: the live dashboard's overview page, "Experiment scorecard — Kmmsell10
slow-information vs everything else".

## 1. What changes against `Jmmsell10`

| | `Jmmsell10` (stands down, drains) | `Kmmsell10` (successor) |
|---|---|---|
| book spec | `…,contestkey=split,sizes=1+3` | `…,contestkey=split,size=1,bigtypes=event_stat+mention+exact_score+outright+game_prop,bigsize=3` |
| contracts per order | 1 or 3 at random (ticker hash) | **3 on the slow-information cell, 1 on everything else** |
| everything else | band, ceiling, contest cap/key, rung cap, 0¢ offset, 4 h timeout, hold to settlement, 40-open cap, twin cap, tier bar, live exposure pauses (KXNFLSPREAD, tennis, KXBTCD), fee model, promotion gate, keep gate, every dollar bound | **unchanged** (asserted in CI) |

**The slow-information cell** is exactly the set P-SLOW froze on 2026-10-01
(`docs/MMSELL_REPLAY_PROBES_20261003.md`): market types `event_stat`, `mention`,
`exact_score`, `outright`, `game_prop` via `kalshi_bot/mmsell/market_types.py`. In practice
that is player-stat and game-prop markets, "will X say Y" mention markets, exact scores and
outrights. An unclassified series is never sized up.

## 2. Why now, and what this is not

What the random split (`Jmmsell10`, 2026-10-04..10-10) established, read 2026-10-10 (ops
`edgesize-fills-1010`, all three canary tags, orders from 10-01):

- **The clip is executable.** 104 of 104 3-lot orders with any fill filled completely (S1).
- **Entry price alone carries no edge.** At 90–94¢ the live win rate is within −1.1 to +2.3
  points of the price.
- **The edge is concentrated by market type.** Slow-information cell: 25 settled markets, 0
  losses, **+7.05¢/contract**. Everything else: 239 settled, 18 losses, **−2.80¢/contract**.
- **The 3-lot half ran −3.19¢/contract vs +1.03¢** for the 1-lot half, at ~95 of the 150
  settled per arm S2 needs. Unread, so not a verdict. It stops here, **recorded as stopped
  early, not as a result**.

**This is not a P-SLOW pass.** 25 settled cell markets is a quarter of the pre-registered
100-fill floor, and 0 losses in 25 would happen about 16% of the time at a 7% true loss rate.
The successor is chosen because it is the lower-risk shape, not because the cell is proven:
most of the book drops to one contract, and the cell accrues its evidence at the size under
test.

## 3. The risk envelope (registered on the version as `risk_json`)

The predecessor's, unchanged in every dollar bound: $3.00 per order and per market, $9.00 per
event, 40 open, −$30 canary loss budget, 4 h timeout, hold to settlement. The expected
footprint falls from ~$75 to **~$45** (≈10% of flow is slow-cell: 4 × $2.82 + 36 × $0.94).

**Globals: none move.** `LIVE_MAX_ORDER_DOLLARS` 3.0 and `MAX_TOTAL_EXPOSURE` 125 are already
set. `MAX_MARKET_EXPOSURE` and `MAX_DAILY_LOSS` are shared with `Alimm1` and not set. The
cutover writes only `MMSELL_VARIANTS` (append the `Kmmsell10` book; `Jmmsell10`'s entry stays,
as `Fmmsell10`'s did) and `LIVE_STRATEGIES` (`Jmmsell10` → `Kmmsell10`, `Alimm1` kept).

## 4. Sequence — authorized by DEC-033, and the hard stops

1. **Merge PR #583** (XOS-000040 held-market gate). *Hard stop, operator merge.* Required
   first: without it `Kmmsell10` stacks a clip on markets `Jmmsell10` still holds.
2. **Merge this PR.** *Hard stop, operator merge* (it touches live sizing and the arming path).
3. **`REGISTER_PACKAGE mmsell-cell-size-canary`** (`xos package-preflight` first). This ends
   only the predecessor's PAPER deployment, handing `mmsell10` over, and arms nothing.
4. **Wait** for `mmsell10` to settle paper trades inside the successor's window (about a day).
   `arm_live_canary` re-evaluates the promotion gate synchronously and refuses until it can.
5. **Cutover** — `ARM_CANARY` (`actor_role: LIVE_OPS`, `approved_by: cal`, DEC-033) and, in
   the same env request, `MMSELL_VARIANTS` (live value read at cutover + `;Kmmsell10:…`) and
   `LIVE_STRATEGIES` (live value with `Jmmsell10` replaced by `Kmmsell10`). Never
   hand-composed.
6. **Verify**, stopping at the first red: receipt SUCCEEDED; an open `live_paper_twins` row for
   `Kmmsell10_pt4`; first `Kmmsell10` orders within two scan cycles, with quantity 3 exactly on
   slow-cell tickers and 1 elsewhere; no new `Jmmsell10` orders; `gate:held` on any ticker
   `Jmmsell10` still holds; the dashboard shows the slow-vs-rest scorecard.

## 5. The readout — pre-registered now, read on fills from the arming instant

Groups are recomputed from each ticker's series type, never from the order. Outcome per filled
order: `(settle_NO − limit) × filled contracts`; per-contract figures divide by filled
contracts. Bootstrap: settlement-date blocks, 5,000 resamples, seed 20261011.

| id | claim | PASS | FAIL |
|---|---|---|---|
| C0 instrument | the rule is the one that ran | ≥ 98% of orders carry the type-assigned quantity | < 98% → HOLD (instrument) |
| C1 fill completeness | slow-cell 3-lots fill | ≥ 70% of slow-cell orders with any fill end fully filled | < 70% → the clip is too large for that cell's flow |
| C2 risk | the envelope holds | no keep-gate stop, no envelope breach | any stop |
| C3 early read | the cell's edge survives the larger clip | slow-cell realized ≥ 0¢/contract at **50** settled slow-cell markets on `Kmmsell10` | < 0¢ at 50 → back to 1 contract everywhere; the size lever closes on this flow |
| P-SLOW (decides a bigger clip) | the cell is a real edge | the probe's own registered bar, unchanged: ≥ 100 settled cell fills, cell ≥ +2.0¢, separation > 0 with bootstrap 5th pct > 0 | separation ≤ 0 at the floor → the cell is not an edge; back to one contract |

**Decision.** C0–C2 on track and C3 PASS keep the canary running. P-SLOW PASS makes a
`bigsize=5` successor the next step (a new package and a hard stop). C3 FAIL or a P-SLOW FAIL
returns the book to one contract everywhere.

**Reported alongside, never decisive:** the rest group's per-contract and loss count; dollars
per month at the realized mix; the slow cell's composition by type.

**Honest expectation.** At ~2.5 slow-cell settlements a day, C3 reads in about three weeks and
P-SLOW's floor about a month later. Dollars: if the cell holds +7¢ at 3 contracts, it adds about
$15/month; the 1-contract rest is roughly flat. Material only if P-SLOW passes and the cell
scales.

## 6. Rollback

- **Stop new entries:** remove `Kmmsell10` from `LIVE_STRATEGIES` (or `KILL_SWITCH=true` for
  the portfolio). Held positions still settle.
- **Undo the runtime:** remove the `Kmmsell10:` entry from `MMSELL_VARIANTS` (never clear the
  variable).
- The registration does not roll back: frozen versions and recorded transitions are
  append-only.
