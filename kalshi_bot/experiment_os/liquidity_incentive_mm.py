"""The liquidity-incentive one-sided live smoke test: contract, envelope, gates, arming.

This module is DATA plus three functions. It places no orders, arms nothing on import, and is
not wired into the trading worker or the read-only ops channel. Nothing here runs until an
operator sends a `REGISTER_PACKAGE` envelope naming `liquidity-incentive-mm`, and the act that
can spend money is a second, separately-approved `ARM_CANARY`.

Scientific contract: `docs/LIQUIDITY_INCENTIVE_THESIS.md`. The Phase 0 pre-registration in §6
of that document is FROZEN and is **not** what this package gates on; §10 is, and the two are
deliberately different questions. Confusing them is the failure this docstring exists to
prevent, so it is worth being blunt about:

    Phase 0 asks whether quoting incentivized markets can earn $1/day net at $100-$500.
    THIS package asks whether one $1 resting bid can go all the way through our own plumbing.

A PASS here says the pipe works and the declared envelope held. It says NOTHING about the
strategy's economics, and at a $10 book ceiling it CANNOT: the reward on one contract is cents
a day against the ~$3/day the Phase 0 bar was written for. Nobody may read a PASS on
`live_canary_keep` as evidence for or against the thesis.

WHY THIS NEEDS A WHOLE EXPERIMENT TO PLACE ONE ORDER
---------------------------------------------------
Enforcement is NEW_ONLY. A strategy tag that does not resolve to an active deployment arm is
refused at the write path, so `Alimm1` cannot place an order until it is registered — and the
only sanctioned way a tag becomes live is `service.arm_live_canary`, which requires a PAPER
experiment, a frozen version, a pre-registered risk envelope, a mandatory paper twin on fresh
tags, and a promotion gate that PASSES on a synchronous re-evaluation. That is the answer to
"can we just place one trade": no, and the reason is the point of the system.

WHY `arm` WALKS PROBE -> PAPER FIRST
-----------------------------------
`register` leaves the experiment at PROBE, with every gate's evidence clock starting at
registration. That is deliberate: this session has already read the shadow instrument's day-0
output, so a gate registered now over a window that INCLUDES that output would not be a
pre-registration at all — it would be a bar chosen after seeing the numbers. Starting the clock
at registration means the probe gate is judged on evidence gathered after its own bar was
frozen, which costs a day and buys the only thing a gate is for.

So `arm` does two things in one approved act: it re-evaluates the probe gate synchronously and
refuses to continue on anything but PASS (a check the engine does NOT require for PROBE→PAPER —
this package is stricter than the engine on purpose), records that PASS on the PROBE→PAPER
transition, and only then calls `arm_live_canary`, which re-evaluates the promotion gate itself.
Two independent, freshly-computed PASSes stand between an envelope and a real order.

WHAT IT STILL CANNOT DO
-----------------------
`arm` opens no allowlist. `LIVE_STRATEGIES` is a separate Live Ops act through the env channel,
and until it names `Alimm1` this book places nothing, armed or not.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ..liquidity_incentive import live as limm
from . import service
from .lifecycle import ArmRole, GateVerdict, LifecycleState
from .models import ExperimentGate, ExperimentVersion
from .read import get_experiment, latest_version

EXPERIMENT_KEY = "liquidity-incentive-mm"
ARM_KEY = "limm1side"

#: Tags are IMPORTED from the runtime module, never retyped. The runtime decides what it
#: trades under; Experiment OS must register exactly that, and a second literal here is how
#: the two silently diverge.
PAPER_TAG = limm.PAPER_TAG
LIVE_TAG = limm.LIVE_TAG
TWIN_TAG = limm.TWIN_TAG

#: This book has NO `mmsell_variants` spec, and that is what None states. The live/paper twin
#: harness builds the book from the runtime allowlist; its economics are the module constants in
#: `liquidity_incentive.live`, which the config-drift detector does not read. Declaring None
#: rather than omitting the name is deliberate: `runtime_config_check` recomputes `book_params`
#: for every named tag and would find None here too, so declared and running agree, and the
#: live-arming package contract (`tests/test_live_material_baseline_contract.py`) can still hold
#: this book to the same shape as every other. Registering an invented spec would instead put
#: the book permanently in EXPERIMENT_CONFIG_DRIFT.
BOOK_PARAMS: str | None = None

PROBE_DEPLOYMENT_KEY = "limm-shadow-probe-1"
LIVE_DEPLOYMENT_KEY = "limm-smoke-live-1"
TWIN_DEPLOYMENT_KEY = "limm-smoke-twin-1"

PROBE_GATE_KEY = "shadow_instrument_ready"
PROMOTION_GATE_KEY = "paper_to_live_smoke"
KEEP_GATE_KEY = "live_canary_keep"

THESIS_DOC = "docs/LIQUIDITY_INCENTIVE_THESIS.md"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# The risk envelope
# ---------------------------------------------------------------------------

#: Every cap here is a MODULE CONSTANT in `liquidity_incentive.live`, not a runtime setting,
#: and `tests/test_liquidity_incentive_xos_package.py` asserts the two are equal. That choice is
#: load-bearing in both directions:
#:
#:   * a code constant cannot drift without a pull request and a redeploy, which is a stronger
#:     guarantee than the config-drift detector gives a setting;
#:   * but it also means the detector has nothing to compare, so `book_params` is registered as
#:     None below rather than as a spec the runtime does not carry. Registering a spec the
#:     runtime cannot produce would put this book permanently in EXPERIMENT_CONFIG_DRIFT.
#:
#: Sizing arithmetic: the book rests ONE contract on the cheaper side at a price capped at 25c,
#: so a clip costs at most $0.25 and the entire downside of a filled clip is that $0.25. The
#: per-order dollar cap is $1.00 and binds only if the price cap is ever raised.
RISK_ENVELOPE: dict = {
    "stage": "smoke_test_stage_1a",
    "question": "does one resting bid survive our own plumbing end to end",
    "contracts_per_order": limm.MAX_CONTRACTS_PER_ORDER,
    "max_order_dollars": limm.MAX_ORDER_DOLLARS,
    "max_price_cents": limm.MAX_PRICE_CENTS,
    "max_open_orders": limm.MAX_OPEN_ORDERS,
    "max_book_exposure_usd": limm.MAX_STRATEGY_EXPOSURE_USD,
    "max_loss_per_clip_usd": round(limm.MAX_PRICE_CENTS / 100.0, 2),
    "sides_quoted": 1,
    "exit_policy": (
        "hold to settlement. NOT a setting: `LiveExecutor.manage_exits` skips this book's tags "
        "outright (limm.owns_tag), because the process-wide TP/SL rules would be an exit rule "
        "this envelope never declared, placed with real money and real fees"
    ),
    "settings": {
        "LIQUIDITY_INCENTIVE_LIVE_ENABLED": "true",
        "LIVE_MAX_ORDER_DOLLARS": "1.0",
        "MAX_MARKET_EXPOSURE": "1.0",
        "MAX_DAILY_LOSS": "5.0",
        "LIVE_KILL_ON_DAILY_LOSS": "true",
        "LIVE_ORDER_TIMEOUT_SECONDS": "14400",
        "LIVE_PAPER_TWIN_SUFFIX": limm.TWIN_SUFFIX,
    },
    "left_alone": {
        "MAX_TOTAL_EXPOSURE": (
            "portfolio-wide and SHARED with the running mmsell canary. Not tightened and not "
            "loosened here: this book limits what it opens, not money other books already "
            "hold. It still applies as a backstop — `_total_exposure_hit` refuses this book's "
            "entries alongside every other when the portfolio cap is reached"
        ),
        "MAX_DAILY_LOSS": (
            "already 5.0 in production and SHARED. Named in `settings` above as a fact of the "
            "envelope, not as a change — this book must not move a breaker another live book "
            "is relying on"
        ),
        "LIVE_EXIT_MODE": (
            "production carries tp_sl for the YES/weather books and this book must not change "
            "it. Its own hold-to-settlement contract is enforced by the tag skip in "
            "manage_exits instead, which touches no other book"
        ),
    },
    "enforced_by": {
        "contracts_per_order": "liquidity_incentive.live.build_live_quote, re-asserted by "
                               "LiveExecutor.mirror_incentive_entry (gate:size)",
        "max_price_cents": "build_live_quote refuses a cheaper-touch price above the cap; "
                           "mirror_incentive_entry re-asserts it",
        "max_open_orders": "repo.count_live_book_open (gate:open_cap)",
        "max_book_exposure_usd": "repo.live_strategy_exposure (gate:strategy_exposure)",
        "market_exposure": "LiveExecutor._market_exposure (gate:exposure)",
        "daily_loss": "LiveExecutor._daily_loss_hit (gate:daily_loss) — SHARED with mmsell",
        "no_contested_markets": "repo.live_buy_exists_for_ticker / live_open_order_exists, "
                                "strategy-agnostic ON PURPOSE (gate:dedup): this book will "
                                "never place a second order on a ticker another book is "
                                "resting in, and that is also why it can only quote ONE side",
        "order_lifetime": "LiveExecutor.reconcile timeout-cancel at LIVE_ORDER_TIMEOUT_SECONDS",
    },
    "genuine_liquidity": (
        "There is no branch in this book that pulls a resting quote. Orders leave the book by "
        "the shared paths only — a fill, the per-order timeout, or drain_stood_down_books when "
        "the allowlist drops the tag. A quote we would cancel the moment it might trade is not "
        "liquidity, and the whole premise of this strategy is that ours is."
    ),
    "stand_down": (
        "Clearing LIVE_STRATEGIES of Alimm1 stops new entries on the next cycle and drains "
        "resting orders within a cycle; any held contract settles normally and remains real "
        "money — at most $10, and at these caps at most $0.25 per market. The twin stands down "
        "with live, because the pairing derives from LIVE_STRATEGIES."
    ),
}


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

#: PROBE -> PAPER. Reads the SHADOW INSTRUMENT and nothing else: has the collector been running,
#: is our program list current, and is it seeing a real universe. Every clause is experiment-
#: scoped at deployment_kind='probe', because the instrument observes a market universe rather
#: than a book — an arm-scoped read of it would imply a split that does not exist.
#:
#: The thinness clauses are `hold_if` rather than a `sample` floor for a structural reason: a
#: `sample` floor is keyed by ARM, and an arm-scoped read of an experiment-wide metric is
#: refused by the provider (MISSING, with the addressing error named). HOLD-on-thin is exactly
#: what a sample floor buys, expressed where the metric can honestly be addressed.
PROBE_GATE_SPEC: dict = {
    "description": (
        "Is the shadow instrument actually running and current? This is an INSTRUMENT check, "
        "not an opportunity check — no clause here reads P&L, reward or edge, and a PASS says "
        "only that the data the live runner will select from is fresh and real."
    ),
    "hold_if": [
        {"metric": "incentive_discovery_cycles", "scope": "experiment",
         "deployment_kind": "probe", "op": "<", "value": 12},
        {"metric": "incentive_shadow_quotes", "scope": "experiment",
         "deployment_kind": "probe", "op": "<", "value": 50},
    ],
    "fail_any": [
        # A program list this stale is not a thin sample, it is a broken instrument.
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

#: PAPER -> LIVE_CANARY. The bar that stands between an envelope and a real order.
#:
#: What it deliberately does NOT contain is a profitability clause, and the reason is honesty
#: rather than convenience: at one contract and a $10 ceiling this book cannot earn a
#: measurable reward, so a P&L bar here would either be unsatisfiable or so loose it certified
#: nothing. What CAN be certified before spending $10 is that the instrument selecting the
#: markets is healthy and has seen enough of them, over a thicker sample than the probe gate
#: asked for. That is what this gate says, and it is all it says.
PROMOTION_GATE_SPEC: dict = {
    "description": (
        "Readiness to place ONE $1 resting bid: the shadow instrument is healthy over a "
        "thicker sample, its program list is current, and it has completed real quote "
        "lifecycles in the universe the live runner will select from. NOT an economic bar — "
        "this book is too small to carry one, and §6 of the thesis is where the economics are "
        "pre-registered."
    ),
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

#: The keep/stop contract, registered BEFORE arming so no threshold is chosen after a result.
#:
#: Its PASS clause is the smoke test's whole claim, stated as narrowly as it deserves: every
#: settled market stayed inside the declared per-clip downside. That is "the plumbing ran and
#: the envelope held". It authorizes nothing — there is no promotion path out of this book, and
#: the next question (does two-sided quoting earn anything) is a different experiment with a
#: different universe, a different envelope and a different bar.
KEEP_GATE_SPEC: dict = {
    "description": (
        "Keep/stop for the one-sided live smoke test. Every clause addresses "
        "deployment_kind='live' explicitly. A PASS means the pipe worked inside its envelope; "
        "it is not evidence about the strategy."
    ),
    "sample": {
        ARM_KEY: {"metric": "live_settled_contracts", "deployment_kind": "live",
                  "op": ">=", "value": 3},
    },
    "max_evidence_horizon": {
        "metric": "live_settled_contracts", "value": 25,
        "arms": [ARM_KEY], "deployment_kind": "live",
    },
    "fail_any": [
        # Half the declared book budget. Reaching it at $0.25 a clip means something other
        # than this envelope is spending money under this tag.
        {"metric": "live_realized_pnl_usd", "arm": ARM_KEY, "deployment_kind": "live",
         "op": "<=", "value": -5.0,
         "min_evidence": {"metric": "live_settled_contracts", "op": ">=", "value": 1}},
        # A resting bid's downside is the price paid, capped at 25c. A settled market losing
        # more than one full clip means the envelope is not being applied — sizing, the
        # hold-to-settlement assumption, or the accounting is wrong.
        {"metric": "live_max_realized_loss_usd", "arm": ARM_KEY, "deployment_kind": "live",
         "op": ">", "value": 1.0,
         "min_evidence": {"metric": "live_settled_contracts", "op": ">=", "value": 1}},
    ],
    "pass_all": [
        {"metric": "live_max_realized_loss_usd", "arm": ARM_KEY, "deployment_kind": "live",
         "op": "<=", "value": 1.0},
    ],
}

#: Thresholds with no repository precedent, and the reasoning behind each. Recorded here
#: rather than only in a pull request because the reason a number is what it is outlives the
#: pull request, and a reader is owed the difference between a registered precedent and a
#: choice someone made once.
OPERATOR_DECISIONS: dict[str, str] = {
    "book budget $10, 3 orders, $1/order":
        "the operator's own stated guardrails for this test (2026-09-17), taken verbatim.",
    "price cap 25c":
        "not asked for and added here. The operator's $1 per-order limit bounds the order; the "
        "price cap bounds the LOSS, which at one contract is the price paid. 25c keeps a clip's "
        "downside at a quarter and keeps the book in the cheap tail where a resting bid is "
        "least likely to be picked off by information.",
    "one side, not two":
        "the shared per-ticker dedup gate refuses a second resting order on a ticker, and that "
        "gate also guards the running mmsell canary. Kalshi scores YES and NO separately, so "
        "one bid still earns — but this test therefore says nothing about two-sided pairing.",
    "probe gate floors (12 cycles, 50 quotes, 10 programs)":
        "roughly an hour of discovery at the 5-minute cadence, and enough quotes that the "
        "shadow is demonstrably quoting rather than merely connected. No precedent existed.",
    "promotion gate floors (200 quotes, 50 outcomes, 20 programs)":
        "a thicker sample than the probe gate for the act that spends money. No precedent.",
    "no profitability clause on the promotion gate":
        "deliberate. At $10 the reward is cents; a P&L bar would be theatre. Stated in the "
        "gate description so no reader mistakes its absence for an oversight.",
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _promotion_spec(sample_floor: int | None) -> dict:
    """The promotion gate, with the transport's optional sample floor applied.

    `_register_package` always passes `promotion_sample_floor=`, so every package must accept
    it. Here it raises the gate's THINNESS bar — the `incentive_shadow_quotes` hold_if — because
    that clause is this gate's sample floor in all but name. (It is a hold_if rather than a
    `sample` block for a structural reason: a `sample` floor is keyed by ARM, and an arm-scoped
    read of an experiment-wide metric is refused by the provider.)

    It can only ever be RAISED. A floor below the registered 200 would loosen a pre-registered
    bar from an environment variable, which is the one thing the envelope's narrow vocabulary
    exists to prevent."""
    if sample_floor is None:
        return PROMOTION_GATE_SPEC
    floor = int(sample_floor)
    registered = next(c["value"] for c in PROMOTION_GATE_SPEC["hold_if"]
                      if c["metric"] == "incentive_shadow_quotes")
    if floor < registered:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is BELOW the registered thinness bar "
            f"({registered} shadow quotes). An envelope may tighten a pre-registered gate, "
            "never loosen one — register a new version if the bar itself should change."
        )
    spec = {k: (list(v) if isinstance(v, list) else v) for k, v in PROMOTION_GATE_SPEC.items()}
    spec["hold_if"] = [
        ({**c, "value": floor} if c["metric"] == "incentive_shadow_quotes" else c)
        for c in PROMOTION_GATE_SPEC["hold_if"]
    ]
    return spec


def material_config(*, live_tag: str = LIVE_TAG, twin_tag: str = TWIN_TAG) -> dict:
    """The live deployment's `config_json`. `book_params` is None for both tags because this
    book's parameters are CODE constants, not an `mmsell_variants` spec — see RISK_ENVELOPE."""
    from . import enforcement

    return {
        "material": enforcement.live_material_block(books={live_tag: (twin_tag, BOOK_PARAMS)}),
        "risk_envelope": RISK_ENVELOPE,
    }


def register(session, *, actor: str = "operator", promotion_sample_floor: int | None = None,
             now: datetime | None = None) -> dict:
    """Create the experiment, freeze v1 with its envelope and all three gates, open e1, register
    the tagless shadow probe deployment, and walk IDEA -> PROBE.

    Stops at PROBE on purpose (see the module docstring): the gates' evidence clocks start here,
    so they are judged on data gathered after their bars were frozen. Arms nothing, places
    nothing, opens no exposure."""
    at = now or _now()
    promo = _promotion_spec(promotion_sample_floor)
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package registers it once"
        )
    experiment = service.create_experiment(
        session,
        key=EXPERIMENT_KEY,
        title="Liquidity-incentive market making — one-sided live smoke test",
        origin="operator",
        family="liquidity_incentive",
        mechanism=(
            "Kalshi pays a liquidity reward for resting size near the Reference Price. A maker "
            "bid earns score while it rests; its only downside is the price paid if it fills."
        ),
        falsification=(
            "The order is refused, never rests, is never scored, contests another book's "
            "market, or settles outside the declared per-clip downside."
        ),
        universe=(
            "markets carrying an active Kalshi liquidity incentive programme "
            "(GET /incentive_programs)"
        ),
        docs={"thesis": THESIS_DOC,
              "research": "docs/LIQUIDITY_INCENTIVE_RESEARCH.md",
              "workstream": "docs/workstreams/WS-020-liquidity-incentive-shadow.md"},
        hypothesis=(
            "A single genuine resting maker bid on an incentivized Kalshi market can be placed, "
            "rest, be scored by the liquidity incentive programme, and settle or expire, through "
            "our own order path — without contesting another book's market and without leaving "
            "its declared risk envelope. This is a PLUMBING claim; the economic claim is §6 of "
            f"{THESIS_DOC} and is NOT gated here."
        ),
        actor=actor,
        now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis=(
            "One post-only bid, one contract, on the cheaper touch of a two-sided incentivized "
            "market whose both sides already meet Target Size, held to settlement."
        ),
        universe_selector=(
            "current liquidity incentive programmes (GET /incentive_programs, status=active) "
            "whose market is open, whose programme has at least "
            f"{limm.MIN_PROGRAM_HOURS_REMAINING}h left, whose book is two-sided and uncrossed, "
            "and whose cheaper touch is at or below "
            f"{limm.MAX_PRICE_CENTS}c. Series named in LIQUIDITY_INCENTIVE_EXCLUDED_SERIES are "
            "never quoted, and no market another live book is resting in is ever contested."
        ),
        entry_rule=(
            "rest a post-only bid AT the cheaper side's touch price — which is also at or above "
            "the Reference Price, so the distance multiplier is 1.0"
        ),
        exit_rule="hold to settlement; no TP/SL (manage_exits skips this book's tags)",
        sizing_rule=(
            f"one contract per order, at most {limm.MAX_OPEN_ORDERS} resting orders, at most "
            f"${limm.MAX_STRATEGY_EXPOSURE_USD:.2f} committed at once"
        ),
        execution_style="maker",
        independent_variable="none — this version tests the execution path, not a rule",
        control_required=False,
        control_exemption_reason=(
            "there is no treatment to control for: the question is whether our own order path "
            "works. The execution control is the registered paper TWIN, armed at the same "
            "instant, which is what makes a fill difference readable at all."
        ),
        risk=RISK_ENVELOPE,
        docs={"thesis": THESIS_DOC,
              "research": "docs/LIQUIDITY_INCENTIVE_RESEARCH.md",
              "workstream": "docs/workstreams/WS-020-liquidity-incentive-shadow.md"},
        change_reason="initial version",
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="one-sided post-only bid at the cheaper touch, one contract",
        params={
            "contracts_per_order": limm.MAX_CONTRACTS_PER_ORDER,
            "max_price_cents": limm.MAX_PRICE_CENTS,
            "max_open_orders": limm.MAX_OPEN_ORDERS,
            "max_strategy_exposure_usd": limm.MAX_STRATEGY_EXPOSURE_USD,
        },
        strategy_tag=PAPER_TAG,
    )
    probe_gate = service.register_gate(
        session, version, gate_key=PROBE_GATE_KEY, kind="promotion",
        spec=PROBE_GATE_SPEC, from_state=LifecycleState.PROBE,
        to_state=LifecycleState.PAPER, registered_at=at,
        notes="instrument health only; no clause reads P&L, reward or edge",
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=promo, from_state=LifecycleState.PAPER,
        to_state=LifecycleState.LIVE_CANARY, registered_at=at,
        notes=("readiness to spend $10, not an economic bar — see the spec's own description"),
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes="pre-registered before arming; every clause names deployment_kind='live'",
    )
    service.freeze_version(session, version, now=at)
    for gate in (probe_gate, promotion_gate, keep_gate):
        service.mark_gate_evidence_started(session, gate, at=at)
    epoch = service.open_epoch(
        session, version,
        reason=("e1 — the shadow instrument's operating interval under the frozen contract. "
                "Evidence starts here, not at the collector's first row: this session had "
                "already read the day-0 output, and a bar chosen after seeing results is not "
                "a pre-registration."),
        started_at=at,
    )
    probe = service.register_deployment(
        session, epoch, deployment_key=PROBE_DEPLOYMENT_KEY,
        stage=LifecycleState.PROBE, kind="probe",
        arms={ARM_KEY: None}, started_at=at,
        notes=("the shadow collector. TAGLESS on purpose: it writes the incentive_* tables, "
               "trades nothing, and holds no strategy tag that could reach the write path"),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("the shadow instrument is the probe. No tag becomes admissible here — the "
                "probe deployment carries none."),
        occurred_at=at, version=version, epoch=epoch,
    )
    return {"experiment": experiment, "version": version, "epoch": epoch,
            "probe_deployment": probe, "probe_gate": probe_gate,
            "promotion_gate": promotion_gate, "keep_gate": keep_gate, "registered_at": at}


def arm(
    session,
    *,
    approved_by: str,
    actor: str = "operator",
    started_at: datetime | None = None,
    reason: str | None = None,
) -> dict:
    """Walk PROBE -> PAPER on a fresh PASS of the probe gate, then arm the canary and its twin.

    THIS FUNCTION EXPANDS REAL-MONEY CAPABILITY. It still places no order — the runtime
    allowlist is a separate switch — but it is the act that makes the book armable.

    The probe-gate re-evaluation here is STRICTER than the engine: `transition_experiment` does
    not require a PASS for PROBE -> PAPER, and this package requires one anyway, because the
    only reason this experiment reaches PAPER at all is to become armable."""
    from .evaluator import evaluate_gate

    at = started_at or _now()
    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} not found — send REGISTER_PACKAGE first")
    version = latest_version(session, experiment)
    if version is None:
        raise service.ExperimentOsError(f"{EXPERIMENT_KEY} has no version")

    if experiment.state == LifecycleState.PROBE.value:
        probe_gate = _gate(session, version, PROBE_GATE_KEY)
        outcome = evaluate_gate(session, probe_gate)
        if outcome.verdict != GateVerdict.PASS.value:
            raise service.ExperimentOsError(
                f"probe gate {PROBE_GATE_KEY!r} evaluates {outcome.verdict}, not PASS — the "
                "shadow instrument has not yet shown it is running and current over the "
                "post-registration window. This package refuses PROBE→PAPER without it: "
                f"{outcome.explanation}"
            )
        from .models import ExperimentGateResult
        result = session.get(ExperimentGateResult, outcome.result_id)
        service.transition_experiment(
            session, experiment, LifecycleState.PAPER, actor=actor,
            gate_result=result, occurred_at=at, version=version,
            reason=("the shadow instrument passed its own pre-registered health bar on "
                    "post-registration evidence; PAPER is entered only to become armable"),
        )
    if experiment.state != LifecycleState.PAPER.value:
        raise service.ExperimentOsError(
            f"{EXPERIMENT_KEY} is {experiment.state}; a live canary arms from PAPER")

    gate = _gate(session, version, PROMOTION_GATE_KEY)
    live, twin, epoch = service.arm_live_canary(
        session, experiment, gate=gate, approved_by=approved_by,
        live_key=LIVE_DEPLOYMENT_KEY, twin_key=TWIN_DEPLOYMENT_KEY,
        live_tags={ARM_KEY: LIVE_TAG}, twin_tags={ARM_KEY: TWIN_TAG},
        config=material_config(), started_at=at, actor=actor,
        reason=reason or (
            f"one-sided liquidity-incentive smoke test armed on {LIVE_TAG} with twin "
            f"{TWIN_TAG} at one boundary; envelope pre-registered on v{version.version}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


def activation_env() -> dict[str, str]:
    """The EXACT Railway variables the runtime-allowlist step sets, in the order they take
    effect. Building the mapping is all this does; nothing here applies it, and the step that
    lets this book spend anything is a Live Ops act through the env channel."""
    env = dict(RISK_ENVELOPE["settings"])
    env["LIVE_PAPER_TWINS"] = f"{LIVE_TAG}:{TWIN_TAG}"
    # Last, so the mapping reads in the order it takes effect: the book is enabled and its caps
    # are pinned before the switch that lets it spend anything.
    env["LIVE_STRATEGIES"] = LIVE_TAG
    return env


#: Every variable `activation_env` can name, declared so CI can assert the env channel will
#: accept each one — a package whose activation the channel refuses halfway through is the #266
#: defect class, and it should fail in CI rather than in front of an operator mid-write.
ACTIVATION_VARS: frozenset[str] = frozenset(
    {"LIVE_STRATEGIES", "LIVE_PAPER_TWINS", *RISK_ENVELOPE["settings"]}
)


def _gate(session, version: ExperimentVersion, gate_key: str) -> ExperimentGate:
    gate = session.scalar(
        select(ExperimentGate).where(
            ExperimentGate.version_id == version.id,
            ExperimentGate.gate_key == gate_key,
        )
    )
    if gate is None:
        raise service.ExperimentOsError(
            f"gate {gate_key!r} is not registered on version {version.version} — run "
            "REGISTER_PACKAGE first"
        )
    return gate


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "EXPERIMENT_KEY", "KEEP_GATE_KEY", "KEEP_GATE_SPEC",
    "LIVE_DEPLOYMENT_KEY", "LIVE_TAG", "OPERATOR_DECISIONS", "PAPER_TAG",
    "PROBE_DEPLOYMENT_KEY", "PROBE_GATE_KEY", "PROBE_GATE_SPEC", "PROMOTION_GATE_KEY",
    "PROMOTION_GATE_SPEC", "RISK_ENVELOPE", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG",
    "activation_env", "arm", "material_config", "register",
]
