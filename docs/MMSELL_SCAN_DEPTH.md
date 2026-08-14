# mmsell SCAN DEPTH — `mmsell10d`, `mmsell10e`

Built 2026-08-13. Paper only. Control: **`mmsell10`**.

> ## VERDICT 2026-08-14 — RETIRED. Deeper scanning is worse, and the decay is monotonic.
>
> **Measured and failed** — not shelved, not unmeasurable. Each book against `mmsell10` over its
> own window, fee-normalized:
>
> | depth | book n | book ¢/trade | control n | control ¢/trade | vs control |
> |---|---|---|---|---|---|
> | 150 (`mmsell10`, control) | — | — | — | — | — |
> | **225** (`mmsell10d`) | 243 | +0.70¢ | 177 | +1.34¢ | **−0.64¢** |
> | **300** (`mmsell10e`) | 133 | **−1.05¢** | 54 | +0.89¢ | **−1.94¢** |
>
> `mmsell10d` reached its pre-registered n≥200 and **failed the KEEP clause** (−0.64¢ against a
> −0.5¢ tolerance). It did not trip the KILL clause (>1.0¢ below control) — the gate has a gap
> there — but the second test settles it.
>
> **It also lost on TOTAL dollars, which was the escape hatch this gate was deliberately built
> with.** The tolerance was asymmetric because a deep book takes strictly more trades than its
> control, so equal per-trade across more trades would still be more money. It did not get equal
> per-trade. Same window: **243 trades × +0.70¢ = +$1.70**, against the control's **177 × +1.34¢
> = +$2.37**. Sixty-six more trades, less money. Backing out the increment, ranks 150–225 run
> about **−1.0¢/trade**; ranks 150–300 worse still.
>
> `mmsell10e` never reached n=200 (n=133) and is retired without one. That is a judgement, and
> the reason it is defensible here: it is **negative in absolute terms** and −1.94¢ below its
> control, so it fails a clause that does not depend on n at all, and no plausible remaining
> sample closes a gap that size.
>
> ### The mechanism — read this before re-trying it on a hunch
>
> The volume rank cut selects for **liquid** events, and liquidity is precisely what a resting
> maker order needs: to fill at all, and to avoid being picked off by the one counterparty who
> crosses into it. This family's largest known drag is maker adverse selection
> (`docs/MMSELL_FILL_MODEL.md`), and thinning the book makes it worse. **The cap was never a
> limitation we were suffering — it was doing work.**
>
> The design section below anticipated exactly this ("thinner books mean worse fills") as one of
> two possible outcomes. It is the one that happened.
>
> ### What was NOT the reason
>
> **Rate limits were not the constraint, and the premise below that they were is now wrong.**
> 429s have been **zero** since the `advanced` grant — measured straight through the 225 → 300
> step, which roughly doubled orderbook fetches per scan (~830 → ~1,800 markets considered) with
> the transient counter staying empty. Retiring both books returns the shared scan depth to the
> global 150 and halves fetches, but that is a side benefit, not the motivation.
>
> ### Consequences
>
> * The **inline quote pre-filter** (`docs/MMSELL_QUOTE_PARITY.md`) is closed by this result. Its
>   only surviving justification was affording deeper scanning; deeper scanning loses money, so
>   paying a biased ~1.2% miss rate to reach it would be paying to lose faster.
> * The ~1,600 eligible events/scan still discarded by the cap are **not** a missed opportunity
>   for this entry. Stop citing that number as upside.
>
> ### Revival condition — evidence, not patience
>
> Revive only on a **mechanically different entry**: a taker rule, or anything that does not
> depend on a counterparty crossing into a resting order. More paper on this entry cannot
> resolve it — the finding is about liquidity and maker fills, and a larger sample would produce
> a more confident version of the same answer. The `scanmax` mechanism is kept in the code (and
> still tested) so a revival does not have to rebuild the isolation property from scratch.

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
