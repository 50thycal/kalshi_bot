# mmsell SCAN DEPTH — `mmsell10d`

Built 2026-08-13. Paper only. Control: **`mmsell10`**.

## The question

The mmsell entry scan pages the entire Kalshi event universe (~9,600 events, cursor exhausted),
ranks the eligible ones by volume, and then **fetches orderbooks for only the top 150**. Roughly
**1,740 eligible events per cycle** are discarded by that cut without ever being priced.

The cut is not a trading decision — it is a budget. Each event costs one orderbook call per
market, and until 2026-08-12 we were metered at 20 requests/sec on Kalshi's `basic` tier while
the scan bursts at 6–25/sec, booking hundreds of 429s a day. The `advanced` grant took that to
**30 req/sec**, which is what makes looking deeper affordable at all.

So: **is the maker edge in the thinner tail of the board as good as it is at the top?**

This is a real question rather than a free lunch. Events ranked 151–225 are lower-volume *by
construction*. The favourite-longshot edge this book harvests could plausibly be **larger**
there (less efficient markets, wider spreads) or **smaller** (thinner books mean worse fills,
and the maker adverse-selection gap is already this family's biggest known drag). Nothing in our
history answers it — every mmsell number ever collected comes from the top 150.

## Design

`mmsell10d` is `mmsell10` with **one** difference: `scanmax=225`. Band, price ceiling, htc
window, sizing and hold-to-settlement are identical. A test asserts the configs differ by
exactly that key, so a divergence between the two books is attributable to the extra events
alone.

### Why a separate book instead of raising the global cap

Raising `mmsell_top_events` from 150 to 225 would have been a one-line config change, and it
would have been the wrong move. The cap decides which candidates **every** book is offered, so
raising it globally would have:

* changed the candidate stream of every paper book *and both live arms* simultaneously;
* made every number collected before the change incomparable with every number after it —
  including the live `mmsell10a`/`mmsell10b` queue-position A/B currently mid-flight;
* left no control, so "did the extra events help?" would have been unanswerable forever.

Instead the scan reaches as deep as the **deepest book asks for**, and each book is gated on the
event's rank. A book without `scanmax` sees exactly the top-150 it always saw — byte-identical
candidate stream — and only `mmsell10d` is offered ranks 150–224.

### Telemetry stays scoped to the control

`events_seen` and `markets_considered` remain scoped to the control's depth, with the extra
reach carried in `events_scanned_deep` / `markets_considered_deep`. Without that split, adding
this experiment would show up in `mmsell scan health` as the scan suddenly seeing 50% more of
the market — indistinguishable from the 2026-08-08 starvation fix working a second time.

## Pre-registered gate

Read `mmsell10d` against **`mmsell10` over the same window** — never in absolute terms, and
never against the control's lifetime number.

- **KEEP** at **n ≥ 200 settled** in the deep slice only if **both**:
  1. `mmsell10d` ¢/trade is **> 0** in absolute terms; and
  2. it is within **−0.5¢** of `mmsell10` over the same window.

  Note the asymmetry: this book takes *strictly more* trades than its control (it sees every
  event the control sees, plus 75 more). So it does not need to *beat* the control — it needs
  to not be diluted by the tail. Equal per-trade P&L across more trades is more total P&L,
  which is the whole point.
- **KILL** if ¢/trade is ≤ 0 absolute, or more than 1.0¢ below `mmsell10` at n ≥ 200. Either
  says the thin tail is worse than the top of the board and the cap was doing useful work.
- **PROMOTE** (raise the global cap) only on KEEP **and** a `RATE LIMITED` count that has not
  materially risen — a wider scan that buys trades by eating 429 backoffs is stealing from the
  rest of the cycle, including live order management.

Gate on the **realizable** ¢/trade as well, per `docs/MMSELL_FILL_MODEL.md` — with the caveat
recorded in `docs/MMSELL_TYPE_BOOKS.md` that the fill model projects entry price only and
therefore cannot discriminate between two books in the same band. It will read ~the same for
both; that is a known blind spot here, not a pass.

## Watch while it runs

* `RATE LIMITED` in `mmsell quote parity` — the constraint that bounds this whole experiment.
* `events_scanned_deep` vs `events_seen` in the scan telemetry — confirms the extra reach is
  actually happening rather than the cap binding somewhere else.
* `markets_considered_deep` — if this is ~0, the deep slice carries no tradeable markets and the
  book will sit at the control's n forever, which is a supply answer rather than an edge answer.

## Widening further

`MMSELL_TOP_EVENTS` and `MMSELL_EVENT_PAGES` are settable from the ops channel, so the *global*
cap can be retuned without a deploy once this experiment answers. Raise in steps and watch the
429 line each time: the scan's burst rate, not its average, is what collides with the bucket.
