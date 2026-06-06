from __future__ import annotations

from kalshi_bot.scanner.metrics import compute_metrics
from kalshi_bot.scanner.signals import score_market


def _market(**kw):
    base = {
        "ticker": "FED-X",
        "title": "Fed rate decision",
        "category": "Economics",
        "volume": 5000,
        "open_interest": 2000,
        "close_time": "2030-01-01T00:00:00Z",
        "rules_primary": "Resolves yes if ...",
    }
    base.update(kw)
    return base


def test_tight_liquid_market_is_candidate(settings):
    ob = {"orderbook": {"yes": [[48, 300], [47, 200]], "no": [[49, 250], [48, 150]]}}
    market = _market()
    metrics = compute_metrics(market, ob, top_n=settings.orderbook_depth)
    sig = score_market(metrics, market, settings=settings)
    assert sig.label == "candidate"
    assert sig.score >= 70
    assert sig.implied_probability == 0.495


def test_wide_spread_thin_market_not_candidate(settings):
    ob = {"orderbook": {"yes": [[20, 50]], "no": [[20, 50]]}}  # yes_ask=80, spread=60
    market = _market(volume=10, open_interest=5)
    metrics = compute_metrics(market, ob, top_n=settings.orderbook_depth)
    sig = score_market(metrics, market, settings=settings)
    assert sig.label in ("ignore", "watch")


def test_non_two_sided_is_ignored(settings):
    ob = {"orderbook": {"yes": [[48, 300]], "no": []}}
    market = _market()
    metrics = compute_metrics(market, ob, top_n=settings.orderbook_depth)
    sig = score_market(metrics, market, settings=settings)
    assert sig.label == "ignore"
    assert sig.implied_probability is None
