# Handoff — Kalshi's historical cutoff silently drops settled markets older than ~3 months

**From:** kalshi_bot, Research Lab session, 2026-11-06 (idea-model run, `docs/IDEA_MODEL_20261106.md`)
**To:** a **task-specific** session for the research-script fix (Part A). The worker and desk paths
(Part B) need a **Platform Change Review** decision before any code changes, and Live Ops must agree
to anything in the live executor.
**Status:** nothing fixed. Diagnosis, blast-radius inventory and one production read only. No
Experiment OS issue opened yet (step 0 below).
**Money at stake:** none today. **What is at stake:** silent data loss. Every affected path fails
by *omission*: a missing label, a skipped row, a `continue`. Nothing errors, so nobody is told.

---

## Read this first

Every runtime fact below was read between **03:46Z and 05:10Z on 2026-11-06**. Re-verify before
acting. Kalshi says the cutoff "advances forward over time", so any date in this brief is already
stale. **Read the cutoff at runtime; never hardcode it.**

## The mechanism

Kalshi splits market data into live and historical tiers ([docs](https://docs.kalshi.com/getting_started/historical_data),
[cutoff endpoint](https://docs.kalshi.com/api-reference/historical/get-historical-cutoff-timestamps)):

- `GET /historical/cutoff` returns, among others, `market_settled_ts`.
- Live `GET /markets` and `GET /markets/{ticker}` **stop returning settled markets older than that
  cutoff**. Kalshi's stated target is about 3 months of live data.
- Those markets are served by `GET /historical/markets` and `GET /historical/markets/{ticker}`
  instead (limit ≤ 1000, cursor paging; per the SDK, `tickers` and `event_ticker` filters are
  mutually exclusive). Historical candlesticks have their own endpoint.

## What is established, and how

1. **It happens in production and it is silent.** MOMO-H probe run 2 (`rl-momo-run2-1106p`, window
   2026-08-01..11-05) asked live `/markets?event_ticker=` for settlement results. **29.6%** of
   decisions (2,612 of 8,815) came back with no `result`, with **0 fetch errors**. The probe treats
   a missing label as an exclusion, so the only symptom was a coverage number.
2. **The cutoff is the cause.** Run 3 on 2026-09-01..11-05 (`rl-momo-run3-1106q`) dropped the
   unlabeled share to **0.16%** (10 of 6,134). The gap is exactly the August markets.
3. **The paper engine is not affected today.** `rl-cutoff-open-1106r`: all **1,084** open paper
   trades (451 tickers) were opened after 2026-10-23, and none is older than 30 days. Worker exposure
   is **latent**: it bites only on a position still open past the cutoff.
4. **A working pattern already exists in the repo.** `kalshi_bot/catalog/ingest.py:419` tries
   `/markets/{t}` and, on a 404 only, `/historical/markets/{t}`. `catalog/selection.py:515`,
   `scripts/catalog_review_sources.py` and `scripts/marktangle_probe.py`/`marktangle2_probe.py`
   already handle the historical tier. `KalshiClient` has a historical *candlesticks* method
   (`get_historical_market_candlesticks`, `kalshi/client.py:353`) but **no historical market lookup**: `get_market` (`:333`) is
   live-only.

## Blast radius (inventory as of `87316e1`)

### Part B — worker and desk paths: latent, need Platform Change Review first

| path | what it does with the market | what happens past the cutoff |
|---|---|---|
| `paper/engine.py:291` | settles every open paper trade from `client.get_market` | 404 → logs "market fetch failed" → `continue`: **the position stays open forever** |
| `live/executor.py:2487` `_closeout_market_untradeable` | is a wind-down ticker still tradeable? | fail-soft returns `False` → treated as tradeable → close orders keep firing until `_closeout_exhausted`'s cap |
| `kalshi/client.py:632` | reads `exchange_index` for shard routing | lookup fails → shard unknown (`None`) |
| `desks/exchange.py:345` `settlement()` | desk settlement, **real-money service** | `_request` raises → desk position cannot settle |
| `evo/marketdata.py:299`, `liquidity_incentive/{programs.py:312, collector.py:890, readonly.py:23}`, `execution/readonly.py:38` | quotes and market reads | expected to fail closed. Verify each |

### Part A — research scripts (21 allowlisted): live damage, low risk

They read settled results from live `/markets` and have **no** historical fallback. The two below
**already reach past the cutoff by default**:

- `scripts/momo_hourly_probe.py` (default `--start 2026-08-01`)
- `scripts/theta_settlement_labels.py` (default `--start 2026-07-11`)

The rest are affected whenever they are pointed at a window older than the cutoff:
`evo_order_probe`, `kalshi_deribit`, `kalshi_desk_board`, `kalshi_favbuy_study`, `kalshi_flb`,
`kalshi_freeze_study`, `kalshi_mlbwx`, `kalshi_mm`, `kalshi_pin15_study`, `kalshi_pinned_study`,
`kalshi_theta_study`, `limm_window_probe`, `mmsell_supply_forecast`, `mmsell_taxonomy_audit`,
`short_window_census`, `untraded_capacity_census`, `xmarket_wc`, `xvenue_game` (inventory: files
that call `/markets` and read `result`/`status=settled`/settlement fields; re-run it, the list drifts).

## The mission (one sentence)

**No code path that grades or settles a Kalshi market may silently lose a market because it
settled before the historical cutoff.** Either it reads the historical tier, or it counts and
reports what it could not read.

Out of scope: re-grading past verdicts (see acceptance check 5), changing any settlement *rule*,
and anything that expands real-money exposure.

## Steps

0. **Open the Experiment OS issue.** No detector covers this, so use `OPEN_MANUAL` (not a
   candidate adoption). Suggested payload: classification `DATA` (provenance problem, not yet proven
   a shared-semantic change), owner `TASK_SPECIFIC`, severity `MEDIUM`, priority `P2`, title
   "Kalshi historical cutoff silently drops settled markets older than ~3 months from live
   /markets". Attach this brief and the three ops ids above as evidence. Procedure:
   `docs/EXPERIMENT_OS_ISSUES.md` "Doing it". Setting `EXPERIMENT_OS_ISSUE_COMMAND` **redeploys the
   worker**; clear it after the receipt.
1. **Part A — one shared stdlib helper for scripts**, modelled on `catalog/ingest.py:419`:
   - `market(ticker)`: live first, then `/historical/markets/{ticker}` **on 404 only**. Any other
     error propagates.
   - `event_markets(event_ticker)`: live `/markets?event_ticker=`, then the historical list for the
     event. Check with one real call whether the historical list accepts `event_ticker`.
   - Return, with every result, **where it came from** (live / historical / not found), so callers
     can count it.
2. **Migrate** `momo_hourly_probe` and `theta_settlement_labels` first (both already lose data),
   then the rest in one sweep. Each migrated script prints `labels: live N / historical N /
   not found N`.
3. **Part B — bring the inventory to Platform Change Review**, not a patch. The proposal to judge:
   add a `KalshiClient.get_market_any(ticker)` (live, then historical on 404), and use it **only**
   where a 404 is currently swallowed (the paper settlement `continue`, the closeout check, desk
   settlement). Leave `get_market` unchanged. PCR decides whether this is a Platform Revision.
   Today it changes the outcome of **zero** rows (fact 3), which argues for a non-revision fix. Live
   Ops must accept the `live/executor.py` and `desks/` changes before they merge.

## Acceptance checks

1. Unit test: live 404 → historical used; both 404 → counted as `not found`, never dropped; a
   non-404 error → raised, not swallowed.
2. The cutoff is read from `/historical/cutoff` at runtime (or not needed at all, because the
   fallback is purely on 404). No hardcoded date anywhere.
3. **Instrument check:** `momo_hourly_probe --start 2026-08-01 --end 2026-11-05` shows `not found`
   ≤ 1% (was 29.6%).
4. Every migrated script reports its label sources. Silence is a fail.
5. **No verdict changes.** MOMO-H stays **KILL** as recorded (`docs/MOMO_HOURLY_PREREG.md`). The
   full-window rerun in check 3 tests the instrument and is not a new reading. Any other probe whose
   recorded verdict used a truncated window gets a note, not a re-grade.
6. Part B: nothing merges without the PCR decision recorded on the issue, plus Live Ops' agreement
   for the executor and desk paths.

## Open questions

- Does `/historical/markets` accept `event_ticker` alone? The SDK says `tickers` and `event_ticker`
  are mutually exclusive.
- Is `XOS-000045` ("live held-position exposure never clears for a market whose settlement was not
  captured on its own UTC day") related? Probably a different mechanism, because its window is days,
  not months. Check rather than assume.
- Do the historical endpoints have their own rate limit?

## Evidence

Ops: `rl-momo-run2-1106p` (29.6% unlabeled, 0 errors), `rl-momo-run3-1106q` (0.16%),
`rl-cutoff-open-1106r` (open paper trades, all < 30 days). Docs: `docs/MOMO_HOURLY_PREREG.md` (run 2,
amendment A3, run 3), `docs/IDEA_MODEL_20261106.md` (A1 platform finding). PRs #736, #739, #740.
