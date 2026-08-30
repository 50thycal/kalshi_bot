# WS-010 — PERP-V1: a research surface for Kalshi perpetual futures

**Phase:** BUILDING
**Status:** Active
**Created:** 2026-08-29
**Updated:** 2026-08-29

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

- **D1. Where does the tape collector run?** Probe 0 decides it. If the perp
  surface is unauthenticated, the collector can live in the ops channel; if it is
  `EXISTS/AUTH`, it must run on the worker, which is the only process holding
  Kalshi credentials — a bigger change (a new collector loop, a new table, a
  migration) and a separate build slice.
- **D2. Does a perp book need a Platform Revision before it could ever be
  live?** Almost certainly yes — leverage, liquidation and an 8-hourly funding
  cash flow are semantics no FEE_MODEL/FILL_MODEL revision in this repository
  describes. That is **Platform Change Review**'s call, not this workstream's, and
  it is not on the critical path while PERP-V1 stays at PROBE.
- **D4. If funding is unreadable, what happens to `perpcarry`?** Nothing is
  frozen yet — the contract is written but not registered — so this is the last
  cheap moment to decide. Options: (a) register as-is and let arm B evaluate
  BLOCKED_DATA, which is honest and costs an arm; (b) re-scope arm B to a
  premium-dispersion ranking, which is a different hypothesis and should be
  admitted as one rather than smuggled in under the same arm key; (c) hold
  registration until the extended Probe 0 run answers. Recommendation: (c), then
  (a) if funding really is absent — an arm that cannot be scored should say so
  through the evaluator rather than be quietly replaced by a different question.
- **D3. Arm C's control.** `perpctl` is a perp-side control and does not by itself
  answer "better than Theta". The gate uses an incremental-over-Theta metric
  instead. Whether a first-class `external_control` reference to the Theta
  experiment is worth registering at PAPER is deferred until arm C has evidence —
  a cross-experiment delta is BLOCKED_PLATFORM whenever the two epochs pin
  different snapshots, which `mmsell-anchor-vol-entry` is currently demonstrating.

## Assumptions

- **A1 — TESTED 2026-08-29, largely CONFIRMED.** Probe 0 ran through the ops
  channel. `/margin/markets`, `/margin/markets/{ticker}` and its `/orderbook` are
  readable **unauthenticated**; positions, balance, fills and fee tiers exist and
  need credentials; tickers look like `KXAAVEPERP`. Critically, `reference_price`
  rides on the market row, so arm A's index anchor exists. Findings recorded in
  `docs/RESEARCH_JOURNAL.md` (PERP-V1 PROBE 0 RESULT 2026-08-29). What A1 got
  wrong is funding — see A4.
- **A4 — OPEN, and it is the one that bites.** Funding was assumed readable at
  `/margin/funding_rates` and `/margin/funding_rate_estimate`; both 404, as does
  the per-market variant. Funding is arm B's entire ranking, arm A's entry
  confirmation, and a cost netted into every arm's headline metric. Two 404s
  against two names from a brief is not proof of absence, so Probe 0 was extended
  to hunt alternatives and to dump the market row's full field set. If funding
  proves genuinely unreadable, **arm B is unscoreable as written** and that is an
  operator decision on the contract (D4), not something to work around.
- **A2.** Funding is published for the forming window, not only historically. Arm
  A's funding confirmation and arm B's ranking both depend on it.
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

Slices 2 and 3 (Probe 1 collector, Probe 2 scorers) are **blocked on Probe 0's
result** and are deliberately not designed yet: a collector written against
assumed field names is the same error as a probe written against guessed series
tickers.

## Implementation State

Slice 1 merged (#275). Probe 0 has RUN once and returned (journal, 2026-08-29): the perp
surface is real and readable, funding is not where the brief said. Probe 0 is being
re-run with a corrected classifier and a wider funding hunt. Registration in production
has **not** been submitted and should now wait on **D4**.

## Review State

Not started.

## Related Decisions

`DEC-001` (the authority boundary — this file links to `perp-v1`, and never restates
its standing or its gate reads). `DEC-008` (why the three mechanisms are arms of one
experiment rather than three experiments, and why each arm carries its own gate).

## Related PRs

This PR.

## Next Step

After this PR merges: re-run `{"type":"script","name":"perp_surface_survey"}` and read
whether funding is reachable anywhere (A4). That answer decides **D4**, and D4 should be
settled before `REGISTER_PACKAGE` freezes the contract.
