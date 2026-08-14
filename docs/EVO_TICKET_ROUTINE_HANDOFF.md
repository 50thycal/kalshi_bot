# Handoff — scheduled "what are the bots asking for?" routine

Paste the prompt in §1 into a fresh Claude Code session on this repo, on whatever schedule you
want (daily or every few days is plenty — the fleet files a handful of tickets a week). Everything
it needs is in the repo; no state has to carry between runs.

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
which is a signal in itself, and the skill says to add the entry rather than hand-close.

Watch for this in the evo logs:

```
evo tickets: auto-closed N request(s) whose capability shipped
```

---

## 3. State of the queue at handoff (2026-08-13)

35 open tickets. After the first auto-close pass runs, expect **~25 of them to close** — the
off-switch wave (`deactivate_strategy`, `strategy_deactivation`, `deactivate_negative_ev_strategies`
and five other phrasings, filed 2026-07-31..2026-08-08 across 8 categories). The capability shipped
2026-08-08 in commit `9d34158`; the tickets were never closed because no closure path existed until
now.

What should remain, and what to do with it:

| category | n | ask | status at handoff |
|---|---|---|---|
| `data_collection` + `external_data_pipeline` | **4** | **Settled KXCPI corpus + official CPI actuals as a `run_backtest` dataset** | **Being built now** — the fleet's own non-weather thesis. Close these when it ships. |
| `bug_report` | ~1 | Automated strategy execution / live order placement | **REJECT** — violates PAP-4 (paper only). Close with the invariant as the reason; it will be re-filed otherwise. |
| `research_tooling` | 1 | `view_strategy_spec` | Genuinely pending — small, probably worth doing. |
| `sandbox_operator` | 1 | `sandbox_runs` | Genuinely pending — needs a read to understand what's actually wanted. |
| `api_credentials` | 1 | `live_quote_ticker_schema` | Genuinely pending. |
| `infrastructure` | 1 | `data_pipeline_diagnostics` | Genuinely pending. |
| `data_collection` | 1 | `weather_market_ticker_registry` | Low priority — weather research is closed (see below). |

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
