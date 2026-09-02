# WS-011 — MARKTANGLE: conditional reversion in recurring binary families

**Phase:** DECIDE
**Status:** Paused
**Created:** 2026-08-29
**Updated:** 2026-08-29 (run 1)

## Goal

Stand up the research apparatus for a new hypothesis family — streak-conditioned
reversal in the *resolutions* of recurring Kalshi binaries — as registered Experiment
OS objects plus a runnable, pre-registered probe, so the question can be answered
cheaply on history instead of argued.

## Context

The idea arrived as two things fused: a **Martingale sizing scheme** and a **claim about
streak-dependent mean reversion**. Only the second is testable; the first is arithmetic
that no data can rescue. The build separates them and pre-registers the separation, so
"we sized up because we were losing" can never be mistaken later for "we sized up because
the estimated edge grew".

The thesis, the arm design and the verdict rule live in `docs/MARKTANGLE_THESIS.md`. This
file tracks the *development* thread only.

## Current Mental Model

```text
  a resolution SEQUENCE per recurring family      (not an intraday price path —
        Y Y N Y Y Y Y N ...                        that question is already dead)
                 │
        ┌────────┴────────┐
   TRAIN 70%          HOLDOUT 30%      time-ordered, never shuffled: the regime
   fit k* only        grade only        structure is what is being tested
                 │
                 ▼
        conditional reversal at k*     ── vs ──   the taker price at T-60m
                 │                                  + worst-case fee
                 └──────────► edge ≥ 3c ? ─────────┘
                                   │
              size ∝ EDGE (never ∝ accumulated losses)
```

Two engines, deliberately separate:

1. **Streak alpha** — estimate `P(reverse | run = k)` and compare it to the price.
2. **Position sizing** — a function of the estimated edge, capped. Never a recovery
   progression.

The control that makes the whole thing falsifiable is `mktcont`, the **continuation
mirror**: the same book pointed the other way. If the treatment cannot beat its own
mirror, the streak carries no direction and the family dies — that is a `fail_any`
clause, not a judgement call.

## Decisions Made

- **Martingale sizing is a pre-registered exclusion**, recorded in v1's `held_constant`
  rather than left as an untested option. Adding it later is a new Version.
- **The probe deployment is tagless.** Under NEW_ONLY a tag no active deployment arm
  carries cannot trade; registering the contract therefore grants no trading capability
  at all. Tags are assigned only when a PAPER deployment is registered, which is a
  separate reviewed step taken only on a probe PASS.
- **No probe gate is registered.** The probe reads public settlement history, which no
  canonical metric provider can compute; a gate against an uncomputable metric would sit
  at `BLOCKED_DATA` forever and misrepresent itself as pre-registration. The probe's bar
  is frozen inside v1's immutable contract instead.
- **Only `mktrev3` can promote.** `mktrev5` and `mktkelly` are read against it. A gate
  that promotes whichever arm looks best is a multi-way search.
- **The fill model is not read here.** It is calibrated for resting maker orders in the
  mmsell cheap band; every MARKTANGLE arm is a taker.
- **D1 answered (operator, 2026-08-29): the first probe run is exchange-wide and
  un-restricted.** Restricting to families with a physical regime story would pre-select
  the answer — it would find the families we already believe in and tell us nothing about
  the ones we do not. The sweep is the script's default, so the request carries no
  `--series`. The cost of the honest version is a longer run and a wider table, not a
  weaker verdict: the holdout floor and the edge bar are unchanged, and a family that
  clears them without a story is a more interesting result, not a less trustworthy one.

## Open Decisions

- ~~**D1.** Probe breadth on the first run.~~ **Answered: exchange-wide, un-restricted.**
  See Decisions Made.
- **D2.** If the probe PASSes on exactly one family, is one family a strategy? The
  promotion gate does not ask this, and it should be answered before PAPER rather than
  after — a single-family book is a single-regime bet whatever its per-trade edge.

## Assumptions

- Kalshi's settled-markets API exposes enough per-family history to split 70/30 with a
  usable holdout. **Unverified** — the first probe run measures it, and a HOLD verdict on
  thin sample is the pre-registered answer if it does not.
- `event_ticker` + ticker suffix identifies a recurring binary. Ladder rungs are separate
  families under this rule, which is the behaviour we want.
- Candlesticks are available at T−60m for settled markets through the live or historical
  archive. If neither answers, the PRICE stage reports fewer priced entries and the floor
  refuses a verdict rather than inventing one.

## Non-Goals

- Trading anything. No tag, no paper book, no exposure.
- Building the regime model that a streak is a proxy for. That is the successor question
  if and only if the crude signal survives.
- Reviving intraday price-path mean reversion in any form.

## Build Card

Delivered in this PR:

- `kalshi_bot/experiment_os/marktangle.py` — the reviewed contract package.
- `kalshi_bot/experiment_os/experiment_commands.py` — package registered on the transport.
- `scripts/marktangle_probe.py` + ops-runner allowlist entry.
- `docs/MARKTANGLE_THESIS.md`, this workstream, `docs/RESEARCH_JOURNAL.md` entry.
- `tests/test_marktangle_package.py`, `tests/test_marktangle_probe.py`.

## Implementation State

Code complete and tested. **Not yet registered in production** — registration is a
`REGISTER_PACKAGE` envelope on the experiment-command transport, executed by the worker
at boot, and it happens after this PR merges.

## Review State

Awaiting review.

## Related Decisions

`DEC-001` (two ledgers, one boundary) — this file tracks the build thread; the
experiment's standing, gates and verdicts are Experiment OS's.

## Related PRs

This PR.

## Correction (2026-09-02): the Experiment OS objects below do not exist

A direct read during WS-013's work returned *no experiment
'marktangle-conditional-reversion'*. This file's Implementation State, its D4 entry and the
objects listed below describe a registration that never landed — the envelope would have hit
the `TypeError: register() got an unexpected keyword argument 'promotion_sample_floor'` that
WS-013 diagnosed and fixed, and nothing that was never created could then be PAUSED. The
research record (runs, findings, the HOLD) is unaffected; only its XOS representation is
missing. Detail and the recommended remedy: WS-013's run log.

## Related Experiment OS objects

Linked, not restated — query Experiment OS for current state.

- `marktangle-conditional-reversion` — the experiment (v1, stage PROBE once registered).
- gates `paper_to_live_canary`, `paper_keep` on v1.
- deployment `marktangle-probe-1` (kind PROBE, tagless).

## Run log

**Run 1 (2026-08-29, ops `mkt-probe-1` + `mkt-diag-1`) — no verdict, instrument fixed.**
The exchange-wide sweep returned 0 usable families; a per-series diagnostic returned 198
through the same code, proving the limit was the endpoint rather than the exchange. Two
instrument fixes shipped (two-stage discovery-then-history fetch; the balanced-base-rate
screen the thesis already pre-registered but the script never had). Contract untouched.
Detail in `docs/MARKTANGLE_THESIS.md` §8b.

**D3 — ANSWERED 2026-08-30: rank by settled frequency.** `/events` enumerates (which
series exist and are still tradeable); the settled listing ranks (how often each settles).
The bias that made the listing a poor enumerator is what makes it a good recurrence
ranker. Superseded reasoning below, kept because the error is the useful part: run 4
ranked by concurrent open events on the claim that "a series carrying more of them recurs
more often", which the data falsified — many concurrent events means a one-shot ladder.

**D3 (sharpened by the fix, now closed).** With `/events` enumerating properly, `--max-series` is now
a prefix of a *real* ranking (series by concurrent open events) rather than a prefix of an
accident — so the question narrows to how large that budget should be, and whether a
single series may contribute unboundedly many families. Still open.

**Original D3.** How deep is deep enough? KXBTCD alone returns 20,000 settled markets and
would dominate any exchange-wide ranking purely by having the most strikes. The current
`--max-series` cap is a rate-limit guard, not a scientific choice, and a ranking dominated
by one series is a finding about that series. Decide whether the unit of the sweep should
be the family (as now) or the series, with a per-series family budget.

## D4 — ANSWERED 2026-08-30: close for now

Operator decision: pause on the run-8 results, with the intent of possibly resuming.
Recorded in Experiment OS as PAUSED (from PROBE) via `scripts/marktangle_pause.py`, which
an operator runs on a writable connection. Nothing about the evidence changes.

**Resume condition, so it is checkable rather than remembered:** a candidate family
(currently `KXUSLTOTAL|2,3,4` or a weather bucket such as `KXHIGHMIA|B92.5`) accumulates
enough settled history that its 30% holdout reaches 100 entries. At a daily cadence that is
months of forward collection. Then re-run `scripts/marktangle_probe.py` unchanged — the
contract, arms, gates and verdict rule are frozen and need no revision.

**Do not resume by** widening the universe, lowering a floor, or re-reading the bar. Those
are the moves this experiment's five run logs exist to prevent.

### Original framing (kept — the reasoning is the useful part)

**Is MARKTANGLE worth further investment?** Five runs have each produced a diagnosed
data-access finding and zero evidence about the hypothesis. The instrument is well tested
and the contract is frozen and sound; what is missing is a way to reach a universe with
enough settled history per family to fit a `k*`. The known remaining route is a per-series
settled count over a pre-filtered candidate set — a real build, and the option already
costed and declined once. Alternatives are to shortlist a hand-picked universe (accepting
that it pre-selects), or to stop and leave the record as durable negative-space evidence.
Operator's call; this workstream does not proceed without it.

## Next Step

**Blocked on D4** — see above. Historical note on the original blocker: the ops runner
executes code from the DEFAULT BRANCH only — a
fail-closed guard (`OPS_RUNNER_CODE_SOURCE=default-branch`, the durable fix for
XOS-000005) — so `marktangle_probe` is not runnable until this PR merges. The Claude
sandbox cannot substitute: its egress policy refuses `api.elections.kalshi.com` outright
(403 on CONNECT), which is why probes run on the ops channel in the first place.

Once merged, in order:

1. `REGISTER_PACKAGE marktangle-reversion` on the experiment-command transport.
2. ~~The exchange-wide sweep (D1)~~ — **run, no verdict; see the run log.** The re-run on
   the fixed instrument:
   `{"type":"script","name":"marktangle_probe","args":["--max-series","40"],"id":"mkt-probe-3"}`
3. The PRICE stage on whatever survives:
   `{"type":"script","name":"marktangle_probe","args":["--pages","12","--price"],"id":"mkt-probe-2"}`
4. Record the verdict in `docs/MARKTANGLE_THESIS.md` and `docs/RESEARCH_JOURNAL.md`.
   A HOLD is a real outcome and is recorded as one.
