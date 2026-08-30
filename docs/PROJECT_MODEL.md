# Project Model — kalshi_bot

How this system works **today**. Present tense, architectural, durable.

**Deliberately absent:** current experiment standings, gate reads, book P&L, which
strategies are running this week. Those live in Experiment OS and change daily; a copy
here would be wrong within a day and believed anyway. Ask Experiment OS
(`docs/OPS_RUNBOOK.md` → `xos control-tower`) for state.

**Last updated:** 2026-08-24 · **Build OS v0.4**

---

## Purpose

An automated trading system for [Kalshi](https://kalshi.com) event contracts, owned and
operated by one person. It scans the exchange, runs strategy "books" against the market
tape, records paper trades, and — under explicit operator arming — places real orders.

Its north star is **$100/month realized profit**. Research that proves a book is −EV
counts: the system is built to retire ideas as readily as it adopts them, which is why so
much of it is machinery for deciding whether an edge is real.

## Major components

| Component | Responsibility |
|---|---|
| `kalshi_bot/main.py` | Worker entrypoint and cycle loop; boot hooks; per-book dispatch |
| `kalshi_bot/config.py` | Fail-closed settings; parses book/variant specs; validates them before anything runs |
| `kalshi_bot/kalshi/` | RSA-PSS request signing and the authenticated REST client |
| `kalshi_bot/scanner/` | Market/order-book fetch, liquidity metrics, deterministic scoring |
| `kalshi_bot/risk/manager.py` | Fail-closed risk gate in front of any order |
| `kalshi_bot/paper/` | Paper execution engine and strategy adapters |
| `kalshi_bot/live/` | Real-money executor, exit rules, sizing, queue-position modelling |
| `kalshi_bot/twin/` | Paper twin harness — a live book's shadow, started at the same instant |
| Strategy trackers | `mmsell/`, `theta/`, `freeze/`, `pin15/`, `tfav/`, `wcprop/`, `xgame/`, `weather/` — one package per book family |
| Instruments (not books) | `perps/` — the PERP-V1 read-only perpetual-futures tape. Writes its own tables, carries no strategy tag, places nothing. Runs in the every-mode cycle hook so its coverage does not depend on `BOT_MODE` |
| `kalshi_bot/mmsell/market_types.py` | The settlement-mode / market-type taxonomy `mode=`/`mtype=` books select on |
| `kalshi_bot/experiment_os/` | Experiment OS: lifecycle, versions, epochs, deployments, gates, evaluator, enforcement, platform impact, issues |
| `kalshi_bot/evo/` | The evolutionary agent fleet — LLM agents that propose and run their own books under budget |
| `kalshi_bot/evo/search/` | Evo historical search — a capability the agents above invoke (`search_strategy_space`): replay a strategy and a bounded neighbourhood of variants, return evidence. Own namespace (`evo_search_*`), no lifecycle of its own (`DEC-003`); replay only, no live path |
| `kalshi_bot/obs/` | Observability: the per-cycle evidence funnel every series-addressed book emits |
| `kalshi_bot/livedash/`, `dashboard/` | Read-only operator dashboards |
| `scripts/` | Self-contained read-only analyses, run through the ops channel |
| `alembic/` | Schema migrations; the worker runs `alembic upgrade head` on boot |

## System boundaries

**Authoritative for:** its own experiment lifecycle and evidence, its paper trade history,
its risk envelope, and the order intents it submits.

**Mirrors:** Kalshi market, order-book and settlement data; weather forecasts and
observations; external price feeds. All are snapshots of someone else's truth, stored so
research is reproducible.

**Calls but does not own:** the Kalshi exchange itself, Railway, and the LLM APIs the evo
fleet uses.

## Runtime shape

Two Railway workers share one Postgres database:

| Worker | `BOT_MODE` | Responsibility |
|---|---|---|
| main | `live` | The scan/trade cycle, all operator books, Experiment OS boot hooks and gate evaluation |
| evo | `evo` | The evolutionary agent fleet |

**The worker is the only writer.** Every other path into production data is read-only by
construction.

## Important data flows

### Scan → candidate → paper trade

```text
Kalshi REST ──► scanner ──► per-book eligibility ──► candidate ──► paper engine
     │              │             │                       │             │
     └─ snapshots   └─ metrics    └─ mode=/mtype=/         └─ candidate  └─ paper_trades
        persisted      + score       only=/skip= filters      ticks         + positions
```

A book's *universe* is decided by its variant spec. Series-substring filters (`only=`,
`skip=`) match raw tickers; structural filters (`mtype=`, `mode=`, `xmtype=`) go through
the taxonomy in `mmsell/market_types.py`. **A series with no taxonomy entry classifies as
`unclassified`/`unknown` and is admitted by no allowlist filter** — an unknown contract is
never swept into a book that did not ask for it.

### Live execution, and its paper twin

```text
candidate ──► risk manager ──► live executor ──► Kalshi order
                  │                                    │
                  └── refuses on any doubt             └── fills / positions persisted
                                                             │
twin harness ─────────────────────────────────────────────────┘
   a FRESH paper book, started at the same instant, same knobs,
   differing only in the fill assumption
```

The twin exists because paper assumes a resting maker order always fills and live does
not. Measuring the gap is the only way to know whether a paper edge is real.

### Evidence funnel

Every series-addressed book ends its cycle with a bounded, publishable funnel line naming
the stage at which its count first became zero (`kalshi_bot/obs/funnel.py`). It exists so
"this book saw no markets" can be distinguished from "this book was never asked" without
reading raw logs.

### The ops channel

The operator's agent sandbox cannot reach Railway or Postgres. Work is driven by pushing a
request to the `ops` branch, which fires a GitHub Actions runner that executes
**default-branch code** against a **read-only** database connection and commits the result
back. Full mechanism: `docs/OPS_RUNBOOK.md`.

## Experiment lifecycle

The central domain object is the **experiment**, and Experiment OS owns its whole life:

```text
IDEA → PROBE → PAPER → LIVE_CANARY → PRODUCTION
                  └──────► PAUSED / RETIRED
```

- An **experiment** asks one question. A **Version** freezes the scientific contract; a
  changed question is a new Version, never an edited one.
- An **epoch** is a changed world under the same question — a taxonomy expansion, a
  candidate-population change. Evidence never pools across an epoch boundary.
- A **deployment** binds a Version's arm to a strategy tag that may actually trade.
- A **gate** is pre-registered; only a **recorded evaluator verdict** authorizes a
  transition. A good P&L number with a failing or unevaluated gate is not a promotion.
- A **Platform Revision** records a change to shared semantics (fees, fills, taxonomy,
  execution, risk, provenance, metric definitions) and forces an impact disposition for
  every affected active experiment before it activates.
- An **issue** is durable state for a problem. It routes work to an existing role; it never
  changes a lifecycle state or a verdict as a side effect.

Enforcement is `NEW_ONLY`: new experimental activity must originate in Experiment OS, and
a tag not registered to an active deployment arm is refused at the write path. Existing
books are grandfathered but may not silently evolve outside the system.

## Session roles

Agent sessions adopt one **role** (`.claude/sessions/`) that bounds what they may write —
Experiment Control Tower, Evo Control Tower, Evo Ticket Workshop, Platform Change Review,
Research Lab, Legacy Migration, Live Ops, or task-specific. The role is chosen once and is
sticky. A read-only role that discovers a needed write recommends the owning role rather
than quietly becoming a write session.

This is a real architectural constraint, not documentation: it is why a session that finds
a platform defect produces a reviewed PR and an issue rather than an edit to production
state.

## External integrations

| System | Used for | Behavior when unavailable |
|---|---|---|
| Kalshi REST | Markets, order books, orders, fills, settlements | Fail closed — the cycle records a fetch failure rather than inferring an empty universe |
| Postgres (Railway) | All durable state | Worker exits; nothing trade-like proceeds |
| Weather providers (NWS/NBM and friends) | Forecasts and observations for the weather books | Those books skip; others are unaffected |
| LLM APIs | The evo fleet's cognition | Fleet pauses under its own budget/ceiling rules |
| GitHub Actions | The ops channel and CI | Analyses are unavailable; production is unaffected |

## Important invariants

- **The worker is the only writer to production data.** The ops channel is read-only
  against Postgres by design (a SELECT-only role, enforced server-side), and no writable
  path may be added to it. A production write reaches the worker as a strictly validated
  envelope in an allowlisted environment variable, executed once at boot against a durable
  receipt: three disjoint transports — issues, platform revisions, and experiment lifecycle
  (`DEC-005`) — never one shared vocabulary.
- **Fail closed.** Bad config, bad auth, or a bad database means the worker does nothing
  trade-like rather than proceeding on defaults.
- **An unclassified series is admitted by no allowlist filter.** Classification debt shows
  up as exclusion, never as a silent default into an eligible bucket.
- **A live canary arms only through `service.arm_live_canary`** — fresh tags with no
  inherited paper state, a twin at the same instant, a pre-registered risk envelope, and
  live/twin tag maps equal to the Version's declared arm set. The last two live on a frozen
  Version, so narrowing either is a **successor Version**, never an edit (`DEC-004`).
- **Only a recorded evaluator PASS authorizes a transition.** A hand-written or stale PASS
  never does, and a dry run authorizes nothing.
- **Evidence never pools across an epoch boundary Experiment OS declares non-poolable**,
  and a pre-registered gate is never re-interpreted after results are seen.
- **`SERIES_TYPES` is duplicated in `kalshi_bot/mmsell/market_types.py` and
  `scripts/mmsell_market_types.py` and the two copies must stay byte-identical** — the ops
  runner cannot import the worker package, so the table is copied rather than shared, and a
  test asserts they have not drifted.
- **Actions that expand real-money exposure require explicit operator confirmation.**
  Reducing exposure follows existing kill-switch semantics.
- **Chat is never durable state.**

## Important persistence

Roughly three families of table, plus Experiment OS's own schema:

- **Market record** — `markets`, `market_snapshots`, `orderbook_snapshots`, `signals`, and
  the per-book candidate/position tick tables that make replays reproducible.
- **Trading record** — `paper_trades`, `paper_positions`, `live_orders`, `fills`,
  `positions`, `account_snapshots`, and the live/paper twin and parity tables.
- **Research inputs** — weather forecasts/observations/ensembles, crypto spot and ladder
  snapshots, cross-venue snapshots, and the backfill tables behind them.
- **Experiment OS** — experiments, versions, arms, epochs, deployments, gates, gate
  results, platform components/revisions/snapshots, impact actions, integrity events,
  issues and their append-only event history.

Schema changes go through Alembic and must leave a single head.

## Current major architectural constraints

- **The agent sandbox cannot reach Railway, Postgres, or most third-party APIs.** Every
  production read goes through the ops channel, which runs default-branch code — so a
  change on a feature branch cannot be exercised against production until it merges. Plan
  measurement around that, or recompute from a read-only query.
- **`SERIES_TYPES` is duplicated on purpose** (above). Ops scripts must stay stdlib +
  psycopg only.
- **The ops channel is a single-slot transport.** One request file, one issue-command
  environment variable; concurrent sessions can overwrite each other's request, so use a
  unique `id` and read back your own result file.
- **Experiment OS writes require a writable `DATABASE_URL`**, which only the worker has.
  Agent sessions can read everything and write only through the narrow, audited
  issue-command envelope — so registering a Version, epoch or Platform Revision is
  necessarily an operator action.
- **Real money is in play.** The safety machinery — kill switch, exposure caps, fail-closed
  risk manager, arming ritual — is load-bearing and is never weakened to make a task
  easier.

---

*Update this file in the same PR as any change that materially alters architecture,
important flows, invariants, or system responsibilities. Do not add current standings.*
