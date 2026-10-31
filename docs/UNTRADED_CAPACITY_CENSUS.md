# Untraded-capacity census — where could a new book live?

**Status:** PRE-REGISTERED 2026-10-31 (Research Lab), before the census ran. **Result 2026-10-31
(`rl-s4-run1-20261031`): C1 PASS — 81 series shortlisted (71 tier A, 10 tier B), a lower bound
because the market pull was truncated (§6).**

Catalog research plan, study 4 (`docs/CATALOG_RESEARCH_PLAN.md`, WS-023). Owner asked for it on
2026-10-31. Probe: `scripts/untraded_capacity_census.py` (read-only, ops-allowlisted). This is a
**census**: its output is a shortlist for `kalshi-idea-model` screening. It tests no edge and
authorizes nothing. Experiment OS is canonical.

## 1. Question

Which Kalshi series settle regularly, with real volume, and have never been traded by any book
here, nor even seen by the MMSELL scan? Study 1 showed the existing MMSELL book will not scale by
coverage, so new money needs new places. This maps them.

## 2. Data

- **Settled markets:** Kalshi public `GET /markets?status=settled&mve_filter=exclude`, with
  `min_close_ts` set to 60 days before the run and `max_close_ts` to the run time, cursor-paged.
  The live endpoint keeps about 70 days (`MMSELL_SEASONAL_FORECAST.md`), so the 60-day window sits
  inside that wall. Parlays (`KXMVE`) are excluded at the source.
- **Series metadata:** `GET /series` (category, title, frequency).
- **What the bot has touched:** distinct series prefixes in `paper_trades`, `live_orders` (any
  tag) and `mmsell_candidate_ticks`, read-only from Postgres.

## 3. Per-series measures (60-day window)

- settled markets; distinct events; distinct close dates (the settlement cadence);
- total volume and median volume per market (`volume_fp`, else `volume`);
- markets per event (median: ladder width);
- YES share (how often a market settles YES; a low share on wide ladders means cheap tails);
- tier:
  - **A:** never in `paper_trades`, `live_orders` or the candidate tape;
  - **B:** seen by the MMSELL scan but never traded;
  - **traded:** in either trade table (reported only).

## 4. Pre-registered shortlist rule

A series makes the shortlist when **all** of these hold:

- ≥ 60 settled markets;
- ≥ 20 distinct close dates (recurring, not one event);
- median volume per market ≥ 500 contracts;
- total 60-day volume ≥ 100,000 contracts;
- tier A or B.

Ranked by total 60-day volume. The top 40 per tier are printed.

**Flags** (printed, never filtering):
- `graveyard-weather` on the Climate and Weather category: plain favourite-longshot and tail
  harvesting there are refuted (journal). Rain and temperature cells are already handled
  elsewhere.
- `crypto` on the Crypto category: the hourly ladder families are already studied.

**Prediction C1 (capacity exists):** at least 10 series pass the shortlist rule.
- **PASS:** hand the shortlist to `kalshi-idea-model` for screening (novelty, mechanism,
  testability). The first concrete next step is an MMSELL-cell replay of the top screened series
  with `mmsell_lookalike_probe`, read with study 1's caveat that a fill-assumed maker replay
  overstates what fills realize.
- **FAIL** (fewer than 10): the untraded universe is thin. Record that, and the new-book route
  goes back to existing families.

## 5. Limits

- 60 days only. Seasonal series (for example NFL in October) are captured in their season, and
  others are missed out of season.
- Volume is not edge, and settlement cadence is not liquidity at a tail price. Both are
  screening inputs, not verdicts.
- Only markets that settled are counted; open-only and long-dated series are not seen.

## 6. Result

**Coverage.**
- The bot has traded 973 series, and the MMSELL scan has seen 878.
- The settled pull **hit the 3,000-page bound: 3.0M markets, not complete.** Counts are therefore
  lower bounds for any series paged late.
- C1 can only gain from a complete pull, so the PASS stands.
- 2,635 series had settled markets.

| tier | series | pass the rule |
|---|---|---|
| A — never traded, never scanned | 1,740 | **71** |
| B — scanned, never traded | 90 | **10** |
| traded (reported only) | 805 | 181 |

**Shortlisted volume by category (A+B, 60 days):**

| category | series | volume |
|---|---|---|
| Commodities | 7 | 1.40B |
| Crypto | 7 | 1.01B |
| Sports | 47 | 77M |
| Financials | 5 | 59M |
| Economics | 13 | 12.6M |
| Politics | 1 | 0.4M |
| Entertainment | 1 | 0.4M |

**What the shortlist is made of.**

1. **15-minute up/down markets on commodities and FX (tier A).**
   - Series: KXGOLD15M (896M contracts in 60 days, the largest series on the shortlist),
     KXWTI15M, KXSILVER15M, KXNATGAS15M, KXCOPPER15M, KXPALLADIUM15M, KXPLATINUM15M; and on FX
     KXEURUSD15M, KXGBPUSD15M, KXUSDJPY15M, KXUSDCAD15M, KXAUDUSD15M.
   - Shape: one market per event, about a 50% YES rate, about 2,300 settlements each.
   - These are **not** cheap-tail markets, so an MMSELL cell does not apply. Their mechanic is
     the 15-minute settlement-average pin already studied on crypto (`PIN15_THESIS.md`). The
     crypto 15M series appear here too, flagged `crypto`.
   - Commodities and FX are a venue the bot has never touched.
2. **Daily state gas-price ladders (Economics, tier A/B).**
   - Series: KXAAAGASD for NY, CT, MA, WA, TX, NJ and IL.
   - Shape: 33 daily close dates, 17-rung ladders, about 1,000–1,300 contracts per market.
   - This is the **same contract shape as KXRAIN** (a measured quantity on a daily threshold
     ladder), the one cell that held at the taker price in study 1.
   - The national KXAAAGASD and KXAAAGASW are traded and were NO-CALL in study 1.
3. **Untouched sports structures.**
   - Tier B: NHL period totals and winners (KXNHL1PTOTAL 9.1M), NFL team sacks, NFL passing
     attempts, MLB first-seven innings and outs, NBL games, Brazilian Serie B halves.
   - Tier A: MLB inning totals and winners, NFL race and first-TD team, NHL 2nd/3rd period
     totals, AHL, NCAA women's volleyball, eBasketball.

**Reading.**
- **C1 PASS:** capacity exists outside everything the bot has touched.
- **By §4, the shortlist goes to `kalshi-idea-model` screening.**
- **Recommended screening order,** by fit with what the evidence says works:
  1. the state gas ladders (rain-shaped; cheapest to replay with `mmsell_lookalike_probe`);
  2. NHL period totals and the NFL team-prop ladders (wide ladders, tier B);
  3. commodity/FX 15-minute markets, as a new mechanic, not an MMSELL cell.
- **Weather is not on the shortlist,** because the bot already trades it, so the
  graveyard-weather flag never fired.
