from __future__ import annotations

from kalshi_bot.scanner.metrics import compute_metrics, parse_dt


def _market(**kw):
    base = {
        "ticker": "T",
        "volume": 1000,
        "open_interest": 500,
        "close_time": "2030-01-01T00:00:00Z",
        "last_price": 50,
    }
    base.update(kw)
    return base


def test_yes_ask_derived_from_best_no_bid():
    ob = {"orderbook": {"yes": [[48, 300], [47, 200]], "no": [[49, 250], [48, 150]]}}
    m = compute_metrics(_market(), ob)
    assert m.best_yes_bid == 48
    assert m.best_no_bid == 49
    assert m.best_yes_ask == 51  # 100 - best_no_bid
    assert m.best_no_ask == 52  # 100 - best_yes_bid
    assert m.spread == 3
    assert m.midpoint == 49.5
    assert m.two_sided is True
    assert m.top_depth == 900
    assert m.liquidity_score > 0


def test_one_sided_book_is_not_two_sided():
    m = compute_metrics(_market(), {"orderbook": {"yes": [[48, 300]], "no": []}})
    assert m.two_sided is False
    assert m.spread is None
    assert m.midpoint is None
    assert m.liquidity_score == 0.0


def test_empty_book():
    m = compute_metrics(_market(), {"orderbook": {"yes": [], "no": []}})
    assert m.two_sided is False
    assert m.top_depth == 0


def test_handles_bare_orderbook_without_wrapper():
    m = compute_metrics(_market(), {"yes": [[48, 10]], "no": [[49, 10]]})
    assert m.best_yes_ask == 51


def test_parse_dt_variants():
    assert parse_dt("2030-01-01T00:00:00Z") is not None
    assert parse_dt(1893456000) is not None
    assert parse_dt(None) is None
    assert parse_dt("not-a-date") is None
