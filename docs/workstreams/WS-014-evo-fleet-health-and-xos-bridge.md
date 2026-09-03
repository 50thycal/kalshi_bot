# WS-014 — Evo fleet health: the dead peer-visibility path, and the evo→XOS bridge

**Phase:** REVIEW
**Status:** Active
**Created:** 2026-09-03
**Updated:** 2026-09-03

## Goal

Restore the two evolutionary mechanisms that are structurally inert in the running fleet
(peer visibility → influence, and listeners), and settle how an agent-concluded
`evo_experiment` becomes a registered Experiment OS experiment.

## Context

An Evo Control Tower check-in on 2026-09-02 — the first in some weeks — found the fleet
busy but not *evolving*. Heartbeats, fills, sandbox runs and cohort finalization all work.
Two of the mechanisms that distinguish this from six independent LLM traders do not, and
one of them is a confirmed code defect rather than agent behaviour. Separately, the half of
spec §22.7 that would let the fleet's research reach the operator has never been built, so
no evo finding can be promoted no matter how good it is.

The check-in was read-only; nothing in the fleet was changed. This workstream is where the
findings and the operator's rulings become resumable.

## Current Mental Model

```text
  interim fitness                                        peer visibility
  ───────────────                                        ───────────────
  orchestrator.py:324   every 3600s ──> evaluate_cohort(kind="interim")
                                             │
  fitness.py:544-546                         └─> UPSERT sets
                                                 visible_after = now + 6h   ← re-armed
                                                                              EVERY hour
                                             ┌───────────────────────────────────┐
  peers.delayed_leaderboard()  filters       │ visible_after <= now  →  never true│
                                             └───────────────────────────────────┘
                                                        │
  cognition.py:1459  delayed_leaderboard=[] ────────────┘
  cognition.py:623   `if ctx.delayed_leaderboard:` ──> peer block NEVER enters the prompt
                                                        │
                                                        ▼
                                          agents cannot see peers
                                                        │
                                                        ▼
                                    evo_influences = 0 rows, all time
                                    (the influence graph is unreachable,
                                     not merely unused)

  NOT affected — selection is sound:
  evolution.py:427  evaluate_cohort(kind="final") consumes its own return value,
                    never the delayed view. Retirement + reproduction rank correctly.

  FIXED this PR: visible_after set once at insert, never re-armed (D2).

  listeners: 175 create_listener attempts, 0 evo_listeners rows — 100% rejection
  ─────────────────────────────────────────────────────────────────────────────
  cognition.ACTION_PROTOCOL documented {name, condition, purpose, effect, ...}
  but never the shape of `condition` itself, nor listeners.py's metric vocabulary
  (MARKET_METRICS / SCALAR_METRICS / delta: / status_is: / result_is: / new_market:)
  — agents guessed blind, every attempt rejected with "condition object required"
  or "condition needs a non-empty 'all' or 'any' list". Dispatch + validator were
  already correct (D1). FIXED this PR: the shape, vocabulary and a worked example
  are now in the prompt.

  the bridge that does not exist
  ──────────────────────────────
  evo_experiments (470 rows, 448 concluded)   ──X──>   experiments (origin='evo': 0)
      free-text promotion_criteria / kill_criteria          needs a registered gate spec,
                                                            an arm, a control, an epoch
  grep for any Experiment OS symbol in kalshi_bot/evo/ = zero hits.
  Coupling is one-directional: XOS mentions evo; evo does not import XOS.
```

## Decisions Made

- **The evo→XOS bridge is a manual Research Lab act** (operator, 2026-09-03). The operator
  reads the fleet's strategies, decides which is worth pursuing, and Research Lab registers
  it by hand. No auto-proposal path, not even into `IDEA` stage. See `DEC-010`.
- **Fleet size stays at 6** (operator, 2026-09-03). `EVO_MAX_ACTIVE_AGENTS=6` on the evo
  service is deliberate and stays while the system is still being tested. Scaling the
  population is not a lever this workstream may reach for; the cheapness of the fleet
  (LLM budget utilisation is low, see the digest) is not an argument to grow it.
- **Selection is not implicated.** Verified against `evolution.py:427` — the `kind="final"`
  path is independent of the broken delayed view, so no past retirement or reproduction
  decision needs to be revisited.

## Open Decisions

- **D1. RESOLVED — broken, not never-elected.** Verified against production:
  `evo_heartbeats.actions_json` shows **175 `create_listener` attempts** across the fleet's
  life, **0 successes**, 100% rejection — `"condition object required"` or `"condition needs
  a non-empty 'all' or 'any' list"` on every one. Root cause: `cognition.ACTION_PROTOCOL`
  documented the top-level `create_listener` field list ({name, condition, purpose, effect,
  ...}) but never told the agent what shape `condition` itself must be, nor the metric
  vocabulary `listeners.validate_condition` actually accepts (`MARKET_METRICS`,
  `SCALAR_METRICS`, the `delta:`/`status_is:`/`result_is:`/`new_market:` clause forms) — that
  vocabulary lived only in `listeners.py`'s module docstring, never surfaced to the LLM.
  Agents were guessing the shape blind and guessing wrong, every time. Dispatch
  (`cognition.py:_execute_one`) and the validator were both already correct; nothing there
  needed to change. Fixed by documenting the `condition` shape, the full vocabulary, and a
  worked example directly in `ACTION_PROTOCOL` — see Implementation State.
- **D2. RESOLVED — visible 6h after first scoring, never re-armed** (operator, 2026-09-03).
  `visible_after` is now set once, at a fitness row's first insert, and held across every
  later interim recompute. The alternative (a rolling delayed snapshot, always 6h-stale)
  was the literal reading of the old docstring but needs a second row/history table for no
  argued benefit — dropped.
- **D3.** Still open. Does the daily `material_revisions_day` cap want raising? It is the one
  budget pinned at 100% every day while every other resource sits idle — but D1/D2 change
  what agents have to revise *toward* (peers are now visible; listeners now actually work),
  so measuring after this PR is live is the cheaper order than guessing now.

## Assumptions

- The 6h delay exists to stop agents from reacting to their own just-written score and to
  each other in the same cycle — i.e. it is an anti-herding device, not a privacy control.
  If that is wrong, D2's recommendation changes.
- The 402-class heartbeat failures of 2026-08-27→29 were provider credit exhaustion and are
  fully resolved; the residual degraded rate is model JSON-parse noise at a tolerable level.
  This assumption is worth re-checking at the next check-in rather than trusted.
- Agent behaviour has been shaped by six weeks of never seeing a peer. Post-fix fleet
  results are not comparable to pre-fix ones and should not be pooled.

## Non-Goals

- Growing the fleet. Settled above.
- Bringing evo *trades* under Experiment OS enforcement. `importer.py:371` and spec §22.7
  are explicit that evo trades live in `evo_*` tables under evo lineage, and NEW_ONLY does
  not refuse them because they never touch the `paper_trades` write path. That stays.
- Re-scoring, re-ranking or reversing any historical cohort outcome.
- Anything touching real-money exposure. Nothing in this workstream reaches a live book.

## Build Card

Not ready — this shipped as a direct fix + Evo Ticket Workshop triage pass rather than
through a Build Card, given the scope (two bounded code defects, one prompt-documentation
gap, no schema change, no new invariant).

## Implementation State

**Shipped, this PR:**

- `kalshi_bot/evo/fitness.py:544` — `visible_after` no longer re-armed on interim recompute
  (D2). Regression test: `tests/test_evo_evolution.py::test_interim_recompute_does_not_rearm_visible_after`.
- `scripts/evo_selftest.py::c_leaderboard_delay` — added a reachability check
  (`visible_after` bounded relative to `created_at`, not just `computed_at`) so the
  self-test can catch a future re-arm regression; the old check (delay not too *short*)
  is kept alongside it.
- `kalshi_bot/evo/cognition.py::ACTION_PROTOCOL` — `create_listener` now documents the
  `condition` object shape (`all`/`any`, clause form, full metric vocabulary, a worked
  example) (D1). Regression: `tests/test_evo_listener_prompt_contract.py` (four tests:
  every vocabulary term is documented, the wrapper is documented, the worked example
  actually validates, the no-op/no-value clause forms validate).
- `kalshi_bot/evo/data_access.py` — two new `inspect_data` sources, `strategies` and
  `sandbox_runs`, reading back `EvoStrategy.spec_json` / `EvoSandboxRun.params_json` +
  `result_json` by `id` (or scoped to `agent_uuid`). Closes tickets **#39**
  (`backtest_spec_inspection`) and **#7** (`view_strategy_spec`) — an agent could not read
  back its own strategy's config or a past backtest's params, which blocked diagnosing a
  book that silently traded nothing. `_cap_cell` gained a bounded JSON-cell case
  (`_JSON_CELL_CAP`) so a spec isn't chopped mid-structure at the existing 200-char string
  cap but a runaway blob still can't blow a row's token budget.
- `kalshi_bot/evo/cognition.py::MARKET_MECHANICS` — capability-map primer updated to name
  the two new sources (kept under the enforced ~1400-token primer budget by trimming the
  addition, not by cutting an existing entry).
- `kalshi_bot/evo/tickets.py::SHIPPED_CAPABILITIES` — registry entry for the `strategies`/
  `sandbox_runs` shipment, anchored on the `spec`/`specification` token (verified against
  the live queue: no other open ticket's `capability` field carries it, so it cannot sweep
  up `strategy_execution`/`strategy_management`/`shared_code_capability`). Auto-closes #39
  and #7 on the next orchestrator cycle after deploy. Regression:
  `tests/test_evo_ticket_resolution.py` (matches both real phrasings, spares all five
  near-miss tickets currently in the queue; `test_a_live_request_is_left_alone` re-pointed
  at #41/#40, the requests still genuinely open as of 2026-09-03).

**Ticket triage this session** (`evo-ticket-triage` skill; 14 open tickets read):

| id(s) | verdict | evidence |
|---|---|---|
| 39, 7 | shipped, this PR | see above |
| 21, 22, 30 | verified resolved in substance; **not closeable this session** | strategies 46/36/30/32/33/49/50 all confirmed `inactive` in production. `_shipped_match()` reads only the ticket's `capability` field, and these carry `deactivation`/`strategy_management`/`shared_code_capability` there — the real ask is in `problem`. Widening the `deactivate_strategy` registry entry's tokens to catch them would sweep up the genuine near-misses (`strategy_execution` bug reports) it exists to protect. The ops DB channel is SELECT-only, and no one-off ticket-resolution write transport exists (unlike Experiment OS's `EXPERIMENT_OS_ISSUE_COMMAND`) — closing these needs either that transport built, or a human review confirming the resolution off-queue. Flagged, not built: out of scope for this PR. |
| 11, 9 | verified stale; same closure gap as above | Both bug reports are about strategies 24/35/38/39/41/43/44/45 showing zero paper_trades. Production `evo_orders` now shows filled orders for 24/38/39/41/45; all three owning agents (BLA-G2-042, EKS-G1-004, HAV-G1-006) are long retired. The fill-engine issue these referenced was fixed before these tickets were even filed (2026-08-01), per the fleet's own later tickets referencing "fill engine fix" from 2026-08-05. |
| 5, 4, 3 | stale, low priority | KXHIGH series-vs-ticker confusion and a "no price configured" error, all from cohort #1's Ekstrom line (2026-07-21/22). `explore_markets` has existed and been used successfully fleet-wide for weeks; the filing agent (EKS-G1-004) retired 2026-08-23. Not worth spending this session's build budget on. |
| 29 | stale/moot | `sandbox_runs` budget exhaustion (50/50) filed 2026-08-05 against a since-elapsed weekly budget cycle. |
| 38 | reject — capability already exists | Weather near-close mispricing research ask; `explore_markets` + `run_backtest dataset='backfill_weather'` already cover it. A research task, not a capability gap. |
| 41, 40 | genuinely pending | #41 (event-timestamp-gated sandbox backtesting, filed 2026-09-03 — same day as this check-in) needs a new sandbox-spec dimension; real design work, not scoped here. #40 (commodities-hub data corpus with exchange-status timestamps) needs new collection infrastructure. Both left open. |

## Review State

Not started.

## Related Decisions

`DEC-010`.

## Related PRs

This PR.

## Next Step

Merge, then watch the next evo orchestrator cycle for `evo tickets: auto-closed N request(s)
whose capability shipped` (expect N≥2 for #39/#7) and confirm `evo_listeners` gains its first
row. D3 (material-revision budget) is worth a look once a few days of post-fix data exist.
The 21/22/30 and 9/11 closure gap is a standing housekeeping item for whichever role next
touches the ticket-resolution transport — not urgent, since the underlying capability is
genuinely delivered either way.
