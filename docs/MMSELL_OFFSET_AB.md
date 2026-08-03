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

## Design — randomized *within* one book

Two live books at different offsets would compete for the same tickers, double the live footprint,
and cross each other (self-trade prevention). Instead each **ticker** is assigned to an arm by a
deterministic hash (`kalshi_bot/live/sizing.py` `offset_arm`):

- **Total live exposure is unchanged from today** — same book, same caps, same one order per ticker.
- Both arms see the same market flow over the same window. This is a genuine randomized
  experiment, not a before/after comparison contaminated by regime change.
- A ticker keeps **one arm for its whole life**, so the entry-retry path
  (`mmsell_live_max_attempts_per_ticker`, up to 6 attempts) cannot flip it mid-market and blend
  two prices into an uninterpretable average.
- Assignment is recomputable from the ticker, so attribution needs **no schema change**. The
  analysis imports the same `offset_arm` the executor called, so the two can never disagree.

**Hot entries are excluded** (arm `None`). A hot entry is priced by the momentum guard
(`mmsell_live_hot_market_defensive_offset_cents`), not by the arm, so counting it into either arm
would measure the guard rather than queue position. The analysis drops them via the `hot_entry`
risk-event code.

Both the live executor (`live/executor.py mirror_mmsell_entry`) and the paper twin
(`mmsell/tracker.py`) call the one shared `maker_offset`, for the same reason they already share
`maker_no_price`: the moment the two derive the price differently, the parity report starts
measuring our own bookkeeping instead of the market.

## Arming it

```jsonc
{"type": "env", "set": {"MMSELL_LIVE_OFFSET_AB_ARMS": "0,1"}}
```

Empty (the default) disables the split entirely and restores exactly today's single-offset
behaviour. A single arm is also treated as off — a one-armed "A/B" cannot answer anything.

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
comparison is reported as UNDERPOWERED, never as a verdict).

- **PROMOTE** a non-zero offset only if its realized ¢/contract beats arm 0 (offset 0) by
  **≥ 0.5¢**. That bar is deliberately above zero: a 1¢ offset must earn back more than the 1¢ it
  pays away on every fill, so anything less than a clear margin is noise dressed as improvement.
- **KILL** that offset if it lands at or below arm 0. Queue priority is not worth paying for, the
  book keeps `offset = 0`, and the ~2¢ adverse-selection gap is confirmed as *not* addressable by
  price — which redirects the effort to selection (what we enter) rather than execution.
- **NO** (neither) if it beats arm 0 but by less than 0.5¢: not worth the added complexity and
  standing cost; keep 0 and stop asking.

Report fill rate alongside the P&L in every read, so a promote can be attributed to *more fills*
rather than *better fills* — those imply different next steps.

## Why the answer matters either way

- If **paying wins**, the fix to mmsell's live problem is execution, and it is a one-line config
  change already built and measured.
- If **paying loses**, adverse selection is not a queue-position problem — it is a selection
  problem, and the remaining levers are all about *which* markets we rest in (the `maxyes` cap,
  the timing work in `docs/MMSELL_ROADMAP.md` §9a), not what price we rest at. That closes off the
  last untested execution knob, which is worth knowing before spending more on execution ideas.
