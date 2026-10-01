# TYPE-RANK — do the market types that earned most on real fills keep earning most?

*Thesis written 2026-10-01, before any per-type read of live outcomes. The predictions, bars and
kill criteria are pre-registered here and are not re-scoped after results. Operator request
(Calvin, 2026-10-01): "review which markets give us the best results and increase our position
size on those." Run doc: [`IDEA_MODEL_20261001_MMSELL_SIZEUP.md`](IDEA_MODEL_20261001_MMSELL_SIZEUP.md).
Status: **pending probe** (no script yet; building it is a `kalshi-probe-builder` step and its
`ops_runner.py` allowlist entry is the operator's hard stop).*

## One-liner

Rank the live book's market types (h2h, player prop, price strike, total, spread) by realized
¢ per fill on real money, then put more contracts on the top-ranked types. This is only worth
doing if the ranking **persists** into fills it has not seen.

## Why market *type*, not series

The operator's request is about markets. The series is the natural unit, and it is untestable:

| outcome-blind census (`sizeup-census-1001`) | value |
|---|---|
| series with ≥ 20 live fills in both windows | 5 |
| series with ≥ 10 in both | 14 |
| largest series' share of fills | 23% (`KXMLBHR`; MLB regular season ended 09-28) |
| window-B fills in series absent from window A | 42 across 10 series |

`MMSELL_SERIES_SCORECARD_HANDOFF.md` §3 already showed that a series needs ~70 contests before its
own record can even flag a disaster, against a family edge of +0.64pp in this price band. Ranking
"winners" at the series level is not resolvable on this book's flow. Type cells pool many series:

| type (taxonomy `scripts/mmsell_market_types.py`) | live fills, window A | live fills, window B |
|---|---|---|
| h2h | 263 | 359 |
| player_prop | 358 | 138 |
| price_strike | 189 | 102 |
| total | 198 | 59 |
| spread | 148 | 24 |
| everything else | 272 | 57 |

## Why sizing is a near-pure multiplier here (measured, outcome-blind)

`sizesel-census-1001b`: for the 301 Fmmsell10 fills matched to the WS-019 trade tape, the taker
order that filled us (all matches at the same instant, same side) had a median size of **59
contracts**; 80% were ≥ 3 and 76% ≥ 10. A 3-contract resting order would therefore fill fully on
the same events that fill a 1-contract order. The marginal contracts inherit the same selection as
the first, so a size-up multiplies whatever the per-fill edge is in the chosen cells. The question
is entirely which cells.

## Which bucket it moves, and why the fill does not select against it

It moves nothing between buckets. It multiplies the **filled** bucket in the selected types. The
census above is the reason the fill does not select differently for the extra contracts.

## Mechanism (and the honest prior)

Different contract structures attract different takers. An h2h winner in-play invites a bettor
backing a comeback; a player-prop tail invites a fan; a price strike invites a hedger or a bot
watching spot. If some of those flows are systematically less informed, the type with the less
informed flow earns more per fill, and that should persist because it is a property of the
structure, not of a team or a week.

**Prior LOW–MEDIUM.** Against: `MMSELL_CELL_SIZE_CENSUS.md` found fill selection uniform across
price cells; the paper type books (`MMSELL_TYPE_BOOKS.md`) projected nearly identical realizable
¢/trade for every type; and the season is changing the sport mix inside every type (MLB out; NFL,
NCAAF, NHL, NBA in). For: no live→live test at the type level has been run, and this is the only
form of "size up the best markets" the data can grade.

**Not a duplicate.** `mmnext-oos-0929` ranked *series* on *paper* history and found no
persistence into live fills. This ranks *types* on *live* fills. CELL-SIZE ranked *price cells*.

## Pre-registered probe (to be built as `scripts/mmsell_type_rank_probe.py`)

**Outcome (real money):** per filled order, `(settle_NO − limit) × min(filled, 1)`, fill and
settlement read exactly as `mmsell_thin_market_probe.py`.

**Cells (frozen):** `market_type` from `mmsell_market_types.classify(series)`; `unclassified`
excluded. A cell is **readable** in a ranking window if it has ≥ 100 settled live fills there.

**Rule (frozen):** **TOP** = the two readable cells with the highest realized ¢/fill in the ranking
window. **REST** = every other readable cell. Ties broken by fill count.

**Two legs.**

- **Leg R (retrospective; can only kill).** Rank on window A (live NO buys of `mmsell10`,
  `mmsell10a`, `mmsell10b`, `Lmmsell10`, `Cmmsell10`, `Dmmsell10`, `Emmsell10`, from
  `2026-07-26T00:00:00Z` to `2026-09-07T02:03:36Z`). Score TOP vs REST on window B (`Fmmsell10`,
  `2026-09-07T02:03:36Z` to `2026-10-01T00:00:00Z`). Window-B per-series outcomes appeared in
  reports on 09-29 and 09-30, so this leg can kill but cannot promote.
- **Leg F (forward; decides promotion).** Rank on A ∪ B (all live fills before
  `2026-10-01T00:00:00Z`). Score TOP vs REST on `Fmmsell10` (or a registered successor tag of the
  same book) for orders decided from `2026-10-01T00:00:00Z`.

**Bootstrap:** settlement-date blocks, 10,000 resamples, seed 20261001 (fills settling the same
day are not independent).

### Predictions, bars and kill criteria

| id | claim | PASS | KILL / HOLD |
|---|---|---|---|
| **T0** instrument | type and outcome are readable | ≥ 95% of filled orders classify and settle-map in each window | < 95% → **HOLD (instrument)** |
| **T1** cells | the ranking has something to rank | ≥ 4 readable cells in each ranking window | fewer → **HOLD (instrument)** |
| **T2** retrospective | the window-A ranking is not reversed in window B | separation TOP − REST in B **> 0** at ≥ 150 TOP and ≥ 100 REST fills | separation ≤ 0 at the floors → **KILL** |
| **T3** forward floor | the forward read is powered | ≥ 150 TOP and ≥ 100 REST settled fills from 10-01 | fewer → **HOLD (accrual)**; re-run weekly |
| **T4** forward | the ranking persists into unseen fills | separation ≥ **+1.5¢** with date-block bootstrap 5th percentile > 0, **and** TOP realized ≥ **+1.0¢/fill** | separation ≤ 0 at T3 floors → **KILL**; otherwise **HOLD (underpowered)** |

**Decision rule (pre-committed).**

- **PROMOTE** = T0–T2 pass and T4 passes. "Size ×3 in the top two types" becomes a candidate
  **sizing treatment** for a successor live canary. It changes contracts per order and exposure
  per market, so it is an operator hard stop with a restated envelope (daily stop, canary budget,
  per-event exposure) and a twin. It is never switched on in the running book.
- **KILL** on T2 or T4 closes market selection as a sizing lever on this book: series (untestable),
  price cell (CELL-SIZE), and type (this) are then all done, and sizing reduces to a capital
  decision on a book whose per-fill edge is indistinguishable from zero.

**Reported alongside (never decisive):** per-type ¢/fill and win rate in every window; the ranking
each leg produced; $/month at ×1 and at ×3-in-TOP; the sport mix inside each type before and after
10-01, so a season-driven reversal is visible as one.

## Cost + capacity

- **Fees:** maker fills bill ~0.01¢; a 3-lot pays the same per contract.
- **Tail:** a 3-lot loses ~$2.80 on one settlement. The current $5 daily stop is under two such
  losses; a successor envelope must restate it.
- **Honest ceiling:** if the top two types carry ~15 fills/day at +2¢, ×3 adds roughly
  15 × 2¢ × 2 × 30 ≈ **$18/month**. A PASS is the first sizing lever with a positive number behind
  it; it does not approach $100/month alone.

## Correlation

Same return driver as the live book; a multiplier on part of it. No diversification credit, and
×3 raises the book's correlation to itself (more contracts on the same contests).

## RESULTS

*(empty until the probe runs; recorded here, in `RESEARCH_JOURNAL.md` and in the scorecard row;
the bars above are not edited)*
