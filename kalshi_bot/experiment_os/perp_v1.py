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

from . import perp_v1_floors as _floors
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

#: The frozen numbers live in `perp_v1_floors`, a module that imports nothing, and
#: are re-exported here so this package stays the name callers reach for. Probe 2
#: (`scripts/perp_arm_scores.py`) runs on the ops runner, which installs psycopg and
#: nothing else — it cannot import this module, because `service` above brings
#: SQLAlchemy. It imports the floors module directly instead of carrying its own
#: copy of a registered bar. See that module's docstring.
SAMPLE_FLOOR = _floors.SAMPLE_FLOOR
COVERAGE_FLOOR_PCT = _floors.COVERAGE_FLOOR_PCT
REGISTERED_HORIZONS_SEC = _floors.REGISTERED_HORIZONS_SEC


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
            "forward_horizons_sec": list(REGISTERED_HORIZONS_SEC),
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
            "this repository does not yet have. Execution style is recorded as "
            "`taker` because the cost model applies crossing costs; the probe "
            "places no orders at all, and a resting fill is never assumed"
        ),
        # `taker`, not prose: the column is String(16) and its vocabulary is
        # maker|taker|mixed. The qualification this used to carry inline — that the
        # probe places no orders and only MODELS taker crossing costs — belongs in
        # `sizing_rule` above, which has room for it. Postgres refused the sentence
        # (DataError, varchar(16)) on the first production run; SQLite had accepted
        # it in every test, because SQLite does not enforce VARCHAR lengths.
        execution_style="taker",
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


# ---------------------------------------------------------------------------
# Retrospective close-out
# ---------------------------------------------------------------------------

#: The three arm verdicts and the reasoning behind each, frozen here as reviewed
#: code rather than passed in an envelope. An envelope that could choose verdicts
#: would make the transport, not the evidence, the thing that decides what happened.
#:
#: `FAIL` rather than a bespoke "FAIL_EXECUTION_ECONOMICS": `GateVerdict` has no such
#: value, and adding one to a shared enum to describe one experiment's cause of death
#: is a platform change that no evidence calls for. The cause lives in the
#: explanation, where prose belongs, and the enum keeps meaning what it meant.
CLOSE_OUT_VERDICTS: tuple[tuple[str, str, str], ...] = (
    (
        f"probe_to_paper_{ARM_REVERT}",
        "FAIL",
        "FAIL on execution economics, not on signal. 913 scored round trips over 72h: "
        "+14.52 bps gross convergence, 8.88 bps measured spread, beating a matched "
        "random-direction control by +15.76 bps — the mechanism is real. It dies on "
        "fee: Kalshi's tier-0 perp taker fee is 0.120%/side, a 24 bps round trip "
        "against an 8.88 bps bid-ask, so the toll is 2.7x the entire spread. Every "
        "execution combination is negative except both-legs-passive, which is the one "
        "least likely to fill (a resting order at an extreme premium fills only when "
        "the premium widens further). Thesis §7 pre-registered this outcome.",
    ),
    (
        f"probe_to_paper_{ARM_CARRY}",
        "BLOCKED_DATA",
        "No funding source exists on this surface. /margin/funding_history returns an "
        "empty list unscoped, scoped to KXAAVEPERP, and scoped twice to KXBTCPERP "
        "(largest open interest) over a 7-day window; no funding field rides on the "
        "market row across 24 keys on 252 live snapshots. This arm ranks its whole "
        "universe on funding, so it has no input. Not re-scoped to a premium proxy — "
        "that would be a different hypothesis wearing this arm's registered gate.",
    ),
    (
        f"probe_to_paper_{ARM_LEAD}",
        "HOLD",
        "HOLD, not FAIL: operator NO-GO on 2026-09-02 with the mechanism UNTESTED at "
        "the horizon it claimed. Clean null at 300s (IC ~0.005 on ~97k pairs; overlay "
        "-0.02 c/trade vs the Theta baseline), but the registered 5/10/30/60s horizons "
        "are unobservable and were refused rather than reported as nulls. The binding "
        "constraint was theta_interval_minutes=5.0 — the event-contract ladder cadence, "
        "not the perp collector — since this arm scores the ladder's forward move. A "
        "FAIL would claim a falsification the evidence does not support.",
    ),
    (
        "perp_probe_stop",
        "HOLD",
        "The stop gate's fail clauses read perp_net_edge_bps_per_trade, which was NOT "
        "PRODUCIBLE (it is defined net of funding, and funding is unreachable). Its "
        "hold_if on perp_data_coverage_pct is readable and was failing: 29.61% against "
        "a registered 80% floor, at an achieved 191.6s cadence versus 60s intended. "
        "The collector never erred — it shares the trading worker's scan loop.",
    ),
)


def close_out_retrospective(
    session, *, actor: str, approved_by: str, reason: str,
    now: datetime | None = None,
) -> dict:
    """Record PERP-V1 as the closed, failed experiment it is, and retire it — in one
    act, having never been registered while it ran.

    Registering it while it ran would have meant redeploying the trading worker for a
    probe that could not trade, so it never was, and the documents became its only
    durable record. This writes that record down where it belongs, late and visibly
    late: every row is stamped at close-out time, so the lateness is legible in the
    timestamps rather than disguised.

    It reuses `register()` for the contract, so the arms and gates are the ones that
    were actually pre-registered rather than a retyped copy that could drift from
    `docs/PERP_V1_THESIS.md`. Then it records the three arm verdicts plus the stop
    gate's, and retires.

    Authorizes nothing, and cannot: `service.close_out_retrospective` refuses a PASS
    verdict outright, refuses any target but RETIRED, and refuses an experiment
    holding deployments. This one holds none — no `perp*` strategy tag was ever
    created, which is why the experiment could sit unregistered under NEW_ONLY
    without any risk of it trading.
    """
    at = now or _now()
    produced = register(session, actor=actor, now=at)
    gates_by_key = {g.gate_key: g for g in produced["gates"]}

    missing = [k for k, _, _ in CLOSE_OUT_VERDICTS if k not in gates_by_key]
    if missing:
        raise service.ExperimentOsError(
            f"close-out names gates the registered contract does not have: {missing} "
            "— the verdict table and the gate specs have drifted apart"
        )
    unjudged = sorted(set(gates_by_key) - {k for k, _, _ in CLOSE_OUT_VERDICTS})
    if unjudged:
        raise service.ExperimentOsError(
            f"close-out leaves gates without a verdict: {unjudged} — a retired "
            "experiment with a silent gate is the fragmentation this is meant to end"
        )

    closed = service.close_out_retrospective(
        session,
        produced["experiment"],
        verdicts=[
            (gates_by_key[key], verdict, explanation)
            for key, verdict, explanation in CLOSE_OUT_VERDICTS
        ],
        actor=actor,
        approved_by=approved_by,
        reason=reason,
        evidence_ref=THESIS_DOC,
        epoch=produced["epoch"],
        now=at,
    )
    return {**closed, "arms": produced["arms"]}
