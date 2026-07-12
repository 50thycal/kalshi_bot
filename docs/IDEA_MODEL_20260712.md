# Idea-Model Run — 2026-07-12

Divergent front-end run against the live `RESEARCH_JOURNAL.md`, `docs/BOOK_REGISTRY.md`, the
five prior idea-model docs (07-04, 07-10 ×2, 07-11 15-min, 07-11 run2), and a fresh board check
(web, 2026-07-12; the Kalshi API is 403 from the sandbox, so the survey used news/search plus
yesterday's FREEZE enumeration for hub numbers). Ranked by expected contribution to the
**$100/month realized** north star; uncorrelated ballast valued above raw edge.

What changed since the 07-11 run2 (which promoted FREEZE): **(1) PIN15's P4 vol-regime test
PASSED** — the mechanics-blindness pin lesson is no longer provisional; it held across vol
quartiles including the wildest (+3.4¢ at T-180s in Q4). **(2) FREEZE ran to UNTESTABLE** —
the hub is metals/energy-first, the ag/soft freeze universe barely exists (1 dark-window close,
0 post-pin trades); shelved on a growth trigger. **Its enumeration co-deliverable fired COMPIN's
unblock trigger**: average/TWAP-settled hub contracts exist (diesel 41/43, some oil). **(3) The
board survey found two venue changes**: Kalshi perps are live with a public API (closing the
07-09 discovery gap), and art markets (live since May 26) surfaced in this survey for the first
time.

---

## Phase 0 — grounding

**Live books (correlation baseline, `BOOK_REGISTRY.md`):**
- `pin15` (paper, built 07-11, P1/P2/P4 passed) — 15-min crypto endgame 60s-average pin; gate
  n≥150, >+1.5¢, edge must live at T≈120–180s entries.
- `theta4` (paper; edge loosened 10→6¢ on 07-11 per the pre-registered 0-trade decision) — gate n≥80.
- `mmsell` family (control + 1/2/3) — sports FLB maker-sell; mmsell3 (5–10¢) gate n≥150.
- `weather_con` + `weather_concity` — synoptic-temperature consensus; concity gate n≥120.

**Graveyard additions since the 07-11 run2 slate (do not regenerate):** FREEZE (UNTESTABLE —
venue-absence, not a measured flat; trigger = grain/soft settled universe in the hundreds), plus
the standing kills (PINNED, DECAY, tfav, theta control/1/2/3, xgame, wcprop, MLBWX, the weather
directional family, and ~45 screened kills across five runs).

**Meta-lessons carried as priors (the 07-11 set, with #3 upgraded from provisional to validated):**
1. Price-history-only edges on mature markets are dead.
2. The staleness family is split: observation-pinning survives; homegrown model-vs-quote keeps
   dying. Prefer signals *deterministic about the outcome*.
3. **The surviving pin shape off-weather is *mechanics blindness*, not *source inattention* —
   now validated across vol regimes.** PINNED: once a public source answers, even backwater
   quotes converge. PIN15: when the pin comes from a *settlement-mechanics rule* the counterparty
   doesn't internalize (a 60-second average vs the flashing last tick), the discount persists
   even on an intensely watched feed, in high vol too. Hunt settlement-arithmetic gaps, not
   unread sources.
4. Sports reaction/latency plays are structurally dead (symmetric shared feed).
5. Adverse selection kills passive-on-informative; FLB harvest only maker-sell, cheapest band.
6. Edges are cell-concentrated; averaging destroys them.
7. Small-n mirages and lookahead bugs are the top process risk (FREEZE v1's fake +15.82¢ on a
   lookahead cell is the freshest example — caught by the probe's own red-flag discipline).

## Phase 1 — board check (2026-07-12, web)

- **Sports:** WC final **Jul 19** — the record June volume spike ends next week; board reverts
  to MLB-daily. No new mechanic (family dead; marquee-fee gate stands).
- **Crypto event contracts:** 15-min = pin15's home; hourly = theta4's. Unchanged.
- **Perps — DISCOVERY GAP CLOSED:** Kalshi perps are live and now expose a **public API surface**
  (REST + WebSocket + FIX; `perps_openapi.yaml` / `perps_asyncapi.yaml` on docs.kalshi.com).
  13 CFTC-approved contracts, ~$16.1B volume since May, ≤5.7× leverage, transparent funding,
  strong institutional participation + professional MMs. The 07-09 journal item ("find the
  perp-specific endpoint, then a funding/basis probe") is now answerable — and screened below.
- **Commodities hub:** July-7 relaunch/marketing push confirmed. Numbers from yesterday's FREEZE
  enumeration stand: metals/energy-first (silver 369 / gold 359 / oil 342 open), **diesel 41/43
  average(TWAP)-settled**, some oil average-settled; ag/softs tiny (coffee 22 / cotton 3 / soy 2)
  → FREEZE trigger unfired.
- **NEW VENUE — Art markets** (live since **May 26**, first survey to catch them): per-lot
  hammer-price markets ("Modigliani: Homme à la pipe sale price?"), **evening-sale total-realized-
  value markets**, artist-record binaries. Settle on publicly reported Christie's/Sotheby's
  results. Kalshi promises expansion "ahead of the fall auction season" — July–September is the
  auction calendar trough, so the venue is data-thin until the Oct/Nov evening sales.
- **Data-plumbing fact with a deadline:** Pyth Benchmarks historical prices (the hub's settlement
  feed) are **publicly pullable today** (TradingView-shim history endpoint, 90 req/10s) but go
  **API-key-gated on Jul 31, 2026** → any probe needing Pyth history should run before then.
- **HURR trigger:** unchanged, no active system, below-normal season.

## Phase 2–3 — slate + screen

14-candidate slate, mechanics as the outer loop, anti-anchor slots forced (art and perps/indices
= zero portfolio exposure). Scored −− … ++ on the six axes (**Corr** = penalty for sharing a
return driver with a live book).

| # | Candidate (mechanic × category) | Corr | Edge | Cost | Test | Cap | Reuse | Call |
|---|---|---|---|---|---|---|---|---|
| **M1** | **COMPIN — settlement-window partial-average pin on TWAP-settled hub contracts (obs-pin × commodities)** | **++** | **+** | **+** | **+** | **−** | **++** | **PROMOTE** — trigger fired 07-11; the validated mechanical-pin shape with an *hours*-scale latency budget; probe deadline Jul 31 (Pyth) |
| A1 | ARTSUM — evening-sale running-total pin: the sale-total ladder is mechanically pinned lot-by-lot as hammers fall (obs-pin × art) | ++ | + | o | − | −− | + | **HOLD (ART family)** — pin15's shape stretched over a 2-hour live-streamed sale, but the settled sample is tiny until the fall season; pre-stage enumeration now |
| A2 | GUARPIN — third-party-guarantee floor pin: guaranteed lots cannot fail to sell, so sub-floor "sell/clear-$X" sides are quasi-decided pre-sale (structural × art) | ++ | + | + | − | −− | o | **HOLD (ART family)** — public catalog symbols are the signal; needs market-type + catalog enumeration first |
| A3 | ARTEST — hammer-vs-estimate systematic bias vs lot-ladder pricing (RV × art) | ++ | − | o | − | −− | o | **KILL** — a public-forecast model-vs-quote play (the dying half; PRECURSOR parent) on a seasonal thin venue |
| A4 | MMART — maker-sell lot-ladder extreme tails at 5–10¢ (maker × art) | − | + | o | + | − | ++ | **FOLD → MMX family** — same FLB driver as mmsell; queues behind mmsell3's n≥150 gate like FIELD/CULTURE/MMCOM |
| A5 | ARTMECE — sale-total ladder vs sum of per-lot ladders coherence (structural × art) | ++ | −− | −− | o | −− | o | **KILL** — locked-arb family (882-event scan; CDF-COHERENCE parent): sub-spread by construction |
| X1 | PERPBASIS — Kalshi perp ↔ spot/event-ladder basis structure (RV × crypto) | − | −− | − | + | ++ | −− | **KILL** — the flagship is professionally MM'd (Pyth Pro, institutional flow); we'd be the slow leg; plus margin-product plumbing the repo doesn't have. The 07-09 discovery item is RESOLVED: endpoint exists, thesis doesn't clear |
| X2 | PERPFUND — funding-rate carry / funding-as-signal (structural × crypto perps) | − | −− | − | + | ++ | −− | **KILL** — carry needs a leg out of product scope (spot custody / offshore perp); funding-as-direction = P9-PERP parent (price-history family) |
| X3 | PERPREF — perp mark price as an alternate fast reference for pin15 entries (data edge × crypto) | −− | o | o | + | ++ | + | **FOLD → pin15** as an execution experiment inside the paper book (like MMPIN); not a thesis |
| M2 | ENDPIN-COMM — near-expiry endgame pin on hub *threshold/close* contracts vs live Pyth (staleness × commodities) | + | −− | o | + | − | ++ | **KILL** — no averaging → no latency budget → it's a race against MMs holding direct Pyth Pro feeds on a continuously-printing watched source (PINNED + EIALAG parents) |
| S1 | SPORTLOCK — in-play one-way lock discount once a game total/threshold is already reached (obs-pin × sports) | o | − | o | −− | + | − | **KILL** — the lock is visible to everyone watching (in-play attention is maximal — xgame's symmetric-feed finding), and it needs the in-play tape collection deliberately declined 07-09 |
| E1 | IDXCLOSE — NYSE closing-auction imbalance (3:50pm feed) vs daily index markets (event-cond × indices) | + | −− | − | o | o | o | **KILL** — racing institutions on the most-watched print in finance (C8/EIALAG parent) |
| M3 | MMCOM — mmsell 5–10¢ maker-sell of hub longshots (maker × commodities) | − | + | o | + | − | ++ | **HOLD** — existing F8 hold, unchanged, behind mmsell3's gate |
| W1 | WXRAIN — obs-pinning on monotone precip accumulation (obs-pin × weather) | − | + | o | + | − | ++ | **HOLD** — existing N6 hold, unchanged (weather-correlated, portfolio-saturated category) |

**Screen result: 1 promote (M1 COMPIN), 1 new hold family (ART: A1+A2 consolidated), 2 folds
(MMART→MMX, PERPREF→pin15), 2 carried holds, 7 kills.**

### Why COMPIN clears the screen where its graveyard neighbors died

Nearest parents, confronted by name:

1. **PINNED (killed 07-10, +1.8¢ < bar; the favorite-drift control caught it).** PINNED's pins
   required *reading a source* (AAA print, BLS release) — source-inattention, which converged
   everywhere tested. COMPIN's pin is *arithmetic on the settlement rule*: settle = window
   average; late in the window the published partial average + a bounded remaining contribution
   *decide* the outcome while the quote tracks flashing spot. That is pin15's exact shape —
   mechanics blindness — validated across vol regimes **yesterday**. And PINNED's killer control
   is pre-registered here as P3: post-decided EV must beat the pre-decided favorite-buy control,
   or the family closes.
2. **FREEZE (UNTESTABLE 07-11 — venue-absence).** FREEZE needed a market universe (settled
   grain/soft markets closing inside dark windows) that barely exists. COMPIN's universe was
   *measured to exist* by the same enumeration: diesel 41/43 average-settled + oil average cells,
   live and settling now. Same family, sibling thesis, but with actual markets to grade.
3. **EIALAG / WASDE (killed 07-11 — racing Pyth-Pro MMs).** COMPIN doesn't race a discrete
   release. The pin *accrues continuously over hours* as the averaging window elapses; if MMs are
   present and pricing the partial average correctly, the probe returns "efficient" cheaply and
   the family closes — that's the honest null, not a reason to skip the test.

Honest weaknesses, stated now: **capacity is the weak axis** (the hub is weather-scale thin and
TWAP contracts are its quiet corner — a "real but hobby-scale" or UNTESTABLE-now outcome is the
most likely result, exactly like FREEZE); the probe's data risk is the **Pyth history
reconstruction** (feed-id mapping per commodity, 1-min bars approximating the true per-print
average — absorbed by a reach-guard + a pre-registered calibration gate); and the **averaging
windows are unparsed** (if enumeration shows only final-minutes windows, the latency budget
shrinks toward pin15-speed on a thin venue — the thesis pre-registers a ≥30-min window floor for
any book). Full pre-registered thesis: `docs/COMPIN_THESIS.md`.

---

## Updated holds queue (the standing forward queue, consolidated)

| hold | unblock trigger |
|---|---|
| MMX family (FIELD/CULTURE/DOOM/MENTION/MMCOM + **new MMART**) | mmsell3 n≥150 fill-realism gate; cheap pre-stage available now via `kalshi_flb` on non-sports categories |
| **ART family (ARTSUM partial-sum pin + GUARPIN guarantee floor) — NEW** | fall auction season (Oct–Nov evening sales) AND ≥~30 settled art markets with candle history; **pre-stage available now**: a ~30-line read-only KXART\* enumeration (market types, settled counts, volumes, intra-sale candle activity) to confirm testability before any thesis |
| PIN60 + ALT15 (pin15 variant ladder) | pin15 n≥150 gate |
| COMPIN | **PROMOTED this run** (leaves the queue) |
| OPTRV (vs CME options-implied) | now concrete: the COMPIN probe's **orderbook-depth co-deliverable** (fixes the FREEZE v2 n/a gap) shows hub spreads/depth are fillable |
| CRYPSUB / NEST | theta4 n≥80 gate |
| RTPIN/BOXPIN (entertainment obs-pin) | a cheap collector or public-history probe angle |
| RATELAG (KXFED back-rung lag) | a live macro shock to the front Fed contract |
| CROSSFREQ | low priority; after pin15 reads out |
| HURR | first landfall-threat storm — below-normal season, unlikely to fire in 2026 |
| FREEZE | freeze-eligible settled grain/soft universe grows to the hundreds (ride-along recheck inside the COMPIN probe) |
| ~~PERPS discovery~~ | **RESOLVED 07-12**: public API exists (`perps_openapi.yaml`); basis/funding theses killed at screen; residual = PERPREF execution experiment inside pin15, optional |

## Handoff

One probe to write: `scripts/kalshi_compin_study.py` (read-only; Kalshi public REST + Pyth
Benchmarks public history; no DB; allowlist in `ops_runner.py`). Four deliverables in one run:
the average-settled **structure enumeration** (which contracts, which windows), the **COMPIN
verdict** (P1–P4 per the thesis), the **orderbook-depth read** that unblocks or kills OPTRV, and
a **FREEZE-trigger recheck** (grain/soft settled count) as a ride-along. **Run before Jul 31**,
when Pyth Benchmarks goes key-gated. Verdict lands in `RESEARCH_JOURNAL.md`; if promoted, paper
book `compin` rides the live cycle hold-to-settlement (the proven path) with a mandatory
`BOOK_REGISTRY.md` row at first trade.

Cheap parallel actions (no thesis needed): the **ART pre-stage enumeration** (testability read
for the new hold family), and the standing **`kalshi_flb` non-sports calibration cut**
pre-staging MMX.
