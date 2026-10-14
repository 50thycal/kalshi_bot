"""mmsell rain season — the contract for `Rainmmsell1` / `Rainmmsell2`.

Registers one PAPER experiment with two arms and two pre-registered keep/kill gates. Arms
nothing, trades nothing, touches no real money: under NEW_ONLY a tag no active deployment arm
carries cannot trade, so registering this contract is what lets these two books start.

WHY THIS EXPERIMENT EXISTS. MMSELL selling the cheap YES tail on Kalshi's daily rain markets
(KXRAIN, "will the named station record > 0.00 in today") is the most consistent positive cell
the bot has: positive live, on the live twin and across every paper book that trades it, and the
cheapest price band (YES ask <= 7c) settled one losing market in 226 against ~6.5% implied
(docs/KXRAIN_MMSELL_CELL.md). All of that evidence is AUGUST-OCTOBER, and Kalshi has listed the
series only since July 2026, so no winter history exists anywhere. Winter is the main risk:
snow counts once melted, the wet season starts in the Pacific Northwest and California, and
the one live loss (Seattle, 10 Oct) came in the first wet-season month. Nothing registered
isolates this cell: it is spread across ~30 paper books, each owned by another experiment's
gate, and each sharing its settlement-date cap with every other series it trades.

WHAT IS GATED HERE. Two absolute questions, one per arm, each with its own gate:
  * `rain_cell` (CONTROL) — does the winning selection, isolated to KXRAIN, stay positive
    THROUGH WINTER? The sample floor is in settlement DAYS and set so the first permitted look
    falls in mid-February: the evidence spans December-February by construction.
  * `rain_adjacent` (TREATMENT) — is the next price band (YES ask 8-12c, disjoint from the cell)
    also positive through winter? It is the capacity lever: more qualifying city-days per day at
    a smaller edge per contract. Paper Aug-Oct reads it at +6 to +8c on thinner, noisier counts.

WHAT IS NOT GATED, AND WHY. SIZE. A scaled-paper arm (the same rule at 10 contracts) would book
exactly ten times the 1-lot P&L, because a paper fill assumes the whole clip at the no-bid. It
would measure nothing, so there is no such arm. Capacity is read from candidate-tick depth and
live fills (the thesis doc), and any live size change is a Live Ops canary on its own Version.
There is deliberately NO promotion gate either: registering PAPER -> LIVE_CANARY now would
pre-authorize a transition for which no winter evidence exists.

Full pre-registration: docs/KXRAIN_MMSELL_CELL.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-rain-season"

CELL_ARM = "rain_cell"
ADJACENT_ARM = "rain_adjacent"

#: Fresh tags. LIVE_STRATEGIES matches by PREFIX, so neither may prefix the other or any
#: existing tag: `Rainmmsell1` does not prefix `Rainmmsell2`, and no tag starts `Rainmmsell`.
CELL_TAG = "Rainmmsell1"
ADJACENT_TAG = "Rainmmsell2"

PROBE_DEPLOYMENT_KEY = "mmsell-rain-probe-1"
PAPER_DEPLOYMENT_KEY = "mmsell-rain-paper-1"

CELL_GATE_KEY = "cell_winter_keep"
ADJACENT_GATE_KEY = "adjacent_winter_keep"

SERIES = "KXRAIN"

#: The winning selection: `mmsell10`'s band and ceiling (buy NO at the no-bid when
#: 5 <= YES mid <= 10 and the YES price actually sold is <= 7c), restricted to KXRAIN.
CELL_PARAMS = f"lo=5,hi=10,maxyes=7,onlyx={SERIES}"
#: The next band up, disjoint from the cell: the cell needs YES ask <= 7, this needs YES mid >= 8.
ADJACENT_PARAMS = f"lo=8,hi=14,maxyes=12,onlyx={SERIES}"

BOOK_SPECS: dict[str, str] = {CELL_TAG: CELL_PARAMS, ADJACENT_TAG: ADJACENT_PARAMS}

#: No `ACTIVATION_VARS`: a package that arms nothing declares none (the reviewed-universe
#: shape). Both books start when their specs are appended to `MMSELL_VARIANTS` through the
#: `env` channel (read the live value first, append, never retype) — a paper-only append.

#: Promotion (keep) floor in SETTLEMENT DAYS. The arms open in mid-October; 120 settled days
#: cannot be reached before mid-February, so at least ~73 of the counted days fall in
#: December-February. A slow book reaches it later, which only adds winter.
SAMPLE_FLOOR_DAYS = 120
#: The last permitted look (~mid-April at one settled day per calendar day): the end of the
#: snow season in every city the series lists. Past it the verdict is HORIZON_EXHAUSTED.
HORIZON_DAYS = 180
#: The early-failure floor: a winter that turns the cell negative is caught here, not at
#: the 120-day look. 45 days is ~2 settled losses at the break-even rate.
EARLY_FAIL_FLOOR_DAYS = 45

#: Pass bar on paper cents per contract. +2c is the measured paper-to-live maker gap
#: (docs/MMSELL_FILL_MODEL.md): a paper edge below it would not survive live fills. The cell
#: ran +5.7c Aug-Oct; this bar is NOT fitted to that number.
PASS_CENTS = 2.0
#: Fail bar at the floor: below +1c the edge cannot pay for live adverse selection.
FAIL_CENTS_AT_FLOOR = 1.0
#: Daily stability (mean/sd of daily paper P&L). A tail-selling book whose losses arrive
#: together on a front-passage day looks positive per contract and unstable per day.
STABILITY_BAR = 0.15


def _gate(arm: str, description: str) -> dict:
    return {
        "description": description,
        "sample": {arm: {"metric": "settled_days", "op": ">=", "value": SAMPLE_FLOOR_DAYS}},
        "max_evidence_horizon": {"metric": "settled_days", "value": HORIZON_DAYS,
                                 "arms": [arm]},
        "pass_all": [
            {"metric": "pnl_cents_per_contract", "arm": arm, "op": ">=", "value": PASS_CENTS},
            {"metric": "daily_pnl_stability", "arm": arm, "op": ">=", "value": STABILITY_BAR},
        ],
        "fail_any": [
            # Early kill: the season turned the book negative.
            {"metric": "pnl_cents_per_contract", "arm": arm, "op": "<", "value": 0.0,
             "min_evidence": {"metric": "settled_days", "op": ">=",
                              "value": EARLY_FAIL_FLOOR_DAYS}},
            # At the floor: too thin to survive live fills.
            {"metric": "pnl_cents_per_contract", "arm": arm, "op": "<",
             "value": FAIL_CENTS_AT_FLOOR},
            {"metric": "daily_pnl_stability", "arm": arm, "op": "<", "value": 0.0},
        ],
    }


CELL_GATE_SPEC: dict = _gate(
    CELL_ARM,
    "Keep/kill for the KXRAIN cheap-tail cell through winter. The floor is in settlement days "
    "and cannot be reached before mid-February, so the evidence spans December-February by "
    "construction. PASS keeps the cell and makes it eligible for a sizing proposal on its own "
    "Version (a Live Ops canary); it authorizes no live change by itself. FAIL ends the rain "
    "cell for this rule.",
)
ADJACENT_GATE_SPEC: dict = _gate(
    ADJACENT_ARM,
    "Keep/kill for the adjacent KXRAIN band (YES 8-12c) through winter — the capacity lever. "
    "Same floor, horizon and bars as the cell, judged on its own arm; the two are not pooled.",
)

ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (CELL_ARM, ArmRole.CONTROL,
     "The winning selection isolated: mmsell10's band and ceiling (5<=mid<=10, YES sold <=7c) "
     "on KXRAIN only. Buy NO at the no-bid, hold to settlement, 1 contract.",
     {"tag": CELL_TAG, "book": CELL_PARAMS, "series": SERIES, "band": "mid 5-10, yes<=7"}),
    (ADJACENT_ARM, ArmRole.TREATMENT,
     "The next band up, disjoint from the cell: 8<=mid<=14, YES sold <=12c, KXRAIN only. "
     "Everything else identical to the cell.",
     {"tag": ADJACENT_TAG, "book": ADJACENT_PARAMS, "series": SERIES,
      "band": "mid 8-14, yes<=12"}),
)

HELD_CONSTANT: list[str] = [
    "both arms trade KXRAIN only, by EXACT series match (`onlyx=KXRAIN`)",
    "entry is the mmsell maker entry on both arms: buy NO at the no-bid, 1 contract, paper",
    "exit is hold to settlement on both arms: no stop, take-profit, volatility gate or strangle",
    "neither arm carries a contest cap or a size rule; the global caps (open positions, "
    "settlement date, three rungs per non-exclusive event) apply unchanged, so at most three "
    "cities per settlement date per arm",
    "the two price bands are disjoint, so no market can be in both arms at one instant",
    "the GLOBAL mmsell knobs stay as they are for the life of this version; both books opt "
    "in through their own specs and no other book's selection moves",
    "both tags are PAPER and appear in no live arm set, so no live mirror fires for either",
]

PROBE_RULE: dict = {
    "instrument": "retrospective read of every settled MMSELL KXRAIN paper and live trade "
                  "(paper_trades, live_orders, fills, mmsell_candidate_ticks) plus the public "
                  "listing history (scripts/kxrain_season_probe.py)",
    "unit_of_observation": "one KXRAIN market (city-day); clustered by settlement date",
    "window": "2026-08-08 to 2026-10-13, the whole history of the series under MMSELL",
    "recorded_result": "cheap cell (paper NO >= 93c): 226 markets over 62 dates, 1 settled "
                       "loss, +5.74c/contract; live 38 of 39 date-bets won. Kalshi lists no "
                       "KXRAIN event before July 2026, so no winter tape exists.",
    "known_confound": "every observation is August-October: one season, mostly convective "
                      "rain. That is the reason this contract exists, not a defect in it.",
    "authority": "NONE. It fixes which bands the arms trade and why the floor is in winter; "
                 "the bars are set from the fill model, not fitted to this read.",
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
    """Create the experiment, freeze v1 with both arms and both gates, open e1 on the ACTIVE
    platform snapshot, record the TAGLESS probe, and register the PAPER deployment that gives
    both books their tags.

    `promotion_sample_floor` may only RAISE the 120-settlement-day floor. Idempotence is
    refusal: a second run raises rather than creating a parallel contract."""
    floor = SAMPLE_FLOOR_DAYS if promotion_sample_floor is None else int(promotion_sample_floor)
    if floor < SAMPLE_FLOOR_DAYS:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is below the reviewed floor {SAMPLE_FLOOR_DAYS} "
            "SETTLEMENT DAYS — an envelope may make a pre-registered bar stricter, never weaker"
        )
    if floor >= HORIZON_DAYS:
        raise service.ExperimentOsError(
            f"promotion_sample_floor={floor} is at or past the {HORIZON_DAYS}-day horizon — "
            "the gate could never render a verdict"
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
        title="mmsell rain season — does the KXRAIN cheap-tail edge survive winter",
        family="maker",
        hypothesis=(
            "On Kalshi's daily rain markets the cheap YES tail (YES <= 7c) settles YES far less "
            "often than its price implies, in winter as in summer; the adjacent band "
            "(YES 8-12c) carries a smaller edge of the same kind."
        ),
        mechanism=(
            "A favourite-longshot floor: 'will it rain at all today' attracts YES buyers at a "
            "few cents on days the forecast already calls dry, and nobody sells YES below about "
            "6c because it ties up 94c to earn 6c. Measured Aug-Oct, the 15-40c band is "
            "calibrated and only the cheapest bands are rich, which is the longshot-bias shape "
            "rather than a forecasting edge."
        ),
        counterparty=(
            "YES buyers paying a few cents for rain at a station on a day the forecast calls "
            "dry — lottery-ticket demand, not informed flow."
        ),
        falsification=(
            "Through December-February the cell's paper edge falls below +1c/contract at 120 "
            "settlement days, or below zero at any point after 45 days, or its daily P&L "
            "series is unstable (mean/sd < 0). Winter weather (synoptic systems, snow counted "
            "melted) prices the tail correctly or worse."
        ),
        universe="KXRAIN only, every listed city, exact series match",
        docs={"thesis": "docs/KXRAIN_MMSELL_CELL.md",
              "probe": "scripts/kxrain_season_probe.py",
              "rules": "kalshi_bot/catalog/approved_reviews.json (KXRAIN-series-approval)"},
        notes=(
            "No promotion gate and no size arm, deliberately: a paper fill assumes the whole "
            "clip, so a scaled-paper arm measures nothing, and a live size change is a Live "
            "Ops canary on its own Version."
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
            "every other series, by construction (`onlyx=` is an exact allowlist). No city "
            "is excluded: the city split is a read, not a filter, so the seasonal question is "
            "answered on the whole series."
        ),
        entry_rule=(
            "sell the cheap tail (buy NO at the no-bid) on a KXRAIN market whose YES mid is in "
            "the arm's band and whose YES price actually sold is under the arm's ceiling: "
            "cell 5<=mid<=10, yes<=7; adjacent 8<=mid<=14, yes<=12."
        ),
        exit_rule="hold to settlement; no stop, no take-profit. Identical on both arms.",
        sizing_rule="flat 1-contract clip on both arms; size is not an arm here.",
        execution_style="maker",
        independent_variable="the price band (cell versus adjacent)",
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": "pnl_cents_per_contract per arm, at a winter-spanning floor",
            "secondary": ["daily_pnl_stability", "settled_days", "settled_trades",
                          "realized_pnl_usd", "win_rate_pct"],
            "note": ("each arm is judged on its own gate; the arms are not pooled and the "
                     "adjacent arm is not a delta against the cell."),
        },
        sample={"probe": PROBE_RULE,
                "paper_floor_settled_days": {CELL_ARM: floor, ADJACENT_ARM: floor},
                "horizon_settled_days": HORIZON_DAYS,
                "early_fail_floor_settled_days": EARLY_FAIL_FLOOR_DAYS},
        costs={"model": "post-2026-08-11 maker fee model, identical on both arms"},
        provenance={"positions": "paper_trades for the two tags",
                    "settlement": "Kalshi KXRAIN settlement (station total > 0.00 in; trace "
                                  "and missing = 0; melted snow counts)"},
        monitoring={"winter_read": "the thesis doc's observational December-February split, "
                                   "read with scripts/kxrain_season_probe.py and the ops db "
                                   "channel; it authorizes nothing",
                    "silent_arm": "both tags must show entries within SILENT_ARM_HOURS of "
                                  "the MMSELL_VARIANTS append"},
        docs={"thesis": "docs/KXRAIN_MMSELL_CELL.md"},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    gates = {}
    for gate_key, spec, arm in ((CELL_GATE_KEY, CELL_GATE_SPEC, CELL_ARM),
                                (ADJACENT_GATE_KEY, ADJACENT_GATE_SPEC, ADJACENT_ARM)):
        s = json.loads(json.dumps(spec))
        s["sample"][arm]["value"] = floor
        gates[gate_key] = service.register_gate(
            session, version, gate_key=gate_key, kind="kill", spec=s, registered_at=at,
            notes=("registered before either arm has traded. The +2c pass bar is the measured "
                   "paper-to-live maker gap, not a fit to the Aug-Oct +5.7c; the floor is set "
                   "by the calendar so the evidence spans winter."),
        )
    service.freeze_version(session, version, now=at)

    epoch = service.open_epoch(
        session, version,
        reason=("v1's first operating interval, pinned to the platform snapshot active at "
                "registration. Both arms open together, so the season they see is the same."),
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
        notes=("TAGLESS BY CONSTRUCTION: a read of settled history that places no order."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("the retrospective read is recorded on v1 with its confound (one season); it "
                "authorizes nothing beyond fixing the bands and the winter floor"),
        occurred_at=at, version=version, epoch=epoch,
    )

    paper = service.register_deployment(
        session, epoch,
        deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER,
        kind=DeploymentKind.PAPER,
        arms={CELL_ARM: CELL_TAG, ADJACENT_ARM: ADJACENT_TAG},
        config={"books": dict(BOOK_SPECS), "series": SERIES},
        started_at=at,
        notes=("Two PAPER books, no real money, neither tag in any live arm set. Fresh tags, "
               "so neither inherits positions or history."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PAPER, actor=actor,
        reason=("pre-registration merged (docs/KXRAIN_MMSELL_CELL.md); the forward winter tape "
                "is the evidence that decides"),
        occurred_at=at, version=version, epoch=epoch,
    )

    return {"version": version, "epoch": epoch, "probe": probe, "paper": paper,
            "gates": gates}
