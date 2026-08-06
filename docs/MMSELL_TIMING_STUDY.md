# mmsell entry-timing study

**Command:** `{"type": "script", "name": "mmsell_timing_study"}` — `scripts/mmsell_timing_study.py`

## What it answers

Does WHEN we enter matter? A backtest over Kalshi's settled h2h history found a U-shape — selling
the cheap tail paid pre-game (+5.4¢) and in the final hour (+6.57¢) but lost through the 1–4h
in-play middle. That covered 1,137 h2h entries and nothing else. This measures it on our own book,
across every market type, on **10,337 in-play trades over 3,783 distinct markets**.

## The trap that shapes the whole design

`hours_to_close` — the obvious timing variable, captured per candidate in
`mmsell_candidate_ticks` — is **valid for scheduled and discrete markets and a fiction for
in-play ones.** Kalshi sets a sports market's `close_time` to a far-future fallback, not the end
of the contest. The script prints this validation first, every run:

| mode | avg htc at entry | avg actual time to resolution | gap |
|---|---|---|---|
| `in_play` | 145.9h | **2.0h** | 143.9h |
| `scheduled` | 44.8h | 46.8h | −2.0h |
| `discrete` | 63.4h | 55.9h | 7.5h |

Per series it is worse: `KXUFCFIGHT` reads 335h-to-close on a fight that resolves in 0.4h (pinned
at our own `htcmax=336` cap — the market's reported close is further out still). Bucketing in-play
trades by `hours_to_close` files every one of them under "24–72h" or "72h+" and measures nothing
while looking perfectly healthy.

**Two consequences beyond the bucketing.** The global `mmsell_min_hours_to_close = 1.0` floor
never binds on sports at all, so the books are *already* entering deep in-play without any rule
saying so. And a live in-play timing gate is currently impossible — it needs a forward-looking
field (Kalshi's `expected_expiration_time`), which the worker does not persist.

So the study uses a **different clock per mode**: realized time-to-resolution
(`closed_at − created_at`) for `in_play`, `hours_to_close` for `scheduled`/`discrete`.

## Standing result (2026-08-05)

### in_play — the endgame is the edge

| window | n | mkts | ¢/trade | loss% | edge |
|---|---|---|---|---|---|
| **<0.25h** | 588 | 223 | **+8.85** | 3.1% | **+11.4** |
| **0.25–0.5h** | 1318 | 588 | **+6.72** | 7.1% | **+7.3** |
| 0.5–1h | 2079 | 1010 | +2.28 | 11.8% | +2.4 |
| **1–2h** | 3488 | 1565 | **−1.55** | 15.9% | **−1.6** |
| 2–4h | 2317 | 916 | +1.77 | 11.5% | +1.9 |
| 4–12h | 283 | 121 | −4.28 | 18.7% | −4.5 |
| 12h+ | 264 | 89 | +2.09 | 8.7% | +2.1 |

The endgame half of the U-shape replicates, with far more power than the backtest that proposed
it — and it was the half predicted *most likely to be a mirage*. The largest single cell (1–2h,
n=3,488) is the one losing money.

### h2h — not a bad type, a badly-timed one

| window | n | ¢/trade | edge |
|---|---|---|---|
| <0.25h | 349 | **+8.97** | +12.3 |
| 0.25–0.5h | 582 | **+7.03** | +8.2 |
| 0.5–1h | 696 | −0.64 | −0.7 |
| 1–2h | 842 | **−3.87** | −4.0 |
| 2–4h | 339 | −1.55 | −1.7 |
| 4–12h | 64 | −11.62 | −13.2 |
| 12h+ | 32 | −13.34 | −13.0 |

`docs/MMSELL_MARKET_TYPES.md` scored h2h at +0.63¢ pooled and called it the weakest large in-play
type. That number was averaging **+8.97¢ in the final 15 minutes against −13.34¢ beyond 12 hours**.
The type finding and the timing finding are the same finding seen from two angles.

`total` behaves identically (+10.48¢ at <0.25h → −4.23¢ at 1–2h → −32.71¢ at 4–12h) and `spread`
similarly. **`player_prop` and `outright` do not** — player props peak at 0.5–1h (+7.79¢) and
outrights are strongest at 2–4h and 12h+. Late-entry is a contest-resolution effect, not a
universal law, and the per-type cut is what separates them.

### scheduled / discrete

`scheduled` runs a genuine U: +9.60¢ (<2h) and **+10.50¢ (72h+, n=210, edge +27.6)** against
−1.12¢ in the 8–24h trough. `discrete` peaks at 8–24h (+6.39¢) with a 24–72h trough. Both are
scored on `hours_to_close`, so coverage is limited to the candidate-tick window (736 of 1,528
scheduled trades; 544 of 1,619 discrete) and grows daily.

## VERDICT (2026-08-05): the timing edge does NOT survive the fill haircut

The `REAL` column projects each window's own entry-price mix through the live maker-fill
calibration (`docs/MMSELL_FILL_MODEL.md`). It is the number to gate on. Paper vs realizable:

| window | paper ¢/trade | **realizable** | coverage |
|---|---|---|---|
| <0.25h | +8.85 | **+0.50** | 63% |
| 0.25–0.5h | +6.72 | **+0.46** | 50% |
| 0.5–1h | +2.28 | +0.29 | 49% |
| 1–2h | **−1.55** | **+0.55** | 48% |
| 2–4h | +1.77 | −0.23 | 55% |
| 4–12h | −4.28 | −0.95 | 43% |
| 12h+ | +2.09 | −0.12 | 61% |

**A 13.1¢ paper spread collapses to 1.45¢ realizable, and the ordering inverts where it matters:**
the 1–2h window that looked worst on paper (−1.55¢) is the *best* realizable cell (+0.55¢), while
the <0.25h endgame that looked best (+8.85¢) lands at +0.50¢ — indistinguishable from it.

The endgame edge is composed almost entirely of fills a resting maker never gets. That is exactly
what was predicted before the test: the final in-play window is the thinnest, fastest book in the
universe, and it is where `mmsell7` (`htcmax=24`) and `mmsell11` (`htcmin=6`) both already died.
This is the third timing signal to die at the same step.

`scheduled` tells the same story — its 72h+ cell, the largest paper edge anywhere in the study at
+10.50¢, reads **−0.87¢ realizable**.

**No `.timeX` book should be built on this.** The pre-registered gates below are retained for any
future re-test (e.g. after a fill-model refresh on a larger live sample), but the current answer
to "does entry timing pay" is: on paper yes, in reality no.

Two honest limits on the verdict. Coverage is 43–63% on the in-play cells — the live calibration
only spans the cheap price cells, so the realizable figure speaks for about half of each cell. And
the calibration is borrowed from live `mmsell3` (n=359), a different market mix. Neither changes
the direction, which is consistent across all seven windows.

## Three caveats before anyone trades this

1. **Long-game confound (the serious one).** Hold time is *entry → resolution*, so a game that runs
   long lands in a later bucket. Games run long when they are close — and a close game is exactly
   where a cheap tail is live. Some unknown share of the gradient is therefore "blowouts end on
   schedule", not "entering late is safe". A rule keyed on *time remaining* only captures the part
   that is genuinely about the clock. This is the main reason a forward-looking field is needed
   before promoting anything.
2. **Detection lag.** The in-play clock is measured at settlement *detection*, up to one management
   cycle after resolution. The bias is constant in absolute terms, so it bites hardest in the
   shortest cell — read `<0.25h` as "detected within a cycle", not a precise quarter-hour.
3. **Fill-everything paper.** The endgame is a thin, fast book: precisely where a resting maker is
   picked off. Two prior timing signals died exactly here — `mmsell7` (`htcmax=24`) was the worst
   variant of its cohort, and `mmsell11` (`htcmin=6`) went +2.38¢ paper → −0.86¢ realizable.
   **Nothing here is promotable until it clears `mmsell fill model`.**

## The TAKER route — why the verdict above is not the end of it

The verdict kills timing **as a maker**. It does not kill timing, because the mechanism that
killed it is specific to resting: a maker only fills when someone crosses into them, and those are
the losers. A taker chooses the moment and keeps the whole distribution.

And the arithmetic is exact. A maker rests at the no-bid and collects the **yes-ask**; a taker
crosses to the no-ask and collects the **yes-bid**. Whether the tail hits or misses, the
difference is the same:

> **taker P&L = paper P&L − spread**

Which makes paper's fill-everything number — the thing this whole doc discounts — *achievable*,
just at a worse price. Measured over the candidate-tick window:

| in-play window | n | spread | maker paper | **TAKER** | maker realizable |
|---|---|---|---|---|---|
| **<15 min** | 170 | 2.50¢ | +6.56 | **+4.06** | +0.50 |
| **15–30 min** | 257 | 2.02¢ | +8.73 | **+6.71** | +0.46 |
| 30–60 min | 450 | 2.27¢ | +2.47 | +0.20 | +0.29 |
| 1–2h | 526 | 2.30¢ | −4.39 | **−6.68** | +0.55 |
| 2–4h | 392 | 2.31¢ | −4.94 | −7.24 | −0.23 |

The spread does **not** widen in the endgame — it is flat at ~2¢, and tighter still on h2h (1.65¢
at <15min → **+4.64¢ taker**). But taking is not better in general: pooled across all in-play
windows a taker runs **−2.64¢/trade**, and the 1–2h window is −6.68¢ taker vs +0.55¢ maker.

**Timing alone (maker) is dead; taker alone is marginal (~+0.7¢); taker + endgame gate is +4 to
+6.7¢.** The two ideas only work together.

### What the Kalshi API actually allows (checked against the docs, not inferred)

* **There is no market order type.** Only limit orders. A "market order" is a marketable limit at
  an aggressive price with `time_in_force: immediate_or_cancel` (or `fill_or_kill`).
* `post_only: true` is the maker-only flag — it is what the current mmsell entry sets. **It cannot
  be combined with `immediate_or_cancel`** (400 `invalid_parameters`), and
  `self_trade_prevention_type` is required (400 `missing_parameters` if omitted). Both already
  learned live and annotated in `live/executor.py`.
* **The taker path already exists and is proven** — the closeout order is annotated as the "EXACT
  field set of a recorded status-201 taker-IOC request". A taker entry is that payload with
  `side: "ask"`, not new infrastructure.
* **Fees do not penalise taking at our size.** Taker is `ceil(0.07 × C × P × (1−P))`, maker
  `ceil(0.0175 × C × P × (1−P))` — a 4× discount that the per-trade round-up to a cent erases
  entirely at 1-contract clips in the cheap band (both charge 1¢ at yes ≤11¢). It only becomes
  real above ~15¢ or at larger clips (~0.3¢/contract at 20-lots). So the taker's only real cost is
  the spread, and `taker = paper − spread` needs no fee correction.

  This **contradicts `docs/MMSELL_ROADMAP.md`**, which claims paper overcharges makers ~1¢/contract
  based on 492 measured contracts. If maker also ceils to 1¢ there is no correction owed. Either
  that measurement predates Kalshi's July-2025 maker-fee change (it was a flat $0.0025/contract
  before, now probability-scaled), or these series sit outside the schedule's "Maker Fees" section.
  Unresolved, and worth a Kalshi statement — it moves maker realizable by a full cent, which is
  most of the maker-vs-taker gap *outside* the endgame.

### The gating unknown: depth

`taker = paper − spread` silently assumes unlimited liquidity at the touch. It is a per-CONTRACT
number, so a window can look excellent at 1 contract and be untradeable at 20 — and the endgame is
exactly where books are thinnest.

`mmsell_candidate_ticks` now captures `depth_at_best_bid` (contracts resting at the YES bid — what
a taker entry lifts) and `depth_at_best_ask` (the YES-ask queue a maker sits behind). The study
renders the median as a `takerQ` column with its coverage, e.g. `3(100%)`. **Capture is
forward-only from 2026-08-05**, so historical windows read `n/a` by design; the column becomes
meaningful as coverage accrues. No taker book should be sized above the median depth its window
actually shows.

## Gates for a timing book

Before building any `.timeX` book:

- The window must show **edge ≥ +3.0pp above the adjacent window** at **n ≥ 300** and **≥ 150
  distinct markets** (trades within one contest are not independent).
- It must hold **within a single type**, not only pooled — `player_prop` and `outright` already
  demonstrate the pooled shape does not generalize.
- **`mmsell fill model` realizable ¢/trade > 0** on the window's price mix.
- For in-play, the worker must first persist a forward-looking resolution estimate; until then an
  in-play timing book cannot be gated live regardless of what this study says.

## Usage

```jsonc
{"type": "script", "name": "mmsell_timing_study"}
{"type": "script", "name": "mmsell_timing_study", "args": ["--maxyes", "7"]}   // live band only
{"type": "script", "name": "mmsell_timing_study", "args": ["--book", "mmsell10"]}
{"type": "script", "name": "mmsell_timing_study", "args": ["--no-types"]}
```

Taxonomy and per-cell statistics are imported from `scripts/mmsell_market_types.py` rather than
re-declared, so a type cannot mean one thing in the census and another here. Bucketing and the
clock mapping are unit-tested in `tests/test_mmsell_timing_study.py`.
