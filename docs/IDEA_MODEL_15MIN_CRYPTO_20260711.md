# Idea-Model Run — 2026-07-11 — scoped to Kalshi's 15-minute crypto up/down markets

Divergent front-end run **scoped by request to the literal 15-minute crypto coin-flip venue**
(`KXBTC15M` and its ETH/XRP/BNB/HYPE/DOGE/SOL twins) — the venue the theta thesis explicitly
*dismissed* but never *probed*. Grounded in the live `RESEARCH_JOURNAL.md`,
`docs/THETA_THESIS.md`, `docs/IDEA_MODEL_20260710.md`, `STRATEGY_LOOP_STATUS.md`, and a fresh
board/structure check (web, 2026-07-11). Ranked by expected contribution to the **$100/month
realized** north star; uncorrelated ballast valued above raw edge.

---

## Phase 0 — grounding

**Live books (correlation baseline):** weather `con` (+`weather_concity` A/B) — the only
historically +EV book, synoptic-temperature driver; `mmsell` family (control + mmsell1/2/3
5–10¢ band) — favorite-longshot premium, mostly sports; `theta4` (fat-tail ×2.0 revival, now
`edge=6`, sparse) — crypto **hourly-ladder** spot-vol driver, rest of theta collect-only.

**Graveyard (do not regenerate):** theta hourly-ladder tail-sell (shelved — distribution-*shape*
model error, not a parameter miss); tfav hourly favorite-buy (−3.6¢ @ n=210); PINNED/DECAY
(both killed at probe 07-10); XGAME/WCPROP/MLBWX sports & weather lead-lag (all symmetric /
efficient). **The 15-minute up/down venue itself has never been probed** — the theta thesis
ruled it out *by argument* ("trades ~50¢ max-fee zone, no tail structure, rewards reaction speed
we don't have at 300s cycles"), not by measurement. That argument is the null this run tests,
and the material difference below is why it deserves a probe rather than a re-litigation.

**Meta-lessons carried in as priors (from the 07-10 run — the current, evolved version):**
1. Price-history-only edges on mature/liquid markets are dead.
2. **The staleness family has SPLIT: observation-pinning SURVIVES (obs/con run on a number
   that's already known); model-vs-quote KEEPS DYING (theta's homegrown vol model, MLBWX's
   rain→runs model — both died to model error).** New prior: **prefer edges where the fresh
   signal is *deterministic about the outcome*, not a forecast.** ← the load-bearing lesson for
   this run.
3. Reaction/latency plays that *race a symmetric fast venue* are structurally dead (xgame: both
   Kalshi and Polymarket track the same live game feed, so there's no follower to pick off).
4. Adverse selection kills passive-on-informative; FLB premium harvestable only maker-sell in the
   cheapest band.
5. Edges are cell-concentrated; averaging across cells destroys them — prefer a decomposition story.
6. Small-n mirages and lookahead bugs are the top process risk.

## Phase 1 — board / structure check (2026-07-11, web)

- **Series:** `KXBTC15M` (BTC, deepest) + ETH/XRP/BNB/HYPE/DOGE/SOL 15-min twins. A fresh Up/Down
  pair opens at the top of each quarter-hour and closes 15 min later; resolves 99¢ / 0¢.
- **The mechanic that changes everything — settlement is a 60-SECOND AVERAGE.** Every Kalshi BTC
  contract, 15-min included, settles on the **average of the CF Benchmarks BRRNY Real-Time Index
  sampled once/second over the final ~60 seconds** (~60 prints averaged into one settle). The
  target ("Up" reference) is the price captured at window open. Public sources are explicit that
  **"the last tick before settlement is NOT the settlement price — a spike in the final ten
  seconds barely moves a sixty-second average."**
- **Liquidity:** "surprisingly deep for short-dated binaries — a few hundred to a few thousand
  contracts within 2¢ of mid" on BTC; thinner on weekends; alts thinner. Crypto is Kalshi's #2
  category; the 15-min BTC market is the highest-volume short-dated crypto contract. **Capacity is
  not the constraint here** (unlike weather).
- **Fees:** worst-near-50¢ zone — `ceil(0.07·qty·P·(1−P)·100)` ≈ **1.75¢/contract/leg at 50¢**,
  falling fast as price leaves the middle; no settlement fee. **Cost is the hard gate for this
  venue** and any promoted edge must clear it *after* leaving the coin-flip zone.

## Phase 2–3 — slate + screen

14-candidate slate, mechanics as the outer loop, anti-anchor slots forced (cross-asset,
cross-venue, cross-frequency, event). Scored −− … ++ on the six axes (**Corr** = penalty for
sharing a return driver with a live book).

| # | Candidate (mechanic × venue) | Corr | Edge | Cost | Test | Cap | Reuse | Call |
|---|---|---|---|---|---|---|---|---|
| **P1** | **PIN15 — endgame 60s-average *observation-pin* fade of the last-tick-anchored quote (obs-staleness × 15-min BTC)** | **++** | **++** | **o** | **+** | **++** | **++** | **PROMOTE** |
| P2 | OPEN15 — open-anchor drift staleness (spot drifts early, quote lingers ~50¢) | ++ | + | o | + | ++ | ++ | **HOLD** → co-measure inside the PIN15 probe (more latency budget but likelier already-efficient) |
| P3 | UPBIAS — persistent retail "Up" bias / pair overround (structural × 15-min) | ++ | o | + | ++ | ++ | + | **HOLD** → cheap structural check, bundle into the PIN15 probe |
| P4 | ALTLAG — BTC spot leads DOGE/XRP/SOL 15-min quote (lead-lag × cross-alt) | + | + | − | + | − | + | **HOLD** — anti-anchor, plausible, but alt 15-min depth thin (cap) + needs per-alt anchor; screen after PIN15 |
| P5 | CROSSFREQ — 15-min up/down vs the overlapping hourly ladder bucket coherence (RV × cross-frequency) | − | o | o | − | + | o | **HOLD** — anti-anchor structural, but partly correlates with theta's hourly leg + complex; low priority |
| P6 | VOLREGIME — trade only high-realized-vol windows where the average "decides" fastest | ++ | + | o | + | + | ++ | **FOLD into PIN15** — a conditioning cell, not a standalone book |
| P7 | SPIKEFADE — fade a late >0.2% 1-min spot candle the average mutes | ++ | ++ | o | + | ++ | ++ | **FOLD into PIN15** — this IS PIN15's sharpest sub-cell |
| P8 | MACRO15 — the window spanning a :30/:00 CPI/FOMC/jobs print | + | o | − | − | −− | o | **KILL** — few windows/month (tiny n), post-print directional = the contested econ-latency graveyard |
| P9 | PERP — perp funding / futures basis as directional prior | ++ | −− | − | + | ++ | o | **KILL** — price-history family; mature/efficient; meta-lesson 1 |
| P10 | MOM15 — last-N-min return sign predicts up/down | ++ | −− | − | ++ | ++ | + | **KILL** — naive price-history; crypto minute returns ≈ random walk; low prior |
| P11 | MMFLIP — rest offers at ~50¢ mid, capture spread | ++ | −− | −− | + | ++ | − | **KILL** — adverse selection at the coin-flip + worst-fee zone; the weather-maker lesson, worst possible venue |
| P12 | XVENUE15 — Polymarket 15-min crypto vs Kalshi | + | −− | − | − | − | + | **KILL** — Polymarket has no 15-min crypto (hourly/daily only); no fast leg exists |
| P13 | ROUNDPIN — round-number target clustering / pinning | + | −− | o | − | + | − | **KILL** — the target is the continuous open reference price, not a round strike; mechanic doesn't apply |
| P14 | DEPTH15 — final-minute orderbook imbalance predicts settle | ++ | − | o | −− | ++ | − | **KILL** — needs sub-minute book history we don't collect; imbalance IS the retail flow we'd fade, not signal about the already-determined average |

**Result: 1 PROMOTE (PIN15), absorbing SPIKEFADE + VOLREGIME as its cells and co-measuring
OPEN15 + UPBIAS as structural checks in the same probe.** Everything else HOLD/KILL. Full
thesis: `docs/PIN15_THESIS.md`.

### Why PIN15 clears the screen where the theta-era dismissal said it wouldn't

- **Edge family (the decisive axis):** the theta thesis dismissed 15-min as a *model-vs-quote*
  reaction race — the family that KEEPS DYING. But the 60-second-average settlement makes the
  right version an **observation-pin**: by T-30s roughly half of the ~60 one-second prints that
  *are* the settlement are **already public and locked in** — the settle is a *partially-known,
  deterministic number*, not a forecast. That's the obs/con family (the one that SURVIVES,
  meta-lesson 2). The residual is only the final few seconds of spot, shrinking to zero at close.
- **Not the xgame trap (meta-lesson 3):** there is no symmetric fast venue arbing this. The
  counterparty is *retail flippers on the same Kalshi market anchored to the flashing last-tick
  price* while the 60s-average is what settles — a **behavioral + mechanical** asymmetry, not a
  venue race we lose. The classic cell: a last-10-second spike pushes the last tick above target,
  retail buys "Up" toward 80–90¢, but the 60s-average barely moved so **Down is the value side**.
- **Correlation:** return driver = "public deterministic signal the quote lags," on a venue with
  **zero existing book**. Uncorrelated with theta (hourly-ladder vol-model tail-sell), mmsell
  (sports FLB), and weather (temperature). Genuine ballast.
- **Capacity + reuse:** BTC 15-min is the deepest short-dated crypto contract (hundreds–thousands
  within 2¢), ~96 windows/day/asset → a readable sample in **days**; reuses theta's
  `CoinbaseSpotClient` + `SpotModel` + the paper engine's hold-to-settlement path almost verbatim.
- **The honest risk the probe must reject (cost + latency):** fees bite hardest at 50¢, so the
  edge only counts once the pinned side has left the middle; and if the edge only exists in the
  final ~5 s (fully-determined, quote already tight), a 300s worker can't act → **dead-for-us**,
  a cheap valid ruling-out. The probe therefore measures **edge as a function of entry latency
  and how-decided-the-partial-average-is** — that curve is the whole verdict.
