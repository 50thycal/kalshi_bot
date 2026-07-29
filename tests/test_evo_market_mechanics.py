"""The durable domain layer: how these markets and this simulator actually work.

Before this existed, agents received a constitution (governance) and an action
protocol (call syntax) and nothing at all about the instrument they trade —
everything they knew about Kalshi came from the base model's pretraining or from
getting an order rejected. Every mistake seen live was a domain gap, not a
schema gap: selling to open a short, limit_price_cents=0, a bare series prefix
as a ticker, and uniform sizing across a 96c favorite and a 74c bet.

The risk with a primer is drift: if it disagrees with the simulator it teaches
agents to mispredict their own fills, and nothing would catch that. These tests
tie the stated numbers back to the implementation they describe.
"""

from __future__ import annotations

from kalshi_bot.evo.cognition import MARKET_MECHANICS, system_blocks
from kalshi_bot.evo.config import EvoSettings


def test_mechanics_reach_the_agent_in_the_cached_system_prompt():
    blocks = system_blocks(EvoSettings(_env_file=None))
    text = "".join(b["text"] for b in blocks)
    assert "HOW THESE MARKETS WORK" in text
    assert "WHAT YOU CAN DO" in text
    # it must ride in the cache-controlled block, not be re-sent uncached
    assert any(b.get("cache_control") for b in blocks)


def _flat(text: str) -> str:
    """Collapse prose line-wrapping so a content assertion doesn't break just
    because a phrase happened to straddle a newline."""
    return " ".join(text.split())


def test_covers_every_mistake_the_fleet_actually_made():
    """Each assertion maps to a real rejected order or a real losing trade."""
    m = _flat(MARKET_MECHANICS)
    # tried to open a short with action="sell"
    assert "THERE IS NO SHORTING" in m
    assert "BUY the other side" in m
    # wrote limit_price_cents=0 six times in one heartbeat
    assert "no 0c and no 100c" in m
    # submitted a bare series prefix as a ticker
    assert "is a series, not a market" in m
    # bought 25 lots at 96c and 25 lots at 74c with identical sizing
    assert "risks P to win (100-P)" in m
    assert "uniform position size" in m.lower()


def test_fee_formula_matches_the_engine():
    """The primer states ceil(0.07 * contracts * P * (1-P)). If the engine's fee
    model changes, this must change with it."""
    import inspect

    from kalshi_bot.paper.engine import kalshi_fee

    assert "0.07" in MARKET_MECHANICS
    src = inspect.getsource(kalshi_fee)
    assert "0.07" in src, "engine fee rate changed — update MARKET_MECHANICS"
    # and the stated shape is right: highest at 50c, ~0 at the extremes
    assert kalshi_fee(50, 100) > kalshi_fee(5, 100)
    assert kalshi_fee(50, 100) > kalshi_fee(95, 100)


def test_maker_haircut_matches_configured_default():
    """The primer tells agents makers forfeit 25% on a trade-through fill."""
    assert "25%" in MARKET_MECHANICS
    assert EvoSettings(_env_file=None).maker_adverse_selection_haircut == 0.25


def test_settlement_values_match_the_paper_engine():
    """100c winner / 0c loser is stated as absolute. paper.mark_and_settle books
    resolved_value_cents = 100 if won else 0 — keep them in agreement."""
    import inspect

    from kalshi_bot.evo import paper

    assert "100c per contract" in _flat(MARKET_MECHANICS)
    src = inspect.getsource(paper.mark_and_settle)
    assert "100 if won else 0" in src


def test_capability_map_lists_only_real_actions_and_sources():
    """A primer that advertises something the agent cannot actually call is
    worse than silence — it burns heartbeats on rejected actions."""
    from kalshi_bot.evo.constitution import PERMITTED_ACTIONS
    from kalshi_bot.evo.data_access import SOURCES
    from kalshi_bot.evo.sandbox import DATASETS

    for action in ("explore_markets", "inspect_data", "run_backtest",
                   "save_strategy", "activate_strategy", "submit_trade_intent",
                   "create_listener", "register_experiment", "conclude_experiment",
                   "revise_belief", "note_episode", "revise_cognitive_genome",
                   "revise_trading_genome"):
        assert action in _flat(MARKET_MECHANICS), action
        assert action in PERMITTED_ACTIONS, f"{action} advertised but not permitted"

    for dataset in DATASETS:
        assert dataset in _flat(MARKET_MECHANICS), f"backtest dataset {dataset} not advertised"
    for source in SOURCES:
        assert source in _flat(MARKET_MECHANICS), f"inspect_data source {source} not advertised"


def test_exit_semantics_are_stated_durably_not_only_as_an_announcement():
    """Announcements expire and are capped; how exits work is permanent. An
    ad-hoc position never closes itself — that must always be in front of the
    agent, not only for the 21 days an announcement happens to be live."""
    assert "Nothing will ever close it for you" in _flat(MARKET_MECHANICS)
    assert "tp_sl" in MARKET_MECHANICS


def test_primer_stays_within_a_sane_prompt_budget():
    """It rides in EVERY heartbeat. Tiers 1/2 route to a backend without prompt
    caching, so this is real input cost on every call — keep it a primer, not a
    textbook."""
    approx_tokens = len(MARKET_MECHANICS) // 3
    assert approx_tokens < 1400, f"~{approx_tokens} tokens is too long for every call"
