# Evo historical search — design record

Status: **implemented (historical replay only)**. No live path, no prospective paper
cohorts, no Experiment OS promotion.
Package: `kalshi_bot/evo/search/`. Workstream: [WS-006](workstreams/WS-006-evo-search-capability.md).
Decisions: `DEC-003` (supersedes `DEC-002`) in [DECISIONS.md](DECISIONS.md).

---

## 1. What this is, and what it is not

This is a **capability the existing Evo agents invoke**. An agent asks: *"around the
strategy I am running, over this window of settled history, what would these variants have
done?"* The search replays the base strategy and a bounded neighbourhood around it, gates
every proposed variant, scores what survived, and returns **evidence**. The agent reads it,
reasons about it, and may then revise its own strategy — or not.

It is **not** a second population. The evolutionary organism is
`kalshi_bot/evo/` ([EVOLUTIONARY_AGENT_SYSTEM.md](EVOLUTIONARY_AGENT_SYSTEM.md)):
autonomous agents with cognitive genomes, memory, heartbeats, budgets, cohort fitness,
selection, reproduction, retirement and prospective paper trading. That system is
untouched and remains the only lifecycle in the repository.

```text
EvoAgent  (kalshi_bot/evo/ — authoritative)
  cognitive genome · memory/beliefs · heartbeats · research · peer learning
  cohort fitness + selection · reproduction · retirement
  trading genome + active strategy
      │  invokes, from its own heartbeat, against its own sandbox budget
      ▼
  historical search  (here)
      deterministic replay · parameter-neighbourhood search · bounded mutation
      gated proposals · per-replay virtual ledger · explainable scoring
      │  returns EVIDENCE
      ▼
  the AGENT reasons and decides
```

Four things this package will not do, each structural rather than conventional:

1. **It runs no lifecycle.** No candidate, generation, cohort, reproduction or retirement.
   `evo_agents`, `evo_cohorts`, `evo_genomes`, `evo_fitness`, `evo_births` and
   `evo_retirements` own all of that.
2. **It writes no genome.** `run_search` returns a dict. Every public entry point in
   `mutation.py` is pure — none takes a `session` — so there is no writer to bypass and no
   forgeable admission.
3. **Its scoring is not agent fitness.** See §5.
4. **It cannot trade.** The replay reads settled history and the ledger is virtual. No
   order path, no executor import, no arming call.

An earlier version of this document described a parallel `evo_pop_*` layer with its own
generations and reproduction. That shape was built, reviewed and rejected; `DEC-003`
records why, and the concept-by-concept mapping is in the workstream.

## 2. Object model

```text
EvoSearchRun          one invocation, by one agent, from one strategy revision
  └─ EvoSearchCandidate   the base genome (idx 0) and each neighbourhood point
       └─ EvoSearchTrade      that point's replayed trade tape
```

Three tables, migration `b1d4f6a80c93`, created from the ORM objects so the DDL cannot
drift from `models.py` — the same technique as the evo base and announcements migrations.

Every run is attributable: `agent_uuid`, `cohort_id`, `heartbeat_id`, `genome_revision`
(the agent's trading-genome revision at the moment it asked) and `base_strategy_name`. A
search is therefore visible in the same timeline as the agent's other actions rather than
in a parallel one.

Refused proposals are stored alongside admitted ones, with their stage and reason. "We
tried that and the gate said no, because…" is evidence about the search space; without it
an agent reproposes the same invalid mutation forever.

## 3. The genome

A genome **is** a `StrategySpec` document (`evo/strategy_spec.py`) — the typed DSL the
sandbox already replays. Reusing it avoids a second answer to "what does this strategy do"
and a forked replay engine.

What this package adds:

- **A mutation surface.** 23 genes, each with a type, range, step, distance weight, an
  `independent` flag and a compatibility rule saying when it applies at all. Nothing
  outside it is mutable. `genome.surface_summary()` prints it.
- **Canonical normalization + hash.** `genome_hash` is SHA-256 over the normalized document
  with `name`/`description` stripped and entry conditions sorted. Renaming a genome does not
  make it a new genome; reordering its conditions does not either. So a duplicate cannot be
  smuggled past the novelty floor under a new name.
- **A distance metric.** Weighted mean per-gene distance in [0, 1]. Numeric genes normalize
  by span; enums are 0/1; `universe.series_prefixes` uses Jaccard, because two genomes with
  identical rules over disjoint universes are not the same strategy. A gene that applies to
  neither genome contributes nothing — `take_profit_cents` on a settlement-exit genome is
  not a real difference.

  `distance(a, b, paths=…)` narrows the measurement to a subset of the surface. The two
  readings answer different questions, and both are used: whole-surface for the recorded
  `distance_from_base` (so it stays comparable between runs), and axes-under-search for the
  novelty gate (§8).

Three genes are in the surface but **not independently mutable**:

- `risk.max_concurrent_positions`, `risk.max_per_event`, `risk.max_cost_per_position_usd`.
  `sandbox.run_backtest` visits markets one at a time and holds at most one position per
  market, so it enforces none of them. Perturbing one produces a variant whose tape is
  provably identical to the base's. They remain part of identity, distance and the risk
  envelope, and an explicit proposal may still set them.
- `universe.series_prefixes` / `categories`: no vocabulary to step through, so blind
  perturbation would invent prefixes matching no market — a zero-trade run that reads as
  "no edge".

## 4. Replay

Delegates to `evo.sandbox.run_backtest`. What this package owns around it:

- **No look-ahead, by refusal.** A window reaching past the declared `data_cutoff` is
  refused, not trimmed. Trimming would mean two searches asking for different windows
  quietly get the same one, and the run rows would claim coverage the evidence lacks.
- **Determinism.** The engine has no wall-clock or RNG dependence — the maker-fill gate is a
  hash of the market key (`evo/fill_model.py`). Each replay records an `outcome_fingerprint`;
  re-running must reproduce it.
- **Isolation.** Each replay builds its own ledger from its own tape. No shared wallet, no
  cross-variant contamination.
- **Three quantities kept apart:**
  - *theoretical opportunity* — gross, before costs;
  - *paper execution* — net of Kalshi fees, what the replay banked;
  - *realizable* — the same trades projected through the measured maker-fill calibration
    (`kalshi_bot/fill_calibration.py`), which carries the adverse-selection correction.
    Collapsing these is how a paper edge that exists only in fills we would never receive
    gets mistaken for a real one.

### Additive changes to the shared engine

All default-off, so no existing caller changes behaviour:

| Change | Why |
|---|---|
| `run_backtest(return_trades=True)` | the ledger needs the per-trade tape; the agent path only reads aggregates, and the tape is never persisted into `EvoSandboxRun.result_json` |
| `_trade()` carries entry/exit times, prices, fees, and `exit_time_exact` | concurrency and exposure cannot be reconstructed without them |
| `register_dataset("synthetic:*", …)` | the proving corpus replays through *this* loop. `DATASETS` (what `cognition.py` validates against) is deliberately **not** extended, so no evo agent can backtest a synthetic corpus and read the result as evidence |
| `run_backtest(skip_crossed_quotes=True)` | a quote with bid ≥ ask is corrupt. **Counting** them is unconditional and inert; **skipping** them is opt-in, and only this package opts in. Making it the default would change execution semantics for every caller — Platform Change Review work |

### The virtual ledger

Per replay. Starting capital, realized P&L, fees, gross, turnover, peak exposure, max
concurrent positions, drawdown, contracts, markets, concentration (top family + HHI), and a
capital-breach flag.

Drawdown is computed on the equity curve ordered by **exit time**, not by the order the
replay visited markets. The replay iterates market by market, so its own trade order is not
chronological; a drawdown read off that order would be an artifact of iteration, and would
badly misrank a variant whose losses clustered in time — which is exactly what the drawdown
component exists to catch.

**Exit times are of two kinds.** A rule-based exit happened *at* the quote that triggered
it, so its timestamp is exact. A settlement exit did not: the replay only knows the last
candle it observed, and settlement occurs at or after that. Treating the last observation as
the settlement time would close positions early and understate overlap, so it is published
as a lower bound, flagged `exit_time_exact=False`, and excluded from exact concurrency and
exposure accounting — which report a `concurrency_coverage` share instead. Drawdown still
uses every trade, because ordering tolerates a lower bound.

Capital is **measured, not enforced**: the engine has no concurrency cap, so a breach is
recorded and penalised through the integrity component rather than prevented mid-run. See D2
in the workstream.

## 5. Scoring — and why it is not fitness

Nine components, each persisted with its raw measurement, its normalized score, its weight
and its contribution — so a rank can be unfolded rather than asserted.

| Component | Default weight | What it catches |
|---|---|---|
| `edge_lcb` | 0.30 | a lucky run: the lower confidence bound on per-contract edge, fill-adjusted where the calibration covers the trades |
| `return_on_capital` | 0.10 | scale relative to the virtual account |
| `drawdown_control` | 0.20 | a reckless run |
| `tail_control` | 0.15 | worst-decile conditional loss, not the single worst trade |
| `stability` | 0.10 | Laplace-smoothed sign consistency across subwindows |
| `exposure_efficiency` | 0.05 | return per turnover dollar |
| `concentration` | 0.04 | one-event dependence |
| `breadth` | 0.03 | distinct market families |
| `integrity` | 0.03 | capital breach, concurrency over cap, uncalibrated fills |

**This is not agent fitness and must never become it.** `evo/fitness.py` scores an
*organism* over a *cohort* and includes dimensions no replay can see — adaptive
intelligence, research quality, opportunity capture, historical reliability. This scores a
*strategy document* over a *replay window*. The two are never written to the same table and
are not comparable.

The distinction matters most at one rule. **Evidence class gates ranking here:** `adequate`
variants rank against each other, while `insufficient` (below the trade minimum) and
`invalid` (failed run, truncated replay, corrupt data) are returned with their reason but
unranked. That is correct for measuring strategies — a six-trade sample cannot order two
strategies — and it would be an **immunity from selection** inside a lifecycle. The
organism's own evaluator deliberately says the opposite: *"evidence & opportunity use — no
incubation: a no-trade agent scores near 0"*, because scoring no-exposure agents generously
once made opting out of trading rank above trading and losing. The code carries that warning
where someone would be tempted to reuse the rule.

## 6. How an agent uses it

The action, beside `run_backtest` in `evo/cognition.py`'s protocol:

```json
{"type": "search_strategy_space",
 "dataset": "backfill_weather",
 "date_from": "2026-01-01", "date_to": "2026-03-01",
 "proposals": [{"path": "entry.max_price_cents", "value": 65,
                "hypothesis": "above ~65c this book is systematically overpriced"}],
 "dimensions": ["entry.min_price_cents"],
 "neighbourhood": 8}
```

- `spec` is optional. Omitted, the search runs around the agent's **active
  `evo_strategies` spec** — a `TradingGenome` is policy prose whose schema forbids extra
  keys, so there are no replayable parameters inside it to search. Resolution order is
  the named strategy's *running* revision, then whatever else is deployed, then the most
  recent validated spec. The status condition on the named branch matters: a name is not
  an identity, so the newest revision under a named strategy can be one the agent saved
  and never deployed, or deployed and then replaced. Matching on name alone would search
  around a spec the agent is not running.
- **`proposals` is the agent's own hypotheses**, `{path, value, hypothesis}`, measured
  first and recorded with their hypothesis against the result. This is the primary way to
  use the tool: the agent has the thesis, the tool has the tape. An agent that fills the
  whole neighbourhood with its own proposals gets no automatic stepping at all, which is
  the intended end of the spectrum.
- `dimensions` narrows the **automatic** perturbation to particular genes — for when the
  agent knows the axis but not the value. Perturbation is the fallback, not the
  intelligence.
- A named proposal is gated exactly like a stepped one: same five gates, same refusals. A
  path that is not a gene, or a value the gene already holds, refuses the *whole call*
  before anything is written — dropping it silently would look like the search ran the
  test and found nothing.
- The call is checked for affordability against the worst case (`neighbourhood + 1`) but
  **charged for the replays that actually ran** — the base plus every admitted variant —
  against the agent's ordinary weekly sandbox budget. A search cannot buy unlimited
  compute by being phrased as a search, and a *refused* search costs nothing: the whole
  call is validated before anything replays, so a refusal leaves both the `evo_search_*`
  tables and the budget ledger untouched. The outcome reports `sandbox_runs_charged`.

What comes back: the base's result and score, each admissible variant ranked with the
component breakdown behind its score, the variants that were refused and why, and a
one-line `finding`. The finding is stated as a finding, never as a recommendation — it ends
*"One window is one window — decide whether that is a reason to revise."*

Adopting a variant is the agent's own next action: `save_strategy` with the returned
document, then `activate_strategy`. Both go through the organism's existing budgets and
audit. `tests/test_evo_search_agent_invocation.py` drives exactly that loop, and also drives
an agent that reads the same evidence and declines.

## 7. Mutation: PROPOSE is not ACCEPT

```text
propose_sweep (the agent names it) / propose_perturbation (the tool steps it)
        │  a MutationProposal — a description, with no authority
        ▼
evaluate_proposal_document:  surface legality → schema → compatibility →
                             not-a-no-op → risk envelope → novelty/duplicate
        │
        ▼
an admitted document is REPLAYED — never written anywhere as a genome
```

Both ends are already wired: an agent's `proposals` reach `propose_sweep`, and the action's
`dimensions` reach `propose_perturbation`. Both inherit every gate. Neither can express a
change outside the gene surface, because a proposal is `(path, value)` pairs against
`MUTATION_SURFACE` — the structural reason an LLM proposer can never rewrite production
code as a mutation. The hypothesis is the agent's; the search only measures.

Mode switches carry **companion genes**: changing `exit.mode` to `tp_sl` without thresholds
produces a rule that never fires, so the perturbation path seeds them. Without this the
whole exit axis is unreachable.

## 8. Diversity

- Duplicate refusal (exact hash, whole-surface) and a near-duplicate floor.
- The floor is measured over **the axes the search is varying**. Against the full 23-gene
  denominator every single-gene step reads as a near-duplicate, so a targeted search would
  refuse its entire neighbourhood. The duplicate check stays unscoped: two documents with
  the same hash are the same strategy no matter which axis was under test.
- `diversity.measure` reports mean/min pairwise distance, distinct genomes, family shares
  and parent shares over any set of genomes, so a narrowing neighbourhood is observable.
- Deliberately **not** novelty search. No bonus steers a ranking toward strangeness; a
  novelty pressure tuned before we can measure whether the search answers questions at all
  would be untestable.

## 9. Memory and shared knowledge

The organism already has both, and they are not duplicated here. An agent's beliefs,
episodes, experiments and peer learning live in `evo_memories` / `evo_experiments`, written
through the agent's own actions and already separating observation from interpretation from
decision. A search result becomes memory the same way any other observation does: the agent
writes a `revise_belief` or `note_episode` citing the run id.

`search.recent_searches()` surfaces an agent's own recent runs — dataset, window,
dimensions, genome revision — so it can see what it has already asked rather than asking
again.

## 10. Reading the results

Search runs are ordinary rows and are read through the ops channel's `db` request type
alongside everything else. There is no separate Control Tower, because there is no separate
lifecycle to report on: the Evo Control Tower role (`.claude/sessions/evo-control-tower.md`)
covers the fleet, and a search is one of the fleet's actions.

`fitness.explain(components)` unfolds a score into every component's measurement, weight and
contribution. That is the answer to "why did this variant outperform that one?" that a
leaderboard cannot give, and it is what the agent receives in each candidate's `why`.

## 11. Problems the search finds

Anomalies belong in the existing durable places, not in a new one:

- A defect in the search machinery, or an agent capability request → `evo_tickets`, triaged
  by the `evo-ticket-triage` skill and the Evo Ticket Workshop role.
- A data defect the replay surfaces (corrupt books, a dataset that cannot be measured) →
  an Experiment OS issue (`docs/EXPERIMENT_OS_ISSUES.md`).
- A change to shared execution semantics (e.g. making crossed-quote skipping the default,
  or teaching the engine to enforce risk caps) → Platform Change Review.

A ticket or issue **authorizes nothing**: it never changes a lifecycle state, gate, verdict,
epoch or exposure.

## 12. Relationship to Experiment OS

Reference-only, by decision (`DEC-003`, carried forward from `DEC-002`).

**Reused:** XOS metric definitions, the shared maker-fill calibration, statistical bounds
conventions.

**Not done:** search runs are not imported as XOS evidence, no experiment is registered, no
lifecycle state moves, no gate is evaluated. XOS spec §22.7 keeps evo lineage out of XOS by
design (`experiment_os/importer.py`), and reversing that is a shared-semantic change —
Platform Change Review work, not this workstream's.

A strategy that earns formal advancement enters the **normal** XOS path through a session
with the authority to register it. There is no `EVO_LIVE`, and nothing in the package has an
arm, promote or deploy call.

## 13. The historical proving run

```python
from kalshi_bot.evo.search import proving_run
report = proving_run.run_proving(session)   # run in CI by tests/test_evo_search_proving.py
```

A deterministic synthetic corpus, an 80-day window, a fixed seed. The corpus is synthetic
**on purpose**: every question the proving run asks is about the machinery, not about
whether a strategy makes money, and synthetic history answers those better because the right
answer is known in advance and the adversarial cases can be constructed rather than hoped
for. It registers into the shared replay engine, so it exercises the same loop the real
datasets use.

### Result: 13/13 CLEAN

| # | Check |
|---|---|
| 1 | documents are valid and content-addressed |
| 2 | replays reproduce — same document, same window, same tape |
| 3 | scores are explainable — every score reconstructs from its components |
| 4 | the base is measured on the same footing as its variants |
| 5 | ledgers reconcile against their own tape |
| 6 | no look-ahead occurs; an over-reaching window is refused, not trimmed |
| 7 | every variant records exactly which genes moved |
| 8 | refused proposals are surfaced with a reason, not silently dropped |
| 9 | near-duplicates are refused |
| 10 | the search decides nothing — no organism table is written |
| A1 | a reckless variant does not outrank a steady one |
| A2 | a thin sample is held, not crowned |
| A3 | a corrupt replay is classified invalid, not ranked badly |

A1 is worth stating plainly: the reckless profile banks **$262.31 against the steady
profile's $219.12** and still scores 0.649 against 0.857, on drawdown control (0.21 vs 0.92)
and tail control (0.00 vs 0.47). Raw P&L did not decide it.

A2 likewise: the thin-sample variant is not ranked *at all*, and the check text says in the
same breath that this is a property of measuring strategies and must not become an
agent-selection rule.

### Defects the proving run found

Fixed, not documented around:

1. **Inert risk mutations.** Variants mutated on a `risk.*` gene produced byte-identical
   trade tapes to the base. Root cause: the replay enforces no risk cap. Fix: removed from
   independent mutation (§3), plus an inert-mutation detector.
2. **Unreachable exit axis.** `exit.mode` switches were either inert (a `timed` exit with no
   horizon never fires) or refused (a `tp_sl` with no thresholds). Fix: companion genes on
   the perturbation path, and a compatibility rule rejecting a `timed` exit with no
   `max_hold_hours`.
3. **A1 passing by rounding error.** The first tuned corpus had steady and reckless within
   9e-6 of each other, because the profiles did not match their names — "reckless" won
   uniformly, which is a tight bound, not recklessness. Retuned so recklessness means
   *dispersion*: a bimodal win phase and a clustered loss phase. A check that passes by
   rounding error is not evidence the evaluator distinguishes the cases.

And one the agent-invocation test found:

4. **The novelty floor refused targeted searches.** Measured over the whole surface, every
   single-gene step is a near-duplicate, so `dimensions: ["entry.max_price_cents"]` — the
   documented use — refused its entire neighbourhood. Scoped to the axes under search (§8).

---

## The first prospective paper cohort

**Not started. This section is the proposal; it needs operator approval.**

Nothing in the package can start one: there is no scheduler, no live quote path and no
prospective mode to enable.

### What must be built first

1. **A time boundary that is not a pair of dates.** In replay a window is two dates and the
   cutoff is enforced by refusal. Prospectively it is a wall-clock interval, and a variant
   entered mid-window must not accrue evidence from before it started.
2. **A paper execution path.** Replay reads settled history; a prospective run needs live
   quotes and open positions, marked and settled over time. The organism's `evo/paper.py`
   already does this per agent, and the open question is whether a prospective search reuses
   it or is simply *not a search any more* — a variant traded forward against live quotes is
   an experiment, and Experiment OS is where experiments live.
3. **Capital enforcement.** Measured-not-enforced (§4) is defensible for replay, where every
   variant is scored on the same tape. Prospectively, a variant that breaches its account is
   trading capital it does not have.

### Before it runs

- D1 (which real dataset) and D2 (risk-cap enforcement) in
  [WS-006](workstreams/WS-006-evo-search-capability.md) resolved.
- A real-dataset historical proving run completed and clean — the synthetic run proves the
  machinery, not that the machinery says anything true about Kalshi.
- Operator approval, explicitly, in this repository. Nothing here starts anything.
