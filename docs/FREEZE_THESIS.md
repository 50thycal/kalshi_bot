# FREEZE — exchange-closure pin on Kalshi commodity-hub markets

*Thesis written 2026-07-11, before any validation ran; the falsifiable predictions below are
pre-registered and must not be re-scoped post-hoc. Promoted from `docs/IDEA_MODEL_20260711_run2.md`
(candidate F1, absorbing F2 SETTLEPIN and F3 TOUCHPIN as co-measured cells).*

**STATUS: PROMOTED TO PAPER 2026-08-13 — books `freeze1`–`freeze4`.**

The revisit trigger fired: settled grain/soft hub markets went from 8 to **241** (corn alone 0 →
233) in the week to 2026-08-11, and the re-run of `scripts/kalshi_freeze_study.py` on 2026-08-12
cleared **all five** pre-registered gates — P1 pooled **+16.10¢/ct** on 39,429 post-pin trades,
P2 **+15.50¢** over the pre-pin favorite control, P3 the FREEZE cell itself at the same +16.10¢,
P4 ~**$1.6M/week** post-pin notional, P5 **zero** wrong pins. Decision rule (P1∧P2∧P3∧P4∧P5) →
paper book. Built as four arms in `kalshi_bot/freeze/`; registry row in `docs/BOOK_REGISTRY.md`.

**Two things the promotion does NOT settle, both load-bearing:**

1. **The live book cannot see the result the backtest scored on.** The study measured "buy the
   side that won". A live book has no such column, so it infers the decided side from the
   market's own favorite. That makes it a favorite-buy — the shape `tfav` died in (−3.6¢/trade at
   n=210). The thesis is that favorites are underpriced *specifically while the source is dark*,
   so the book ships arm **`freeze3`**, which takes the identical trade in an OPEN window. **The
   gate is `freeze1` minus `freeze3`, never `freeze1` alone.**
2. **The backtest number may itself be an artifact.** +16.10¢ sits close to the **+15.82¢** a v1
   lookahead bug manufactured before it was caught (below), and 100% of the edge is one commodity
   that appeared in a single week. Forward paper trading cannot have lookahead by construction, so
   this book is the artifact check. **Read the paper arms, not the backtest, before live capital.**

*Prior status, kept for the record — UNTESTABLE, provisionally shelved 2026-07-11 (NOT killed):*
 Probe
`scripts/kalshi_freeze_study.py` ran to verdict same-day (ops `freeze-0711-1`→`freeze-0711-2`; full
write-up in `RESEARCH_JOURNAL.md`). The load-bearing analytical refinement: the hub settles on
**Pyth (continuous 24/7 pricing)**, so metals/energy never truly freeze — only grains/softs can, and
the hub has **essentially not listed/settled them yet** (settled grain 0 / soft 8; open coffee 22 /
cotton 3 / soybeans 2; no corn/wheat/sugar). The FREEZE cell got **0 post-pin trades** → the
mechanism was never exercised, so the pre-registered "P3 fail → close the family" does NOT fire (it
requires real data). A v1 probe bug (a SETTLEPIN cell scoring Pyth-continuous markets with the
realized result = lookahead) manufactured a fake +15.82¢ and was caught + removed in v2. **Revisit
trigger:** settled grain/soft hub markets grow into the hundreds → re-run the probe.

## One-liner

Kalshi's new commodities hub trades 24/7, but the underlying exchanges stop printing — CME
grains halt ~2:20pm–8pm ET every weekday, softs trade only a short day session, energy/metals
close nightly and all weekend. When a contract's remaining settlement window falls entirely
inside a source freeze, the outcome is **mechanically decided**; buy the decided side while
launch-era retail keeps quoting it 3–15¢ from certainty.

## Mechanism

- **What mispricing:** a hub contract whose settlement window ends while the source feed is
  frozen (or whose settle print / touch has already occurred) has a deterministic payout, but
  keeps trading at a discount to certainty — the winning side under ~97¢, the losing side above
  ~3¢ — for hours to days.
- **Why it exists / who's on the other side:** launch-era retail on a heavily-marketed new
  product, trading "will gold be above X this weekend?" as if the price could still move — the
  same counterparty PIN15 just validated: flow anchored to the *instrument's story* rather than
  the *settlement mechanics*. Kalshi's MMs hold direct Pyth Pro feeds, but resting retail orders
  and thin books leave post-pin trades on the tape.
- **Why it persists:** pennies per contract fragmented across ~14 thin commodities; requires
  cross-referencing exchange calendars with contract windows — a mechanics-reasoning step, not a
  data feed anyone sells.
- **Edge family:** observation-pinning staleness, specifically the **mechanical-rule pin**
  (meta-lesson 3, new today): PINNED proved *source-inattention* pins converge off-weather;
  PIN15 proved *mechanics-blindness* pins persist even on watched feeds. FREEZE is a
  mechanics-blindness pin with an hours-long latency budget.

## Confronting the graveyard (required — PINNED died 24h ago)

PINNED (+1.8¢ pooled, gas cell +0.02¢, P2 fail → killed 2026-07-10) is the nearest neighbor.
Material differences, named: (1) pin = market-hours freeze (a mechanical rule), not a published
source answer; (2) venue = a hub launched this month with fresh retail flow, not mature weekly
gas series; (3) latency budget = 6–60h freeze windows, no race. If the probe comes back at
PINNED-like numbers anyway, the *entire* off-weather pinning family closes — that ruling-out is
itself worth the probe's cost.

## Pre-registered predictions (net of both-leg fees; all measured on **actual post-pin trades**, never quoted asks — fillability is load-bearing)

- **P1 — The pinned discount exists and is traded.** Across the enumerated commodity-hub
  universe, mechanically-pinned contracts show **≥ 3¢ average discount to settlement value on
  real post-pin trades** (winners trading ≤97¢ / losers ≥3¢), pooled n ≥ 80 post-pin trades.
  **KILL if < 1.5¢** (PINNED's exact bar, kept identical for cross-run comparability).
- **P2 — The pin is the mechanism, not favorite drift.** Post-pin EV exceeds a pre-pin
  favorite-buy control on the same markets by **≥ 2¢/ct**. KILL otherwise (a rebranded tfav —
  that family is dead).
- **P3 — The FREEZE cell specifically clears the bar.** The exchange-closure cell alone nets
  **≥ 3¢/ct at n ≥ 25 post-pin trades**. The SETTLEPIN cell (post-settle-print) is expected
  efficient per PINNED and serves as the control; TOUCHPIN (post-touch, pre-early-expiration)
  is a secondary with the same ≥3¢ bar, reported but not load-bearing. If only SETTLEPIN or
  only TOUCHPIN clears and FREEZE doesn't, do NOT promote (that's PINNED again).
- **P4 — Capacity floor.** Post-pin traded volume at ≥ 2¢ discount averages **≥ $150/week
  notional** across the hub. If P1–P3 pass but P4 fails: log as "real but hobby-scale", do not
  build a book, set a revisit trigger on hub volume growth (the hub is new; volume may come).
- **P5 — Zero wrong pins.** Any market where the "pinned" side lost is a probe-construction red
  flag (calendar error, feed didn't actually freeze, settlement-rule subtlety) and blocks
  promotion until explained — the PINNED v3 lesson (8,193 false pins from bad path
  reconstruction) applied in advance.
- **Decision rule:** paper book `freeze` only if **P1 ∧ P2 ∧ P3 ∧ P4 ∧ P5**. P3 fail →
  off-weather pinning family fully closed (kill FREEZE, COMPIN, and strike TOUCHPIN
  permanently). P4-only fail → hold with a named volume trigger.

## Probe plan

- **Script:** NEW `scripts/kalshi_freeze_study.py` — read-only, self-contained, public data
  only, stdlib; **allowlist in `ops_runner.py`: yes.** Reuses the settlement-archive loader +
  post-pin trade-measurement design from `scripts/kalshi_pinned_study.py` (v4, the clean run)
  with a new pin-time constructor.
- **Dataset + provenance:** Kalshi public REST — settled + open commodity-hub series/markets
  (rules text for price type, in/at operator, window), 1-min candles and trade tape (browser UA
  per the Cloudflare gotcha). Exchange trading calendars encoded as **fixed constants** (CME
  Globex energy/metals: Sun 6pm–Fri 5pm ET with 5–6pm daily halt; CBOT grains day session
  9:30am–2:20pm ET + overnight 8pm–8:45am; ICE softs day sessions; LBMA fix times) — public,
  deterministic, no external fetch. Public REST provenance, separate from all live tables.
- **Structure enumeration (co-deliverable):** per commodity × series — price type (settle /
  last-trade-at / fix / TWAP/VWAP), cadence (intraday/daily/weekly/monthly), operator (in/at),
  spread and depth snapshot. This unblocks or kills the COMPIN and OPTRV holds regardless of
  the FREEZE verdict.
- **Pin-time construction (no-lookahead):**
  - *FREEZE pin:* the moment the source's last possible print before the contract's window end
    has occurred — computed from the fixed calendar only (e.g. a "gold above X at Sat noon"
    contract pins Fri 5pm ET). A commodity counts as frozen **only if** its settlement feed's
    constituents are all closed for the entire remaining window; when in doubt (24/7-ish OTC
    gold spot), classify NOT frozen — conservative.
  - *SETTLEPIN:* the official publication clock time per exchange (e.g. CME crude settle
    ~2:30pm ET) + a 5-min grace.
  - *TOUCHPIN:* first candle strictly crossing the threshold for "in" markets; entries measured
    from the **next** candle onward; require final settlement agree with the touch (guards
    against data glitches).
  - All entry prices are trades strictly after pin time; labels from the settlement archive.
- **Measurement:** per cell (pin type × commodity × cadence) — post-pin trade prices vs
  settlement in ¢/ct net of the taker fee at the traded price, n, win%, pre-pin favorite-buy
  control EV (P2), post-pin traded-volume histogram (P4), wrong-pin list (P5), split-half by
  calendar time.
- **Promotion result:** P1–P5 → paper book `freeze` riding the live cycle: watch the calendar,
  take the decided side post-pin at ≥3¢ discount, hold to settlement (no exits — the proven
  live path), caps per market and per day. `BOOK_REGISTRY.md` row mandatory at first trade.

## Cost + capacity

- **Fee/spread math:** buying the decided side at 85–97¢ → taker fee `ceil(0.07·P·(1−P)·100)`
  ≈ **1¢ or less**; single leg, hold to settlement. Discounts are measured on traded prices, so
  the spread is already inside the measurement.
- **Adverse selection:** none (taking, outcome already decided). The real risk is a **wrong
  pin** — P5 makes any instance promotion-blocking.
- **Capacity:** the honest weak axis. Hub is weather-scale (~$4M on its most liquid market) and
  freeze windows are the quiet hours; expect small size per market but recurring windows —
  grain afternoons (5×/wk), softs' 19-hour daily gaps, nightly metal/energy halts, full
  weekends, across ~14 commodities. P4 measures whether that sums to a grinder or a hobby.

## Correlation

- **Vs current book:** zero shared return driver — not synoptic temperature (con/concity), not
  sports FLB (mmsell), not crypto spot-vol (theta4), not BTC endgame displacement (pin15).
  Driver = commodity-exchange calendar mechanics + launch-era hub flow. Conceptual kinship with
  pin15 (both mechanics-blindness pins) but the P&L streams share no underlying, no venue, and
  no clock.
- **Value to $100/mo:** a candidate uncorrelated ballast stream at near-zero validation cost
  (one script, public data, reusing the pinned-study machinery built yesterday); and either
  way it settles the last open question of the pinning family — mechanical-rule pins off-crypto
  — with a clean pre-registered verdict.
