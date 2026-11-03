"""mmsell mentions forward — the contract for `Basemmsell10`.

Registers one PAPER experiment with one arm and one keep/kill gate. Arms nothing, trades
nothing, touches no real money: under NEW_ONLY a tag no active deployment arm carries cannot
trade, so registering this contract is what lets this one book start.

WHY THIS EXPERIMENT EXISTS. `docs/MMSELL_LIVE_CANARY_PATTERNS.md` §6 pre-registered a forward
read on the paper already running: every `mmsell10` paper MENTION trade that closes after
2026-10-15 counts, and at 60 settled trades mentions either qualify for the WIN-set live
allowlist (>= +2c/contract), are dropped from it (<= 0c), or HOLD to 120. On 2026-11-02 the
owner retired `mmsell-price-ceiling-cell-size` (the MMSELL close-out,
`docs/handoffs/HANDOFF-mmsell-standdown-closeout.md`), and with it the base `mmsell10` paper
book, its only open arm. The read's sample stopped growing at 2026-11-02 14:29Z.

The owner's direction (2026-11-03): "add that paper book back just as a new name and tied to
this experiment". This package does exactly that: the base book's spec, verbatim
(`lo=5,hi=10,maxyes=7`, recovered from the close-out's own env receipt `lo-mmclose-env-m10`),
on a FRESH tag, so it inherits no positions or history. DEC-041 declares the tag the §6
read's successor sample, taken blind (counts only; no outcome in the window was read).

WHAT IS GATED HERE. Nothing new is being tested: the cell is mmsell10's, unchanged. The gate
is a keep/kill guard on the copy itself, so a tape that has gone bad is stopped on the
evaluator's verdict rather than left running. The §6 read is the decision this book exists
for; the gate grammar has no market-type subset, so that read is recorded as a pre-registered
MONITORING read with a named consequence, never a verdict (the temperature-season precedent).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import service
from .lifecycle import ArmRole, DeploymentKind, LifecycleState
from .read import get_experiment

EXPERIMENT_KEY = "mmsell-mentions-forward"

#: How quiet an armed arm may go before the global monitor (WS-026) reads it STALE. The base
#: cell wrote about 145 paper trades a day; a day of silence means the book is gone.
#: Monitoring configuration only — never a gate, authorizes nothing.
MONITOR_EXPECT = {"max_silence_hours": 24}

BASE_ARM = "base_cell"

#: Fresh tag. LIVE_STRATEGIES matches by PREFIX: no existing tag starts `Base`, and the tag
#: prefixes none of them.
BASE_TAG = "Basemmsell10"

PROBE_DEPLOYMENT_KEY = "mmsell-mentions-forward-probe-1"
PAPER_DEPLOYMENT_KEY = "mmsell-mentions-forward-paper-1"

BASE_GATE_KEY = "base_cell_keep"

#: The retired base book's spec, verbatim (`mmsell10:lo=5,hi=10,maxyes=7` in MMSELL_VARIANTS
#: until env act `lo-mmclose-env-m10`, 2026-11-02 14:52Z): buy NO at the no-bid when
#: 5 <= YES mid <= 10 and the YES price actually sold is <= 7c, every series the global skip
#: list allows.
BASE_PARAMS = "lo=5,hi=10,maxyes=7"

BOOK_SPECS: dict[str, str] = {BASE_TAG: BASE_PARAMS}

#: No `ACTIVATION_VARS`: a package that arms nothing declares none. The book starts when its
#: spec is appended to `MMSELL_VARIANTS` through the `env` channel (read the live value
#: first, append, never retype) — a paper-only append.

#: The §6 read, as pre-registered (docs/MMSELL_LIVE_CANARY_PATTERNS.md §6). Restated here
#: only so the monitoring text and the tests can name it; the doc is canonical.
MENTIONS_FIRST_LOOK = 60
MENTIONS_EXTENDED_LOOK = 120
MENTIONS_QUALIFY_CENTS = 2.0
MENTIONS_DROP_CENTS = 0.0
#: Where the predecessor's sample stops and this one's starts (DEC-041).
PREDECESSOR_TAG = "mmsell10"
PREDECESSOR_LAST_TRADE = "2026-11-02T14:29:36Z"

#: Keep/kill guard on the copy. The cell's record is long (paper ~+1c/contract over
#: ~10,000 settled trades), so this is not a discovery bar: it catches a tape that has turned
#: negative, and it ends the copy once the §6 read can have been taken.
SAMPLE_FLOOR_DAYS = 30
HORIZON_DAYS = 90
EARLY_FAIL_FLOOR_DAYS = 14
#: Pass bar: +1c, the cell's long-run paper level, not a fit to anything in this window
#: (which has no trades yet). Fail: below zero.
PASS_CENTS = 1.0
FAIL_CENTS = 0.0
STABILITY_BAR = 0.10


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
            {"metric": "pnl_cents_per_contract", "arm": arm, "op": "<", "value": FAIL_CENTS,
             "min_evidence": {"metric": "settled_days", "op": ">=",
                              "value": EARLY_FAIL_FLOOR_DAYS}},
            {"metric": "daily_pnl_stability", "arm": arm, "op": "<", "value": 0.0},
        ],
    }


BASE_GATE_SPEC: dict = _gate(
    BASE_ARM,
    "Keep/kill guard on the base mmsell10 cell carried forward on a fresh tag. FAIL (the copy "
    "has turned negative, or its daily P&L is unstable) ends the book. PASS keeps it; it "
    "authorizes no live change. The decision this book exists for is the pre-registered "
    "mentions forward read (MMSELL_LIVE_CANARY_PATTERNS.md §6), recorded as monitoring.",
)

ARMS: tuple[tuple[str, ArmRole, str, dict], ...] = (
    (BASE_ARM, ArmRole.CONTROL,
     "The base mmsell10 cell, verbatim: 5<=mid<=10, YES sold <=7c, every series the global "
     "skip list allows. Buy NO at the no-bid, hold to settlement, 1 contract.",
     {"tag": BASE_TAG, "book": BASE_PARAMS, "predecessor_tag": PREDECESSOR_TAG,
      "band": "mid 5-10, yes<=7"}),
)

PROBE_RULE: dict = {
    "instrument": "the predecessor's own paper tape: settled mmsell10 paper trades on markets "
                  "no live canary traded (scripts/live_canary_patterns.py)",
    "window": "through 2026-10-15 (MMSELL_LIVE_CANARY_PATTERNS.md §7, ops "
              "paper-mmsell10-nolive-20261015a)",
    "recorded_result": "8,351 settled mmsell10 paper trades over 81 event days; mentions were "
                       "left to a forward read (§6) rather than decided on that history",
    "authority": "NONE. It fixes the cell and the forward read; it decides nothing.",
}

HELD_CONSTANT: list[str] = [
    "the spec is the retired base book's, character for character (lo=5,hi=10,maxyes=7)",
    "the global skip list and every global cap are unchanged",
    "entry is the mmsell maker entry: buy NO at the no-bid, 1 contract, paper",
    "exit is hold to settlement: no stop, take-profit, volatility gate or strangle",
    "the tag is PAPER and appears in no live arm set; LIVE_STRATEGIES is not touched",
]


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
    snapshot, and register the PAPER deployment that gives the book its tag.

    `promotion_sample_floor` may only RAISE the 30-settlement-day floor. Idempotence is
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
        title="mmsell mentions forward — the base mmsell10 paper cell carried forward",
        family="maker",
        hypothesis=(
            "The base mmsell10 cheap-tail cell, on MENTION markets, earns >= +2c/contract on "
            "paper fills that close after 2026-10-15 (the pre-registered §6 forward read)."
        ),
        mechanism=(
            "A favourite-longshot floor: mention long shots trade at a few cents of lottery "
            "demand and settle YES less often than priced; the maker NO at the no-bid "
            "collects the gap."
        ),
        counterparty=(
            "YES buyers paying a few cents for a word that is unlikely to be said — "
            "lottery-ticket demand, plus informed flow near the event, which is the risk."
        ),
        falsification=(
            "At 60 settled mention trades (pooled with the predecessor's, DEC-041) the mean is "
            "<= 0c/contract: mentions are dropped from the WIN-set allowlist. For the copy "
            "itself: below 0c after 14 settled days, or unstable daily P&L."
        ),
        universe="every series the global skip list allows (the base mmsell10 universe)",
        docs={"thesis": "docs/MMSELL_LIVE_CANARY_PATTERNS.md",
              "decision": "docs/DECISIONS.md#dec-041",
              "closeout": "docs/handoffs/HANDOFF-mmsell-standdown-closeout.md"},
        notes=(
            "A carry-forward, not a new hypothesis: the predecessor's spec on a fresh tag, so "
            "a pre-registered read keeps its sample. No promotion gate and no size arm."
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
        universe_exclusions="the global skip list, unchanged; nothing book-specific",
        entry_rule=(
            "sell the cheap tail (buy NO at the no-bid) on a market whose YES mid is 5-10 and "
            "whose YES price actually sold is <= 7c."
        ),
        exit_rule="hold to settlement; no stop, no take-profit.",
        sizing_rule="flat 1-contract clip; size is not an arm here.",
        execution_style="maker",
        independent_variable="none — one absolute arm judged on its own gate",
        held_constant=HELD_CONSTANT,
        control_required=True,
        metrics={
            "primary": "pnl_cents_per_contract (the copy's keep/kill guard)",
            "secondary": ["daily_pnl_stability", "settled_days", "settled_trades"],
            "note": "the §6 mentions read is a monitoring read on the mention subset.",
        },
        sample={"probe": PROBE_RULE,
                "paper_floor_settled_days": {BASE_ARM: floor},
                "horizon_settled_days": HORIZON_DAYS,
                "early_fail_floor_settled_days": EARLY_FAIL_FLOOR_DAYS,
                "predecessor": {"tag": PREDECESSOR_TAG,
                                "last_trade": PREDECESSOR_LAST_TRADE}},
        costs={"model": "post-2026-08-11 maker fee model"},
        provenance={"positions": "paper_trades for the tag",
                    "settlement": "Kalshi settlement of each market"},
        monitoring={
            "mentions_forward_read": (
                f"pre-registered (MMSELL_LIVE_CANARY_PATTERNS.md §6; successor sample per "
                f"DEC-041): {PREDECESSOR_TAG} paper mention trades closing after 2026-10-15 "
                f"up to {PREDECESSOR_LAST_TRADE}, pooled with {BASE_TAG}'s from its first "
                f"trade. At {MENTIONS_FIRST_LOOK} settled: >= {MENTIONS_QUALIFY_CENTS:+.0f}c "
                f"qualifies mentions for the WIN-set live allowlist, <= "
                f"{MENTIONS_DROP_CENTS:+.0f}c drops them, otherwise HOLD to "
                f"{MENTIONS_EXTENDED_LOOK}. A qualification is an OWNER decision through a "
                "successor package (live allowlist change), never a verdict here."),
            "silent_arm": "the tag must show entries within SILENT_ARM_HOURS of the "
                          "MMSELL_VARIANTS append",
        },
        docs={"thesis": "docs/MMSELL_LIVE_CANARY_PATTERNS.md"},
        now=at,
    )

    for arm_key, role, description, params in ARMS:
        service.add_arm(session, version, arm_key=arm_key, role=role,
                        description=description, params=params)

    spec = json.loads(json.dumps(BASE_GATE_SPEC))
    spec["sample"][BASE_ARM]["value"] = floor
    gates = {BASE_GATE_KEY: service.register_gate(
        session, version, gate_key=BASE_GATE_KEY, kind="kill", spec=spec, registered_at=at,
        notes=("registered before the arm has traded. A guard on a carried-forward cell, not "
               "a discovery bar."),
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
        notes="TAGLESS BY CONSTRUCTION: the predecessor's recorded tape; places no order.",
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PROBE, actor=actor,
        reason=("the predecessor's paper history is recorded on v1 (§7); it fixes the cell "
                "and the forward read, and authorizes nothing else"),
        occurred_at=at, version=version, epoch=epoch,
    )

    paper = service.register_deployment(
        session, epoch,
        deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER,
        kind=DeploymentKind.PAPER,
        arms={BASE_ARM: BASE_TAG},
        config={"books": dict(BOOK_SPECS), "predecessor_tag": PREDECESSOR_TAG},
        started_at=at,
        notes=("One PAPER book, no real money, the tag in no live arm set. Fresh tag, so it "
               "inherits no positions or history."),
    )
    service.transition_experiment(
        session, experiment, LifecycleState.PAPER, actor=actor,
        reason=("owner direction 2026-11-03: carry the retired base mmsell10 paper book "
                "forward on a fresh tag for the §6 mentions read (DEC-041)"),
        occurred_at=at, version=version, epoch=epoch,
    )

    return {"version": version, "epoch": epoch, "probe": probe, "paper": paper,
            "gates": gates}
