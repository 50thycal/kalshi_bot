"""The joint restart of 2026-10-29: Kmmsell10 without sports and other categories
(`successor_mmsell10_cell_size_2`, live Xmmsell10) and the WIN-set book beside it
(`mmsell_win_set_3`, live Ymmsell10).

Pinned, in the order in which breaking it would cost money:

  * the new Kmmsell10 trades weather, index, politics and mention series and refuses sports
    and every other category, exactly the scorecard's four kept categories;
  * both books name the SAME pair, so neither reads as drifted when the env act sets it;
  * everything except the universe, tags and partner is the predecessor's object;
  * the tags are fresh and prefix-safe against every production tag;
  * register (and, for the WIN-set book, arm) runs end to end.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kalshi_bot.experiment_os import mmsell_win_set_2 as ws2
from kalshi_bot.experiment_os import mmsell_win_set_3 as ws3
from kalshi_bot.experiment_os import mmsell_win_set_canary as ws1
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_cell_size as cs
from kalshi_bot.experiment_os import successor_mmsell10_cell_size_2 as cs2
from kalshi_bot.mmsell.tracker import MmSellTracker, _live_eligible
from tests.test_mmsell_win_set_canary import PRODUCTION_TAGS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mmsell_scorecard as scorecard  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
LIVE_SINCE_TAGS = {"Nmmsell10", "Nmmsell10_pt4", "Pmmsell10", "Vmmsell10", "Vmmsell10_pt4",
                   "Qmmsell10"}


def _book(settings, spec):
    settings.mmsell_variants = spec
    (book,) = settings.mmsell_variant_list
    return book


@pytest.mark.parametrize("series", ["KXRAIN", "KXRAINNYC", "KXINX", "KXINXU", "KXNASDAQ100U",
                                    "KXTRUMPSAY", "KXTRUMPMENTION", "KXFEDMENTION",
                                    "KXTRUTHSOCIAL"])
def test_the_new_book_keeps_weather_index_politics_and_mentions(settings, series):
    assert MmSellTracker._book_admits_series(_book(settings, cs2.LIVE_BOOK_SPEC), series)


@pytest.mark.parametrize("series", ["KXMLBGAME", "KXNFLSPREAD", "KXMLBHR", "KXUCLTOTAL",
                                    "KXBTCD", "KXWTI", "KXCPI", "KXRT", "KXYTVIEWSHIGH",
                                    "KXSPACEXCOUNT"])
def test_the_new_book_refuses_sports_and_other(settings, series):
    assert not MmSellTracker._book_admits_series(_book(settings, cs2.LIVE_BOOK_SPEC), series)


def test_the_filter_is_the_scorecards_four_categories_over_every_live_series(settings):
    """No series live can trade falls on the wrong side: every graduated, unpaused series the
    book admits is in a kept category, and every one in a kept category is admitted."""
    from kalshi_bot.mmsell.market_types import SERIES_TYPES

    types = scorecard.load_series_types()
    book = _book(settings, cs2.LIVE_BOOK_SPEC)
    kept = {"weather", "index", "politics", "mentions"}
    for prefix, _kind, _mode in SERIES_TYPES:
        if not _live_eligible(prefix, settings.mmsell_live_skip_series_list,
                              settings.mmsell_live_min_tier):
            continue
        in_kept = scorecard.category(f"{prefix}-26OCT29-X", types) in kept
        assert MmSellTracker._book_admits_series(book, prefix) == in_kept, prefix


def test_only_the_universe_keys_move_against_the_predecessors_book():
    assert cs2.BOOK_PARAMS.startswith(cs.BOOK_PARAMS + ",")
    extra = cs2.BOOK_PARAMS[len(cs.BOOK_PARAMS) + 1:].split(",")
    assert [kv.split("=")[0] for kv in extra] == ["universe", "only"]


def test_the_gates_and_dollar_bounds_are_the_predecessors():
    assert cs2.PROMOTION_GATE_SPEC is cs.PROMOTION_GATE_SPEC
    keep = {k: v for k, v in cs2.KEEP_GATE_SPEC.items() if k != "description"}
    assert keep == {k: v for k, v in cs.KEEP_GATE_SPEC.items() if k != "description"}
    for key in ("max_order_dollars", "max_market_exposure_usd", "max_open_positions",
                "max_book_exposure_usd", "total_canary_loss_budget_usd",
                "daily_realized_loss_stop_usd", "contracts_per_order"):
        assert cs2.RISK_ENVELOPE[key] == cs.RISK_ENVELOPE[key], key
    settings = dict(cs2.RISK_ENVELOPE["settings"])
    assert settings.pop("MMSELL_LIVE_SHARED_MARKET_PAIRS") == cs2.SHARED_PAIR_ENV
    assert settings == cs.RISK_ENVELOPE["settings"]


def test_the_win_set_book_drops_only_the_world_cup_series():
    assert set(ws3.WIN_SERIES) == set(ws2.WIN_SERIES) - {"KXWCMENTION"}
    assert ws3.BOOK_PARAMS == ws2.BOOK_PARAMS.replace("+KXWCMENTION", "")
    assert ws3.PROMOTION_GATE_SPEC is ws2.PROMOTION_GATE_SPEC
    assert ws3.KEEP_GATE_SPEC is ws2.KEEP_GATE_SPEC
    for key in ("max_order_dollars", "max_market_exposure_usd", "total_canary_loss_budget_usd",
                "contracts_per_order"):
        assert ws3.RISK_ENVELOPE[key] == ws2.RISK_ENVELOPE[key], key


def test_both_books_register_the_same_pair(settings):
    assert cs2.SHARED_PAIR_ENV == ws3.SHARED_PAIR_ENV == "Xmmsell10+Ymmsell10:6:6.00"
    assert cs2.PARTNER_LIVE_TAG == ws3.LIVE_TAG
    assert cs2.material_config()["material"]["shared_market_pair"] == \
        ws3.material_config()["material"]["shared_market_pair"]
    settings.mmsell_live_shared_market_pairs = cs2.SHARED_PAIR_ENV
    for tag in (cs2.LIVE_TAG, ws3.LIVE_TAG):
        pair = settings.mmsell_live_shared_pair_for(tag)
        assert pair.tags == frozenset({cs2.LIVE_TAG, ws3.LIVE_TAG})
        assert pair.max_contracts == 6 and pair.max_usd == 6.0


@pytest.mark.parametrize("new", [cs2.LIVE_TAG, cs2.TWIN_TAG, cs2.PAPER_TAG,
                                 ws3.LIVE_TAG, ws3.TWIN_TAG, ws3.PAPER_TAG])
def test_new_tags_are_fresh_and_prefix_safe(new):
    existing = PRODUCTION_TAGS | LIVE_SINCE_TAGS
    assert new not in existing
    for tag in existing:
        assert not tag.startswith(new), tag
        if not new.endswith("_pt4"):
            assert not new.startswith(tag), tag
    assert len(new) <= 24


def test_the_packages_are_registered_with_allowlisted_activation_vars():
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    for name, mod in (("mmsell-cell-size-2-canary", cs2), ("mmsell-win-set-3-canary", ws3)):
        pkg = _packages()[name]
        assert pkg.experiment_key == mod.EXPERIMENT_KEY
        assert pkg.register is mod.register and pkg.arm is mod.arm
        assert set(pkg.strategy_tags) == {mod.PAPER_TAG, mod.LIVE_TAG, mod.TWIN_TAG}
        for var in pkg.activation_vars:
            assert var in railway_env.ALLOWED_VARS, var


@pytest.mark.parametrize("mod", [cs2, ws3])
def test_register_refuses_a_sample_floor(mod):
    with pytest.raises(svc.ExperimentOsError):
        mod.register(object(), actor="cal", promotion_sample_floor=1, now=T0)


def test_cell_size_2_registers_in_paper_and_ends_nothing(xos_session, xos_platform):
    from kalshi_bot.experiment_os.read import get_experiment

    s = xos_session
    pred = svc.create_experiment(s, key=cs.SUCCESSOR_KEY, origin="operator", now=T0)
    out = cs2.register(s, actor="cal", now=T0)
    assert out["experiment"].state == "PAPER"
    assert out["experiment"].predecessor_experiment_id == pred.id
    assert out["paper_deployment"].deployment_key == cs2.PAPER_DEPLOYMENT_KEY
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        cs2.register(s, actor="cal", now=T0 + timedelta(minutes=1))
    assert get_experiment(s, cs.SUCCESSOR_KEY).state == pred.state


def test_win_set_3_registers_then_arms_end_to_end(xos_session, xos_platform):
    from kalshi_bot.experiment_os.read import get_experiment

    s = xos_session
    ws1.register(s, actor="cal", now=T0 - timedelta(days=13))
    ws2.register(s, actor="cal", now=T0 - timedelta(days=2))
    out = ws3.register(s, actor="cal", now=T0)
    assert out["experiment"].state == "PAPER"
    armed = ws3.arm(s, approved_by="Calvin", started_at=T0 + timedelta(minutes=5))
    assert armed["live"].started_at == armed["twin"].started_at
    assert armed["live"].config_json["material"]["shared_market_pair"] == ws3.SHARED_PAIR_ENV
    assert get_experiment(s, ws3.EXPERIMENT_KEY).state == "LIVE_CANARY"
    assert get_experiment(s, ws2.EXPERIMENT_KEY).state == "PAPER"
