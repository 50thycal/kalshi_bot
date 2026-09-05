"""mmsell correlation cap — the contract for `Gmmsell0` / `Gmmsell1` / `Gmmsell2`.

Registers one PAPER experiment with three arms and one pre-registered keep gate. Arms nothing,
trades nothing, touches no real money: under NEW_ONLY a tag that no active deployment arm
carries cannot trade at all, so registering the contract is what lets these books start.

WHY THIS EXPERIMENT EXISTS. XOS-000020, opened 2026-09-05 against the `Dmmsell10` live failure:
every concentration cap mmsell has ever run keys on `event_ticker`, which is series x occasion.
On 26SEP02 the live book held 31 markets across 23 event_tickers spanning eleven real games —
NYYLAA alone under five separate series — and that slate was the entire live drawdown. The book
believed it held N independent 7c lottery tickets; it held one bet on a high-scoring night.

WHY THREE ARMS AND NOT ONE. The counterfactual on the paper tape decomposes the effect and it
does NOT sit where the ticket said. Capping the CONTEST buys +0.09c/trade and does not improve
the worst day at all (-$9.49 -> -$9.56); capping every unit of correlation buys +0.61c and cuts
the worst day 76% (-> -$2.31). The effect is the LADDER axis. Shipping only the tradeable rule
would have left the two inseparable, so `corr_cap_game` is kept as the decomposition arm and
`corr_cap_all - corr_cap_game` IS the ladder axis. Full pre-registration, with the falsification
recorded before either book existed: docs/MMSELL_CORRELATION_CAP.md.

WHY THE CONTROL IS `Gmmsell0` AND NOT `mmsell10`. `mmsell10` is already the control arm of
`mmsell-price-ceiling-capacity` v3/e2, and a tag carries one active deployment arm — claiming it
would mean ending a running experiment's deployment to start this one. Naming it as an EXTERNAL
control instead is what has `mmsell-anchor-vol-entry` in BLOCKED_PLATFORM: a cross-snapshot delta
pools incomparable evidence. An in-experiment control shares this epoch and snapshot by
construction and cannot acquire that failure mode.

WHY THE GATE IS NOT ON c/TRADE. Measured per-trade standard deviation on this book is $0.2343,
so an 80%-power test of a +0.30c difference needs ~95,700 settled trades per arm — about 2.8
years at the observed flow. That is docs/MMSELL_ROADMAP.md S1's power limit binding exactly where
it predicted. A cap is a drawdown control; it is gated on the daily series, which is far better
powered on the same evidence, and c/trade is demoted to a floor that can only ever kill.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-correlation-cap"

CONTROL_ARM = "uncapped"
GAME_ARM = "corr_cap_game"
ALL_ARM = "corr_cap_all"

CONTROL_TAG = "Gmmsell0"
GAME_TAG = "Gmmsell1"
ALL_TAG = "Gmmsell2"

PROBE_DEPLOYMENT_KEY = "mmsell-corrcap-probe-1"
PAPER_DEPLOYMENT_KEY = "mmsell-corrcap-paper-1"

KEEP_GATE_KEY = "paper_keep"

#: Shared entry parameters. Identical on all three arms, so the ONLY difference between any two
#: books is which unit of correlation each will hold more than one position in.
BASE_BOOK_PARAMS = "lo=5,hi=10,maxyes=7"

#: The pre-registered sample floor, in SETTLEMENT DAYS rather than trades. Sixty, not the 35 the
#: counterfactual observed: a 5th percentile over 35 days interpolates between the 2nd and 3rd
#: worst days, so one bad slate moves it bodily.
SAMPLE_FLOOR_DAYS = 60

#: The stability bar, on `daily_pnl_stability` = mean(daily P&L) / sd(daily P&L). Absolute on a
#: scale-free statistic, deliberately: a relative bar would drift with the control. The
#: counterfactual read control 0.218, game 0.153, all 0.326, so +0.05 is about half the observed
#: effect — a bar the treatment must clear on its own, not one fitted to what it already scored.
STABILITY_BAR = 0.05

#: The c/trade floor. A FLOOR, never a promotion criterion — see the module docstring. It exists
#: only to catch a cap that destroys the edge while smoothing it.
EDGE_FLOOR_CENTS = -0.5

ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (CONTROL_ARM, ArmRole.CONTROL,
     "Uncapped. Byte-identical to mmsell10 on every knob; carries no correlation cap, so "
     "every existing cap (position, settlement-date, event rung) applies exactly as today.",
     {"tag": CONTROL_TAG, "book": BASE_BOOK_PARAMS, "corrcap": None}),
    (GAME_ARM, ArmRole.TREATMENT,
     "Caps CONTESTS only (corrscope=game, corrcap=1): one position per in-play game, spanning "
     "series. Every ladder cap is left exactly as the control's, so this arm differs from the "
     "control by the contest axis alone — the axis XOS-000020 named.",
     {"tag": GAME_TAG, "book": BASE_BOOK_PARAMS, "corrcap": 1, "corrscope": "game"}),
    (ALL_ARM, ArmRole.TREATMENT,
     "Caps EVERY unit of correlation (corrscope=all, corrcap=1), which additionally tightens "
     "scheduled/discrete ladders from the rung cap's 3 rungs to 1. This arm minus the game arm "
     "IS the ladder axis.",
     {"tag": ALL_TAG, "book": BASE_BOOK_PARAMS, "corrcap": 1, "corrscope": "all"}),
)

#: What makes this a concentration experiment rather than a re-parameterisation of the book.
#: Frozen: changing any of these is a new Version, not a tweak.
HELD_CONSTANT: list[str] = [
    "entry band and price ceiling are identical on all three arms (lo=5, hi=10, maxyes=7) — "
    "the price band is not an arm, and the type-book family already established that it does "
    "the work a selection filter is usually credited with",
    "no arm carries a stop, volatility gate or strangle leg; those are the anchor set's "
    "experiment and would confound this one",
    "no arm selects on market TYPE or on historical per-cell performance. The Tmmsell family "
    "measured that axis to a verdict at n in the hundreds; re-introducing it here would make "
    "the cap and the selection inseparable",
    "the cap counts OPEN positions and never closed ones, so it bounds carried risk rather "
    "than acting as an entry-timing rule",
    "which contract is kept within a capped unit is FIRST ARRIVAL, never a ranking. A ranking "
    "would be a second, unvalidated hypothesis riding on this one",
]

#: The counterfactual that stands in for a probe. Recorded as the instrument it is: a replay of
#: the control book's own settled tape under each candidate rule. It authorizes nothing.
PROBE_RULE: dict = {
    "instrument": "SQL replay of mmsell10's settled paper tape under each candidate cap, "
                  "keying by kalshi_bot/mmsell/correlation.correlation_key",
    "unit_of_observation": "one settled paper trade, and its settlement DAY",
    "window": "post-cohort-boundary only (>= 2026-08-13T18:09:40Z), n=3,296 settled trades "
              "over 35 settlement days",
    "recorded_result": {
        "control": {"cents_per_trade": 0.649, "worst_day_usd": -9.49, "daily_stability": 0.218},
        "corr_cap_game": {"cents_per_trade": 0.739, "worst_day_usd": -9.56,
                          "daily_stability": 0.153},
        "corr_cap_all": {"cents_per_trade": 1.259, "worst_day_usd": -2.31,
                         "daily_stability": 0.326},
    },
    "falsified": "the headline hypothesis. The CONTEST axis XOS-000020 named moves neither the "
                 "mean materially nor the worst day at all; the effect is the ladder axis.",
    "authority": "NONE. This is a post-hoc replay of the same tape that generated the "
                 "hypothesis, on paper fills, and it cannot model the slot a real capped book "
                 "frees. It sets the prior and fixes the bars BEFORE any arm trades.",
}

#: Paper keep/kill. There is deliberately NO promotion gate: `Dmmsell10` is stood down, nothing
#: here is a live candidate, and registering a PAPER -> LIVE_CANARY gate now would pre-authorize
#: a transition for which no evidence exists. A promotion is a new gate on a new Version.
KEEP_GATE_SPEC: dict = {
    "description": (
        "A concentration cap is a DRAWDOWN control: it trades total return for smoothness by "
        "construction and must be judged as one. Keep an arm only if it buys materially more "
        "daily stability than the uncapped control while not destroying the edge. Read on the "
        "daily series, not on cents per trade — at this book's per-trade variance a c/trade "
        "test of the effect size in question needs ~95,700 trades per arm."
    ),
    "sample": {
        CONTROL_ARM: {"metric": "settled_days", "op": ">=", "value": SAMPLE_FLOOR_DAYS},
        ALL_ARM: {"metric": "settled_days", "op": ">=", "value": SAMPLE_FLOOR_DAYS},
    },
    "pass_all": [
        {"metric": "delta.daily_pnl_stability", "treatment": ALL_ARM,
         "control": CONTROL_ARM, "op": ">=", "value": STABILITY_BAR},
        {"metric": "delta.pnl_cents_per_trade", "treatment": ALL_ARM,
         "control": CONTROL_ARM, "op": ">=", "value": EDGE_FLOOR_CENTS},
    ],
    "fail_any": [
        # The cap bought no smoothness. On this evidence that kills the mechanic, not the arm:
        # if capping every unit of correlation does not steady the daily series, the clustering
        # the whole thesis rests on was luck.
        {"metric": "delta.daily_pnl_stability", "treatment": ALL_ARM,
         "control": CONTROL_ARM, "op": "<", "value": 0},
        # Smoothness bought at too high a price in edge.
        {"metric": "delta.pnl_cents_per_trade", "treatment": ALL_ARM,
         "control": CONTROL_ARM, "op": "<", "value": EDGE_FLOOR_CENTS},
    ],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(
    session,
    *,
    actor: str,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Create the experiment, freeze v1 with its three arms and the keep gate, open e1 on the
    ACTIVE platform snapshot, record the counterfactual as a tagless PROBE, then register the
    PAPER deployment that gives the three books their tags.

    `promotion_sample_floor` is the knob the experiment-command transport always passes. Here it
    may only RAISE the 60-day floor: an envelope may make a pre-registered bar stricter, never
    weaker. It is measured in SETTLEMENT DAYS on this contract, not trades — passing a
    trade-count by habit would silently make the gate unreachable rather than stricter.

    Idempotence is refusal, not a no-op: a second run raises rather than quietly creating a
    parallel contract."""
    floor = SAMPLE_FLOOR_DAYS if promotion_sample_floor is None else int(promotion_sample_floor)
    if floor < SAMPLE_FLOOR_DAYS:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is below the reviewed floor {SAMPLE_FLOOR_DAYS} "
            "SETTLEMENT DAYS — an envelope may make a pre-registered bar stricter, never weaker"
        )
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"experiment {EXPERIMENT_KEY!r} already exists — this package registers it once; "
            "a changed contract is a new Version"
        )

    experiment = service.create_experiment(
        session,
        key=EXPERIMENT_KEY,
        origin="operator",
        title="mmsell correlation cap — the unit of correlation is the occasion, not the event",
        family="risk_concentration",
        hypothesis=(
            "mmsell's diversification premise fails because its caps count `event_ticker`, "
            "which is series x occasion. Capping open positions per UNIT OF CORRELATION "
            "instead materially steadies the daily P&L series without destroying the edge."
        ),
        mechanism=(
            "One occasion resolves every contract written on it at once. An MLB game settles "
            "its TOTAL, TEAMTOTAL, SPREAD and HR markets together; an hourly BTC ladder settles "
            "every rung on one path. Positions the book counts as independent are one position "
            "at N x size, so its realised loss distribution has a far fatter tail than the "
            "position count implies."
        ),
        counterparty=(
            "nobody — this is not an edge claim. The counterparty is our own risk model, which "
            "prices diversification it does not have."
        ),
        falsification=(
            "Capping every unit of correlation does not improve daily stability over the "
            "uncapped control at 60 settlement days, OR it improves it only by giving up more "
            "than 0.5c/trade of edge. Either kills the mechanic. The CONTEST-only half is "
            "already falsified on the counterfactual and its arm exists to measure how much."
        ),
        universe=(
            "identical to mmsell10's: cheap-band tails (lo=5, hi=10, maxyes=7) in the shared "
            "mmsell scan. No arm selects on series, market type or historical performance."
        ),
        docs={"thesis": "docs/MMSELL_CORRELATION_CAP.md",
              "issue": "XOS-000020",
              "key": "kalshi_bot/mmsell/correlation.py"},
        notes=(
            "Opened against XOS-000020, whose diagnosis this contract records as PARTLY "
            "FALSIFIED before any arm traded — see PROBE_RULE.falsified."
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
            "unchanged from mmsell10: the global mmsell_skip_series list (parlays, weather). "
            "An UNCLASSIFIED series is admitted exactly as the control admits it and is keyed "
            "to itself — never merged into another unit of correlation, since a contract nobody "
            "has classified must not be declared correlated with one that has been."
        ),
        entry_rule=(
            "the control's entry, unchanged: sell the cheap tail (buy NO at the no-bid) on any "
            "in-band candidate. A treatment arm additionally declines the candidate when it "
            "already holds `corrcap` open positions in that candidate's unit of correlation."
        ),
        exit_rule="hold to settlement; no stop, no take-profit. Identical on all three arms.",
        sizing_rule="flat 1-contract clip on every arm; size is not an arm here.",
        execution_style="maker",
        independent_variable=(
            "which units of correlation are capped: none (control), contests only, or all"
        ),
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": "delta.daily_pnl_stability (corr_cap_all minus uncapped)",
            "secondary": ["daily_pnl_stability", "settled_days", "pnl_cents_per_trade",
                          "realized_pnl_usd", "settled_trades"],
            "decomposition_arm": GAME_ARM,
            "note": "c/trade is a FLOOR on this contract, never a promotion criterion. "
                    "Measured per-trade sd is $0.2343, so an 80%-power test of a 0.30c "
                    "difference needs ~95,700 settled trades per arm. The fill model is not "
                    "read: all three arms are maker books in one band, where it cannot "
                    "discriminate (docs/MMSELL_TYPE_BOOKS.md).",
        },
        sample={"probe": PROBE_RULE,
                "paper_floor_settled_days": {CONTROL_ARM: floor, ALL_ARM: floor}},
        costs={"model": "post-2026-08-11 maker fee model, identical on every arm, so it "
                        "cancels in every delta this gate reads"},
        provenance={"positions": "paper_trades joined to mmsell_settlement_meta for the "
                                 "series/event of each held market",
                    "correlation_key": "kalshi_bot/mmsell/correlation.py, derived from "
                                       "market_types.classify — the same taxonomy the type "
                                       "books select on"},
        monitoring={"telemetry": "MmSellCycleSummary.skipped_correlation_cap, persisted per "
                                 "cycle to system_events — an arm that declined nothing "
                                 "measured nothing"},
        docs={"thesis": "docs/MMSELL_CORRELATION_CAP.md"},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    keep_spec = json.loads(json.dumps(KEEP_GATE_SPEC))
    for arm_key in (CONTROL_ARM, ALL_ARM):
        keep_spec["sample"][arm_key]["value"] = floor
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=keep_spec, registered_at=at,
        notes=("registered before any arm has traded a single market. The counterfactual that "
               "fixed these bars is recorded on the version as PROBE_RULE, including that it "
               "falsified the ticket's own headline axis."),
    )
    service.freeze_version(session, version, now=at)

    epoch = service.open_epoch(
        session, version,
        reason=("v1's first operating interval, pinned to the platform snapshot active at "
                "registration. All three arms open together in one epoch, which is what makes "
                "the control poolable with the treatments without an external reference."),
        started_at=at,
    )

    probe = service.register_deployment(
        session, epoch,
        deployment_key=PROBE_DEPLOYMENT_KEY,
        stage=LifecycleState.PROBE,
        kind=DeploymentKind.PROBE,
        arms={arm_key: None for arm_key, _, _, _ in ARMS},
        config={"instrument": PROBE_RULE["instrument"], "window": PROBE_RULE["window"]},
        started_at=at,
        notes=("TAGLESS BY CONSTRUCTION. The counterfactual is a replay of already-settled "
               "history; it places no order and carries no tag, so nothing on it can reach the "
               "exchange. Recorded as a deployment so the replay that fixed the gate's bars is "
               "part of the contract rather than a claim in prose."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("the cap counterfactual is recorded on v1, including its falsification of the "
                "contest axis XOS-000020 named"),
        occurred_at=at, version=version, epoch=epoch,
    )

    paper = service.register_deployment(
        session, epoch,
        deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER,
        kind=DeploymentKind.PAPER,
        arms={CONTROL_ARM: CONTROL_TAG, GAME_ARM: GAME_TAG, ALL_ARM: ALL_TAG},
        config={"books": {tag: spec for tag, spec in (
            (CONTROL_TAG, BASE_BOOK_PARAMS),
            (GAME_TAG, f"{BASE_BOOK_PARAMS},corrcap=1,corrscope=game"),
            (ALL_TAG, f"{BASE_BOOK_PARAMS},corrcap=1,corrscope=all"),
        )}},
        started_at=at,
        notes=("Three PAPER books, no real money. All three tags are fresh, so none inherits "
               "open positions or history from another experiment and the three series start "
               "at the same instant — which is what the gate's same-window reads assume."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PAPER, actor=actor,
        reason=("the counterfactual fixed the bars and the pre-registration is merged "
                "(docs/MMSELL_CORRELATION_CAP.md); the forward test is the evidence that "
                "decides, and a replay authorizes nothing"),
        occurred_at=at, version=version, epoch=epoch,
    )

    return {
        "version": version,
        "epoch": epoch,
        "probe": probe,
        "paper": paper,
        "keep_gate": keep_gate,
    }
