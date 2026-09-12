# METALHALT — the exchange-closure pin on Kalshi metals markets, on the settlement-source axis

*Thesis written 2026-09-12, before any validation ran; the falsifiable predictions below are
pre-registered and must not be re-scoped post-hoc. Promoted from `docs/IDEA_MODEL_20260912.md`
(candidate S1). Status: **KILLED AT CENSUS 2026-09-12 — premise falsified** (see Results). No probe, no book.*

**This is a riff on FREEZE, and it names the material difference.** FREEZE
(`docs/FREEZE_THESIS.md`) is the mechanically-decided-but-still-quoted pin: when a contract's
remaining window falls inside a period in which its settlement source cannot print, the outcome
is fixed while retail keeps quoting it cents from certainty. It was built as `freeze1`–`freeze4`
on 2026-08-13 and **stood down** because the grain/soft universe it was pointed at does not exist
on Kalshi (WS-005, Blocked: "admission was by crop NAME, the wrong axis; it must be by settlement
SOURCE"). The 2026-07-11 probe had excluded metals and energy with one sentence: *"the hub settles
on Pyth (continuous 24/7 pricing), so metals/energy never truly freeze."*

That sentence is false for metals. Pyth's XAU/USD and XAG/USD feeds publish on **metal market
hours** — Sunday 18:00 ET to Friday 17:00 ET, with a daily 17:00–18:00 ET break — and the
15-minute gold and silver markets Kalshi launched in August 2026 run around the clock on
weekdays (96 windows a day). Every 15-minute window that opens at or after 17:00 ET and closes
before 18:00 ET on a weekday is a contract whose reference price cannot move during its life.
The filed 24/7 schedule (pending) would add ~48 hours of frozen windows every weekend. This is
the qualifying universe WS-005 D1 asked whether anyone would look for, found on the axis it said
to look on. It is **new** to the record: no probe, census or book has ever classified a metals
market against the Pyth halt calendar.

## One-liner

Buy the decided side of a Kalshi gold/silver market whose remaining window sits entirely inside
a Pyth feed halt (weekday 17:00–18:00 ET; the weekend once 24/7 listing goes live), whenever it
still trades ≥ 3¢ from certainty — hold to settlement, single leg, taker.

## Mechanism

- **What mispricing:** with the feed halted, the market's reference price at close is already
  known (the last pre-halt print). A window fully inside the halt is decided at its open; a
  window that straddles the halt start is decided from 17:00 ET. Yet the book keeps quoting the
  winner below 100¢ and the loser above 0¢ for the remaining minutes.
- **Why it exists / who's on the other side:** launch-era retail on a heavily-marketed new
  product — the same counterparty PIN15 measured on 15-minute crypto — trading "will gold be up
  this window?" as if the price could still move; plus resting orders left from before the halt.
- **Why it persists:** pennies per contract in the quietest hour of the day, on a venue five
  weeks old; and the venue's own market makers hold Pyth feeds, so any discount that survives is
  what retail flow leaves *after* the makers have pulled.
- **Edge family:** observation-pin / mechanics-blindness — the only family that has ever passed
  (PIN15). The prior is high on the mechanism and **low on capacity**; P4 is the load-bearing
  clause.

## Confronting the graveyard (required)

- **PINNED** (+1.8¢, killed): a published-source pin that converged once the source was public.
  Here the "source" is the *absence* of a print — nothing to converge to until the feed resumes.
- **PIN15** (retired 2026-07-16): a 60-second-average pin with a T-window that netted +0.27¢ in
  live paper against a +1.5¢ bar — the discount closed faster than the book could act. METALHALT
  windows are decided **minutes to hours** early (15 to 60 minutes on the daily break; up to 49
  hours on a weekend), not seconds; the latency budget is the difference.
- **FREEZE's own promotion (+16.10¢ backtest, 100% one commodity in one week)**: the live arms
  could not see the settled result and inferred the decided side from the favorite, making them
  a favorite-buy (`tfav`'s shape). **P2 keeps FREEZE's control**: the same trade in an open
  window must lose to the halted-window trade by the thesis bar, or this is tfav on metals.
- **`weather_maker` / adverse selection**: not applicable — taking a decided outcome, no
  passive leg.

## Pre-registered predictions (net of both-leg fees; measured on **actual post-halt prints**, never quoted asks)

- **P1 — The pinned discount exists and is traded.** On settled metals windows classified
  `inside` or `tail` by the Pyth halt calendar, trades after the decided instant show a mean
  discount to settlement value **≥ 3¢/ct**, pooled n ≥ 80 post-halt trades. **KILL if < 1.5¢**
  (PINNED's bar, kept identical for cross-run comparability).
- **P2 — The halt is the mechanism, not favorite drift.** Post-halt EV exceeds the identical
  trade (buy the market's favorite at the same discount bar) on `outside` windows in the same
  hour-of-day by **≥ 2¢/ct**. KILL otherwise (rebranded tfav).
- **P3 — Zero wrong pins.** Any `inside`/`tail` market whose "decided" side lost is a
  calendar/rules defect (a DST boundary, a holiday session, a settlement rule that reads the
  first post-resume print) and **blocks promotion until explained**. `boundary` windows (close
  exactly at a resume instant) are excluded from the pin population by construction.
- **P4 — Capacity floor.** Post-halt traded notional at ≥ 2¢ discount averages **≥ $150/week**
  across gold + silver. If P1–P3 pass and P4 fails: "real but hobby-scale", no book; trigger =
  the filed 24/7 schedule going live (weekend windows multiply the pinned universe ~30×).
- **Decision rule:** paper book only if P1 ∧ P2 ∧ P3 ∧ P4. P1 or P2 fail → the exchange-closure
  pin is closed **for metals**; with WS-005's grain/soft result that closes the FREEZE family
  on Kalshi and WS-005 moves to `ABANDONED` with the reasoning preserved — which is the
  outcome the workstream itself asked for.

## Probe plan (staged — recon census FIRST)

- **Recon census (step 1, cheap, built):** `scripts/kalshi_metalhalt_census.py` — enumerates
  settled `KXGOLD*`/`KXSILVER*` markets, classifies each window against the halt calendar
  (`outside` / `tail` / `inside` / `boundary`), counts and sums volume per class, and for a
  sample of `inside`/`tail` markets reads 1-minute candles over the decided stretch: active
  minutes and the candle-close discount to certainty. Verdict PROMOTE-TO-PROBE only if ≥ 40
  pinned markets have settled with volume **and** post-halt activity at ≥ 3¢ is observed;
  HOLD (universe absent / too new) otherwise. If Kalshi simply does not list windows inside the
  halt, the census says so in one run and the trigger becomes the 24/7 schedule.
- **Full probe (step 2, only if the census promotes):** point `scripts/kalshi_freeze_study.py`
  at the metals series with the Pyth halt calendar as its dark-window rule (replacing the
  CME/ICE session tables), scoring actual prints from `/markets/trades`. Reuses FREEZE's scorer,
  SETTLEPIN control and P5 wrong-pin check. Needs allowlisting: **no** (existing script, new
  args) unless the calendar is split into its own module.
- **Dataset + provenance:** public Kalshi REST (settled events, candlesticks, trades). No Pyth
  history is needed — the pin is the *calendar*, not the price — which is exactly what makes
  this testable after Pyth Benchmarks went key-gated on 2026-07-31 (COMPIN's blocker).
- **No-lookahead construction:** the decided side is inferred from the **last pre-halt quote of
  the market itself** at the decided instant (the live book cannot see settlement), then graded
  against the realized result — so the cell *can* come out negative, the property WS-005 said the
  original probe lacked.
- **Measurement:** mean post-halt discount (P1), `inside`/`tail` vs `outside` control (P2), wrong
  pins (P3), weekly notional at bar (P4); sliced by metal and by halt type (daily break vs
  weekend).
- **Promotion result:** P1–P4 pass → register `metalhalt` in Experiment OS as a paper book with
  the halt calendar frozen into the contract; the existing `freeze` package is not reused (its
  universe is the wrong one and its contract is frozen).

## Cost + capacity

- **Fee/spread math:** buying the decided side at 85–97¢ → taker fee `ceil(0.07·P·(1−P)·100)`
  ≈ 1¢ or less; single leg, hold to settlement; discounts measured on traded prices, so the
  spread is inside the measurement.
- **Adverse selection:** none (taking a decided outcome). The risk is a **wrong pin** — P3.
- **Capacity:** the honest weak axis. Today's pinned universe is at most 4 windows × 2 metals ×
  5 weekdays = 40 windows/week on the daily break; retail volume in that hour is the question P4
  answers. The 24/7 filing is the capacity trigger.

## Correlation

- **Vs current book:** zero shared driver with `Fmmsell10` (sports cheap-tail maker-sell) or
  with PERPMM (crypto perp micro-reversion). Driver = the metals market calendar + launch-era
  retail flow.
- **Value to $100/mo:** a candidate uncorrelated ballast stream at near-zero validation cost
  (one census, public data, an existing scorer); and either way it *closes* WS-005 — the
  FREEZE family stops being re-proposed every quarter.

## Results — census runs 1 and 2, 2026-09-12 (KILL — PREMISE)

**Run 1** (ops `metalhalt-census-1`, code `2ea29a04`): the pinned universe *exists* as the calendar
defines it — 62 settled 15-minute gold/silver windows (`KXGOLD15M` 31, `KXSILVER15M` 31) whose
entire life sits inside Pyth's published XAU/XAG halt, $4.6M of volume, plus 82 `boundary`
windows. But the tape read saw zero active candles (a `volume` vs `volume_fp` key miss) and the 12
sampled "inside" windows resolved to a **mix** of yes and no — impossible if the reference price
were frozen. Run 1's HOLD was therefore not trusted; the script gained a C0 frozen-reference
test and the `_fp` fix (PR #399) and was re-run.

**Run 2** (ops `metalhalt-census-2`, code `5fdb57a9`):

| check | result |
|---|---|
| C0 — do windows decided inside the *same* halt settle on the *same* value? | **No.** 62 windows with a recorded settlement value, 2 halt stretches, **2 of 2 stretches' settlement values moved** |
| C3 — post-"pin" tape (`_fp` fixed) | 15/16 active minutes per window; candle-close "discounts" of 7–68¢ — i.e. ordinary live prices on markets whose outcome was still open, not a pinned side trading cheap |
| verdict as printed | **KILL (PREMISE)** |

**Reading.** The settlement feed Kalshi uses for its metals hub keeps printing through Pyth's
nominal metals halt. Pyth launched **24/7 gold and silver indices in June 2026** (MarketVector
governance), and the hub evidently settles on that continuous source, not on the market-hours
XAU/USD feed this thesis's calendar came from. So nothing is mechanically decided early: P1–P4
never reach a measurement because the "decided instant" does not exist. The July 2026 FREEZE
probe's exclusion of metals ("Pyth is continuous") was right for this venue; this thesis's
material difference was wrong.

**What it closes.** The exchange-closure pin has now been searched on both axes WS-005 named:
by crop name (grains/softs — no universe, `freeze-dark-window-pin` RETIRED 2026-09-06) and by
settlement source (metals — source is continuous). **Recommendation for WS-005 D1: `ABANDONED`,
reasoning preserved**, unless a Kalshi series is listed whose rules text names a source that
provably stops printing. Nothing here re-scopes P1–P4; they stand as written and were never
scored.

**Cost:** two read-only ops requests, one 200-line script, zero paper. **Kept:** the halt-calendar
classifier and the C0 frozen-reference test, reusable for any future source-hours pin claim —
the test is exactly the check FREEZE's P5 ("zero wrong pins") should have run first.
