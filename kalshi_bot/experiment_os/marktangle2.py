"""MARKTANGLE-2 — the conditional-dependence experiment package.

Spec (pre-registration): `docs/MARKTANGLE_2_SPEC.md`. Instrument:
`scripts/marktangle2_probe.py`. This module is DATA plus one function. It
registers a scientific contract; it arms nothing, deploys no tag and places no
order. The PROBE deployment it registers carries **no tags on purpose** — the
probe is an offline historical scan, and under NEW_ONLY nothing here can reach
the exchange.

WHY A SEPARATE EXPERIMENT, NOT A VERSION OF MARKTANGLE-1
-------------------------------------------------------
MARKTANGLE-1 asked one question — does reversal probability rise with run
length — on single families, and it is frozen at HOLD with a 100-entry holdout
floor that this experiment must not lower, widen or reinterpret. MARKTANGLE-2
asks a different question (does the price already carry whatever serial
dependence exists, in EITHER direction), on a different unit (a preregistered
homogeneous class with family effects; a state-duration process with the
underlying's distance to the strike), with different arms and different
floors. That is a new question, so it is a new experiment with MARKTANGLE-1 as
its recorded predecessor, never a new epoch or version of it.

WHY THESE ARMS
--------------
Two tracks that may not rescue each other, each with the same skeleton:

  * an independence BASELINE (family base rate) that every treatment must beat
    on calibration AND on money — the arm that answers "does serial
    information add anything at all";
  * treatments that add one thing at a time: the previous outcome (A1/B1),
    the streak length or state duration (A2/B2), and the structural pooling
    (A3, ridge family effects) or the underlying's normalized distance to the
    strike (B3);
  * one MIRROR CONTROL per track: the treatment's own entries, the opposite
    side, the same book at the same instant. If the primary cannot separate
    from its mirror by the pre-registered margin, whatever it earned was a
    side-bias or noise, and that is a FAIL clause, not a judgement.

The primary treatment per track is fixed before the data: A3 for Track A, B3
for Track B. The other treatments are read against it and cannot promote. A
gate that promotes whichever of three arms looks best is a three-way search.

WHAT A PROBE PASS AUTHORIZES: NOTHING LIVE. A PASS means "candidate alpha";
the next step is a prospective paper/twin experiment under the promotion gate
registered here, and PROBE -> PAPER remains an operator transition.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import read, service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "marktangle-2-conditional-dependence"
PREDECESSOR_KEY = "marktangle-conditional-reversion"
PROBE_DEPLOYMENT_KEY = "marktangle2-probe-1"

SPEC_DOC = "docs/MARKTANGLE_2_SPEC.md"
INSTRUMENT = "scripts/marktangle2_probe.py"
WORKSTREAM = "docs/workstreams/WS-013-marktangle-2-conditional-dependence.md"

ARM_A_BASE, ARM_A1, ARM_A2, ARM_A3, ARM_A_MIRROR = "m2a0", "m2a1", "m2a2", "m2a3", "m2amirror"
ARM_B_BASE, ARM_B1, ARM_B2, ARM_B3, ARM_B_MIRROR = "m2b0", "m2b1", "m2b2", "m2b3", "m2bmirror"
PRIMARY = {"A": ARM_A3, "B": ARM_B3}
MIRROR = {"A": ARM_A_MIRROR, "B": ARM_B_MIRROR}

#: Net edge bar, cents per contract after worst-case taker fee and modeled
#: slippage. Consistent with MARKTANGLE-1's bar.
EDGE_BAR_CENTS = 3.0
#: Treatment must beat its mirror by this many cents per trade (§17.5).
MIRROR_DELTA_CENTS = 3.0

#: The Phase-A decision rule, frozen with the contract. Numbers here are the
#: numbers in the instrument's module constants; a test asserts they agree.
PROBE_RULE: dict = {
    "instrument": INSTRUMENT,
    "unit_of_observation": "one prediction point = the next resolution of a recurring "
                           "binary family (series + market suffix) given strictly earlier "
                           "information; classes pool families by market STRUCTURE",
    "split": "first 70% of each class's prediction points by decision time is TRAIN; "
             "the last 30% is HOLDOUT and is read only to grade. No validation segment "
             "because no model-selection degree of freedom is left free",
    "decision_offset": "T-60m before the next market's close; quote = the completed "
                       "1-minute candle at or before that instant, taker side",
    "execution": "taker at the touch; worst-case fee ceil(7 p (1-p)) c; slippage 1c; "
                 "quote wider than 10c is not executable; 1 contract per trade",
    "entry": f"best side by net edge, only when net edge >= {EDGE_BAR_CENTS}c",
    "primary_per_track": PRIMARY,
    "floors": {
        "train_prediction_points": 500,
        "holdout_trades": 100,
        "holdout_price_coverage": 0.50,
    },
    "pass": [
        "holdout net P&L > 0 and EV/trade > 0 on >= 100 holdout trades",
        "holdout Brier below the independence baseline AND holdout net P&L above it",
        f"EV/trade minus the mirror's EV/trade >= {MIRROR_DELTA_CENTS}c",
        "net P&L stays > 0 without the most profitable family",
        "net P&L stays > 0 without the top 1% of trades",
        "the PRIMARY treatment (A3 / B3) clears all of the above in >= 1 preregistered class",
    ],
    "fail": "the primary is adequately powered and fails a clause in every class",
    "hold": "a floor is unmet — thin holdout, too few train points, or price coverage "
            "below 50%. Thin sample is not a negative result; it is no result",
    "no_repricing": "no threshold, bucket, class, floor, penalty or sizing rule is "
                    "changed after the holdout is read. A class that misses the bar "
                    "is not promoted on a narrower slice of itself",
    "track_independence": "Track A and Track B are graded separately; neither rescues "
                          "the other",
}

HELD_CONSTANT: list[str] = [
    "position size is 1 unit for the primary test; the secondary sizing study is a "
    "function of ESTIMATED EDGE only — never of the number or size of preceding losses. "
    "No loss-chasing progression of any kind",
    "no doubling, no Martingale, no anti-Martingale, no recovery multiplier; a losing "
    "previous trade has zero effect on the next trade's size",
    "class membership is decided from market structure before any conditional return is "
    "inspected; families are never pooled because they behaved alike",
    "daily crypto thresholds are excluded from Track A by construction",
    "MARKTANGLE-1's contract, floors, universe and HOLD are untouched",
    "one decision offset (T-60m) for every arm; entry timing is not an arm",
]


#: Paper promotion evidence floor, settled trades on the primary and its mirror.
PAPER_SAMPLE_FLOOR = 200


def _promotion_gate(track: str, floor: int = PAPER_SAMPLE_FLOOR) -> dict:
    t, m = PRIMARY[track], MIRROR[track]
    return {
        "description": (
            f"Track {track}: the primary treatment must be profitable in paper and beat "
            f"its mirror by a material margin. Only {t} can promote; the other arms are "
            "read against it and cannot."
        ),
        "sample": {
            t: {"metric": "settled_trades", "op": ">=", "value": floor},
            m: {"metric": "settled_trades", "op": ">=", "value": floor},
        },
        "max_evidence_horizon": {"metric": "settled_trades", "value": 1500},
        "pass_all": [
            {"metric": "pnl_cents_per_trade", "arm": t, "op": ">", "value": 0},
            {"metric": "delta.pnl_cents_per_trade", "treatment": t, "control": m,
             "op": ">=", "value": MIRROR_DELTA_CENTS},
        ],
        "fail_any": [
            {"metric": "delta.pnl_cents_per_trade", "treatment": t, "control": m,
             "op": "<=", "value": 0},
        ],
    }


def _keep_gate(track: str) -> dict:
    t, m = PRIMARY[track], MIRROR[track]
    return {
        "description": (
            f"Track {track} paper keep/stop: stop when the thesis is refuted, not when a "
            "drawdown feels bad. Both stopping clauses carry their own evidence floor."
        ),
        "sample": {t: {"metric": "settled_trades", "op": ">=", "value": 400}},
        "pass_all": [
            {"metric": "delta.pnl_cents_per_trade", "treatment": t, "control": m,
             "op": ">", "value": 0},
        ],
        "fail_any": [
            {"metric": "pnl_cents_per_trade", "arm": t, "op": "<=", "value": -3.0,
             "min_evidence": {"metric": "settled_trades", "op": ">=", "value": 150}},
            {"metric": "delta.pnl_cents_per_trade", "treatment": t, "control": m,
             "op": "<=", "value": 0,
             "min_evidence": {"metric": "settled_trades", "op": ">=", "value": 250}},
        ],
    }


GATES: tuple[tuple[str, str, str], ...] = (
    ("paper_to_live_canary_a", "promotion", "A"),
    ("paper_keep_a", "kill", "A"),
    ("paper_to_live_canary_b", "promotion", "B"),
    ("paper_keep_b", "kill", "B"),
)

#: arm_key -> (role, description, params). Tags deliberately absent.
ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (ARM_A_BASE, ArmRole.BENCHMARK,
     "Track A independence baseline: the family's unconditional YES rate (shrunk toward "
     "the class rate). Establishes whether serial information adds anything",
     {"track": "A", "model": "family_base_rate", "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    (ARM_A1, ArmRole.TREATMENT,
     "Track A one-step transition: P(next | previous resolution), per family, shrunk",
     {"track": "A", "model": "one_step_transition", "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    (ARM_A2, ArmRole.TREATMENT,
     "Track A streak-length reversion: direction-specific P(next | streak direction, "
     "streak length k), class-pooled, k<=5 individually and k>=6 pooled",
     {"track": "A", "model": "streak_table", "max_k": 6, "edge_bar_cents": EDGE_BAR_CENTS,
      "sizing": "flat"}),
    (ARM_A3, ArmRole.TREATMENT,
     "Track A PRIMARY — hierarchical logistic on streak direction, ln(k), their "
     "interaction, and a ridge-penalized family effect (the family baseline)",
     {"track": "A", "model": "hierarchical_logistic", "family_ridge": 1.0,
      "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    (ARM_A_MIRROR, ArmRole.CONTROL,
     "Track A mirror: the treatment's own entries, the opposite side of the same book at "
     "the same instant, identical eligibility and execution assumptions",
     {"track": "A", "side": "opposite", "sizing": "flat"}),
    (ARM_B_BASE, ArmRole.BENCHMARK,
     "Track B independence baseline: the family's unconditional YES rate, shrunk",
     {"track": "B", "model": "family_base_rate", "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    (ARM_B1, ArmRole.TREATMENT,
     "Track B one-step persistence: P(same | previous resolution), per family, shrunk",
     {"track": "B", "model": "one_step_transition", "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    (ARM_B2, ArmRole.TREATMENT,
     "Track B state duration: P(continuation | state, duration bucket), class-pooled; "
     "the hazard is read on both the young and the aged regime",
     {"track": "B", "model": "duration_table", "buckets": [[1, 1], [2, 2], [3, 3], [4, 5], [6, 9], [10, 19], [20, None]],
      "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    (ARM_B3, ArmRole.TREATMENT,
     "Track B PRIMARY — continuation logistic on state, ln(duration), signed normalized "
     "distance to the strike (ln(spot/strike) / trailing 20-day realized vol, no "
     "lookahead) and their interaction",
     {"track": "B", "model": "state_duration_distance_logistic", "vol_window_days": 20,
      "z_cap": 6.0, "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    (ARM_B_MIRROR, ArmRole.CONTROL,
     "Track B mirror: the treatment's own entries, the opposite side of the same book at "
     "the same instant",
     {"track": "B", "side": "opposite", "sizing": "flat"}),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Create the experiment (predecessor: MARKTANGLE-1 when it exists), freeze v1
    with its ten arms and four gates, open e1 on the ACTIVE platform snapshot,
    register a tagless PROBE deployment and move IDEA -> PROBE.

    `promotion_sample_floor` is the one knob the experiment-command transport
    passes (always, as None when the envelope omits it). It may only RAISE the
    paper promotion floor: a lower one would let the primary promote on a thinner
    sample than the reviewed contract asks for.

    Idempotence is refusal: a second run raises rather than creating a parallel
    contract under a suffixed key."""
    floor = PAPER_SAMPLE_FLOOR if promotion_sample_floor is None else int(promotion_sample_floor)
    if floor < PAPER_SAMPLE_FLOOR:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is below the reviewed floor "
            f"{PAPER_SAMPLE_FLOOR} — an envelope may make a pre-registered bar "
            "stricter, never weaker"
        )
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package registers it "
            "once; a changed contract is a new Version"
        )
    predecessor = get_experiment(session, PREDECESSOR_KEY)

    experiment = service.create_experiment(
        session,
        key=EXPERIMENT_KEY,
        origin="operator",
        title="MARKTANGLE-2 — conditional dependence alpha",
        family="serial_dependence",
        hypothesis=(
            "Where a recurring Kalshi market exhibits measurable serial dependence in its "
            "resolutions — reversion in homogeneous fresh-event classes, or continuation "
            "in daily crypto thresholds — a model of the current state, its duration and "
            "the relevant structural variables predicts the next resolution better than "
            "the contemporaneous executable price, by enough to be net positive after "
            "fees, slippage and a liquidity screen."
        ),
        mechanism=(
            "Track A: a streak in a fresh-event family proxies a maturing hidden regime, "
            "and flow anchored to the family's marginal frequency does not condition on "
            "it. Track B: a daily threshold market is a repeated observation of a slow "
            "underlying against a fixed level, so its resolutions are a state-duration "
            "process whose continuation probability depends jointly on how long the "
            "state has held and how far the underlying sits from the level; the trading "
            "claim is that the quote does not fully incorporate that joint structure."
        ),
        counterparty=(
            "flow anchored to the marginal frequency (Track A); flow pricing the level "
            "crossing from spot alone without the duration term, or slow to update "
            "(Track B)"
        ),
        falsification=(
            "On untouched holdout, the primary treatment fails to beat the independence "
            "baseline on calibration or money, OR fails to separate from its mirror by "
            "the registered margin, OR its profit is carried by one family or by the top "
            "1% of trades. Predictable sequences whose prices already carry the "
            "predictability are a FAIL (efficient market), recorded as a successful "
            "negative discovery."
        ),
        universe=(
            "structurally classified recurring binary families: sports totals/spreads "
            "pooled within a sport, weather high/low buckets and thresholds, and daily "
            "crypto threshold families per asset. Class membership is decided from market "
            "structure before any result is inspected."
        ),
        docs={"spec": SPEC_DOC, "instrument": INSTRUMENT, "workstream": WORKSTREAM},
        notes=(
            "Separate experiment from MARKTANGLE-1, whose contract, floors and HOLD are "
            "untouched. Martingale sizing is a pre-registered EXCLUSION on v1's "
            "held_constant."
        ),
        predecessor=predecessor,
        actor=actor,
        now=at,
    )

    version = service.create_experiment_version(
        session, experiment,
        hypothesis=experiment.hypothesis,
        mechanism=experiment.mechanism,
        counterparty=experiment.counterparty,
        falsification=experiment.falsification,
        universe_selector=experiment.universe,
        universe_exclusions=(
            "same-close ties are dropped, never ordered by guess; constant families "
            "(0% or 100% YES) carry no conditional structure; Track A excludes daily "
            "crypto thresholds; Track B excludes bucket (between) markets and families "
            "with fewer than 5 observations of either outcome; leagues outside the "
            "structural sport table are reported and never pooled"
        ),
        entry_rule=(
            "at T-60m before the next market's close, with the family's history known, "
            "lift the taker side whose net edge (100 p - price - fee - slippage) is "
            f"largest, only when it is >= {EDGE_BAR_CENTS}c"
        ),
        exit_rule="hold to settlement; no take-profit or stop-loss",
        sizing_rule=(
            "1 contract per qualifying trade for every graded number. A secondary "
            "quarter-Kelly study (one unit per 2% bankroll fraction, capped at 4) is "
            "reported and never gated; its size is a function of estimated edge only."
        ),
        execution_style="taker",
        independent_variable=(
            "what the model conditions on: nothing (baseline), the previous outcome, "
            "the streak length / state duration, and the structural term (family "
            "effects in Track A; normalized distance to the strike in Track B)"
        ),
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": "holdout net P&L and EV/trade of the primary treatment vs its "
                       "mirror and vs the independence baseline",
            "secondary": ["brier", "accuracy", "return_on_risk", "max_drawdown",
                          "longest_losing_streak", "profit_factor", "per_family_pnl",
                          "yes_no_decomposition"],
            "read_only_arms": [ARM_A1, ARM_A2, ARM_B1, ARM_B2],
            "note": "the fill model is NOT read on this experiment: every arm is a taker",
        },
        sample={"probe": PROBE_RULE,
                "paper_floor_settled_trades": {ARM_A3: floor, ARM_A_MIRROR: floor,
                                               ARM_B3: floor, ARM_B_MIRROR: floor}},
        costs={"model": "worst-case Kalshi taker fee, ceil(7 * p * (1-p)) cents per "
                        "contract, charged on entry; settlement is free",
               "slippage_cents": 1.0, "max_spread_cents": 10.0,
               "edge_bar_cents": EDGE_BAR_CENTS},
        provenance={"resolutions": "Kalshi public settled-markets API (per series)",
                    "prices": "Kalshi 1-minute candlesticks (live + historical archives), "
                              "taker side of the book at T-60m",
                    "spot": "Coinbase Exchange public candles (hourly close for spot, "
                            "daily close for realized vol); no lookahead"},
        monitoring={"probe": f"{INSTRUMENT} via the ops channel; package split by "
                             "scripts/marktangle2_package.py"},
        docs={"spec": SPEC_DOC},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    gates = []
    for gate_key, kind, track in GATES:
        spec = _promotion_gate(track, floor) if kind == "promotion" else _keep_gate(track)
        gates.append(service.register_gate(
            session, version, gate_key=gate_key, kind=kind, spec=spec,
            from_state=LifecycleState.PAPER if kind == "promotion" else None,
            to_state=LifecycleState.LIVE_CANARY if kind == "promotion" else None,
            registered_at=at,
            notes=("registered at IDEA, before any evidence of any kind exists — the "
                   "strongest form of pre-registration available here"),
        ))
    service.freeze_version(session, version, now=at)

    epoch = service.open_epoch(
        session, version,
        reason=("v1's first operating interval, pinned to the platform snapshot active "
                "at registration. The probe reads public settlement, quote and spot "
                "history, so nothing in this epoch depends on our own fills."),
        started_at=at,
    )
    deployment = service.register_deployment(
        session, epoch,
        deployment_key=PROBE_DEPLOYMENT_KEY,
        stage=LifecycleState.PROBE,
        kind=DeploymentKind.PROBE,
        arms={arm_key: None for arm_key, _, _, _ in ARMS},
        config={"instrument": INSTRUMENT},
        started_at=at,
        notes=("TAGLESS BY CONSTRUCTION. The probe is an offline scan of public "
               "history; under NEW_ONLY a tag no active deployment arm carries cannot "
               "trade, and this deployment carries none. Tags are assigned when a "
               "PAPER deployment is registered, which happens only on a probe PASS "
               "and an operator transition."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("historical scan registered; the pre-registered probe rule is frozen "
                "on v1"),
        occurred_at=at, version=version, epoch=epoch,
    )

    # Evidence is deliberately NOT started on any gate: they read `paper_trades`
    # through deployment-arm tags, and no arm has a tag yet.
    #
    # The keys in `experiment_commands.RESULT_OBJECT_KEYS` carry ORM OBJECTS, not
    # identifiers: the transport builds its receipt by reading `version.version`,
    # `epoch.epoch_number` and the rest off this dict. Returning the identifiers
    # directly is what failed m2-register-1..3 in production.
    return {
        "version": version,
        "epoch": epoch,
        "probe": deployment,
        "gates": gates,
        # Identifiers, for a caller reading this dict rather than the receipt.
        "experiment": experiment.key,
        "predecessor": predecessor.key if predecessor is not None else None,
        "state": experiment.state,
        "version_number": version.version,
        "epoch_number": epoch.epoch_number,
        "arms": [a for a, _, _, _ in ARMS],
        "gate_keys": [g.gate_key for g in gates],
        "deployment": deployment.deployment_key,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# Retrospective close-out
# ---------------------------------------------------------------------------

RESULTS_DOC = "docs/marktangle2/MARKTANGLE_2_SUMMARY.md"

#: All four gate verdicts, frozen here as reviewed code rather than passed in an
#: envelope, so the transport can never be the thing that chooses what happened.
#:
#: READ THE DISCREPANCY BEFORE THE VERDICTS. The instrument's frozen TRACK rule
#: printed `A HOLD` and `B HOLD` on run 2 (ops `m2-run-2`, 2026-09-02). These rows
#: record FAIL and BLOCKED_DATA. That is deliberate, it is an OPERATOR conclusion
#: rather than a re-run of the rule, and it is written down here rather than
#: smoothed over:
#:
#:   * Track A printed HOLD only because two of five classes were under-powered.
#:     The three that WERE adequately powered failed every economic clause. Spec
#:     §19's Track A kill rule and the track-verdict rule pointed in opposite
#:     directions, which is a defect in the frozen rule, not an open question about
#:     the evidence; the operator closed the track on 2026-09-03. Recording HOLD
#:     would file the line's one real falsification as "no result".
#:   * Track B printed HOLD on the price-coverage floor. BLOCKED_DATA rather than
#:     HOLD because HOLD invites "wait for more evidence" and more evidence will
#:     never come: the class has no book to trade, not a thin one. Same call, and
#:     the same reasoning, as PERP-V1's funding arm.
#:
#: Every row is still a non-authorizing verdict — `service.close_out_retrospective`
#: refuses PASS outright — so nothing here can promote anything.
CLOSE_OUT_VERDICTS: tuple[tuple[str, str, str], ...] = (
    (
        "paper_to_live_canary_a",
        "FAIL",
        "FAIL on the premise, recorded against a gate that never opened: MARKTANGLE-2 "
        "never reached PAPER, so this paper->live gate has no settled_trades of its "
        "own. What failed is the thing it exists to promote. On untouched holdout, "
        f"{PRIMARY['A']} lost money in all three adequately-powered classes — "
        "BASEBALL_TOTAL -3.17c/trade over 2040 trades, BASKETBALL_TOTAL -5.83c over "
        "243, SOCCER_TOTAL -9.59c over 153 — failing net P&L, EV/trade, the 3c mirror "
        "separation, and staying negative after removing both the most profitable "
        "family and the top 1% of trades. In soccer the mirror was POSITIVE (+1.82c) "
        "while the treatment lost: wrong-signed, not merely uninformative.",
    ),
    (
        "paper_keep_a",
        "FAIL",
        "FAIL for the same evidence, and this gate is where the mechanism is recorded: "
        "the coefficient the track rides on is dead. `prev_dir x ln(k)` must be "
        "NEGATIVE for reversal to rise with run length; it measured -0.019 (z -0.49) "
        "in baseball, +0.466 (z +3.23) in basketball and +0.193 (z +1.62) in soccer — "
        "zero within noise twice and significantly the WRONG SIGN once. Mild one-step "
        "reversion is real; streak LENGTH adds nothing. Several arms beat the "
        "independence baseline on Brier while losing money, which is spec §22's "
        "Outcome 3 in this experiment's own numbers: forecastability is not alpha.",
    ),
    (
        "paper_to_live_canary_b",
        "BLOCKED_DATA",
        "No executable price exists for this class, so the track has no input. Of 9,980 "
        "BTC holdout prediction points the run's fetch budget reached ~2,000, and 16 "
        "returned a two-sided quote at the T-60m decision instant (<1%); ETH, SOL and "
        "XRP were never reached. Holdout price coverage 0% against a pre-registered 50% "
        "floor. This answers open question D1 with evidence rather than leaving it "
        "open: the class pools all 113 BTC rungs, most permanently deep in or out of "
        "the money, and a rung nobody trades has an empty book an hour before close. A "
        "larger fetch budget cannot lift a sub-1% quote rate to a 50% floor.",
    ),
    (
        "paper_keep_b",
        "BLOCKED_DATA",
        "BLOCKED_DATA on the same missing input, and the finding worth keeping is that "
        "the PREDICTION was never the problem. B1/B2 reach 98.3% holdout accuracy with "
        "a Brier of 0.015 against the independence baseline's 0.045 — crypto threshold "
        f"persistence is real and strongly forecastable — while {PRIMARY['B']} cannot be "
        "graded at all because no price was obtainable. Predictable and unpriceable. "
        "Not re-scoped to near-the-money rungs to manufacture coverage: that is a "
        "different universe wearing this track's registered gate, which §11 and §19 "
        "forbid once the holdout is open. The remedy, if the operator wants one, is a "
        "new Version or forward quote collection.",
    ),
)


def close_out_retrospective(
    session, *, actor: str, approved_by: str, reason: str,
    now: datetime | None = None,
) -> dict:
    """Retire MARKTANGLE-2 with its four gate verdicts recorded, closing both tracks.

    Unlike PERP-V1's and MARKTANGLE-1's, this close-out does NOT register: the
    contract is already in production (registered 2026-09-02, `m2-register-4`), so
    it ADOPTS the registered objects and refuses if they are absent. Registering
    here would either raise on the duplicate or, worse, author a second contract
    beside the one the verdicts belong to.

    "Retrospective" is still the honest word for what it writes. The probe ran as an
    ops-channel script over public settlement history, not through the evaluator, so
    every verdict was computed outside the system and is being recorded by hand,
    late; `computed_by` is stamped `retrospective:<actor>` on all four rows so a
    reader can never mistake them for the evaluator's.

    Both tracks close together because the operator closed them together on
    2026-09-03, each on its own evidence and neither rescuing the other — the track
    independence §11 requires holds through the close-out. See CLOSE_OUT_VERDICTS
    for where these verdicts depart from the instrument's printed track rule, and
    why.

    Authorizes nothing, and cannot: PASS is unrepresentable through this path and
    the only target is RETIRED. The probe deployment is TAGLESS — no `m2*` strategy
    tag was ever created — so nothing here ever reached the trading write path; it
    is ended as part of the retirement rather than left open.
    """
    at = now or _now()
    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} is not registered — this close-out records "
            "verdicts against the contract that is already in production and will not "
            "author one; run REGISTER_PACKAGE marktangle-2 first"
        )
    version = read.latest_version(session, experiment)
    gates_by_key = {g.gate_key: g for g in read.gates_for(session, version)}

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
        experiment,
        verdicts=[
            (gates_by_key[key], verdict, explanation)
            for key, verdict, explanation in CLOSE_OUT_VERDICTS
        ],
        actor=actor,
        approved_by=approved_by,
        reason=reason,
        evidence_ref=RESULTS_DOC,
        epoch=read.open_epoch_for(session, version)
        or read.epochs_for(session, version)[-1],
        now=at,
    )
    return {**closed, "arms": [a for a, _, _, _ in ARMS]}
