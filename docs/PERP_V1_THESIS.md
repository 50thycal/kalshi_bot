# PERP-V1 — Kalshi crypto perpetual futures: three mechanisms, one horse race

**Status:** **CLOSED 2026-09-02.** Nothing here ever traded; no perp order path exists in
this repository. All three arms are done — arm A FAIL on execution economics, arm B
BLOCKED_DATA, arm C NO-GO. The document below is preserved as the frozen pre-registration
it was, unedited except for the measured limits in §7.1 and this header: what it predicted
is only meaningful if it is still readable as what was predicted.

Outcome and reasoning: `docs/workstreams/WS-010-perp-v1-pre-registration.md` (Outcome) and
`docs/RESEARCH_JOURNAL.md` (PERP-V1 CLOSED 2026-09-02). **§7's second bullet called it** —
the arm that worked was killed by the fee, which is the outcome this section named as most
likely before any data existed.

**Experiment OS key:** `perp-v1` · **Version:** 1 · **Origin:** operator
**Family:** `perp` · **Registered by package:** `perp-v1` (`kalshi_bot/experiment_os/perp_v1.py`)

Experiment OS is canonical for this experiment's lifecycle state, arms, gates and
verdicts. This document is the **scientific contract** the gates were written
against — the "why", not the standing. Ask `xos show perp-v1` for the standing.

---

## 1. What is new about the surface

Every book this repository has run so far asks one question: *is this Kalshi event
contract mispriced against our own forecast?* The edge is always an estimate of an
unobservable probability, and the graveyard is mostly full of estimates that were
real in-sample and gone after spread and fees.

Kalshi's crypto **perpetual futures** are a different kind of instrument. A perp is
tied to an underlying reference price by an explicit, published mechanism — a
funding payment every 8 hours that pays the cheap side and charges the expensive
one. That gives us two exchange-published anchors we have never had before:

* a **reference/index price** (CF Benchmarks, reachable with the same Kalshi
  credentials), and
* a **funding rate**, including an estimate for the window still forming.

So the question changes shape, from *"what is the true probability"* to *"where is
risk priced differently across two instruments tied to the same underlying"*.
Relative pricing gives far stronger controls than outright forecasting, which is
the strategic reason this experiment exists at all.

## 2. Why this is not a duplicate and not a revival

Checked against the repository's own history before proposing (Research Lab
startup routine):

* **`docs/RESEARCH_JOURNAL.md`, PERPS SURVEY 2026-07-09** surveyed exactly this
  product and recorded a **discovery gap, not a kill**: no perp series was
  reachable through the public `/trade-api/v2` event/market endpoints, 76 open
  Crypto events scanned, every candidate series ticker resolving to zero markets.
  Its recorded next step was, verbatim: *"find Kalshi's perp-specific
  endpoint/feed, then a funding/basis staleness probe gated on normal fees — not a
  series-name guess against the event API."* That condition is what this
  experiment claims has now been met, and **Probe 0 exists to verify the claim
  rather than assume it** (§6).
* **No open experiment covers perps.** No `perp*` strategy tag, package, book
  registry row or thesis existed before this one; the perp family is new to
  Experiment OS.
* **The one adjacent book, THETA, is not this.** Theta prices Kalshi's hourly
  crypto ladders from Coinbase spot candles. Arm C below is deliberately scoped as
  an **overlay measured against Theta**, not a replacement for it, and its gate is
  *incremental* cents over Theta rather than standalone accuracy.

## 3. The shape of the experiment, and why it is one experiment

The operator's instruction was one experiment with three arms, one per candidate
mechanism, run as a horse race: *see which does best, whether several do, or
whether none is sufficient.* That is the registered shape.

It is defensible as one experiment because the three arms share the thing that
actually decides them: **one universe (the Kalshi perp book), one cost model, one
measurement instrument (the tape collector), and one headline metric** — net edge
in basis points of notional after fees, slippage and funding. Three separate
experiments would have made those three separately-chosen quantities, and the
comparison the operator asked for would then rest on an assumption of
comparability rather than on a shared contract.

What it costs, stated plainly: **arms in one version freeze together**. Changing
one arm's rule is a new Version for all three. That is the price of the horse
race and it is accepted deliberately.

### 3.1 Arms

| arm | role | mechanism |
|---|---|---|
| `perprevert` | TREATMENT | premium reversion — trade extreme mark-vs-index divergence back toward the index, funding-confirmed |
| `perpcarry` | TREATMENT | funding dispersion — cross-sectional long/short carry across the perp universe, beta-neutralised |
| `perplead` | TREATMENT | perp→prediction lead/lag — perp microstructure as an information overlay on Kalshi's crypto event contracts |
| `perpctl` | CONTROL | matched random entry: same assets, same timestamps, same notional and same holding period as whichever treatment fired, direction drawn at random |

The control is not decoration and is not the operator's fourth strategy. Every one
of these arms can be made to look profitable by an accidental long-crypto tilt in
a rising sample. `perpctl` is what separates "the mechanism worked" from "crypto
went up during the window": it takes the treatments' own entry times, assets,
sizes and holds, and randomises only the thing under test — the direction the
signal chose.

## 4. The three mechanisms

### Arm A — `perprevert` (premium reversion)

**Hypothesis.** When a Kalshi perp's mark price diverges far from its reference
index relative to its own recent behaviour, the funding mechanism pulls it back,
and the convergence is large enough to pay for fees, spread and any funding paid
while holding.

**Signal.** For each asset, `premium = (mark − index) / index`, and
`premium_z = zscore(premium)` over a trailing 7-day window taken **at the same
distance from the funding settlement**, because premium is mechanically
time-of-cycle dependent and a naive z-score would mostly measure the clock.
Entry requires the live estimated funding rate to agree in sign with the premium.

**Exit.** Premium decays below a pre-registered residual band, OR the z-score
returns inside ±0.5, OR a maximum hold expires, OR the risk stop is hit —
whichever comes first. All four are pre-registered; none is chosen after seeing
a path.

**What would falsify it.** Convergence that is real but smaller than the round
trip costs. This is the single most likely outcome and the probe is built to
measure the *net* number first and the raw one only as a diagnostic.

### Arm B — `perpcarry` (funding dispersion)

**Hypothesis.** Ranking the whole perp universe by funding and going long the
cheapest / short the most expensive earns a carry premium that survives the
relative price moves of the two legs, fees and slippage.

**Construction.** Dollar-neutral at entry, rebalanced on the 8-hour funding
cycle. Then, the part that matters: **beta-neutral, not merely dollar-neutral.**
Each asset's rolling beta to BTC is estimated and the legs are sized so
`Σ(position × β_BTC) ≈ 0`. Without this the book is a hidden long or short on
crypto as a whole wearing a market-neutral label, and that is exactly the class
of hidden exposure a paper sample flatters.

**Headline metric.** Net P&L **minus** the P&L attributable to common crypto
beta. If funding income is +$20 while relative price movement is −$35, "collect
funding" is not an edge and this arm is expected to say so.

### Arm C — `perplead` (perp → prediction lead/lag)

**Hypothesis.** Kalshi's own leveraged perp traders move on information before
Kalshi's short-duration crypto event-contract book reprices, so perp
microstructure carries information about where the ladder is about to go that
Coinbase spot candles alone do not.

**Candidate features**, tested independently before anything is combined:
perp return over a short lookback; buyer/seller trade imbalance; book depth
imbalance; premium impulse `Δ(mark − index)`; open-interest impulse; funding
impulse.

**Metric.** Not accuracy. **Incremental realizable cents per trade over the
registered Theta model** — the repository has learned repeatedly (mmsell6,
mmsell11) that a statistically interesting signal need not survive spread, maker
fills and fees, and an accuracy gate would hide that.

**Deliberate constraint.** Arm C starts as a *collector*, not a trader. The probe
records the joint tape — index → mark → BBO/depth → trades → funding estimate →
OI → event-contract quotes — and measures forward moves at 5s/10s/30s/60s/5m.

## 5. Pre-registered gates

Registered on version 1 before any evidence, and frozen with it. Every quantity
below is a **probe-instrument metric** (`provided=False` in
`kalshi_bot/experiment_os/metrics.py`): probes are validation instruments, not
deployments, so the probe script computes them and the result is recorded against
the gate — the same shape the FREEZE probe used.

**One promotion gate per arm** (`probe_to_paper_perprevert`, `…_perpcarry`,
`…_perplead`), each PROBE → PAPER. This is the horse race made structural: an arm
that clears its own bar can carry into paper without waiting for, or being
rescued by, the other two. The binding rule that goes with it, stated here
because Experiment OS cannot enforce it for us: **the paper deployment registered
after promotion carries only the arms whose own gate PASSed**, plus `perpctl`.

Each promotion gate requires, all of them:

| clause | why |
|---|---|
| `perp_net_edge_bps_per_trade > 0` on the arm | after fees, slippage and funding — the only number that pays for anything |
| `delta.perp_net_edge_bps_per_trade > 0` vs `perpctl` | the mechanism, not the market direction |
| `perp_data_coverage_pct >= 80` (experiment scope) | an estimate speaking for a fifth of the intended tape is not the same claim as one speaking for all of it — the `fill_model_coverage_pct` lesson |
| sample floor: `perp_probe_observations >= 200` per arm | thin-sample HOLD is a correct verdict, not a delay |

Arm B carries one clause the others do not: `perp_beta_adjusted_net_edge_bps > 0`.
Arm B's entire claim is that the edge is not crypto beta, so its gate says so.

Arm C carries one the others do not:
`perp_incremental_cents_per_trade_vs_theta > 0`. Standalone perp signal quality
is not what arm C is for.

A **kill gate**, `perp_probe_stop`, is registered alongside: an arm whose net edge
is materially negative on a sample past the floor is stopped rather than iterated
on, and a data-coverage collapse blocks rather than passes.

**None of this authorizes anything.** A PASS on a probe gate moves the experiment
to PAPER. Real money would be a separate, operator-approved `arm_live_canary`
act, on a successor version, with a pre-registered risk envelope that does not
exist yet — and perps carry leverage and liquidation, which no risk envelope in
this repository has ever had to model.

## 6. The probe, and the cheapest thing that could falsify all of it

**Probe 0 — surface survey (`scripts/perp_surface_survey.py`, ops-runnable).**
Before any strategy question: does the perp surface actually exist where this
document says it does, and is it readable without credentials the ops channel does
not have? The survey walks candidate `/trade-api/v2` perp endpoints, reports
verbatim which resolve and which do not, and — for whatever does resolve — records
the field names actually present rather than the ones assumed here.

This is deliberately the first thing that runs, because §1 and §4 rest on an API
surface **this session could not reach**: outbound HTTPS to Kalshi and to
`docs.kalshi.com` is blocked from the development sandbox, so every endpoint and
field name in this document is stated from the operator's brief and is unverified.
The 2026-07-09 survey is precedent for the failure mode: the product existed and
the API surface assumed for it did not.

If Probe 0 finds no readable perp surface, this experiment stops at PROBE with a
recorded BLOCKED_DATA verdict and no strategy work is done — the same outcome the
2026-07-09 survey reached, reached again cheaply.

**Probe 1 — the tape collector.** Only if Probe 0 resolves a surface. A read-only
collector (`markets / mark / index / BBO / book / trades / OI / funding_estimate /
funding_history`) that places **no orders**, storing the joint tape all three arms
score against. One collector serves all three arms; that is the other reason this
is one experiment.

**Probe 2 — the scorers (`scripts/perp_arm_scores.py`, ops-runnable, written
2026-09-02).** Per-arm backtests over the collected tape, computing exactly the
metrics §5 gates on, against `perpctl` on the same tape. One script rather than the
three this section originally anticipated, because the arms share a tape, a cost
model and a control, and three scripts would have been three chances for those to
drift apart.

It also reports what it **cannot** compute, which on the surface as measured (§7.1)
is most of it. The rule it follows: a quantity that omits an input its registered
definition names is a different quantity and never gets the registered name. So
`perp_net_edge_bps_per_trade` — defined net of funding — is reported NOT PRODUCIBLE,
with an explicitly-named ex-funding figure beside it, and the gate clauses reading
it are reported unreadable rather than read against the substitute.

Cost: Probe 0 is one ops request. Probe 1 is a collector and a table. Probe 2 is one
analysis script. No exposure at any point.

## 7. Known ways this could be wrong

* **The surface may not be reachable** the way §1 assumes. Probe 0 first, for
  exactly this reason.
* **Fees.** Kalshi's crypto products have carried promotional zero-fee periods.
  An edge measured under a promotion dies when fees normalize; the 2026-07-09
  survey flagged this and the gates here are on **net** numbers under the fee
  model the active platform snapshot declares, never a promotional one.

  **This is what closed the experiment (2026-09-02).** Not a promotion expiring — the
  Launch Fee Schedule read at tier 0 is taker 0.120%/side, a **24 bps round trip against a
  measured 8.88 bps spread**. Arm A's real +14.52 bps of gross convergence does not survive
  it. Recorded here rather than only in the workstream because this section is where the
  risk was named in advance, and the pre-registration is worth more if the place it was
  right is visible.
* **Leverage and liquidation** are semantics no book in this repository has ever
  had. They are a platform question, not a strategy one — if perps ever approach
  real money the fee/fill/risk components almost certainly need a Platform
  Revision, which is **Platform Change Review**'s call and not this experiment's.
* **Arm B's beta estimate is itself a model**, and a wrong beta manufactures
  exactly the neutrality it claims to prove. This is why the raw and
  beta-adjusted numbers are both recorded and why only the adjusted one gates.
* **Arm C can leak look-ahead** trivially — the MLBWX probe manufactured a fake
  +5.5¢ edge from settled-price direction. Arm C's forward windows are measured
  strictly from features timestamped before the window opens.

## 7.1 Measured instrument limits (added 2026-08-30, no gate changed)

Two properties of the tape were measured **after** this document was frozen. They
bound what a null result can mean; they change no arm, no metric, no gate clause
and no threshold, and nothing here is to be read as re-interpreting a
pre-registered bar.

* **Sampling cadence is 191.6 s** (measured over 72 h on 2026-09-02; the first hour's
  145 s was optimistic), set by the worker's scan loop rather than by the collector's own
  60 s floor. Ample for arm A, whose premium reverts around 8-hourly funding — arm A
  produced 913 scored round trips over three days. It **bounds arm C**: a perp → ladder
  lead shorter than ~3 minutes cannot be seen at all, so an arm C null is ambiguous
  between "no lead" and "a lead faster than the tape", and must be reported as such
  rather than as a kill on the mechanism. This is not hypothetical any more: the
  2026-09-02 run returned a clean null at 300 s (IC ≈ 0.005 on ~97k pairs) and **refused**
  5/10/30/60 s. The brief's actual claim was a *fast* lead; it remains untested. Arm A's
  and arm B's nulls are unaffected.
* **No funding source exists on this surface — settled 2026-08-30.** Four readings
  agree: `/margin/funding_history` returns `{"funding_history": []}` unscoped, scoped
  to `KXAAVEPERP`, and scoped to `KXBTCPERP` (the largest open interest on the
  exchange) over a 7-day window; and no funding field rides on the market row at all,
  across 24 keys read off 252 live snapshots. The endpoint sits under `/margin/`
  beside positions and balance and requires auth — a market-wide feed has no reason to
  be empty for BTC over seven days; an account payment ledger has exactly one, which
  is that we hold no perp positions. WS-010 D4 is closed on this.

  Two consequences, and both are results rather than repairs:

  - **Arm B `perpcarry` is BLOCKED_DATA.** `perp_funding_capture_bps` has no input.
    The arm is **not** deleted and **not** re-scoped: it registers as frozen, reaches
    its gate, and fails to produce evidence — a pre-registration working, not
    breaking. Ranking on *premium* instead is a different hypothesis, so a new Version
    and an operator decision.
  - **Arm A loses its entry confirmation, not its signal.** §4 pre-registers "entry
    requires the live estimated funding rate to agree in sign with the premium". That
    condition cannot be evaluated. The signal itself is unaffected —
    `premium = (mark − index) / index` comes from two fields that both ride on the
    market row and are in the tape now. Arm A therefore proceeds **without** the
    confirmation, and Probe 2 must say so in its result: a pre-registered condition
    that could not be evaluated is not the same experiment as one that was evaluated
    and passed, and the difference belongs in the record rather than in a footnote.

* **The fee schedule is unreadable without credentials.** `/margin/fee_tiers` answers
  401 to the ops runner, which holds no Kalshi key by design. Every gate here is on a
  **net** number, so a guessed fee would decide a promotion. Probe 2 therefore reports
  each arm's **breakeven round-trip fee** — the cost at which its measured edge is
  exactly zero — rather than picking one. That is a number an operator can check
  against a real schedule; a guess is a number nobody can check. As of 2026-09-02 that
  number is **5.63 bps for arm A**, which turns the fee from a caveat into this
  experiment's cheapest decisive question: the ops runner holds no key, but the worker
  does, and one authenticated read settles whether arm A is interesting or dead
  (WS-010 D7).

* **Coverage is measured against the intended cadence, not the achieved one.**
  `perp_data_coverage_pct` is a clause on all three promotion gates and the stop
  gate's `hold_if`. Measured against what the collector actually managed it is 100%
  by construction and says nothing, so Probe 2 divides by the configured
  `PERPS_INTERVAL_SECONDS`. Measured 2026-09-02: **29.61%** — 1279 cycles against 4320
  intended, at an achieved 191.6 s. Per-cycle coverage is 100% with zero errors, so this
  is cadence and not a collector fault. It is well under the registered 80% bar, which
  means **no arm can PASS on the current tape whatever its edge**. Closing that gap is a
  **platform** change (giving the collector its own cadence instead of the shared worker
  loop), not a scorer change; lowering the floor after seeing 29.61% would be re-tuning a
  pre-registered gate against results.

## 8. Where the standing lives

Not here. `xos show perp-v1`, `xos scoreboard`, `xos control-tower`. This document
is not updated with results; a changed question is a new Version and a changed
world is a new epoch.
