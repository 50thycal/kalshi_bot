# MMSELL offline replays and forward reads — 2026-10-03

```
SESSION: Research Lab
MODE: read-only probes (ops db reads); no book, gate, envelope or config changed
ENFORCEMENT: NEW_ONLY · AS OF: 2026-10-03 15:50 UTC
```

**Scope.** Operator request (Calvin, 2026-10-03): run the no-money probes on the ideas from the
2026-10-02 brainstorm before any real-money step, and rank them by how much they help the current
MMSELL book. Seven questions, all on **real-money fills** from four live windows. The hypotheses
and bars below were written to a scratch file **before any outcome was read** and are copied here
verbatim; nothing was re-scoped after results. Experiment OS is canonical for every standing;
this document records research evidence only.

Windows: **C** = `mmsell3` live 07-13..07-19; **A** = the seven pre-9/7 live books from 07-26;
**B** = `Fmmsell10` 09-07..09-30; **F** = forward, orders from 2026-10-01 (not inspected before
these reads). Outcome per order = `settle_NO − limit` at one contract; settlement from the mmsell
paper record of the same ticker.

## Ranking (helps the current book most → least)

| # | lever | verdict | the number |
|---|---|---|---|
| 1 | **Contest-key correction** (`contestkey=split` on the live book) | found, not pre-registered — evidence for a bound correction | Fmmsell10's shipped key refused 125 distinct KXRAIN markets vs 23 opened (9/7–9/30); subject-split series: **93 live fills across 9 books, 0 losses, ~+7¢**. `max_event_rungs=3` still binds, so realistic gain is ~2 fills/day, not every refusal |
| 2 | **Size ramp** (P-SIZE) | **SUPPORTS-RAMP** | taker ≥ 3 contracts **+3.23¢** (n=246) vs 1–2 lot **−3.90¢** (n=63); separation (matched only) +7.14¢, 90% band [+0.18, +14.7]. One window; one of three weeks reversed |
| 3 | **Slow-information cell** (P-SLOW) | retro leg **not killed**; forward accruing (1/100) | window C: cell +1.96¢ (n=79) vs rest +0.15¢ (n=285), 90% band [−10.1, +9.8]. Same sign in A (+5.31 vs −1.73) and B (+6.77 vs +0.04), both seen before |
| 4 | **Decision-time spread ≥ 3¢** (P-SPREAD) | forward only, accruing (6/80) | no untouched retro window exists |
| 5 | **Rest longer than 4 h** (P-REST) | **HOLD** | B would-fill +4.89¢ (n=88, 90% [+1.72, +7.16]) passes; A −4.35¢ (n=351) fails; A without `mmsell10b` −0.96¢ (n=263) |
| 6 | **Pre-game only** (P-PREGAME) | **HOLD at floor — not viable** | pre-start fills: A 35, B 41, C 3 of 617 / 238 / 62 timed fills; the book's flow is in-play |
| 7 | **Reactive cancel** (P-REACT) | **KILL** | avoided fills were winners: T1 L=2 s avoided n=104 **+3.01¢**; policy $5.74 vs actual $8.87 |

Two regularities worth carrying forward: **every time-based lever flips sign between window A
(August) and window B (September)** — fill timing on 10-02, rest-longer today — so timing is a
regime property on this book, not an edge; and **the only cross-window-stable market signals are
structural** (the slow-information cell positive, spread-type markets negative).

## Pre-registration (verbatim, frozen ~15:35 UTC before any outcome read)


Outcome everywhere: per order, (settle_NO - limit) cents at 1 contract; settle_NO from any mmsell
paper_trades row on the same ticker (resolved_value; side-adjusted). Bootstrap: settlement/creation
date blocks, 5,000 resamples, seed 20261003.

### P-SIZE  (do the contracts a bigger order adds fill on worse flow?)
Sample: Fmmsell10 fills since 2026-09-16 with a WS fill event, matched to execution_trade_events
at the fill instant. S = taker order size = sum count_fp of YES-taker prints, same market, same ts_ms.
Marginal contracts of a k-lot fill only when S >= k.
SUPPORTS-RAMP: mean(S>=3) >= mean(all) - 1.0c.  AGAINST-RAMP: mean(S>=3) - mean(S<3) <= -3.0c with
bootstrap 95th pct < 0.  Otherwise INCONCLUSIVE. (S>=10 reported for a 10-lot.)

### P-REACT  (cancel the resting order when our level comes under attack)
Sample: Fmmsell10 orders created since 2026-09-16 that filled with a WS fill event.
Trigger T1: first YES-taker print at yes_price >= our YES price (100 - limit) after the order rested,
not one of our own fill trades. Trigger T2 (burst): first instant trailing-60s YES-taker contracts at
that level >= 50. Cancel latency L = 2 s primary (1 s, 5 s reported). Policy cancels at trigger; a
fill landing >= L after the trigger is avoided (value 0). Unfilled orders cost nothing either way.
PASS: avoided fills mean <= -2.0c, n_avoided >= 50, bootstrap 95th pct of avoided mean < 0, AND
kept fills >= 40% of all fills.  KILL: avoided fills mean >= 0.  Else HOLD.

### P-REST  (extend the 4 h timeout)
Sample: timeout-cancelled orders, window B (Fmmsell10) and window A (pre-9/7 books, from 2026-07-26).
Would-fill proxy: any mmsell_position_ticks row after the cancel with no_bid < limit (our level
cleared; 5-min tape => lower bound on touches). Score would-fill orders at settlement.
PASS: would-fill mean >= +1.0c with bootstrap 5th pct > 0 in window B, and window A mean > 0.
KILL: window B would-fill mean <= 0.  Else HOLD.

### P-PREGAME  (rest only before the contest starts)
Sample: fills in windows A and B whose event ticker encodes a start time (YYMONDDHHMM, ET).
Split: fill exchange time before vs after start.
PASS: pre - inplay >= +2.0c in BOTH windows with >= 50 fills per side, bootstrap 5th pct > 0 in B.
KILL: pre <= inplay in either window at the floor.  Else HOLD.

### P-SLOW  (slow-information cell; set frozen in MMSELL_TYPE_RANK_THESIS.md on 2026-10-01)
Cell: market types {event_stat, mention, exact_score, outright, game_prop} via
scripts/mmsell_market_types.classify. REST = every other classified type.
Retro leg (can only kill): window C = mmsell3 live, 2026-07-13..07-19 (never used by TYPE-RANK).
KILL if cell mean <= REST mean at >= 30 cell fills. Forward leg (decides): Fmmsell10 fills on orders
created from 2026-10-01T00:00Z: PASS at >= 100 cell fills, cell mean >= +2.0c, separation > 0 with
bootstrap 5th pct > 0.

### P-SPREAD  (decision-time YES spread >= 3c)
No independent retro window (candidate ticks begin 2026-07-23; A and B were seen 10-02).
Forward only: Fmmsell10 orders created from 2026-10-01T00:00Z (not inspected by this session),
spread = execution_order_context.spread. PASS at >= 80 fills with spread >= 3: mean(>=3) - mean(<=2)
>= +3.0c, bootstrap 5th pct > 0. KILL: separation <= 0 at the floor.

## Results by probe

### P-SIZE — the extra contracts of a bigger order fill on the better flow

401 Fmmsell10 fills since 09-16 with a WebSocket fill event and a settlement (396 with tape in
the market). Taker order size = all YES-taker prints on the market at the fill's exchange
millisecond.

| taker size | fills | realized | loss rate |
|---|---|---|---|
| unmatched (no print at the fill ms) | 92 | +3.67¢ | 3.3% |
| 1–2 | 63 | **−3.90¢** | 11.1% |
| 3–9 | 12 | +7.50¢ | 0.0% |
| 10–58 | 79 | +4.57¢ | 2.5% |
| ≥ 59 | 155 | +2.22¢ | 5.2% |
| **≥ 3** | **246** | **+3.23¢** | 4.1% |

mean(S≥3) +3.23¢ ≥ mean(all) +2.21¢ − 1.0 → **SUPPORTS-RAMP**. By week (≥3 vs <3): wk38 +2.92 /
−2.14, wk39 +4.14 / −17.65, wk40 +2.34 / +7.33 — the last week reverses on 21 small fills. Read:
the contracts a 3-lot adds fill only on sweeps of ≥ 2–3 contracts, and those were not adversely
selected on this sample. It sets the prior for `docs/MMSELL_SIZE_SPLIT_CANARY.md`; it is not a
promotion.

### P-REACT — cancelling when our level is under attack — KILL

| trigger | lead | avoided fills | avoided mean | kept | policy $ vs actual |
|---|---|---|---|---|---|
| T1 any YES-taker print at our level | 1 s | 108 | +3.26¢ | 73% | $5.35 vs $8.87 |
| T1 | **2 s** | **104** | **+3.01¢** (90% −0.35, +5.82) | 74% | **$5.74 vs $8.87** |
| T1 | 5 s | 99 | +2.74¢ | 75% | $6.16 vs $8.87 |
| T2 burst ≥ 50 contracts / 60 s | 2 s | 84 | +3.32¢ | 79% | $6.08 vs $8.87 |

Avoided mean ≥ 0 → **KILL**. Prints at our price before our fill precede the queue ahead being
worked down by ordinary longshot flow, which is the flow the book is paid by. Extends FLOW-VETO's
null (pre-post, 10 min) to the seconds before a fill.

### P-REST — extending the 4 h timeout — HOLD

| window | timeout orders taped after cancel | level later cleared | would-fill mean | loss | 90% band |
|---|---|---|---|---|---|
| B Fmmsell10 | 123 | 88 (72%) | **+4.89¢** | 2.3% | +1.72, +7.16 |
| A pre-9/7 books | 498 | 351 (70%) | **−4.35¢** | 11.4% | −8.62, −0.09 |
| A without `mmsell10b` (sensitivity) | — | 263 | −0.96¢ | 8.0% | −4.59, +2.29 |

B by time after cancel: 0–2 h +1.29 (17), 2–8 h +2.14 (21), 8–24 h +7.20 (20), 24 h+ +7.30 (30).
**Instrument caveat found after the read, stated as such:** `mmsell10b` rested 1¢ above the market
bid (the +1¢ offset arm), so "no-bid below our limit" fires for it without any trade; its 83
rows read −15.73¢. Even without it A is not positive, so the pre-registered rule's HOLD stands.
The replay needs no live change and can be re-run on future timeouts.

### P-PREGAME — rest only before the contest starts — not viable

| window | fills with a start time in the ticker | pre-start | in-play | pre − in-play |
|---|---|---|---|---|
| A | 617 of 1,445 | 35, +0.94¢ | 582, −2.28¢ | +3.22 (90% −5.7, +11.1) |
| B | 238 of 727 | 41, −0.66¢ | 197, +0.07¢ | −0.72 |
| C | 62 of 364 | 3 | 59, −4.64¢ | — |

Floors (50 per side) not met; B shows no advantage; a pre-game rule would remove 85–95% of the
book's timed fills. Closed as a lever.

### P-SLOW — the slow-information cell

| window | cell fills | cell | rest fills | rest |
|---|---|---|---|---|
| **C (independent)** | **79** | **+1.96¢** (6.3% loss) | 285 | +0.15¢ (8.4%) |
| A (seen 10-01) | 154 | +5.31¢ (1.9%) | 1,224 | −1.73¢ |
| B (seen 10-01) | 52 | +6.77¢ (0.0%) | 675 | +0.04¢ |

Window C's cell is World Cup exact scores and mentions (KXWCSCORE 26, KXWCMENTION 16), not the
weather-heavy cells of A/B. Retro leg: separation +1.81¢ > 0 → **not killed** (90% band
−10.1 to +9.8, so not evidence of a size either). Forward leg (decides): 1 settled cell fill since
10-01 against a floor of 100. **Capacity read (outcome-blind):** the `mmsell10` paper book sees
~10 cell candidates a day, of which live ordered 26%; the gate funnel shows the contest cap, not
the tier bar, refusing most of them (344 distinct cell markets `skip_contest_cap` vs 19
`skip_live_tier`). The forward leg continues on `Hmmsell10`, the registered successor of the same
book (extended before any `Hmmsell10` order exists).

### The contest-key finding (outside the slate)

From the gate funnel (`live_paper_parity_events`, Fmmsell10, 9/7–9/30, distinct markets per
outcome): KXRAIN opened 23 / `skip_contest_cap` 125; KXTRUMPSAY 3 / 49; KXFEDMENTION 1 / 17. The
shipped `contest_key_of` folds a subject-split series to one contest per date
(`docs/MMSELL_CONTEST_KEY_SUBJECT_SPLIT.md`), so `contestcap=1` refused markets sharing no
outcome. Live record on those series across all books: 93 fills, 0 losses, ~+7¢. Carried into the
successor as a bound correction (`contestkey=split`), not as a candidate.

### P-SPREAD — forward only

6 settled fills with decision-time spread ≥ 3¢ since 10-01, floor 80. Continues on `Hmmsell10`.

## Ops requests (all read-only)

2026-10-02: `rl-tier-ct-1`, `rl-tier-truth-1`, `rl-tier-slice-1`, `rl-tier-oosA-1`,
`rl-tier-barred-1`. 2026-10-03: `rl-pr-inv-1` (inventory), `rl-pr-q6-1` / `rl-pr-q7-1`
(orders + trade tape), `rl-pr-rest-1`, `rl-pr-fills-1`, `rl-pr-fwd-1`, `rl-pr-cap-1`,
`rl-pr-gate-1`, `rl-cn-env-1` (env read). Results rotate after 80 files; the numbers above are
the record. Channel reset to `noop`.
