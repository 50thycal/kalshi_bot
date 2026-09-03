# WS-014 — Evo fleet health: the dead peer-visibility path, and the evo→XOS bridge

**Phase:** DECIDE
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

- **D1.** Is the listener path broken or merely never elected? `evo_listeners` and
  `evo_listener_events` are empty over the full life of the system, across 30 founders and
  every cohort since, and the `triggered_heartbeats` budget shows zero utilisation. Zero in
  six weeks across that many agents reads as a closed code path, but that has not been
  proven — the action-validation path in `cognition.py` has not been read. Resolving this is
  the first task for whoever picks up the listener half.
- **D2.** What should `visible_after` mean? Two readings, and they behave differently under
  an hourly recompute: (a) *peers see a snapshot 6h stale* — retain a delayed copy and serve
  the most recent row whose value is at least 6h old; (b) *a cohort's standings become
  visible 6h after scoring first starts* — set `visible_after` once, on insert, and never
  re-arm it. (b) is a two-line fix; (a) is the stated intent in `fitness.py`'s docstring
  ("the 6-hour-delayed peer-visible copy") and needs a second row or a history table.
  Recommend (b) now and (a) only if the staleness guarantee turns out to matter.
- **D3.** Does the daily `material_revisions_day` cap want raising? It is the one budget
  pinned at 100% every day while every other resource sits idle — but the peer-visibility
  fix changes what agents are revising *toward*, so measuring after that lands is the
  cheaper order.

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

Not ready. D1 and D2 gate it.

## Implementation State

None. The check-in that produced this was read-only and changed no evo config, budget,
agent state or ticket.

## Review State

Not started.

## Related Decisions

`DEC-010`.

## Related PRs

This PR.

## Next Step

Evo Ticket Workshop takes D2 and ships the `fitness.py:544-546` fix, correcting the
one-sided assertion at `scripts/evo_selftest.py:332` in the same change.
