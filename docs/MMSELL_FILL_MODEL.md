# mmsell maker-fill model — aligning paper P&L with realizable (live) P&L

**Problem this solves:** the mmsell paper books overstate their edge because they assume a resting
maker order (sell-YES == buy-NO at the no-bid) **always fills** at that price. Live, it fills ~70%
of the time, and the ~30% it misses are disproportionately the winners. This doc records the
root-cause analysis of the paper→live divergence, the model that corrects paper's number, its
validation against the live ground truth, and the resulting re-read of the whole cohort.

## 1. Root cause: adverse selection on maker fills (not fees, not price)

The mmsell3 live test ended ~breakeven (+0.18¢/contract realized, 91.1% win over 359 settled) vs a
paper book that showed +1.2¢/contract at 93.6% win over the same window. Decomposing on the **exact
tickers**, split by whether the live maker order could fill them:

| bucket | paper n | paper win% | paper ¢/trade | entry (no-px) |
|---|---|---|---|---|
| A — live **filled** | 355 | 91.8% | **−0.67¢** | 91.5¢ |
| B — live tried, **never filled** | 147 | 95.9% | **+3.77¢** | 91.1¢ |
| C — paper-only (live never ordered) | 401 | 94.3% | +1.93¢ | 91.3¢ |

Entry price is identical across buckets → it is **not** a price or fee gap. Live's realized number
on the filled subset matches paper's own number on those same tickers (both ≈ 0). The entire gap is
that paper's headline averages in the **+3.77¢ winners a maker can never capture**: a passive bid is
only hit when someone actively takes the other side, and the quiet cheap longshots that settle
worthless never trade against you. You book them in paper; you can't live.

**It is concentrated by price.** Splitting the live fill-vs-nofill P&L by YES entry price:

| YES entry | filled ¢/trade | not-filled ¢/trade | fill % |
|---|---|---|---|
| 6¢ | +1.67 | +1.88 | 65% |
| 7¢ | +0.92 | −1.14 | 81% |
| **8¢** | **−1.06** | +2.83 | 72% |
| **9¢** | **−6.04** | +0.31 | 69% |
| **10¢** | **−1.96** | +3.12 | 81% |

At the cheap end (≤7¢) filled ≈ not-filled → adverse selection is mild and the paper edge is real
and capturable. At 8–10¢ the fills you get crater while the winners you miss stay positive. The old
"9¢ is the worst cell" price-gradient finding was never about 9¢ trades being bad — it was about the
9¢ trades *a maker can fill* being adversely selected.

## 2. Why this is a calibration model, not a per-ticker replay

The faithful fix would replay each paper entry against its post-entry price path ("would this
resting order have been lifted before close?"). **We cannot: the mmsell tracker fetches orderbooks
live to decide entry but never persists `market_snapshots`/`orderbook_snapshots` for the sports
markets it trades** (those tables are the main scanner's different universe — 0 rows for mmsell
tickers). The paper books throw away the exact data a fill model needs.

So the only real fill data we have is the **live mmsell3 ground truth**. The model
(`scripts/mmsell_fill_model.py`) calibrates the empirical relationship

    YES entry price  →  ( P[fill] , realizable P&L | fill )

from live mmsell3 (per-cent cell, trusted at ≥8 live fills), then projects each paper book's own
entry-price distribution through it. The central live finding — **fillability is driven by the price
cell, not the sport or the variant label** (non-WC live win% matched paper exactly; the selection
lives in the price cells) — is precisely what licenses that projection across variants. The output
per book is the **realizable per-trade edge**: paper corrected for which trades a maker actually
gets, plus a **coverage** figure (share of the book's trades priced in a trusted live cell).

The projection math is a pure function (`project_realizable`) unit-tested in
`tests/test_mmsell_fill_model.py`, independent of the DB.

## 3. Validation

Projected onto **itself**, mmsell3 comes out at **−1.06¢ realizable** — not its +1.2¢ optimistic
number, and in line with the live outcome (breakeven-to-slightly-negative, ≈ −$4 all-in). The model
reproduces what the real-money test actually proved, rather than the paper fantasy. Live fill rate
was 70.7%; the model's book-wide `est_fill` lands at 71–75% across the cohort. It is consistent with
reality where reality is known, which is the licence to trust it where it isn't (the variants).

Honest caveats: (a) the calibration is drawn from one live book over one window, so it is a live-
*informed estimate*, not a guarantee; (b) a couple of rich cells (12–13¢, small n) are noisy —
`coverage` and the cheap-band focus are how we keep those from driving conclusions; (c) it assumes
price-transfer across variants, which is the best-supported assumption we have but is itself the
falsifiable claim any future live re-test checks.

## 4. Cohort re-read (run `mmsell fill model` for the live numbers)

Optimistic (fill-everything) vs realizable (live-calibrated) per book:

| book | n | coverage | opt ¢/ct | **realizable ¢/ct** | read |
|---|---|---|---|---|---|
| **mmsell10** (maxyes price ceiling) | 64 | 100% | +3.80 | **+1.40** | **REALIZABLE EDGE** |
| **mmsell9** (cheap sweet-spot cell) | 13 | 100% | +5.23 | **+1.49** | REALIZABLE EDGE (small n) |
| mmsell8 (scheduled-settle) | 17 | 94% | −3.47 | +1.89 | realizable but tiny n |
| mmsell1 | 2326 | 52% | +1.79 | +0.18 | thin + |
| mmsell5 | 79 | 91% | −3.14 | +0.80 | thin + |
| mmsell (control) | 3587 | 34% | +1.53 | +0.11 | low coverage (trades too rich) |
| mmsell2 | 1523 | 21% | +2.66 | +4.92 | low coverage (est. speaks for 1/5 of book) |
| **mmsell3** (ex-live) | 903 | 95% | +1.21 | **−1.06** | **MIRAGE** — validates the live loss |
| **mmsell6** (ultra-cheap) | 256 | 98% | +1.63 | **−0.43** | **MIRAGE** |
| **mmsell11** (no-late-entry) | 139 | 93% | +2.38 | **−0.86** | **MIRAGE** |
| mmsell4 | 123 | 94% | −2.22 | −1.05 | dead (paper-negative too) |
| mmsell7 | 16 | 100% | −5.31 | −1.23 | dead |

**The actionable correction:** on blended paper, mmsell6 and mmsell11 looked like promote
candidates. Under the fill model they are **mirages** — their edge lives in the 8–11¢ cells that
adverse-select, so it evaporates for a maker. **mmsell10 (the entry-price ceiling) is the one true
realizable candidate**: 100% coverage, +1.40¢ realizable, and it works for the mechanistic reason —
capping entry price keeps only the cheap cells where fill-conditional P&L still matches paper. It is
the clean re-live-test candidate, straight into the mmsell3 entry as a `maxyes` cap.

## 5. What to adjust in paper trading

1. **Gate on the realizable number, not blended paper.** Every promote/kill decision reads
   `real_$/ct` from `mmsell fill model`, not `paper_trades.avg(pnl)`. `docs/BOOK_REGISTRY.md` gates
   updated accordingly. This alone prevents the mmsell6/mmsell11 mistake.
2. **Close the collection gap (durable follow-up).** Persist a per-cycle price snapshot for each
   in-band mmsell candidate *and each held mmsell position* (yes/no bid/ask, last, volume) so a true
   per-ticker replay fill model becomes possible — turning this live-calibrated estimate into a
   direct measurement that also covers the rich cells the current calibration can't reach.
   **[2026-07-22] Promoted to a near-term prerequisite** — the operator expects a new mmsell live
   re-test (target: `mmsell10`) within ~1 week; build this candidate-snapshot collection + replay
   as **step 0** of that re-test (see the callout at the top of `docs/MMSELL_LIVE_PLAN.md`).
   **[2026-07-23] Collection BUILT.** Both halves now exist: held positions in `mmsell_position_ticks`
   (pre-existing) and the missing in-band **candidate** universe in the new `mmsell_candidate_ticks`
   table — one orderbook snapshot per in-band candidate per entry cycle, captured off the book the
   tracker already fetches (no extra API), config-gated (`mmsell_capture_candidates`, default on) and
   per-cycle capped (`mmsell_candidate_capture_max`, default 400), fail-soft so it can never break
   the trading loop. Coverage now **accrues per-cycle** — a candidate must be born, taped, and settle
   inside the capture window before it is replayable, so it is a short data-maturity wait (days), not
   instant. **What remains is the replay itself** — a `scripts/mmsell_fill_replay.py` that, per taped
   candidate, asks "would a resting buy-NO at the no-bid at cycle T have been lifted before close, and
   at what realizable P&L?", turning §2's calibrated *estimate* into a direct per-ticker measurement
   that reaches the rich cells the live calibration can't. Run it once the table has enough settled
   candidates (before the mmsell10 live re-test). (Area-2's OFLOW ruling-out means this is for fill
   realism only, not a signal.)
3. **Re-test live only from the cheap fillable band.** Any future mmsell live test enters only where
   realizable ≈ paper (≤7¢ / under a `maxyes` cap), i.e. mmsell10's regime — not the whole cohort.
