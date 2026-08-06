# mmsell queue-position A/B — what is 1¢ of queue priority worth?

*Built 2026-08-03, **INERT by default** (`mmsell_live_offset_ab_arms=""`). Pre-registered before
any data exists, per `docs/MMSELL_ROADMAP.md` §5.*

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

Evaluated at **n ≥ 150 settled contracts per arm** (both arms must clear it — an underpowered
comparison is reported as UNDERPOWERED, never as a verdict). Compare `mmsell10b` against
`mmsell10a`, **not** against `mmsell10`: the incumbent trades a different slice of flow (it takes
candidates first) and a different clip size, so it is not a valid control for this. `mmsell10a` is
the control, and it exists precisely so there is one.

- **PROMOTE** the 1¢ offset only if `mmsell10b`'s realized ¢/contract beats `mmsell10a` by
  **≥ 0.5¢**. That bar is deliberately above zero: a 1¢ offset must earn back more than the 1¢ it
  pays away on every fill, so anything less than a clear margin is noise dressed as improvement.
- **KILL** it if it lands at or below `mmsell10a`. Queue priority is not worth paying for, the book
  keeps `offset = 0`, and the ~2¢ adverse-selection gap is confirmed as *not* addressable by
  price — which redirects the effort to selection (what we enter) rather than execution.
- **NO** (neither) if it beats `mmsell10a` but by less than 0.5¢: not worth the added complexity
  and standing cost; keep 0 and stop asking.

Sanity checks to read alongside, before trusting either verdict:

- **`mmsell10a`'s average fill price should sit ~1¢ above `mmsell10b`'s NO price.** If the two
  arms show the same average price, the experiment is not actually running.
- **Order counts per arm should be within ~10% of each other.** A large imbalance means the
  partition is not working (or, in pre-2026-08-04 data, one arm was being starved by the
  incumbent), and the comparison is confounded.
- **Each arm's twin (`mmsell10a_pt` / `mmsell10b_pt`) vs its live book** — via `live_paper_parity`.
  A twin/live gap that differs sharply *between* arms is itself the finding: it is adverse
  selection responding to queue position, which is the mechanism under test.

Report fill rate alongside the P&L in every read, so a promote can be attributed to *more fills*
rather than *better fills* — those imply different next steps.

## Why the answer matters either way

- If **paying wins**, the fix to mmsell's live problem is execution, and it is a one-line config
  change already built and measured.
- If **paying loses**, adverse selection is not a queue-position problem — it is a selection
  problem, and the remaining levers are all about *which* markets we rest in (the `maxyes` cap,
  the timing work in `docs/MMSELL_ROADMAP.md` §9a), not what price we rest at. That closes off the
  last untested execution knob, which is worth knowing before spending more on execution ideas.
