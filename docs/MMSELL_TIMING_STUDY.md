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
