"""The WIN-set successor with KXTRUMPMENTION (`mmsell_win_set_2`, owner decision 2026-10-27).

Pinned, in the order in which breaking it would cost money:

  * the universe is exactly the predecessor's seven series plus KXTRUMPMENTION, and the
    added series clears the live tier bar only because this same change classifies and
    graduates it;
  * KXTRUMPMENTION is subject-split, so `contestkey=split` counts each word as its own
    contest (as KXTRUMPSAY) rather than one market per appearance;
  * everything except the universe and the tags is the predecessor's object;
  * the tags are fresh and prefix-safe against every production tag, the predecessor's
    included;
  * register -> arm runs end to end through `arm_live_canary`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import mmsell_win_set_2 as ws2
from kalshi_bot.experiment_os import mmsell_win_set_canary as pred
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.mmsell.market_types import classify
from kalshi_bot.mmsell.regimes import contest_key_of
from kalshi_bot.mmsell.tracker import MmSellTracker, _live_eligible
from kalshi_bot.registry import state_of
from tests.test_mmsell_win_set_canary import PRODUCTION_TAGS

UTC = timezone.utc
T0 = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)


def _book(settings):
    settings.mmsell_variants = ws2.LIVE_BOOK_SPEC
    (book,) = settings.mmsell_variant_list
    return book


def test_the_universe_is_the_predecessors_plus_trump_mention():
    assert set(ws2.WIN_SERIES) == set(pred.WIN_SERIES) | {"KXTRUMPMENTION"}
    assert ws2.ADDED_SERIES == ("KXTRUMPMENTION",)


def test_trump_mention_is_classified_graduated_and_live_eligible(settings):
    assert classify("KXTRUMPMENTION") == ("mention", "discrete")
    assert state_of("KXTRUMPMENTION") == "graduated"
    assert _live_eligible("KXTRUMPMENTION", settings.mmsell_live_skip_series_list,
                          settings.mmsell_live_min_tier)
    assert MmSellTracker._book_admits_series(_book(settings), "KXTRUMPMENTION")


def test_the_b_series_does_not_ride_in_on_the_longest_prefix(settings):
    """KXTRUMPMENTIONB would inherit KXTRUMPMENTION's graduation by longest prefix; its own
    in_review row keeps it off real money (unreviewed rules, negative paper)."""
    assert state_of("KXTRUMPMENTIONB") == "in_review"
    assert not _live_eligible("KXTRUMPMENTIONB", settings.mmsell_live_skip_series_list,
                              settings.mmsell_live_min_tier)


@pytest.mark.parametrize("series", ["KXNFLTOTAL", "KXBTCD", "KXGOLDD", "KXTRUMPMENTIONB",
                                    "KXTRUMPSAYMONTH"])
def test_anything_else_is_refused(settings, series):
    assert not MmSellTracker._book_admits_series(_book(settings), series)


def test_trump_mention_words_are_separate_contests():
    a = contest_key_of("KXTRUMPMENTION-26SEP29-CRYP", split_subjects=True)
    b = contest_key_of("KXTRUMPMENTION-26SEP29-TARI", split_subjects=True)
    assert a != b
    # without the split the appearance is one contest, as before
    assert contest_key_of("KXTRUMPMENTION-26SEP29-CRYP") == \
        contest_key_of("KXTRUMPMENTION-26SEP29-TARI")


def test_only_the_universe_and_tags_differ_from_the_predecessor():
    assert ws2.PROMOTION_GATE_SPEC is pred.OWNER_WAIVER_PROMOTION_SPEC
    assert ws2.KEEP_GATE_SPEC is pred.KEEP_GATE_SPEC
    assert ws2.BOOK_PARAMS.replace("+KXTRUMPMENTION", "") == pred.BOOK_PARAMS
    for key in ("contracts_per_order", "max_order_dollars", "max_market_exposure_usd",
                "max_open_positions", "total_canary_loss_budget_usd", "exit_policy"):
        assert ws2.RISK_ENVELOPE[key] == pred.RISK_ENVELOPE[key], key
    shared, pshared = ws2.RISK_ENVELOPE["shared_market"], pred.RISK_ENVELOPE["shared_market"]
    assert shared["max_combined_contracts"] == pshared["max_combined_contracts"]
    assert shared["max_combined_usd"] == pshared["max_combined_usd"]
    assert shared["partner_tag"] == pshared["partner_tag"] == "Kmmsell10"
    # the predecessor's objects are untouched by the copy
    assert pred.RISK_ENVELOPE["settings"]["MMSELL_LIVE_SHARED_MARKET_PAIRS"] == pred.SHARED_PAIR_ENV


def test_the_pair_swaps_the_predecessor_for_the_successor(settings):
    assert ws2.SHARED_PAIR_ENV == "Kmmsell10+Vmmsell10:6:6.00"
    settings.mmsell_live_shared_market_pairs = ws2.SHARED_PAIR_ENV
    (pair,) = settings.mmsell_live_shared_pairs
    assert pair.tags == frozenset({"Kmmsell10", ws2.LIVE_TAG})
    assert pair.env_value() == ws2.SHARED_PAIR_ENV


@pytest.mark.parametrize("new", [ws2.LIVE_TAG, ws2.PAPER_TAG, ws2.TWIN_TAG])
def test_new_tags_are_fresh_and_prefix_safe(new):
    existing = PRODUCTION_TAGS | {pred.LIVE_TAG, pred.TWIN_TAG, pred.PAPER_TAG}
    assert new not in existing
    for tag in existing:
        assert not tag.startswith(new), tag
        if new != ws2.TWIN_TAG:
            assert not new.startswith(tag), tag
    assert not ws2.PAPER_TAG.startswith(ws2.LIVE_TAG)
    assert ws2.TWIN_TAG == f"{ws2.LIVE_TAG}_pt4"


def test_the_package_is_registered_with_its_activation_vars():
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    pkg = _packages()["mmsell-win-set-2-canary"]
    assert pkg.experiment_key == ws2.EXPERIMENT_KEY
    assert pkg.register is ws2.register and pkg.arm is ws2.arm
    assert set(pkg.strategy_tags) == {ws2.PAPER_TAG, ws2.LIVE_TAG, ws2.TWIN_TAG}
    for name in pkg.activation_vars:
        assert name in railway_env.ALLOWED_VARS, name


def test_register_refuses_a_sample_floor():
    with pytest.raises(svc.ExperimentOsError):
        ws2.register(object(), actor="cal", promotion_sample_floor=1, now=T0)


def test_register_then_arm_end_to_end(xos_session, xos_platform):
    """Needs the predecessor; registers in PAPER on Qmmsell10 without ending anything, and
    arms at once under the owner-waiver bar with live and twin at one instant."""
    from kalshi_bot.experiment_os.read import get_experiment

    s = xos_session
    pred.register(s, actor="cal", now=T0 - timedelta(days=11))
    out = ws2.register(s, actor="cal", now=T0)
    assert out["experiment"].state == "PAPER"
    assert out["experiment"].predecessor_experiment_id == out["predecessor"].id
    assert out["paper_deployment"].deployment_key == ws2.PAPER_DEPLOYMENT_KEY
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        ws2.register(s, actor="cal", now=T0 + timedelta(minutes=1))
    armed = ws2.arm(s, approved_by="Calvin", started_at=T0 + timedelta(minutes=5))
    assert armed["live"].started_at == armed["twin"].started_at
    assert armed["live"].config_json["material"]["shared_market_pair"] == ws2.SHARED_PAIR_ENV
    assert get_experiment(s, ws2.EXPERIMENT_KEY).state == "LIVE_CANARY"
    # the predecessor is untouched by the successor's registration and arming
    assert get_experiment(s, pred.EXPERIMENT_KEY).state == "PAPER"
