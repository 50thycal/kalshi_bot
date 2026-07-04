# Idea-Model Run — 2026-07-04

Divergent front-end run against `50thycal/kalshi_bot` @ `a1b7914`. Grounded in the live
`RESEARCH_JOURNAL.md` / `edge_research.md` / `THETA_THESIS.md`, the live book roster, and a
fresh Kalshi board survey. Ranked by expected contribution to the **$100/month realized**
north star, with uncorrelated ballast valued above raw edge.

---

## Phase 0 — grounding (the correlation baseline + the graveyard)

**Live books (the correlation baseline):**
- **Weather family** (one shared synoptic driver — all co-move in a heat wave): `fav` (control),
  `favband` (LAX 50–70¢), `nws`, `cal`, `dist`/`low_dist` (ensemble distribution), `pm`
  (Polymarket-cross), `con` (consensus), `cwin` (city×window), `obs` (obs-pinned late entry).
- **`mmsell`** — maker-SELL of overpriced 5–35¢ longshots, hold-to-settlement. **First +EV edge**
  (trade-tape backtest +5..22¢/ct, split-half +0.187/+0.185, survives off-sports). Open question:
  **fill realism** (queue competition → possible adverse fill subset). Mostly sports.
- **`theta` + `theta1/2/3`** — model-filtered tail-selling on hourly BTC/ETH ladders. **Live and
  bleeding** (−$13/40 settled vs +4.4¢ backtest); diagnosed to near-money RANGE buckets at 20–40¢
  (model mis-centered) + 40–55m entries; three gated revision books running vs the control.
- **Scanner books** `buy_favorite`/`reversion`/`momentum` — mostly dead/testing.

**The graveyard (do not regenerate without a specific material difference):** weather directional
taking; weather near-money market-making (adverse selection −8.6¢); weather entry-timing /
calibration (except LAX favband) / exits (hold-to-settlement optimal, stops are poison); crypto
directional; crypto options-replication vs Deribit; **naive** crypto tail-selling at quotes (~0 EV);
cross-venue divergence *averaged* + lead-lag on liquid non-shocking markets (~1–1.6¢ < 2–4¢ cost);
cross-venue *shock* on WC-winner (0 shocks) + crypto-EOY (mean-reverting noise); structural
no-arb / Dutch-book (882 events → artifacts); favorite-longshot bias taker side (accrues to makers);
diurnal-range coupling, cross-city W→E lead-lag, distribution-tail, favorite momentum, persistence,
mean-reversion, ladder-overround arb — all priced or sub-fee.

**The pending frontier (NOT ruled out — the repo's own live NEXT STEP):** in-play game-market
sub-minute shock test, PM→Kalshi. Method (`xvenue_shock`) validated; prior markets didn't shock.
Never run on live in-play tapes (the repo doesn't collect them yet).

**Meta-lessons (priors, re-derived from the current record):**
1. Efficient on everything derivable from price history (temperature especially) → **low prior** on naive price-only edges.
2. **Staleness / information-lag is the edge DNA** (`obs`, `theta`) → **high prior**; dig here.
3. Passive/maker on *informative near-money* markets dies to adverse selection — **but** maker-SELL of
   *structurally-overpriced longshots* held to settlement is the one +EV edge (`mmsell`). The distinction
   is *what you rest on*, and fill realism is still unproven.
4. Every taker avenue is dead; real edge accrues to the maker/informed side.
5. No locked arb. 6. Cost dominates on liquid markets — clear it by a margin, not on average.
7. Small-n mirages are the recurring enemy (fooled ~5×) → split-half OOS everything.

## Phase 1 — live board (mid-2026, re-confirmed; drifts)

| Category | Liquidity | Cadence | What moves it | Mechanic fit |
|---|---|---|---|---|
| **Sports** (esp. live World Cup, MLB, NBA) | **~80–87% of volume**; millions/match | in-play sub-minute + one-off | scoring shocks, lineups, news | **lead-lag (in-play), structural** |
| **Crypto** hourly ladders | #2 (~$20M/24h) | hourly settles | 1-min spot | **staleness** (theta lives here) |
| **Economics / Fed** (CPI, NFP, FOMC) | thin (~$2M/24h), efficient | scheduled releases | the released number | post-release latency only (contested) |
| **Politics / Elections** | lumpy, spiky | one-off | headlines | event-conditional; variance trap |
| **Weather** | backwater ($4.4M, 0.2%) | daily settles | NBM/HRRR/obs | portfolio-saturated |
| **Culture / mention / tech-IPO** | thin, one-off | mostly one-off | scheduled releases, headlines | case-by-case; hard to size |

**Fee reality (verified against the official June-2026 schedule):** taker `round(0.07·P·(1−P))`
(peaks 1.75¢ @ 50¢, ~free at tails) — repo formula correct. Maker = 25% of taker, **$0.00 after
rounding on standard markets; NO rebate** (the "0.05% maker rebate" claim is a blog conflating a
designated-MM program / Polymarket). **New wrinkle:** marquee events (NFL/NBA finals, presidential
elections) carry a flat **0.25% maker fee**, which on sub-5¢ longshots is 12–50% of price →
**degrades `mmsell` on big game days; gate it.** Polymarket sports remain mostly zero-fee → a
one-venue "watch PM, trade Kalshi" play pays only Kalshi's cost.

---

## Phases 2–3 — slate + screen

18-candidate slate walking mechanics (outer loop) × categories × data-edge, weighted toward the
staleness family, with forced anti-anchor slots (mechanics/categories the book has zero exposure to).
Scored −− (kill) … ++ (strong) on the six axes. **Corr** = correlation to live books (a shared
*return driver* is heavily penalized even if the edge is real). Blunt by design — most don't promote.

| # | Candidate (mechanic × category) | Corr | Edge | Cost | Test | Cap | Reuse | Call |
|---|---|---|---|---|---|---|---|---|
| C1 | Econ post-release directional take (directional × econ) | + | −− | − | + | − | o | **KILL** — taker vs colocated institutions on the most-analyzed numbers; volume tiny |
| C2 | Sports pre-game model vs line (directional × sports) | o | −− | − | o | ++ | − | **KILL** — pre-game efficient vs sharp books (Pinnacle) |
| C3 | In-play own win-prob model (staleness × sports) | ++ | + | o | − | ++ | o | **HOLD** — weaker than C4 (needs a *better* model, not just a venue lag) |
| **C4** | **In-play PM→Kalshi lead-lag (lead-lag × sports)** | **++** | **++** | **o** | **−** | **++** | **++** | **PROMOTE** — cleanest uncorrelated edge; repo's pending #1; WC live now |
| C5 | Intraday HRRR/obs staleness (staleness × weather) | −− | + | o | + | − | ++ | **KILL** — already covered by `obs`; no material difference |
| C6 | Sub-hourly crypto ladder staleness (staleness × crypto) | − | + | o | + | + | ++ | **HOLD** — theta-correlated; don't stack crypto mid-diagnosis |
| **C7** | **Crypto favorite-BUY (staleness × crypto, favorite side)** | **o/−** | **+** | **++** | **++** | **+** | **++** | **PROMOTE** — parked lead, near-free to test, cheap fees, maybe theta's better side |
| C8 | Econ release micro-lag (staleness × econ) | + | − | − | o | − | o | **KILL** — institutional co-lo race; contested; thin |
| C9 | mmsell → crypto/weather tails (maker-sell × recurring) | −− | + | o | + | + | ++ | **KILL** — correlated into books that already own those tails |
| C10 | Maker-sell politics/culture longshots (maker-sell × non-sports) | + | + | − | + | o | ++ | **HOLD** — validate base mmsell fills first; then extend to uncorrelated cats |
| C11 | Two-sided maker on liquid in-play (maker × sports) | o | −− | −− | − | ++ | + | **KILL** — maker on maximally-informative markets = adverse-selection death |
| C12 | Kalshi↔Polymarket-US same-contract RV (relative value × dual-listed) | o | − | − | + | + | ++ | **KILL** — averaged divergence < cost (proven); shock-conditional is C4 |
| **C13** | **WC cross-market coherence (structural × sports tournament)** | **o** | **o/+** | **o** | **++** | **o** | **+** | **PROMOTE** — novel mechanic, testable NOW on public data, WC live |
| C14 | Ladder mis-centering (structural × recurring ladder) | − | o | o | + | + | + | **HOLD** — exactly the failure that bit theta live; model-center dependent |
| C15 | Settlement/timing-window quirk (structural × weather/econ) | − | o | + | o | − | o | **KILL** — one-off; the LST high-window quirk is already understood |
| C16 | Exotics/combo soft mispricing (structural × exotics) | o | − | o | − | − | − | **KILL** — liquid combos price clean; illiquid ones unfillable |
| C17 | Scheduled non-econ release reaction (event-cond × culture/tech) | + | o | − | o | − | − | **KILL** — thin, lumpy, no track-record rate |
| C18 | Weather model-cycle reaction (event-cond × weather) | −− | + | o | + | − | ++ | **KILL** — inside dist/obs already; backwater caps it |

**Screen result: 3 promote (C4, C7, C13), 4 hold, 11 kill.** Top hold to revisit: **C10** once
`mmsell`'s fill-realism is settled (it's the base case for any maker extension).

---

# Phase 4 — pre-registered theses + probe plans

*The predictions below are written before any validation runs. Do not re-scope thresholds post-hoc.*

---

## XGAME — in-play game-market lead-lag (PM→Kalshi on scoring shocks)

*Thesis written 2026-07-04, before any validation ran; predictions pre-registered. Status: pending
probe. This is the repo's own pending NEXT STEP, now formalized as a falsifiable thesis + collector spec.*

### One-liner
On live in-play game moneylines, a scoring event reprices the deeper venue (Polymarket) first; take
the matching Kalshi contract in the ~10–90s window before Kalshi's quote catches up — one venue
(watch PM, trade only Kalshi; no PM KYC/gas).

### Mechanism
- **What mispricing:** immediately after an in-game scoring event, the Kalshi in-play moneyline lags
  the new fair value PM (2–4× the per-game sports depth) has already moved to.
- **Who's on the other side:** Kalshi in-play takers and resting liquidity that haven't updated to the
  event inside the lag window.
- **Why it persists:** sub-minute cross-venue latency + attention fragmentation; the gap is too
  small/fast for casual Kalshi traders to close and too operationally annoying (two venues) to arb —
  but you only *watch* PM, you don't trade it.
- **Edge family:** lead-lag / information-lag, the shock-conditional variant `xvenue_shock` validated
  (method proven; prior markets — WC-winner, crypto-EOY — simply didn't shock on news).

### Pre-registered predictions (net of both-leg Kalshi fees; PM leg untraded)
- **P1 — Directional lead exists.** PASS if PM→Kalshi follow% **> 55%** AND same-10s-bar co-move% **< 40%**
  (a real lag, not simultaneous). KILL if follow% ≤ 50% or same-bar% ≥ 60%.
- **P2 — The lag clears cost.** Median Kalshi follow-through from the first post-shock quote **≥ 4¢**
  (clears ~2–4¢ round-trip + parabolic taker at the traded band). PASS ≥ 4¢ net; KILL if < 2¢.
- **P3 — Asymmetry (PM leads, not vice-versa).** PM→Kalshi follow% exceeds Kalshi→PM by **≥ 10 pts**.
  PASS if directional; KILL if symmetric (shared reaction to a common third feed, no venue edge).
- **P4 — Actable at latency.** Median exploitable window (PM shock → Kalshi convergence) **≥ 20s**.
  PASS ≥ 20s; note-only if 10–20s (borderline, needs colocation).
- **Decision rule:** paper book only if **P1 ∧ P2 ∧ P3**. P2 fail (lag < cost) → shelve (same verdict as
  averaged cross-venue). P1 fail → the in-play frontier is closed and the lead-lag family fully ruled out.

### Probe plan
- **Script:** extend `scripts/xvenue_game_probe.py`, reusing `xvenue_shock.shock_study` +
  `xvenue_leadlag.align/pm_series`. Allowlist in `ops_runner.py`: **yes**.
- **Data collection (the blocker):** the repo does NOT yet store in-play game tapes. Spec a lightweight
  collector riding the live cycle (like theta's ladder-snapshot collector) that, for a matched game,
  polls **both** venues' trade tapes at **≥10s cadence** — Kalshi `GET /markets/{ticker}/trades`, PM
  trades API — into a new `game_tape_snapshots` table (**separate provenance**). Build ~10s bars over
  the game window.
- **Matching:** pair Kalshi `KXWCGAME`/`KXWCROUND` (**World Cup — LIVE NOW, ideal**) or a current
  MLB/NBA series vs PM per-game markets by the two team names; **precision over recall**; use the
  **full** PM clobTokenId.
- **No-lookahead:** the PM shock is identified from PM bars ≤ t; the Kalshi entry is priced at the first
  Kalshi quote strictly **> t**. No decision uses a bar at or after its own entry timestamp.
- **Measurement:** per scoring event — PM→Kalshi vs Kalshi→PM same-bar%/follow%/follow-size (¢), median
  exploitable window (s), net-of-fee follow-through EV in ¢/ct at the traded band; split by sport & shock size.
- **Promotion result:** P1∧P2∧P3 → paper book `xgame` riding the live cycle, one-venue, small size,
  exit on convergence or game-state change.

### Cost + capacity
- **Fee:** Kalshi taker parabolic; in-play moneylines often 30–70¢ (fee 1.5–1.75¢) — P2's 4¢ bar is set
  to clear it; post-shock tail trades are cheaper. PM leg untraded (and PM sports mostly zero-fee).
- **Adverse selection:** prefer **taking** Kalshi on a detected PM shock — here *you* are the informed
  taker (an inversion of the usual maker adverse-selection problem). If resting, haircut for being filled
  by the very shock you're chasing.
- **Capacity:** HIGH — WC per-game volume in the millions/match; MLB/NBA in-play liquid; dozens of
  games/week → good track-record rate once live.

### Correlation
- **Vs current book:** ZERO shared driver with weather (synoptic), theta (crypto spot-vol), or mmsell
  (FLB premium). Driver = in-game scoring shocks + cross-venue latency.
- **Value to $100/mo:** the genuine uncorrelated ballast the goal values most, in the single most liquid
  category — the highest-capacity diversifier available. The missing piece was the pre-registered thesis
  + the collector spec; this supplies both, at the ideal moment (live World Cup).

---

## TFAV — crypto hourly favorite-buy (model-underpriced favorites)

*Thesis written 2026-07-04, before any validation ran; predictions pre-registered. Status: pending probe.*

### One-liner
The mirror of theta — on Kalshi's hourly BTC/ETH ladders, **buy** the 65–90¢ favorites whose price sits
materially *below* what a live realized-vol spot model says they're worth, in the final hour to
settlement, small size, held to close.

### Mechanism
- **What mispricing:** theta's parked side-finding — hourly 65–90¢ favorites ran **~9–11¢ UNDER**priced
  (small n). The retail lottery flow that *over*pays for the exciting tail (theta's edge) correspondingly
  *under*pays for the boring favorite.
- **Who's on the other side:** the same retail lottery flow that funds theta.
- **Why it persists:** attention asymmetry — favorites are boring; the same behavior that over-prices the
  tail under-prices the favorite.
- **Edge family:** staleness (compute the favorite's fair value from a 1-min spot model faster than the
  quote) — same DNA as theta/obs, but the **favorite side**, which theta's live post-mortem suggests is
  better-behaved than the near-money tail-sell that's currently bleeding.

### Pre-registered predictions (net of both-leg fees; favorites → cheap fees)
- **P1 — Unconditional favorite-buy is ~0** (mirror of theta P1). Buying every 65–90¢ favorite at the ask
  is ≤ **+1¢/ct**. If it looks strongly +EV unconditionally, suspect the "already-decided favorite"
  artifact (FLB study) — re-check by horizon before trusting.
- **P2 — Model-filtered favorite-buy clears cost.** Buying only favorites where **100·P_model − ask ≥ 5¢**,
  final-hour entry, nets **≥ +3¢/ct** with **both split-half OOS halves > 0**. PASS ≥ +3¢ AND halves agree;
  KILL if < +1¢ or halves disagree in sign (the 6th small-n mirage).
- **P3 — tte structure matches theta.** Edge concentrates inside the final hour, flat/negative >60m out.
  PASS if final-hour EV > >60m EV.
- **P4 — Low correlation to theta's realized P&L.** On the **same** `crypto_ladder_snapshots`, the
  favorite-buy trade-level P&L series has **|corr| < 0.4** with theta's tail-sell P&L over the same events.
  PASS < 0.4 (its own book); **DEMOTE to a theta feature** if > 0.7 (just theta inverted).
- **Decision rule:** paper book only if **P2 ∧ P4** (real edge AND uncorrelated-enough to stand alone).
  P2 pass / P4 fail → fold the favorite signal into theta's gating, not a standalone book. P2 fail → close
  the favorite side permanently.

### Probe plan
- **Script:** buy-side branch on `scripts/kalshi_theta_study.py` (or `scripts/kalshi_favbuy_study.py`
  reusing its spot-model + ladder-snapshot loaders). Allowlist in `ops_runner.py`: **yes**.
- **Dataset + provenance:** the **already-collected** `crypto_ladder_snapshots` (model P attached) +
  `crypto_spot_candles` (1-min Coinbase) — **live provenance, same tables theta uses. No new collection**
  → fastest verdict of the three. Kept separate from any backfill.
- **No-lookahead:** model P at each snapshot is from spot strictly before the snapshot time (already how
  theta stores it); label by spot-at-close.
- **Measurement:** buyEV in ¢/ct net of worst-case fee, by price band (65–75/75–85/85–90¢), by tte bucket,
  by model-underpricing magnitude; split-half OOS; and the P&L correlation vs theta (P4).
- **Promotion result:** P2∧P4 → paper book `tfav` beside theta on the live cycle, small size, capped per
  event, hold-to-settlement.

### Cost + capacity
- **Fee:** 65–90¢ favorites have LOW parabolic fees (0.6–1.6¢/ct) — cheaper than theta's near-money tails,
  a structural advantage.
- **Adverse selection:** taking the ask → none of the maker variety; the risk is spot moving across the
  strike post-entry, limited by the final-hour window + model filter.
- **Capacity:** same hourly BTC/ETH liquidity as theta (24 settles/day/series, retail depth) → Kelly-sizable
  track record accrues fast.

### Correlation
- **Vs current book:** same *series* as theta but a different *side/return driver* — favorite-buy profits in
  calm regimes and loses on big spot moves; tail-sell profits when tails stay OOM. **P4 is the promotion
  gate** that measures the actual correlation. Zero correlation to weather/mmsell.
- **Value to $100/mo:** cheapest of the three to validate (reuses all theta infra + data), low fees, and if
  P4 passes, a crypto book that diversifies *within* the spot-staleness family instead of doubling theta.
  Also a live diagnostic on whether theta's bleed is a side-selection problem (tail wrong, favorite right).

---

## WCPROP — World Cup cross-market coherence (match result → winner-market propagation lag)

*Thesis written 2026-07-04, before any validation ran; predictions pre-registered. Status: pending probe.
Tournament-scoped / tactical — capacity bounded by the World Cup calendar.*

### One-liner
When a World Cup match settles decisively, the tournament-**winner** ladder must reprice the involved
teams' title odds; if the winner market lags the match settlement, trade the coherent correction on
Kalshi — one venue, public data, **testable today**.

### Mechanism
- **What mispricing:** `KXWCGAME` (per-match) and `KXWC` (tournament-winner) are structurally linked — a
  team's title probability is a function of its results. After a decisive result (elimination, or a
  favorite advancing), the winner ladder should jump; if it lags, the involved teams' winner-contracts are
  briefly mispriced.
- **Who's on the other side:** traders who follow match markets but are slow to propagate the result into
  the different-audience, longer-horizon winner ladder.
- **Why it persists:** attention fragmentation between the two books; the propagation is "obvious" but not
  instant, and it's a *soft* structural edge the locked-arb scan (Dutch-book only) wouldn't catch.
- **Edge family:** structural coherence + event-conditional — a mis-centering across *related* markets, not
  within one ladder. The portfolio has **zero** cross-market-coherence exposure.

### Pre-registered predictions (net of both-leg fees)
- **P1 — The winner market lags settlements.** After a decisive `KXWCGAME` settlement, the involved team's
  `KXWC` contract completes **< 70%** of its eventual repricing within the first **5 min**. PASS if median
  5-min completion < 70%; KILL if ≥ 90% (efficient — the "cross-city lead-lag: real but fully priced" outcome).
- **P2 — The lag clears cost.** The residual move after +5 min, capturable entering at the +5-min winner
  quote and exiting at convergence (or holding), **≥ 3¢/ct net** of both-leg fees. PASS ≥ 3¢; KILL < 2¢.
- **P3 — Not just noise/illiquidity.** The effect holds on winner-contracts with a quoted 2-sided spread
  **≤ 5¢** at the time (real liquidity, not a stale wide quote). PASS if it survives the liquidity filter;
  KILL if it only appears on illiquid wide quotes (unfillable).
- **Decision rule:** paper book only if **P1 ∧ P2 ∧ P3**. P1 fail → cross-market coherence in sports is
  priced; log it and close the family (a cheap, valuable ruling-out during a live World Cup).

### Probe plan
- **Script:** NEW `scripts/xmarket_wc.py` reusing `xvenue_leadlag.kalshi_candles`. Allowlist in
  `ops_runner.py`: **yes**. **Testable NOW with public web-fetchable data — no live-collection blocker**
  (unlike XGAME).
- **Dataset + provenance:** Kalshi public candlesticks (`period_interval=1`) for the `KXWC` winner-ladder
  contracts + `KXWCGAME`/`KXWCROUND` match settlements (timestamps + results) — public REST, **separate
  provenance** from the `weather_*`/`crypto_*` live tables. Pull the last N tournament days.
- **No-lookahead:** align each match settlement timestamp to winner-market minute candles strictly after it;
  entry priced at the **+5-min candle**, never before the result is known.
- **Measurement:** per decisive result — winner-contract % repricing completed at +1/+5/+15 min; residual
  capturable move (¢, net fee) after +5 min; sliced by result type (elimination vs advance) and by
  winner-contract liquidity (the P3 spread filter).
- **Promotion result:** P1∧P2∧P3 → paper book `wcprop` (event-conditional: on a decisive match settlement,
  take the lagging winner-contract, exit on convergence). Tournament-scoped — a seasonal/tactical book.

### Cost + capacity
- **Fee:** Kalshi taker parabolic; live-team winner-contracts trade mid-range (fee up to 1.75¢) — P2's 3¢
  bar clears it; long-tail teams (cheap contracts) have low fees.
- **Adverse selection:** taking on a detected settlement → informed-taker side; risk is the winner market
  being *right* to lag (result already priced in). P3's liquidity filter guards against unfillable stale quotes.
- **Capacity:** the WC winner market is very deep (~$253M projected tournament volume), but the *number of
  tradeable events* is bounded by decisive matches over the remaining tournament (dozens) — a tactical,
  high-conviction book, not a Kelly-grinder.

### Correlation
- **Vs current book:** sports outcomes, but the *return driver* (structural propagation lag between two
  related Kalshi markets) is one nothing in the book shares — orthogonal to weather, crypto, the FLB
  premium, and even to XGAME's in-play scoring-shock driver (this is post-match, multi-minute, title-odds
  propagation, not in-play moneyline microstructure).
- **Value to $100/mo:** **cheapest to a verdict** (public data, no collector); a novel uncorrelated mechanic
  if it works, a clean cheap ruling-out during the exact testable window (live WC) if it doesn't. Either way a win.

---

## Handoff — where these go next
Each thesis enters the repo's pipeline: write the probe → run via the `ops` channel → log the verdict in
`RESEARCH_JOURNAL.md`/`edge_research.md` → if +EV, build the paper book (`paper/strategies.py` +
`kalshi_bot/<name>/`) riding the live cycle like `mmsell`/`theta` → forward-test (~100s trades/week) →
live gate (`live/executor.py` allowlist), small size. Generically: enters `kalshi-strategy` at Phase 2
(data pipeline) / Phase 4 (backtest) with the thesis + predictions already articulated.

**Suggested run order (by cost-to-verdict):** TFAV first (reuses all theta infra + already-collected data,
~0 new plumbing) → WCPROP (public candles, one new read-only script, no collector) → XGAME (needs the
in-play tape collector, but it's the highest-value uncorrelated edge and the WC window is open now).
