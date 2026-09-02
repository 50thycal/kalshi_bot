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

from . import service
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


def _promotion_gate(track: str) -> dict:
    t, m = PRIMARY[track], MIRROR[track]
    return {
        "description": (
            f"Track {track}: the primary treatment must be profitable in paper and beat "
            f"its mirror by a material margin. Only {t} can promote; the other arms are "
            "read against it and cannot."
        ),
        "sample": {
            t: {"metric": "settled_trades", "op": ">=", "value": 200},
            m: {"metric": "settled_trades", "op": ">=", "value": 200},
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


def register(session, *, actor: str, now: datetime | None = None) -> dict:
    """Create the experiment (predecessor: MARKTANGLE-1 when it exists), freeze v1
    with its ten arms and four gates, open e1 on the ACTIVE platform snapshot,
    register a tagless PROBE deployment and move IDEA -> PROBE.

    Idempotence is refusal: a second run raises rather than creating a parallel
    contract under a suffixed key."""
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
                "paper_floor_settled_trades": {ARM_A3: 200, ARM_A_MIRROR: 200,
                                               ARM_B3: 200, ARM_B_MIRROR: 200}},
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
        spec = _promotion_gate(track) if kind == "promotion" else _keep_gate(track)
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
    return {
        "experiment": experiment.key,
        "predecessor": predecessor.key if predecessor is not None else None,
        "state": experiment.state,
        "version": version.version,
        "arms": [a for a, _, _, _ in ARMS],
        "gates": [g.gate_key for g in gates],
        "epoch": epoch.epoch_number,
        "deployment": deployment.deployment_key,
        "tags": [],
    }
