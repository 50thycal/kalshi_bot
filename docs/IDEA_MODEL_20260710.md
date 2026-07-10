# Idea-Model Run — 2026-07-10

Divergent front-end run against the live `RESEARCH_JOURNAL.md` / `docs/IDEA_MODEL_20260704.md` /
`STRATEGY_LOOP_STATUS.md` and a fresh board check. Ranked by expected contribution to the
**$100/month realized** north star, with uncorrelated ballast valued above raw edge.

---

## Phase 0 — grounding (what changed since the 07-04 run)

**Live books (the correlation baseline, as of today):**
- **weather `con`** (+ new `weather_concity` AUS/CHI/NYC A/B, gate n≥120) — the only historically
  +EV book; synoptic-temperature driver.
- **`mmsell` family** (control ~breakeven + mmsell1/2 + new **mmsell3** 5–10¢ band, gate n≥150) —
  favorite-longshot premium, mostly sports.
- **`theta4`** (fat-tail ×2.0 revival, gate n≥80, expected sparse) — crypto spot-vol driver;
  the rest of theta is collect-only.

**Graveyard additions since 07-04 (do not regenerate):** XGAME WC in-play lead-lag (P2 −2¢ net;
P3 *symmetric* — both venues track the game itself, a structural reason that transfers to any
sport); WCPROP (zero repricing lag through the whole knockout stage); TFAV (−3.6¢ at n=210 —
the favorite side isn't harvestable either); theta family shelved (distribution-*shape* error,
not a parameter error); MLBWX rain→total staleness (P2 −1.5¢ — MLB totals price weather
efficiently); perps unreachable via the public event API.

**Updated meta-lessons (priors for this run):**
1. Price-history-only edges on mature markets: dead (unchanged).
2. The staleness family splits: **observation-pinning survives; model-vs-quote keeps dying.**
   obs/con run on a number that's already *known*; theta (homegrown vol model) and MLBWX
   (rain→runs model) both died to model error. New prior: prefer edges where the fresh signal
   is *deterministic about the outcome*, not a forecast.
3. **Sports latency/reaction plays are structurally dead** — xgame's symmetry finding (both
   venues track the shared live feed) killed the whole family, and wcprop/MLBWX confirmed it
   from other angles. Strong prior against anything that races sports information.
4. Adverse selection still kills passive-on-informative; the FLB premium is harvestable only in
   the cheapest band (5–10¢) via maker-sell.
5. **Edges are cell-concentrated** (mmsell's 5–10¢ band, con's three cities, theta's window) —
   averaging across cells destroys them. Prefer ideas with a natural decomposition story.
6. Small-n mirages and lookahead bugs remain the top process risk (MLBWX v1's fake +5.5¢).

## Phase 1 — board check (2026-07-10)

No structural drift from the 07-04 survey: Kalshi ~**$10B/30d** total, **sports >80%** (WC
winner alone >$800M; final Jul 19 — the WC window is *closing*, not opening), crypto #2,
finance/Fed ~1%, weather ~0.2% backwater, entertainment/other thin. Fee schedule unchanged
(taker `round(0.07·P·(1−P))`, maker ~$0, marquee-event 0.25% maker exception).

---

## Phases 2–3 — slate + screen

14-candidate slate, mechanics as the outer loop, forced anti-anchor slots. Scored −− … ++ on
the six axes (Corr = penalty for shared return driver with a live book). Most don't promote.

| # | Candidate (mechanic × category) | Corr | Edge | Cost | Test | Cap | Reuse | Call |
|---|---|---|---|---|---|---|---|---|
| **N1** | **PINNED — settlement-source pin-buy on slow-data markets (staleness-obs × econ/energy/culture)** | **++** | **++** | **+** | **++** | **o** | **+** | **PROMOTE** |
| **N2** | **DECAY — deadline-hazard premium on "by-date" markets (structural × politics/world/culture)** | **o** | **+** | **+** | **++** | **o** | **+** | **PROMOTE** |
| N3 | FEDSYNC — CME FedWatch ↔ KXFED divergence (RV × econ) | ++ | o | − | + | − | o | **HOLD** — averaged divergence likely < cost (the xvenue lesson); revisit only shock-conditional |
| N4 | HURR — NHC published probabilities vs hurricane markets (staleness × tropical) | + | + | o | − | − | + | **HOLD** — right family, but seasonal/lumpy n; validation too slow to lead with |
| N5 | MMX — mmsell 5–10¢ band → non-sports categories (maker × politics/culture) | − | + | o | ++ | o | ++ | **HOLD** — explicitly sequenced behind mmsell3's n≥150 gate; don't stack maker books mid-A/B |
| N6 | WXRAIN — obs-pinning on monotone rain accumulation (staleness-obs × weather) | − | + | o | + | − | ++ | **HOLD** — pure pin mechanics, but temp-obs book died at −3.7¢ and portfolio is weather-heavy |
| N7 | CRYPSUB — sub-hourly crypto ladder staleness (staleness × crypto) | − | + | o | + | + | ++ | **HOLD** — theta4-correlated; sequenced behind theta4's gate |
| N8 | RELIST — recurring-series opening-quote naivety (structural × any) | o | o | −− | + | o | + | **KILL** — opening spreads are wide precisely when quotes are naive; likely unfillable |
| N9 | MISC — ladder mis-centering vs ensemble (structural × weather) | −− | o | o | + | − | + | **KILL** — con-correlated + exactly the model-center failure that bit theta |
| N10 | CPINOW — Cleveland nowcast vs KXCPI ladder (staleness × econ) | + | o | − | + | −− | o | **KILL** — model-based (lesson 2) and ~12 settles/yr; no track record possible |
| N11 | POLSHOCK — headline shock PM→Kalshi politics (lead-lag × politics) | + | − | − | − | − | + | **KILL** — the xgame symmetry finding transfers (shared feed); shocks rare + lumpy |
| N12 | BOXOFF — tracking data vs box-office markets (directional × entertainment) | + | o | − | − | − | − | **KILL** — data paywalled, weekly cadence, thin |
| N13 | DEEPTAIL — mmsell below 5¢ (maker × sports) | −− | o | o | + | o | ++ | **KILL** — mmsell3-correlated; 1¢ tick makes sub-5¢ risk/reward degenerate |
| N14 | EARN — company/earnings-adjacent reaction (event-cond × tech) | o | − | − | − | −− | − | **KILL** — one-off, thin, no rate |

**Screen result: 2 promote (N1, N2), 5 hold, 7 kill.** Top holds to revisit: **MMX** the moment
mmsell3 passes its gate (it's the natural uncorrelated-underlying extension of a proven band);
**HURR** if the season produces a landfall-threat storm while other work is idle.

---

# Phase 4 — pre-registered theses + probe plans

*Written 2026-07-10, before any validation ran. Thresholds must not be re-scoped post-hoc.*

---

## PINNED — settlement-source pin-buy on slow-data markets

*Thesis written 2026-07-10, before any validation ran; predictions pre-registered.
Status: pending probe.*

### One-liner
On Kalshi markets whose settlement source is a public, slow-moving or scheduled number (AAA gas
average, EIA prints, CPI ladder rungs post-release, chart/rankings markets), buy the side the
already-published source has effectively decided while the quote still trades at a discount to
certainty — the obs edge's DNA (the resolving number is public before the quote converges) on
categories the portfolio doesn't touch.

### Mechanism
- **What mispricing:** after the settlement source pins the outcome (the AAA average can no
  longer mathematically cross the strike; the CPI print is out and every rung is determined;
  the chart tracking week closed), the winning side should trade at 100 minus carry, but on
  backwater markets it keeps trading at 88–96¢ (and the losing side above 0) for hours or days.
- **Why it exists / who's on the other side:** wishful holders who anchor to the story rather
  than the source; stale resting orders; nobody professional watches $10k markets. The **obs**
  lesson says this inattention window is real; the difference from the pruned temp-obs *book*
  (−3.7¢) is material and named: temp-obs competed on Kalshi's flagship weather product where
  everyone watches the same thermometer, and entered on a *running* signal mid-race. PINNED
  enters only at **P≈1 certainty** on markets whose settlement source almost nobody tracks.
- **Why it persists:** capital-unattractive (pennies per contract), fragmented across many small
  markets, and requires actually reading the settlement source.
- **Edge family:** observation-pinning staleness — the one family the record supports (lesson 2).

### Pre-registered predictions (net of both-leg fees; all measured on **actual trades**, never on quoted asks — the favbuy study's "already-decided favorite" artifact was exactly a phantom-quote mirage, so fillability is load-bearing)
- **P1 — Post-pin discount exists and is traded.** Across the study universe, contracts whose
  outcome is source-pinned show **≥ 3¢ average discount to settlement value on real post-pin
  trades** (winners trading ≤97¢ / losers ≥3¢), with ≥100 post-pin trades pooled. KILL if
  < 1.5¢ (inside fee+tick noise).
- **P2 — The pin is the mechanism.** Post-pin EV exceeds pre-pin EV on the same markets by
  **≥ 2¢/ct** (otherwise it's generic favorite drift, not the pin — do not promote a rebranded
  favorite-buy; that family is dead via tfav).
- **P3 — Breadth.** The effect clears P1's bar in **≥ 2 independent series families** (e.g. gas
  AND post-release econ rungs) — one quirky series is not a strategy.
- **P4 — Capacity floor.** Post-pin volume at ≥2¢ discount averages **≥ $200/week notional**
  across the universe (enough that a small paper book can express it; $100/mo needs ~$25/wk).
- **Decision rule:** paper book only if **P1 ∧ P2 ∧ (P3 ∨ P4)**. P1 fail → the inattention
  window doesn't exist off-weather; close the family. P2 fail → artifact; close.

### Probe plan
- **Script:** NEW `scripts/kalshi_pinned_study.py` — read-only, self-contained, public data
  only; allowlist in `ops_runner.py`: **yes** (it needs no DB at all, but ops gives it a runner).
- **Dataset + provenance:** Kalshi public settlement archive + 1-min candles/trade tape
  (`period_interval=1`, browser UA per the Cloudflare gotcha) for 2–4 candidate series
  (gas-price series; CPI/jobs ladder rungs; a chart/rankings series if listed); the settlement
  source's own public history (AAA daily national average, BLS release timestamps, chart
  tracking-week calendar) to reconstruct **pin time** per market. Public REST provenance,
  separate from all live tables; never mixed.
- **No-lookahead:** pin time is computed from the source's publication timestamps only (e.g.
  the first AAA print after which no admissible path crosses the strike; the BLS release
  minute); all entry prices are trades strictly **after** pin time.
- **Measurement:** per market — pin time, post-pin trade prices vs settlement, discount ¢/ct
  net of the (tiny, ~1¢ at 90¢) taker fee, pre-pin control EV, per-series split, and the
  post-pin traded-volume histogram (P4).
- **Promotion result:** P1∧P2∧(P3∨P4) → paper book `pinned` riding the live cycle: watch the
  source, take the discounted side post-pin, hold to settlement (no exits needed — the proven
  live path).

### Cost + capacity
- **Fee/spread math:** buying winners at 88–96¢ → taker fee `ceil(0.07·P·(1−P)·100)` ≈ **1¢ or
  less**; single leg (hold to settlement). The measured discount is already net of the spread
  because it's measured on traded prices.
- **Adverse selection:** none of the maker variety (taking); the real risk is a *wrong pin*
  (source revision, settlement-rule subtlety) — the probe must log any market where the pinned
  side lost, and any such case at all is a red flag to investigate before promotion.
- **Capacity:** small per market but recurring (daily/weekly settles across several series) —
  a grinder profile, Kelly-sizable, fits the $100/mo goal's actual scale.

### Correlation
- **Vs current book:** zero shared driver — not synoptic temperature (con/concity), not the
  sports FLB premium (mmsell), not crypto spot-vol (theta4). Driver = post-publication quote
  inertia on backwater markets.
- **Value to $100/mo:** the highest-prior family (obs DNA) on genuinely uncorrelated
  categories; cheap to test (one script, public data); either a new ballast book or a clean
  cheap ruling-out of "does the inattention window exist outside weather".

---

## DECAY — deadline-hazard premium on "by-date" markets

*Thesis written 2026-07-10, before any validation ran; predictions pre-registered.
Status: pending probe.*

### One-liner
On Kalshi's "will X happen by \<date\>" markets, the YES side stays sticky at 5–20¢ while the
remaining window shrinks; sell it (buy NO, hold to settlement) in the final weeks and harvest
the gap between the quoted hope and the collapsing hazard rate.

### Mechanism
- **What mispricing:** holders anchor to the narrative ("it could still happen") while the
  event's remaining opportunity window shrinks; the quote decays slower than the hazard math.
- **Why it exists / who's on the other side:** hope buyers and stale holders; shorting a 12¢
  contract to win 12¢ risking 88¢ is capital-unattractive and shock-exposed, so nobody
  systematically sweeps it on backwater political/world/culture markets.
- **Why it persists:** it's insurance selling — the premium is partly a *fair* payment for
  news-shock tail risk. The thesis is that on by-date markets the premium **exceeds** fair,
  the same way mmsell's 5–10¢ sports band exceeds fair — but that must be shown net, not assumed.
- **Edge family:** structural time-decay / FLB-adjacent premium selling. Prior is moderate:
  mmsell proves the cheap band premium is real on sports; tfav's death proves the *favorite*
  side isn't — this is the longshot side, different category, taker-held-to-settlement.

### Pre-registered predictions (net of both-leg fees, on executable candle prices)
- **P1 — The premium clears cost.** Buying NO against YES∈[5,20]¢ at **T−14d** on settled
  by-date markets nets **≥ +2¢/ct** pooled at ≥85% win, n≥150. KILL if < +1¢.
- **P2 — The hazard signature.** EV is monotone in time-to-deadline: T−7d cohort ≥ T−14d ≥
  T−28d. If flat/inverted, it's generic longshot premium, not decay — fold the finding into
  the mmsell family's knowledge instead of building a book.
- **P3 — Not a mirage.** Split-half OOS: both halves positive; and the result survives
  excluding the single largest-loss market (no one-event carry).
- **P4 — Survivable tails.** Worst single settlement-day loss across the pooled history ≤ 10×
  the median winning day (else it's steamroller pennies and can't be sized).
- **Decision rule:** paper book only if **P1 ∧ P2 ∧ P3**. P4 fail → paper book allowed but
  flagged size-capped. P1 fail → the by-date premium is fair insurance; close the family.

### Probe plan
- **Script:** NEW `scripts/kalshi_decay_study.py` — read-only, public data; allowlist in
  `ops_runner.py`: **yes**.
- **Dataset + provenance:** Kalshi public settlement archive to enumerate settled markets whose
  title/rules match by-date patterns ("by \<date\>", "before \<date\>", "announce", "sign",
  "resign", …) across politics/world/culture series; public 1-min candles for prices at the
  T−28/14/7d snapshots. Public REST provenance, separate from live tables.
- **No-lookahead:** entry price = last traded/quoted candle price at the T−N timestamp; label =
  settlement. Trivially point-in-time.
- **Measurement:** EV ¢/ct net of the NO-side fee by (price band × tte cohort × category),
  win%, split-half, worst-market and worst-day concentration (P3/P4), and the count of markets
  per month (capacity/track-record rate).
- **Promotion result:** P1∧P2∧P3 → paper book `decay`: taker buy-NO on qualifying by-date
  markets at T−14d, small size, capped per market, hold to settlement (no exits — proven path).

### Cost + capacity
- **Fee/spread math:** buy NO at 80–95¢ → fee ≤ 1.4¢ single leg; the 2¢ bar in P1 clears
  fee + tick. Thin books are fine because entries are patient (any day inside the window).
- **Adverse selection:** taker entry → none; the exposure is a real news shock resolving YES —
  P4 exists to measure exactly that, and the paper book would cap per-market and per-day size.
- **Capacity:** many small markets, monthly-ish resolution each, rolling universe — moderate
  but steady track-record rate; sized to the goal, not to a fund.

### Correlation
- **Vs current book:** conceptually adjacent to mmsell (premium selling) but a different
  driver — deadline hazard on political/world/culture one-offs vs in-game sports longshots
  settling daily. Zero overlap with weather or crypto. If promoted, log realized P&L
  correlation vs mmsell in the loop reports; expected low.
- **Value to $100/mo:** a second, mostly-uncorrelated premium stream that reuses the
  settlement-archive machinery PINNED builds; cheap shared validation cost.

---

## Handoff — where these go next

Both probes are single read-only scripts on public data (settlement archive + candles) — **no
new collectors, no DB, no Railway change**. Suggested run order: **PINNED first** (higher-prior
family, and its settlement-archive loader is the shared plumbing), then **DECAY** (reuses it).
Write probe → allowlist in `ops_runner.py` → run via the ops channel → verdict in
`RESEARCH_JOURNAL.md` → if promoted, paper book riding the live cycle → forward-test → live
gate. Holds to revisit on triggers: **MMX** when mmsell3 passes n≥150; **CRYPSUB** when theta4
resolves; **HURR** on the first landfall-threat storm of the season.
