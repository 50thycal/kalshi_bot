# WS-010 — PERP-V1: a research surface for Kalshi perpetual futures

**Phase:** BUILDING
**Status:** Active
**Created:** 2026-08-29
**Updated:** 2026-08-30

## Goal

Open perpetual futures as a research surface for this bot: pre-register one
Experiment OS experiment (`perp-v1`) whose three treatment arms race three
perp-native mechanisms against a matched control, and ship the cheapest probe
that could falsify the whole line before any strategy work is done.

## Context

Every book this repository has run asks *is this Kalshi event contract mispriced
against our own forecast?* — a bet on an unobservable probability, and the
graveyard is mostly estimates that were real in-sample and gone after spread and
fees. Kalshi's crypto perpetuals are a different instrument class: an explicit
funding mechanism tethers the perp to a published reference index, so the
question becomes *where is risk priced differently across two instruments tied to
the same underlying* — relative pricing, which admits much stronger controls.

The repository has been here once. `docs/RESEARCH_JOURNAL.md` PERPS SURVEY
2026-07-09 surveyed the same product and recorded a **discovery gap, not a kill**:
the product was real, and no perp series was reachable through the public
event/market endpoints. Its recorded next step was to find a perp-specific
endpoint and only then probe funding/basis, gated on normal fees. This workstream
claims that condition has now been met — and treats the claim as a claim.

## Current Mental Model

```text
  the pre-registration (shipped)                the probe programme (staged)
  ------------------------------                ----------------------------
  docs/PERP_V1_THESIS.md          <-- cites --  Probe 0  surface survey
        |                                          |     scripts/perp_surface_survey.py
        | executable form                          |     ops-runnable, no credentials
        v                                          |
  experiment_os/perp_v1.py                         |  ABSENT everywhere
        |  REGISTER_PACKAGE "perp-v1"              +----------------------> STOP
        v                                          |  (BLOCKED_DATA at PROBE)
  perp-v1 @ PROBE, v1 frozen, e1 open              |
        arms: perprevert  (premium reversion)      |  READABLE / EXISTS-AUTH
              perpcarry   (funding dispersion)     v
              perplead    (perp -> ladder lead)  Probe 1  read-only tape collector
              perpctl     (matched random dir)      |     markets/mark/index/BBO/book/
        gates: one PROBE->PAPER bar per arm         |     trades/OI/funding — NO orders
               + perp_probe_stop                    v
                                                  Probe 2  three arm scorers
                                                          -> record gate results
  no strategy tag · no deployment · no exposure
```

The three arms are one experiment because they share what decides them: one
universe, one cost model, one collector and one headline metric (net edge in bps
of notional, after fees, slippage **and** funding). Registered separately, those
would be three separately-chosen quantities and the horse race would rest on an
assumption of comparability rather than a frozen contract.

## Decisions Made

- **One experiment, three treatment arms.** The operator's instruction, and the
  right shape: the comparison is only meaningful under a shared contract. Cost
  accepted: arms freeze together, so changing one arm's rule is a new Version for
  all three.
- **A fourth, control arm (`perpctl`).** Matched entries with randomised
  direction. Without it every arm can be flattered by an accidental long-crypto
  tilt, and `delta.perp_net_edge_bps_per_trade` has nothing to resolve against.
- **One promotion gate per arm, not one for the version.** `arm: "*"` would make
  all three promote together or none; the horse race needs an arm to be able to
  clear its own bar. The binding rule that goes with it is written in the thesis:
  the paper deployment carries only the arms whose own gate PASSed.
- **Register at PROBE with no tag and no deployment.** A probe is an instrument.
  Under NEW_ONLY an unregistered tag cannot trade, which is the correct state for
  an experiment whose data source has never once been read successfully.
- **Perp P&L is denominated in bps of notional, not cents per contract.** A perp
  has no contract face value; reusing the event-contract unit would make the two
  families' numbers look poolable when they are not.
- **Probe 0 before Probe 1.** §6 of the thesis. The API surface is unverified from
  this environment, and 2026-07-09 is the precedent for what happens when that
  assumption is skipped.

## Open Decisions

- ~~**D1. Where does the tape collector run?**~~ **CLOSED 2026-08-30 — on the
  worker.** Not because the surface needs credentials (the market, book and
  candle reads are public), but because the ops channel runs one script per
  request against a read-only database connection: it can survey, it cannot
  accumulate. A tape needs a writer on a schedule, and the worker is the only
  process that is one. It runs in the every-mode cycle hook, beside the Experiment
  OS hook, for the reason recorded there — a hook inside `_run_cycle` silently
  never runs under live/weather/mmsell/evo.
- **D2. Does a perp book need a Platform Revision before it could ever be
  live?** Almost certainly yes — leverage, liquidation and an 8-hourly funding
  cash flow are semantics no FEE_MODEL/FILL_MODEL revision in this repository
  describes. That is **Platform Change Review**'s call, not this workstream's, and
  it is not on the critical path while PERP-V1 stays at PROBE.
- ~~**D4. If funding is unreadable, what happens to `perpcarry`?**~~ **CLOSED
  2026-08-30 — no funding source exists; arm B is BLOCKED_DATA.** The probe asked
  the busiest perp on the exchange (`KXBTCPERP`) over a 7-day window, twice, and got
  `{"funding_history": []}` both times — after the same answer unscoped and scoped to
  `KXAAVEPERP`, and after reading all 24 market-row keys and finding nothing
  funding-shaped. The endpoint sits under `/margin/` beside positions and balance and
  requires auth: a market-wide feed has no reason to be empty for BTC over seven days,
  an account payment ledger has exactly one. Arm B is **not deleted and not
  re-scoped** — the package registers it as pre-registered, it reaches its gate, and it
  fails to produce evidence, which is a pre-registration working rather than breaking.
  Running the ranking on *premium* instead is a different hypothesis: new Version,
  operator decision. Arms A and C are untouched and their inputs are in the tape.
  Residual thread, deliberately not chased: `exchange_index` on the market row.
  Full record: `docs/RESEARCH_JOURNAL.md` (PERP-V1 D4 CLOSED 2026-08-30).

  The history below is kept because the error in it is the instructive part.

- ~~**D4 (history). RE-OPENED
  2026-08-30** — it was closed earlier the same day on a misreading, and the
  misreading is kept here because it is the instructive part. A
  `400 "start_date is required"` from `/margin/funding_history` was taken as
  "funding is reachable". It proved only that the **path exists**. Supplied with a
  date range the same endpoint returns `401 token_authentication_failure`, and the
  authenticated worker gets a 200 carrying **no records at all**. Leading
  hypothesis: it is our own funding-**payment** ledger, not a market rates feed —
  we hold no perp positions, so empty is exactly what that would produce, and every
  market-wide rates path probed so far 404s. Hypothesis, not finding. The tape now
  records the response envelope's **shape** (top-level keys and list lengths, keys
  only, no values — the endpoint is authenticated and `notes_json` is read through
  the public ops channel) on any zero-row parse, which makes the question decidable
  on evidence rather than inference. Until it is decided, `perpcarry` stands exactly
  as registered and no re-scope is made: re-scoping arm B to a premium-dispersion
  ranking would be a different hypothesis under the same arm key, and its gate was
  pre-registered before any of this.

  **First shape read, 2026-08-30:** `{"funding_history": []}` — one key, an empty
  list. The parser was not missing a differently-shaped envelope; there are
  genuinely zero records. Separately, **no funding field rides on the market row**
  (all 24 keys listed in the journal), so assumption **A2** has no confirmed source
  on this surface. One question remains genuinely unasked: every call so far passed
  **no ticker**, and empty is equally what an account ledger returns and what a
  market feed returns unfiltered. The collector now retries once, scoped to a real
  ticker, shape recorded the same keys-only way, only when the unscoped call found
  nothing.

  **Scoped read, 2026-08-30:** also `{"funding_history": []}` — so the filter was
  not the problem. One weakness in that answer: it asked about `KXAAVEPERP`, the
  first ticker in an alphabetical listing rather than a chosen market, and "no
  funding on an illiquid market" is not a claim strong enough to block an arm on.
  The probe now asks about the market with the largest open interest. If **that**
  is empty over a 7-day window, D4 resolves to arm B = **BLOCKED_DATA** and there
  is nothing cheaper left to ask.
- **D3. Arm C's control.** `perpctl` is a perp-side control and does not by itself
  answer "better than Theta". The gate uses an incremental-over-Theta metric
  instead. Whether a first-class `external_control` reference to the Theta
  experiment is worth registering at PAPER is deferred until arm C has evidence —
  a cross-experiment delta is BLOCKED_PLATFORM whenever the two epochs pin
  different snapshots, which `mmsell-anchor-vol-entry` is currently demonstrating.
- ~~**D5. Does the collector need its own cadence?**~~ **CLOSED 2026-09-02 — MOOT.** The
  measured numbers stand (29.61% coverage, 191.6 s achieved against 60 s intended, 100%
  per-cycle, zero errors), but the decision they fed no longer has anything to decide:
  arm A failed on fees, arm C is a NO-GO, arm B has no data. Recorded rather than dropped
  because the finding survives the experiment — **the collector cannot meet an 80% coverage
  floor while it shares the worker's scan loop**, and any future perp or high-cadence
  research inherits that.

  One correction worth keeping: the constraint on arm C was never the perp collector. It
  was `theta_interval_minutes = 5.0` — the Kalshi ladder snapshot cadence. Arm C scores the
  forward move of the *event contract*, so speeding up the perp side alone would have
  changed nothing. A fast arm C test needs BOTH sides at seconds, in one process off one
  clock. Kalshi does expose a perps WebSocket (`ticker` / `orderbook_delta` / `trade`; auth
  required on the handshake even for public channels) and this repository has no WebSocket
  code; account tier is `basic`, 20 reads/sec, which the mmsell scan already bursts against.

- ~~**D7. What is the Kalshi perp round-trip fee?**~~ **CLOSED 2026-09-02 — it ends the
  experiment.** Operator read the Launch Fee Schedule: tier 0 taker **0.120%/side = 24 bps
  round trip**, maker 0.020%/side = 4 bps round trip; tier is 30-day trailing perps +
  prediction notional. Arm A's gross convergence is 14.52 bps against 8.88 bps of paid
  spread. Taker/taker is **−18.4 bps**; maker on one leg only is **−3.9 bps**; only
  maker/maker is positive (**+10.5 bps**) and it is the combination least likely to fill.
  Full arithmetic and reasoning: `docs/RESEARCH_JOURNAL.md`, PERP-V1 CLOSED 2026-09-02.

  The finding generalises past arm A: **the tier-0 round trip costs 2.7x the entire
  measured bid-ask.** Any single-digit-bps mechanism is structurally dead on this surface
  at this tier.

- ~~**D8. Is there resting depth for a maker variant to rest against?**~~ **CLOSED
  2026-09-02 — yes, and that is not the good news it sounds like.** Run to test whether
  thin books made the maker question moot on capacity grounds. They do not: ETH turns over
  **$172M/24h at a 0.7 bps spread**, BTC **$42.7M at 0.4 bps**. The prediction was wrong
  and the opposite is recorded.

  The load-bearing finding is the mismatch. **Arm A paid 4.44 bps per side; BTC/ETH quote
  0.4-0.7 bps total.** Arm A's entries therefore came from the wide-spread illiquid names
  (ADA 33.2, BNB 21.4, AAVE 16.2 bps), not the liquid ones — consistent with an extreme
  premium z-score being largely a symptom of illiquidity. So a maker variant pointed at
  BTC/ETH may find very few entries: capacity exists where the signal probably does not.
  That is a known unknown now rather than an assumption, and it is the first thing such a
  variant would have to establish.

## Assumptions

- **A1 — TESTED 2026-08-29, largely CONFIRMED.** Probe 0 ran through the ops
  channel. `/margin/markets`, `/margin/markets/{ticker}` and its `/orderbook` are
  readable **unauthenticated**; positions, balance, fills and fee tiers exist and
  need credentials; tickers look like `KXAAVEPERP`. Critically, `reference_price`
  rides on the market row, so arm A's index anchor exists. Findings recorded in
  `docs/RESEARCH_JOURNAL.md` (PERP-V1 PROBE 0 RESULT 2026-08-29). What A1 got
  wrong is funding — see A4.
- **A4 — CLOSED 2026-08-30 as originally feared. There is no funding source.**
  See D4. What follows is the reasoning as it stood when A4 was reopened.
- **A4 (history) — OPEN AGAIN 2026-08-30. Funding is not established.** It was recorded
  RESOLVED earlier the same day and that was an over-read of one status code.
  `/margin/funding_history` answered `400 "Query argument start_date is required"`,
  which proves the **path exists** and wants a date range — it says nothing about
  what the path returns. Given the range it returns `401
  token_authentication_failure`, and the authenticated worker gets a 200 with **no
  records**. The names in the brief (`/margin/funding_rates`,
  `/margin/funding_rate_estimate`) are still simply wrong, and the 400-vs-404
  classifier fix is still what stopped this being recorded as a kill — both of
  those hold. What does not hold is "funding is reachable". See D4 for the
  hypothesis and the diagnostic that decides it. Journal:
  `docs/RESEARCH_JOURNAL.md` (PERP-V1 A4 RESOLVED 2026-08-30, corrected by
  PERP-V1 TAPE LIVE 2026-08-30).
- **A5 — NEW, and smaller than A4 was.** Arm C's *trade imbalance* feature has no
  public source: `/margin/markets/{t}/trades` 404s and no trade-ish field rides on
  the market row. The other five arm-C features survive. The version declares the
  features as candidates tested independently, so this removes a candidate rather
  than invalidating the arm — but arm C's eventual result must state which features
  it could actually see.
- ~~**A2.** Funding is published for the forming window, not only historically.~~
  **FALSIFIED 2026-08-30** — see D4. Nothing readable on this surface publishes a
  funding rate at all, forming-window or historical. Arm B's ranking depended on it
  entirely and is BLOCKED_DATA. **Arm A depended on it only for entry confirmation**,
  not for its signal: `premium = (mark − index) / index` is computed from two fields
  that both ride on the market row and are in the tape now. Arm A therefore proceeds
  with its funding-agreement condition unavailable — which Probe 2 must state, since a
  pre-registered entry condition that cannot be evaluated is not the same experiment as
  one that was evaluated and passed.
- **A3.** Fees at the level the active platform snapshot declares, not a
  promotional zero-fee level. The 2026-07-09 survey flagged that a promo makes
  every cost gate misleadingly easy.

## Non-Goals

- Placing a perp order. No perp order path exists in this repository and this
  workstream adds none.
- Real money, leverage or a live canary. Those need a successor version with a
  pre-registered risk envelope that does not exist, and a platform answer to D2.
- The perp-hedged-Theta idea (using perps to delta-hedge prediction-market
  positions). Deliberately parked: it is a different question — variance
  reduction on an existing edge, not a new edge — and belongs in its own
  experiment if PERP-V1's collector proves out.

## Build Card

Slice 1 (this PR): pre-registration + Probe 0.

- `docs/PERP_V1_THESIS.md` — the scientific contract
- `kalshi_bot/experiment_os/perp_v1.py` — its executable form; a reviewed package
- `kalshi_bot/experiment_os/metrics.py` — seven declared-unprovided probe metrics
- `scripts/perp_surface_survey.py` + ops allowlist — Probe 0
- `tests/test_perp_v1_package.py`

Slices 2 and 3 (Probe 1 collector, Probe 2 scorers) were **blocked on Probe 0's
result** and deliberately not designed until it landed: a collector written against
assumed field names is the same error as a probe written against guessed series
tickers.

Slice 3 (this PR): Probe 2.

- `scripts/perp_arm_scores.py` + ops allowlist — the arm scorers
- `tests/test_perp_arm_scores.py` — the refusals pinned as hard as the arithmetic
- `docs/PERP_V1_THESIS.md` §6, §7.1 — Probe 2 named; the fee and coverage limits recorded
- `kalshi_bot/experiment_os/metrics.py` — the seven references now name a written provider

One script, not the three §6 anticipated. The arms share a tape, a cost model and a
control; three scripts would have been three chances for those to drift apart, and a
drifted cost model between two arms of one horse race is the comparison failing
silently.

## Outcome — CLOSED 2026-09-02

**All three arms are done. None promotes. The experiment is closed on a COST finding, not
a signal finding, and that distinction is the result.**

| arm | verdict | why |
|---|---|---|
| `perprevert` (A) | **FAIL — execution economics** | mechanism real (+5.63 bps/trade pre-fee, 913 obs, vs a −10.13 control); tier-0 taker round trip is 24 bps |
| `perpcarry` (B) | **BLOCKED_DATA** | no funding source exists on this surface (D4) |
| `perplead` (C) | **NO-GO** (operator, 2026-09-02) | null at 300 s on ~97k pairs; fast horizons untested, not falsified |

Arm A is the one worth stating carefully. **The premium-reversion effect is real** — 913
scored round trips, +14.52 bps of gross convergence, beating a matched random-direction
control by +15.76 bps on the same entries. It dies on the fee, at a surface where the
round trip costs 2.7x the entire bid-ask. §7 of the thesis pre-registered this as the most
likely honest outcome ("convergence that is real but smaller than the round trip"), and
that is what happened.

**What would reopen it** — neither is an experiment, both are decisions:
1. A fee-tier change. Tier 3 ($10M/30d) makes maker 0.000% and taker 2 bps.
2. A maker variant registered as a **new Version**, with a pre-registered fill model built
   on a trade-print tape the collector does not gather. D8 says capacity exists on BTC/ETH
   and warns the signal probably does not live there.

**Not registered in production, and therefore not in Experiment OS.** The probe ran its
whole lifecycle unregistered — correct under NEW_ONLY, since an unregistered tag cannot
trade — which means there is no XOS gate result, verdict or transition for any of it. These
documents are the record. Whether a retrospective registration is wanted so the graveyard
carries a structured entry is an operator/Control Tower call, deliberately left open.

## Implementation State

Slices 1–3 built (#275, #277, #280, #284). **The tape is live.** `PERPS_COLLECTOR_ENABLED=true`
on the main worker since 2026-08-30 12:35Z. First measured hour: 26 cycles, 546 market
snapshots, 312 order books, **0 funding rows**, 0 errors.

Two measurements from that hour change what the tape can answer:

- **Cadence is 145 s**, set by the worker's whole scan loop, not by the 60 s
  `PERPS_INTERVAL_SECONDS` floor (which only prevents polling *too often*). Ample for arm
  A's 8-hourly premium reversion. It bounds **arm C**: a perp→Kalshi lead shorter than
  ~2.5 minutes is invisible, so an arm C null is ambiguous between "no lead" and "faster
  than we sampled" and must not be read as a kill on the mechanism. Not fixed by threading
  the collector — that buys resolution beside real money before Probe 2 has shown we need it.
- **Funding returns 200 and zero rows** — see D4, re-opened.

The perp surface is real and its history endpoints are readable. Registration in production has
**not** been submitted — that is a `REGISTER_PACKAGE` envelope through the `env` channel,
which redeploys the worker while the mmsell10 canary holds real money, so it stays an
operator act.

**Probe 2 ran on 2026-09-02** (`perp2-d6-3`), after two import failures under the ops
runner (#307 path, #308 dependencies — both recorded there; both were environments I
asserted rather than reproduced). Result: coverage **29.61%**, arm A **913** observations
at **+5.63 bps/trade ex funding** vs a **−10.13** control, arm C **null at 300 s** with
its fast horizons unobservable, arm B **BLOCKED_DATA**, and **every gate NOT READABLE**.
D6 closed; D5 is now the live decision and D7 (the fee) is the cheapest open question.
Full numbers: `docs/RESEARCH_JOURNAL.md`, PERP-V1 PROBE 2 FIRST RUN 2026-09-02.

**Probe 2's design, unchanged by the run:**
`scripts/perp_arm_scores.py`, ops-runnable and read-only. Its design is governed by one
rule, because three pre-registered inputs turned out not to exist: *a quantity that omits
an input its registered definition names is a different quantity, and never gets the
registered name.* Concretely —

- `perp_net_edge_bps_per_trade` is defined net of funding, so the scorer reports it NOT
  PRODUCIBLE and prints an explicitly-named ex-funding figure beside it. Every gate clause
  reading that key is reported unreadable rather than read against the substitute.
- Arm A's un-evaluated funding-agreement entry condition, its missing funding-window exit
  clock and its missing z-score conditioning are each printed as deviations on the arm's
  own result — a pre-registered condition that could not be evaluated is not the same
  experiment as one that was evaluated and passed.
- The fee schedule needs credentials the ops runner does not hold, so no fee is guessed;
  each arm reports its **breakeven round-trip fee** instead, which an operator can check
  against a real schedule.
- Arm C's registered 5/10/30/60 s horizons are **refused**, not reported as nulls, because
  they sit under the ~145 s sampling interval. Only 300 s is measurable.
- `perp_data_coverage_pct` divides by the *intended* cadence. Against the achieved one it
  is 100% by construction and says nothing.

The expected consequence, stated before the run so it cannot be read as a result: with a
60 s intended interval and a ~145 s achieved one, coverage lands near 40%, under the
registered 80% floor — so **no arm can PASS on the current tape whatever its edge**, and
HOLD is the correct verdict. Closing that gap is a platform change (the collector's own
cadence), not a scorer change. Lowering the floor after seeing the number would be
re-tuning a pre-registered gate against results.

Registration and the collector are **independent**: the collector writes its own
instrument tables and creates no strategy tag, so it can run before `perp-v1` is
registered. What it cannot do is produce a PAPER book — that needs Probe 2's scorers, a
recorded gate PASS, and a registered deployment, in that order.

## Review State

Not started.

## Related Decisions

`DEC-001` (the authority boundary — this file links to `perp-v1`, and never restates
its standing or its gate reads). `DEC-008` (why the three mechanisms are arms of one
experiment rather than three experiments, and why each arm carries its own gate).

## Related PRs

This PR.

## Next Step

**None inside this workstream.** It is closed. Three things leave it, each owned elsewhere:

1. **Turn the collector off, or decide to keep paying for it.** `PERPS_COLLECTOR_ENABLED`
   is still `true` on the main worker and the tape is still accumulating at ~26k rows/day
   against three closed arms. Turning it off is an `env` write (Live Ops). Keeping it is
   defensible only if a maker variant is actually intended.
2. **The coverage finding outlives the experiment** (D5): nothing sharing the worker's scan
   loop can meet an 80% coverage floor. Any future high-cadence research inherits it.
3. **Retrospective XOS registration**, if wanted — Control Tower's call, not this
   workstream's.
