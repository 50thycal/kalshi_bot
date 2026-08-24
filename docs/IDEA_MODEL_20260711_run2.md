# Idea-Model Run #2 — 2026-07-11 (post-PIN15, commodities-hub survey)

Divergent front-end run against the live `RESEARCH_JOURNAL.md`, `docs/BOOK_REGISTRY.md`, the
four prior idea-model docs (07-04, 07-10 ×2, 07-11 15-min), and a fresh board check (web,
2026-07-11). Ranked by expected contribution to the **$100/month realized** north star;
uncorrelated ballast valued above raw edge.

Context that makes this run different from run #2 on 07-10 (which promoted zero and concluded
the board was mined out): **two things changed since that verdict.** (1) **PIN15 passed** its
pre-registered gates (P1 ✅ P2 ✅ P4 ✅) and became a paper book — adding a new, *validated*
meta-lesson the 07-10 run didn't have. (2) **Kalshi launched a Commodities Hub** — a genuinely
new venue that did not exist in any prior survey.

---

## Phase 0 — grounding

**Live books (correlation baseline, from `BOOK_REGISTRY.md`):**
- `pin15` (paper, built today) — 15-min crypto endgame observation-pin; gate n≥150, >+1.5¢,
  edge must live at T≈120–180s entries.
- `theta4` (paper; rest of theta collect-only) — fat-tail ×2.0 crypto tail-sell, edge=6¢; gate n≥80.
- `mmsell` family (control + 1/2/3) — sports FLB maker-sell; mmsell3 (5–10¢) gate n≥150.
- `weather_con` + `weather_concity` — synoptic-temperature consensus; concity gate n≥120.

**Graveyard additions since the 07-10 slate (do not regenerate):** PINNED (post-pin discount
+1.8¢ < bar; gas/CPI cells perfectly efficient — "off-weather source-inattention pinning
converges"), DECAY (−19.97¢ — by-date hope is *under*priced), plus the standing kills
(tfav, theta control/1/2/3, xgame, wcprop, MLBWX, weather directional family).

**Meta-lessons carried as priors — with one NEW entry from PIN15:**
1. Price-history-only edges on mature markets are dead.
2. The staleness family is split: observation-pinning survives; homegrown model-vs-quote keeps
   dying. Prefer signals *deterministic about the outcome*.
3. **NEW (PIN15, validated today): the surviving pin shape off-weather is *mechanics blindness*,
   not *source inattention*.** PINNED showed that once a public *source* answers the question
   (AAA print, BLS release), even backwater quotes converge — source-inattention doesn't
   generalize. PIN15 showed that when the pin comes from a *settlement-mechanics rule* the
   counterparty doesn't internalize (a 60-second average vs the flashing last tick), the discount
   persists **even on an intensely watched feed**. New pins should be mechanical-rule pins, not
   "nobody reads the source" pins.
4. Sports reaction/latency plays are structurally dead (symmetric shared feed).
5. Adverse selection kills passive-on-informative; FLB harvest only maker-sell in the cheapest band.
6. Edges are cell-concentrated; averaging destroys them.
7. Small-n mirages and lookahead bugs are the top process risk.

## Phase 1 — board check (2026-07-11, web)

- **Sports >80%** of a record ~$31B June, World-Cup-driven; **WC final is Jul 19** — the spike
  ends next week and the board reverts to MLB-daily-dominated. No new mechanic fits (family dead).
- **Crypto #2 (~7%)**; the 15-min venue is pin15's home; hourly ladders are theta's.
- **NEW: Commodities Hub** — launched this month with a marketing push. Event contracts on
  WTI/Brent/gold/silver + natural gas, coffee, copper, sugar, corn, soybeans, wheat, nickel,
  diesel, lithium. **Settles on Pyth Network feeds** (Pyth Pro goes direct to Kalshi's MMs);
  trades **24/7 including nights/weekends when the underlying exchanges are closed**; expiries
  intraday → weekly → quarterly. The contract-terms template (public PDF) allows price types:
  official settlement, open/close/high/low, last-trade-at-time, **LBMA-style fixes, TWAP, VWAP**;
  comparison structures include **one-touch "in" markets with early expiration on touch** and
  discrete-time "at" markets. **Liquidity is thin** — the most liquid oil market ~$4M
  (weather-scale backwater), and the hub is brand new.
- **Econ/Fed ~1%**, efficient; **weather ~0.2%** backwater (portfolio home).
- **HURR trigger check:** no active Atlantic system; NOAA/CSU call a below-normal El Niño
  season (high shear). The HURR hold's trigger is unfired and unlikely to fire soon.
- Perps remain unreachable via the public event API (unchanged from 07-09).

**The structural observation powering this run's slate:** the commodity hub trades 24/7 against
underlyings that **stop printing** — CME grains halt ~2:20pm–8pm ET every weekday, softs
(coffee/sugar) trade only a short day session, energy/metals halt nightly 5–6pm ET and all
weekend (Fri 5pm → Sun 6pm ET). A Kalshi market whose remaining settlement window falls
entirely inside a source freeze is **mechanically decided** — same determinism DNA as pin15,
with hours-to-days of latency budget instead of seconds.

---

## Phases 2–3 — slate + screen

16-candidate slate, mechanics as the outer loop, anti-anchor slots forced (commodities = zero
prior portfolio exposure; climate; politics). Scored −− … ++ on the six axes
(**Corr** = penalty for sharing a return driver with a live book).

| # | Candidate (mechanic × category) | Corr | Edge | Cost | Test | Cap | Reuse | Call |
|---|---|---|---|---|---|---|---|---|
| **F1** | **FREEZE — exchange-closure pin on commodity-hub markets (obs-pin × commodities)** | **++** | **+** | **+** | **++** | **−** | **++** | **PROMOTE** — the one new mechanical-rule pin on the one new venue; probe is a cheap variant of the existing pinned study |
| F2 | SETTLEPIN — post-settle-print discount on daily commodity markets (obs-pin × commodities) | ++ | o | + | ++ | o | ++ | **CO-MEASURE inside F1** — PINNED says this cell is efficient; the new-hub retail flow is the only material diff; it rides the same enumeration for free and doubles as F1's control |
| F3 | TOUCHPIN — post-touch discount on one-touch "in" markets w/ early expiration (structural × commodities) | ++ | o | + | + | − | ++ | **CO-MEASURE inside F1** — TOUCH-LOCK was killed (run #2 07-10) as a PINNED rebirth; alive here ONLY as a free secondary measurement with its own strict bar, never a standalone probe |
| F4 | COMPIN — settlement-window TWAP/VWAP endgame partial-average pin (obs-pin × commodities) | + | + | o | − | − | + | **HOLD** — pin15's exact shape, but needs structure discovery (which hub contracts actually use averaging); F1's enumeration answers that for free; revisit after |
| F5 | OPTRV — Kalshi monthly thresholds vs CME options-implied density (RV × commodities) | ++ | o | − | o | − | − | **HOLD** — the "model" is a liquid options market (not homegrown), which is materially new; but new CME-data plumbing for thin monthly settles; wait for F1's spread/depth read before building anything |
| F6 | EIALAG — post-EIA-storage-report repricing on gas/crude thresholds (event-cond × commodities) | + | − | − | + | −− | + | **KILL** — racing MMs who hold direct Pyth Pro feeds on a thin market; the econ-release-latency graveyard (C1/C8/MACRO15) transfers |
| F7 | WASDE — USDA report reaction on grain markets (event-cond × commodities) | + | − | − | + | −− | + | **KILL** — same race, 12 releases/yr → no track record possible |
| F8 | MMCOM — mmsell 5–10¢ maker-sell of hub longshots (maker × commodities) | − | + | o | + | − | ++ | **HOLD → joins the MMX family** behind mmsell3's n≥150 gate (same FLB driver; do not stack maker books mid-A/B) |
| F9 | WKNDVAR — weekend variance-premium sell on between/at markets (structural × commodities) | + | − | o | − | − | o | **KILL** — pricing it needs a homegrown vol model, the dying family |
| C1 | PIN60 — hourly-ladder endgame 60s-average pin (obs-pin × crypto) | −− | + | o | + | + | ++ | **HOLD behind pin15's n≥150 gate** — same return driver (crypto endgame spot-pin) + the tfav favorite-buy caution; a pin15 *variant*, not a thesis |
| C2 | ALT15 — pin15 on XRP/SOL/DOGE/BNB/HYPE 15-min twins (obs-pin × crypto) | −− | + | o | + | − | ++ | **HOLD** — the existing ALTLAG hold, unchanged; pin15 variant ladder |
| C3 | MMPIN — maker-entry variant for pin15 (rest a bid at T≈240s) (maker × crypto) | −− | o | − | + | + | ++ | **FOLD into pin15** as an execution experiment inside the paper book (adverse-selection haircut mandatory); not a thesis |
| C4 | CROSSFREQ — 15-min vs hourly bucket coherence (RV × crypto) | − | o | o | − | + | o | **HOLD** — existing hold, unchanged, low priority |
| A1 | ELNINO — seasonal-forecast staleness on hurricane-count/climate ladders (directional × climate) | + | + | o | − | −− | + | **KILL** — n≈1 per season; no validatable track record however good the CSU/NOAA signal is |
| A2 | NBMLONG — CPC monthly outlook vs monthly avg-temp markets (directional × weather) | −− | o | o | + | −− | ++ | **KILL** — weather-correlated (synoptic driver) + monthly settles + backwater |
| A3 | MIDTERM — generic-ballot vs poll aggregates into Nov (directional × politics) | + | −− | − | o | − | o | **KILL** — the variance-trap family; nothing material has changed |

**Screen result: 1 promote (F1, absorbing F2/F3 as co-measured cells), 5 holds (F4, F5, F8→MMX,
C1, C4; C2 unchanged), 1 fold (C3 → pin15), 7 kills.**

### Why FREEZE clears the screen where PINNED died 24 hours earlier

The PINNED kill must be confronted head-on — it is the nearest graveyard neighbor. Three
material, named differences:

1. **Pin mechanism (the decisive one, per new meta-lesson 3):** PINNED's pins required *reading
   a source* (AAA print, BLS release) — source-inattention, which converged everywhere tested.
   FREEZE's pin is a *mechanical rule*: the underlying exchange is closed, the remaining window
   cannot print, therefore the outcome is decided. That is the pin15 shape — mechanics blindness
   — which passed a probe *today* against an intensely watched feed. Retail trading "gold this
   weekend" contracts is speculating on a price that cannot move.
2. **Venue age:** PINNED tested mature series (weekly AAA gas markets, institutional CPI).
   The commodities hub launched *this month* with a marketing push — launch-era retail flow is
   exactly pin15's counterparty.
3. **Latency budget:** freeze windows run 6–60+ hours (grain afternoons, softs' 19-hour daily
   gaps, full weekends). No race, no worker-cadence constraint at all — the opposite failure
   mode from every latency-gated kill.

Honest weaknesses, stated now: **capacity is the weak axis** (the hub is weather-scale thin,
and freeze windows are the *quiet* hours) — P4 below is load-bearing, and a "real but
hobby-scale" outcome is the single most likely result. And the SETTLEPIN control cell is
*expected* to come back efficient (that is PINNED's verdict); if the freeze cell matches it,
the whole family closes for good.

Full pre-registered thesis: `docs/FREEZE_THESIS.md`.

---

## Updated holds queue (the standing forward queue, consolidated)

| hold | unblock trigger |
|---|---|
| MMX family (FIELD/CULTURE/DOOM/MENTION + **new MMCOM**) | mmsell3 n≥150 fill-realism gate; cheap pre-stage available now via `kalshi_flb` on non-sports categories |
| PIN60 + ALT15 (pin15 variant ladder) | pin15 n≥150 gate |
| COMPIN (commodity TWAP endgame pin) | F1 probe's structure enumeration shows averaging-settled hub contracts exist |
| OPTRV (vs CME options-implied) | F1 probe shows hub spreads/depth make RV fillable |
| CRYPSUB / NEST | theta4 n≥80 gate |
| RTPIN/BOXPIN (entertainment obs-pin) | a cheap collector or public-history probe angle |
| RATELAG (KXFED back-rung lag) | a live macro shock to the front Fed contract |
| CROSSFREQ | low priority; after pin15 reads out |
| HURR | first landfall-threat storm — **checked today: none, below-normal El Niño season; unlikely to fire in 2026** |

## Handoff

One probe to write: `scripts/kalshi_freeze_study.py` (read-only, public REST + fixed exchange
calendars; no DB; allowlist in `ops_runner.py`). It delivers four things in one run: the FREEZE
verdict (P1–P4), the SETTLEPIN control cell, the TOUCHPIN secondary, and the **structure
enumeration** (price types × cadence × depth per commodity) that unblocks or kills the COMPIN
and OPTRV holds. Run via the ops channel once the scripts are merged to default. Verdict lands in
`RESEARCH_JOURNAL.md`; if promoted, paper book `freeze` rides the live cycle
(hold-to-settlement — the proven path), and a `BOOK_REGISTRY.md` row is mandatory at first trade.
