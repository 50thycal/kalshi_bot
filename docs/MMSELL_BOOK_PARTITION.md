# Running two overlapping mmsell experiments at once

**Status:** mechanism shipped (#413), unused. No book declares `part=` in production.
**Scope:** book selection only. Changes no price, no size, no risk limit, no live safeguard.

> ## BEFORE ARMING A SECOND LIVE CANARY — READ THIS
>
> **This mechanism is necessary but NOT sufficient.** It makes two books disjoint per *ticker*.
> It does **not** separate their risk budgets: `MAX_TOTAL_EXPOSURE` and `MAX_DAILY_LOSS` are
> account-wide and are checked *before* the dedup gate, so whichever book the scan reaches first
> can exhaust the budget and gate the other out of the entire slate — the same scan-order bias,
> one level up, where a ticker partition cannot reach.
>
> Arming a second live book without per-book budgets means the second canary's numbers are the
> first one's leftovers, which is exactly the failure this document exists to prevent. Build
> them first (parked in `docs/workstreams/ACTIVE.md`), as budgets checked **in addition to** the
> account-level ones, never instead of. That is a live-safeguard change and an operator decision.
>
> Arming itself remains a hard stop under `DEC-012` regardless: a pre-registered risk envelope,
> explicit operator confirmation, and its own registered Experiment OS deployment arm.

## The problem

Two live mmsell books whose universes overlap do not double-trade a market — but they do
compete, and they compete in the worst available way.

Both books live in one worker and one scan loop. `MmSellTracker._books()` returns the control
book, then the configured variants **in `MMSELL_VARIANTS` order**, then the twins; the per-market
loop walks them sequentially inside a single cycle. So there is no race — there is an order.

When the first book to be evaluated places, `mirror_mmsell_entry` writes the `live_orders` row
and **commits it before the Kalshi POST**. The next book on the same market then hits:

```python
if repo.live_buy_exists_for_ticker(session, ticker, strategy) \
        or repo.live_open_order_exists(session, ticker):
    return "gate:dedup"
```

`live_open_order_exists` filters on `market_ticker` and status alone — it is **strategy-agnostic
by design**, and that is the guard that stops two books stacking real exposure on one market. It
works. The problem is who loses:

- **The loser is systematic, not random.** It is always the book listed later in
  `MMSELL_VARIANTS`. It does not trade its strategy; it trades the *residue* of markets the
  winner's gates happened to refuse (`gate:open_cap`, `gate:spread`, `gate:exposure`,
  `gate:no_balance`). Its P&L is not interpretable as its strategy's P&L.
- **The lockout is the life of the position.** mmsell rests a GTC post-only maker held to
  settlement, so the claim holds until it fills and settles or is cancelled — hours, not a cycle.
- **Any overlap that does occur deletes the market from BOTH books.** In
  `experiment_os/metrics.py`, `contested` is every market another strategy also has a
  `live_orders` row on, with **no time window**, and `ours = mine - contested`. So if the
  winner's order times out and the loser enters the same ticker later, that market drops out of
  both arms' P&L permanently.

## What this mechanism does

`part=i/n` on a book's `MMSELL_VARIANTS` spec. The book takes only the tickers whose hash
partition equals `i`; nothing else about the book changes.

```
MMSELL_VARIANTS="mmsellP0:lo=5,hi=10,maxyes=7,part=0/2;mmsellP1:lo=5,hi=10,maxyes=7,part=1/2"
```

Assignment is `sha256(MMSELL_LIVE_PARTITION_SALT + ":" + ticker) % n`
(`live/sizing.py::ticker_partition`). Deterministic rather than drawn, so the entry-retry path
cannot flip a ticker's owner mid-market; recomputable from the ticker alone, so attribution needs
no new column; reproducible on a fixed salt. The ticker is hashed rather than the event, so the
books stay balanced inside a single event's ladder.

Twins inherit their parent's `part` (`_twin_books` copies the parent spec), so a twin measures
the same universe its live parent could trade. `part` and the salt are recorded in `_twin_params`,
so changing either mid-flight is reported as param drift rather than silently blending two
universes.

## Why it is not `abarm`

`abarm` already partitions — but `arm_book_offset` returns **an offset in cents** as its verdict.
Enrolling two books in it therefore also forces them to rest at different prices. For two books
asking the same question (the queue-position A/B, `docs/MMSELL_OFFSET_AB.md`) that is the point.
For two books asking *different* questions it is a confound nobody asked for.

`ticker_partition` is that primitive with the price removed. `offset_arm` now delegates to it, so
the offset A/B's historical assignment is unchanged — pinned by
`test_the_offset_ab_assignment_is_unchanged_by_the_refactor`.

A book may declare `part` **or** `abarm`, never both: two independent hash splits would leave it
trading `1/(n × arms)` of the flow while its spec reads as `1/n`. The parser refuses the spec.

## The cost, stated plainly

Each book sees about half the candidate flow. Measured on `Fmmsell10`, the 95% CI on the
per-contract edge was [−2.60¢, +3.95¢] — it contains zero, and excluding zero needs roughly 5,480
settled markets. **Halving n roughly doubles time-to-signal for each book.** Two concurrent
experiments is a throughput decision, not a free one: if the two books are asking genuinely
different questions it is the right trade; if they are variants of one question, a single book
with full n answers sooner.

## What this does NOT fix

1. **The shared risk budget.** `_total_exposure_hit` (`MAX_TOTAL_EXPOSURE`) and
   `_daily_loss_hit` (`MAX_DAILY_LOSS`) are account-wide and are checked *before* the dedup
   gate, so one book exhausting the budget gates the other out of the entire slate — the same
   scan-order bias, one level up, and per-ticker partitioning cannot reach it. Fixing it means
   per-book budgets checked *in addition to* the account-level ones, never instead of. That is a
   live-safeguard change and an operator decision.
2. **Exchange position netting.** Two books' fills on one market are one exchange position and
   cannot be split per tag. Partitioning makes this moot by never letting them hold the same
   ticker; it bites only if you deliberately want overlapping holdings, which needs a separate
   Kalshi account.
3. **Contested-market attribution.** The `contested` guard in `experiment_os/metrics.py` is
   correct (a netted position genuinely cannot be attributed) and is left alone. Under
   partitioning `contested_markets` should be **structurally zero**; a non-zero count means the
   partition leaked and is worth an Experiment OS issue rather than a quietly shrinking sample.

## Operating notes

- `MMSELL_LIVE_PARTITION_SALT` is allowlisted in `scripts/railway_env.py`. **Changing it
  re-randomizes every assignment**, so evidence either side of the change is not poolable. Bump
  it only to start a genuinely new split, and record the change here.
- Arming a second live book is a hard stop under `DEC-012` — it expands real-money exposure and
  needs a pre-registered risk envelope and explicit operator confirmation. This mechanism makes
  that safe to *ask for*; it does not authorize it.
- Under `NEW_ONLY` enforcement a partitioned book still needs its own registered Experiment OS
  deployment arm, exactly like any other tag. A `part=` spec does not create one.
