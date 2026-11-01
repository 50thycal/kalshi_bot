# Handoff — a 4-hour research check-in loop

- **From:** the catalog-research session (Research Lab), 2026-11-01.
- **To:** a new, standing **Research Lab** session that the owner keeps open to read.
- **Goal:** every 4 hours, a short **read-only** status on the research in flight, posted in that
  session's chat. The shape is the LIMM check-in loop; the content differs. It reports **progress
  and health**. It never reads a pre-registered result early.

## What it watches

| # | item | why it matters | pre-registration |
|---|---|---|---|
| A | Incentive shadow collector | Order-book snapshots and public trades on the ~150 top-reward programmes. Turned back on 2026-10-30. It died silently once (2026-10-07). | `LIMM_PROGRAMME_OUTCOMES.md` §7–§10 |
| B | LIMM exits vs holding, accrual | Settled `Alimm1` markets toward the floor of 30 markets and 15 events. The read is scheduled for 2026-11-13. | `LIMM_PROGRAMME_OUTCOMES.md` §9, §11 |
| C | P-SLOW v2, accrual | Filled live buy-NO orders since 2026-10-30, by tag. The read is once, on 2026-12-04. | `MMSELL_FILL_FORWARD_READ.md` §6 |
| D | State gas ladder screen | Nothing to accrue in the DB; the re-run is on 2026-12-07. | `MMSELL_LOOKALIKE_PROBE.md` §16–§17 |
| E | The scheduled check-in routines | They must stay enabled and fire on their dates. | `docs/RESEARCH_CALENDAR.md` |
| F | Which live tags are placing orders | Catches a book going silent (C's sample depends on it). | — |

## The no-peek rule (hard)

- **Counts only.** Never run `limm_exit_vs_hold`, `mmsell_fill_forward_read`,
  `mmsell_lookalike_probe` or `limm_programme_outcomes` outside their calendar dates.
- Never compute P&L, win rate or settlement outcome for a pre-registered sample. Reading the answer
  early lets the stopping point choose it.
- Order counts, market counts, settled-market counts and collector row counts are fine.

## One check (every 4 hours)

Use the ops channel exactly as `CLAUDE.md` describes. Every request gets a unique id with the date
and time (for example `rc-a-1101-1600`). Send one `db` statement per request, with no `;`. These
queries were run on 2026-11-01 and work. Wider ones (all-time distinct counts) hit the statement
timeout.

**A. Collector (two requests).**
```sql
SELECT count(*) AS snaps_4h, count(DISTINCT market_ticker) AS mkts_4h, max(at) AS last_snap FROM incentive_market_snapshots WHERE at >= now() - interval '4 hours'
```
```sql
SELECT count(*) AS trades_4h, count(DISTINCT market_ticker) AS trade_mkts_4h, max(received_at) AS last_trade FROM incentive_trade_events WHERE received_at >= now() - interval '4 hours'
```
- **Healthy (2026-11-01):** about 7,500 snapshots and about 150 markets per 4 h, with the latest
  snapshot under 10 minutes old.
- **RED:** the latest snapshot is over 30 minutes old, or there are under 2,000 snapshots in 4 h.
- **Once a day only:** list the top series (`split_part(market_ticker,'-',1)`, distinct markets,
  last 24 h, `LIMIT 15`). Also check whether NFL ladder rewards are back:
```sql
SELECT split_part(market_ticker,'-',1) AS series, count(*) AS live_progs FROM incentive_programs WHERE superseded_at IS NULL AND disappeared_at IS NULL AND incentive_type='liquidity' AND end_date > now() AND last_seen_at > now() - interval '1 day' AND (market_ticker LIKE 'KXNFLESCALATOR%' OR market_ticker LIKE 'KXNFLLADDER%' OR market_ticker LIKE 'KXNFLFFPTS%') GROUP BY 1
```
- Kalshi pulled all seven NFL ladder series on 2026-10-20. If any are back, say so: they were the
  best-paying lead (about $1.00 per market-day).

**B. LIMM accrual (floor: 30 markets, 15 events).**
```sql
SELECT count(DISTINCT p.market_ticker) AS settled_markets, count(DISTINCT regexp_replace(p.market_ticker, '-[^-]*$', '')) AS settled_events FROM positions p WHERE p.raw_json ? 'settled_time' AND p.market_ticker IN (SELECT f.market_ticker FROM fills f JOIN live_orders o ON o.kalshi_order_id = f.kalshi_order_id WHERE o.strategy = 'Alimm1' AND o.action = 'buy')
```
- Baseline 2026-11-01: 17 markets, 15 events.

**C. P-SLOW v2 accrual (all cells, so it is an upper bound on cell fills; the bar is 100 cell fills).**
```sql
SELECT o.strategy, count(DISTINCT o.kalshi_order_id) AS filled_orders, count(DISTINCT o.market_ticker) AS markets FROM live_orders o JOIN fills f ON f.kalshi_order_id = o.kalshi_order_id WHERE o.strategy LIKE '%mmsell10' AND o.action = 'buy' AND o.side = 'no' AND o.created_at >= '2026-10-30 00:00+00' GROUP BY 1
```
- Baseline 2026-11-01: `Kmmsell10` 4 fills (no orders after 2026-10-30 13:32Z), `Vmmsell10` 3,
  `Xmmsell10` 14, `Ymmsell10` 11. See the open item below.

**F. Live tags (last 4 h).**
```sql
SELECT strategy, count(*) AS orders, max(created_at) AS last_order FROM live_orders WHERE created_at >= now() - interval '4 hours' GROUP BY 1 ORDER BY 3 DESC
```
- Normal 2026-11-01: `Alimm1`, `Xmmsell10` and `Ymmsell10` active.
- Flag any live tag in `LIVE_STRATEGIES` with no order for 24 h. One quiet 4-hour window is
  normal for `mmsell`.

**E. Routines.** `list_triggers`. The three `Kalshi:` routines must be enabled with the dates in
`docs/RESEARCH_CALENDAR.md`. After a date passes, confirm that its PR merged, that the calendar row
reads `DONE — <verdict>`, and relay the verdict. If a run failed or left no PR, flag it.

**Finish with `reset ops/request.json` to `{"type":"noop"}`.**

## What to post (every check)

At most 8 plain lines:
- first `ALL GREEN` or `NEEDS ATTENTION: <what>`;
- then one line each:
  - collector;
  - LIMM n/30;
  - P-SLOW fills;
  - live tags;
  - the next calendar date and days to go.

The trend is what matters: say "LIMM 17 → 19 since yesterday", not just the number.

## What it never does

- No env change, lifecycle move, gate verdict, arming or exposure change. No PR per check.
- A problem (collector dead, routine failed, book silent) is reported to the owner. If it is
  durable, it becomes an Experiment OS issue routed to its owning role (Live Ops for a dead
  collector or a silent live book). It is never fixed from the loop.
- No model identifiers in anything pushed.

## Setting it up (the new session does this once)

1. Read `CLAUDE.md`, this file and `docs/RESEARCH_CALENDAR.md`. State the role: Research Lab.
2. Run one check now as the baseline.
3. `create_trigger`, bound to **this** session (no `persistent_session_id`, no new session per
   fire). Name it `Research check-in (4h)`. Cron is
   `CRON_TZ=America/Chicago 13 1,5,9,13,17,21 * * *`: off the hour, and the 9:13 run is the
   morning read. The prompt is the next block.
4. Add the routine to the "Other standing routines" table in `docs/RESEARCH_CALENDAR.md` with a
   docs-only PR, and merge it when CI is green.

> **Routine prompt:** Research check-in (4h). Follow docs/handoffs/HANDOFF-research-checkin.md
> "One check" exactly (git fetch the default branch first): run checks A, B, C and F through the
> ops channel with unique ids, plus E via list_triggers; A's top-series and NFL query only on the
> 09:13 run. Counts only, never read a pre-registered result early. Compare with the previous
> check in this chat, and post the 8-line status (ALL GREEN or NEEDS ATTENTION first). Reset
> ops/request.json to noop. Change nothing in the bot.

## Open item at handoff (owner decision, before 2026-12-04)

- **P-SLOW v2's sample stopped growing.** Under §6, the sample is `Kmmsell10`, or a successor
  declared in `docs/DECISIONS.md`. On 2026-10-30, `Kmmsell10` was restarted as `Xmmsell10`
  (`MMSELL_WIN_SET_CANARY.md` §10): no sports or other categories, and the same slow-cell sizing.
- **No successor has been declared,** so the Dec 4 read will see about 4 fills, extend to Jan 8,
  and likely lapse.
- **The script's `%mmsell%` pattern would also count `Ymmsell10`,** a different book with
  WIN-set selection.
- **This needs deciding now, while no outcome has been read:**
  - (a) declare `Xmmsell10` the successor and exclude `Ymmsell10`; or
  - (b) let P-SLOW v2 lapse.
- The loop reports C by tag in either case.
