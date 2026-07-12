# COMPIN — settlement-window partial-average pin on TWAP-settled commodity-hub contracts

*Thesis written 2026-07-12 (promoted from `docs/IDEA_MODEL_20260712.md` M1), before any
validation ran; the falsifiable predictions below are pre-registered. Status: pending probe.*

## One-liner

On Kalshi commodity-hub contracts that settle on a **time-window average** of the Pyth feed
(diesel: 41/43 average-settled; some oil cells), the elapsed portion of the averaging window
progressively **locks the settlement value** while quotes track the flashing spot price — buy
the average-locked side once the remaining window can no longer flip the outcome, hold to
settlement.

## Mechanism

- **What mispricing:** settle = the average of the source feed over a defined window. Once a
  fraction *(1−w)* of the window has elapsed, the settle is `A·(1−w) + R·w` with `A` (the
  partial average) already public and fixed. For the outcome to flip, the remaining-window
  average `R` must move by `(strike − A·(1−w))/w − spot` — late in the window this requires
  implausible or outright impossible moves. The quote, anchored to flashing spot near the
  strike, keeps pricing two-way risk (~50–80¢) on an outcome the arithmetic has decided
  (worth ~95–99¢).
- **Why it exists / who's on the other side:** launch-era hub retail trading the live Pyth
  print (the hub launched with a marketing push this month); market-makers concentrate on the
  marquee threshold/close contracts, leaving TWAP cells thin. Nobody casual runs the running
  average — the same counterparty behavior pin15 just validated (anchoring to the last tick
  when a 60-second average settles).
- **Why it persists:** brand-new product; fragmented small markets; the required computation
  (live partial TWAP per contract) is trivial but nobody does it — **mechanics blindness**,
  not source inattention.
- **Edge family:** mechanical-rule observation-pin — the one staleness sub-family the record
  supports (pin15 P1/P2/P4 passed, including across vol quartiles). Distinct from PINNED
  (source-inattention, killed): the counterparty here isn't failing to *read* a public answer,
  they're failing to *compute* one from the settlement rule. Sibling of FREEZE (killed only as
  UNTESTABLE — venue-absence); COMPIN's eligible universe was measured to exist by the same
  enumeration.

## Pre-registered predictions (net of both-leg fees; all EV measured on **actual post-decided trades**, never quoted asks — the phantom-quote/already-decided-favorite mirage has bitten three probes)

- **P1 — The decided-classifier is calibrated.** Define decided-time t\* per market/side as the
  first moment the reach-guard says the remaining window cannot flip the outcome (max plausible
  remaining move = running per-minute reach rate × 1.5, the PINNED-v4 guard, applied to the
  residual weight *w*). PASS if the decided side settles as computed on **≥97%** of decided
  markets; any decided-side **loss** is a red flag halting the verdict until diagnosed (feed
  mapping, window parsing, or guard bug — the PINNED v3 lesson). KILL the probe run (fix before
  grading) if calibration <97%.
- **P2 — The post-decided discount exists and is traded.** Pooled across average-settled
  markets, buying the decided side on real trades strictly after t\* nets **≥ +3¢/ct** net of
  taker fee at ≥100 pooled post-decided trades. KILL if **< +1.5¢** (inside fee+tick noise).
- **P3 — The pin is the mechanism, not favorite drift.** Post-decided EV exceeds the
  **pre-decided favorite-buy control** (same markets, same price bands, entries before t\*) by
  **≥ 2¢/ct**. This is the exact control that killed PINNED; failing it means COMPIN is a
  rebranded favorite-buy (dead via tfav) — KILL.
- **P4 — Capacity floor.** Post-decided traded notional at ≥2¢ discount averages **≥ $150/week**
  across the universe. FAIL → paper book still allowed but flagged size-capped (hobby-scale
  ballast); state so in the registry row.
- **P5 — Window floor (book-shape constraint, pre-registered now).** Only markets whose
  averaging window is **≥30 minutes** qualify for any book (shorter windows collapse toward
  pin15-speed races on a thin venue — out of scope for a ~300s worker). The probe reports EV by
  window length and decided-latency; if the edge lives only in sub-30-min windows, shelve as
  "real but uncapturable at our cadence".
- **UNTESTABLE branch (the FREEZE precedent, stated in advance):** if pooled post-decided real
  trades **< 25**, the verdict is **UNTESTABLE — do not kill the family**; set the revisit
  trigger to: average-settled *settled* market count ≥200, or pooled post-decided trades ≥100.
- **Decision rule:** build paper book `compin` only if **P1 ∧ P2 ∧ P3** (P4 modulates size;
  P5 filters the tradeable universe). If P2 or P3 fail **on ≥25 real trades**, close the
  commodity mechanical-pin family (COMPIN, and do not resurrect SETTLEPIN/TOUCHPIN variants —
  they are co-measured cells of the same claim). No re-scoping after results.

## Probe plan

- **Script:** NEW `scripts/kalshi_compin_study.py` — read-only, self-contained (stdlib +
  urllib), no DB; allowlist in `ops_runner.py`: **yes**. Extends `kalshi_freeze_study.py`'s
  machinery (hub enumeration, contract-terms price-type classification, candle/tape pulls,
  red-flag discipline).
- **Stage 1 — structure enumeration (gates everything):** enumerate settled + open hub markets;
  parse contract terms for `average`/TWAP/VWAP price types **and the averaging-window
  definition** (start, length); output universe table: commodity × cadence × window length ×
  settled count × volume. (Also re-emits the freeze-eligible grain/soft count — the FREEZE
  trigger recheck rides along.)
- **Stage 2 — reconstruction:** for each settled average-settled market, pull the settlement
  feed's history from **Pyth Benchmarks** (public TradingView-shim history endpoint, 1-min bars,
  90 req/10s; feed-id per commodity verified against the contract terms). Compute the running
  partial average A(t) from bars fully closed ≤ t and the decided-time t\* under the ×1.5
  reach-guard. **Deadline: Pyth Benchmarks goes API-key-gated 2026-07-31 — run before then.**
- **Stage 3 — measurement:** Kalshi public REST trade tape (browser UA per the Cloudflare
  gotcha; `yes_price_dollars` field per the PINNED v2 lesson) strictly after t\*: post-decided
  EV net of taker fee vs settlement, the pre-decided favorite control (P3), per-commodity and
  per-window-length splits (P5), post-decided volume histogram (P4), and a red-flag log (P1).
- **Co-deliverable:** an orderbook-depth snapshot on open hub markets (fixes the FREEZE v2
  `n/a` gap) — this is **OPTRV's unblock trigger**, delivered free.
- **Dataset + provenance:** Kalshi public REST (settlement archive, candles, tape) + Pyth
  Benchmarks public history. No live tables touched; provenance never mixed.
- **No-lookahead construction:** A(t) uses only bars fully closed at t; t\* is computed from
  feed data alone (never from the contract's price path); entries are actual trade prints
  strictly after t\*; labels from the settlement archive.
- **Promotion result:** P1∧P2∧P3 → paper book `compin` riding the live cycle: subscribe the
  Pyth feed (free real-time via Hermes), compute running partial averages for open qualifying
  markets, taker-buy the decided side post-t\*, hold to settlement (no exits — the proven
  path). Mandatory `BOOK_REGISTRY.md` row at first trade.

## Cost + capacity

- **Fee/spread math:** decided-side entries at 85–99¢ → taker fee `ceil(0.07·P·(1−P)·100)` =
  **1¢ (≤2¢ at 80¢)**, single leg, hold to settlement. EV is measured on traded prices, so the
  spread is already inside the measurement.
- **Adverse selection:** none (taker). The real risk is a **wrong pin** — feed-mapping error,
  mis-parsed window, or a settlement-rule subtlety; P1's any-loss red flag exists precisely
  for this.
- **Capacity:** honestly thin — the hub's most liquid market is ~$4M and TWAP cells are its
  quiet corner; ~43 diesel + some oil markets, daily/weekly-ish cadence, 24/7. A grinder
  profile at best; P4's $150/wk floor is the pre-registered minimum for unflagged sizing. The
  most likely outcomes are "efficient" or "UNTESTABLE-now" — both cheap, both useful.

## Correlation

- **Vs current book:** zero shared return driver with `weather_con`/`concity` (synoptic
  temperature), `mmsell` (sports FLB), or `theta4` (crypto vol model). Family kinship with
  `pin15` (both mechanical-rule pins) but different underlying (commodity vs BTC), venue (hub
  vs 15-min crypto), counterparty (hub launch retail vs crypto flippers), and timescale (hours
  vs seconds) — a crypto vol shock, heat wave, or sports cycle hits neither the same way.
- **Value to $100/mo:** first commodities exposure; diversifies the validated pin family across
  venues. Small expected dollars, but uncorrelated ballast — and if efficient, a one-script
  ruling-out that also delivers OPTRV's depth read and the FREEZE trigger recheck for free.
