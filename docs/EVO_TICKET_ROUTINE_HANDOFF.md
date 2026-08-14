# Handoff — scheduled "what are the bots asking for?" routine

Paste the prompt in §1 into a fresh Claude Code session on this repo, on whatever schedule you
want (daily or every few days is plenty — the fleet files a handful of tickets a week). Everything
it needs is in the repo; no state has to carry between runs.

**Scheduled 2026-08-14: daily at 09:47 local.** One caveat worth knowing — a `/loop` cron job is
**session-only**: it lives in the Claude session that created it, is never written to disk, and
auto-expires after 7 days. So the schedule has to be re-armed roughly weekly, or from a session
that stays alive. A durable alternative is a Routine (`create_trigger`), which fires server-side
into a fresh session on its own cron and survives container restarts; it needs one approval to
create. Either way the prompt is §1.

Because a fresh firing has no memory of the previous one, the routine defines "since the last run"
from the data rather than from recall: a ticket is NEW if `created_at` is inside the last 25 hours,
and something CHANGED if any ticket was created, auto-closed or resolved in that window. That is
what makes §1's "stay silent when nothing changed" instruction actually executable statelessly.

---

## 1. The routine prompt (copy this verbatim)

> EVO TICKET TRIAGE (recurring). Run the `evo-ticket-triage` skill: read the evolutionary fleet's
> capability-request queue (`evo_tickets`) via the ops channel, classify every open ticket, and
> report.
>
> Report ONLY if something needs my attention. Specifically, stay SILENT (no message) when the
> only open tickets are ones you have already reported to me before and nothing changed. Otherwise
> lead with **NEW REQUESTS** — asks that were not in the queue at the last run — then anything that
> auto-closed, then what you recommend building and what you recommend rejecting.
>
> For each ticket you recommend building, tell me in one line: what the agent is trying to test,
> what data or capability it needs, and roughly what it costs to give them. I decide; you do not
> build anything in this session without me saying so.
>
> Do NOT propose research topics of your own. The point of this routine is that the FLEET sets the
> agenda. If the queue is empty of real asks, say that plainly.

That's it. The skill (`.claude/skills/evo-ticket-triage/SKILL.md`) carries the queries, the
classification rules, the closure mechanics and the guardrails.

---

## 2. What's already automated (so the routine doesn't duplicate it)

`tickets.auto_resolve_shipped()` runs **every evo cycle** in the orchestrator. It closes open
tickets whose requested capability has since shipped, matching against the explicit
`SHIPPED_CAPABILITIES` registry in `kalshi_bot/evo/tickets.py`, and writes what shipped into
`implementation_result`.

So the routine should find *already-shipped* tickets only when the registry is missing an entry —
which is a signal in itself, and the skill says to add the entry rather than hand-close. Adding
that entry is the one build the routine may do without asking: it is bookkeeping for something
that already exists, and the queue cannot be closed any other way (the ops DB role is SELECT-only).

**Know what the matcher reads.** `_shipped_match()` tokenizes the `capability` string and nothing
else — not `problem`, not the benefit fields. So a ticket whose real ask lives in its prose is
invisible to the registry no matter how many entries you add, and the fix is an operator decision
rather than a wider matcher. Widening `all_of` until such a ticket matches is how you start
closing the near-misses the registry exists to protect (`strategy_execution`,
`strategy_management`). Three tickets in the queue are in exactly this state — see §3.

One live caveat on the econ entry, for whoever touches it next: its second group includes
`pipeline`, which none of the three real CPI phrasings actually need (they all carry `backtest`
or `backtesting`). So a future ask like "CPI data pipeline for live quotes" would match and close
against a settled-history dataset it cannot use. Dropping `pipeline`, or adding
`none_of={"live", "realtime"}`, would close that hole. Left as-is here rather than changed in
passing, because the entry was merged deliberately and narrow matching is the house rule.

Watch for this in the evo logs:

```
evo tickets: auto-closed N request(s) whose capability shipped
```

---

## 3. State of the queue at handoff (2026-08-13), and the first run (2026-08-14)

35 open tickets at handoff. After the first auto-close pass ran, **22 closed** — the off-switch
wave (`deactivate_strategy`, `strategy_deactivation`, `deactivate_negative_ev_strategies` and five
other phrasings, filed 2026-07-31..2026-08-08 across 8 categories). The capability shipped
2026-08-08 in commit `9d34158`; the tickets were never closed because no closure path existed until
then. **13 survived.** What they are, and what the first run concluded:

| id(s) | category | ask | verdict |
|---|---|---|---|
| 25, 27, 28 | `data_collection` + `external_data_pipeline` | Settled KXCPI corpus + official CPI actuals as a `run_backtest` dataset | **PARTIALLY SHIPPED, now closing.** The corpus landed 2026-08-13 as `run_backtest dataset='econ'` (111 econ markets, 18,710 candles); the official CPI **actuals** they also asked for are not collected, so a spec still cannot gate on the released number. Registry entry added 2026-08-14 — its note names both halves on purpose, so the fleet re-asks for the missing one. Note this is **three** tickets, not the four §3 originally claimed: the fourth `data_collection` row was `weather_market_ticker_registry`. |
| 21, 22, 30 | `other`, `bug_report` | "Deactivate strategies 49/50" / "[46,36,30,32,33] not yet deactivated" | **ALREADY SHIPPED**, but *not* registry-fixable: `_shipped_match()` reads `capability` only, and these carry `deactivation` / `strategy_management` / `shared_code_capability` there with the real ask in `problem`. Needs an operator decision, not a matcher change — widening the matcher to cover them would sweep up genuine near-misses. |
| 9, 11 | `bug_report` | "8 active strategies show ZERO paper_trades across many heartbeats" | Filed 2026-08-01 by Havel. Reads as PAP-4 bait from the capability string ("live order placement"), but the `problem` text is a **fill-engine bug report**, not a request for real money. The fill engine was fixed after these were filed (agents reference the fix from 2026-08-05 on, and backtest 1855 ran 4,339 trades), so they are most likely stale. Verify, then close as fixed. |
| 29 | `sandbox_operator` | `sandbox_runs` budget exhausted (50/50) | Genuinely pending — a quota bump, not a build. Blackwood wants to retest pre-fill-engine-fix conclusions it says are now invalid. |
| 7 | `research_tooling` | `view_strategy_spec` | Genuinely pending — small, probably worth doing. An agent cannot read back its own strategy's filters/entry conditions, so it cannot diagnose a book that silently trades nothing. |
| 3, 4, 5 | `infrastructure`, `data_collection`, `api_credentials` | `data_pipeline_diagnostics`, `weather_market_ticker_registry`, `live_quote_ticker_schema` | All three are Ekstrom, 2026-07-21/22, all weather-cohort artifacts ("no live quote for KXHIGH", "no price configured"). Low priority — weather research is closed (§4). |

**Queue liveness matters as much as queue content.** Nothing has been filed since 2026-08-06, but
the fleet is alive (1,119 heartbeats in the 7 days to 2026-08-14, 6 active agents) — so the silence
is an unblocked fleet, not a stalled worker. The routine checks both, because those two look
identical in the queue and call for opposite responses.

---

## 4. Standing context the routine should respect

- **Weather is done.** Every weather book was retired 2026-08-12, and two independent
  divergence-from-market signals were killed 2026-08-13 (our NWS/ensemble forecast, and
  Polymarket — `docs/PMDIV_THESIS.md`). The generalizable finding, in `RESEARCH_JOURNAL.md`: on
  Kalshi weather the market is the best forecaster we have access to, so "X disagrees with the
  market" ideas in this domain are anti-signals. Don't spend build budget on weather tickets
  without a mechanism argument.
- **PAP-4: paper only.** No agent gets real order placement, regardless of supporter count.
- **North star: $100/month realized.** Prefer tickets that unblock an agent's *edge* hypothesis
  over ones that add comfort, dashboards or permissions.
- **The ops DB role is SELECT-only.** Tickets cannot be closed with a query; closure is worker
  code (`tickets.resolve_ticket`). Plan builds accordingly.

---

## 5. Known gaps this routine will probably surface

Two things agents can't currently do that nobody has ticketed, worth building if they come up:

- **`inspect_data` source `polymarket` omits `low_f`/`high_f`**, so an agent reading it gets a
  probability with no idea which temperature bucket it belongs to. One-line fix in
  `kalshi_bot/evo/data_access.py`.
- **No weather-forecast sources are exposed at all** — `weather_ensembles`, `weather_forecasts`,
  `weather_settlements`, `weather_forecast_outcomes` are absent from the `inspect_data` allowlist,
  so agents cannot reach the forecast data to reach their own conclusions about it.

Both are cheap. Neither is urgent given §4's weather finding, but if an agent asks, the answer is
"yes, and it's an hour of work".
