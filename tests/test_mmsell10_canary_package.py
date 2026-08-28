"""The mmsell10 Stage-1 live canary, proved end to end before any real money.

This file is the acceptance evidence for `kalshi_bot/experiment_os/canary_mmsell10.py`.
It builds `mmsell-price-ceiling` as production actually holds it — v1 frozen
2026-08-16, two arms (`mmsell9` + `mmsell10`), no `risk_json` — under NEW_ONLY
enforcement, and then proves three things in order:

  1. **Why v1 cannot be armed.** Both structural refusals are reproduced against
     the real `arm_live_canary`, not asserted in prose. They are the reason the
     package registers a successor version at all, and if either ever stops being
     true these tests fail loudly rather than leaving a stale justification in a
     docstring.
  2. **That the successor registration is safe.** Single arm, pre-registered
     envelope, gates frozen before arming, and — the part easiest to get wrong —
     the `mmsell10` tag handed from v1 to v2 without ever resolving to two active
     deployment arms, which the enforcement resolver refuses as ambiguous and
     which would have stopped the paper book.
  3. **That the armed pair is what the contract says.** Fresh tags, one exact
     boundary, a first-class twin link, no inherited evidence or positions, every
     keep/stop metric resolving to the deployment it names, each risk gate
     demonstrated, and parameter drift detected on either side.

Arming happens only inside these tests, against in-memory SQLite. Nothing here
touches production and nothing places an order.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from kalshi_bot.experiment_os import canary_mmsell10 as pkg
from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import evaluator, read
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.metrics import compute_metric
from kalshi_bot.experiment_os.models import (
    ExperimentDeployment,
    ExperimentDeploymentArm,
    ExperimentIntegrityEvent,
    ExperimentOsEnforcement,
)
from kalshi_bot.models import Fill, LiveOrder, LivePaperParityEvent, PaperTrade, Position

UTC = timezone.utc
#: Relative to the wall clock, not absolute. Evidence windows end at `now()`, so
#: a hard-coded boundary in the future would silently make every sample empty and
#: turn these tests into a slow-motion time bomb.
_NOW = datetime.now(UTC)
V1_FROZE = _NOW - timedelta(days=12)     # v1 froze 2026-08-16 in production
ARMED_AT = _NOW - timedelta(days=2)


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _set_mode(s, mode, *, cutover_id):
    return enf.record_enforcement_change(
        s, mode=mode, actor="operator", reason="test", cutover_id=cutover_id,
        readiness={"ok": True, "checks": {}},
    )


@pytest.fixture
def price_ceiling(xos_session, xos_platform):
    """`mmsell-price-ceiling` as production holds it: PAPER, v1 frozen with TWO
    secondary arms and NO risk envelope, one two-arm paper deployment, and the
    promotion gate on the bar v1 actually registered."""
    s = xos_session
    exp = svc.create_experiment(s, key=pkg.EXPERIMENT_KEY, origin="operator")
    ver = svc.create_experiment_version(
        s, exp,
        hypothesis="capping entry price keeps the fillable cheap cells",
        independent_variable="entry-price ceiling (maxyes) vs cell selection",
        control_required=False,
        control_exemption_reason="gated on absolute realizable per-trade",
        now=V1_FROZE,
    )
    svc.add_arm(s, ver, arm_key="mmsell9", role="secondary", strategy_tag="mmsell9")
    svc.add_arm(s, ver, arm_key="mmsell10", role="secondary",
                params={"lo": 5, "hi": 10, "maxyes": 7}, strategy_tag="mmsell10")
    svc.freeze_version(s, ver, now=V1_FROZE)
    epoch = svc.open_epoch(s, ver, reason="paper", started_at=V1_FROZE)
    svc.register_deployment(
        s, epoch, deployment_key=pkg.LEGACY_PAPER_DEPLOYMENT_KEY,
        stage="PAPER", kind="paper",
        arms={"mmsell9": "mmsell9", "mmsell10": "mmsell10"}, started_at=V1_FROZE,
    )
    gate = svc.register_gate(
        s, ver, gate_key=pkg.PROMOTION_GATE_KEY, kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={"pass_all": [{"metric": "realizable_cents_per_trade", "arm": "*",
                            "op": ">", "value": 0}]},
    )
    svc.mark_gate_evidence_started(s, gate, at=V1_FROZE)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    s.commit()
    return exp, ver, epoch, gate


def _paper_trades(s, tag, n, *, pnl=0.02, price=94, at=None, status="settled"):
    """Settled paper rows priced inside a trusted fill-calibration cell, so
    `realizable_cents_per_trade` resolves rather than reporting no coverage."""
    for i in range(n):
        s.add(PaperTrade(
            market_ticker=f"{tag}-{i}", strategy=tag, status=status, pnl=pnl,
            quantity=1, side="no", action="buy", assumed_price=price,
            created_at=at or (ARMED_AT + timedelta(minutes=1))))


def _register(s, **kw):
    out = pkg.register_successor_version(s, actor="operator", now=ARMED_AT, **kw)
    s.commit()
    return out


# ===========================================================================
# 1. Why the current version cannot be armed
# ===========================================================================


def test_v1_cannot_be_armed_because_it_has_no_risk_envelope(price_ceiling, xos_session):
    """Refusal #1, reproduced. The envelope is part of the scientific contract,
    so it cannot be bolted on after the fact."""
    s = xos_session
    exp, ver, _epoch, gate = price_ceiling
    assert ver.risk_json is None
    with pytest.raises(svc.ExperimentOsError, match="risk envelope"):
        svc.arm_live_canary(
            s, exp, gate=gate, approved_by="operator",
            live_key="x-live", twin_key="x-twin",
            live_tags={"mmsell9": "Lm9", "mmsell10": "Lm10"},
            twin_tags={"mmsell9": "Lm9_pt", "mmsell10": "Lm10_pt"},
            started_at=ARMED_AT,
        )
    assert exp.state == "PAPER"


def test_v1s_risk_envelope_cannot_be_added_after_freezing(price_ceiling, xos_session):
    """...and the obvious shortcut is refused too. A changed contract is a NEW
    version, which is the whole reason the package registers one."""
    s = xos_session
    _exp, ver, _epoch, _gate = price_ceiling
    ver.risk_json = pkg.RISK_ENVELOPE
    with pytest.raises(svc.ImmutableRecord, match="frozen"):
        s.flush()
    s.rollback()


def test_a_two_arm_contract_cannot_be_armed_on_one_arm(xos_session, xos_platform):
    """Refusal #2, with refusal #1 granted for the sake of argument: even WITH a
    risk envelope, a two-arm contract cannot be armed on one arm. The sanctioned
    path requires the tag maps to equal the declared arm set exactly, so a canary
    on v1's shape would have to put mmsell9 — the arm whose observed paper
    economics are negative — on real money as well.

    Built as its own two-arm experiment rather than by mutating v1, because
    mutating v1 is precisely what the previous test proves impossible."""
    s = xos_session
    exp = svc.create_experiment(s, key="two-arm-shape", origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", risk=pkg.RISK_ENVELOPE, control_required=False,
        control_exemption_reason="shape fixture", now=V1_FROZE)
    svc.add_arm(s, ver, arm_key="mmsell9", role="secondary", strategy_tag="t9")
    svc.add_arm(s, ver, arm_key="mmsell10", role="secondary", strategy_tag="t10")
    svc.freeze_version(s, ver, now=V1_FROZE)
    svc.open_epoch(s, ver, reason="paper", started_at=V1_FROZE)
    gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion",
        from_state="PAPER", to_state="LIVE_CANARY",
        spec={"pass_all": [{"metric": "pnl_cents_per_trade", "arm": "*",
                            "op": ">", "value": 0}]})
    svc.mark_gate_evidence_started(s, gate, at=V1_FROZE)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    s.commit()

    with pytest.raises(svc.ExperimentOsError, match="must equal the declared arm set"):
        svc.arm_live_canary(
            s, exp, gate=gate, approved_by="operator",
            live_key="x-live", twin_key="x-twin",
            live_tags={"mmsell10": pkg.LIVE_TAG},
            twin_tags={"mmsell10": pkg.TWIN_TAG},
            started_at=ARMED_AT,
        )


# ===========================================================================
# 2. The successor registration
# ===========================================================================


def test_registration_produces_a_single_arm_contract_with_the_envelope(
    price_ceiling, xos_session
):
    s = xos_session
    out = _register(s)
    ver = out["version"]
    assert ver.version == 2
    assert ver.frozen_at is not None
    assert {a.arm_key for a in read.arms_for(s, ver)} == {"mmsell10"}
    assert ver.risk_json == pkg.RISK_ENVELOPE
    assert ver.change_reason and "risk envelope" in ver.change_reason


def test_the_arm_is_carried_across_verbatim(price_ceiling, xos_session):
    """The parameters, not a re-derivation of them. lo/hi/maxyes must match v1's
    declared arm exactly, or this is a different experiment wearing the same
    name."""
    s = xos_session
    _exp, v1, _epoch, _gate = price_ceiling
    v1_params = {a.arm_key: a.params_json for a in read.arms_for(s, v1)}["mmsell10"]
    out = _register(s)
    v2_params = {a.arm_key: a.params_json
                 for a in read.arms_for(s, out["version"])}["mmsell10"]
    assert v2_params == v1_params == {"lo": 5, "hi": 10, "maxyes": 7}
    assert "no crypto exclusion" in (out["version"].universe_selector or "").lower() \
        or "No crypto exclusion" in (out["version"].universe_selector or "")


def test_the_promotion_bar_is_v1s_bar_unfloored(price_ceiling, xos_session):
    """v1's decision content, carried across verbatim and UNFLOORED (n = 0).

    A 300-trade floor was proposed and declined on 2026-08-28. Registering one
    would have been this session's pre-registration choice rather than a
    restatement of the operator's, and v1's registered contract carries no
    explicit n — the legacy manifest says so and deliberately invented none."""
    s = xos_session
    _exp, _v1, _epoch, g1 = price_ceiling
    out = _register(s)
    spec = out["promotion_gate"].spec_json
    assert spec["pass_all"] == g1.spec_json["pass_all"]
    assert "sample" not in spec


def test_an_unfloored_gate_still_refuses_to_pass_on_no_evidence(
    price_ceiling, xos_session
):
    """The load-bearing property of n = 0: no floor is not the same as no bar.

    With zero settled rows the fill-model projection is MISSING, and missing is
    BLOCKED_DATA — never a zero that a `> 0` clause could be argued past. So an
    unfloored gate cannot promote a book that has not traded; what it CAN do is
    promote one that has traded thinly, which is the risk the operator accepted
    and which `PROMOTION_GATE_SPEC` says to read coverage against at arming."""
    s = xos_session
    out = _register(s)
    s.commit()
    outcome = evaluator.evaluate_gate(s, out["promotion_gate"])
    assert outcome.verdict != "PASS"
    with pytest.raises(svc.ExperimentOsError, match="not PASS"):
        pkg.arm(s, approved_by="operator", started_at=ARMED_AT + timedelta(hours=1))
    s.rollback()
    assert read.get_experiment(s, pkg.EXPERIMENT_KEY).state == "PAPER"


def test_a_floor_can_still_be_registered_if_that_judgement_changes(
    price_ceiling, xos_session
):
    """The floor is a parameter, not a deletion — but adding one later is a new
    Version, because a registered gate's spec is immutable."""
    s = xos_session
    out = _register(s, promotion_sample_floor=300)
    assert out["promotion_gate"].spec_json["sample"]["mmsell10"]["value"] == 300


def test_the_keep_gate_is_registered_before_arming(price_ceiling, xos_session):
    """Pre-registration is the point: after arming, no threshold in it can be
    chosen or moved, because the flush guard freezes a gate's spec at
    registration and refuses any edit once evidence starts."""
    s = xos_session
    out = _register(s)
    keep = out["keep_gate"]
    assert keep.kind == "kill"
    assert keep.spec_hash
    keep.spec_json = {"pass_all": [{"metric": "live_cents_per_contract",
                                    "op": ">", "value": -99}]}
    with pytest.raises(svc.ImmutableRecord):
        s.flush()
    s.rollback()


def test_the_mmsell10_tag_never_resolves_to_two_active_deployments(
    price_ceiling, xos_session
):
    """The failure this hand-over exists to avoid.

    An ambiguous tag is refused outright by the enforcement resolver, so leaving
    the v1 two-arm deployment active while registering a v2 deployment on the
    same tag would have STOPPED the mmsell10 paper book — the opposite of what
    registering its successor is for."""
    s = xos_session
    _set_mode(s, "NEW_ONLY", cutover_id="prod-new-only-test")
    _register(s)

    active = s.execute(
        select(ExperimentDeploymentArm.strategy_tag)
        .join(ExperimentDeployment,
              ExperimentDeployment.id == ExperimentDeploymentArm.deployment_id)
        .where(ExperimentDeployment.ended_at.is_(None))
    ).scalars().all()
    assert active.count("mmsell10") == 1
    assert active.count("mmsell9") == 1

    enf.refresh(s)
    assert enf.tag_admissible(s, "mmsell10") is True
    assert enf.tag_admissible(s, "mmsell9") is True


def test_v1s_evidence_still_resolves_after_the_handover(price_ceiling, xos_session):
    """Ending a deployment must not orphan the evidence it gathered. Metric
    scopes resolve tags over every deployment in the epoch, ended or not; only
    the enforcement resolver looks at `ended_at`."""
    s = xos_session
    _exp, v1, v1_epoch, _gate = price_ceiling
    _paper_trades(s, "mmsell9", 5, at=V1_FROZE + timedelta(hours=1))
    _register(s)

    scope = evaluator._arm_scope(  # noqa: SLF001 — the real resolution path
        s, read.get_experiment(s, pkg.EXPERIMENT_KEY), v1, v1_epoch, "mmsell9",
        "paper", (V1_FROZE, ARMED_AT + timedelta(days=1)), "f" * 64,
    )
    assert scope.strategy_tags == ("mmsell9",)
    assert compute_metric(s, "settled_trades", scope).value == 5


def test_registration_is_refused_twice(price_ceiling, xos_session):
    s = xos_session
    _register(s)
    with pytest.raises(svc.ExperimentOsError, match="already been registered"):
        _register(s)


# ===========================================================================
# 3. The armed pair
# ===========================================================================


@pytest.fixture
def armed(price_ceiling, xos_session):
    """Registered, given fresh v2 paper evidence, then armed through the
    sanctioned path.

    The rows exist so the fill-model projection RESOLVES, not to clear a floor —
    the gate has none. Their count is deliberately modest, because that is what
    an unfloored gate actually looks like at arming time."""
    s = xos_session
    out = _register(s)
    _paper_trades(s, "mmsell10", 40)
    s.commit()
    res = pkg.arm(s, approved_by="operator", started_at=ARMED_AT + timedelta(hours=1))
    s.commit()
    return out, res


#: `LIVE_PAPER_TWIN_SUFFIX` as production carries it, read 2026-08-28. The twin
#: tag is DERIVED from it, so this value is part of the canary's contract.
PRODUCTION_TWIN_SUFFIX = "_pt3"


def test_the_twin_tag_matches_what_the_runtime_derives(settings):
    """The registered twin tag must be the tag the twin book actually trades on.

    They are produced by different mechanisms: Experiment OS records whatever
    `arm_live_canary` was handed, while the runtime derives
    `<live_tag><LIVE_PAPER_TWIN_SUFFIX>`. Production carries `_pt3`, so a
    registered `Cmmsell10_pt` would have meant the twin wrote rows under
    `Cmmsell10_pt3` — a tag resolving to no active deployment arm, refused at the
    write path under NEW_ONLY. The canary would have armed with a twin that could
    not record anything, and the parity comparison it exists for would be empty.

    Pinned here rather than trusted, because the two sides are only equal by
    agreement and nothing else would notice them diverging."""
    settings.live_strategies = pkg.LIVE_TAG
    settings.live_paper_twin_suffix = PRODUCTION_TWIN_SUFFIX
    settings.live_paper_twins = ""          # derived, not explicitly configured
    assert settings.live_paper_twin_pairs == [(pkg.LIVE_TAG, pkg.TWIN_TAG)]
    # ...and the envelope pins the suffix it was derived under, so a change to
    # that variable shows up as config drift rather than as a silent rename.
    assert pkg.RISK_ENVELOPE["settings"]["LIVE_PAPER_TWIN_SUFFIX"] == \
        PRODUCTION_TWIN_SUFFIX


def test_the_twin_tag_is_not_allowed_to_place_real_orders(settings):
    """Belt and braces on a prefix-matched allowlist: `Cmmsell10_pt3` starts with
    `Cmmsell10`, so an allowlist entry naming the live tag matches it too. The
    executor rejects every configured twin tag outright rather than relying on
    the prefix."""
    from kalshi_bot.live.executor import LiveExecutor

    settings.live_strategies = pkg.LIVE_TAG
    settings.live_paper_twin_suffix = PRODUCTION_TWIN_SUFFIX
    ex = LiveExecutor(client=None, settings=settings, risk=None)
    assert ex._allowed(pkg.LIVE_TAG) is True
    assert ex._allowed(pkg.TWIN_TAG) is False


def test_the_live_clip_is_a_book_param_not_a_process_wide_setting(settings):
    """`size=1` caps this book's live clip, and because it lives in
    `mmsell_variants` it is inside the config-drift material — so raising the
    clip later is DETECTED. `MAX_ORDER_SIZE` would not be: the drift detector
    does not watch it, and it would cap every book sharing the process."""
    assert "size=1" in pkg.BOOK_PARAMS
    assert "MAX_ORDER_SIZE" not in pkg.RISK_ENVELOPE["settings"]
    assert "MAX_ORDER_SIZE" in pkg.RISK_ENVELOPE["left_alone"]

    settings.mmsell_variants = f"{pkg.LIVE_TAG}:{pkg.BOOK_PARAMS}"
    book = {b["tag"]: b for b in settings.mmsell_variant_list}[pkg.LIVE_TAG]
    assert book["size"] == 1
    assert (book["lo"], book["hi"], book["maxyes"]) == (5.0, 10.0, 7.0)


def test_the_envelope_does_not_reach_outside_this_book(settings):
    """Two settings were removed after reading production, both because their
    blast radius is wider than this canary.

    `LIVE_EXIT_MODE` is `tp_sl` in production and is correct for the YES/weather
    books; mmsell holds to settlement structurally, so forcing `settlement` would
    change another family's exits and buy this canary nothing.
    `MAX_TOTAL_EXPOSURE` is portfolio-wide and is left where production has it."""
    for key in ("LIVE_EXIT_MODE", "MAX_TOTAL_EXPOSURE"):
        assert key not in pkg.RISK_ENVELOPE["settings"]
        assert key in pkg.RISK_ENVELOPE["left_alone"]
    assert "max_total_live_exposure_usd" not in pkg.RISK_ENVELOPE
    # The canary's own exposure ceiling is its book cap, and it is stated.
    assert pkg.RISK_ENVELOPE["max_book_exposure_usd"] == 19.80


def test_arming_produces_one_boundary_and_a_first_class_twin_link(armed, xos_session):
    _out, res = armed
    live, twin, epoch = res["live"], res["twin"], res["epoch"]
    assert twin.twin_of_deployment_id == live.id
    assert twin.started_at == live.started_at == epoch.started_at
    assert live.kind == "live" and twin.kind == "paper_twin"
    assert twin.epoch_id == live.epoch_id
    assert epoch.impact_class == "I2"
    assert live.grandfathered is False and twin.grandfathered is False


def test_the_armed_tags_are_fresh_and_unused(armed, xos_session):
    s = xos_session
    out, res = armed
    tags = {t for _a, t in read.deployment_arms(s, res["live"])} | {
        t for _a, t in read.deployment_arms(s, res["twin"])}
    assert tags == {pkg.LIVE_TAG, pkg.TWIN_TAG}
    # No historical tag is reused, and none of the retired twin generations
    # reappears.
    assert not tags & {"mmsell10", "mmsell10_pt", "mmsell10_pt3",
                       "Lmmsell10", "Lmmsell10_pt3", "mmsell9"}
    # No inherited evidence: the fresh tags carry no rows at all.
    for tag in tags:
        assert s.scalar(select(PaperTrade).where(PaperTrade.strategy == tag)) is None
        assert s.scalar(select(LiveOrder).where(LiveOrder.strategy == tag)) is None
    assert out["version"].version == 2


def test_arming_refuses_a_tag_that_carries_inherited_paper_state(
    price_ceiling, xos_session, monkeypatch
):
    """The 2026-08-15 failure, checked against THIS package's tags: mmsell10 went
    live on a tag holding 87 open paper positions and never placed one order."""
    s = xos_session
    _register(s)
    _paper_trades(s, "mmsell10", 40)
    s.add(PaperTrade(market_ticker="OLD", strategy=pkg.LIVE_TAG, status="open",
                     created_at=ARMED_AT))
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="inherited paper state"):
        pkg.arm(s, approved_by="operator", started_at=ARMED_AT + timedelta(hours=1))
    assert read.get_experiment(s, pkg.EXPERIMENT_KEY).state == "PAPER"


def test_arming_is_atomic_when_the_gate_does_not_pass(price_ceiling, xos_session):
    """A book whose projection has gone negative does not promote, and nothing is
    left half-armed: no live deployment, no state change, no transition.

    The entry price is what moves this metric, not the observed P&L: the
    projection is a property of the book's price MIX read through the live
    calibration. Entries at a 91c NO (a 9c yes-equivalent) sit in a trusted cell
    the live decomposition measured at -5.79c/trade — the "8-11c is net negative"
    finding the whole `maxyes=7` ceiling exists to avoid."""
    s = xos_session
    _register(s)
    _paper_trades(s, "mmsell10", 60, price=91, pnl=0.05)
    s.commit()
    with pytest.raises(svc.ExperimentOsError, match="not PASS"):
        pkg.arm(s, approved_by="operator", started_at=ARMED_AT + timedelta(hours=1))
    s.rollback()
    assert read.get_experiment(s, pkg.EXPERIMENT_KEY).state == "PAPER"
    assert s.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == pkg.LIVE_DEPLOYMENT_KEY)
    ) is None


def test_no_open_positions_or_orders_are_inherited(armed, xos_session):
    """A fresh canary starts flat. Exposure is measured on the live tag, and it
    is zero at the boundary — which is also what makes the loss budget mean
    what it says."""
    s = xos_session
    _out, res = armed
    scope = _live_scope(s, res)
    assert compute_metric(s, "live_open_exposure_usd", scope).value == 0.0
    assert compute_metric(s, "live_settled_contracts", scope).value == 0.0


# ---------------------------------------------------------------------------
# The measurement contract: every metric resolves to the deployment it names
# ---------------------------------------------------------------------------


def _live_scope(s, res, *, kind="live"):
    exp = read.get_experiment(s, pkg.EXPERIMENT_KEY)
    ver = read.latest_version(s, exp)
    epoch = res["epoch"]
    return evaluator._arm_scope(  # noqa: SLF001
        s, exp, ver, epoch, pkg.ARM_KEY, kind,
        (epoch.started_at, epoch.started_at + timedelta(days=30)), "f" * 64,
    )


KEEP_METRICS = (
    "live_settled_contracts", "live_cents_per_contract", "live_realized_pnl_usd",
    "live_fill_rate_pct", "live_open_exposure_usd", "live_max_realized_loss_usd",
    "live_tail_loss_markets", "live_blocked_entries", "twin_live_winrate_gap_pp",
    "twin_live_gap_cents", "twin_live_paired_gap_cents", "twin_mirror_coverage_pct",
)


def test_every_live_keep_metric_addresses_the_live_deployment(armed, xos_session):
    """The defect this canary exists not to repeat: `mmsell-scheduled-settle-live`
    is BLOCKED_DATA because its clauses default to `paper` on an epoch that holds
    only live and paper_twin. Here the live scope resolves to the LIVE tag, and
    the twin scope to the TWIN tag, with no overlap."""
    s = xos_session
    _out, res = armed
    live = _live_scope(s, res)
    twin = _live_scope(s, res, kind="paper_twin")
    assert live.strategy_tags == (pkg.LIVE_TAG,)
    assert twin.strategy_tags == (pkg.TWIN_TAG,)
    assert live.deployment_keys == (pkg.LIVE_DEPLOYMENT_KEY,)
    assert twin.deployment_keys == (pkg.TWIN_DEPLOYMENT_KEY,)

    for key in KEEP_METRICS:
        mv = compute_metric(s, key, live)
        assert mv.missing is False, f"{key} unresolvable on the live scope: {mv.reason}"
        assert mv.provenance.get("addressing_error") is None


def test_paper_twin_economics_address_the_twin_not_the_live_book(armed, xos_session):
    """The twin's own realized rate — the other half of the delta — is a universal
    metric addressed at kind='paper_twin', and it must read the twin's tag."""
    s = xos_session
    _out, res = armed
    twin = _live_scope(s, res, kind="paper_twin")
    for i in range(4):
        s.add(PaperTrade(market_ticker=f"TW-{i}", strategy=pkg.TWIN_TAG,
                         status="settled", pnl=0.03, quantity=1,
                         created_at=res["epoch"].started_at + timedelta(hours=1)))
    s.commit()
    mv = compute_metric(s, "pnl_cents_per_contract", twin)
    assert mv.provenance["strategy_tags"] == [pkg.TWIN_TAG]
    assert mv.value == pytest.approx(3.0)


def test_no_live_metric_can_be_answered_from_a_paper_deployment(armed, xos_session):
    """Missing stays missing. A `>=` floor must never read a paper number as if it
    were a live one."""
    s = xos_session
    _out, res = armed
    paper = _live_scope(s, res, kind="paper")
    for key in ("live_settled_contracts", "live_cents_per_contract",
                "live_realized_pnl_usd", "live_fill_rate_pct",
                "live_open_exposure_usd", "live_blocked_entries"):
        mv = compute_metric(s, key, paper)
        assert mv.missing is True and mv.value is None, key


def test_the_keep_gate_resolves_rather_than_blocking_on_addressing(armed, xos_session):
    """The one thing that must be true before arming: the registered evaluator
    can actually render a verdict on this pair. A canary whose keep gate is
    BLOCKED_DATA from its first cycle cannot be judged at all."""
    s = xos_session
    out, res = armed
    _settled_live(s, res, realized=0.05)
    _twin_row(s, res, pnl=0.05)
    s.commit()
    outcome = evaluator.evaluate_gate(s, out["keep_gate"])
    assert outcome.verdict != "BLOCKED_DATA", outcome.explanation
    assert outcome.verdict != "BLOCKED_INTEGRITY", outcome.explanation
    # Thin evidence is HOLD — "keep running inside the envelope", not a stop.
    assert outcome.verdict == "HOLD"


def test_the_keep_gate_stops_on_the_preregistered_loss_budget(armed, xos_session):
    """Category 3: strategy loss. The early-safety floor (20 settled contracts) is
    deliberately far below the 150-contract promotion floor, so a book losing its
    whole budget is stopped rather than left at HOLD while real money trades."""
    s = xos_session
    out, res = armed
    for _ in range(24):
        _settled_live(s, res, realized=-0.95)
    _twin_row(s, res, pnl=-0.95)
    s.commit()
    outcome = evaluator.evaluate_gate(s, out["keep_gate"])
    assert outcome.verdict == "FAIL"
    assert "live_realized_pnl_usd" in outcome.explanation


def test_the_keep_gate_stops_on_a_matched_market_accounting_gap(armed, xos_session):
    """Category 2: our own arithmetic is wrong. A gap on markets BOTH sides
    settled cannot be fill rate or adverse selection — we got the trade — so it
    invalidates paper gates on every book, not just this one."""
    s = xos_session
    out, res = armed
    for i in range(32):
        t = _settled_live(s, res, realized=0.00, ticker=f"M-{i}")
        _twin_row(s, res, pnl=0.05, ticker=t)     # twin claims 5c the live book never saw
    s.commit()
    outcome = evaluator.evaluate_gate(s, out["keep_gate"])
    assert outcome.verdict == "FAIL"
    assert "twin_live_paired_gap_cents" in outcome.explanation


def test_a_single_market_losing_more_than_one_clip_is_a_stand_down(armed, xos_session):
    """A structural clause, not an invented threshold: under a one-contract clip
    at 93-99c a settled market cannot lose more than ~$1. If one does, the
    envelope is not being applied."""
    s = xos_session
    out, res = armed
    _settled_live(s, res, realized=-4.00, contracts=1)
    _twin_row(s, res, pnl=-4.00)
    s.commit()
    outcome = evaluator.evaluate_gate(s, out["keep_gate"])
    assert outcome.verdict == "FAIL"
    assert "live_max_realized_loss_usd" in outcome.explanation


# ---------------------------------------------------------------------------
# Parameter drift, on either side
# ---------------------------------------------------------------------------


def test_a_parameter_change_on_the_live_book_is_detected(armed, xos_session, settings):
    """Drift is not absorbed: it records an integrity event, and the evaluator
    then refuses to render any verdict over the drifted deployment. So a retuned
    canary cannot quietly keep accumulating evidence against its own contract."""
    s = xos_session
    out, res = armed
    _armed_runtime(settings)
    settings.mmsell_variants = f"{pkg.LIVE_TAG}:lo=5,hi=10,maxyes=9,size=1"  # 7 -> 9
    findings = enf.runtime_config_check(s, settings)
    s.commit()

    assert any(f["deployment"] == pkg.LIVE_DEPLOYMENT_KEY for f in findings), findings
    ev = s.scalar(select(ExperimentIntegrityEvent).where(
        ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT",
        ExperimentIntegrityEvent.resolved_at.is_(None)))
    assert ev is not None
    assert evaluator.evaluate_gate(s, out["keep_gate"]).verdict == "BLOCKED_INTEGRITY"


def _armed_runtime(settings):
    """The runtime configuration this canary is registered against."""
    settings.bot_mode = "live"
    settings.live_strategies = pkg.LIVE_TAG
    settings.live_paper_twin_suffix = PRODUCTION_TWIN_SUFFIX
    settings.mmsell_variants = f"{pkg.LIVE_TAG}:{pkg.BOOK_PARAMS}"
    return settings


def test_the_matching_configuration_produces_no_drift(armed, xos_session, settings):
    """The other half — a detector that always fires is not a detector."""
    s = xos_session
    _out, _res = armed
    assert enf.runtime_config_check(s, _armed_runtime(settings)) == []


def test_a_changed_twin_suffix_is_detected_as_drift(armed, xos_session, settings):
    """The suffix is not cosmetic: it DERIVES the twin's tag, so changing it
    silently re-points the twin at a tag with no registered deployment arm. The
    material records the pairing rather than the suffix, which is why moving the
    suffix shows up here at all."""
    s = xos_session
    out, _res = armed
    _armed_runtime(settings)
    settings.live_paper_twin_suffix = "_pt4"
    findings = enf.runtime_config_check(s, settings)
    s.commit()
    assert any(f["deployment"] == pkg.LIVE_DEPLOYMENT_KEY for f in findings), findings
    assert evaluator.evaluate_gate(s, out["keep_gate"]).verdict == "BLOCKED_INTEGRITY"


def test_a_raised_live_clip_is_detected_as_drift(armed, xos_session, settings):
    """The reason the clip is a book param. `size=1` rides in `mmsell_variants`,
    so raising it lands in `book_params` and the gate stops rendering verdicts —
    where `MAX_ORDER_SIZE` would have been changed with nothing noticing."""
    s = xos_session
    out, _res = armed
    _armed_runtime(settings)
    settings.mmsell_variants = f"{pkg.LIVE_TAG}:lo=5,hi=10,maxyes=7,size=5"
    findings = enf.runtime_config_check(s, settings)
    s.commit()
    assert any(f["deployment"] == pkg.LIVE_DEPLOYMENT_KEY for f in findings), findings
    assert evaluator.evaluate_gate(s, out["keep_gate"]).verdict == "BLOCKED_INTEGRITY"


def test_the_twin_cannot_drift_independently_of_its_live_parent(armed, xos_session,
                                                                settings):
    """The twin book is built as `dict(parent)` with only the tag replaced, so it
    has no parameters of its own to drift. The registered material records BOTH
    tags anyway, so a future refactor that gave the twin its own spec would be
    caught rather than silently permitted."""
    s = xos_session
    _out, res = armed
    material = (res["live"].config_json or {})["material"]
    assert set(material["book_params"]) == {pkg.LIVE_TAG, pkg.TWIN_TAG}
    assert material["twin_pairs"] == {pkg.LIVE_TAG: pkg.TWIN_TAG}

    _armed_runtime(settings)
    # A spec appearing for the twin tag is itself the drift.
    settings.mmsell_variants = (f"{pkg.LIVE_TAG}:{pkg.BOOK_PARAMS};"
                                f"{pkg.TWIN_TAG}:{pkg.BOOK_PARAMS}")
    findings = enf.runtime_config_check(s, settings)
    assert findings, "an independently-specified twin must not pass unnoticed"


# ---------------------------------------------------------------------------
# Stand-down
# ---------------------------------------------------------------------------


def test_emptying_the_allowlist_stands_down_without_looking_like_drift(
    armed, xos_session, settings
):
    """Removing the runtime allowlist stops NEW entries. It is a recorded STATE,
    not a contamination: gate evaluation is explicitly not blocked, because the
    evidence gathered before the pause is unaffected."""
    s = xos_session
    out, res = armed
    _armed_runtime(settings)
    settings.live_strategies = ""
    enf.runtime_config_check(s, settings)
    s.commit()

    ev = s.scalar(select(ExperimentIntegrityEvent).where(
        ExperimentIntegrityEvent.kind == "EXPERIMENT_EXECUTION_STOOD_DOWN",
        ExperimentIntegrityEvent.deployment_id == res["live"].id))
    assert ev is not None and ev.severity == "info"
    assert s.scalar(select(ExperimentIntegrityEvent).where(
        ExperimentIntegrityEvent.kind == "EXPERIMENT_CONFIG_DRIFT",
        ExperimentIntegrityEvent.resolved_at.is_(None))) is None
    assert evaluator.evaluate_gate(s, out["keep_gate"]).verdict != "BLOCKED_INTEGRITY"


def test_a_stood_down_book_still_reports_its_held_exposure(armed, xos_session):
    """The number an operator most needs during a stand-down is the one a
    resting-order-only reading loses. Held positions are still real money and
    still exit and settle."""
    s = xos_session
    _out, res = armed
    t = _settled_live(s, res, realized=None, contracts=1, closed=False,
                      exposure=0.94)
    s.commit()
    assert t
    mv = compute_metric(s, "live_open_exposure_usd", _live_scope(s, res))
    assert mv.value == pytest.approx(0.94)
    assert mv.provenance["exposure"]["open_positions"] == 1


# ---------------------------------------------------------------------------
# helpers that write live/twin rows
# ---------------------------------------------------------------------------

_N = [0]


def _settled_live(s, res, *, realized, contracts=1, ticker=None, closed=True,
                  exposure=None):
    _N[0] += 1
    ticker = ticker or f"LV-{_N[0]}"
    oid = f"ko-{_N[0]}"
    at = res["epoch"].started_at + timedelta(hours=1)
    s.add(LiveOrder(kalshi_order_id=oid, market_ticker=ticker,
                    strategy=pkg.LIVE_TAG, action="buy", side="no",
                    quantity=contracts, limit_price=94, status="filled",
                    created_at=at))
    s.add(Fill(kalshi_fill_id=f"kf-{_N[0]}", kalshi_order_id=oid,
               market_ticker=ticker, action="buy", quantity=contracts, price=94,
               filled_at=at))
    s.add(Position(market_ticker=ticker, captured_at=at + timedelta(days=1),
                   quantity=0 if closed else contracts, avg_price=94,
                   market_exposure=exposure, realized_pnl=realized))
    s.add(LivePaperParityEvent(recorded_at=at, twin_tag=pkg.TWIN_TAG,
                               live_tag=pkg.LIVE_TAG, market_ticker=ticker,
                               twin_outcome="opened", live_outcome="placed"))
    return ticker


def _twin_row(s, res, *, pnl, ticker=None):
    _N[0] += 1
    s.add(PaperTrade(market_ticker=ticker or f"TWX-{_N[0]}", strategy=pkg.TWIN_TAG,
                     status="settled", pnl=pnl, quantity=1, side="no",
                     action="buy", assumed_price=94,
                     created_at=res["epoch"].started_at + timedelta(hours=1)))


def test_enforcement_state_is_unchanged_by_this_package(price_ceiling, xos_session):
    """Registering and arming are experiment acts. Neither touches the recorded
    enforcement mode, and neither is a substitute for the operator switch that
    actually lets an order reach Kalshi."""
    s = xos_session
    _set_mode(s, "NEW_ONLY", cutover_id="prod-new-only-test")
    before = s.scalar(select(ExperimentOsEnforcement.id)
                      .order_by(ExperimentOsEnforcement.id.desc()).limit(1))
    _register(s)
    _paper_trades(s, "mmsell10", 40)
    s.commit()
    pkg.arm(s, approved_by="operator", started_at=ARMED_AT + timedelta(hours=1))
    s.commit()
    after = s.scalar(select(ExperimentOsEnforcement.id)
                     .order_by(ExperimentOsEnforcement.id.desc()).limit(1))
    assert before == after
