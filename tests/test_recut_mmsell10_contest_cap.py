"""Re-cutting the contest-cap canary's epoch onto fresh tags.

The failure this package exists to correct was NOT a trading bug. The canary was
stood down at 19:25Z on 2026-09-06, two defects were fixed in code, and it was
re-armed at 23:51Z on the SAME tags by setting `LIVE_STRATEGIES`. Two things went
wrong in the record and neither showed up as an error:

  1. no epoch boundary, so pre-fix and post-fix live evidence sit in one bucket
     under `Emmsell10` with nothing saying they must not pool;
  2. `sync_twin_epoch` is get-or-create on the twin tag, so the closed
     `Emmsell10_pt4` row was returned untouched and never reopened — the live
     dashboard, which reads that table, showed the running canary as retired.

So what these tests pin is that the re-cut moves the RECORD and nothing else: it
promotes nothing, widens nothing, and refuses any production shape it was not
reviewed against. The happy path is the easy half.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import recut_mmsell10_contest_cap as recut
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_contest_cap as cc
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 9, 6, 18, 18, tzinfo=UTC)
BOUNDARY = datetime(2026, 9, 7, 2, 0, tzinfo=UTC)
#: sqlite hands timestamps back naive. Compare instants, not tzinfo.
NAIVE_BOUNDARY = BOUNDARY.replace(tzinfo=None)


def _at(dt):
    """The stored instant, tz-normalised so sqlite and Postgres compare alike."""
    return dt if dt is None or dt.tzinfo is None else dt.replace(tzinfo=None)


# --- the framing: this is not a promotion and not a widening ----------------


def test_the_envelope_is_the_successors_object_not_a_retyped_copy():
    """A re-cut that could carry a different envelope would be a re-arm with a
    new risk profile wearing an epoch boundary's clothes. Identity makes a
    loosened bound impossible rather than merely unlikely."""
    assert recut.RISK_ENVELOPE is cc.RISK_ENVELOPE
    assert recut.BOOK_PARAMS is cc.BOOK_PARAMS
    assert recut.ARM_KEY is cc.ARM_KEY


def test_the_book_spec_still_carries_the_contest_cap():
    """`contestcap=1` lives inside the drift-checked `book_spec`. If the re-cut
    dropped it, the new book would run UNCAPPED and the drift check — which
    compares against this very string — would agree that it should."""
    assert recut.LIVE_BOOK_SPEC == f"{recut.LIVE_TAG}:{cc.BOOK_PARAMS}"
    assert "contestcap=1" in recut.LIVE_BOOK_SPEC
    assert recut.material_config()["risk"]["max_contest_positions"] == 1
    # The predecessor's rung cap is untouched: the contest cap is the TIGHTER
    # bound, never a replacement.
    assert recut.material_config()["risk"]["max_event_rungs"] == 3


def test_the_module_authors_no_gate_and_no_transition():
    """An epoch boundary says the world changed. It says nothing about the
    experiment earning anything, so nothing here may evaluate or transition."""
    source = (recut.__file__ or "")
    text = open(source, encoding="utf-8").read()
    for forbidden in ("evaluate_gate", "transition_experiment", "arm_live_canary"):
        assert f"{forbidden}(" not in text, (
            f"{forbidden} in a re-cut would make a boundary into a promotion"
        )


def test_activation_vars_all_clear_the_env_allowlist():
    """A package whose activation the env channel refuses halfway through leaves
    an operator with a write already submitted."""
    import scripts.railway_env as railway_env

    assert recut.ACTIVATION_VARS <= set(railway_env.ALLOWED_VARS)


# --- tag safety -------------------------------------------------------------


def test_the_new_tags_are_prefix_safe_against_every_tag_they_replace():
    """`LIVE_STRATEGIES` matches by PREFIX. A tag that is a prefix of another —
    in either direction — makes one allowlist entry arm two books."""
    others = [cc.LIVE_TAG, cc.TWIN_TAG, cc.PAPER_TAG, "Gmmsell1", "Gmmsell0"]
    for new in (recut.LIVE_TAG, recut.TWIN_TAG):
        for other in others:
            assert not new.startswith(other), f"{new} would be armed by {other}"
            assert not other.startswith(new), f"{new} would arm {other}"


def test_the_twin_tag_follows_the_global_suffix_rather_than_choosing_one():
    """`LIVE_PAPER_TWIN_SUFFIX` is process-wide and production holds `_pt4`. The
    worker DERIVES every twin tag as `<live_tag><suffix>`, so a twin tag that
    disagrees names a book the harness never writes to."""
    suffix = cc.TWIN_TAG[len(cc.LIVE_TAG):]
    assert suffix == "_pt4"
    assert recut.TWIN_TAG == f"{recut.LIVE_TAG}{suffix}"


def test_the_tags_fit_the_columns_they_are_written_to():
    """`paper_trades.strategy` is String(24); a silently truncated tag would
    collide with its own prefix."""
    assert len(recut.TWIN_TAG) <= 24
    assert len(recut.LIVE_TAG) <= 24
    assert recut.LIVE_TAG != recut.TWIN_TAG


# --- fixture: the shape production is actually in ---------------------------


def _live_canary(s, *, live_tag=cc.LIVE_TAG, twin_tag=cc.TWIN_TAG,
                 paper_tag=recut.PAPER_TAG):
    """A LIVE_CANARY experiment holding an open live epoch with a live
    deployment, its twin, and the paper parent riding alongside."""
    exp = svc.create_experiment(s, key=cc.SUCCESSOR_KEY, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="live contest cap", now=T0)
    svc.add_arm(s, ver, arm_key=cc.ARM_KEY, role="treatment", strategy_tag=paper_tag)
    ver.control_exemption_reason = "single-book canary fixture"
    ver.risk_json = dict(cc.RISK_ENVELOPE)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="live execution boundary", started_at=T0)
    live = svc.register_deployment(
        s, epoch, deployment_key=cc.LIVE_DEPLOYMENT_KEY, stage="LIVE_CANARY",
        kind="live", arms={cc.ARM_KEY: live_tag}, started_at=T0,
        _sanctioned_canary=True,
    )
    svc.register_deployment(
        s, epoch, deployment_key=cc.TWIN_DEPLOYMENT_KEY, stage="LIVE_CANARY",
        kind="paper_twin", arms={cc.ARM_KEY: twin_tag}, twin_of=live,
        started_at=T0, _sanctioned_canary=True,
    )
    svc.register_deployment(
        s, epoch, deployment_key=cc.PAPER_DEPLOYMENT_KEY, stage="PAPER",
        kind="paper", arms={cc.ARM_KEY: paper_tag}, started_at=T0,
    )
    # Armed through the sanctioned path in production; the fixture sets the state
    # directly because the transition is not what is under test here.
    exp.state = "LIVE_CANARY"
    s.flush()
    return exp, ver, epoch


@pytest.fixture
def canary(xos_session, xos_platform):
    del xos_platform  # required for open_epoch's snapshot resolution
    return _live_canary(xos_session)


# --- the happy path ---------------------------------------------------------


def test_it_closes_the_old_epoch_and_opens_its_successor_at_one_instant(
    xos_session, canary
):
    _exp, ver, old_epoch = canary

    out = recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert out["already_recut"] is False
    assert out["closed_epoch"] == old_epoch.epoch_number
    assert out["epoch"] == old_epoch.epoch_number + 1
    assert _at(old_epoch.ended_at) == NAIVE_BOUNDARY
    from sqlalchemy import select

    from kalshi_bot.experiment_os.models import ExperimentEpoch

    opened = xos_session.scalar(
        select(ExperimentEpoch).where(
            ExperimentEpoch.version_id == ver.id, ExperimentEpoch.ended_at.is_(None)
        )
    )
    assert opened is not None
    assert _at(opened.started_at) == NAIVE_BOUNDARY, (
        "no gap may open between the two epochs"
    )
    assert opened.impact_class == "I2"
    assert recut.FIX_COMMIT in opened.reason


def test_the_predecessors_tags_are_retired_and_the_fresh_pair_registered(
    xos_session, canary
):
    """The whole point: the live book comes back on tags with no history, and the
    old pair is closed rather than carried."""
    from sqlalchemy import select

    from kalshi_bot.experiment_os.models import ExperimentDeployment

    out = recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert out["live"] == {"deployment": recut.LIVE_DEPLOYMENT_KEY,
                           "tag": recut.LIVE_TAG}
    assert out["twin"] == {"deployment": recut.TWIN_DEPLOYMENT_KEY,
                           "tag": recut.TWIN_TAG}
    assert out["retired_tags"] == [cc.LIVE_TAG, cc.TWIN_TAG]

    for key in (cc.LIVE_DEPLOYMENT_KEY, cc.TWIN_DEPLOYMENT_KEY):
        dep = xos_session.scalar(
            select(ExperimentDeployment).where(
                ExperimentDeployment.deployment_key == key)
        )
        assert _at(dep.ended_at) == NAIVE_BOUNDARY, (
            f"{key} must not survive the boundary"
        )


def test_the_twin_starts_in_the_same_epoch_as_its_live_deployment(
    xos_session, canary
):
    """A twin that starts anywhere else is not an execution control — it is a
    second book that happens to share a name."""
    from sqlalchemy import select

    from kalshi_bot.experiment_os.models import ExperimentDeployment

    recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    live, twin = (
        xos_session.scalar(
            select(ExperimentDeployment).where(
                ExperimentDeployment.deployment_key == k)
        )
        for k in (recut.LIVE_DEPLOYMENT_KEY, recut.TWIN_DEPLOYMENT_KEY)
    )
    assert twin.epoch_id == live.epoch_id
    assert _at(twin.started_at) == _at(live.started_at) == NAIVE_BOUNDARY
    assert twin.twin_of_deployment_id == live.id, "the pair must be structural"


def test_the_paper_parent_rides_across_the_boundary(xos_session, canary):
    """XOS-000011: an epoch cut that opens an empty successor is a trading
    outage, not a gap in the record — the parent's tag stops resolving and every
    entry it attempts is refused."""
    out = recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert out["carried_paper"], "the mmsell10 control must not go dark"
    carried = out["carried_paper"][0]
    assert carried.startswith(cc.PAPER_DEPLOYMENT_KEY)


def test_the_experiment_does_not_move_state(xos_session, canary):
    """It was LIVE_CANARY before and it is LIVE_CANARY after. A re-cut that
    changed state would be a promotion smuggled through a boundary."""
    exp, _ver, _epoch = canary

    recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert exp.state == "LIVE_CANARY"


def test_a_second_run_reports_already_recut_rather_than_cutting_again(
    xos_session, canary
):
    """A duplicate envelope must not put the tag on two active arms — which is
    the other way to stop a book."""
    first = recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)
    second = recut.recut(
        xos_session, approved_by="50cal", started_at=BOUNDARY + timedelta(hours=1)
    )

    assert first["already_recut"] is False
    assert second["already_recut"] is True
    assert second["tags"] == [recut.LIVE_TAG]
    assert second["live_open"] is True


# --- the refusals, which are the safety argument ----------------------------


def test_it_refuses_without_an_approver(xos_session, canary):
    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.recut(xos_session, approved_by="  ", started_at=BOUNDARY)

    assert "approved_by" in str(exc.value)


def test_it_refuses_an_experiment_that_is_not_live_canary(xos_session, canary):
    """This re-cuts under a canary already armed. Against a PAPER experiment it
    would be creating live lineage, which only arm_live_canary may do."""
    exp, _ver, _epoch = canary
    exp.state = "PAPER"
    xos_session.flush()

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert "not LIVE_CANARY" in str(exc.value)


def test_it_refuses_when_the_live_deployment_carries_a_different_tag(
    xos_session, xos_platform
):
    """If production is not the shape this was reviewed against, a half-applied
    re-cut is worse than none."""
    del xos_platform
    _live_canary(xos_session, live_tag="Zmmsell10")

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert "does not match what this re-cut was reviewed against" in str(exc.value)


def test_it_refuses_when_the_predecessor_live_deployment_is_already_closed(
    xos_session, canary
):
    """Nothing to cut. Opening a boundary over an absent predecessor would
    invent one."""
    from sqlalchemy import select

    from kalshi_bot.experiment_os.models import ExperimentDeployment

    dep = xos_session.scalar(
        select(ExperimentDeployment).where(
            ExperimentDeployment.deployment_key == cc.LIVE_DEPLOYMENT_KEY)
    )
    svc.end_deployment(xos_session, dep, ended_at=BOUNDARY - timedelta(hours=1))

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert "to be open on epoch" in str(exc.value)


def test_it_refuses_a_new_tag_that_already_has_paper_history(xos_session, canary):
    """The 2026-08-15 Lmmsell failure: a tag with prior paper positions hands
    live a book of tickers it can never trade, throttling one side ~29x harder
    than the other. `arm_live_canary` enforces this; so must any path that sets
    `_sanctioned_canary=True`."""
    xos_session.add(PaperTrade(
        market_ticker="KXMLBTOTAL-26SEP061310ATLPHI-13",
        strategy=recut.LIVE_TAG, created_at=BOUNDARY - timedelta(hours=2),
    ))
    xos_session.flush()

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert "FRESH tags with no inherited paper state" in str(exc.value)
    assert recut.LIVE_TAG in str(exc.value)


def test_a_paper_row_written_after_the_boundary_does_not_block_the_recut(
    xos_session, canary
):
    """The rule is about INHERITED state. A row the freshly armed book writes
    for itself, at or after the boundary, is the system working."""
    xos_session.add(PaperTrade(
        market_ticker="KXMLBTOTAL-26SEP061310ATLPHI-13",
        strategy=recut.LIVE_TAG, created_at=BOUNDARY + timedelta(minutes=5),
    ))
    xos_session.flush()

    out = recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert out["already_recut"] is False


def test_it_refuses_a_new_tag_already_carried_by_an_active_deployment(
    xos_session, xos_platform
):
    """Two active arms on one tag suppress live candidates — the other way a
    book stops without saying so."""
    del xos_platform
    _exp, _ver, epoch = _live_canary(xos_session)
    # Some OTHER open deployment already holds the tag — the shape check passes,
    # so this exercises the freshness guard rather than tripping an earlier one.
    svc.register_deployment(
        xos_session, epoch, deployment_key="somebody-elses-book", stage="PAPER",
        kind="paper", arms={cc.ARM_KEY: recut.LIVE_TAG}, started_at=T0,
    )

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert "already carried by an active deployment" in str(exc.value)


def test_it_refuses_when_the_paper_parent_is_missing(xos_session, xos_platform):
    """Cutting here would take the control book dark, which is exactly the
    outage the carry-forward exists to prevent."""
    del xos_platform
    _live_canary(xos_session, paper_tag="mmsellSOMETHINGELSE")

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.recut(xos_session, approved_by="50cal", started_at=BOUNDARY)

    assert "would take the control book dark" in str(exc.value)


# --- the transport ----------------------------------------------------------


def test_the_package_is_reachable_by_arm_canary_and_registers_no_contract():
    """ARM_CANARY is the action; REGISTER_PACKAGE aimed here is mis-addressed
    and must say so rather than quietly doing nothing."""
    from kalshi_bot.experiment_os import experiment_commands as ec

    pkg = ec._packages()["mmsell-contestcap-epoch2"]

    assert pkg.arm is recut.recut
    assert pkg.experiment_key == cc.SUCCESSOR_KEY
    assert pkg.activation_vars == recut.ACTIVATION_VARS
    with pytest.raises(svc.ExperimentOsError):
        pkg.register(object(), actor="cal")


def test_arming_this_package_is_restricted_to_live_ops():
    """Real money is Live Ops' call, and a receipt naming another role would
    misdescribe who decided."""
    from kalshi_bot.experiment_os import experiment_commands as ec

    assert ec.ACTION_ROLES["ARM_CANARY"] == frozenset({"LIVE_OPS"})


# --- the activation value, derived rather than hand-composed ----------------

#: The running value read off the service at 2026-09-07T01:29Z, verbatim. Pinned
#: here because the derivation's whole job is to survive a real ~900-char string
#: holding nineteen books, not a two-entry fixture.
PROD_VARIANTS = (
    "mmsell5:lo=5,hi=12,only=TOTAL+SPREAD+ASG+HRDERBY;"
    "mmsell6:lo=5,hi=8;"
    "mmsell7:lo=5,hi=10,htcmax=24;"
    "mmsell8:lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY;"
    "mmsell9:lo=5,hi=12,only=TOTAL+SPREAD+ASG+HRDERBY+BTCD+ETH,maxyes=7;"
    "mmsell10:lo=5,hi=10,maxyes=7;"
    "mmsellA4:lo=5,hi=10,maxyes=7,volw=6,volv=6;"
    "mmsellA5:lo=5,hi=10,maxyes=7,strangle=1;"
    "Tmmsell1:lo=5,hi=10,maxyes=7,mtype=price_strike;"
    "Tmmsell2:lo=5,hi=10,maxyes=7,mtype=mention;"
    "Tmmsell5:lo=5,hi=10,maxyes=7,mode=scheduled+discrete,"
    "xmtype=event_stat+politics+announcement;"
    "Tmmsell6:lo=5,hi=10,maxyes=7,"
    "mtype=player_prop+spread+exact_score+mention+price_strike+outright+rank_culture;"
    "Lmmsell8:lo=5,hi=12,only=BTCD+ETH+ASG+HRDERBY;"
    "Lmmsell10:lo=5,hi=10,maxyes=7;"
    "Cmmsell10:lo=5,hi=10,maxyes=7,size=1;"
    "Dmmsell10:lo=5,hi=10,maxyes=7,size=1;"
    "Gmmsell0:lo=5,hi=10,maxyes=7;"
    "Gmmsell1:lo=5,hi=10,maxyes=7,contestcap=1;"
    "Emmsell10:lo=5,hi=10,maxyes=7,size=1,contestcap=1"
)


def _tags(variants: str) -> list[str]:
    return [t.partition(":")[0] for t in variants.split(";") if t]


def test_the_derivation_swaps_exactly_one_book_and_drops_no_other():
    """The value is one ~900-char string holding every mmsell book. Dropping one
    by a typo stops it silently, which is the whole reason this is derived."""
    out = recut.variants_for_recut(PROD_VARIANTS)

    before, after = _tags(PROD_VARIANTS), _tags(out)
    assert set(before) - set(after) == {cc.LIVE_TAG}
    assert set(after) - set(before) == {recut.LIVE_TAG}
    assert len(after) == len(before)
    # Every OTHER book keeps its spec byte for byte.
    kept = [t for t in out.split(";") if not t.startswith(f"{recut.LIVE_TAG}:")]
    assert kept == [
        t for t in PROD_VARIANTS.split(";") if not t.startswith(f"{cc.LIVE_TAG}:")
    ]


def test_the_retired_book_is_removed_not_merely_shadowed():
    """`Emmsell10`'s deployment closes at the boundary. A stale entry left behind
    defines a book with no active arm — under NEW_ONLY every entry it attempts is
    refused, every cycle. That is XOS-000011 in a config file."""
    out = recut.variants_for_recut(PROD_VARIANTS)

    assert f"{cc.LIVE_TAG}:" not in out
    assert f"{recut.LIVE_TAG}:{cc.BOOK_PARAMS}" in out


def test_the_new_book_carries_the_cap_and_the_size():
    out = recut.variants_for_recut(PROD_VARIANTS)

    spec = next(t for t in out.split(";") if t.startswith(f"{recut.LIVE_TAG}:"))
    assert spec == recut.LIVE_BOOK_SPEC
    assert "contestcap=1" in spec and "size=1" in spec


def test_the_derivation_is_idempotent():
    once = recut.variants_for_recut(PROD_VARIANTS)

    assert recut.variants_for_recut(once) == once


def test_it_refuses_when_the_running_predecessor_spec_is_not_what_we_registered():
    """If production is not running the book we believe we are retiring, the
    belief is what is wrong — not the config."""
    drifted = PROD_VARIANTS.replace(
        f"{cc.LIVE_TAG}:{cc.BOOK_PARAMS}", f"{cc.LIVE_TAG}:lo=5,hi=10,maxyes=7"
    )

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.variants_for_recut(drifted)

    assert "reconcile the running config" in str(exc.value)


def test_it_refuses_to_overwrite_a_different_spec_on_the_new_tag():
    """Silently replacing it would be an undetected parameter change to a
    registered book."""
    hostile = PROD_VARIANTS + f";{recut.LIVE_TAG}:lo=1,hi=99"

    with pytest.raises(svc.ExperimentOsError) as exc:
        recut.variants_for_recut(hostile)

    assert "undetected parameter change" in str(exc.value)


def test_activation_env_pins_the_safeguards_and_names_the_switch_last():
    """The envelope must be true of the PROCESS, not merely equal to today's code
    defaults — and the book has to exist before the switch that lets it spend."""
    class _S:
        mmsell_variants = PROD_VARIANTS

    env = recut.activation_env(_S())

    assert set(env) == set(recut.ACTIVATION_VARS)
    assert env["LIVE_STRATEGIES"] == recut.LIVE_TAG
    assert env["MMSELL_VARIANTS"] == recut.variants_for_recut(PROD_VARIANTS)
    for name, value in cc.RISK_ENVELOPE["settings"].items():
        assert env[name] == value, f"{name} must be pinned as the envelope declares"
    # The global contest-cap switch stays out: this book opts in through its own
    # `contestcap=1`, so no other mmsell book's selection moves.
    assert "MMSELL_CONTEST_CAP_ENABLED" not in env
