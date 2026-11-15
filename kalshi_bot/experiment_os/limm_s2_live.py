"""LIMM S2 live test (`limm-s2-live`) — the contract, registered at PROBE. ARMS NOTHING.

Pre-registration: `docs/LIMM_S2_LIVE_TEST.md`. Rules: `kalshi_bot/liquidity_incentive/s2_live.py`
(every number here is imported from there, never retyped). Handoff:
`docs/handoffs/HANDOFF-limm-s2-live-test.md`.

WHAT IT ASKS
------------
First: on the markets the paper arm `S2-best` picks (gas, ski, data ladders — never checked
against a real payout), what does Kalshi actually pay per unit of our estimate? The paper arms
assume 1.34, measured only on mention and YouTube payouts. Second: do ~5 live $10 bids there
earn more in CONFIRMED payouts than their fills lose over two weeks?

WHAT THIS MODULE DOES AND DOES NOT DO
-------------------------------------
`register` creates the experiment, freezes v1 with the risk envelope and three gates, opens e1,
registers a TAGLESS probe deployment (the shadow collector, which already records `S2-best`)
and walks IDEA -> PROBE. No tag becomes admissible; nothing can trade.

`arm` (added with the runner, Build B) walks PROBE -> PAPER on a fresh probe-gate PASS, ends the
probe deployment, and calls `service.arm_live_canary` with fresh live and twin tags. It is sent
only as an owner-approved `ARM_CANARY` (a hard stop). The owner decided on 2026-11-14 to arm as
soon as Build B is merged rather than after the 2026-11-24 paper read (LIMM_S2_LIVE_TEST.md §0);
the arming reason records that. `activation_env` builds the env step (another hard stop).

WHY THE GATES SAY SO LITTLE
---------------------------
Evaluator metrics cannot see a payout: Kalshi has no payout endpoint, so confirmed payouts come
only from the owner's Rewards screenshots (`reward_ledger.CONFIRMED_REWARD_PAYOUTS`). So:
  * the probe and promotion gates are INSTRUMENT health (the same shape as
    `liquidity-incentive-mm`): the collector that supplies the live selection is current;
  * the keep gate is the LOSS side only — it can stop the book, never promote it;
  * the economic bars (net confirmed payouts minus fill losses; paid / estimated) are read once
    by hand at the end against `s2_live.read_labels`, as the pre-registration states.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ..liquidity_incentive import s2_live as s2
from . import service
from .lifecycle import ArmRole, GateVerdict, LifecycleState
from .models import ExperimentGate, ExperimentVersion
from .read import get_experiment, latest_version

EXPERIMENT_KEY = "limm-s2-live"
ARM_KEY = "s2best_live"

PAPER_TAG = s2.PAPER_TAG
LIVE_TAG = s2.LIVE_TAG
TWIN_TAG = s2.TWIN_TAG

#: No `mmsell_variants` spec: this book's economics are constants in `s2_live`.
BOOK_PARAMS: str | None = None

PROBE_DEPLOYMENT_KEY = "limm-s2-live-probe-1"
LIVE_DEPLOYMENT_KEY = "limm-s2-live-1"
TWIN_DEPLOYMENT_KEY = "limm-s2-live-twin-1"

PROBE_GATE_KEY = "s2_instrument_ready"
PROMOTION_GATE_KEY = "s2_paper_to_live"
KEEP_GATE_KEY = "s2_live_loss_stop"

SPEC_DOC = "docs/LIMM_S2_LIVE_TEST.md"
HANDOFF_DOC = "docs/handoffs/HANDOFF-limm-s2-live-test.md"
PAPER_SPEC_DOC = "docs/LIMM_PAPER_STRATEGIES.md"
RULES_MODULE = "kalshi_bot/liquidity_incentive/s2_live.py"

#: How quiet an armed arm may go before the global monitor reads it STALE. Five resting bids
#: re-placed only on a selection change or a fill: a day without a new order is normal.
MONITOR_EXPECT = {"max_silence_hours": 72,
                  "note": "five resting $10 bids; a placement follows only a selection change"}

_R = s2.DEFAULT_RULES

#: The pre-registered envelope. Every value comes from `s2_live.S2LiveRules`.
RISK_ENVELOPE: dict = {
    "stage": "live_s2_best_test",
    "question": ("does Kalshi pay on S2's markets what the paper arms estimate, and do ~5 "
                 "live $10 bids there earn more in confirmed payouts than fills lose"),
    "max_markets": _R.max_markets,
    "max_markets_per_event": _R.max_per_event,
    "bid_usd": _R.bid_usd,
    "max_resting_usd": _R.max_resting_usd,
    "max_contracts_per_bid": _R.max_contracts,
    "cheap_max_cents": _R.cheap_max_cents,
    "step_aside_hours_before_close": _R.step_aside_hours,
    "selection": (f"the paper arm {s2.SOURCE_ARM}'s OPEN quote rows; sticky; highest paper "
                  "reward rate first; one market per event; a $10 bid must project "
                  f">= ${_R.min_projected_usd:.2f} calibrated (x{_R.calibration}) to its "
                  "step-aside; never a market the account holds; never one this book filled"),
    "min_projected_usd": _R.min_projected_usd,
    "selection_calibration": _R.calibration,
    "excluded_series": sorted(s2.EXCLUDED_SERIES),
    "placement": "the paper arm's own side and price (the best bid on the cheap side)",
    "fills": "held to settlement; no stop-loss, take-profit or resting sell; never re-entered",
    "fill_loss_stop_usd": _R.fill_loss_stop_usd,
    "worst_case_loss_usd": _R.worst_case_loss_usd,
    "duration_days": _R.duration_days,
    "separate_from": "Alimm1 (liquidity-incentive-mm): own tags, own runner path, no shared rule",
    "left_alone": {
        "MAX_DAILY_LOSS / MAX_TOTAL_EXPOSURE": "account-wide; not changed for this book",
        "LIVE_PAPER_TWIN_SUFFIX": "fleet-wide; this book pins its twin through LIVE_PAPER_TWINS",
        "LIQUIDITY_INCENTIVE_LIVE_ENABLED": "Alimm1's switch; stays false (thesis §9.78)",
    },
}

#: PROBE -> PAPER: the shadow collector that supplies the selection is running and current.
PROBE_GATE_SPEC: dict = {
    "description": ("INSTRUMENT check: the shadow collector (which records S2-best, the live "
                    "book's selection source) is running and its programme list is current. "
                    "No clause reads reward or P&L."),
    "hold_if": [
        {"metric": "incentive_discovery_cycles", "scope": "experiment",
         "deployment_kind": "probe", "op": "<", "value": 12},
        {"metric": "incentive_shadow_quotes", "scope": "experiment",
         "deployment_kind": "probe", "op": "<", "value": 50},
    ],
    "fail_any": [
        {"metric": "incentive_discovery_error_pct", "scope": "experiment",
         "deployment_kind": "probe", "op": ">", "value": 75.0},
    ],
    "pass_all": [
        {"metric": "incentive_discovery_error_pct", "scope": "experiment",
         "deployment_kind": "probe", "op": "<=", "value": 25.0},
        {"metric": "incentive_programs_observed", "scope": "experiment",
         "deployment_kind": "probe", "op": ">=", "value": 10},
    ],
}

#: PAPER -> LIVE_CANARY: the same instrument over a thicker sample. NOT the economic
#: authorization — that is the 2026-11-24 paper read (S2-best PROMISING) plus the owner's go,
#: which the arming step (Build B) must name in its reason. Stated here so no reader takes a
#: PASS on this gate for evidence that S2 pays.
PROMOTION_GATE_SPEC: dict = {
    "description": ("Readiness only: the selection source is healthy over a thicker sample. "
                    "The economic precondition is the 2026-11-24 paper read showing S2-best "
                    "PROMISING, plus the owner's explicit go; neither is visible to a gate."),
    "hold_if": [
        {"metric": "incentive_shadow_quotes", "scope": "experiment",
         "deployment_kind": "probe", "op": "<", "value": 200},
        {"metric": "incentive_shadow_outcomes", "scope": "experiment",
         "deployment_kind": "probe", "op": "<", "value": 50},
        {"metric": "incentive_programs_observed", "scope": "experiment",
         "deployment_kind": "probe", "op": "<", "value": 20},
    ],
    "fail_any": [
        {"metric": "incentive_discovery_error_pct", "scope": "experiment",
         "deployment_kind": "probe", "op": ">", "value": 50.0},
    ],
    "pass_all": [
        {"metric": "incentive_discovery_error_pct", "scope": "experiment",
         "deployment_kind": "probe", "op": "<=", "value": 10.0},
    ],
}

#: The loss side of the stop rules, on the live deployment. A FAIL stops the book; a PASS
#: authorizes nothing (payouts are invisible to the evaluator — the read is by hand).
#: Realized only: held fills marked at the best bid are watched by Live Ops at the 24 h and
#: daily checks against the same $30 (handoff §5), because no metric marks an open position.
KEEP_GATE_SPEC: dict = {
    "description": ("Loss stop for the S2 live test. Every clause addresses "
                    "deployment_kind='live'. FAIL = stop. PASS = the envelope held so far; it "
                    "is not evidence that S2 pays."),
    "sample": {
        ARM_KEY: {"metric": "live_settled_contracts", "deployment_kind": "live",
                  "op": ">=", "value": 1},
    },
    "fail_any": [
        {"metric": "live_realized_pnl_usd", "arm": ARM_KEY, "deployment_kind": "live",
         "op": "<=", "value": -_R.fill_loss_stop_usd,
         "min_evidence": {"metric": "live_settled_contracts", "op": ">=", "value": 1}},
        # One settled market losing more than one whole bid (plus a cent of fees per contract
        # at most) means the $10 sizing is not being applied.
        {"metric": "live_max_realized_loss_usd", "arm": ARM_KEY, "deployment_kind": "live",
         "op": ">", "value": _R.bid_usd + 0.50,
         "min_evidence": {"metric": "live_settled_contracts", "op": ">=", "value": 1}},
    ],
    "pass_all": [
        {"metric": "live_max_realized_loss_usd", "arm": ARM_KEY, "deployment_kind": "live",
         "op": "<=", "value": _R.bid_usd + 0.50},
    ],
}

READ_RULE: dict = {
    "when": "once, 7 days after the book stops (payouts land after programmes end)",
    "primary": (f"net = confirmed payouts - fill losses over the test; SCALE-CANDIDATE when "
                f"net >= ${s2.SCALE_NET_USD:.0f} and the ratio HOLDS; STOP when net <= 0 or the "
                f"loss stop fired; EXTEND under {s2.MIN_BID_DAYS:.0f} live $10 bid-days or "
                f"{s2.MIN_PROGRAMMES} programmes; else KEEP"),
    "secondary": (f"paid / uncalibrated estimate on this book's own resting time, pooled and per "
                  f"programme: HOLDS >= {s2.RATIO_HOLDS}, WEAK >= {s2.RATIO_FAILS}, else FAILS"),
    "payouts": "only rows in reward_ledger.CONFIRMED_REWARD_PAYOUTS (owner Rewards screenshots)",
    "consequence": "every label is a recommendation to the owner; nothing transitions on it",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(session, *, actor: str = "research-lab", promotion_sample_floor: int | None = None,
             now: datetime | None = None) -> dict:
    """Create the experiment, freeze v1 (envelope, one arm, three gates), open e1, register
    the tagless probe and walk IDEA -> PROBE. Arms nothing. A promotion floor may only raise
    the promotion gate's thinness bar; the transport always passes the argument."""
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — registered once; a changed "
            "contract is a new Version")
    promo = PROMOTION_GATE_SPEC
    if promotion_sample_floor is not None:
        registered = next(c["value"] for c in PROMOTION_GATE_SPEC["hold_if"]
                          if c["metric"] == "incentive_shadow_quotes")
        if int(promotion_sample_floor) < registered:
            raise service.ExperimentOsError(
                f"promotion_sample_floor={promotion_sample_floor} is below the registered "
                f"{registered}; an envelope may tighten a gate, never loosen one")
        promo = {**PROMOTION_GATE_SPEC, "hold_if": [
            ({**c, "value": int(promotion_sample_floor)}
             if c["metric"] == "incentive_shadow_quotes" else c)
            for c in PROMOTION_GATE_SPEC["hold_if"]]}
    experiment = service.create_experiment(
        session, key=EXPERIMENT_KEY, origin="operator",
        title="LIMM S2 live test — $10 bids at the best bid on S2's markets",
        family="liquidity_incentive",
        hypothesis=(
            "On the markets the paper arm S2-best selects, Kalshi pays close to what the "
            "collector estimates (paid / estimate near the 1.34 the paper arms assume), and "
            "about five live $10 bids at the best bid earn more in confirmed payouts than "
            "their fills lose over two weeks."),
        mechanism=("Kalshi pays per contract resting near the best bid (LIP); the touch scores "
                   "at full weight; programmes under $1 per market pay nothing."),
        counterparty="the incentive pool; fills come from takers repricing the market",
        falsification=(
            "Over the two weeks, confirmed payouts minus fill losses are <= $0, or the "
            "loss stop fires; paid / estimated below 0.5 says the paper reward level is "
            "not real on these markets."),
        universe="the paper arm S2-best's current selection (open shadow quote rows)",
        docs={"spec": SPEC_DOC, "handoff": HANDOFF_DOC, "paper_spec": PAPER_SPEC_DOC,
              "rules": RULES_MODULE},
        notes=("Separate from Alimm1 (liquidity-incentive-mm). Waits on the 2026-11-24 paper "
               "read (S2-best PROMISING) and the owner's go; arming is a Live Ops hard stop."),
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis=experiment.hypothesis, mechanism=experiment.mechanism,
        counterparty=experiment.counterparty, falsification=experiment.falsification,
        universe_selector=RISK_ENVELOPE["selection"],
        universe_exclusions=("series the owner paused (KXYTVIEWSHIGH); markets the account "
                             "holds under any tag; markets this book has filled on; sides "
                             "above 10c; within 48 h of close"),
        entry_rule=RISK_ENVELOPE["placement"] + ", refreshed with the paper arm",
        exit_rule=RISK_ENVELOPE["fills"],
        sizing_rule=f"${_R.bid_usd:.0f} per bid, <= {_R.max_markets} markets, "
                    f"<= ${_R.max_resting_usd:.0f} resting",
        execution_style="maker",
        independent_variable="none — the test measures the real payout ratio and net",
        held_constant=[
            "the paper arm S2-best's rules (selection, side, price) — copied, not changed",
            "the selection calibration (1.34), used to rank only; the read measures the real one",
            "Alimm1 untouched: no rule, universe, size or env change",
        ],
        control_required=False,
        control_exemption_reason=(
            "the comparison is the paper arm S2-best on the same markets in the same hours "
            "(its simulated fills and calibrated reward), plus the mandatory XOS twin at the "
            "arming instant; there is no second live treatment to control for"),
        risk=RISK_ENVELOPE,
        metrics={"primary": "confirmed payouts - fill losses over the test (USD)",
                 "secondary": ["paid / uncalibrated estimate, pooled and per programme",
                               "fills per bid-day vs the paper arm's conservative model",
                               "fill loss at settlement vs the paper arm's 24 h markout"]},
        sample={"read": READ_RULE},
        docs={"spec": SPEC_DOC, "rules": RULES_MODULE},
        change_reason="initial version",
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description=(f"live $10 bids at the best bid on <= {_R.max_markets} of S2-best's "
                     "markets, one per event"),
        params={"source_arm": s2.SOURCE_ARM, "bid_usd": _R.bid_usd,
                "max_markets": _R.max_markets, "max_resting_usd": _R.max_resting_usd},
        strategy_tag=PAPER_TAG,
    )
    gates = [
        service.register_gate(session, version, gate_key=PROBE_GATE_KEY, kind="promotion",
                              spec=PROBE_GATE_SPEC, from_state=LifecycleState.PROBE,
                              to_state=LifecycleState.PAPER, registered_at=at,
                              notes="instrument health only"),
        service.register_gate(session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
                              spec=promo, from_state=LifecycleState.PAPER,
                              to_state=LifecycleState.LIVE_CANARY, registered_at=at,
                              notes=("readiness only; the economic precondition is the 11-24 "
                                     "paper read plus the owner's go")),
        service.register_gate(session, version, gate_key=KEEP_GATE_KEY, kind="kill",
                              spec=KEEP_GATE_SPEC, registered_at=at,
                              notes="loss side only; pre-registered before arming"),
    ]
    service.freeze_version(session, version, now=at)
    for g in gates:
        service.mark_gate_evidence_started(session, g, at=at)
    epoch = service.open_epoch(
        session, version,
        reason="e1 — the contract frozen before any live data; probe = the shadow collector",
        started_at=at,
    )
    probe = service.register_deployment(
        session, epoch, deployment_key=PROBE_DEPLOYMENT_KEY, stage=LifecycleState.PROBE,
        kind="probe", arms={ARM_KEY: None}, started_at=at,
        notes="TAGLESS: the shadow collector's S2-best arm; nothing here can trade",
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason="contract registered before live data; no tag admissible",
        occurred_at=at, version=version, epoch=epoch,
    )
    return {
        "version": version, "epoch": epoch, "probe": probe, "gates": gates,
        "experiment": experiment.key, "state": experiment.state,
        "version_number": version.version, "epoch_number": epoch.epoch_number,
        "arms": [ARM_KEY], "gate_keys": [g.gate_key for g in gates],
        "deployment": probe.deployment_key, "tags": [],
    }


def material_config(*, live_tag: str = LIVE_TAG, twin_tag: str = TWIN_TAG) -> dict:
    """The live deployment's `config_json`: the drift baseline plus the envelope."""
    from . import enforcement

    return {"material": enforcement.live_material_block(books={live_tag: (twin_tag, BOOK_PARAMS)}),
            "risk_envelope": RISK_ENVELOPE}


def _gate(session, version: ExperimentVersion, gate_key: str) -> ExperimentGate:
    gate = session.scalar(select(ExperimentGate).where(
        ExperimentGate.version_id == version.id, ExperimentGate.gate_key == gate_key))
    if gate is None:
        raise service.ExperimentOsError(f"gate {gate_key!r} not registered — REGISTER_PACKAGE first")
    return gate


ARM_REASON = ("S2 live test armed on the owner's decision of 2026-11-14, DEC-047 (arm as soon as the "
              "runner is merged, before the 2026-11-24 paper read); envelope pre-registered in "
              "docs/LIMM_S2_LIVE_TEST.md")


def arm(session, *, approved_by: str, actor: str = "operator",
        started_at: datetime | None = None, reason: str | None = None) -> dict:
    """PROBE -> PAPER on a fresh PASS of the probe gate (ending the probe deployment, which
    `arm_live_canary` cannot carry across the live boundary), then arm the canary and its twin
    at one instant. EXPANDS REAL-MONEY CAPABILITY; places nothing until LIVE_STRATEGIES names
    the tag and LIQUIDITY_INCENTIVE_S2_LIVE_ENABLED is on (separate, owner-approved acts)."""
    from .evaluator import evaluate_gate
    from .liquidity_incentive_mm import _end_probe_deployments
    from .models import ExperimentGateResult

    at = started_at or _now()
    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        raise service.ExperimentOsError(f"{EXPERIMENT_KEY!r} not found — send REGISTER_PACKAGE first")
    version = latest_version(session, experiment)
    if experiment.state == LifecycleState.PROBE.value:
        outcome = evaluate_gate(session, _gate(session, version, PROBE_GATE_KEY))
        if outcome.verdict != GateVerdict.PASS.value:
            raise service.ExperimentOsError(
                f"probe gate {PROBE_GATE_KEY!r} evaluates {outcome.verdict}, not PASS: "
                f"{outcome.explanation}")
        service.transition_experiment(
            session, experiment, LifecycleState.PAPER, actor=actor,
            gate_result=session.get(ExperimentGateResult, outcome.result_id), occurred_at=at,
            version=version,
            reason="instrument healthy on post-registration evidence; PAPER only to become armable")
        _end_probe_deployments(session, version, at=at)
    if experiment.state != LifecycleState.PAPER.value:
        raise service.ExperimentOsError(f"{EXPERIMENT_KEY} is {experiment.state}; arms from PAPER")
    live, twin, epoch = service.arm_live_canary(
        session, experiment, gate=_gate(session, version, PROMOTION_GATE_KEY),
        approved_by=approved_by, live_key=LIVE_DEPLOYMENT_KEY, twin_key=TWIN_DEPLOYMENT_KEY,
        live_tags={ARM_KEY: LIVE_TAG}, twin_tags={ARM_KEY: TWIN_TAG},
        config=material_config(), started_at=at, actor=actor, reason=reason or ARM_REASON)
    return {"live": live, "twin": twin, "epoch": epoch}


#: The switch-on settings. Caps are constants in `s2_live`, never env vars.
ACTIVATION_SETTINGS: dict[str, str] = {"LIQUIDITY_INCENTIVE_S2_LIVE_ENABLED": "true"}


def activation_env(*, current_live_strategies: str, current_live_paper_twins: str = "") -> dict:
    """The env step, DERIVED from the running values: appends this book to LIVE_PAPER_TWINS and
    LIVE_STRATEGIES (last) and never drops another tag. Builds the mapping only; applying it is
    a Live Ops act on the owner's go."""
    from .liquidity_incentive_mm import _append_token

    env = dict(ACTIVATION_SETTINGS)
    env["LIVE_PAPER_TWINS"] = _append_token(current_live_paper_twins, f"{LIVE_TAG}:{TWIN_TAG}",
                                            key=LIVE_TAG)
    env["LIVE_STRATEGIES"] = _append_token(current_live_strategies, LIVE_TAG)
    new = env["LIVE_STRATEGIES"].split(",")
    for tag in (t.strip() for t in (current_live_strategies or "").split(",")):
        if tag and tag not in new:
            raise service.ExperimentOsError(f"derived allowlist would drop {tag!r}")
    return env


ACTIVATION_VARS: frozenset[str] = frozenset(
    {"LIVE_STRATEGIES", "LIVE_PAPER_TWINS", *ACTIVATION_SETTINGS})


__all__ = [
    "ACTIVATION_VARS", "ARM_REASON", "BOOK_PARAMS", "activation_env", "arm", "material_config",
    "ARM_KEY", "EXPERIMENT_KEY", "KEEP_GATE_KEY", "KEEP_GATE_SPEC", "LIVE_DEPLOYMENT_KEY",
    "LIVE_TAG", "PAPER_TAG", "PROBE_DEPLOYMENT_KEY", "PROBE_GATE_KEY", "PROBE_GATE_SPEC",
    "PROMOTION_GATE_KEY", "PROMOTION_GATE_SPEC", "READ_RULE", "RISK_ENVELOPE",
    "TWIN_DEPLOYMENT_KEY", "TWIN_TAG", "register",
]
