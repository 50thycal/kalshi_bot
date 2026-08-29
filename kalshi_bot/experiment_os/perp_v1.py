"""The PERP-V1 registration package: Kalshi crypto perpetual futures, three
mechanisms, one horse race.

This module is DATA plus one function. It places no orders, arms nothing on
import, opens no exposure and is not wired into the trading worker or the
read-only ops channel. Nothing here runs until an operator submits a
`REGISTER_PACKAGE` envelope naming `perp-v1`.

Scientific contract: `docs/PERP_V1_THESIS.md`. That document is what the gates
below were written against; this module is the executable form of it, and the two
disagreeing is a bug here.

WHY ONE EXPERIMENT WITH THREE TREATMENT ARMS
--------------------------------------------
The operator's instruction was one experiment, three arms, one per candidate
mechanism, run as a horse race — which of premium reversion, funding carry and
perp→prediction lead/lag earns its keep, whether several do, or whether none is
sufficient.

That is a legitimate single experiment because the three arms share the things
that decide them: one universe (Kalshi's perp book), one cost model, one
measurement instrument (the tape collector of `docs/PERP_V1_THESIS.md` §6), and
one headline quantity — net edge in basis points of notional after fees, slippage
and funding. Registered as three separate experiments those would be three
separately-chosen quantities and the comparison would rest on an assumption of
comparability instead of on a shared frozen contract.

The price, stated because it is real: **arms freeze together.** Changing one
arm's rule is a new Version for all three. Accepted deliberately.

WHY THERE IS A FOURTH ARM
-------------------------
`perpctl` is not a fourth strategy and is not padding. Every one of these
mechanisms can be made to look profitable by an accidental long-crypto tilt in a
rising sample; arm B's entire claim is that its edge is *not* that. The control
takes the treatments' own entry times, assets, notionals and holding periods and
randomises only the thing under test — the direction the signal chose. Without
it `delta.perp_net_edge_bps_per_trade` has nothing to resolve against and the
horse race measures the crypto tape.

WHY NO DEPLOYMENT AND NO STRATEGY TAGS
--------------------------------------
This registers a PROBE, and a probe is a validation instrument, not a deployment.
No `perp*` tag is created, so nothing becomes admissible to the write path under
NEW_ONLY — which is the correct state for an experiment whose data source this
repository has never once successfully read (§6 of the thesis: the 2026-07-09
survey found the product real and the assumed API surface absent). Arms get
concrete tags when, and only when, a probe gate PASSes and a paper deployment is
registered.

WHAT A PASS HERE WOULD AND WOULD NOT AUTHORIZE
----------------------------------------------
A PASS on a probe gate moves the experiment to PAPER. It authorizes no real
money, and this package cannot arm anything — it has no `arm` function, so
`ARM_CANARY` aimed at it has nothing to call. Perps carry leverage and
liquidation, which no risk envelope in this repository has ever had to model;
that is a Platform Change Review question before it is ever a promotion question.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "perp-v1"

#: Arm keys. Kept short and prefix-distinct: `LIVE_STRATEGIES` matches by PREFIX,
#: so a future live tag derived from one of these must not be a prefix of another.
ARM_REVERT = "perprevert"
ARM_CARRY = "perpcarry"
ARM_LEAD = "perplead"
ARM_CONTROL = "perpctl"

TREATMENT_ARMS = (ARM_REVERT, ARM_CARRY, ARM_LEAD)

THESIS_DOC = "docs/PERP_V1_THESIS.md"

#: The evidence floor, in scored round trips (arms A/B) or scored event-contract
#: decisions (arm C). Below it the correct verdict is HOLD, not a thin PASS.
SAMPLE_FLOOR = 200

#: The tape-completeness floor. Read every perp number against its coverage: an
#: estimate speaking for a fifth of the intended tape is not the same claim as one
#: speaking for all of it. This is the `fill_model_coverage_pct` lesson applied
#: before the first number exists rather than after a promotion turns out to have
#: rested on one.
COVERAGE_FLOOR_PCT = 80


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _promotion_gate_spec(
    arm: str, extra_clauses: list[dict], description: str,
    sample_floor: int = SAMPLE_FLOOR,
) -> dict:
    """One arm's PROBE→PAPER bar.

    Three clauses are common to all three arms and none of them is negotiable:

      * the arm's own NET edge is positive — after fees, slippage AND the funding
        paid or received while holding. Gross convergence is a diagnostic, never
        a bar; the most likely honest outcome of this whole experiment is a real
        effect smaller than its round trip;
      * the arm beats `perpctl` on the same tape — the mechanism, not the market;
      * the tape is actually there (`perp_data_coverage_pct`), experiment-scoped
        because coverage is a property of the collector, not of an arm.

    The sample floor is the gate's `sample`, so a thin sample renders HOLD rather
    than a lucky PASS.
    """
    return {
        "description": description,
        "sample": {
            arm: {"metric": "perp_probe_observations", "op": ">=",
                  "value": int(sample_floor)},
        },
        "pass_all": [
            {"metric": "perp_net_edge_bps_per_trade", "arm": arm,
             "op": ">", "value": 0},
            {"metric": "delta.perp_net_edge_bps_per_trade", "treatment": arm,
             "control": ARM_CONTROL, "op": ">", "value": 0},
            {"metric": "perp_data_coverage_pct", "scope": "experiment",
             "op": ">=", "value": COVERAGE_FLOOR_PCT},
            *extra_clauses,
        ],
    }


#: The four gate specs, built together so the sample floor stays consistent across
#: them. `sample_floor` is the one knob the REGISTER_PACKAGE envelope may turn, and
#: only upward (see `register`): a floor can make a gate stricter and can never make
#: it pass on less evidence.
#:
#: Arm A carries the common bar and nothing more. Premium reversion's claim is
#: exactly "the convergence pays for the round trip", which the common clauses
#: already state; an extra clause would be a second, weaker way to say it.
#:
#: Arm B adds the clause that IS its hypothesis. Dollar-neutral is not neutral:
#: DOGE does not carry BTC's sensitivity, so a dollar-flat book can be a large net
#: long wearing a market-neutral label. The gate reads the beta-adjusted number,
#: and the raw one is recorded beside it so the gap is visible, not inferred.
#:
#: Arm C's bar is incremental, not standalone. A perp signal that predicts the
#: crypto ladder better than Theta does but cannot be traded through the spread at
#: maker fill rates is the mmsell6/mmsell11 mirage in a new instrument, and an
#: accuracy gate would hide it.
#:
#: The stop is registered before any evidence for the same reason every keep gate
#: in this repository is: a threshold chosen after seeing a path is not a
#: threshold. Its `min_evidence` is deliberately a FIFTH of the promotion floor and
#: not the floor itself — one number for both would leave an arm that is clearly
#: and materially losing sitting at HOLD until it had gathered a full promotion
#: sample. −25 bps per round trip is not a tuned number and is not claimed to be
#: one: it is roughly an order of magnitude worse than any edge these mechanisms
#: could plausibly earn, so tripping it means the mechanism is absent or the scorer
#: is wrong, and either is a stop rather than an iteration.
def gate_specs(sample_floor: int = SAMPLE_FLOOR) -> tuple[tuple[str, dict, str], ...]:
    revert = _promotion_gate_spec(
        ARM_REVERT, [],
        "PERP-V1 arm A (premium reversion): net convergence edge after fees, "
        "slippage and funding, beating a matched random-direction control on the "
        "same tape.",
        sample_floor=sample_floor,
    )
    carry = _promotion_gate_spec(
        ARM_CARRY,
        [{"metric": "perp_beta_adjusted_net_edge_bps", "arm": ARM_CARRY,
          "op": ">", "value": 0}],
        "PERP-V1 arm B (funding dispersion): cross-sectional carry that survives "
        "fees, slippage, the relative moves of its own legs AND the removal of "
        "common crypto beta. Funding income with a larger relative-price loss is "
        "the failure mode, not the edge.",
        sample_floor=sample_floor,
    )
    lead = _promotion_gate_spec(
        ARM_LEAD,
        [{"metric": "perp_incremental_cents_per_trade_vs_theta", "arm": ARM_LEAD,
          "op": ">", "value": 0}],
        "PERP-V1 arm C (perp -> prediction lead/lag): realizable cents per trade "
        "the perp overlay adds OVER the existing Theta spot model, not standalone "
        "signal quality.",
        sample_floor=sample_floor,
    )
    stop = {
        "description": (
            "PERP-V1 probe stop: an arm this far under water on a sample past the "
            "early floor is stopped, not iterated on. Coverage collapse blocks "
            "rather than passes — a number computed over a tenth of the intended "
            "tape is not evidence of anything."
        ),
        "fail_any": [
            {"metric": "perp_net_edge_bps_per_trade", "arm": arm, "op": "<=",
             "value": -25.0,
             "min_evidence": {"metric": "perp_probe_observations", "op": ">=",
                              "value": max(1, int(sample_floor) // 5)}}
            for arm in TREATMENT_ARMS
        ],
        "hold_if": [
            {"metric": "perp_data_coverage_pct", "scope": "experiment", "op": "<",
             "value": COVERAGE_FLOOR_PCT},
        ],
    }
    return (
        (f"probe_to_paper_{ARM_REVERT}", revert, "promotion"),
        (f"probe_to_paper_{ARM_CARRY}", carry, "promotion"),
        (f"probe_to_paper_{ARM_LEAD}", lead, "promotion"),
        ("perp_probe_stop", stop, "kill"),
    )


# ---------------------------------------------------------------------------
# The arms
# ---------------------------------------------------------------------------

ARMS: tuple[dict, ...] = (
    {
        "arm_key": ARM_REVERT,
        "role": ArmRole.TREATMENT,
        "description": (
            "premium reversion: enter against an extreme mark-vs-index divergence "
            "when the live estimated funding rate agrees in sign, exit on decay of "
            "the premium, on the z-score returning inside the band, on the maximum "
            "hold, or on the risk stop"
        ),
        "params": {
            "signal": "premium_z",
            "premium": "(mark - index) / index",
            "zscore_window_days": 7,
            # Premium is mechanically dependent on where the funding cycle is, so a
            # naive trailing z-score would mostly measure the clock. The window is
            # taken at a MATCHED distance from settlement.
            "zscore_conditioning": "matched time-to-funding bucket",
            "entry_abs_z": 2.5,
            "funding_confirmation": "estimated funding rate agrees in sign with the premium",
            "exit_abs_z": 0.5,
            "exit_residual_premium_bps": 5,
            "max_hold_funding_windows": 1,
        },
    },
    {
        "arm_key": ARM_CARRY,
        "role": ArmRole.TREATMENT,
        "description": (
            "funding dispersion: long the bottom-quartile funding names, short the "
            "top-quartile, rebalanced on the 8-hour funding cycle, sized so the "
            "book's BTC beta nets to approximately zero"
        ),
        "params": {
            "rank_on": "estimated_8h_funding_rate",
            "long_bucket": "bottom_quartile",
            "short_bucket": "top_quartile",
            "rebalance": "each 8h funding settlement",
            # Dollar-neutral is the starting construction; beta-neutral is the
            # contract. The difference is the arm's entire scientific claim.
            "neutrality": "beta_neutral",
            "beta_reference": "BTC",
            "beta_estimator": "rolling regression of asset returns on BTC returns",
            "premium_confirmation": "optional secondary; recorded, not gated",
        },
    },
    {
        "arm_key": ARM_LEAD,
        "role": ArmRole.TREATMENT,
        "description": (
            "perp -> prediction lead/lag: perp microstructure as an overlay on the "
            "existing Theta probability model for Kalshi's short-duration crypto "
            "ladders. Features are tested independently before any are combined"
        ),
        "params": {
            "features": [
                "perp_return_short_lookback",
                "trade_imbalance",
                "book_depth_imbalance",
                "premium_impulse",
                "open_interest_impulse",
                "funding_impulse",
            ],
            "forward_horizons_sec": [5, 10, 30, 60, 300],
            "baseline": "registered Theta spot-vol probability model",
            # The MLBWX probe manufactured a +5.5c edge by taking direction from the
            # settled price. Every feature here is timestamped strictly before the
            # forward window it is scored against.
            "look_ahead_control": "features timestamped strictly before the forward window opens",
        },
    },
    {
        "arm_key": ARM_CONTROL,
        "role": ArmRole.CONTROL,
        "description": (
            "matched random-direction control: the treatments' own entry timestamps, "
            "assets, notionals and holding periods, with the direction drawn at "
            "random. Separates 'the mechanism worked' from 'crypto moved'"
        ),
        "params": {
            "matched_on": ["asset", "entry_timestamp", "notional", "holding_period"],
            "randomised": "direction",
            "draws_per_matched_entry": 1,
        },
    },
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Create `perp-v1`, register and freeze version 1 with its four arms and four
    gates, open v1/e1 on the ACTIVE platform snapshot, and walk IDEA → PROBE.

    Arms nothing. Places nothing. Creates no strategy tag, so nothing becomes
    admissible to the trading write path. Returns the objects for inspection.

    Idempotence is by refusal rather than by a no-op: an experiment key that
    already exists means either this package already ran or something else claimed
    the name, and both deserve a REJECTED receipt an operator can read rather than
    a silent success that hides which.
    """
    # The transport always passes this field; it may only RAISE the declared floor.
    # A lower one would let an arm promote on a thinner sample than the reviewed
    # contract asks for, which is the one direction an envelope must never move a
    # pre-registered bar.
    floor = SAMPLE_FLOOR if promotion_sample_floor is None else int(promotion_sample_floor)
    if floor < SAMPLE_FLOOR:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is below the reviewed floor "
            f"{SAMPLE_FLOOR} — an envelope may make a pre-registered bar stricter, "
            "never weaker"
        )

    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package has "
            "already been registered, or the key is taken"
        )

    experiment = service.create_experiment(
        session,
        key=EXPERIMENT_KEY,
        origin="operator",
        title="Perpetual futures V1 — three mechanisms, one horse race",
        family="perp",
        hypothesis=(
            "Kalshi's crypto perpetual futures expose two exchange-published "
            "anchors this repository has never had — a reference index price and "
            "an 8-hourly funding mechanism. At least one of premium reversion, "
            "cross-sectional funding carry, or perp-microstructure lead/lag into "
            "the crypto event-contract book earns a net edge, after fees, slippage "
            "and funding, that a matched random-direction control does not."
        ),
        mechanism=(
            "A perp is tied to its index by an explicit funding payment that pays "
            "the cheap side and charges the expensive one. The question therefore "
            "changes from 'what is the true probability' — the question behind "
            "every book this repository has run and most of its graveyard — to "
            "'where is risk priced differently across two instruments tied to the "
            "same underlying'. Relative pricing admits far stronger controls than "
            "outright forecasting."
        ),
        counterparty=(
            "leveraged crypto perp flow, and (arm C) the slower event-contract book "
            "on the same underlying"
        ),
        falsification=(
            "Any arm whose NET edge after fees, slippage and funding fails to beat "
            "the matched random-direction control on the same tape is not promoted, "
            "however large its gross convergence or funding income. If the perp API "
            "surface proves unreadable from the ops channel, the experiment stops at "
            "PROBE with BLOCKED_DATA and no strategy work is done."
        ),
        universe="Kalshi crypto perpetual futures; arm C additionally the Kalshi "
                 "short-duration BTC/ETH event-contract ladders",
        docs={"thesis": THESIS_DOC},
        notes=(
            "Not a revival. docs/RESEARCH_JOURNAL.md PERPS SURVEY 2026-07-09 recorded "
            "a DISCOVERY GAP, not a kill: the product existed and no perp series was "
            "reachable through the public event/market endpoints. Its recorded next "
            "step was to find a perp-specific endpoint and then probe funding/basis "
            "gated on normal fees. This experiment claims that condition is now met "
            "and Probe 0 exists to verify the claim rather than assume it."
        ),
        actor=actor,
        now=at,
    )

    version = service.create_experiment_version(
        session,
        experiment,
        hypothesis=experiment.hypothesis,
        mechanism=experiment.mechanism,
        counterparty=experiment.counterparty,
        falsification=experiment.falsification,
        universe_selector=(
            "the Kalshi perpetual universe as the surface survey reports it, "
            "restricted to assets whose index, mark, book and funding history are "
            "ALL readable — an asset missing any leg cannot be scored and is "
            "excluded rather than partially estimated"
        ),
        universe_exclusions=(
            "assets with no readable reference index (the premium is undefined "
            "without one) and assets with no funding history (the carry ranking "
            "and the net-of-funding P&L are both undefined without it)"
        ),
        entry_rule="per arm — see each arm's declared params",
        exit_rule=(
            "arms A and C exit on their own pre-registered conditions; arm B "
            "rebalances on the 8-hour funding settlement. Every exit condition is "
            "declared before evidence; none is chosen after seeing a path"
        ),
        sizing_rule=(
            "probe stage: notional is a scoring unit, not an exposure. Arms A and C "
            "score a fixed notional per entry; arm B scores dollar-neutral legs "
            "rescaled to beta-neutral. No capital is committed at this stage and no "
            "leverage is modelled — leverage and liquidation are platform semantics "
            "this repository does not yet have"
        ),
        execution_style="probe instrument (no orders); cost model applies taker "
                        "crossing costs unless a resting fill is demonstrable",
        independent_variable=(
            "which perp-native mechanism generates the entry: premium reversion, "
            "cross-sectional funding carry, or perp microstructure. Everything else "
            "— universe, cost model, tape, control construction and headline metric "
            "— is held constant across the three, which is what makes the horse "
            "race a comparison rather than three unrelated readings"
        ),
        held_constant=[
            "universe (the readable perp assets)",
            "cost model (fees + slippage + funding, under the active platform snapshot)",
            "measurement instrument (one tape collector serves all three arms)",
            "headline metric (net edge in bps of notional)",
            "control construction (matched entries, randomised direction)",
        ],
        control_required=True,
        metrics={
            "headline": "perp_net_edge_bps_per_trade",
            "vs_control": "delta.perp_net_edge_bps_per_trade",
            "arm_specific": {
                ARM_CARRY: "perp_beta_adjusted_net_edge_bps",
                ARM_LEAD: "perp_incremental_cents_per_trade_vs_theta",
            },
            "diagnostics": [
                "perp_funding_capture_bps",
                "perp_signal_ic",
                "perp_data_coverage_pct",
            ],
            "unit_note": (
                "perp P&L is in BASIS POINTS OF NOTIONAL, not cents per contract: a "
                "perp position has no contract face value to divide by, and reusing "
                "the event-contract unit would make the two families' numbers look "
                "poolable when they are not"
            ),
        },
        sample={
            "unit": "one scored round trip (arms A/B); one scored event-contract "
                    "decision (arm C)",
            "floor_per_arm": floor,
            "coverage_floor_pct": COVERAGE_FLOOR_PCT,
        },
        costs={
            "model": "the fee model declared by the epoch's pinned platform snapshot",
            "funding": "funding paid or received while holding is a COST, netted "
                       "into the headline metric, never reported only as income",
            "slippage": "taker crossing cost from the recorded book unless a "
                        "resting fill is demonstrable on the tape",
            "promotional_fees": (
                "Kalshi's crypto products have carried zero-fee promotions. An edge "
                "measured under one dies when fees normalize — the 2026-07-09 survey "
                "flagged exactly this — so no gate here reads a promotional fee level"
            ),
        },
        provenance={
            "perp_market_data": "Kalshi perpetual endpoints, as resolved by the "
                                "surface survey (scripts/perp_surface_survey.py)",
            "reference_price": "CF Benchmarks index as exposed by Kalshi",
            "event_contract_data": "existing Kalshi event-contract tape (arm C only)",
            "unverified_at_registration": (
                "the perp API surface could not be reached from the development "
                "sandbox (outbound HTTPS to Kalshi and to its docs is blocked), so "
                "every endpoint and field name behind this contract is stated from "
                "the operator brief and is UNVERIFIED. Probe 0 verifies it; the "
                "2026-07-09 survey is the precedent for the failure mode"
            ),
        },
        monitoring={
            "coverage": "perp_data_coverage_pct is read beside every perp number, "
                        "never after it",
            "stop": "perp_probe_stop",
        },
        docs={"thesis": THESIS_DOC,
              "journal_precedent": "docs/RESEARCH_JOURNAL.md (PERPS SURVEY 2026-07-09)"},
        now=at,
    )

    for arm in ARMS:
        service.add_arm(
            session, version,
            arm_key=arm["arm_key"], role=arm["role"],
            description=arm["description"], params=arm["params"],
            # Deliberately no strategy_tag: a probe is an instrument, not a
            # deployment, and an unregistered tag is exactly what NEW_ONLY should
            # keep out of the write path until an arm has earned a paper book.
            strategy_tag=None,
        )

    gates = []
    for gate_key, spec, kind in gate_specs(floor):
        gates.append(service.register_gate(
            session, version,
            gate_key=gate_key, kind=kind, spec=spec,
            from_state=LifecycleState.PROBE if kind == "promotion" else None,
            to_state=LifecycleState.PAPER if kind == "promotion" else None,
            registered_at=at,
            notes=("pre-registered before any perp tape exists — there is no result "
                   "yet that a threshold could have been chosen to fit"),
        ))

    service.freeze_version(session, version, now=at)
    # Evidence begins at the contract boundary. Nothing has been collected, so this
    # costs nothing and buys the property that any tape gathered before the contract
    # existed can never be pooled into it.
    for gate in gates:
        service.mark_gate_evidence_started(session, gate, at=at)

    epoch = service.open_epoch(
        session, version,
        reason=(
            "PERP-V1's first operating interval, pinned to the platform snapshot "
            "active at registration. The fee model in that snapshot is what the "
            "cost gates read; a promotional fee level would be a different world "
            "and therefore a different epoch."
        ),
        started_at=at,
    )

    transition = service.transition_experiment(
        session, experiment, LifecycleState.PROBE,
        actor=actor,
        reason=(
            "Contract frozen and gates pre-registered; the probe programme "
            "(surface survey, then tape collector, then the three scorers) can "
            "begin. No tag, no deployment, no exposure."
        ),
        occurred_at=at,
        version=version,
        epoch=epoch,
    )

    return {
        "experiment": experiment,
        "version": version,
        "epoch": epoch,
        "gates": gates,
        "transition": transition,
        "arms": [a["arm_key"] for a in ARMS],
    }
