# Live QUEUE POSITION sampling + the ORDERLY DRAIN

Built 2026-08-14 from `docs/EXTERNAL_REPO_RESEARCH.md` §1 and §6. Read-only measurement plus one
safety behaviour. **No entry rule changes and no book's economics change.**

## Part 1 — Queue position: replacing an inference with an observation

### The problem it solves

`docs/MMSELL_FILL_MODEL.md` states the whole paper→live gap is maker adverse selection, worth
~2¢/contract, and that we cannot replay it because "the paper books throw away the exact data a
fill model needs." `docs/MMSELL_OFFSET_AB.md` is currently spending **real money** to recover one
piece of that: `mmsell10a` rests at the no-bid, `mmsell10b` rests 1¢ better, and the two are
compared on realized ¢/contract.

**That comparison cannot finish.** Measured 2026-08-14, from its own gate:

```
mmsell10b vs mmsell10a: -2.77c/contract  SE 2.20c  95% CI [-7.09, +1.54]
   smallest difference detectable at this n: ~6.2c
   Detecting +0.5c needs ~47,106 contracts/arm
```

We have ~270 contracts/arm. It is not a slow experiment — at our size it is an unanswerable one,
and it is being paid for in live capital. It also carries a confound the same read flags: **149 of
`mmsell10b`'s orders were rejected against 3 of `mmsell10a`'s** (post-only cross on 1¢-wide books),
so the two arms are not even trading the same markets.

### What we do instead

Kalshi assigns queue position by price-time priority and exposes it directly:

- `GET /portfolio/orders/queue_positions` — batch
- `GET /portfolio/orders/{order_id}/queue_position` — single

Once per reconcile we sample every **resting** order and store the rank in
`live_order_queue_ticks`, with the covariates any P(fill) fit needs (`limit_price`,
`rest_seconds`, `contracts_ahead`). That turns the offset question from *"did the cent produce
more P&L"* — 47,106 contracts — into *"did the cent move us up the queue, and past how many
contracts"*, which is answerable on the ~35 orders resting at any moment and accrues whether or
not an A/B is armed.

Read it with **`{"type":"script","name":"mmsell_queue_position"}`**.

### What Kalshi actually sends — confirmed live 2026-08-14

```json
{"queue_position_fp": "0.00"}       ← front of the queue
{"queue_position_fp": "2028.55"}    ← 2028.55 contracts resting ahead of us
```

**One fixed-point figure, and it is a CONTRACT QUANTITY, not an ordinal rank.** A rank cannot be
`2028.55`. It is the same fixed-point convention as `count_fp` / `position_fp` / `volume_fp` and
the `_dollars` prices — Kalshi's house style, applied here too.

That is the *more* useful of the two possible measures, and it is what the read leads on: rank 3
behind three 1-lots is a completely different queue from rank 3 behind three 500-lots. It
populates `contracts_ahead`; `queue_position` carries the same figure rounded, kept populated only
so the coverage check has something to key on.

> **This shape was missed on the first two deploys.** The key list had `queue_position` and three
> synonyms — but not the `_fp` spelling — so the parser read *nothing* in production while this
> very document warned about the fixed-point migration. **The design still worked**: the failure
> was loud, counted into `queue_unparsed`, and the raw payload was persisted, so the true shape was
> recovered from `live_order_queue_ticks.raw_json` rather than guessed. That is precisely what the
> next section is for, and it earned its keep on day one.

### The trap this is built around

A queue sampler that silently writes nulls is worse than one that crashes: it runs every cycle,
rows accumulate, and every rank is empty — indistinguishable from a book with nothing resting
until someone tries to analyse it weeks later. Kalshi has already migrated a payload under us
once (the orderbook grew `orderbook_fp` with `_dollars` STRING prices and dropped the integer
keys, making a naive `market.get("yes_bid")` silently `None`).

So the parsing is isolated in `kalshi_bot/live/queue_position.py` and:

* it is **shape-tolerant** (flat, one envelope layer, string numerics — Kalshi's house style);
* rank **0 survives** — front of the queue is the most interesting value there is, and any
  truthiness check would erase it;
* an unreadable payload returns `None`, never `0` — "we could not read this" must never reach the
  database as a confident claim of front-of-queue;
* `parse_batch` returns **the failed rows**, not a count, so each failure is stored against the
  order it belonged to *and* surfaces in the cycle summary (`queue_unparsed`);
* the read's **COVERAGE section gates everything below it** — a high `null_rank%` means every
  table after it describes a shrinking, non-random subset.

### What this does NOT tell you

**Whether the cent was worth paying.** A better queue position that fills more often is not
automatically good — the entire adverse-selection finding is that the fills a maker wins are the
losers. This measures the *mechanism* (did the cent buy priority). `mmsell_live` and
`mmsell_fill_model` measure whether the resulting fills made money. **A promotion needs both.**

### Cost and control

One extra GET per reconcile (batch), plus a **bounded** per-order fallback for anything the batch
omits — bounded so that a Kalshi change emptying the batch response cannot silently multiply our
request rate forever. Off with `LIVE_QUEUE_POSITION_SAMPLING=false`, no deploy needed. 429s have
been zero since the `advanced` grant, so one call per cycle is not a rate-limit concern.

## Part 2 — The orderly drain: turning a book off now turns its exposure off

### The hole

There was **no path that cancels already-resting orders when a book stands down.** A strategy
dropped from `LIVE_STRATEGIES` stopped placing immediately, but its orders kept working on the
exchange, reachable only by the 4-hour per-order timeout.

**And the kill switch made it worse.** `cancel_events_order` sat behind `_ensure_live_enabled`,
which fails when `kill_switch` is true. So flipping the switch froze every resting order *on the
book*, working, with no way to pull them. **The switch made the position less controllable — the
precise inversion of its purpose.**

This is not hypothetical; it is the same coupling, on a different path, that produced 1,913 dead
`pending` rows when a closeout loop retried forever against a kill switch (`_closeout_can_place`
exists because of it).

### The fix

`client._ensure_can_cancel` guards cancels instead: it requires `BOT_MODE=live` and
**deliberately does not check the kill switch**. Cancelling can only reduce exposure; there is no
state of the world where we want the switch on *and* our orders left resting.

`LiveExecutor.drain_stood_down_books` then runs each reconcile and cancels resting orders that are:

1. **de-allowlisted** — strategy no longer in `LIVE_STRATEGIES` (`cancel_reason=book_stood_down`);
2. **everything**, when the kill switch is on (`cancel_reason=kill_switch`).

It follows `rodlaf`'s discipline from the research: **verify before dropping state.** A row is
marked `canceled` only when Kalshi accepted the cancel. A failure leaves it `resting` and counts
into `drain_failed`, so the next cycle retries — claiming a cleanup that did not happen is how our
state drifts away from the exchange's.

Ordering matters and is asserted: **sample queue positions before draining.** The drain destroys
the last observation of the orders it cancels, and those are the interesting ones — a stood-down
book's rank is the record of what it never got filled at.

Off with `LIVE_DRAIN_STOOD_DOWN=false`.

## OUTCOME 2026-08-14 — it delivered the mechanism leg, then its data source was retired

The sampler worked (93% of post-fix samples readable) and **directly observed the thing the offset
A/B was built to infer**: `mmsell10b` (resting 1¢ better) sat at the front of the queue **77.0%** of
the time against `mmsell10a`'s **35.9%**. The cent buys real priority.

Combined with the twin-paired P&L, that closed the offset question —
`docs/MMSELL_OFFSET_AB.md` **VERDICT: KILL**. The mechanism leg did not decide it (the P&L pairing
did, at n≈400 per arm) but it is what makes the verdict *explainable*: the cent gets you to the
front, and the front is where the losers cross into you.

**Its data source is now nearly gone.** Live mmsell trading ended 2026-08-14, so the only resting
maker orders left come from `theta4` — which placed 13 orders in 48h against mmsell's ~510, and
typically holds **zero** resting at any moment. Expect this read to return almost nothing until a
new live maker book exists.

**Do not read that as a fault.** The COVERAGE section will correctly say "no queue samples yet",
and the sampler will resume the moment a maker book is armed again — it needs no re-enabling. The
one thing to check when that happens is that coverage is still ~93%+; a drop means Kalshi moved the
payload again, which has now happened twice.

## Pre-registered read — what would make us act

This is measurement, so there is no P&L gate. The decision it feeds is the offset one:

- **If `p50 ahead` is materially lower for `mmsell10b` than `mmsell10a`** — the cent
  buys real priority. The offset stays a live question, and the next read is whether those extra
  fills are profitable (`mmsell_fill_model`), which is a different question this cannot answer.
- **If the two arms' contracts-ahead distributions are indistinguishable** — the cent buys
  nothing at our sizes. **Stop paying it**, retire `mmsell10b`, and close the offset A/B as
  *measured and failed* rather than waiting for an n that arithmetic says will never arrive.
- **If `null_rank%` is high** — decide nothing. Fix the parser first.

At ~35 resting orders sampled per cycle this reaches a usable n in days, against the offset A/B's
47,106 contracts/arm.

## Not built (deliberately)

**`amend`/`decrease` instead of cancel-and-repost** (research §2) is the natural follow-on — every
timeout-cancel and re-entry at an unchanged price donates queue rank. It is **sequenced after**
this on purpose: if queue rank turns out not to drive fills at our size, then preserving rank on a
repost buys nothing either, and the work would be wasted. This data decides whether that item is
worth building.
