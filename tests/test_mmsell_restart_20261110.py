"""The politics-free restart of 2026-11-10: Xmmsell10 without politics
(`successor_mmsell10_cell_size_3`, live Ummsell10) and the WIN-set book beside it
(`mmsell_win_set_4`, live Ommsell10).

Pinned, in the order in which breaking it would cost money:

  * the new cell-size book trades weather, index and mention series (KXTRUMPMENTION included)
    and refuses every politics series, exactly the scorecard's three kept categories;
  * the WIN-set book drops KXTRUMPSAY and nothing else;
  * both books name the SAME pair, so neither reads as drifted when the env act sets it;
  * everything except the universe, tags and partner is the predecessor's object;
  * the tags are fresh and prefix-safe against every production tag;
  * register (and arm) runs end to end.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kalshi_bot.experiment_os import mmsell_win_set_2 as ws2
from kalshi_bot.experiment_os import mmsell_win_set_3 as ws3
from kalshi_bot.experiment_os import mmsell_win_set_4 as ws4
from kalshi_bot.experiment_os import mmsell_win_set_canary as ws1
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.experiment_os import successor_mmsell10_cell_size as cs
from kalshi_bot.experiment_os import successor_mmsell10_cell_size_2 as cs2
from kalshi_bot.experiment_os import successor_mmsell10_cell_size_3 as cs3
from kalshi_bot.mmsell.tracker import MmSellTracker, _live_eligible
from tests.test_mmsell_restart_20261029 import LIVE_SINCE_TAGS as LIVE_SINCE_1029
from tests.test_mmsell_win_set_canary import PRODUCTION_TAGS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mmsell_scorecard as scorecard  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)
#: Every tag that has traded or been registered since PRODUCTION_TAGS was pinned (ops
#: `tags-1110`, `tags3-1110`).
LIVE_SINCE_TAGS = LIVE_SINCE_1029 | {
    "Basemmsell10", "Smmsell10", "Xmmsell10", "Xmmsell10_pt4", "Ymmsell10", "Ymmsell10_pt4",
    "Zmmsell10", "limm1", "freeze2", "freeze3", "freeze4",
}


def _book(settings, spec):
    settings.mmsell_variants = spec
    (book,) = settings.mmsell_variant_list
    return book


@pytest.mark.parametrize("series", ["KXRAIN", "KXRAINNYC", "KXINX", "KXINXU",
                                    "KXNASDAQ100U", "KXTRUMPMENTION", "KXFEDMENTION"])
def test_the_new_book_keeps_weather_index_and_mentions(settings, series):
    assert MmSellTracker._book_admits_series(_book(settings, cs3.LIVE_BOOK_SPEC), series)


@pytest.mark.parametrize("series", ["KXTRUMPSAY", "KXTRUTHSOCIAL", "KXMLBGAME", "KXBTCD",
                                    "KXWTI", "KXCPI"])
def test_the_new_book_refuses_politics_sports_and_other(settings, series):
    assert not MmSellTracker._book_admits_series(_book(settings, cs3.LIVE_BOOK_SPEC), series)


def test_the_filter_is_the_scorecards_three_categories_over_every_live_series(settings):
    """No series live can trade falls on the wrong side: every graduated, unpaused series the
    book admits is weather, index or mentions, and every one of those is admitted."""
    from kalshi_bot.mmsell.market_types import SERIES_TYPES

    types = scorecard.load_series_types()
    book = _book(settings, cs3.LIVE_BOOK_SPEC)
    kept = {"weather", "index", "mentions"}
    for prefix, _kind, _mode in SERIES_TYPES:
        if not _live_eligible(prefix, settings.mmsell_live_skip_series_list,
                              settings.mmsell_live_min_tier):
            continue
        in_kept = scorecard.category(f"{prefix}-26NOV10-X", types) in kept
        assert MmSellTracker._book_admits_series(book, prefix) == in_kept, prefix


def test_only_the_only_list_moves_against_the_predecessors_book():
    head, _, only = cs3.BOOK_PARAMS.partition(",only=")
    pred_head, _, pred_only = cs2.BOOK_PARAMS.partition(",only=")
    assert head == pred_head
    assert set(pred_only.split("+")) - set(only.split("+")) == {"KXTRUMP", "KXTRUTHSOCIAL"}
    assert set(only.split("+")) <= set(pred_only.split("+"))


def test_the_gates_and_dollar_bounds_are_the_predecessors():
    assert cs3.PROMOTION_GATE_SPEC is cs2.PROMOTION_GATE_SPEC is cs.PROMOTION_GATE_SPEC
    keep = {k: v for k, v in cs3.KEEP_GATE_SPEC.items() if k != "description"}
    assert keep == {k: v for k, v in cs2.KEEP_GATE_SPEC.items() if k != "description"}
    for key in ("max_order_dollars", "max_market_exposure_usd", "max_open_positions",
                "max_book_exposure_usd", "total_canary_loss_budget_usd",
                "daily_realized_loss_stop_usd", "contracts_per_order"):
        assert cs3.RISK_ENVELOPE[key] == cs2.RISK_ENVELOPE[key], key
    settings = dict(cs3.RISK_ENVELOPE["settings"])
    assert settings.pop("MMSELL_LIVE_SHARED_MARKET_PAIRS") == cs3.SHARED_PAIR_ENV
    pred_settings = dict(cs2.RISK_ENVELOPE["settings"])
    pred_settings.pop("MMSELL_LIVE_SHARED_MARKET_PAIRS")
    assert settings == pred_settings


def test_the_win_set_book_drops_only_the_trump_says_series():
    assert set(ws4.WIN_SERIES) == set(ws3.WIN_SERIES) - {"KXTRUMPSAY"}
    assert ws4.BOOK_PARAMS == ws3.BOOK_PARAMS.replace("+KXTRUMPSAY", "")
    assert ws4.PROMOTION_GATE_SPEC is ws3.PROMOTION_GATE_SPEC
    assert ws4.KEEP_GATE_SPEC is ws3.KEEP_GATE_SPEC
    for key in ("max_order_dollars", "max_market_exposure_usd", "total_canary_loss_budget_usd",
                "contracts_per_order"):
        assert ws4.RISK_ENVELOPE[key] == ws3.RISK_ENVELOPE[key], key


def test_both_books_register_the_same_pair(settings):
    assert cs3.SHARED_PAIR_ENV == ws4.SHARED_PAIR_ENV == "Ommsell10+Ummsell10:6:6.00"
    assert cs3.PARTNER_LIVE_TAG == ws4.LIVE_TAG
    assert cs3.material_config()["material"]["shared_market_pair"] == \
        ws4.material_config()["material"]["shared_market_pair"]
    settings.mmsell_live_shared_market_pairs = cs3.SHARED_PAIR_ENV
    for tag in (cs3.LIVE_TAG, ws4.LIVE_TAG):
        pair = settings.mmsell_live_shared_pair_for(tag)
        assert pair.tags == frozenset({cs3.LIVE_TAG, ws4.LIVE_TAG})
        assert pair.max_contracts == 6 and pair.max_usd == 6.0


@pytest.mark.parametrize("new", [cs3.LIVE_TAG, cs3.TWIN_TAG, cs3.PAPER_TAG,
                                 ws4.LIVE_TAG, ws4.TWIN_TAG, ws4.PAPER_TAG])
def test_new_tags_are_fresh_and_prefix_safe(new):
    existing = PRODUCTION_TAGS | LIVE_SINCE_TAGS
    assert new not in existing
    for tag in existing:
        assert not tag.startswith(new), tag
        if not new.endswith("_pt4"):
            assert not new.startswith(tag), tag
    assert len(new) <= 24


def test_the_new_books_lead_the_scorecard():
    assert scorecard.LIVE_TAGS[:2] == (cs3.LIVE_TAG, ws4.LIVE_TAG)


def test_the_packages_are_registered_with_allowlisted_activation_vars():
    from kalshi_bot.experiment_os.experiment_commands import _packages
    from scripts import railway_env

    for name, mod in (("mmsell-cell-size-3-canary", cs3), ("mmsell-win-set-4-canary", ws4)):
        pkg = _packages()[name]
        assert pkg.experiment_key == mod.EXPERIMENT_KEY
        assert pkg.register is mod.register and pkg.arm is mod.arm
        assert set(pkg.strategy_tags) == {mod.PAPER_TAG, mod.LIVE_TAG, mod.TWIN_TAG}
        for var in pkg.activation_vars:
            assert var in railway_env.ALLOWED_VARS, var


@pytest.mark.parametrize("mod", [cs3, ws4])
def test_register_refuses_a_sample_floor(mod):
    with pytest.raises(svc.ExperimentOsError):
        mod.register(object(), actor="cal", promotion_sample_floor=1, now=T0)


def test_cell_size_3_registers_in_paper_and_ends_nothing(xos_session, xos_platform):
    from kalshi_bot.experiment_os.read import get_experiment

    s = xos_session
    pred = svc.create_experiment(s, key=cs2.EXPERIMENT_KEY, origin="operator", now=T0)
    out = cs3.register(s, actor="cal", now=T0)
    assert out["experiment"].state == "PAPER"
    assert out["experiment"].predecessor_experiment_id == pred.id
    assert out["paper_deployment"].deployment_key == cs3.PAPER_DEPLOYMENT_KEY
    with pytest.raises(svc.ExperimentOsError, match="already exists"):
        cs3.register(s, actor="cal", now=T0 + timedelta(minutes=1))
    assert get_experiment(s, cs2.EXPERIMENT_KEY).state == pred.state


def test_win_set_4_registers_then_arms_end_to_end(xos_session, xos_platform):
    from kalshi_bot.experiment_os.read import get_experiment

    s = xos_session
    ws1.register(s, actor="cal", now=T0 - timedelta(days=25))
    ws2.register(s, actor="cal", now=T0 - timedelta(days=14))
    ws3.register(s, actor="cal", now=T0 - timedelta(days=12))
    out = ws4.register(s, actor="cal", now=T0)
    assert out["experiment"].state == "PAPER"
    armed = ws4.arm(s, approved_by="Calvin", started_at=T0 + timedelta(minutes=5))
    assert armed["live"].started_at == armed["twin"].started_at
    assert armed["live"].config_json["material"]["shared_market_pair"] == ws4.SHARED_PAIR_ENV
    assert get_experiment(s, ws4.EXPERIMENT_KEY).state == "LIVE_CANARY"
    assert get_experiment(s, ws3.EXPERIMENT_KEY).state == "PAPER"
