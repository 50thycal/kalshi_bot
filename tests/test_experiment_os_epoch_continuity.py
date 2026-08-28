"""XOS-000011: a closed epoch must not silently strand the books running in it.

On 2026-08-24 a MARKET_TAXONOMY I2 boundary closed `mmsell-type-tight` v1/e1 and
opened v1/e2. The cut left `tmmsell-paper-legacy-1` open on the CLOSED epoch and
registered nothing on the new one, so `Tmmsell1/2/5/6` resolved to no active
deployment arm. Under NEW_ONLY every entry they attempted was refused — and the
refusal propagated out of `MmSellTracker.run_once` into the caller's single
`session_scope`, which rolled the whole transaction back and discarded every OTHER
mmsell book's entries too. Sixteen books went dark for four days, and the only
symptom was one ERROR line naming a book nobody was watching.

Three things had to be true at once, and each gets its own proof here:

  1. `close_epoch` could leave deployments open on a closed epoch — a row saying a
     book was running that the resolver read as unregistered.
  2. an epoch cut opened an EMPTY successor, so even a correctly ended predecessor
     left the tags with nowhere to live.
  3. one book's refusal could cost every other book its cycle.

The canary path had the same shape latent in it: `arm_live_canary` closes the paper
epoch, so with (1) fixed it would have ended the paper parent's deployment and taken
out the very book the canary was promoted from.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot import db
from kalshi_bot.experiment_os import enforcement as enf
from kalshi_bot.experiment_os import evaluator
from kalshi_bot.experiment_os import repair_tmmsell_epoch as repair
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os.metrics import compute_metric
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)
BOUNDARY = datetime(2026, 8, 24, 14, 21, 17, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_resolver():
    enf.reset_for_tests()
    yield
    enf.reset_for_tests()


def _settled_trade(s, tag, *, pnl=0.05, at=None):
    s.add(PaperTrade(
        market_ticker=f"T-{tag}-{id(object())}", strategy=tag, status="settled",
        pnl=pnl, quantity=1, created_at=at or (T0 + timedelta(hours=1)),
    ))


def _new_only(s):
    enf.record_enforcement_change(
        s, mode="NEW_ONLY", actor="operator", reason="continuity tests",
        cutover_id="test-new-only", readiness={"ok": True, "checks": {}},
    )


def _admissible(s, tag: str) -> bool:
    """Ask the resolver the way the worker does: refresh once, then decide."""
    enf.reset_for_tests()
    enf.refresh(s)
    return enf.tag_admissible(s, tag)


def _paper_experiment(s, key="tmmsell-like", tags=("t1", "t2")):
    """A PAPER experiment with one deployment carrying several tags."""
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0)
    for i, tag in enumerate(tags):
        svc.add_arm(s, ver, arm_key=f"arm{i}",
                    role="treatment" if i == 0 else "control", strategy_tag=tag)
    if len(tags) == 1:
        ver.control_exemption_reason = "single-book continuity fixture"
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    dep = svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={f"arm{i}": tag for i, tag in enumerate(tags)}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    return exp, ver, epoch, dep


# ---------------------------------------------------------------------------
# 1. Closing an epoch ends the deployments in it
# ---------------------------------------------------------------------------


def test_closing_an_epoch_ends_the_deployments_running_in_it(xos_session, xos_platform):
    s = xos_session
    _, _, epoch, dep = _paper_experiment(s)
    assert dep.ended_at is None
    svc.close_epoch(s, epoch, ended_at=BOUNDARY)
    assert dep.ended_at.replace(tzinfo=UTC) == BOUNDARY, (
        "a deployment left open on a closed epoch is a row claiming a book is "
        "running that the resolver reads as unregistered"
    )


def test_a_deployment_stranded_on_a_closed_epoch_cannot_trade(xos_session, xos_platform):
    """The exact production state, and why it was invisible.

    The deployment row says ACTIVE. The resolver requires the EPOCH to be open too,
    so the tag resolves to nothing and NEW_ONLY refuses it — with no integrity event,
    no drift record and no gate verdict, because as far as the system is concerned
    the tag was simply never registered. Nothing in the portfolio view goes red.
    """
    s = xos_session
    _new_only(s)
    _, _, epoch, dep = _paper_experiment(s, tags=("t1",))
    s.commit()
    assert _admissible(s, "t1")

    # Reproduce the pre-fix shape by hand: close the epoch, leave the deployment.
    epoch.ended_at = BOUNDARY
    s.flush()
    s.commit()
    assert dep.ended_at is None                    # the row still says "running"
    assert not _admissible(s, "t1")                # the resolver disagrees
    enf.refresh(s)
    with pytest.raises(enf.LineageBlocked):
        enf.stamp_or_block(s, "t1", channel="paper")


def test_ending_an_epochs_deployments_keeps_their_evidence_addressable(
    xos_session, xos_platform
):
    """The cascade closes the operating interval; it does not retract the record.

    Metric scopes resolve tags across every deployment in an epoch, ended or not —
    only the enforcement resolver reads `ended_at`. If ending the deployment also
    hid its evidence the fix would be worse than the defect.
    """
    s = xos_session
    exp, ver, epoch, _dep = _paper_experiment(s, tags=("t1",))
    for _ in range(5):
        _settled_trade(s, "t1", at=T0 + timedelta(hours=1))
    s.commit()
    svc.close_epoch(s, epoch, ended_at=BOUNDARY)
    s.commit()

    assert svc.open_deployments(s, epoch) == []
    scope = evaluator._arm_scope(  # noqa: SLF001 — the real resolution path
        s, exp, ver, epoch, "arm0", "paper", (T0, BOUNDARY), "f" * 64,
    )
    assert scope.strategy_tags == ("t1",)
    assert compute_metric(s, "settled_trades", scope).value == 5


# ---------------------------------------------------------------------------
# 2. A new epoch is not an empty one
# ---------------------------------------------------------------------------


def test_carrying_forward_keeps_every_tag_admissible_across_the_boundary(
    xos_session, xos_platform
):
    s = xos_session
    _new_only(s)
    _exp, ver, epoch, dep = _paper_experiment(s, tags=("t1", "t2"))
    s.commit()

    carried_from = svc.open_deployments(s, epoch)
    svc.close_epoch(s, epoch, ended_at=BOUNDARY)
    new_epoch = svc.open_epoch(s, ver, reason="I2 boundary", started_at=BOUNDARY)
    svc.carry_deployments_forward(
        s, carried_from, new_epoch, started_at=BOUNDARY, reason="taxonomy boundary")
    s.commit()

    assert _admissible(s, "t1") and _admissible(s, "t2")
    carried = svc.open_deployments(s, new_epoch)
    assert [d.deployment_key for d in carried] == [f"{dep.deployment_key}-e2"]
    assert carried[0].started_at.replace(tzinfo=UTC) == BOUNDARY
    assert carried[0].kind == dep.kind and carried[0].stage == dep.stage


def test_each_tag_resolves_to_exactly_one_active_arm_after_a_carry_forward(
    xos_session, xos_platform
):
    """The failure mode a naive fix would introduce.

    Leaving the predecessor open alongside its successor puts the tag on TWO active
    deployment arms, which the resolver refuses as ambiguous — a different way to
    stop the same books. The predecessor must be ENDED, not merely superseded.
    """
    s = xos_session
    _new_only(s)
    _exp, ver, epoch, _dep = _paper_experiment(s, tags=("t1",))
    s.commit()
    carried_from = svc.open_deployments(s, epoch)
    svc.close_epoch(s, epoch, ended_at=BOUNDARY)
    new_epoch = svc.open_epoch(s, ver, reason="I2", started_at=BOUNDARY)
    svc.carry_deployments_forward(
        s, carried_from, new_epoch, started_at=BOUNDARY, reason="boundary")
    s.commit()

    enf.reset_for_tests()
    enf.refresh(s)
    assert enf.stamp_or_block(s, "t1", channel="paper") is not None


def test_evidence_is_not_pooled_across_the_boundary(xos_session, xos_platform):
    """Carrying the book forward must not carry its evidence forward.

    The whole reason an I2 boundary is cut is that the old sample was gathered in a
    different world. A carry-forward that let the new epoch see pre-boundary trades
    would silently undo the boundary it was created to honour.
    """
    s = xos_session
    exp, ver, epoch, _dep = _paper_experiment(s, tags=("t1",))
    for _ in range(3):
        _settled_trade(s, "t1", at=T0 + timedelta(hours=1))          # pre-boundary
    s.commit()
    carried_from = svc.open_deployments(s, epoch)
    svc.close_epoch(s, epoch, ended_at=BOUNDARY)
    new_epoch = svc.open_epoch(s, ver, reason="I2", started_at=BOUNDARY)
    svc.carry_deployments_forward(
        s, carried_from, new_epoch, started_at=BOUNDARY, reason="boundary")
    for _ in range(7):
        _settled_trade(s, "t1", at=BOUNDARY + timedelta(hours=1))    # post-boundary
    s.commit()

    after = evaluator._arm_scope(  # noqa: SLF001
        s, exp, ver, new_epoch, "arm0", "paper",
        (BOUNDARY, BOUNDARY + timedelta(days=1)), "f" * 64,
    )
    assert compute_metric(s, "settled_trades", after).value == 7


def test_a_live_deployment_is_never_carried_forward_automatically(
    xos_session, xos_platform
):
    """Real-money lineage is `arm_live_canary`'s to create, and nothing else's.

    A carry-forward can prove none of what that path proves — fresh tags, a twin at
    the same instant, a re-evaluated promotion gate — so it refuses by name rather
    than minting live lineage on a platform boundary.
    """
    s = xos_session
    _exp, ver, epoch, _dep = _paper_experiment(s, tags=("t1",))
    live = svc.register_deployment(
        s, epoch, deployment_key="live-1", stage="LIVE_CANARY", kind="live",
        arms={"arm0": "Lt1"}, started_at=T0, grandfathered=True,
    )
    s.commit()
    new_epoch_src = svc.open_deployments(s, epoch)
    assert live in new_epoch_src
    svc.close_epoch(s, epoch, ended_at=BOUNDARY)
    new_epoch = svc.open_epoch(s, ver, reason="I2", started_at=BOUNDARY)
    with pytest.raises(svc.ExperimentOsError, match="live-1"):
        svc.carry_deployments_forward(
            s, new_epoch_src, new_epoch, started_at=BOUNDARY, reason="boundary")


# ---------------------------------------------------------------------------
# 3. One book's refusal must not cost every other book its cycle
# ---------------------------------------------------------------------------

_GOOD = "mmsellok:lo=5,hi=95"
_BLOCKED = "mmsellbad:lo=5,hi=95"


def _mkt(ticker, sub, bid_c, ask_c, vol=500, hours=48):
    close = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
    return {"ticker": ticker, "yes_sub_title": sub, "close_time": close,
            "volume_fp": f"{vol}.0",
            "yes_bid_dollars": f"{bid_c / 100:.4f}",
            "yes_ask_dollars": f"{ask_c / 100:.4f}"}


def _ob(bid_c, ask_c):
    return {"orderbook_fp": {
        "yes_dollars": [[f"{bid_c / 100:.4f}", "300"]],
        "no_dollars": [[f"{(100 - ask_c) / 100:.4f}", "300"]]}}


class _FakeClient:
    def __init__(self, events, books):
        self._events, self._books = events, books

    def get_exchange_status(self):
        return {"exchange_active": True, "trading_active": True}

    def get_events(self, status="open", with_nested_markets=True, limit=200,
                   cursor=None):
        return {"events": self._events, "cursor": ""}

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _register_book(s, key, tag):
    """One PAPER experiment per book, so each tag's lineage is independent."""
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="l", now=T0)
    svc.add_arm(s, ver, arm_key="a0", role="treatment", strategy_tag=tag)
    ver.control_exemption_reason = "single-book continuity fixture"
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="i", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-p-1", stage="PAPER", kind="paper",
        arms={"a0": tag}, started_at=T0)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    return epoch


def test_one_lineage_blocked_book_does_not_cost_the_others_their_cycle(settings):
    """The blast radius, asserted through the real entry path.

    `good` is registered; `blocked` is not. Before the fix `blocked`'s refusal
    escaped `run_once`, and the caller's `session_scope` rolled back `good`'s entry
    with it — every book in the process losing its cycle to one book's problem.
    """
    settings.bot_mode = "mmsell"
    db.init_engine(settings.database_url)
    db.create_all()
    settings.mmsell_variants = f"{_GOOD};{_BLOCKED}"
    settings.live_strategies = ""
    settings.mmsell_quote_parity = False
    settings.mmsell_capture_candidates = False
    settings.mmsell_prefilter_enabled = False

    with db.session_scope() as s:
        from kalshi_bot.experiment_os.models import STANDARD_PLATFORM_COMPONENTS

        svc.ensure_standard_components(s)
        for key in STANDARD_PLATFORM_COMPONENTS:
            svc.register_platform_revision(s, key, version="v1", activate=True)
        _new_only(s)
        _register_book(s, "good-exp", "mmsellok")
        # NB: the control book's own tag must resolve too, or it is skipped as well.
        _register_book(s, "control-exp", MmSellTracker.STRATEGY)
    enf.reset_for_tests()

    ev = {"event_ticker": "KXTEAM-26", "series_ticker": "KXTEAM",
          "markets": [_mkt("KXTEAM-26-A", "A", 5, 9)]}
    client = _FakeClient([ev], {"KXTEAM-26-A": _ob(5, 9)})
    with db.session_scope() as s:
        enf.refresh(s)
        summ = MmSellTracker(client, settings).run_once(s)

    assert "mmsellbad" in summ.blocked_books
    assert summ.per_book.get("mmsellok", 0) >= 1, (
        "the registered book must keep its entry — a neighbouring book's lineage "
        "problem is not its problem"
    )
    with db.session_scope() as s:
        kept = s.query(PaperTrade).filter(PaperTrade.strategy == "mmsellok").count()
        none = s.query(PaperTrade).filter(PaperTrade.strategy == "mmsellbad").count()
    assert kept >= 1, "the cycle was committed, not rolled back"
    assert none == 0, "the blocked book is still blocked — this is not a bypass"


# ---------------------------------------------------------------------------
# 4. The one-shot repair for the rows that broke before the fix existed
# ---------------------------------------------------------------------------


def _broken_tmmsell(s):
    """`mmsell-type-tight` exactly as production holds it: e1 CLOSED at the taxonomy
    boundary with `tmmsell-paper-legacy-1` still open on it, and e2 open and EMPTY."""
    exp = svc.create_experiment(s, key=repair.EXPERIMENT_KEY, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="market-type tight bands", independent_variable="type",
        now=T0)
    for tag in repair.EXPECTED_TAGS:
        svc.add_arm(s, ver, arm_key=tag, role="treatment", strategy_tag=tag)
    ver.control_exemption_reason = "type books read against the shared control"
    svc.freeze_version(s, ver, now=T0)
    e1 = svc.open_epoch(s, ver, reason="migration epoch", started_at=T0)
    dep = svc.register_deployment(
        s, e1, deployment_key=repair.STRANDED_DEPLOYMENT_KEY, stage="PAPER",
        kind="paper", arms={t: t for t in repair.EXPECTED_TAGS}, started_at=T0)
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    # The pre-fix cut, by hand: epoch closed, deployment left open, successor empty.
    e1.ended_at = repair.BOUNDARY
    s.flush()
    e2 = svc.open_epoch(s, ver, reason="platform boundary", started_at=repair.BOUNDARY,
                        impact_class="I2")
    s.commit()
    return exp, ver, e1, e2, dep


def test_the_repair_restores_every_blocked_book(xos_session, xos_platform):
    s = xos_session
    _new_only(s)
    _exp, _ver, e1, e2, dep = _broken_tmmsell(s)
    for tag in repair.EXPECTED_TAGS:
        assert not _admissible(s, tag), f"{tag} should start blocked"

    out = repair.repair(s, actor="claude-code")
    s.commit()

    assert out["already_repaired"] is False
    assert out["tags_restored"] == list(repair.EXPECTED_TAGS)
    assert out["registered"] == [repair.SUCCESSOR_DEPLOYMENT_KEY]
    assert dep.ended_at.replace(tzinfo=UTC) == repair.BOUNDARY
    assert [d.deployment_key for d in svc.open_deployments(s, e2)] == [
        repair.SUCCESSOR_DEPLOYMENT_KEY]
    assert svc.open_deployments(s, e1) == []
    for tag in repair.EXPECTED_TAGS:
        assert _admissible(s, tag), f"{tag} is still blocked after the repair"


def test_the_repair_is_idempotent(xos_session, xos_platform):
    """A second run must not put each tag on two active arms — which the resolver
    refuses as ambiguous, stopping the books a second way."""
    s = xos_session
    _new_only(s)
    _broken_tmmsell(s)
    repair.repair(s, actor="claude-code")
    s.commit()

    again = repair.repair(s, actor="claude-code")
    s.commit()
    assert again["already_repaired"] is True
    for tag in repair.EXPECTED_TAGS:
        assert _admissible(s, tag)


def test_the_repair_refuses_a_shape_it_was_not_reviewed_against(
    xos_session, xos_platform
):
    """A repair that half-applies to a state it does not recognise is worse than no
    repair, so every precondition is checked rather than assumed."""
    s = xos_session
    exp = svc.create_experiment(s, key=repair.EXPERIMENT_KEY, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="type", now=T0)
    svc.add_arm(s, ver, arm_key="Tmmsell1", role="treatment", strategy_tag="Tmmsell1")
    ver.control_exemption_reason = "fixture"
    svc.freeze_version(s, ver, now=T0)
    e1 = svc.open_epoch(s, ver, reason="i", started_at=T0)
    svc.register_deployment(
        s, e1, deployment_key=repair.STRANDED_DEPLOYMENT_KEY, stage="PAPER",
        kind="paper", arms={"Tmmsell1": "Tmmsell1"}, started_at=T0)
    s.commit()
    # Only one of the four tags: not the deployment this repair was written for.
    with pytest.raises(svc.ExperimentOsError, match="does not match"):
        repair.repair(s, actor="claude-code")


def test_the_repair_leaves_lifecycle_state_untouched(xos_session, xos_platform):
    """It fixes rows. It is not a promotion, a verdict or a transition."""
    s = xos_session
    _new_only(s)
    exp, ver, _e1, _e2, _dep = _broken_tmmsell(s)
    before = (exp.state, ver.version, ver.frozen_at)
    repair.repair(s, actor="claude-code")
    s.commit()
    assert (exp.state, ver.version, ver.frozen_at) == before
