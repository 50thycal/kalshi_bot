# The mmsell ANCHOR SET — forward-testing tail mitigation as paper books

**Five paper-only books (`mmsellA1`–`mmsellA5`) that each add exactly one tail-mitigation
mechanic on top of the `mmsell10` entry.** No live capital is involved. The point is to accrue
forward-tested n on the three mechanics that the backtests said were promising but underpowered,
so that a future "anchor" decision (a larger position size on one book) rests on measured
out-of-sample evidence rather than on an 11–23 trade backtest slice.

## Why this exists

mmsell sells cheap tails. The P&L shape is many small wins (+3–6¢) and a rare near-full-stake
loss (−93¢). At a 7¢ entry the break-even win rate is **93.9%** — so the entire question is
whether the loss tail can be cut without eating the thin premium that pays for it.

Three candidate answers came out of `docs/MMSELL_CRYPTO_STUDY.md` (a Kalshi-history backtest) and
`docs/MMSELL_EXIT_STUDY.md` (a replay of our own captured intraday ticks):

| mechanic | backtest verdict | why it still needs forward n |
|---|---|---|
| bid-triggered stop-loss | **works** — every bid-triggered level improved **both** mean and 5th-pctile tail vs hold (best: bid L15 K1, −0.56¢ vs −3.67¢ hold, p5 −21.5¢ vs −95.5¢) | measured on `htc<1h` crypto, because Kalshi only serves ~1h of candles for these series. mmsell trades `htc≥1h`. **Different population.** |
| volatility **entry** gate | **right sign, underpowered** — calm tape +2.85 to +5.25¢ at 100% win; active tape −39¢. n=13–17. | n is far too small to separate the effect from noise |
| short strangle | **most intriguing, most fragile** — +3.30¢/pair at 100% win, but n=23; the 95% lower confidence bound is 87.8% vs a 93.9% break-even | needs ~**82 clean pairs** to clear its bound. Free to accrue in paper. |

The volatility **exit** gate is deliberately absent: the backtest killed it (it fires on 71–100%
of positions and is far worse than holding at every window/threshold).

## The books

All five sit on the **mmsell10** base — `lo=5, hi=10, maxyes=7` — so entry is held constant and
**`mmsell10` itself is the control**. Each book varies exactly one thing.

| tag | spec | mechanic |
|---|---|---|
| `mmsellA1` | `stopl=12, stopk=2` | tight confirmed stop |
| `mmsellA2` | `stopl=20, stopk=2` | medium confirmed stop |
| `mmsellA3` | `stopl=30, stopk=2` | loose confirmed stop |
| `mmsellA4` | `volw=6, volv=6` | volatility ENTRY gate |
| `mmsellA5` | `strangle=1` | two-sided short strangle |

### A1–A3 — the stop-loss level sweep

Exit the short when the **yes-bid** is at or above `stopl` for `stopk` consecutive manage cycles.

Two design choices worth stating, because both were arrived at the hard way:

**The trigger is the yes-BID, never the mid or the ask.** At these prices books quote wide — a
`bid 8 / ask 62` market has a mid of 35, which clears a 30¢ stop with no real buyer anywhere near
it. Mid- and ask-triggered stops in the backtest fired on ~100% of positions and returned −74 to
−91¢, a pure quoting artifact. A rising *bid* is genuine buying interest. This is pinned by
regression tests in `tests/test_mmsell_anchor_set.py`.

**`stopk` is held at 2 across all three levels.** The sweep varies the level only, so "tight or
loose" gets a clean answer. The backtest's single best cell was actually `L15 K1` (K=1 exits
earlier and preserved more mean), but every book added splits the available entry flow, and n is
the binding constraint here — so the confirm is fixed at the conservative K=2 and **K=1 is the
follow-up experiment if a level wins**, not a fourth book competing for the same trades.

The levels bracket the backtest optimum (which sat at L12–L15, *tighter* than the ~30¢ my
hand-derived estimate had predicted) with one clearly looser arm at L30 to confirm the shape.

### A4 — volatility entry gate

Before entering, read the last `volw=6` recorded mids for the candidate market
(`mmsell_candidate_ticks`). If the range over that window is `≥ volv=6`¢, skip the entry.

**The gate does not fire on thin history.** With fewer than 3 recorded ticks it passes the
candidate through exactly as the control would. This is deliberate: a newly in-band market has no
tape, and if the gate blocked those, A4 would differ from mmsell10 by *when markets were
discovered* rather than by volatility, and the A/B would be meaningless. It also fails soft —
a database error admits the trade rather than silently turning the book off.

### A5 — the short strangle

Enter **both** mutually-exclusive cheap tails of the same event: sell the cheap YES on a high
strike (the normal mmsell trade) and sell the cheap NO on a low strike (implemented as a
`side="yes"` leg bought at `best_yes_bid`, the mirror band `100-hi … 100-lo`).

The structural claim: the upper leg loses only if the market settles YES, the lower leg only if
it settles NO. **One settlement can never lose both**, so the pair collects two premiums against
at most one loss.

`_event_has_both_tails()` requires an event to actually contain both a cheap-YES and a cheap-NO
strike under the price cap before either leg is entered. A lone tail is an ordinary mmsell trade —
entering it as a "strangle" would silently make A5 a duplicate of mmsell10 and destroy the
pairing the thesis rests on.

**Fixed 2026-08-03 — A5 had never traded, and not because it was selective.** The pairing gate
read `mk.get("yes_bid")` / `mk.get("yes_ask")` straight off the nested market payload. Kalshi's
live events endpoint sends those quotes as `yes_bid_dollars` / `yes_ask_dollars` **strings and
omits the integer-cent keys entirely**, so both reads returned `None` for every market, every
market was skipped, and the gate returned `False` for every event — A5 was structurally incapable
of opening a position from the day it shipped. Its zero rows were a plumbing bug wearing the
costume of a selective book, which is the dangerous shape: the thesis predicted slow accrual, so
"no trades yet" looked like the expected outcome. It now reads through
`scanner.metrics.market_price_cents`, which accepts both shapes, and the strangle tests run
against both payload shapes so the same gap can't reopen.

Two transferable lessons: **never read a Kalshi price field raw** — the `_dollars`/`_fp` variants
are what live data actually carries — and **a paper book at exactly zero rows is a bug report
until proven otherwise**, never evidence of selectivity.

Honest caveat, carried forward from the backtest: an event with both tails simultaneously cheap is
an event the market *prices as low-volatility*. So A5 is a pure short-volatility bet on a
subsample selected for low volatility. That is the same signal the A4 entry gate is chasing,
expressed structurally — if both books work, they are probably not two independent edges.

## Pre-registered gates

Registered before any data arrives. Read them against the **control (`mmsell10`) over the same
window**, not against absolute numbers — the anchor set launched mid-regime and market supply
varies.

| book | promote (consider larger size) | kill |
|---|---|---|
| `mmsellA1` / `A2` / `A3` | at **n≥100 settled**: 5th-pctile P&L clearly above control **AND** mean ≥ control − 0.3¢/trade. Only the single best level is a promote candidate. | mean ≥ 1.0¢/trade below control at n≥100 (the stop is selling winners), or tail no better than control |
| `mmsellA4` | at **n≥100 settled**: mean/trade above control by ≥ 1.0¢ **AND** the gate actually rejected ≥ 15% of candidates (`skipped_vol_gate`) | at n≥100, mean ≤ control, or rejection rate < 5% (the gate is inert and the book is a duplicate) |
| `mmsellA5` | at **n≥82 clean pairs**: the 95% lower confidence bound on the pair win rate clears **93.9%** (the 7¢ break-even) — the exact bound the backtest failed at n=23 | lower bound fails at n≥82, or paired entry rate is so low the book can't reach 82 pairs in a quarter |

**None of these promote to live on the paper number alone.** Every mmsell book must also clear
`mmsell fill model`'s **realizable ¢/trade** (`docs/MMSELL_FILL_MODEL.md`) — paper assumes a
resting maker order always fills, live it fills ~70% and misses the winners. The anchor set
inherits that requirement; A1–A3 additionally take a *taker* exit, whose cost the paper engine
does not currently model, so a winning stop book's realizable number will be worse than its paper
number by more than the usual gap.

## Where the code lives

| piece | file |
|---|---|
| book specs + spec-grammar keys (`stopl`, `stopk`, `volw`, `volv`, `strangle`) | `kalshi_bot/config.py` (`mmsell_variants`, `mmsell_book_by_tag`) |
| stop execution | `kalshi_bot/paper/engine.py` (`_anchor_stop_hit`, wired into `_mark_or_exit` → `closed_sl`) |
| vol gate + strangle pairing / mirror leg | `kalshi_bot/mmsell/tracker.py` (`_vol_gate_blocks`, `_event_has_both_tails`) |
| tick history reads | `kalshi_bot/repository.py` (`recent_candidate_mids`, `recent_position_yes_bids`) |
| regression tests | `tests/test_mmsell_anchor_set.py` |

## Reading the results

`mm check 1` picks the anchor books up automatically (they write `paper_trades.strategy` like any
other book). Compare each against `mmsell10` in the same run. Two standing cautions from this
project's own history:

- **Require 3+ consecutive checks at growing n before calling a gate cleared.** Earlier in the
  mmsell program two books "cleared" and then reversed sign (+0.84 → −0.57, +1.00 → −0.45) as n
  doubled.
- Expect A5 to accrue slowly — it needs an event with *both* tails cheap, which is a small subset
  of the flow mmsell10 sees. **But slow is not zero:** A5's clock starts at the 2026-08-03 fix
  (see above), and if it is still at n=0 several checks after that deploy, treat it as a second
  bug rather than as selectivity.
