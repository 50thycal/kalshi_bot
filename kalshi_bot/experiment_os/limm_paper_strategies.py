"""LIMM paper strategies S1-S4 — the incentive paper programme's contract (DEC-043, DEC-044).

Spec (pre-registration): `docs/LIMM_PAPER_STRATEGIES.md`. Instrument: the shadow collector's
paper arms (`kalshi_bot/liquidity_incentive/paper_arms.py`), read once by
`scripts/limm_paper_strategies_read.py`. This module is DATA plus one function: it registers a
scientific contract at PROBE. It arms nothing, deploys no tag and places no order.

WHY TAGLESS. The arms are rows the GET-only shadow collector writes into `incentive_*`
tables. They never touch `paper_trades` (thesis §9.52: a `paper_trades` twin is invalid for an
incentive book, because reward is not a fill) and never reach the exchange. So the PROBE
deployment carries no strategy tags, and under NEW_ONLY nothing here is admissible to trade.

WHY NO GATES. Every evaluator metric reads `paper_trades` through deployment-arm tags; none can
see a shadow-collector row. A gate registered here could only ever read BLOCKED_DATA. The
pre-registered decision rule is the probe rule frozen on the version (`PROBE_RULE`), read once
by the instrument against bars fixed in the spec. A PROMISING arm is a NOMINATION — one plain
question to the owner — never a transition: any live use is a new experiment or epoch and an
owner decision, and live bids above $10 are a hard stop.

WHY THESE ARMS. Four questions, each with its own control where one exists:
  S1          discovery sweep of the highest daily-pay programmes (no control: it asks "is
              there money here at all", read against the PROMISING bar);
  S2          at the best bid (treatment) vs one tick behind (control, the §9.72 rule);
  S3          release-aware step-aside (treatment) vs never pulled (control);
  S4          $50 burst to the $1 minimum (treatment) vs $10 flat (control).
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "limm-paper-strategies"

#: How quiet an armed arm may go before the global monitor (WS-026) reads it STALE. A tagless
#: probe writes no paper_trades, so the monitor has nothing to expect. Monitoring only.
MONITOR_EXPECT = {"max_silence_hours": 24, "note": "tagless shadow probe; no paper_trades expected"}

PROBE_DEPLOYMENT_KEY = "limm-paper-strategies-probe-1"
SPEC_DOC = "docs/LIMM_PAPER_STRATEGIES.md"
AGE_STUDY_DOC = "docs/LIMM_PROGRAMME_AGE.md"
HANDOFF_DOC = "docs/handoffs/HANDOFF-limm-paper-strategies.md"
INSTRUMENT = "kalshi_bot/liquidity_incentive/paper_arms.py"
READ_SCRIPT = "scripts/limm_paper_strategies_read.py"
#: The paper-only runtime switch, set by a separate env act after registration. Not declared as
#: the package's `activation_vars`: a package that arms nothing declares none.
ACTIVATION_VAR = "LIQUIDITY_INCENTIVE_PAPER_ARMS_ENABLED"

ARM_S1, ARM_S2_BEST, ARM_S2_BEHIND = "S1", "S2-best", "S2-behind"
ARM_S3_ASIDE, ARM_S3_CONTROL = "S3-aside", "S3-control"
ARM_S4_BURST, ARM_S4_FLAT = "S4-burst", "S4-flat"

#: The decision rule, frozen with the contract. Numbers equal the read script's constants; a
#: test asserts they agree.
PROBE_RULE: dict = {
    "instrument": READ_SCRIPT,
    "unit": "one $10-equivalent bid-day (an arm's bid size / $10 x hours rested / 24)",
    "reward": "scoring.estimate x 1.34 (fixed calibration), Kalshi's $1 minimum applied per "
              "market per programme",
    "fill_cost": "the 24 h markout of an unmatched fill; else settlement; else half the price",
    "primary_fill_model": "conservative",
    "stress_fill_model": "whole_order",
    "promising": "net >= $0.15 per $10 bid-day with lower 95% bound > 0 (event-clustered), "
                 "and the whole-order model does not turn it into a loss",
    "pairwise": "S2-best - S2-behind, S3-aside - S3-control, S4-burst - S4-flat per event over "
                "the control's bid-days; DECIDES when the 95% interval excludes 0",
    "floors": {"bid_days_per_arm": 100, "events_per_arm": 10},
    "hold": "a floor unmet: HOLD (accrual), one re-read two weeks later",
    "read_not_before": "2026-11-24T00:00:00+00:00",
    "no_repricing": "no bar, calibration, universe or size changes after recording starts",
    "consequence": "a PROMISING arm is a nomination to the owner; nothing transitions",
}

HELD_CONSTANT: list[str] = [
    "paper only: the shadow collector holds a GET-only client and writes only incentive_* "
    "tables; no arm can place an order",
    "one calibration (1.34) for every arm, fixed before recording",
    "every arm bids the cheap side (1-10c) the selection picked, and leaves 48 h before close",
    "paired arms quote the same markets in the same hours (one selection per strategy)",
    "Alimm1 (stood down 2026-11-08) is untouched: no rule, universe, size or env change",
]

#: arm_key -> (role, description, params). Tags deliberately absent.
ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (ARM_S1, ArmRole.TREATMENT,
     "S1 weekly money-finder: the highest pool-per-day programmes, at the best bid; $20 where "
     "the pool is >= $50/day else $10; <= 20 markets, <= 3 per event",
     {"strategy": "S1", "placement": "best", "size_usd": [10, 20]}),
    (ARM_S2_BEST, ArmRole.TREATMENT,
     "S2 at the best bid: the ranker's top quotable markets; $10; <= 10 markets",
     {"strategy": "S2", "placement": "best", "size_usd": 10}),
    (ARM_S2_BEHIND, ArmRole.CONTROL,
     "S2 one tick behind the best bid (the §9.72 rule; 1c stays 1c), same markets and hours",
     {"strategy": "S2", "placement": "behind_1_tick", "size_usd": 10}),
    (ARM_S3_ASIDE, ArmRole.TREATMENT,
     "S3 release-aware: BLS ladders, AAA gas, ski opening ladders; pulled 2 h before to 48 h "
     "after a verified BLS release, 12-16Z for gas, and on a one-event dump or last 3 days",
     {"strategy": "S3", "placement": "best", "size_usd": 10}),
    (ARM_S3_CONTROL, ArmRole.CONTROL,
     "S3 never pulled (only the common 48 h pre-close step-aside), same markets and hours",
     {"strategy": "S3", "placement": "best", "size_usd": 10}),
    (ARM_S4_BURST, ArmRole.TREATMENT,
     "S4 burst: programmes a $10 bid would leave in the $0.30-1.00 band; $50 until the "
     "accrued calibrated reward clears $1.25, timing from the programme-age study",
     {"strategy": "S4", "placement": "best", "size_usd": [50, 10]}),
    (ARM_S4_FLAT, ArmRole.CONTROL,
     "S4 flat: $10 at the best bid for the whole programme, same markets and hours",
     {"strategy": "S4", "placement": "best", "size_usd": 10}),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(session, *, actor: str, promotion_sample_floor: int | None = None,
             now: datetime | None = None) -> dict:
    """Create the experiment, freeze v1 with its seven arms (no gates — see the module
    docstring), open e1 on the ACTIVE platform snapshot, register the tagless PROBE deployment
    and move IDEA -> PROBE. `promotion_sample_floor` is accepted because the transport always
    passes it; there is no paper promotion gate here, so a value is refused rather than
    silently ignored. Idempotence is refusal."""
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "limm-paper-strategies has no promotion gate; promotion_sample_floor does not apply")
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package registers it once; "
            "a changed contract is a new Version")
    experiment = service.create_experiment(
        session, key=EXPERIMENT_KEY, origin="operator",
        title="LIMM paper strategies S1-S4 — where and how to rest incentive bids",
        family="liquidity_incentive",
        hypothesis=(
            "Among live liquidity-incentive programmes, one-sided cheap-side resting bids earn "
            "more calibrated reward than their fills cost in specific places and ways: the "
            "highest daily-pay programmes (S1), at the touch rather than one tick behind (S2), "
            "stepping aside around known releases (S3), and a short $50 burst that clears "
            "Kalshi's $1 minimum (S4)."),
        mechanism=(
            "Kalshi pays per contract resting near the best bid (LIP R4-R6); the touch scores "
            "double (discount factor 5000); fills cluster at releases and late certainty dumps; "
            "programmes under $1 per market pay nothing."),
        counterparty="the incentive pool itself; fills come from takers repricing the market",
        falsification=(
            "At the pre-registered read, an arm's net per $10 bid-day is below $0.15 or its "
            "lower bound is not above 0, or the whole-order fill model turns it into a loss; a "
            "pairwise difference whose 95% interval includes 0 decides nothing."),
        universe="live liquidity-incentive programmes the shadow collector watches",
        docs={"spec": SPEC_DOC, "age_study": AGE_STUDY_DOC, "handoff": HANDOFF_DOC,
              "instrument": INSTRUMENT, "read": READ_SCRIPT},
        notes=("Paper instrument only. Alimm1's experiment (liquidity-incentive-mm) is "
               "untouched; a nomination from this read is a separate owner decision."),
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis=experiment.hypothesis, mechanism=experiment.mechanism,
        counterparty=experiment.counterparty, falsification=experiment.falsification,
        universe_selector=experiment.universe,
        universe_exclusions=("programmes inside 48 h of close; sides above 10c; snapshots "
                             "that would not qualify (a side under Target Size) at selection"),
        entry_rule="rest one bid per arm and market at the arm's price, refreshed hourly",
        exit_rule="pull per the arm's rule; a filled bid is held (cost = 24 h markout)",
        sizing_rule="$10 per bid; S1 $20 at pool >= $50/day; S4-burst $50 during the burst",
        execution_style="maker (simulated)",
        independent_variable="placement (S2), step-aside (S3), burst sizing (S4)",
        held_constant=HELD_CONSTANT, control_required=True,
        metrics={"primary": "calibrated net per $10 bid-day after Kalshi's $1 minimum",
                 "secondary": ["fill rate per bid-hour", "fill cost at 24 h and settlement",
                               "paid c/h vs fill c/h", "share of programmes clearing $1",
                               "thin-rule hours"]},
        sample={"probe": PROBE_RULE},
        costs={"fill_models": ["conservative", "queue_aware", "optimistic", "whole_order"],
               "maker_fees": "recorded per fill by the shadow fee rule"},
        provenance={"book_and_tape": "Kalshi WebSocket orderbook_delta and trade (GET-only)",
                    "reward": "liquidity_incentive.scoring.estimate (lip-v1)"},
        monitoring={"counts": f"{READ_SCRIPT} --counts via the ops channel (rows only)"},
        docs={"spec": SPEC_DOC},
        now=at,
    )
    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)
    service.freeze_version(session, version, now=at)
    epoch = service.open_epoch(
        session, version,
        reason=("v1's first interval, pinned to the platform snapshot active at registration; "
                "the arms record from the activation switch onward"),
        started_at=at,
    )
    deployment = service.register_deployment(
        session, epoch, deployment_key=PROBE_DEPLOYMENT_KEY, stage=LifecycleState.PROBE,
        kind=DeploymentKind.PROBE, arms={arm_key: None for arm_key, _, _, _ in ARMS},
        config={"instrument": INSTRUMENT, "read": READ_SCRIPT, "activation_var": ACTIVATION_VAR},
        started_at=at,
        notes=("TAGLESS BY CONSTRUCTION: shadow-collector rows only; nothing here is "
               "admissible to trade under NEW_ONLY."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason="paper probe registered; the read rule is frozen on v1",
        occurred_at=at, version=version, epoch=epoch,
    )
    # ORM objects for the receipt builder (tests/test_xos_package_result_shape.py).
    return {
        "version": version, "epoch": epoch, "probe": deployment, "gates": [],
        "experiment": experiment.key, "state": experiment.state,
        "version_number": version.version, "epoch_number": epoch.epoch_number,
        "arms": [a for a, _, _, _ in ARMS], "gate_keys": [],
        "deployment": deployment.deployment_key, "tags": [],
    }
