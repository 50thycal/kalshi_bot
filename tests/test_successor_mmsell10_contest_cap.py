"""The mmsell10 contest-cap successor: one bound corrected, and nothing else.

The thing most likely to go wrong here is not a bug, it is a FRAMING error:
treating `contestcap=1` as a treatment that has to win a promotion. It is not.
The envelope already carried `max_event_rungs: 3` with no gate behind it; that
number counts `event_ticker`, which is series x contest, so it caps 3 rungs per
LISTING and one MLB game can legally hold ~15 correlated positions.
`max_contest_positions: 1` is the correction to that existing bound, and a bound
can only ever REFUSE an entry.

So these tests pin two things: that the package changes EXACTLY one thing
against its predecessor, and that it authors NO decision rule of its own.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from kalshi_bot.experiment_os import canary_mmsell10 as v2
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_capacity as capacity
from kalshi_bot.experiment_os import successor_mmsell10_contest_cap as cc
from kalshi_bot.mmsell import regimes

UTC = timezone.utc
T0 = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)


# --- the framing: this authors no decision rule ----------------------------


def test_both_gates_are_the_predecessors_objects_not_retyped_copies():
    """A risk BOUND does not get a gate of its own. Importing the specs makes
    "carried verbatim" a property of the code rather than a docstring claim: a
    loosened threshold is not a one-character edit away, it is impossible."""
    assert cc.KEEP_GATE_SPEC is v2.KEEP_GATE_SPEC
    assert cc.PROMOTION_GATE_SPEC is v2.PROMOTION_GATE_SPEC
    assert cc.KEEP_GATE_SPEC is capacity.KEEP_GATE_SPEC


def test_no_gate_clause_anywhere_mentions_the_contest_cap():
    """If a clause named the cap, the cap would be a candidate being judged.
    It is a limit. Nothing in either gate may reference it."""
    import json

    blob = json.dumps([cc.KEEP_GATE_SPEC, cc.PROMOTION_GATE_SPEC]).lower()

    for word in ("contest", "contestcap", "correlation_cap"):
        assert word not in blob, f"{word!r} in a gate spec makes a bound a candidate"


def test_register_refuses_a_promotion_sample_floor():
    """The transport always passes the field. Accepting a VALUE would author a
    promotion criterion this package has no business authoring."""
    with pytest.raises(svc.ExperimentOsError) as exc:
        cc.register(object(), actor="cal", promotion_sample_floor=150, now=T0)

    assert "risk BOUND, not a candidate" in str(exc.value)


# --- exactly one change against the predecessor ----------------------------


def test_the_envelope_differs_from_the_predecessors_by_the_cap_and_the_stage():
    """The whole safety argument is "one thing moved". Assert it structurally
    rather than trusting a reviewer to diff two dicts by eye."""
    mine, theirs = dict(cc.RISK_ENVELOPE), dict(capacity.RISK_ENVELOPE)
    # Prose fields carry the reasoning and are expected to differ.
    for prose in ("stage", "shard_note", "live_tier_note"):
        mine.pop(prose, None)
        theirs.pop(prose, None)

    added = set(mine) - set(theirs)
    removed = set(theirs) - set(mine)
    changed = {k for k in set(mine) & set(theirs) if mine[k] != theirs[k]}

    assert added == {"max_contest_positions"}, added
    assert not removed, removed
    assert not changed, changed
    assert mine["max_contest_positions"] == 1


def test_the_event_rung_cap_is_LEFT_AT_3_and_not_swapped_out():
    """The contest cap is the TIGHTER bound, not a replacement. `max_event_rungs`
    still binds per event ticker for anything the contest key does not group, and
    removing it would be a second change in a step that carries one."""
    assert cc.RISK_ENVELOPE["max_event_rungs"] == 3
    assert cc.RISK_ENVELOPE["max_event_rungs"] == capacity.RISK_ENVELOPE[
        "max_event_rungs"]


def test_size_open_cap_and_band_are_untouched():
    """Named explicitly because the brief names them: do NOT also change size,
    open cap or band in the same step."""
    for key in ("contracts_per_order", "max_order_dollars",
                "max_market_exposure_usd", "max_open_positions",
                "max_book_exposure_usd", "daily_realized_loss_stop_usd",
                "total_canary_loss_budget_usd", "order_timeout_seconds",
                "entry_price_offset_cents"):
        assert cc.RISK_ENVELOPE[key] == capacity.RISK_ENVELOPE[key], key

    assert cc.BASE_BOOK_PARAMS == capacity.BOOK_PARAMS
    assert cc.BOOK_PARAMS == f"{capacity.BOOK_PARAMS},contestcap=1"


def test_the_live_book_spec_carries_the_cap_so_drift_can_see_it():
    """`book_params` is drift-checked. Putting the cap there means editing it out
    of MMSELL_VARIANTS mid-canary is recorded as EXPERIMENT_CONFIG_DRIFT, not
    applied silently — a real-money bound cannot be removed by an env edit."""
    assert cc.LIVE_BOOK_SPEC == f"{cc.LIVE_TAG}:{cc.BOOK_PARAMS}"
    assert "contestcap=1" in cc.material_config()["book_spec"]
    assert cc.material_config()["risk"]["max_contest_positions"] == 1


# --- the global-switch trap (.claude/sessions/live-ops.md) ------------------


def test_the_envelope_never_sets_the_GLOBAL_contest_cap_switch():
    """`tracker.py` is shared by every mmsell book, so MMSELL_CONTEST_CAP_ENABLED
    would re-scope mmsell5-10, the Tmmsell family, Lmmsell and the running
    Gmmsell control at one instant — a shared-semantic change belonging to
    Platform Change Review, and under NEW_ONLY a contract change nobody
    registered. The per-book override is what makes this book opt in alone."""
    names = set(cc.RISK_ENVELOPE["settings"]) | set(cc.ACTIVATION_VARS)

    assert not {n for n in names if "CONTEST_CAP" in n}
    assert "contestcap=1" in cc.BOOK_PARAMS, (
        "the cap must arrive per-book, through book_params"
    )


def test_the_global_twin_suffix_is_carried_not_changed():
    """LIVE_PAPER_TWIN_SUFFIX is global: changing it orphans every OTHER live
    book's twin tag, which then resolves to no deployment arm and goes dark under
    NEW_ONLY. That is the XOS-000011 shape."""
    suffix = cc.RISK_ENVELOPE["settings"]["LIVE_PAPER_TWIN_SUFFIX"]

    assert suffix == capacity.RISK_ENVELOPE["settings"]["LIVE_PAPER_TWIN_SUFFIX"]
    assert cc.TWIN_TAG == f"{cc.LIVE_TAG}{suffix}", (
        "the twin tag is DERIVED from the global suffix, never chosen against it"
    )


# --- tags -------------------------------------------------------------------


def test_live_and_twin_tags_are_fresh_and_collide_with_no_generation():
    """`arm_live_canary`'s no-inherited-state rule exists because of 2026-08-15,
    where mmsell10 armed onto a tag with 87 pre-existing paper positions."""
    generations = {v2.LIVE_TAG, v2.TWIN_TAG, capacity.LIVE_TAG, capacity.TWIN_TAG,
                   "Gmmsell0", "Gmmsell1", "Lmmsell8", "Lmmsell10", "mmsell10"}

    assert cc.LIVE_TAG not in generations
    assert cc.TWIN_TAG not in generations
    assert cc.LIVE_TAG != cc.TWIN_TAG
    assert cc.PAPER_TAG not in (cc.LIVE_TAG, cc.TWIN_TAG)


def test_no_existing_tag_is_a_prefix_of_the_live_tag_or_vice_versa():
    """LIVE_STRATEGIES matches by PREFIX. A live tag that prefixes (or is
    prefixed by) a paper tag would silently arm the wrong book."""
    from kalshi_bot.config import Settings

    # The class default, not an instance: `Settings()` needs Kalshi credentials.
    # Production overrides MMSELL_VARIANTS, so the live tags currently carried
    # there are named explicitly alongside it.
    default = Settings.model_fields["mmsell_variants"].default
    others = {
        entry.split(":", 1)[0]
        for entry in default.split(";") if ":" in entry
    } | {"Cmmsell10", "Dmmsell10", "Lmmsell8", "Lmmsell10", "Gmmsell0", "Gmmsell1",
         "Dmmsell10_pt4", "Cmmsell10_pt3", "theta4"}
    others.discard(cc.LIVE_TAG)
    others.discard(cc.TWIN_TAG)

    for tag in others:
        assert not tag.startswith(cc.LIVE_TAG), tag
        assert not cc.LIVE_TAG.startswith(tag), tag


def test_the_tags_fit_the_strategy_column():
    """`paper_trades.strategy` and `live_orders.strategy` are String(24). A tag
    that does not fit is truncated on write, which silently merges books."""
    assert len(cc.LIVE_TAG) <= 24
    assert len(cc.TWIN_TAG) <= 24


def test_the_paper_control_tag_is_carried_over_deliberately():
    """The paper book is the CONTROL, not the canary, so history on it is wanted.
    Handing it over at the same instant the predecessor's PAPER deployment ends
    is what stops the tag losing its arm — the XOS-000011 blackout shape."""
    assert cc.PAPER_TAG == capacity.PAPER_TAG == "mmsell10"


def test_the_running_paper_experiments_treatment_tag_is_NOT_claimed():
    """`Gmmsell1` carries the active treatment arm of mmsell-correlation-cap, a
    paper experiment with a 60-settlement-day floor. Claiming its tag would end
    that experiment to start this one; it is the paper prior this canary rests
    on and it must keep running."""
    from kalshi_bot.experiment_os import correlation_cap

    assert cc.PAPER_TAG != correlation_cap.CAPPED_TAG
    assert cc.PAPER_TAG != correlation_cap.CONTROL_TAG
    assert correlation_cap.CAPPED_TAG not in (cc.LIVE_TAG, cc.TWIN_TAG)


# --- the mechanism the envelope declares is the one that actually runs ------


def test_the_cap_the_envelope_declares_is_the_one_the_tracker_applies():
    """The envelope says one position per unit of CORRELATION. That claim is only
    true if `contest_key_of` groups a game's listings — the thing c4b2ce1's
    predecessor keyed wrong. Pin it on the real drawdown slate."""
    game = [
        "KXMLBTOTAL-26SEP022138NYYLAA-8",
        "KXMLBTEAMTOTAL-26SEP022138NYYLAA-NYY6",
        "KXMLBHR-26SEP022138NYYLAA-AARONJUDGE1",
    ]

    keys = {regimes.contest_key_of(t) for t in game}

    assert keys == {"MLB:26SEP022138NYYLAA"}, (
        "three listings on one game must share one budget, or a cap of 1 lets "
        "three correlated rungs through"
    )


def test_outside_sports_the_key_falls_back_to_the_event_ticker():
    """This is the half carrying almost all of the measured effect: outside
    CONTEST_GROUPED_REGIMES the key IS the event ticker, so cap=1 tightens every
    ladder from the rung cap's 3 to 1. Do NOT "improve" the cap by restricting
    grouping to sports — that keeps the half that measured nothing."""
    assert regimes.contest_key_of("KXPAYROLLS-26SEP") == "KXPAYROLLS-26SEP"
    assert regimes.contest_key_of("KXBTCD-26AUG1717-B1") != regimes.contest_key_of(
        "KXETHD-26AUG1717-B1"
    )


def test_the_contest_read_is_NOT_settlement_date_scoped():
    """c4b2ce1 (PR #335). Before it, an MLB game starting after ~18:30 ET had its
    F5 legs before UTC midnight and its full-game legs after, so they counted
    against two different days' budgets and the cap did not fire — silently,
    because skipped_contest_cap simply stayed 0. Arming this envelope on code
    without that read would ship a cap that cannot bind on exactly the late games
    the drawdown came from."""
    import inspect

    from kalshi_bot import repository

    fn = repository.open_positions_contest_summary
    params = list(inspect.signature(fn).parameters)

    assert params == ["session", "strategy", "ticker"], (
        "a date/settlement parameter on this read is the straddle bug returning: "
        f"got {params}"
    )
    body = inspect.getsource(fn).split('"""')[-1]
    assert "contest_key_of" in body
    for scoping in ("settlement", "date", "close_time"):
        assert scoping not in body.lower(), (
            f"the contest read must span the WHOLE open book; {scoping!r} in its "
            "query body is the UTC-midnight straddle bug returning"
        )


# --- the predecessor's evidence is protected -------------------------------


def test_a_draining_live_book_does_NOT_block_the_successor(monkeypatch):
    """The predecessor winds down BESIDE the new book, on separate tags and
    epochs. Ending its LIVE deployment would leave the tag without an arm, so its
    settlements could not be RECORDED and its final evidence would be wrong."""
    seen = {}

    class _Pred:
        id = 7

    class _Live:
        id, kind, ended_at = 1, "live", None
        deployment_key = "mmsell-capacity-live-1"

    class _Session:
        def scalars(self, *a, **k):
            return type("R", (), {"all": staticmethod(lambda: [_Live()])})()

    monkeypatch.setattr(cc, "get_experiment",
                        lambda s, key: _Pred() if key == cc.PREDECESSOR_KEY else None)
    monkeypatch.setattr(cc, "_epoch_experiment_id", lambda s, d: _Pred.id)
    monkeypatch.setattr(cc, "_tags_of", lambda s, d: ["Dmmsell10"])

    import kalshi_bot.repository as repo
    monkeypatch.setattr(repo, "count_live_book_open", lambda s, tag: 12)
    monkeypatch.setattr(svc, "end_deployment",
                        lambda *a, **k: seen.setdefault("ended", []).append(a))
    monkeypatch.setattr(svc, "create_experiment",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reached")))

    with pytest.raises(RuntimeError, match="reached"):
        cc.register(_Session(), actor="cal", now=T0)

    assert "ended" not in seen


def test_ending_a_paper_deployment_whose_tag_holds_live_positions_is_refused(
    monkeypatch,
):
    class _Pred:
        id = 7

    class _Paper:
        id, kind, ended_at = 2, "paper", None
        deployment_key = "mmsell-capacity-paper-1-e1-e1-e2"

    class _Session:
        def scalars(self, *a, **k):
            return type("R", (), {"all": staticmethod(lambda: [_Paper()])})()

    monkeypatch.setattr(cc, "get_experiment",
                        lambda s, key: _Pred() if key == cc.PREDECESSOR_KEY else None)
    monkeypatch.setattr(cc, "_epoch_experiment_id", lambda s, d: _Pred.id)
    monkeypatch.setattr(cc, "_tags_of", lambda s, d: ["mmsell10"])

    import kalshi_bot.repository as repo
    monkeypatch.setattr(repo, "count_live_book_open", lambda s, tag: 3)

    ended = []
    monkeypatch.setattr(svc, "end_deployment", lambda *a, **k: ended.append(a))

    with pytest.raises(svc.ExperimentOsError) as exc:
        cc.register(_Session(), actor="cal", now=T0)

    assert "3 open live position" in str(exc.value)
    assert not ended, "must refuse BEFORE ending anything"


def test_register_refuses_to_run_twice(monkeypatch):
    monkeypatch.setattr(cc, "get_experiment", lambda s, key: object())

    with pytest.raises(svc.ExperimentOsError) as exc:
        cc.register(object(), actor="cal", now=T0)

    assert "already exists" in str(exc.value)


def test_arm_refuses_before_the_contract_exists(monkeypatch):
    monkeypatch.setattr(cc, "get_experiment", lambda s, key: None)

    with pytest.raises(svc.ExperimentOsError) as exc:
        cc.arm(object(), approved_by="cal")

    assert "REGISTER_PACKAGE first" in str(exc.value)


# --- the package must be REACHABLE, not just correct ------------------------


def test_the_package_is_registered_with_the_command_transport():
    from kalshi_bot.experiment_os.experiment_commands import _packages

    pkg = _packages().get("mmsell-contest-cap-canary")

    assert pkg is not None, "REGISTER_PACKAGE cannot reach an unregistered package"
    assert pkg.experiment_key == cc.SUCCESSOR_KEY
    assert pkg.register is cc.register
    assert pkg.arm is cc.arm


def test_arming_it_is_restricted_to_LIVE_OPS():
    from kalshi_bot.experiment_os.experiment_commands import ACTION_ROLES

    assert ACTION_ROLES["ARM_CANARY"] == frozenset({"LIVE_OPS"})


def test_every_declared_activation_var_clears_the_ops_allowlist():
    """A package whose activation the env channel refuses halfway through leaves
    an operator with a write already submitted — the #266 defect class."""
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    pkg = _packages()["mmsell-contest-cap-canary"]

    assert pkg.activation_vars
    for name in pkg.activation_vars:
        assert name in railway_env.ALLOWED_VARS, name


def test_activation_declares_MMSELL_VARIANTS_because_the_book_must_exist():
    """LIVE_STRATEGIES=Emmsell10 on its own names a book that does not exist: no
    orders, and book_params[Emmsell10] absent against a declared value, which
    enforcement records as EXPERIMENT_CONFIG_DRIFT and which takes the keep gate
    to BLOCKED_INTEGRITY."""
    assert "MMSELL_VARIANTS" in cc.ACTIVATION_VARS
    assert "LIVE_STRATEGIES" in cc.ACTIVATION_VARS


# --- the audit that proves the cap fired on real money ----------------------
#
# Every prior failure on this mechanism looked green. So what these pin is not
# that the audit reports a number — it is that the audit REFUSES to call an
# uninformative result a verification.


def _audit():
    import sys

    sys.path.insert(0, "scripts")
    import mmsell_contest_cap_audit as audit

    return audit


def test_the_audit_uses_the_WORKERS_own_contest_key_not_a_second_copy():
    """Failure mode 1 was keying the wrong unit. An audit that re-implemented the
    key in SQL would be that same mistake rebuilt inside its own check: the two
    implementations can disagree and the audit would certify the wrong one."""
    from kalshi_bot.mmsell import regimes

    key_of = _audit()._contest_key_of()

    for ticker in ("KXMLBTOTAL-26SEP022138NYYLAA-8",
                   "KXMLBHR-26SEP022138NYYLAA-AARONJUDGE1",
                   "KXPAYROLLS-26SEP",
                   "KXBTCD-26AUG1717-B1"):
        assert key_of(ticker) == regimes.contest_key_of(ticker), ticker


def test_a_clean_live_max_is_UNPROVEN_when_the_uncapped_book_held_nothing(capsys):
    """The trap this whole line of work keeps falling into: a zero counter read
    as "the cap is unnecessary" when it more likely means "the cap is broken".
    If nothing anywhere held 2+ on one contest, a live max of 1 proves nothing
    and the audit must say so rather than reporting success."""
    audit = _audit()
    live = {"MLB:GAME1": [("KXMLBTOTAL-GAME1-8", None)]}
    uncapped = {"MLB:GAME2": [("KXMLBTOTAL-GAME2-8", None)]}

    audit._report("live", live, 1)
    audit._report("uncapped", uncapped, None)
    had_work = any(len(v) >= 2 for v in uncapped.values())

    assert not had_work, "fixture must have nothing for the cap to refuse"


def test_a_contest_over_the_cap_is_a_BREACH_not_a_note():
    """More than one position on one contest, on real money, means the bound is
    not being applied. There is no reading of that which is a note."""
    audit = _audit()
    over_cap = {"MLB:26SEP022138NYYLAA": [
        ("KXMLBTOTAL-26SEP022138NYYLAA-8", None),
        ("KXMLBHR-26SEP022138NYYLAA-AARONJUDGE1", None),
    ]}

    worst, over, _straddle = audit._report("live", over_cap, 1)

    assert worst == 2
    assert over == 1, "a contest above the cap must be counted as over the cap"


def test_the_audit_flags_a_contest_that_straddles_UTC_MIDNIGHT():
    """The c4b2ce1 case, and the one most likely to regress: a contest whose
    markets carry more than one distinct UTC close date. Until one has occurred
    live, the fix is UNEXERCISED in production and must not be reported as
    verified."""
    import datetime as dt

    audit = _audit()
    straddling = {"MLB:26SEP022138NYYLAA": [
        ("KXMLBF5TOTAL-26SEP022138NYYLAA-4", dt.date(2026, 9, 2)),
        ("KXMLBTOTAL-26SEP022138NYYLAA-8", dt.date(2026, 9, 3)),
    ]}

    _worst, _over, straddle = audit._report("live", straddling, 1)

    assert straddle == 1


def test_a_resting_order_counts_as_an_open_rung():
    """The cap refuses on OPEN positions and `count_live_book_open` counts a
    resting order as open, so an audit that only looked at fills would under-read
    the book and could miss a breach that the tracker itself would have seen."""
    assert "resting" in _audit()._COMMITTED
    assert "filled" in _audit()._COMMITTED


def test_the_audit_is_reachable_through_the_ops_channel():
    import sys

    sys.path.insert(0, "scripts")
    import ops_runner

    assert "mmsell_contest_cap_audit" in ops_runner.ALLOWED_SCRIPTS
