"""mmsell weather bands — the contract for `Tempmmsell2`, `Tempmmsell3` and `Rainmmsell3`.

Registers one PAPER experiment with three arms, each with its own pre-registered keep/kill gate.
It arms nothing, trades nothing and touches no real money. Under NEW_ONLY a tag carried by no
active deployment arm cannot trade, so registering this contract is what lets these books start.

WHY THIS EXPERIMENT EXISTS. The rain and temperature contracts trade the shipped cell (YES mid
5-10, YES sold <= 7c). Option C of docs/MMSELL_LOOKALIKE_PROBE.md (§7 pre-registration, §10
results) replayed two bands either side of it on Kalshi's public history, under the same §3 gate:

  * upper (10 <= mid <= 16, YES sold <= 15c) PROMOTEs on KXHIGH (+2.41c, t 2.72, 24 months) and
    KXHIGHT (+3.60c, t 3.46), and HOLDs on KXLOWT;
  * ultra (1 <= mid <= 5, YES sold <= 4c) PROMOTEs on KXRAIN (+2.10c, t 7.81) and KXHIGHT
    (+2.04c, t 7.66), and HOLDs just under the +2c bar on KXHIGH and KXLOWT.

Each arm trades only the families that PROMOTEd in its band. Selecting a family is the gate's
own unit, not a fit: a family that HOLDs is left out.

WHAT IS GATED. Three absolute questions, one gate per arm: does each band, on its families, stay
above the +2c paper-to-live maker gap THROUGH WINTER on a forward tape? The floor is in
settlement days and cannot be reached before mid-February, as in the rain and temperature
contracts. The rain upper band is not here, because `Rainmmsell2` (8-12c) already tests it.

WHAT IS NOT GATED. Size and promotion, for the same reasons as the sibling contracts. This
contract also does not compare a band with the shipped cell: the books trade different markets
at different prices, so each arm is judged alone.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-weather-bands"

UPPER_ARM = "temp_upper"
HIGHT_ULTRA_ARM = "hight_ultra"
RAIN_ULTRA_ARM = "rain_ultra"

#: Fresh tags. LIVE_STRATEGIES matches by PREFIX. None of these prefixes another or any existing
#: tag (`Tempmmsell1`, `Rainmmsell1/2`), and no tag of ten or more (`Tempmmsell10`) exists that a
#: one-digit tag would prefix.
UPPER_TAG = "Tempmmsell2"
HIGHT_ULTRA_TAG = "Tempmmsell3"
RAIN_ULTRA_TAG = "Rainmmsell3"

PROBE_DEPLOYMENT_KEY = "mmsell-weather-bands-probe-1"
PAPER_DEPLOYMENT_KEY = "mmsell-weather-bands-paper-1"

UPPER_BAND = "lo=10,hi=16,maxyes=15"
ULTRA_BAND = "lo=1,hi=5,maxyes=4"

#: Temperature books reach the globally skipped series through their own `optin=`, exactly as
#: `Tempmmsell1` does. KXHIGH covers KXHIGH<city> and KXHIGHT<city>; KXLOWT HOLDs in both bands
#: and is left out.
UPPER_PARAMS = f"{UPPER_BAND},htcmax=48,optin=KXHIGH,only=KXHIGH"
HIGHT_ULTRA_PARAMS = f"{ULTRA_BAND},htcmax=48,optin=KXHIGH,only=KXHIGHT"
RAIN_ULTRA_PARAMS = f"{ULTRA_BAND},onlyx=KXRAIN"

BOOK_SPECS: dict[str, str] = {
    UPPER_TAG: UPPER_PARAMS,
    HIGHT_ULTRA_TAG: HIGHT_ULTRA_PARAMS,
    RAIN_ULTRA_TAG: RAIN_ULTRA_PARAMS,
}

GATE_KEYS: dict[str, str] = {
    UPPER_ARM: "temp_upper_winter_keep",
    HIGHT_ULTRA_ARM: "hight_ultra_winter_keep",
    RAIN_ULTRA_ARM: "rain_ultra_winter_keep",
}

SAMPLE_FLOOR_DAYS = 120
HORIZON_DAYS = 180
EARLY_FAIL_FLOOR_DAYS = 45
#: +2c is the measured paper-to-live maker gap (docs/MMSELL_FILL_MODEL.md), the bar every replay
#: in §10 was read against. In the ultra band a contract can earn at most its YES price (<= 4c),
#: so this bar is hard there. It is kept on purpose, not relaxed to fit the band.
PASS_CENTS = 2.0
FAIL_CENTS_AT_FLOOR = 1.0
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
            {"metric": "pnl_cents_per_contract", "arm": arm, "op": "<", "value": 0.0,
             "min_evidence": {"metric": "settled_days", "op": ">=",
                              "value": EARLY_FAIL_FLOOR_DAYS}},
            {"metric": "pnl_cents_per_contract", "arm": arm, "op": "<",
             "value": FAIL_CENTS_AT_FLOOR},
            {"metric": "daily_pnl_stability", "arm": arm, "op": "<", "value": 0.0},
        ],
    }


GATE_SPECS: dict[str, dict] = {
    UPPER_ARM: _gate(UPPER_ARM, "Keep/kill for the upper band (YES 10-15c) on KXHIGH and "
                                "KXHIGHT through winter. Judged alone; not pooled."),
    HIGHT_ULTRA_ARM: _gate(HIGHT_ULTRA_ARM, "Keep/kill for the ultra band (YES <= 4c) on KXHIGHT "
                                            "through winter. Judged alone; not pooled."),
    RAIN_ULTRA_ARM: _gate(RAIN_ULTRA_ARM, "Keep/kill for the ultra band (YES <= 4c) on KXRAIN "
                                          "through winter. Judged alone; not pooled."),
}

ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (UPPER_ARM, ArmRole.CONTROL,
     "Upper band on daily high temperature: 10<=mid<=16, YES sold <=15c, entry within T-48h, "
     "KXHIGH* (incl. KXHIGHT*) only. Buy NO at the no-bid, hold to settlement, 1 contract.",
     {"tag": UPPER_TAG, "book": UPPER_PARAMS, "series": ["KXHIGH"], "band": "mid 10-16, yes<=15"}),
    (HIGHT_ULTRA_ARM, ArmRole.TREATMENT,
     "Ultra band on KXHIGHT: 1<=mid<=5, YES sold <=4c, entry within T-48h. Same entry and exit.",
     {"tag": HIGHT_ULTRA_TAG, "book": HIGHT_ULTRA_PARAMS, "series": ["KXHIGHT"],
      "band": "mid 1-5, yes<=4"}),
    (RAIN_ULTRA_ARM, ArmRole.TREATMENT,
     "Ultra band on KXRAIN: 1<=mid<=5, YES sold <=4c, exact series match. Same entry and exit.",
     {"tag": RAIN_ULTRA_TAG, "book": RAIN_ULTRA_PARAMS, "series": ["KXRAIN"],
      "band": "mid 1-5, yes<=4"}),
)

HELD_CONSTANT: list[str] = [
    "entry is the mmsell maker entry on every arm: buy NO at the no-bid, 1 contract, paper",
    "exit is hold to settlement on every arm: no stop, take-profit, volatility gate or strangle",
    "each band is disjoint from the shipped cell (mid 5-10, YES <= 7): ultra sells YES <= 4c, so "
    "its mid is <= 4; upper needs mid >= 10",
    "the temperature arms reach the skipped series through their own `optin=`; the global skip "
    "list is unchanged and the tracker refuses every live mirror on a skipped series",
    "no contest cap or size rule; the global caps apply unchanged",
    "every tag is PAPER and appears in no live arm set",
]

PROBE_RULE: dict = {
    "instrument": "replay of each band on Kalshi's public live + historical archive (hourly "
                  "candles, no auth): scripts/mmsell_lookalike_probe.py --lo/--hi/--maxyes",
    "unit_of_observation": "first entry per market; bet unit one family-day",
    "window": "KXRAIN 2026-07..10, KXHIGH 2024-10..2026-10, KXHIGHT 2026-01..10",
    "recorded_result": "upper: KXHIGH +2.41c t 2.72, KXHIGHT +3.60c t 3.46 (KXLOWT HOLD); "
                       "ultra: KXRAIN +2.10c t 7.81, KXHIGHT +2.04c t 7.66 (KXHIGH, KXLOWT HOLD "
                       "just under +2c). Ops ids c-<band>-<family>-20261015a.",
    "known_confound": "gross of fees and assuming the maker fill; the ultra edge sits at the +2c "
                      "bar, so a forward PASS there is far from assured; the arms share weather "
                      "days with each other and with Tempmmsell1/Rainmmsell1.",
    "authority": "NONE. It fixes the bands and families; the bars come from the fill model.",
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
    """Create the experiment, freeze v1 with three arms and three gates, open e1 on the ACTIVE
    platform snapshot, record the TAGLESS probe, and register the PAPER deployment that gives the
    three books their tags. `promotion_sample_floor` may only RAISE the floor; a second run is
    refused."""
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
        title="mmsell weather bands — is the cheap tail overpriced outside the shipped cell",
        family="maker",
        hypothesis=(
            "The longshot overpricing that the shipped cell sells on daily rain and temperature "
            "extends to the cheaper (YES <= 4c) and dearer (YES 10-15c) bands, on the families "
            "where the replay PROMOTEd each band."
        ),
        mechanism=(
            "The same favourite-longshot floor: brackets the forecast rules out trade at a few "
            "cents of lottery demand at every price up to the mid-teens. Replayed, realized YES "
            "runs about a third below implied in both bands."
        ),
        counterparty="YES buyers paying for outcomes the forecast already rules out.",
        falsification=(
            "At 120 settlement days an arm's paper edge is below +1c/contract, or below zero at "
            "any point after 45 days, or its daily P&L is unstable (mean/sd < 0)."
        ),
        universe="KXHIGH* (upper), KXHIGHT* (ultra), KXRAIN (ultra)",
        docs={"thesis": "docs/MMSELL_LOOKALIKE_PROBE.md",
              "probe": "scripts/mmsell_lookalike_probe.py",
              "siblings": "kalshi_bot/experiment_os/temperature_season.py, rain_season.py"},
        notes="No promotion gate and no size arm, deliberately, as in the sibling contracts.",
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
            "KXLOWT in both bands and KXHIGH in the ultra band: each HOLDs in its replay. Rain "
            "in the upper band: `Rainmmsell2` already tests 8-12c."
        ),
        entry_rule=(
            "sell the cheap tail (buy NO at the no-bid) when the YES mid is in the arm's band "
            "and the YES price actually sold is under its ceiling: upper 10<=mid<=16, yes<=15; "
            "ultra 1<=mid<=5, yes<=4."
        ),
        exit_rule="hold to settlement; no stop, no take-profit. Identical on every arm.",
        sizing_rule="flat 1-contract clip on every arm; size is not an arm here.",
        execution_style="maker",
        independent_variable="none — three absolute arms, each judged on its own gate",
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": "pnl_cents_per_contract per arm, at a winter-spanning floor",
            "secondary": ["daily_pnl_stability", "settled_days", "settled_trades",
                          "realized_pnl_usd", "win_rate_pct"],
            "note": "arms are not pooled and not compared with each other.",
        },
        sample={"probe": PROBE_RULE,
                "paper_floor_settled_days": {arm: floor for arm, _, _, _ in ARMS},
                "horizon_settled_days": HORIZON_DAYS,
                "early_fail_floor_settled_days": EARLY_FAIL_FLOOR_DAYS},
        costs={"model": "post-2026-08-11 maker fee model, identical on every arm"},
        provenance={"positions": "paper_trades for the three tags",
                    "settlement": "Kalshi KXHIGH/KXHIGHT/KXRAIN settlement"},
        monitoring={"silent_arm": "every tag must show entries within SILENT_ARM_HOURS of the "
                                  "MMSELL_VARIANTS append"},
        docs={"thesis": "docs/MMSELL_LOOKALIKE_PROBE.md"},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    gates = {}
    for arm_key, _, _, _ in ARMS:
        spec = json.loads(json.dumps(GATE_SPECS[arm_key]))
        spec["sample"][arm_key]["value"] = floor
        gates[GATE_KEYS[arm_key]] = service.register_gate(
            session, version, gate_key=GATE_KEYS[arm_key], kind="kill", spec=spec,
            registered_at=at,
            notes=("registered before any arm has traded; the +2c bar is the maker gap, not a "
                   "fit to the replay"),
        )
    service.freeze_version(session, version, now=at)

    epoch = service.open_epoch(
        session, version,
        reason="v1's first operating interval; all three arms open together.",
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
        reason="the band replays are recorded on v1 with their confounds; they authorize nothing",
        occurred_at=at, version=version, epoch=epoch,
    )

    paper = service.register_deployment(
        session, epoch,
        deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER,
        kind=DeploymentKind.PAPER,
        arms={UPPER_ARM: UPPER_TAG, HIGHT_ULTRA_ARM: HIGHT_ULTRA_TAG,
              RAIN_ULTRA_ARM: RAIN_ULTRA_TAG},
        config={"books": dict(BOOK_SPECS)},
        started_at=at,
        notes="Three PAPER books, no real money, no tag in any live arm set. Fresh tags.",
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PAPER, actor=actor,
        reason=("pre-registration merged (docs/MMSELL_LOOKALIKE_PROBE.md §7, §10); the forward "
                "winter tape decides"),
        occurred_at=at, version=version, epoch=epoch,
    )

    return {"version": version, "epoch": epoch, "probe": probe, "paper": paper,
            "gates": gates}
