"""The mmsell WIN-set live canary — trade live only where the live canaries won, ALONGSIDE
`Kmmsell10`, sharing markets inside a registered combined bound. WS-027. Plan:
`docs/MMSELL_WIN_SET_CANARY.md`; evidence: `docs/MMSELL_LIVE_CANARY_PATTERNS.md` (PR #636).
Owner decision 2026-10-15 (design only — arming stays a hard stop).

THE QUESTION. Does the mmsell price-ceiling rule (lo=5, hi=10, maxyes=7), restricted to the
categories that won on BOTH paper and live (weather, index, the Politics regime, mentions),
earn a positive per-contract live return — i.e. does restricting the universe stop paying the
sports/crypto paper-to-live fill gap? Check 1 of the evidence doc: WIN +4.67c/contract on 277
independent paper markets, WIN - LOSE +3.53c (90% [+1.81, +5.14]); live weather +3.8c, index
+4.2c, Politics +5.5c.

A NEW EXPERIMENT, not a Kmmsell10 successor: a different question (a universe restriction, not
a sizing rule) on a book that runs BESIDE the incumbent, which stays unchanged.

THE UNIVERSE, exact (`onlyx`), and why each bar holds:
  * the WIN categories of `scripts/live_canary_patterns.category_of`, restricted to series the
    registry GRADUATES — `mmsell_live_min_tier="graduated"` refuses the rest live, and this
    package does not weaken it. On the 2026-10-16 manifest that is seven series:
      weather   KXRAIN       (temperature, KXHIGH/KXLOW, is refused live and skipped in paper)
      index     KXINX, KXINXU, KXNASDAQ100U
      Politics  KXTRUMPSAY   (KXTRUMPSAYCOMPANY is in_review; KXTRUMPSAYMONTH is barred)
      mentions  KXFEDMENTION, KXWCMENTION
  * `KXYTVIEWSHIGH` is NOT here although `category_of` calls it weather: it is YouTube view
    counts, matched on the substring HIGH. Recorded in the plan doc rather than inherited.
  * no series on `mmsell_live_skip_series` (none of the seven is).
An exact list, not substrings: a newly listed series joins only by a new Version.

SIZE AND BOUNDS (the risk envelope below). A 3-lot everywhere ($3.00 per order and per market,
the incumbent's per-market bound). Where both books hold one market the pair's COMBINED bound
is 6 contracts / $6.00 — Kmmsell10's largest clip plus this book's, and no more than the sum
of the two registered per-market bounds ($3.00 + $3.00). The executor enforces it
(`gate:shared_bound`) once `MMSELL_LIVE_SHARED_MARKET_PAIRS` names the pair.

TAGS. Fresh and prefix-safe under LIVE_STRATEGIES' PREFIX match, checked against every tag in
production on 2026-10-16 (ops `ws027-tags-2`): no existing tag starts with `Nmmsell10` or
`Pmmsell10`, and neither new tag starts with any existing tag. The PAPER tag deliberately does not start with
the LIVE tag — a paper book whose tag prefix-matches an allowlisted live tag would mirror live.
  live  Nmmsell10       twin  Nmmsell10_pt4 (the global `_pt4` suffix)       paper  Pmmsell10

ORDER IN MMSELL_VARIANTS IS MATERIAL. Books are visited per market in variant order and the
live mirror fires inside that visit, so `Nmmsell10` must come AFTER `Kmmsell10`: on a market
both want in the same cycle, Kmmsell10 rests first and keeps queue priority. That is the
mechanism behind Platform Change Review's ruling that admitting this partner does not change
Kmmsell10's world (`docs/LIVE_FILL_ATTRIBUTION.md` §6) — and the per-tag shared-market fill
rate (`live_fill_rate_pct` provenance) is what checks it.

GLOBALS, NAMED (live-paper-parallel §3b). This package sets ONE new variable,
`MMSELL_LIVE_SHARED_MARKET_PAIRS`, which affects only the two tags it names. It sets none of
the global mmsell knobs: `MMSELL_LIVE_MAX_OPEN_POSITIONS` (40), `LIVE_MAX_ORDER_DOLLARS` (3.0),
`MAX_TOTAL_EXPOSURE` (125), `MAX_MARKET_EXPOSURE` / `MAX_DAILY_LOSS` (shared with Kmmsell10 and
Alimm1) and `LIVE_PAPER_TWIN_SUFFIX` (`_pt4`) all stay as they are. The account-level breakers
are shared: this book's footprint counts against them. Measured 2026-10-16 (ops
`ws027-gates-1`): neither refused a single live entry in 14 days.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

from sqlalchemy import select

from . import enforcement, service
from .lifecycle import ArmRole, LifecycleState
from .models import ExperimentGate
from .read import get_experiment
from .successor_mmsell10_cell_size import KEEP_GATE_SPEC as _INCUMBENT_KEEP
from .successor_mmsell10_cell_size import LIVE_TAG as INCUMBENT_LIVE_TAG
from .successor_mmsell10_cell_size import RISK_ENVELOPE as _INCUMBENT_ENVELOPE
from .successor_mmsell10_cell_size import SUCCESSOR_KEY as INCUMBENT_KEY

EXPERIMENT_KEY = "mmsell-win-set"

#: How quiet an armed arm may go before the global monitor (WS-026) reads it STALE.
#: WIN-set flow is ~3-4 qualifying markets a day, so a day of silence is not news.
MONITOR_EXPECT = {"max_silence_hours": 48}

ARM_KEY = "winset"
PAPER_TAG = "Pmmsell10"
LIVE_TAG = "Nmmsell10"
TWIN_TAG = "Nmmsell10_pt4"

PAPER_DEPLOYMENT_KEY = "mmsell-winset-paper-1"
LIVE_DEPLOYMENT_KEY = "mmsell-winset-live-1"
TWIN_DEPLOYMENT_KEY = "mmsell-winset-twin-1"

#: The WIN categories (check 1, docs/MMSELL_LIVE_CANARY_PATTERNS.md §3).
WIN_CATEGORIES: tuple[str, ...] = ("weather", "index", "politics", "mentions")
#: Graduated, live-eligible WIN series on the 2026-10-16 manifest. Asserted in CI against
#: `category_of`, the registry tier and both skip lists.
WIN_SERIES: tuple[str, ...] = ("KXFEDMENTION", "KXINX", "KXINXU", "KXNASDAQ100U", "KXRAIN",
                               "KXTRUMPSAY", "KXWCMENTION")
#: Mapped to a WIN category by `category_of` but NOT a WIN market (YouTube views, "HIGH").
CATEGORY_ARTIFACTS: tuple[str, ...] = ("KXYTVIEWSHIGH",)

CONTRACTS = 3
#: The evidence book's band (`mmsell10:lo=5,hi=10,maxyes=7`) with the incumbent's live contest
#: semantics, the exact WIN universe, the graduated floor (so paper and live trade one universe)
#: and a 3-lot.
BOOK_PARAMS = (
    "lo=5,hi=10,maxyes=7,contestcap=1,contestkey=split,"
    f"size={CONTRACTS},universe=graduated,onlyx={'+'.join(WIN_SERIES)}"
)
LIVE_BOOK_SPEC = f"{LIVE_TAG}:{BOOK_PARAMS}"
PAPER_BOOK_SPEC = f"{PAPER_TAG}:{BOOK_PARAMS}"

#: The registered combined per-market bound for the pair (contracts, dollars).
COMBINED_MAX_CONTRACTS = 6
COMBINED_MAX_USD = 6.00
#: The `MMSELL_LIVE_SHARED_MARKET_PAIRS` value this package registers. Tags sorted, exactly
#: the canonical `SharedMarketPair.env_value()` spelling.
SHARED_PAIR_ENV = (f"{'+'.join(sorted((INCUMBENT_LIVE_TAG, LIVE_TAG)))}:"
                   f"{COMBINED_MAX_CONTRACTS}:{COMBINED_MAX_USD:.2f}")

PROMOTION_GATE_KEY = "paper_to_live_canary"
KEEP_GATE_KEY = "live_canary_keep"

#: The paper bar arm_live_canary re-evaluates synchronously. A SMOKE bar, not the evidence:
#: the evidence is check 1 on 8,351 independent paper trades. It asks only that the book trades
#: its intended universe at all (>= 30 settled) and is not negative there. ~10 days at WIN flow.
PROMOTION_GATE_SPEC: dict = {
    "description": (
        "WIN-set paper smoke bar: at least 30 settled paper trades on the exact WIN universe and "
        "a positive per-contract realized return. The case for the canary is check 1 of "
        "docs/MMSELL_LIVE_CANARY_PATTERNS.md (independent paper history), not this window."
    ),
    "sample": {ARM_KEY: {"metric": "settled_trades", "op": ">=", "value": 30}},
    "pass_all": [
        {"metric": "pnl_cents_per_contract", "arm": ARM_KEY, "op": ">", "value": 0.0},
    ],
}

#: The incumbent's keep contract on this book's arm, with the loss budget sized to this book.
KEEP_GATE_SPEC: dict = copy.deepcopy(_INCUMBENT_KEEP)
KEEP_GATE_SPEC["description"] = (
    "Keep/stop for the mmsell WIN-set canary (Nmmsell10): the incumbent's live contract with the "
    "canary loss budget at -$15 and the per-market loss bound at $3.00 (one 3-lot). Every clause "
    "addresses deployment_kind='live' and, once METRICS_ENGINE:live_fill_attribution_2026_10 is "
    "active, reads this tag's own fills on a market it shares with Kmmsell10."
)


def _rename_arm(spec, old: str, new: str):
    """Every reference to arm `old` — a key (`sample`), a value (`arm`) or a list entry
    (`arms`) — becomes `new`. Anything else is copied as is."""
    if isinstance(spec, dict):
        return {(new if k == old else k): _rename_arm(v, old, new) for k, v in spec.items()}
    if isinstance(spec, list):
        return [_rename_arm(v, old, new) for v in spec]
    return new if spec == old else spec


KEEP_GATE_SPEC = _rename_arm(KEEP_GATE_SPEC, "mmsell10", ARM_KEY)
KEEP_LOSS_BUDGET_USD = -15.0
KEEP_PER_MARKET_BOUND_USD = 3.0
for _clause in KEEP_GATE_SPEC["fail_any"]:
    if _clause["metric"] == "live_realized_pnl_usd":
        _clause["value"] = KEEP_LOSS_BUDGET_USD
    if _clause["metric"] == "live_max_realized_loss_usd":
        _clause["value"] = KEEP_PER_MARKET_BOUND_USD

RISK_ENVELOPE: dict = {
    "stage": "canary_win_set_stage_1",
    "universe": f"exact series allowlist {list(WIN_SERIES)} (WIN categories, graduated, "
                "live-eligible)",
    "contracts_per_order": CONTRACTS,
    "max_order_dollars": 3.00,
    "max_market_exposure_usd": 3.00,
    "shared_market": {
        "partner_tag": INCUMBENT_LIVE_TAG,
        "partner_experiment": INCUMBENT_KEY,
        "partner_max_market_exposure_usd": _INCUMBENT_ENVELOPE["max_market_exposure_usd"],
        "max_combined_contracts": COMBINED_MAX_CONTRACTS,
        "max_combined_usd": COMBINED_MAX_USD,
        "env": SHARED_PAIR_ENV,
        "queue_priority": f"{INCUMBENT_LIVE_TAG} precedes {LIVE_TAG} in MMSELL_VARIANTS",
    },
    "max_contest_positions": 1,
    "max_open_positions": 40,
    # The open cap is the GLOBAL MMSELL_LIVE_MAX_OPEN_POSITIONS (40), applied per tag. WIN flow
    # is ~3-4 markets a day held 1-3 days, so ~10 open at ~$2.80 is the expected footprint; the
    # all-40 worst case is the bound.
    "expected_book_exposure_usd": 28.20,
    "max_book_exposure_usd": 112.80,
    "total_canary_loss_budget_usd": 15.00,
    "order_timeout_seconds": 14400,
    "entry_price_offset_cents": 0,
    "exit_policy": "hold to settlement; no TP/SL",
    "settings": {"MMSELL_LIVE_SHARED_MARKET_PAIRS": SHARED_PAIR_ENV},
    "portfolio_note": (
        "MAX_TOTAL_EXPOSURE (125), MAX_MARKET_EXPOSURE and MAX_DAILY_LOSS are account-wide and "
        "shared with Kmmsell10 and Alimm1; this package sets none of them. Per-book budgets are "
        "parked (docs/workstreams/ACTIVE.md), so this book's footprint can, near the cap, gate "
        "the incumbent out of an entry or be gated out itself. Measured 2026-10-16: neither "
        "breaker refused an entry in 14 days. Re-read before arming."
    ),
}

#: Everything the two runtime steps set: the paper book (paper scope), then at arm time the
#: live book, the allowlist entry and the pair (owner-confirmed Live Ops acts).
ACTIVATION_VARS: frozenset[str] = (
    frozenset(RISK_ENVELOPE["settings"]) | {"MMSELL_VARIANTS", "LIVE_STRATEGIES"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register(
    session,
    *,
    actor: str,
    evidence_started_at: datetime | None = None,
    promotion_sample_floor: int | None = None,
    now: datetime | None = None,
) -> dict:
    """Create the experiment and walk it to PAPER on `Pmmsell10`. Arms nothing, places no
    order, ends no other deployment — the incumbent is untouched."""
    if promotion_sample_floor is not None:
        raise service.ExperimentOsError(
            "this package registers its own promotion bar (30 settled) — drop "
            f"promotion_sample_floor={promotion_sample_floor!r} from the envelope."
        )
    at = now or _now()
    if get_experiment(session, EXPERIMENT_KEY) is not None:
        raise service.ExperimentOsError(
            f"{EXPERIMENT_KEY} already exists — this package refuses rather than re-running"
        )
    experiment = service.create_experiment(
        session, key=EXPERIMENT_KEY, origin="operator",
        title="mmsell price-ceiling restricted to the WIN categories, beside Kmmsell10",
        family="maker",
        hypothesis=(
            "Restricted to the categories that won on both paper and live (weather, index, the "
            "Politics regime, mentions), the mmsell price-ceiling rule earns a positive "
            "per-contract live return, because those categories carry little of the "
            "paper-to-live fill gap that sports and crypto pay."
        ),
        mechanism=(
            "The live loss is a fill gap concentrated where informed flow is plausible (liquid "
            "sports and crypto): paper +1.9c vs live -2.4c on sports totals, +0.3c vs -8.3c on "
            "crypto. Slow-resolving weather, index, political-speech and mention markets show "
            "no such gap (live weather +3.8c, index +4.2c, Politics +5.5c)."
        ),
        falsification=(
            "The keep gate's stops trip, or at its sample floor (150 settled live contracts) "
            "the live per-contract realized return is <= 0, or the twin win-rate gap exceeds "
            "the incumbent's bound — i.e. the WIN set also pays a fill gap live."
        ),
        docs={"canary": "docs/MMSELL_WIN_SET_CANARY.md",
              "evidence": "docs/MMSELL_LIVE_CANARY_PATTERNS.md",
              "attribution": "docs/LIVE_FILL_ATTRIBUTION.md"},
        actor=actor, now=at,
    )
    version = service.create_experiment_version(
        session, experiment,
        hypothesis="WIN-set restriction of the mmsell price-ceiling rule is live-positive.",
        universe_selector=(
            f"exact series {', '.join(WIN_SERIES)} (WIN categories, graduated, not paused); "
            "cheap band lo=5,hi=10, entry-price ceiling maxyes=7"
        ),
        entry_rule="rest a buy-NO maker order at the no-bid (offset 0)",
        exit_rule="hold to settlement",
        sizing_rule=f"{CONTRACTS} contracts under a $3.00 per-order dollar cap",
        execution_style="maker",
        control_required=False,
        control_exemption_reason=(
            "the paper control of the same rule is Pmmsell10 (this experiment's paper arm) and "
            "the unrestricted rule is mmsell10 (the incumbent's paper control); the execution "
            "control is the registered paper TWIN, armed at the same instant"
        ),
        independent_variable="market universe restricted to the WIN categories",
        held_constant=[
            "band and ceiling (lo=5, hi=10, maxyes=7)",
            "contestcap=1, contestkey=split",
            "the 0c offset and the 4h order timeout",
            "hold-to-settlement exits",
            "the tier bar and the live series pauses",
            "the fee model",
            f"the incumbent {INCUMBENT_LIVE_TAG}: no spec, epoch or Version change",
        ],
        risk=RISK_ENVELOPE,
        docs={"canary": "docs/MMSELL_WIN_SET_CANARY.md"},
        change_reason="new experiment (WS-027, owner decision 2026-10-15)",
        now=at,
    )
    service.add_arm(
        session, version, arm_key=ARM_KEY, role=ArmRole.TREATMENT,
        description="mmsell price-ceiling on the exact WIN-set series, 3-lot",
        params={"lo": 5, "hi": 10, "maxyes": 7, "size": CONTRACTS,
                "onlyx": list(WIN_SERIES), "universe": "graduated"},
        strategy_tag=PAPER_TAG,
    )
    promotion_gate = service.register_gate(
        session, version, gate_key=PROMOTION_GATE_KEY, kind="promotion",
        spec=PROMOTION_GATE_SPEC,
        from_state=LifecycleState.PAPER, to_state=LifecycleState.LIVE_CANARY,
        registered_at=at, notes="paper smoke bar; the evidence is check 1",
    )
    keep_gate = service.register_gate(
        session, version, gate_key=KEEP_GATE_KEY, kind="kill",
        spec=KEEP_GATE_SPEC, registered_at=at,
        notes="the incumbent's keep contract with this book's loss budget",
    )
    service.freeze_version(session, version, now=at)
    evidence_at = evidence_started_at or at
    service.mark_gate_evidence_started(session, promotion_gate, at=evidence_at)
    service.mark_gate_evidence_started(session, keep_gate, at=at)
    service.transition_experiment(session, experiment, LifecycleState.PROBE,
                                  actor=actor, occurred_at=at,
                                  reason="contract registered; the probe is check 1 of "
                                         "docs/MMSELL_LIVE_CANARY_PATTERNS.md")
    service.transition_experiment(session, experiment, LifecycleState.PAPER,
                                  actor=actor, occurred_at=at,
                                  reason="paper on Pmmsell10 accrues the promotion sample")
    epoch = service.open_epoch(
        session, version,
        reason="first operating interval, pinned to the snapshot active at registration",
        started_at=at,
    )
    paper = service.register_deployment(
        session, epoch, deployment_key=PAPER_DEPLOYMENT_KEY,
        stage=LifecycleState.PAPER, kind="paper",
        arms={ARM_KEY: PAPER_TAG}, started_at=at,
        notes=f"paper book {PAPER_BOOK_SPEC}",
    )
    return {
        "experiment": experiment, "version": version, "promotion_gate": promotion_gate,
        "keep_gate": keep_gate, "epoch": epoch, "paper_deployment": paper,
        "registered_at": at, "evidence_started_at": evidence_at,
    }


def material_config() -> dict:
    """The drift baseline: the book's params AND the shared pair it was registered with."""
    return {
        "material": enforcement.live_material_block(
            books={LIVE_TAG: (TWIN_TAG, BOOK_PARAMS)},
            shared_market_pair=SHARED_PAIR_ENV,
        ),
        "book_spec": LIVE_BOOK_SPEC,
        "twin_tag": TWIN_TAG,
        "risk": RISK_ENVELOPE,
    }


def arm(
    session,
    *,
    approved_by: str,
    actor: str = "operator",
    started_at: datetime | None = None,
    reason: str | None = None,
) -> dict:
    """Arm through `service.arm_live_canary`, the one sanctioned path: fresh tags, the twin at
    the same instant, the envelope pre-registered. Places no order by itself; the env step
    (MMSELL_VARIANTS append after Kmmsell10, LIVE_STRATEGIES, the pair) is a separate act."""
    from .read import latest_version

    experiment = get_experiment(session, EXPERIMENT_KEY)
    if experiment is None:
        raise service.ExperimentOsError(f"{EXPERIMENT_KEY} does not exist — REGISTER_PACKAGE "
                                        "first")
    version = latest_version(session, experiment)
    gate = session.scalar(
        select(ExperimentGate).where(
            ExperimentGate.version_id == version.id,
            ExperimentGate.gate_key == PROMOTION_GATE_KEY,
        )
    )
    if gate is None:
        raise service.ExperimentOsError(f"{EXPERIMENT_KEY} has no {PROMOTION_GATE_KEY} gate")
    live, twin, epoch = service.arm_live_canary(
        session, experiment,
        gate=gate,
        approved_by=approved_by,
        live_key=LIVE_DEPLOYMENT_KEY,
        twin_key=TWIN_DEPLOYMENT_KEY,
        live_tags={ARM_KEY: LIVE_TAG},
        twin_tags={ARM_KEY: TWIN_TAG},
        config=material_config(),
        started_at=started_at,
        actor=actor,
        reason=reason or (
            f"mmsell WIN-set canary armed on {LIVE_TAG} with twin {TWIN_TAG} at one boundary; "
            f"shares markets with {INCUMBENT_LIVE_TAG} inside {SHARED_PAIR_ENV}, "
            f"pre-registered on v{version.version}"
        ),
    )
    return {"live": live, "twin": twin, "epoch": epoch}


__all__ = [
    "ACTIVATION_VARS", "ARM_KEY", "BOOK_PARAMS", "CATEGORY_ARTIFACTS", "COMBINED_MAX_CONTRACTS",
    "COMBINED_MAX_USD", "CONTRACTS", "EXPERIMENT_KEY", "INCUMBENT_KEY", "INCUMBENT_LIVE_TAG",
    "KEEP_GATE_KEY", "KEEP_GATE_SPEC", "LIVE_BOOK_SPEC", "LIVE_DEPLOYMENT_KEY", "LIVE_TAG",
    "PAPER_BOOK_SPEC", "PAPER_DEPLOYMENT_KEY", "PAPER_TAG", "PROMOTION_GATE_KEY",
    "PROMOTION_GATE_SPEC", "RISK_ENVELOPE", "SHARED_PAIR_ENV", "TWIN_DEPLOYMENT_KEY", "TWIN_TAG",
    "WIN_CATEGORIES", "WIN_SERIES", "arm", "material_config", "register",
]
