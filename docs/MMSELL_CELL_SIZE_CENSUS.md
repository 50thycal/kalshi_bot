# CELL-SIZE — is there any price cell on the live mmsell book where size ×3 is worth it?

*Census pre-registered 2026-09-30, before any per-price-cell read of `Fmmsell10` was taken. Origin:
Research Lab idea-model run [`IDEA_MODEL_20260930_MMSELL.md`](IDEA_MODEL_20260930_MMSELL.md),
operator request (Calvin, 2026-09-29 handoff), open lead "size ×3 where fill selection is neutral
(≤ 6¢ band)". Status: **NO CELL PASSES (census run 2026-09-30) — sizing on this book is closed until a per-fill edge exists; see RESULTS.**
Script: `scripts/mmsell_cell_size_census.py` (#497).*

## The question, stated once

Sizing multiplies whatever the per-fill edge is. On `Fmmsell10` the book-wide per-fill edge is
about +0.2 to +0.4¢ (`mmideas-truth-0930`: +$2.49 realized over 689 settled), so size ×3 on the
whole book is worth ~$3–7/month and adds three times the tail. Size is only worth taking in a
**price cell** where two things are both true:

1. the fills we get earn a clearly positive amount per contract, and
2. the fills are **not** adversely selected relative to the rests that never fill in the same
   cell, because a 3-lot fills fully only when a larger taker sweeps the level, and larger
   takers are more informed.

`MMSELL_FILL_MODEL.md` §1 (live `mmsell3`, 2026-07) found exactly one such cell: 6¢ YES entry,
filled +1.67¢ vs not-filled +1.88¢, fill 65%. That calibration is from a different book and a
different season. This census asks whether such a cell exists on the book that is live now.

## Which bucket it moves, and why the fill does not select against it

Sizing moves nothing between buckets; it multiplies the **filled** bucket in one cell. The
fill-neutrality bar (C3) is the explicit check that the multiplier lands on fills that are not
worse than the rests. If no cell is fill-neutral, sizing is closed and the census says so.

## Pre-registered census

**Data (provenance: bot DB only).** `live_orders` for `Fmmsell10` (`action='buy'`, `side='no'`,
`kalshi_order_id` not null, `limit_price` in 93–97) from the epoch start `2026-09-07T02:03:36Z`
to the run instant; `fills` for filled quantity and fill time; realized outcome per settled
contract as `(settle_NO − limit) × min(filled, 1)`, settlement from `GET /markets/{ticker}` as in
`mmsell_thin_market_probe.py`; for **unfilled** orders, the simulated P&L of the same market under
the live tag's own `paper_trades` row (the "ordered, never filled" bucket `live_book_truth`
already isolates) as the counterfactual. `execution_trade_events` (WS-019, since 2026-09-16) for
the size of trades printing at our level after we post.

**Cells (frozen):** NO limit price 93, 94, 95, 96, 97 (YES entry 7, 6, 5, 4, 3¢). No pooling
across cells; no widening to the `lo=5,hi=10` control band.

**Bars (frozen).**

| id | claim | PASS | otherwise |
|---|---|---|---|
| **C0** instrument | fills map to settled outcomes | ≥ 95% of filled orders settle-mapped | **HOLD (instrument)** |
| **C1** cell floor | the cell is readable | ≥ 150 settled fills in the cell | cell is **unreadable**; reported, never decided |
| **C2** positive edge | fills earn money in the cell | realized ¢/fill ≥ +1.0¢ **and** bootstrap 95% lower bound > 0 | cell fails |
| **C3** fill neutrality | fills are not worse than rests in the cell | `(unfilled paper ¢) − (filled realized ¢) ≤ 2.0¢` | cell fails (the book-wide gap is ~6.4¢; 2.0¢ is under a third of it) |
| **C4** taker size (reported) | a 3-lot can fill in one print | median size of trades at our YES price after we post ≥ 3 contracts | size candidate is capped at the median instead of 3 |

**Decision rule (pre-committed).**

- A cell passing **C1, C2 and C3** makes "size ×3 in that cell" (or ×median under C4) a candidate
  **sizing treatment** for a successor live canary. It arms nothing. A successor re-registers the
  risk envelope (`contracts per order` and `exposure per market` both change), so it is an
  operator hard stop under `DEC-012`, and the pre-registered twin protocol applies.
- **No cell passes** → sizing on this book is **closed** until a per-fill edge exists somewhere;
  the run doc's stop-investing recommendation is then supported by a number rather than a
  suspicion. Record the verdict in the scorecard row.
- The census is never re-cut on the `lo=5,hi=10` control band, on the twin's trades, or on a
  different cell width. Any of those is a new census.

**Reported alongside (never decisive):** fills/day per cell; implied $/month at size 1 and at the
candidate size (`fills/day × ¢/fill × size × 30`); win rate; the same cells on the pre-9/7 live
books as context (a different season, not pooled).

## Cost + capacity

- **Fees:** maker fills bill ~0.01¢/contract; a 3-lot pays the same per contract.
- **Tail:** a 3-lot loses three clips on one settlement (~−$2.80 at 93¢). The current envelope's
  daily stop is $5.00 and the canary budget $15.00; a successor must restate both against the
  new clip, and the contest cap (`contestcap=1`) still bounds correlated rungs.
- **Honest ceiling:** the best plausible cell is about 15 fills/day at +1.5¢. At ×3 that is
  ~$20/month. It does not reach $100/month; it is a multiplier on whatever edge YOUNG-SERIES or
  another lever proves, and worth nothing without one.

## Correlation

Pure multiplier on the existing live book. No diversification credit.

## RESULTS

**Census run 2026-09-30 (ops `cellsize-20260930-1`, code `6b5b9b2d`). Verdict: NO CELL PASSES.**

`Fmmsell10` since the epoch start, 23.5 days: 793 NO-buy orders in cells 93–97 (a further 141
orders rested below 93¢, outside the pre-registered cells, because the book joins the NO bid and
the bid can sit 1–2¢ under `100 − maxyes`). C0: 567 of 584 filled orders settle-mapped (97.1%).

| cell | orders | fill | settled fills | win | realized/fill | boot 95% LB | unfilled paper/ct | gap | median print | status |
|---|---|---|---|---|---|---|---|---|---|---|
| 93 | 446 | 70.6% | 315 | 92.4% | **−0.64¢** | −3.18¢ | +5.09¢ (n=114) | **+5.73¢** | 62 | fails C2, C3 |
| 94 | 330 | 76.4% | 252 | 94.8% | **+0.84¢** | −1.54¢ | +5.92¢ (n=66) | **+5.07¢** | 43 | fails C2, C3 |
| 95–97 | — | — | 0 | — | — | — | — | — | — | unreadable |

Pooled (reference only): 567 fills, +0.02¢/fill. Context, pre-9/7 books, same cells: 93 −0.58¢
(n=488), 94 −0.09¢ (n=476), 95 −0.66¢ (n=53).

**What it found.** Neither readable cell earns +1.0¢ with a lower bound above zero, and in both
the orders that never filled would have earned 5–6¢ more than the ones that did. Fill selection is
**uniform across price cells** on this book; the July `mmsell3` reading that the 6¢ cell was
fill-neutral does not hold on `Fmmsell10`. Taker prints at our level are large (median 43–62
contracts), so a 3-lot would fill; it would simply fill on the same adversely selected flow, three
times over.

**Decision (pre-committed).** Sizing is closed until a per-fill edge exists somewhere on the book.
Combined with YOUNG-SERIES (KILL, same day), no such edge is on record.
