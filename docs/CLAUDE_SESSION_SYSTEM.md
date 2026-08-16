# Claude Session System — implementation, parity review, retirement record

Implements `docs/CLAUDE_SESSION_SYSTEM_SPEC.md` on top of the **live** Experiment
OS (production cutover to `NEW_ONLY` on 2026-08-16). The spec was written before
PR 1–5 existed; where production reality gave a cleaner mechanism, this document
records what changed and why.

## 1. Routing mechanism

| Layer | File | Why |
|---|---|---|
| Behavioural rule | `CLAUDE.md` | Auto-loaded into every session — the only place guaranteed to be read |
| Visible menu | `.claude/settings.json` → `SessionStart` hook printing `.claude/sessions/ROUTER.txt` | Puts the menu in front of the session even if `CLAUDE.md` is skimmed; deterministic (a `cat`, no logic) |
| Playbooks | `.claude/sessions/<role>.md` | Loaded on demand once a role is chosen, so global context stays small |

**Roles are deliberately NOT skills.** A skill auto-triggers on description
match, so a session asking about tickets could silently *become* the Evo Ticket
Workshop — which §16.8 of the spec explicitly warns against. Role selection must
be an explicit act, so it lives in instructions and a menu, not in a matcher.

Sticky by construction: the rule says "ask once, then follow"; nothing re-asks.

## 2. What Experiment OS made obsolete

The spec assumed the Control Tower would need to *reconstruct* status. It does
not — PR 1–5 already own lifecycle, evidence, gates, platform state and lineage.
So the Control Tower is a **renderer over canonical reads**, not a status engine.
`kalshi_bot/experiment_os/control_tower.py` calls `read`, `evaluator` (DRY-RUN,
`persist=False`), `enforcement` and `platform_impact`. It re-derives nothing.

The one genuinely new thing it adds is **infrastructure health Experiment OS does
not model**: data-collector freshness. A stalled collector starves an
experiment's evidence and fails no gate, so no amount of Experiment OS state
would surface it.

### How a session reads production

`{"type":"xos","command":"control-tower","id":"<slug>"}` on the ops channel runs
the **canonical CLI itself** against `DATABASE_URL_RO` (the workflow installs the
full dependency set only for this request type). The alternative — a
psycopg-only SQL report — would have been a second implementation of "what does
this gate say", which is exactly the drift the cutover removed.

## 3. Parity review vs the retired checkers

Every unique behaviour of the superseded workflows, and where it now lives.

| Legacy behaviour | Source | Disposition |
|---|---|---|
| Per-book paper P&L / n / open | `kalshi_Loop_checker`, `phase_3`, `full-update` | **Canonical** — Control Tower, per lifecycle state, from Experiment OS metrics |
| Data-collector freshness (3× cadence) | `phase_3` step 1/3 | **Incorporated** — Control Tower `DATA COLLECTORS`; a stall (hours) is now distinguished from a collector simply not in use (months → `INACTIVE`) |
| **UNTRACKED book reconciliation** | `phase_3` step 3a | **Obsolete by construction.** Under `NEW_ONLY` an unregistered tag cannot write a row at all. The check existed because a book could appear from a parallel session with no rationale; that is now refused at the write path, not detected afterwards |
| Active-experiment gate sweep, distance-to-gate | `phase_3` step 3b | **Canonical** — gates are executable; the Tower reports the evaluator's verdict and the registered floors |
| Live P&L vs paper shadow (adverse selection) | `phase_3` step 1b | **Retained as a specialist diagnostic** (`mmsell_live`, `live_paper_parity`). The Tower shows the twin link, boundary match and live exposure, and points at these for realized live economics. *Not* folded in: they compute fill-level economics no generic metric owns |
| North-star $/month standing | `full-update` step 5 | **Incorporated** — Control Tower `PORTFOLIO`, labelled PAPER, with the fill-model caveat stated |
| Realizable ¢/trade vs blended paper | `mm_check_1` | **Retained** — `mmsell_fill_model`; gate-relevant analysis, not status |
| Exit-rule study | `mm_check_1` | **Retained** — `mmsell_exit_study` |
| Percentile-tail instability at n<20; correlated books never summed; twins are controls not variants | `mm_check_1` | **Preserved as interpretation rules** in the Control Tower playbook — they are how to read numbers, so they belong with the reader |
| Cohort boundary (Tmmsell restart, pre-boundary trades dropped) | `mm_check_1` | **Structural** — epochs. The evaluator refuses to pool across a boundary; no human rule needed |
| Settled-history capture health, regime supply | `mmsell-seasonal-check` | **Retained** — `mmsell_history_status`, `mmsell_supply_forecast`, `mmsell_regime_backtest`; owned by Live Ops (collector health) and Research Lab (supply), not a standing session |
| FREEZE market-listing trigger | `phase_3` step 3b | **Retained** — `kalshi_freeze_listing_check`. Noted gap below |
| Central-Time reporting | all checkers | **Incorporated** — the Tower renders `AS OF` in America/Chicago |
| Carried-over suggestion list on a status branch | all checkers | **Deliberately dropped.** Durable state belongs in Experiment OS, a PR, or a research doc — not a second interpretation persisted on a branch (spec §14.3) |

### Known gaps, stated rather than hidden

1. **External-world triggers have no home in Experiment OS.** The FREEZE listing
   check ("has Kalshi listed enough grain/soft markets to make the thesis
   testable?") is not evidence about an experiment; it is a precondition for one.
   Experiment OS gates evaluate evidence, so this stays a script the Research Lab
   runs. A first-class "watcher" concept would be the clean fix — deliberately
   not invented here.
2. **"Blocked on another experiment's gate"** is not a first-class dependency.
   The idea queue tracked it in prose. An `IDEA`-state experiment can record it
   in its notes today; a real dependency edge is future work.
3. **Live realized P&L is not in the Tower's own numbers** — exposure and the
   twin link are, realized economics come from the specialist scripts. Folding
   settlement-level live P&L into generic metrics is a metrics-layer change, not
   a reporting one.

## 4. Retirement

Removed from the active skill surface (Git history is the reference; §25 of the
spec: a `DEPRECATED` heading is not enough when the skill can still auto-trigger):

- `full-update` → Control Tower + `PORTFOLIO`
- `kalshi_Loop_checker` → Control Tower
- `kalshi_loop_checker_phase_3` → Control Tower + Live Ops
- `mm_check_1` → Control Tower + the retained mmsell analysis scripts
- `mmsell-seasonal-check` → Live Ops (capture health) + Research Lab (supply)

Retained and pointed at Experiment OS: `kalshi-idea-model`,
`kalshi-probe-builder`, `kalshi-strategy` (research), `evo-ticket-triage` (the
Ticket Workshop's procedure), `live-paper-parallel` (live-canary diagnostics
beyond what `arm_live_canary` already enforces), `bot-readable-strategy`
(transitional until evo reads Experiment OS directly).

**No analysis script was deleted.** All 87 allowlisted ops scripts remain; only
the session-facing *instruction surface* shrank.

### Status branches

`strategy-loop-status` and `mmsell-check-status` have no writer left once the
checkers are gone. They are left in place but **inert** — no active instruction
references them, and nothing reads them for lifecycle truth. `digest-archive`
remains a historical operational archive; `ops` is infrastructure, not status.

## 5. Guard

`tests/test_session_system.py` fails if the retired skills reappear, if a role
playbook goes missing or loses its required sections, if `CLAUDE.md` stops
routing, or if an active instruction file starts advertising a retired workflow
as current.
