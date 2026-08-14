# mmsell queue-position A/B — what is 1¢ of queue priority worth?

*Built 2026-08-03, **INERT by default** (`mmsell_live_offset_ab_arms=""`). Pre-registered before
any data exists, per `docs/MMSELL_ROADMAP.md` §5.*

> ## VERDICT 2026-08-14 — KILL. The 1¢ offset buys queue priority, and the priority loses money.
>
> **The experiment concluded on its primary read and answered its question.** Live trading for both
> arms was ended 2026-08-14 (`LIVE_STRATEGIES=theta4`, `MMSELL_LIVE_OFFSET_AB_ARMS=""`); open
> positions were left to settle naturally.
>
> ### The gate, applied
>
> `gap = (twin ¢/contract) − (live ¢/contract)`; **KILL when `gap_b ≥ gap_a`.**
>
> | arm | twin ¢/ct | live ¢/ct | **gap** | settled n |
> |---|---|---|---|---|
> | `mmsell10a` (+0¢, rests at the no-bid) | +0.78 | **+0.84** | **−0.06¢** | 420 |
> | `mmsell10b` (+1¢, rests better) | +0.16 | **−3.29** | **+3.45¢** | 384 |
>
> `gap_b ≥ gap_a` by **3.51¢**. KILL, and not marginally. `mmsell10a`'s live execution *beat* its
> own twin by a hair; `mmsell10b` gave up 3.45¢/contract to it.
>
> ### Why it failed, mechanically — the doc called this shot in advance
>
> The offset did exactly what it was bought to do. It is the *consequence* that kills it:
>
> | | `mmsell10a` | `mmsell10b` |
> |---|---|---|
> | fill rate | 55.3% | **58.2%** |
> | at the front of the queue | 35.9% | **77.0%** |
> | live win% vs its own twin | 93.3% vs 94.7% (−1.4pp) | 89.3% vs 94.1% (**−4.8pp**) |
>
> Better queue position, more fills, and a win rate that collapses against its own twin. That is
> this document's own pre-registered failure signature: *"A higher fill rate with **worse** realized
> P&L is the signature of buying adverse selection, and is a kill rather than a puzzle."* **The cent
> gets you to the front of the queue, and the front of the queue is where the losers cross into
> you.**
>
> The queue-position figures above come from `live_order_queue_ticks`
> (`docs/LIVE_QUEUE_POSITION.md`), built 2026-08-14 — the mechanism leg was *observed*, not
> inferred. It did not decide the verdict (the twin-paired P&L did) but it is what makes the
> verdict explainable rather than merely measured.
>
> ### Two honesty notes on the record
>
> 1. **`mmsell10b` was killed at n=384 against its own n≥400 bar** — 96% of the way. The operator
>    ended live trading for the whole mmsell family before it crossed. Called out because this repo
>    reads gates literally: the verdict direction is unambiguous (a 3.51¢ margin where the design
>    was powered for a fraction of that), but the bar was not formally cleared, and nothing should
>    be promoted later on a claim that it was.
> 2. **The between-arm P&L race never became decidable and never could have** — final read
>    −2.77¢/contract, 95% CI [−7.09, +1.54], against a requirement of ~30,391 contracts/arm for a
>    0.5¢ effect. That is not a shortfall; the §"Why the direct A-vs-B P&L comparison cannot settle
>    this" section predicted it exactly, which is *why* the twin-paired read was pre-registered as
>    primary. **The design's most valuable act was refusing to use the obvious comparison.**
>
> ### Sanity checks — all three passed, so the experiment genuinely ran
>
> * average fill prices differed (92.6¢ vs 93.0¢ — under 1¢, exactly as predicted, because 1¢-wide
>   books force both arms to the bid);
> * fill rate differed (mechanism live);
> * queue depth differed (mechanism directly observed).
>
> ### What is NOT concluded
>
> This kills **improving** the price. It says nothing about the inverse — deliberately resting
> *worse* than the no-bid to shed adverse selection at the cost of fill rate. The data points at
> that as the natural follow-on, but it is a new hypothesis needing its own pre-registration, not a
> repair of this one.
>
> **Do not re-arm `MMSELL_LIVE_OFFSET_AB_ARMS` on a hunch.** With it non-empty, `maker_offset`
> silently splits **any** live mmsell book's orders across the arms by ticker hash — so a future
> live book would inherit the losing treatment without anyone choosing it. It was cleared to `""`
> as part of this retirement for exactly that reason.

## The question

`mmsell_live_price_offset_cents` has always been `0` — rest at the no-bid, join the queue — and has
never been varied. It is the **only untested live knob that acts on the mechanism that actually
decided the mmsell3 live test**: maker adverse selection.

The size of that mechanism, after the maker-fee correction (`docs/MMSELL_ROADMAP.md` §2):

| | ¢/contract |
|---|---|
| paper gross (fee model corrected to the measured ~0.013¢ maker fee) | ~+2.2 |
| live realized (mmsell3, n=359) | **+0.18** |
| **gap attributable to adverse selection** | **~2.0** |

Nothing else in the book is worth 2¢/contract. The trade the offset makes is explicit and
two-sided:

- bidding 1¢ above the no-bid **costs 1¢ of edge on every fill**, but
- it buys a higher fill rate **and earlier queue priority** — and queue priority is what decides
  whether you get the quiet fills or only the ones informed flow chooses to hand you.

The prior favouring "pay it": the live retry analysis found the tickers live **missed** earned the
same in paper as the ones it captured (6.15 vs 6.26 ¢/contract). The misses were **lost volume,
not dodged bullets** — so filling more of them should be worth something. That is an argument, not
a measurement. This is the measurement.

## Design — two live books, partitioned by a per-ticker hash

**Shape (operator's choice, 2026-08-03):** two separate live books, `mmsell10a` (arm 0, rests AT
the no-bid — the incumbent's behaviour) and `mmsell10b` (arm 1, rests 1¢ better). Each gets its own
strategy tag, its own auto-created paper twin (`mmsell10a_pt` / `mmsell10b_pt`) and its own P&L
line, so each arm's performance is directly visible next to the rest of the cohort. Both use
**1-contract clips** (`size=1`). The incumbent **`mmsell10` is untouched** — same knobs, same
2-contract clips, and it still evaluates first. *(Status 2026-08-04: that described the initial
arming; the operator has since stood `mmsell10`'s **live** arm down — see "The cost of leaving
`mmsell10` running" below. The `mmsell10` paper book still runs unchanged.)*

**The non-obvious part: the hash is what makes two books a valid experiment.**
`repository.live_open_order_exists(ticker)` is **strategy-agnostic** — any in-flight live order on
a market blocks every other book from it. So two books over the same entry spec would be split by
*book evaluation order*, not at random: whichever ran first would claim nearly everything. Assigning
each ticker to exactly one arm by a deterministic hash (`live/sizing.py` `arm_book_offset`) fixes
that:

- **No ticker is ever contested** — the two books are disjoint by construction, so neither can
  block or queue against the other, and the split is random rather than a race.
- Both arms see the same market flow over the same window: a genuine randomized experiment, not a
  before/after comparison contaminated by regime change.
- A ticker keeps **one arm for its whole life**, so the entry-retry path
  (`mmsell_live_max_attempts_per_ticker`, up to 6 attempts) cannot flip it mid-market and blend
  two prices into an uninterpretable average.
- Assignment is recomputable from the ticker, so attribution needs **no schema change**. The
  analysis imports the same hash the executor called, so the two can never disagree.

**Hot entries are excluded** (arm `None`). A hot entry is priced by the momentum guard
(`mmsell_live_hot_market_defensive_offset_cents`), not by the arm, so counting it into either arm
would measure the guard rather than queue position. The analysis drops them via the `hot_entry`
risk-event code.

Both the live executor (`live/executor.py mirror_mmsell_entry`) and the paper twin
(`mmsell/tracker.py`) resolve the offset through the same shared helpers, for the same reason they
already share `maker_no_price`: the moment the two derive the price differently, the parity report
starts measuring our own bookkeeping instead of the market.

The single-book form (one book splitting its own orders, via `maker_offset`) is still supported and
is what `mmsell_live_offset_ab_arms` drives when no arm book is armed. The two-book form is
preferred because it gives each arm its own reportable P&L.

### The cost of leaving `mmsell10` running

Because the dedup gate is strategy-agnostic and `mmsell10` evaluates first, it claims a candidate
before either arm book sees it. The arm books therefore trade **only the flow `mmsell10` did not
take** (largely: candidates arriving while it sits at its 50-position cap).

This is a deliberate, accepted trade:

- **Internal validity is preserved.** `mmsell10` blocks both arms *symmetrically* and the hash
  still randomizes whatever flow reaches them, so the A-vs-B comparison stays clean.
- **External validity and power are reduced.** The arm books see a smaller, non-representative
  slice, so n accrues more slowly than if `mmsell10`'s live arm were stood down. At 1 contract per
  fill the gate below (150 settled contracts per arm) is a matter of weeks, not days.

If the experiment is starved — check `mmsell_offset_ab`'s per-arm order counts — the lever is to
stand `mmsell10` down from `LIVE_STRATEGIES` and let the arm books take the full flow. That is an
operator decision, not something to change silently mid-experiment.

**Status 2026-08-04: that lever was pulled.** The operator stood `mmsell10` down from
`LIVE_STRATEGIES` at ~16:00 UTC on 2026-08-04, one day into the experiment, so the arm books now
take the full flow. The `mmsell10` paper book keeps running (and still evaluates first in the
paper scan), but paper holdings do not trip the strategy-agnostic live dedup gate — only live
orders do — so it no longer claims candidates ahead of the arms. The starvation caveat above only
applies to data collected before this point. Two knock-on effects to expect when reading state:

- **Live order volume dropped sharply at the switch, by design.** `mmsell10` was the live volume
  driver (~10 orders/day at 2-contract clips); the arms are 1-contract clips and each claims only
  its hash's half of the flow. A quiet live order feed after 2026-08-04 is the expected shape of
  this experiment, not a stall.
- **`mmsell10_pt`'s twin epoch auto-ended at the same moment** (the harness retires a twin when
  its live book leaves `LIVE_STRATEGIES`), so mmsell10 paper-vs-live parity reads stop there.

## Arming it

```jsonc
{"type": "env", "set": {
    "MMSELL_LIVE_OFFSET_AB_ARMS": "0,1",
    "LIVE_STRATEGIES": "theta4,mmsell10a,mmsell10b"
}}
```

This is the configuration as armed since 2026-08-04: `mmsell10` is deliberately absent (stood
down, see above). The original 2026-08-03 arming listed it
(`"mmsell10,theta4,mmsell10a,mmsell10b"`) and ran that way for its first day.

**Both are required.** With no arms configured an arm book claims *no* tickers at all — it fails
closed rather than falling back to a default offset, because an arm book has no defined price
unless the experiment is running. A single arm is likewise treated as off; a one-armed "A/B"
cannot answer anything.

Added live footprint: 2 books × 50 positions × 1 contract ≈ **$93 at ~93¢/contract**. While
`mmsell10` was still live (before 2026-08-04) its ~$93 (50 × 2) sat on top of that; since the
stand-down the arm books are the only mmsell live exposure. Lower the arm books' share by dropping
`MMSELL_LIVE_MAX_OPEN_POSITIONS` if that is more exposure than intended — it is a shared cap, so it
applies to `mmsell10` too.

**Do not change `mmsell_live_offset_ab_salt` mid-experiment.** It re-randomizes every ticker's arm,
which silently invalidates comparison with everything collected under the old salt. It is recorded
on the twin's epoch row so the harness reports the change as param drift rather than blending two
experiments. Bump it only to start a genuinely new run, and note the change here.

Standing policy applies: **no strategy goes live without a twin** (`docs/LIVE_PAPER_TWIN.md`).

## Reading it

```jsonc
{"type": "script", "name": "mmsell_offset_ab"}
```

Reports per arm: orders placed, fill rate (with a 95% Wilson interval), average fill price, and
realized ¢/contract on settled positions.

**Read the realized ¢/contract, not the fill rate.** The offset is *supposed* to raise the fill
rate — that is what you are buying, not evidence that you bought well. A higher fill rate with
**worse** realized P&L is the signature of buying adverse selection, and is a kill rather than a
puzzle. The average-fill-price column is a sanity check: if the arms show the same average price,
the experiment is not actually running.

## Pre-registered gate

> **CORRECTED 2026-08-05.** The original gate — "n ≥ 150 settled contracts per arm, promote at
> ≥ 0.5¢" — was arithmetically unreachable. It was set by analogy to the other mmsell gates
> without a power calculation, and those gates measure a *different quantity* (a book's own mean
> against zero, pooled over its whole history) rather than a small difference between two arms.
> The numbers below replace it.

### Why the direct A-vs-B P&L comparison cannot settle this

mmsell's per-trade P&L is bimodal and violent: about +5.5¢ when the sold tail misses, about −94¢
when it hits. The **measured** per-trade standard deviation is **22¢ in both arms**. Against that,
detecting a 0.5¢ difference between two independent arms needs:

| true difference to detect | n per arm (80% power, α=.05) |
|---|---|
| **0.5¢** (the original bar) | **30,391** |
| 1.0¢ | 7,598 |
| 2.0¢ | 1,899 |
| 5.0¢ | 304 |

At n=150/arm the smallest detectable difference is **~7¢** — fourteen times the bar it was
supposedly gating. At the observed accrual rate (~119 settled contracts per arm per day), a
properly powered 0.5¢ read is **~250 days** away. The direct comparison is not a viable primary
read and must not be treated as one.

Concretely, at n≈139/99 the arms read `+2.62¢` vs `+2.32¢`, a difference of `−0.30¢ ± 2.89¢`
(95% CI `[−5.97, +5.37]`) — and the sign flips depending on whether hot entries are included.
That is what no signal looks like.

### The primary read: twin-paired (difference-in-differences)

The two arms trade **disjoint markets** (the hash guarantees it), so a raw A-vs-B comparison is
dominated by which markets each arm happened to draw. The twins fix this, and this is what they
are *for*:

    per arm:  gap = (twin ¢/contract) − (live ¢/contract)

The twin trades the **same markets** as its live book under a 100%-fill assumption, so market luck
largely cancels **within** each arm. `gap` isolates what non-fills cost that arm; comparing
`gap_a` vs `gap_b` isolates the queue-position effect with far less variance than comparing the
raw means. Every mmsell live book already runs a twin under standing policy, so this needs no new
collection — only that the analysis use it.

**PROMOTE** the 1¢ offset when `gap_b` is smaller than `gap_a` (the offset recovers more of the
paper edge) by a margin exceeding the paired standard error, at **n ≥ 400 settled contracts per
arm**. **KILL** when `gap_b ≥ gap_a` at that n. Re-derive the required n from the *observed paired*
standard error on first read — the 400 is a planning figure from the paired design, not a
measured one, and the honest move is to replace it with the real number once there is one.

### The high-power secondary read: fill rate

Fill rate is a proportion, not a heavy-tailed P&L, so it needs orders of magnitude less data. It
does **not** answer "is the offset worth it" on its own — a higher fill rate bought with worse
trades is the failure mode — but it definitively confirms **the mechanism is live**, and it does
so within days rather than months. Report it always; treat a null here as evidence the experiment
is not running rather than as a result.

### Sanity checks, before trusting any verdict

- **The arms' average fill prices must differ by ~1¢.** Identical prices mean the treatment is not
  being applied. Note the offset is only applicable where the NO spread is **≥ 2¢** — on a 1¢-wide
  book there is no non-crossing price above the bid, so both arms rest at the bid there (see
  `maker_no_price`). Expect the realized average gap to be **under** 1¢ for that reason.
- **Order counts per arm within ~25%.** A large imbalance means the partition is broken, an arm is
  starved, or one arm is being rejected — all confounds. This check caught the post-only-cross
  bug below.
- **Rejected-order counts per arm must be comparable.** A one-sided rejection rate silently
  changes *which markets* an arm trades and invalidates the comparison outright.

### Invalidated data window

**Orders placed before 2026-08-05 are not comparable and must be excluded.** `maker_no_price`
capped the price at the no-ask, but every order is `post_only` — resting *at* the ask is a cross,
which Kalshi rejects (`invalid_order` / "post only cross"). This never bound while the offset was
0, and appeared the moment the +1¢ arm armed: on a 1¢-wide NO spread, `no_bid + 1` clamped to
`no_ask` and was rejected. Measured: **140 of `mmsell10b`'s 331 orders rejected (42%) against 1
for `mmsell10a`** — and because a 1¢ spread is the tightest, most liquid market, the treatment arm
was systematically locked out of exactly the population the control arm traded freely. That is a
confound, not lost volume. Fixed by capping at `no_ask − 1`; the clock on this experiment restarts
from that deploy.
