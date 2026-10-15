"""mmsell temperature season — the contract for `Tempmmsell1`.

Registers one PAPER experiment with one arm and one pre-registered keep/kill gate. Arms
nothing, trades nothing, touches no real money: under NEW_ONLY a tag no active deployment arm
carries cannot trade, so registering this contract is what lets this one book start.

WHY THIS EXPERIMENT EXISTS. The rain cell (`rain_season.py`) works because the cheapest YES on
a daily weather ladder is overpriced: buyers pay a few cents for an outcome the forecast already
rules out. Daily temperature ladders (KXHIGH*, KXHIGHT*, KXLOWT*) are the closest mechanical
twin, but `mmsell_skip_series` has dropped them from every MMSELL book since the weather books
existed, so the MMSELL cell (a maker NO at the no-bid on YES <= 7c) has never traded them. The
look-alike probe (`scripts/mmsell_lookalike_probe.py`, docs/MMSELL_LOOKALIKE_PROBE.md) replayed
that cell on Kalshi's public history: about 1,700 first entries across three families, implied
YES ~6.6% against ~4% realized, +2.5 to +3.1c/contract gross, PROMOTE on all three under a
gate fixed before it ran.

Earlier temperature evidence is a different rule: backfill probe #4 sold at the TAKER price and
was refuted; the maker favourite-buy died to adverse selection. Neither tested this cell.

WHAT IS GATED HERE. One absolute question: does the cell, on temperature only, stay positive
THROUGH WINTER on a forward paper tape? The replay's worst months were winter 2024-25 in
KXHIGH (Dec -10.2c, Jan -9.4c) while winter 2025-26 was positive, so winter is the risk and
the sample floor is in settlement DAYS set so the first permitted look falls in mid-February.

WHAT IS NOT GATED, AND WHY.
  * The December-February split. The gate grammar has no seasonal subset, and adding one is a
    shared-metric change (Platform Change Review), so the pre-registered winter stop (Dec-Feb
    mean <= -3c/contract) is a MONITORING read with a named consequence, not a verdict.
  * Size, and promotion — exactly as in the rain contract: a scaled-paper arm measures nothing,
    and no live step exists until a separate Live Ops canary on its own Version.

THE REACH. The temperature series stay on the global skip list. This book reaches them through
its own `optin=` key; the tracker ranks opted-in events in a separate list after the normal
ones (so no other book's candidates move) and refuses every live mirror on a skipped series.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-temperature-season"

CELL_ARM = "temp_cell"

#: Fresh tag. LIVE_STRATEGIES matches by PREFIX: no existing tag starts `Tempmmsell`, and
#: `Tmmsell*` does not prefix it (`Te` vs `Tm`).
CELL_TAG = "Tempmmsell1"

PROBE_DEPLOYMENT_KEY = "mmsell-temperature-probe-1"
PAPER_DEPLOYMENT_KEY = "mmsell-temperature-paper-1"

CELL_GATE_KEY = "temp_cell_winter_keep"

#: Series prefixes on the global skip list that this book opts into, and the only series it
#: trades. KXHIGH covers KXHIGH<city> and KXHIGHT<city>; KXLOW covers KXLOWT<city>.
SERIES_PREFIXES: tuple[str, ...] = ("KXHIGH", "KXLOW")

#: The rain cell's selection (`mmsell10`'s band and ceiling: buy NO at the no-bid when
#: 5 <= YES mid <= 10 and the YES price actually sold is <= 7c), on temperature only, inside the
#: probe's entry window (T-48h .. T-1h; the global floor is already 1h).
_PREFIXES = "+".join(SERIES_PREFIXES)
CELL_PARAMS = f"lo=5,hi=10,maxyes=7,htcmax=48,optin={_PREFIXES},only={_PREFIXES}"

BOOK_SPECS: dict[str, str] = {CELL_TAG: CELL_PARAMS}

#: No `ACTIVATION_VARS`: a package that arms nothing declares none. The book starts when its
#: spec is appended to `MMSELL_VARIANTS` through the `env` channel (read the live value first,
#: append, never retype) — a paper-only append.

#: Keep floor in SETTLEMENT DAYS. The arm opens mid-October; 120 settled days cannot be reached
#: before mid-February, so at least ~70 of them fall in December-February (the pre-registered
#: minimum is 30).
SAMPLE_FLOOR_DAYS = 120
#: The last permitted look (~mid-April). Past it the verdict is HORIZON_EXHAUSTED.
HORIZON_DAYS = 180
#: Early-failure floor: a winter that turns the cell negative is caught here.
EARLY_FAIL_FLOOR_DAYS = 45

#: Pass bar: +2c is the measured paper-to-live maker gap (docs/MMSELL_FILL_MODEL.md), the same
#: bar as the probe gate and the rain contract. Not fitted to the replay's +2.5..+3.1c.
PASS_CENTS = 2.0
#: Fail bar at the floor. The probe doc nominated "<= 0 at the floor"; this contract tightens it
#: to the rain contract's +1c BEFORE any paper trade exists, because temperature carries a
#: recorded history of maker adverse selection and an edge under +1c cannot pay for it.
FAIL_CENTS_AT_FLOOR = 1.0
#: Daily stability (mean/sd of daily paper P&L). Every city on one cold front settles together.
STABILITY_BAR = 0.15
#: The pre-registered winter stop, read observationally (see module docstring).
WINTER_STOP_CENTS = -3.0
WINTER_MIN_DAYS = 30


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
    "Keep/kill for the MMSELL cheap-tail cell on daily temperature ladders through winter. The "
    "floor is in settlement days and cannot be reached before mid-February, so the evidence "
    "spans December-February by construction. PASS keeps the cell and makes it eligible for a "
    "live-twin proposal on its own Version (a Live Ops canary); it authorizes no live change by "
    "itself. FAIL ends the temperature cell for this rule.",
)

ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (CELL_ARM, ArmRole.CONTROL,
     "The rain cell's selection on temperature: 5<=mid<=10, YES sold <=7c, entry inside "
     "T-48h..T-1h, KXHIGH*/KXHIGHT*/KXLOWT* only. Buy NO at the no-bid, hold to settlement, "
     "1 contract.",
     {"tag": CELL_TAG, "book": CELL_PARAMS, "series": list(SERIES_PREFIXES),
      "band": "mid 5-10, yes<=7"}),
)

HELD_CONSTANT: list[str] = [
    "the book trades only series starting KXHIGH or KXLOW (`only=`), reached through its own "
    "`optin=`; the GLOBAL skip list is unchanged, so every other book still never sees them",
    "opted-in events are ranked in their own list after the normal ones, so no other book's "
    "candidate set or rank moves",
    "entry is the mmsell maker entry: buy NO at the no-bid, 1 contract, paper",
    "exit is hold to settlement: no stop, take-profit, volatility gate or strangle",
    "no contest cap or size rule; the global caps (open positions, settlement date, rungs per "
    "non-exclusive event) apply unchanged and bound how many cities one weather day can add",
    "the tag is PAPER and appears in no live arm set; the tracker also refuses every live "
    "mirror on a globally skipped series, so no live order can fire for it",
]

PROBE_RULE: dict = {
    "instrument": "replay of the mmsell10 cell on Kalshi's public live + historical archive "
                  "(hourly candles, no auth): scripts/mmsell_lookalike_probe.py",
    "unit_of_observation": "first entry per market; bet unit one family-day",
    "window": "KXHIGH 2024-10..2026-10, KXHIGHT 2026-01..10, KXLOWT 2025-12..2026-10",
    "recorded_result": "PROMOTE on all three families under the pre-registered gate: KXHIGH "
                       "564 entries/120 days +2.47c t 2.94; KXHIGHT 744/70 +2.54c t 3.72; "
                       "KXLOWT 390/48 +3.06c t 3.21. Control KXRAIN reproduced (+4.21c, t 4.53).",
    "known_confound": "gross of fees; assumes the maker no-bid fills, which temperature's "
                      "adverse-selection history makes the main open question; winter 2024-25 "
                      "KXHIGH was sharply negative; the three families share weather days.",
    "authority": "NONE. It nominates this tape and fixes the cell; the bars come from the fill "
                 "model, not from the replay.",
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
    """Create the experiment, freeze v1 with its arm and gate, open e1 on the ACTIVE platform
    snapshot, record the TAGLESS probe, and register the PAPER deployment that gives the book
    its tag.

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
        title="mmsell temperature season — does the cheap-tail cell work on daily temperature",
        family="maker",
        hypothesis=(
            "On Kalshi's daily temperature ladders the cheap YES tail (YES <= 7c) settles YES "
            "less often than its price implies, as it does on daily rain, and the maker NO at "
            "the no-bid captures it through winter."
        ),
        mechanism=(
            "A favourite-longshot floor on a ladder of mutually exclusive brackets: the "
            "brackets far from the forecast trade at a few cents of lottery demand, and nobody "
            "sells YES below about 6c because it ties up 94c to earn 6c. Replayed on public "
            "history: ~6.6% implied against ~4% realized across three families."
        ),
        counterparty=(
            "YES buyers paying a few cents for a temperature bracket the forecast already rules "
            "out — lottery-ticket demand, not informed flow."
        ),
        falsification=(
            "At 120 settlement days the paper edge is below +1c/contract, or below zero at any "
            "point after 45 days, or daily P&L is unstable (mean/sd < 0). Informed late flow "
            "(forecast updates inside the day) picks off the resting NO, or winter fronts price "
            "the tail correctly or worse."
        ),
        universe="series starting KXHIGH or KXLOW (daily high, daily high T, daily low T)",
        docs={"thesis": "docs/MMSELL_LOOKALIKE_PROBE.md",
              "probe": "scripts/mmsell_lookalike_probe.py",
              "sibling": "kalshi_bot/experiment_os/rain_season.py"},
        notes=(
            "No promotion gate and no size arm, deliberately, as in the rain contract. The "
            "winter split is a monitoring read because the gate grammar has no seasonal subset."
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
            "every other series, by construction (`only=` on the two prefixes). No city is "
            "excluded: per-city replay counts (~50 entries) are noise, so cities are a read, "
            "never a filter."
        ),
        entry_rule=(
            "sell the cheap tail (buy NO at the no-bid) on a temperature market whose YES mid "
            "is 5-10 and whose YES price actually sold is <= 7c, 1h to 48h before close."
        ),
        exit_rule="hold to settlement; no stop, no take-profit.",
        sizing_rule="flat 1-contract clip; size is not an arm here.",
        execution_style="maker",
        independent_variable="none — one absolute arm judged on its own gate",
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": "pnl_cents_per_contract at a winter-spanning floor",
            "secondary": ["daily_pnl_stability", "settled_days", "settled_trades",
                          "realized_pnl_usd", "win_rate_pct"],
            "note": "the replay is gross of fees; the paper book records them.",
        },
        sample={"probe": PROBE_RULE,
                "paper_floor_settled_days": {CELL_ARM: floor},
                "horizon_settled_days": HORIZON_DAYS,
                "early_fail_floor_settled_days": EARLY_FAIL_FLOOR_DAYS},
        costs={"model": "post-2026-08-11 maker fee model"},
        provenance={"positions": "paper_trades for the tag",
                    "settlement": "Kalshi KXHIGH/KXHIGHT/KXLOWT settlement (NWS climate report "
                                  "for the named station)"},
        monitoring={
            "winter_stop": (
                f"pre-registered: once >= {WINTER_MIN_DAYS} December-February settlement days "
                f"exist, a Dec-Feb mean <= {WINTER_STOP_CENTS:+.0f}c/contract means stop the "
                "book. Read with the ops db channel; the gate grammar cannot express it, so "
                "it is an Experiment OS issue routed to Research Lab recommending the spec's "
                "removal, never a verdict."),
            "silent_arm": "the tag must show entries within SILENT_ARM_HOURS of the "
                          "MMSELL_VARIANTS append",
            "isolation": "events_optin / markets_considered_optin in the scan telemetry show "
                         "the opted-in reach apart from the control funnel",
        },
        docs={"thesis": "docs/MMSELL_LOOKALIKE_PROBE.md"},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    spec = json.loads(json.dumps(CELL_GATE_SPEC))
    spec["sample"][CELL_ARM]["value"] = floor
    gates = {CELL_GATE_KEY: service.register_gate(
        session, version, gate_key=CELL_GATE_KEY, kind="kill", spec=spec, registered_at=at,
        notes=("registered before the arm has traded. The +2c pass bar is the measured "
               "paper-to-live maker gap, not a fit to the replay; the floor is set by the "
               "calendar so the evidence spans winter."),
    )}
    service.freeze_version(session, version, now=at)

    epoch = service.open_epoch(
        session, version,
        reason=("v1's first operating interval, pinned to the platform snapshot active at "
                "registration."),
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
        notes="TAGLESS BY CONSTRUCTION: a replay of public history that places no order.",
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("the historical replay is recorded on v1 with its confounds (gross, assumed "
                "fills, one bad winter); it authorizes nothing beyond fixing the cell"),
        occurred_at=at, version=version, epoch=epoch,
    )

    paper = service.register_deployment(
        session, epoch,
        deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER,
        kind=DeploymentKind.PAPER,
        arms={CELL_ARM: CELL_TAG},
        config={"books": dict(BOOK_SPECS), "series_prefixes": list(SERIES_PREFIXES)},
        started_at=at,
        notes=("One PAPER book, no real money, the tag in no live arm set. Fresh tag, so it "
               "inherits no positions or history."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PAPER, actor=actor,
        reason=("pre-registration merged (docs/MMSELL_LOOKALIKE_PROBE.md); the forward "
                "winter tape is the evidence that decides"),
        occurred_at=at, version=version, epoch=epoch,
    )

    return {"version": version, "epoch": epoch, "probe": probe, "paper": paper,
            "gates": gates}
