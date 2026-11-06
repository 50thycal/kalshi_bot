"""Pure helpers of the window probe (study 8): the live price rule and $10 sizing."""

import importlib.util
import os

_P = os.path.join(os.path.dirname(__file__), "..", "scripts", "limm_window_probe.py")
_spec = importlib.util.spec_from_file_location("limm_window_probe", _P)
wp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wp)


def test_price_rule_rests_one_tick_behind_a_reference_at_the_touch():
    assert wp.price_rule(5, 5) == 4
    assert wp.price_rule(5, 3) == 3          # already behind the touch
    assert wp.price_rule(1, 1) == 1          # nowhere behind 1c
    assert wp.price_rule(None, None) is None
    assert wp.price_rule(11, 11) is None     # outside the cheap band


def test_ten_dollar_sizing():
    assert wp.qty_for(4) == 250
    assert wp.qty_for(1) == wp.MAX_QTY


def test_cents_parses_dollar_strings_and_cent_ints():
    assert wp._cents("0.0500") == 5
    assert wp._cents(7) == 7
    assert wp._cents(None) is None
