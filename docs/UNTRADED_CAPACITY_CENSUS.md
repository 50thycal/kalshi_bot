# Untraded-capacity census — where could a new book live?

**Status:** PRE-REGISTERED 2026-10-31 (Research Lab), before the census ran. Result: _pending — §6._

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

_Pending._
