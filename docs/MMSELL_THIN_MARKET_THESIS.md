# THIN-MARKET — do mmsell fills in quiet markets escape adverse selection?

*Thesis written 2026-09-29, before the probe ran. The predictions, bars and kill criteria are
pre-registered here and are not re-scoped after results. Operator request (Calvin, 2026-09-29):
"do 2 and 3 next". This is #2. Status: **KILL (probe run 2026-09-29) — family closed; see RESULTS.***

## One-liner

Rest mmsell NO bids only (or preferentially) in markets that have traded little in the last
24 h. The hypothesis is that informed YES buyers concentrate where the action is, so fills in
quiet markets are less adversely selected.

## Where the idea came from (and why that is a weakness)

On 2026-09-29, a quick read split Fmmsell10's live fills (twin P&L on the same market) by how
much history the series had in the incumbent `mmsell10` paper book before 2026-09-07 (ops
`mmnext-oos-0929`):

| series history before 9/7 | fills | ¢/fill |
|---|---|---|
| ≥ 30 trades, was profitable | 309 | −0.98 |
| ≥ 30 trades, was losing | 143 | −0.45 |
| **< 30 trades (thin/new)** | 230 | **+2.28** |

The same read showed that series-level past P&L does **not** predict future fills, so a
series keep-list is dead. The thin-history bucket is the one cell that stood out. **It was
spotted after the fact, on the same sample.** This probe therefore does not test it on
Fmmsell10 first. It tests a *market-level, outcome-blind* version of the idea, on **earlier live
books** the observation never touched. Fmmsell10 is used only as confirmation.

## Mechanism

A post-only NO bid gets hit by a YES taker. In an active market, a meaningful share of YES
takers are informed: news, a game state change, a price feed. That is the adverse selection
that turns the twin's +1.93¢ into live +0.14¢. In a market nobody is trading, the takers who
do show up are more likely the retail longshot buyers the strategy exists to sell to. The
prediction is that per-fill P&L falls as recent activity rises.

**Not a duplicate:** spread and depth filters are DEAD in the cheap band because 96% of trades
sit at a 0–2¢ spread, so there is no variance to filter on (`MMSELL_ROADMAP.md` §5). Traded
volume varies by orders of magnitude across these markets, and it measures *flow*, not the
resting book. Type filters (`MMSELL_TYPE_BOOKS.md`) and series lists (above) select on
labels, not on activity.

**Why it could fail:** quiet markets may simply fill less, and what does fill may be the rare
informed trade, which would make fills there *more* toxic. OFLOW's null (flow does not predict
price) and FLOW-VETO's null (pre-post flow does not predict losing fills, 2026-09-29) both
argue for a **LOW** prior.

## Pre-registered probe (`scripts/mmsell_thin_market_probe.py`)

**Samples (provenance: bot DB for our orders and fills; Kalshi public REST for candles and
settlement).**

- **Primary (out-of-sample):** live NO buys of `mmsell10`, `mmsell10a`, `mmsell10b`,
  `Lmmsell10`, `Cmmsell10`, `Dmmsell10` and `Emmsell10` created before
  `2026-09-07T02:03:36Z`.
- **Confirmation:** `Fmmsell10` from `2026-09-07T02:03:36Z`.

**Feature (frozen): `V24`.** Contracts traded in the market over the 24 hourly candles whose
`end_period_ts` is at or before the order's decision instant. Only complete hours are used, so
nothing after the decision leaks in. The source is
`GET /series/{series}/markets/{ticker}/candlesticks?period_interval=60`, where the series is the
ticker prefix; volume is `volume_fp`, else `volume`. The decision instant is
`execution_order_context.decided_at`, else `live_orders.created_at`. Cumulative volume since
the market opened is reported, but does not decide.

**Split (frozen, outcome-blind):** tercile cut points of `V24` over **all primary orders**,
filled or not. "Thin" is the bottom tercile. The same cut points are applied to the
confirmation sample.

**Outcome (real money):** per filled order, `(settle_NO − limit) × min(filled, 1)`. Fill time
and quantity are read exactly as in `mmsell_flow_veto_probe.py`. Settlement comes from
`GET /markets/{ticker}`.

### Predictions, bars and kill criteria

| id | claim | PASS | KILL |
|---|---|---|---|
| **T0** instrument | the feature is readable | `V24` for ≥ 85% of primary orders | < 85% → **HOLD (instrument)** |
| **T1** floor | enough thin fills | ≥ 100 settled fills in the thin tercile (primary) | < 100 → **HOLD** |
| **T2** primary | thin fills beat the rest | (thin mean − other-two-terciles mean) ≥ **+2.0¢**, bootstrap 5th percentile > 0 (2,000 resamples, seed 20260929), **and** thin mean ≥ **+1.0¢/fill** | separation **≤ 0** at the T1 floor |
| **T3** confirmation | it holds on Fmmsell10 | separation **> 0** with ≥ 40 thin fills (fewer → **HOLD**) | separation **≤ 0** with ≥ 40 thin fills |

**Decision rule.**

- **PROMOTE** = T0–T3 all pass. That makes a thin-market filter a candidate treatment arm for a
  successor canary. Arming is a hard stop, and a probe PASS does not authorize it.
- **KILL** = either kill cell trips.
- **HOLD** = anything else.

**Reported but not decision-driving:**

- fill rate by tercile;
- cumulative-volume terciles;
- per-book separation;
- the realized $ the filter would have kept vs. removed.

**No lookahead.** Candles are cut at the last complete hour before the decision. Settlement
only scores. The tercile cut points use the feature, never the outcome.

## Cost, capacity and value to the $100/month goal

A filter can only shrink the book, by a third or more. If thin fills truly earn about +2¢ at
1 contract, that is roughly $3–5/month at today's volume. The value is that it is the first
MM lever that would make the per-fill edge clearly positive, which is the precondition for
adding size (idea #3). Without that, adding size just scales a breakeven book.

## Correlation

This is the same book as Fmmsell10. It is a selection filter, not a new edge.

## RESULTS

**Probe run 2026-09-29 (ops `thin-probe-20260929-1`, code `2e6574f`). Verdict: KILL, on both
T2 and T3.**

The V24 tercile cut points, frozen from the primary sample, were thin ≤ 1,589 and mid ≤ 11,365
contracts.

| sample | tercile | orders | fill rate | settled fills | realized/fill |
|---|---|---|---|---|---|
| primary (pre-9/7 books) | thin | 855 | 43.6% | 373 | −1.09¢ |
| | mid | 854 | 56.1% | 479 | −1.71¢ |
| | thick | 855 | 69.0% | 590 | **+0.38¢** |
| confirmation (Fmmsell10) | thin | 264 | 58.0% | 145 | −0.85¢ |
| | mid | 243 | 70.0% | 164 | −0.53¢ |
| | thick | 493 | 76.9% | 374 | **+0.97¢** |

| gate | result |
|---|---|
| T0 instrument | 2,564 / 2,564 primary orders had V24 → PASS |
| T1 floor | 373 thin fills → PASS |
| T2 primary | separation **−0.53¢**, bootstrap 5th percentile −3.19¢ → **KILL** (≤ 0) |
| T3 confirmation | separation **−1.36¢** at n_thin = 145 → **KILL** (≤ 0) |

**What it found.** Quiet markets fill less often and do not fill better. If anything the ordering
runs the other way: in both samples, the only positive tercile is the *busiest*. That reversal is
a post-hoc observation, and it is **not** a new hypothesis this probe can promote. It varies by
book (mmsell10a thick +3.99¢, mmsell10b thick −4.11¢) and would need its own pre-registration on
fresh data. The one clear, consistent point is negative: low recent activity does not mark
retail-only flow on these markets. The "thin-history series" cell that motivated this probe
(+2.28¢ on Fmmsell10) did not survive translation to a market-level, outcome-blind feature.

**Cumulative volume** (reported only): separation −0.10¢, so there is no signal there either.

**Decision (pre-committed).** Closed. Together with RUNAWAY-CHASE, FLOW-VETO and the
series-persistence read the same day, every selection lever tried on 2026-09-29 is null. The
mmsell book's live edge is about +0.2¢ per fill, and no tested filter moves it.

*(A duplicate request, `thin-probe-20260929-2`, was queued when the first run looked lost. It runs
the same frozen code on the same data and does not change this verdict.)*
