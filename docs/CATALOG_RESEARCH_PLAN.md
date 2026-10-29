# Catalog research plan — using the thinned catalog to grow real-money profit

**Status:** plan, owner-requested 2026-10-29 (Calvin: "what should we analyze to see if we can
improve any of our current active strategy or start any new ones"). Nothing here is a probe,
an experiment or a gate. Each study becomes a pre-registered probe in a **Research Lab** session
(`kalshi-probe-builder`), one at a time, in the order below. Parent: WS-023 (catalog) and
DEC-038 (retention, executed 2026-10-28).

## Why this order

Experiment OS state on 2026-10-29: paper books realized about $8.5k over 30 days, but real money
is the north star (DEC-039), and real money is small. It is roughly $79 in lifetime confirmed
liquidity rewards plus small MMSELL canaries. The catalog's paper-vs-live join (WS-023
continuation) found that **on the markets live traded, paper roughly matched live**: the gap was
coverage, because paper traded about 30× more markets. So the lever is turning existing paper
edge into **more real fills safely** and cutting what loses. Finding new edges comes second.
Read standings, gates and P&L from Experiment OS (`xos control-tower`), never from this file.

## What the catalog can and cannot answer

- **Has:** about 6.08M markets, 873k events and 15k series. Each carries rules, lifecycle, final
  result and settlement, joined to paper and live evidence and live economics. Full revision
  history is kept for every selected (traded/tracked) market, every open market and every market
  whose rules changed.
- **Has not:** a price history. For about 3.27M settled, never-selected markets whose rules never
  changed, the intermediate lifecycle snapshots were retired (DEC-038), and coverage flags them
  `revisions_retired_current_object_only`.
- **Prices live in Postgres:** `mmsell_candidate_ticks` (every market the MMSELL scan
  considered, with bid/ask/depth/strike), `incentive_*`, `crypto_ladder_snapshots`,
  `weather_bucket_snapshots`. Every study below joins Postgres prices to catalog outcomes and
  structure.

## Studies

### 1. MMSELL selection-bias audit (first)

- **Question:** across every market the MMSELL scan considered, entered or not, is the edge
  real by series × strike structure × price band × time to expiration, or only in what we
  happened to fill? Which skip rules discard positive-EV markets?
- **Join:** `mmsell_candidate_ticks` (entry price, depth, strike) → final result per ticker.
- **Feeds:**
  - XOS-000046 (stop MLB totals/spreads?);
  - XOS-000009 (substring crypto blocklist drops non-crypto markets);
  - which cells can carry more size, the direct route to more real fills.
- **Why first:** `catalog/research.py` lists `opportunity_population_not_imported` as a
  missing denominator in every cohort. This study supplies it.
- **Cost realism:** score at the taker price (`yes_bid` for a sell) and at the maker price. Cap
  fills at `depth_at_best_bid`. Paper P&L inflated by never-filled orders (XOS-000031) is never
  the yardstick.

### 2. Same-game correlation, so size can go up safely

- **Question:** within one game, how often do spread, total and moneyline tails lose together?
- **Join:** settled results grouped by game (event and series structure), weighted by the cells
  MMSELL actually trades.
- **Feeds:** XOS-000020 (caps key on event, not game) and the `mmsell-correlation-cap` paper
  experiment's cap level.

### 3. Liquidity-incentive placement grounded in outcomes

- **Question:** which programs pay the most reward per market-day against how far markets settle
  from the reference price (lone-fill adverse selection)?
- **Join:** `incentive_programs` and the shadow/marks tables → final results and rules.
- **Feeds:** `LIMM_PLACEMENT_THESIS.md`, whose run 1 flagged the reward model optimistic. This is
  the book already paying confirmed real money.
- **Note:** its gates are BLOCKED_PLATFORM today, so this study is descriptive until Platform
  Change Review applies the open disposition.

### 4. Untraded-capacity map, for new books

- **Question:** which series have settled, never-traded inventory with the volume, open interest
  and settlement regularity to host an MMSELL-style cell?
- **Output:** a shortlist for `kalshi-idea-model` screening, not trades.
- **Caveat:** the research journal already refuted plain favorite-longshot and tail harvesting
  on weather. Do not rediscover them.

## Guardrails

- **XOS-000047 is a pre-registered forward test.** Historical studies may inform it but never
  re-score it, and no gate is re-read after seeing results.
- **No pooling across epochs** that Experiment OS declares non-poolable.
- **A study authorizes nothing.** Promotions follow recorded gate passes only.

## Decisions before study 1

- **D1 — how the analysis reaches the catalog.** The ops runner has read-only Postgres and public
  Kalshi access, but no catalog URL or token (`catalog_review_sources` reads Kalshi, not the
  catalog; WS-026 D1 is the same gap for the monitor). Options:
  - (a) read outcomes for the bounded candidate set from Kalshi's public market endpoint. This
    needs no new access and is enough for study 1.
  - (b) give the ops runner catalog read access. This changes the ops workflow file, which is a
    hard stop for the owner, and it would serve studies 2–4.

  **Recommendation:** (a) for study 1, and decide on (b) before study 2.
