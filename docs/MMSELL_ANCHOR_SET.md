# The mmsell ANCHOR SET — forward-testing tail mitigation as paper books

**Five paper-only books (`mmsellA1`–`mmsellA5`) that each add exactly one tail-mitigation
mechanic on top of the `mmsell10` entry.** No live capital is involved. The point is to accrue
forward-tested n on the three mechanics that the backtests said were promising but underpowered,
so that a future "anchor" decision (a larger position size on one book) rests on measured
out-of-sample evidence rather than on an 11–23 trade backtest slice.

> ## VERDICT 2026-09-06 — `mmsellA4` and `mmsellA5` are RETIRED. The anchor set is closed.
>
> Both books met their own pre-registered kill clauses and were retired through Experiment OS
> (`RETIRE_ON_GATE_FAIL`, authorized by the recorded evaluator verdict, not by this document).
> They failed for **opposite reasons**, and the difference is the whole lesson.
>
> ### A4 — the volatility entry gate: no effect, and none reachable
>
> | | mmsellA4 | mmsell10 control |
> |---|---|---|
> | n settled | 1,944 | 2,703 |
> | ¢/trade | 0.459 | 0.670 |
> | delta | **−0.211¢** (kill clause: `≤ 0`) | |
> | candidate rejection rate | 27.1% | — |
>
> Seven consecutive FAILs at 19× the n≥100 floor. The gate was **not inert** — it rejected 27% of
> candidates, well clear of the 5% inertness floor — so the mechanism fired as designed and simply
> did not help. With per-trade sd ≈ 23.7¢ the SE of the delta is 0.665¢, so −0.21¢ is
> **statistically indistinguishable from zero** (95% CI −1.56 to +1.05). Read that as *no
> detectable effect*, not *harmful*. It still kills the book: the promote bar was +1.0¢, which sits
> 1.89 SE above a point estimate already on the wrong side of zero at 19× the required sample.
> Accruing more n narrows the interval around a negative number.
>
> **Do not re-sweep `volw`/`volv`.** That is the same trap A1–A3 fell into: the motivating backtest
> was n=13–17 measured on a different population. The level was never the problem there either.
>
> ### A5 — the short strangle: right sign, wrong gate
>
> | | value |
> |---|---|
> | clean pairs | 422 (5× the n≥82 floor) |
> | pair win rate, point estimate | **94.55%** (399/422) |
> | 95% lower bound | 92.37% |
> | registered bar | 93.9% — point estimate is **above** it |
> | corrected break-even (post fee re-baseline) | 93.1% — also **above** it |
>
> A5 was retired on a literal reading of its pre-registration, and that reading is correct: the
> kill clause is `pair_win_rate_95lb_pct ≤ 93.9`, it fired eight consecutive times, and the bound
> fails against the corrected 93.1% break-even too — so there is no "use the right bar" shortcut,
> and none was taken. Measurement was clean: `multi_leg_events: 0`,
> `trades_without_event_mapping: 0`, and the pairing boundary was enforced structurally by the
> gate's `evidence_started_at`, not by hand.
>
> **But the book was never shown to be bad — only unproven.** Its central estimate is above
> break-even on both bars, and the book as traded returned 1.44¢/trade against a ~0.67¢ control.
> It failed a *confidence* test, and the two findings are not the same.
>
> ### What to do differently if A5 is revived
>
> The revival condition is **a properly powered pre-registration**, not a re-read of this one and
> not a parameter sweep. Three concrete faults to fix:
>
> 1. **The sample floor was set for the wrong constraint.** The kill wording assumed reaching 82
>    pairs would be the hard part ("or paired entry rate is so low the book can't reach 82 pairs in
>    a quarter"). Actual supply ran ~24 clean pairs/day — the floor was ~3.5 days of accrual. A 95%
>    lower-bound test at n=82, against a bar sitting ~0.65pp under the true rate, fails almost
>    regardless of merit. It was underpowered by construction. Derive the floor from power analysis:
>    at the observed 94.55%, clearing 93.1% needs **~663 pairs** (~10 days) and clearing 93.9% needs
>    **~3,300 pairs** (~4 months).
> 2. **The bar was stale on the day it was frozen.** The 2026-08-11 maker-fee correction moved
>    break-even from 93.9% to **93.1%** (see the fee note above), but the gate table carried 93.9%
>    forward and the gate was frozen 2026-08-16 — five days *after* the correction. Any successor
>    must take its break-even from the current fee model, computed at freeze time rather than copied.
> 3. **The headline overstates the mechanism.** Of 1,012 events seen, 568 were one-sided: the book
>    as traded was only ~42% actual strangles. Quote pair economics off the pairs, never off the
>    per-leg ¢/trade.
>
> A successor is a **new Version** (the question changes), registered through a reviewed package —
> never an edit to this contract. Nothing here re-interprets the recorded FAIL, which stands.

> ## VERDICT 2026-08-12 — `mmsellA1`/`A2`/`A3` (the bid-triggered stops) are RETIRED. A4 and A5 run on.
>
> **The pre-registered gate failed on the half that mattered.** The gate asked for a better
> 5th-percentile tail AND a mean no worse than 0.3¢ below control. Read correctly — as
> `status IN ('settled','closed_sl')`, which includes the positions the stop actually closed —
> all three levels fail both halves:
>
> * A1 **−4.16¢/trade** against the `mmsell10` control's **+3.14¢**
> * p5 **−19.0** against the control's **+5.0** — the stop makes the tail WORSE
>
> The mechanism is visible in one number: the stop fires on **52%** of positions. At that rate it
> is not truncating a rare disaster, it is converting ordinary winners into realized losses. A
> cheap tail that ticks up is usually still going to expire worthless; selling it on the way is
> paying the spread to exit a position that was about to pay.
>
> This is a RELATIVE gate, so the 2026-08-11 maker-fee correction does not touch it — control and
> book move together.
>
> **Why the backtest was wrong, specifically.** `docs/MMSELL_CRYPTO_STUDY.md` measured
> bid-triggered stops improving both mean and tail. That was on `htc<1h` crypto, because Kalshi
> only serves ~1h of candles for those series. mmsell trades `htc≥1h` sports. Crypto prices move
> continuously, so a stop exits near its trigger; a sports contract JUMPS on a score, straight
> through the trigger. The backtest population and the trading population were different, and the
> study said so — this is the forward test confirming it mattered.
>
> **Revival condition:** a stop whose trigger cannot fire on a non-informative quote, plus fresh
> pre-registration. Not a re-sweep of levels on this data — the level was never the problem.


## Why this exists

mmsell sells cheap tails. The P&L shape is many small wins (+3–6¢) and a rare near-full-stake
loss (−93¢). At a 7¢ entry the break-even win rate is **93.1%** — so the entire question is
whether the loss tail can be cut without eating the thin premium that pays for it.

> **[2026-08-11] The break-even moved, because the fee did.** It is
> `p = (100 − entry + fee)/100`, so the fee sits inside it. This doc previously said **93.9%**,
> computed with the paper engine's ~1¢ taker fee — but these entries REST, and Kalshi bills a
> maker 0.003¢/contract, not 1¢ (n=342 live fills). With the corrected maker fee the bar is
> **93.1%**. Everything downstream that was sized against 93.9% (notably A5's n≥82) is therefore
> **conservative, not wrong** — a bar that got easier cannot invalidate a sample sized for a
> harder one. See the FEE RE-BASELINE section of `docs/BOOK_REGISTRY.md`.

Three candidate answers came out of `docs/MMSELL_CRYPTO_STUDY.md` (a Kalshi-history backtest) and
`docs/MMSELL_EXIT_STUDY.md` (a replay of our own captured intraday ticks):

| mechanic | backtest verdict | why it still needs forward n |
|---|---|---|
| bid-triggered stop-loss | **works** — every bid-triggered level improved **both** mean and 5th-pctile tail vs hold (best: bid L15 K1, −0.56¢ vs −3.67¢ hold, p5 −21.5¢ vs −95.5¢) | measured on `htc<1h` crypto, because Kalshi only serves ~1h of candles for these series. mmsell trades `htc≥1h`. **Different population.** |
| volatility **entry** gate | **right sign, underpowered** — calm tape +2.85 to +5.25¢ at 100% win; active tape −39¢. n=13–17. | n is far too small to separate the effect from noise |
| short strangle | **most intriguing, most fragile** — +3.30¢/pair at 100% win, but n=23; the 95% lower confidence bound is 87.8% vs a 93.1% break-even | needs ~**82 clean pairs** to clear its bound. Free to accrue in paper. |

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

**Fixed 2026-08-14 — `_event_has_both_tails()` certifies the EVENT, not the PAIR, and NFL supply
was the first regime to make that gap visible.** The check only asks "does this event carry a
cheap-YES market and a cheap-NO market somewhere," then the entry loop lets EVERY market that
individually clears either band open its own leg. On a two-market event that's harmless — the
one market on each side IS the pair. On a multi-strike ladder (`KXNFLSPREAD`, `KXNFLTOTAL`, and
any other series with several strikes per event) several markets can independently sit in the
same band: one event opened four cheap-NO legs (`ARI2`/`ARI3`/`ARI4`/`ARI5`, four different
spread lines on the same game) and zero cheap-YES legs. Those four legs are **positively
correlated** — a single bad result on that game can move several strikes together — which is
exactly the risk the strangle exists to avoid, not a strangle at all. A pairing audit across the
book's full history found only **27% of events** (192/715) had actually taken a leg on both
sides; 64% took the upper leg only, 9% the mirror leg only.

The fix caps entry to **one leg per side per event**: `MmSellTracker._strangle_leg_taken` (via
`repo.event_has_strangle_leg`) checks whether this book already holds ANY trade — open, stopped,
or settled, since the dedup is against the event's own outcome, not current risk — of the
candidate's side on this event, and skips the entry if so. The first market to clear each side's
band still wins that leg; every later same-side candidate in the same event is refused. Fails
open (like every anchor gate) if the read errors or the event has no `event_ticker`.

**What this means for A5's numbers so far.** Every trade already recorded is genuine — the
per-trade economics don't change, since a leg's own P&L doesn't depend on whether its partner
exists. What changes is that a large share of the pre-fix volume was NOT the hedged pair the
promotion gate's confidence-interval math assumes; the pair win rate computed over that data
mixes true pairs with the ordinary uncorrelated risk of a wide single-leg mmsell trade. **Read
the pre-boundary sample as directional, not as clean evidence toward the n≥82 gate.**

**This is a PAIRING boundary, the same species as the 2026-08-13 UNIVERSE boundary
(`docs/BOOK_REGISTRY.md`) — a hard floor, not a correctable offset.** There is no conversion that
turns a one-sided-legs sample into a paired one; the only remedy is to drop pre-boundary trades
from the pair-rate gate specifically (NOT from the book's own P&L history, which stays real and
whole). **A boundary recorded only in prose gets blended away the next time someone reads the
table** — that exact failure is why the universe boundary lives in code (`COHORT_START` in
`scripts/mmsell_fill_model.py`), not just here. A5 has no dedicated analysis script yet, so until
one exists the floor has to be applied by hand: **any query computing A5's pair count or pair
win-rate bound must add `created_at >= '2026-08-14T14:31:12Z'`** (PR #213's merge time — the
`mm check 1` skill's step 3b carries this filter; see there for the up-to-date boundary if this
one is ever superseded). This timestamp is provisional pending empirical confirmation on the next
check (too little post-merge volume existed at merge time to verify the deploy took effect the
way the 2026-08-11 fee boundary's timestamp was empirically pinned) — if a later check finds
same-side multi-leg events still forming after this timestamp, move the boundary to match and
update both this doc and the skill.

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
| vol gate + strangle pairing / mirror leg / one-leg-per-side cap | `kalshi_bot/mmsell/tracker.py` (`_vol_gate_blocks`, `_event_has_both_tails`, `_strangle_leg_taken`) |
| tick history reads | `kalshi_bot/repository.py` (`recent_candidate_mids`, `recent_position_yes_bids`, `event_has_strangle_leg`) |
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
- **A5's clock restarted again at the 2026-08-14 one-leg-per-side fix.** Before it, ladder events
  (NFL spread/total) could open several correlated same-side legs per event with no opposing leg
  at all — genuine trades, but not the hedged pair the win-rate gate assumes. When reading the
  pair win rate toward n≥82, check whether the sample spans the fix: pre-fix trades are real P&L
  but should not be treated as clean "pair" evidence.
