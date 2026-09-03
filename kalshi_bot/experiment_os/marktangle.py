"""MARKTANGLE — the conditional-reversion experiment package.

Thesis: `docs/MARKTANGLE_THESIS.md`. Phase-A probe: `scripts/marktangle_probe.py`.
This module is DATA plus one function. It registers a scientific contract; it
arms nothing, deploys no tag and places no order. Under NEW_ONLY a tag that no
active deployment arm carries cannot trade at all, and the PROBE deployment this
package registers carries **no tags on purpose** — the probe is an offline
historical scan, and nothing about it should be able to reach the exchange.

WHAT THE EXPERIMENT IS, AND WHAT IT DELIBERATELY IS NOT
------------------------------------------------------
It is NOT the Martingale. Doubling after a loss changes no per-trade expectation;
it only reshapes the loss distribution, trading a high probability of a small win
for a small probability of a ruinous one. Ten doublings off a $10 base commit
$10,230 to chase $10, and a finite bankroll meeting a long enough streak is not a
risk we would be measuring — it is one we would be waiting for. Martingale sizing
is therefore a **pre-registered exclusion**, recorded in `held_constant` so that
adding it later is a visible contract change rather than a quiet retune.

It is also NOT "ten YESes mean a NO is due". Under independence that is false by
construction: P(N | Y^10) = P(N). A family can resolve YES exactly 50% of the
time and be perfectly memoryless.

What it IS: whether some recurring binary family carries **negative serial
dependence that survives out of sample and exceeds the market's own price**. If
after six consecutive YES resolutions the conditional reversal rate is 70% and
the NO side still costs 50c, the position is larger because the *estimated edge*
is larger — never because the last four attempts lost.

WHY THIS IS NOT A REVIVAL
-------------------------
Two dead families sit nearby and neither is this one:

* `scanner-ta-books` (momentum / reversion / buy_favorite) and the nine
  `backfill-structural-probes` both tested **intraday price paths** — the
  autocorrelation of a market's own quote. Both are refuted at the fee scale, and
  the graveyard's revival condition for the surviving fragment is explicit: use
  mean reversion as a MODEL FEATURE, never as a standalone trade.
* This experiment's unit of observation is not a price path at all. It is the
  **resolution of consecutive events in one recurring family** — a sequence of
  settlements through time, one per event — and its entry condition is a modelled
  conditional probability measured against the quote, which is exactly the
  "model feature inside a multi-signal book" shape the graveyard asks for.

The mechanically new premise, stated once so it can be held against results: a
streak in a recurring family may be a proxy for a **hidden regime** (a weather
system maturing, a policy cycle, a seasonal run) rather than a cause of anything.
If that is true the dependence is real and exploitable; if it is false the probe
says so cheaply, on history, before a dollar moves.

WHY FIVE ARMS
-------------
The question has two independent halves, and one arm cannot separate them:

  * does the streak carry DIRECTION?  -> `mktcont`, the mirror. Same universe,
    same cadence, same sizing, entry on the CONTINUATION side. If the treatment
    does not beat its own mirror, the streak carries no direction and whatever
    the treatment earned came from a side-bias in the family or from noise.
  * does the EDGE GATE do the work?   -> `mktnaive`, the gambler's-fallacy arm:
    reversal entry at the same threshold with NO price comparison. If the
    treatment does not beat it, the price test is decoration and the book is a
    fallacy with extra steps.

The other two treatments vary the one declared independent variable — where on
the streak-length axis the edge lives (`mktrev5`) — and the sizing engine
(`mktkelly`, edge-proportional under a hard cap). They are **read**, not gated:
only `mktrev3` can promote. That is deliberate. A gate that promotes whichever of
three arms looks best is a three-way search wearing a p-value, and its winner's
bar is not the bar that was pre-registered.

There is no sixth arm. Every additional arm splits the same settlement cadence,
and the 200-settled-trade floor on `mktrev3` and `mktcont` is already the binding
constraint on how long this experiment takes to say anything.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "marktangle-conditional-reversion"
PROBE_DEPLOYMENT_KEY = "marktangle-probe-1"

TREATMENT_ARM = "mktrev3"
MIRROR_CONTROL_ARM = "mktcont"
NAIVE_BENCHMARK_ARM = "mktnaive"

#: Threshold at which the edge test is applied, in cents per contract, net of
#: worst-case taker fees. Below this a modelled edge is inside the noise of the
#: quote we would have to lift.
EDGE_BAR_CENTS = 3.0

#: The Phase-A decision rule, frozen with the contract. It is here, in the
#: version, rather than only in the probe script because the script's thresholds
#: are arguments and arguments can be chosen after seeing output; a frozen
#: contract cannot be.
#:
#: A probe verdict authorizes nothing on its own: PROBE -> PAPER is an operator
#: transition justified by the merged research document, and the first
#: machine-executable gate is `paper_to_live_canary` below. The probe's evidence
#: is an offline scan of public settlement history, which no metric provider can
#: read — recording a gate against a metric the engine cannot compute would put a
#: permanent BLOCKED_DATA in the portfolio and call it pre-registration.
PROBE_RULE: dict = {
    "instrument": "scripts/marktangle_probe.py",
    "unit_of_observation": "resolution of consecutive events in one recurring "
                           "binary family (series + market suffix)",
    "split": "first 70% of each family's history by close time is TRAIN; the "
             "last 30% is HOLDOUT and is read only to grade",
    "threshold_fitting": "k* = the smallest streak length whose TRAIN reversal "
                         "rate has a Wilson 95% lower bound above 50% on >= 30 "
                         "observations. Smallest, not best-looking: a maximum "
                         "over 15 candidates is a 15-way search",
    "pass": [
        "at least one family with >= 100 HOLDOUT entries at run length >= k*",
        "HOLDOUT reversal rate Wilson 95% lower bound > 50%",
        f"mean net edge vs the taker price at T-60m >= +{EDGE_BAR_CENTS}c/contract "
        "after worst-case fees, on >= 100 priced holdout entries",
    ],
    "fail": "every holdout survivor is priced at or through its edge",
    "hold": "no family reaches the 100-entry holdout floor — thin sample is not a "
            "negative result, it is no result",
    "no_repricing": "the bar is not re-read after results. A family that misses "
                    "it is not promoted on a narrower slice of itself",
}

#: The exclusions that make this a conditional-reversion experiment rather than a
#: Martingale. Frozen, so adding any of them later is a new Version.
HELD_CONSTANT: list[str] = [
    "position size is a function of ESTIMATED EDGE only — never of the number or "
    "size of preceding losses. No loss-chasing progression of any kind",
    "no doubling, no Martingale, no anti-Martingale, no recovery multiplier",
    "per-market and per-family exposure caps are fixed for the life of the "
    "version and do not widen after a losing sequence",
    "the family universe is chosen by the pre-registered probe rule, never by "
    "which families looked profitable in the holdout",
    "one decision offset (T-60m) for every arm; entry timing is not an arm",
]

#: PAPER -> LIVE_CANARY. Absolute profitability is necessary but nowhere near
#: sufficient: the whole scientific claim is the DELTA against the mirror.
PROMOTION_GATE_SPEC: dict = {
    "description": (
        "The treatment must be profitable, must beat its own mirror by a "
        "material margin, and must beat the un-gated fallacy arm. Only mktrev3 "
        "can promote; mktrev5 and mktkelly are read against it and cannot."
    ),
    "sample": {
        TREATMENT_ARM: {"metric": "settled_trades", "op": ">=", "value": 200},
        MIRROR_CONTROL_ARM: {"metric": "settled_trades", "op": ">=", "value": 200},
    },
    "max_evidence_horizon": {"metric": "settled_trades", "value": 1500},
    "pass_all": [
        {"metric": "pnl_cents_per_trade", "arm": TREATMENT_ARM,
         "op": ">", "value": 0},
        {"metric": "delta.pnl_cents_per_trade", "treatment": TREATMENT_ARM,
         "control": MIRROR_CONTROL_ARM, "op": ">=", "value": EDGE_BAR_CENTS},
        {"metric": "delta.pnl_cents_per_trade", "treatment": TREATMENT_ARM,
         "control": NAIVE_BENCHMARK_ARM, "op": ">=", "value": 1.0},
    ],
    "fail_any": [
        # The kill condition for the whole family: the mirror ties or wins. A
        # streak that predicts nothing directional cannot be traded either way.
        {"metric": "delta.pnl_cents_per_trade", "treatment": TREATMENT_ARM,
         "control": MIRROR_CONTROL_ARM, "op": "<=", "value": 0},
    ],
}

#: The keep/kill contract for the paper stage, registered before any evidence so
#: no threshold can be chosen after seeing one.
KEEP_GATE_SPEC: dict = {
    "description": (
        "Stop the paper book when the thesis is refuted, not when a drawdown "
        "feels bad. Both stopping conditions carry their own evidence floor, "
        "deliberately lower than the promotion floor: one number for both would "
        "leave a clearly-dead book running to 200 trades."
    ),
    "sample": {
        TREATMENT_ARM: {"metric": "settled_trades", "op": ">=", "value": 400},
    },
    "pass_all": [
        {"metric": "delta.pnl_cents_per_trade", "treatment": TREATMENT_ARM,
         "control": MIRROR_CONTROL_ARM, "op": ">", "value": 0},
    ],
    "fail_any": [
        {"metric": "pnl_cents_per_trade", "arm": TREATMENT_ARM,
         "op": "<=", "value": -3.0,
         "min_evidence": {"metric": "settled_trades", "op": ">=", "value": 150}},
        {"metric": "delta.pnl_cents_per_trade", "treatment": TREATMENT_ARM,
         "control": MIRROR_CONTROL_ARM, "op": "<=", "value": 0,
         "min_evidence": {"metric": "settled_trades", "op": ">=", "value": 250}},
    ],
}

PROMOTION_GATE_KEY = "paper_to_live_canary"
KEEP_GATE_KEY = "paper_keep"

#: arm_key -> (role, description, params). Tags are deliberately absent: a tag is
#: assigned when a PAPER deployment is registered, which is a separate reviewed
#: step taken only if the probe passes.
ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (TREATMENT_ARM, ArmRole.TREATMENT,
     "reversal side at run length >= 3, entered only when the modelled "
     "conditional reversal probability beats the taker price by >= 3c net of "
     "worst-case fees; flat size",
     {"min_run": 3, "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    ("mktrev5", ArmRole.TREATMENT,
     "identical to mktrev3 with the threshold at run length >= 5 — the "
     "independent variable is where on the streak-length axis the edge lives",
     {"min_run": 5, "edge_bar_cents": EDGE_BAR_CENTS, "sizing": "flat"}),
    ("mktkelly", ArmRole.TREATMENT,
     "streak-agnostic: enter whenever the modelled edge clears the bar, size "
     "proportional to edge under a hard cap (quarter-Kelly, capped at 4x the "
     "flat clip). Size tracks ESTIMATED EDGE, never accumulated losses",
     {"min_run": 1, "edge_bar_cents": EDGE_BAR_CENTS,
      "sizing": "quarter_kelly", "size_cap_multiple": 4}),
    (MIRROR_CONTROL_ARM, ArmRole.CONTROL,
     "the mirror: same universe, same cadence, same sizing as mktrev3, entered "
     "on the CONTINUATION side. Isolates the direction of the signal from the "
     "cost floor and from any one-sided bias in the family",
     {"min_run": 3, "side": "continuation", "sizing": "flat"}),
    (NAIVE_BENCHMARK_ARM, ArmRole.BENCHMARK,
     "the gambler's-fallacy arm: reversal side at run length >= 3 with NO price "
     "comparison. Measures how much of any edge comes from the price test "
     "rather than from the streak",
     {"min_run": 3, "edge_bar_cents": None, "sizing": "flat"}),
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
    """Create the experiment, freeze v1 with its five arms and both gates, open
    e1 on the ACTIVE platform snapshot, register a tagless PROBE deployment and
    move IDEA -> PROBE.

    `promotion_sample_floor` is the knob the experiment-command transport always
    passes (None when the envelope omits it). It may only RAISE the 200-trade
    paper promotion floor, never lower it. Before this parameter existed the
    transport's call raised TypeError, so the package could not be registered
    through the sanctioned write path at all (found on MARKTANGLE-2's first
    envelope, 2026-09-02).

    Idempotence is refusal, not a no-op: a second run raises rather than
    quietly creating a parallel contract under a suffixed key."""
    floor = 200 if promotion_sample_floor is None else int(promotion_sample_floor)
    if floor < 200:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is below the reviewed floor 200 — an "
            "envelope may make a pre-registered bar stricter, never weaker"
        )
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package "
            "registers it once; a changed contract is a new Version"
        )

    experiment = service.create_experiment(
        session,
        key=EXPERIMENT_KEY,
        origin="operator",
        title="MARKTANGLE — conditional reversion in recurring binary families",
        family="serial_dependence",
        hypothesis=(
            "Some recurring binary Kalshi families exhibit negative serial "
            "dependence in their RESOLUTIONS: the probability that the next "
            "event resolves opposite to the current run rises with run length, "
            "by more than the quote already prices, net of taker fees."
        ),
        mechanism=(
            "The streak is a proxy for a hidden regime reaching maturity (a "
            "weather system dissipating, a cycle turning), not a cause. The "
            "counterparty prices the marginal base rate and does not condition "
            "on the family's own settlement history."
        ),
        counterparty=(
            "retail flow anchored to the marginal frequency of the family, plus "
            "market makers quoting the ladder without a run-length term"
        ),
        falsification=(
            "Conditional reversal probability does not rise with run length out "
            "of sample, OR it rises but is already inside the quote after fees, "
            "OR the treatment fails to beat its own continuation mirror. Any one "
            "of the three kills the family."
        ),
        universe=(
            "recurring binary families (series + market suffix) whose settlement "
            "history is long enough to split 70/30 and which clear the "
            "pre-registered probe rule. Chosen by rule, never by which families "
            "looked profitable in the holdout."
        ),
        docs={"thesis": "docs/MARKTANGLE_THESIS.md",
              "probe": "scripts/marktangle_probe.py",
              "workstream": "docs/workstreams/WS-011-marktangle-conditional-reversion.md"},
        notes=(
            "Martingale sizing is a pre-registered EXCLUSION, not an untested "
            "option — see held_constant on v1."
        ),
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
            "families whose events do not form a sequence (multiple markets "
            "closing at the same instant are dropped, never ordered by guess); "
            "ladder rungs are separate families and are never pooled into one "
            "sequence, which would manufacture dependence out of the ladder's "
            "own geometry"
        ),
        entry_rule=(
            "at T-60m before the next event's close, with the family's current "
            "run length known, lift the offer on the arm's side when the arm's "
            "entry condition holds"
        ),
        exit_rule="hold to settlement; no take-profit or stop-loss",
        sizing_rule=(
            "a function of estimated edge only. mktrev3/mktrev5/mktcont/mktnaive "
            "are flat; mktkelly is quarter-Kelly capped at 4x the flat clip. No "
            "arm's size depends on preceding outcomes."
        ),
        execution_style="taker",
        independent_variable=(
            "the run-length threshold at which the reversal side is taken "
            "(3 vs 5 vs edge-only), and the sizing engine"
        ),
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": "delta.pnl_cents_per_trade (mktrev3 minus mktcont)",
            "secondary": ["pnl_cents_per_trade", "win_rate_pct", "settled_trades"],
            "read_only_arms": ["mktrev5", "mktkelly"],
            "note": "the fill model is NOT read on this experiment: it is "
                    "calibrated for resting maker orders in the mmsell cheap "
                    "band, and every arm here is a taker",
        },
        sample={"probe": PROBE_RULE,
                "paper_floor_settled_trades": {TREATMENT_ARM: floor,
                                               MIRROR_CONTROL_ARM: floor}},
        costs={"model": "worst-case Kalshi taker fee, ceil(7 * p * (1-p)) cents "
                        "per contract, charged on entry; settlement is free",
               "edge_bar_cents": EDGE_BAR_CENTS},
        provenance={"resolutions": "Kalshi public settled-markets API",
                    "prices": "Kalshi 1-minute candlesticks (live + historical "
                              "archives), taker side of the book"},
        monitoring={"probe": "scripts/marktangle_probe.py via the ops channel"},
        docs={"thesis": "docs/MARKTANGLE_THESIS.md"},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    promotion_spec = json.loads(json.dumps(PROMOTION_GATE_SPEC))
    for arm_key in (TREATMENT_ARM, MIRROR_CONTROL_ARM):
        promotion_spec["sample"][arm_key]["value"] = floor
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=promotion_spec, from_state=LifecycleState.PAPER,
        to_state=LifecycleState.LIVE_CANARY, registered_at=at,
        notes=("registered at IDEA, before any evidence of any kind exists — "
               "the strongest form of pre-registration available here"),
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes="paper keep/stop; both stopping clauses carry their own floor",
    )
    service.freeze_version(session, version, now=at)

    epoch = service.open_epoch(
        session, version,
        reason=("v1's first operating interval, pinned to the platform snapshot "
                "active at registration. The probe reads public settlement "
                "history, so nothing in this epoch depends on our own fills."),
        started_at=at,
    )
    deployment = service.register_deployment(
        session, epoch,
        deployment_key=PROBE_DEPLOYMENT_KEY,
        stage=LifecycleState.PROBE,
        kind=DeploymentKind.PROBE,
        arms={arm_key: None for arm_key, _, _, _ in ARMS},
        config={"instrument": "scripts/marktangle_probe.py"},
        started_at=at,
        notes=("TAGLESS BY CONSTRUCTION. The probe is an offline scan of public "
               "history; under NEW_ONLY a tag no active deployment arm carries "
               "cannot trade, and this deployment carries none, so no arm here "
               "can reach the exchange. Tags are assigned when a PAPER "
               "deployment is registered, which happens only if the probe "
               "passes."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("Phase-A historical scan registered; the pre-registered probe "
                "rule is frozen on v1"),
        occurred_at=at, version=version, epoch=epoch,
    )

    # Evidence is deliberately NOT started on either gate. Both read
    # `paper_trades` through deployment-arm tags, and no arm has a tag yet;
    # starting the clock now would floor every future window at a boundary that
    # predates the book by however long the probe takes.
    # Objects, not identifiers, on the keys the transport's receipt builder reads
    # (`experiment_commands.RESULT_OBJECT_KEYS`) — see marktangle2 for the
    # production failure this shape prevents.
    return {
        "version": version,
        "epoch": epoch,
        "probe": deployment,
        "promotion_gate": promotion_gate,
        "keep_gate": keep_gate,
        # Identifiers, for a caller reading this dict rather than the receipt.
        "experiment": experiment.key,
        "state": experiment.state,
        "version_number": version.version,
        "epoch_number": epoch.epoch_number,
        "arms": [a for a, _, _, _ in ARMS],
        "gate_keys": [promotion_gate.gate_key, keep_gate.gate_key],
        "deployment": deployment.deployment_key,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# Retrospective close-out
# ---------------------------------------------------------------------------

THESIS_DOC = "docs/MARKTANGLE_THESIS.md"

#: Both gate verdicts, frozen here as reviewed code rather than passed in an
#: envelope — a transport that could choose verdicts would make the channel, not
#: the evidence, the thing that decides what happened.
#:
#: Both are HOLD, and neither is a FAIL, because MARKTANGLE-1's own frozen verdict
#: rule says so: `PROBE_RULE["hold"]` reserves HOLD for "no family reaches the
#: 100-entry holdout floor — thin sample is not a negative result, it is no
#: result", and that is exactly what run 8 returned. Recording FAIL here would
#: claim a falsification the evidence never supported, and re-reading the rule
#: after results is the one thing the contract forbids.
CLOSE_OUT_VERDICTS: tuple[tuple[str, str, str], ...] = (
    (
        PROMOTION_GATE_KEY,
        "HOLD",
        "HOLD on sample, by the contract's own frozen rule. The experiment never "
        "reached PAPER, so this gate never had evidence to read: it is a paper->live "
        "gate on settled_trades, and no arm was ever deployed under a strategy tag. "
        "Eight probe runs (2026-08-29..30) produced one graded result — run 8, the "
        "hand-picked shortlist — and its best families reached holdouts of 13-27 "
        "against a pre-registered floor of 100. Thin sample is not a negative result.",
    ),
    (
        KEEP_GATE_KEY,
        "HOLD",
        "HOLD for the same reason and with the same standing: a paper keep/kill gate "
        "on an experiment that never opened a paper book has nothing to stop. Its "
        "sample floor is 400 settled trades against zero. Closed at PROBE by operator "
        "decision on 2026-08-30 with the directional finding intact and against the "
        "thesis: daily crypto threshold families are momentum machines, not coin "
        "flips (P(Y|Y) and P(N|N) near-total in the ladder families), which refutes "
        "conditional REVERSION for that market class rather than leaving it open. "
        "That finding is the premise MARKTANGLE-2 was built on, and it survives this "
        "close-out as recorded history.",
    ),
)


def close_out_retrospective(
    session, *, actor: str, approved_by: str, reason: str,
    now: datetime | None = None,
) -> dict:
    """Record MARKTANGLE-1 as the closed, un-answered experiment it is, and retire
    it — in one act, having never been registered while it ran.

    Three documents said it was PAUSED at PROBE in Experiment OS. It was not in
    Experiment OS at all: the 2026-08-29 registration was blocked by the
    `promotion_sample_floor` transport defect WS-013 diagnosed and fixed on
    2026-09-02, and nobody re-checked afterwards. So the system could not say the
    one thing that was true — this ran, and it is over — while its successor
    MARKTANGLE-2 sat registered above a predecessor that formally did not exist.

    Like PERP-V1's, this reuses `register()` for the contract, so the five arms and
    both gates are the ones actually pre-registered rather than a retyped copy that
    could drift from `docs/MARKTANGLE_THESIS.md`. Every row is stamped at close-out
    time, so the lateness is legible in the timestamps rather than disguised.

    Authorizes nothing, and cannot: `service.close_out_retrospective` refuses a PASS
    verdict outright and refuses any target but RETIRED. Its probe deployment is
    TAGLESS — no `mkt*` strategy tag was ever created — so nothing here ever
    reached the trading write path, and the close-out ends that deployment rather
    than leaving a retired experiment holding open research.
    """
    at = now or _now()
    produced = register(session, actor=actor, now=at)
    gates_by_key = {
        produced["promotion_gate"].gate_key: produced["promotion_gate"],
        produced["keep_gate"].gate_key: produced["keep_gate"],
    }

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
        get_experiment(session, EXPERIMENT_KEY),
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
