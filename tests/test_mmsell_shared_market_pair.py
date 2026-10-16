"""The scoped shared-market PAIR exception (WS-027 phase 2, docs/MMSELL_WIN_SET_CANARY.md).

Acceptance checks pinned here:

  2. With the pair list EMPTY every live test passes unchanged and XOS-000040's double clip
     is refused — and with a pair listed, any tag outside it is still refused.
  3. With the pair listed both tags can hold one market, and combined contracts never
     exceed the registered combined bound (the boundary is tested on both sides, in
     contracts and in dollars, counting the partner's still-working order).
  4. A closeout on a shared market never exceeds the selling tag's own filled quantity.

Holdings are seeded the way production records them: a filled NO-buy order, its fill
(`side=no, action=sell`, `count_fp`), and an account position snapshot.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot import repository as repo
from kalshi_bot.config import SharedMarketPair
from kalshi_bot.live.executor import LiveExecutor
from kalshi_bot.risk.manager import RiskManager
from tests.test_mmsell_closeout import FakeCloseoutClient
from tests.test_mmsell_live import FakeLiveClient, _metrics

TICKER = "KXRAIN-26OCT20-NYC"
PAIR = "Kmmsell10+Nmmsell10:6:6.00"
_SEQ = itertools.count()


def _settings(settings, **over):
    settings.bot_mode = "live"
    settings.mmsell_live_min_tier = "unclassified"
    settings.kill_switch = False
    settings.live_enabled = True
    settings.live_strategies = "Kmmsell10,Nmmsell10,Jmmsell10"
    settings.live_max_order_dollars = 3.0
    settings.max_order_size = 100
    settings.max_market_exposure = 25.0
    settings.mmsell_live_max_open_positions = 60
    settings.mmsell_live_price_offset_cents = 0
    settings.mmsell_live_max_spread_cents = 40
    for k, v in over.items():
        setattr(settings, k, v)
    db.init_engine(settings.database_url)
    db.create_all()
    return settings


def _hold(session, tag, contracts, *, price=93, status="filled", fill=True, ticker=TICKER):
    """One order for `tag`; filled orders get a production-shaped fill."""
    oid = f"K-held-{next(_SEQ)}"
    repo.create_live_order(
        session, signal_id=None, ticker=ticker, event_ticker="KXRAIN-26OCT20",
        strategy=tag, side="no", action="buy", limit_price=price, quantity=int(contracts),
        status=status, client_order_id=f"{tag}:{oid}", raw_order_json={})
    row = session.query(m.LiveOrder).order_by(m.LiveOrder.id.desc()).first()
    row.kalshi_order_id = oid
    if fill:
        session.add(m.Fill(kalshi_fill_id=f"f-{oid}", kalshi_order_id=oid,
                           market_ticker=ticker, side="no", action="sell", price=price,
                           quantity=int(round(contracts)), fee=0,
                           raw_fill_json={"count_fp": f"{contracts:.2f}"}))
    session.flush()


def _account(session, contracts, *, ticker=TICKER):
    session.add(m.Position(
        market_ticker=ticker, captured_at=datetime.now(timezone.utc), side="no",
        quantity=-int(contracts), quantity_fp=-float(contracts), avg_price=93.0,
        market_exposure=contracts * 0.93, realized_pnl=None, raw_json=None))
    session.flush()


def _enter(settings, session, client, tag, *, contracts=3):
    ex = LiveExecutor(client, settings, RiskManager(settings))
    return ex.mirror_mmsell_entry(
        session, strategy=tag, event_ticker="KXRAIN-26OCT20", ticker=TICKER,
        metrics=_metrics(), no_price=92, account_state={"cash_balance": 500.0},
        max_contracts=contracts)


# ---------------------------------------------------------------------------
# The setting: fail-closed parsing
# ---------------------------------------------------------------------------


def test_default_is_empty(settings):
    assert settings.mmsell_live_shared_market_pairs == ""
    assert settings.mmsell_live_shared_pairs == ()
    assert settings.mmsell_live_shared_pair_for("Kmmsell10") is None


def test_a_pair_parses_and_round_trips(settings):
    settings.mmsell_live_shared_market_pairs = PAIR
    (pair,) = settings.mmsell_live_shared_pairs
    assert pair == SharedMarketPair(frozenset({"Kmmsell10", "Nmmsell10"}), 6, 6.0)
    assert pair.env_value() == PAIR
    assert settings.mmsell_live_shared_pair_for("Nmmsell10") == pair
    # EXACT tags, never the allowlist's prefix match
    assert settings.mmsell_live_shared_pair_for("Kmmsell10x") is None


@pytest.mark.parametrize("raw", [
    "Kmmsell10+Nmmsell10",              # no bound
    "Kmmsell10+Nmmsell10:6",            # no dollar bound
    "Kmmsell10:6:6.00",                 # one tag
    "Kmmsell10+Kmmsell10:6:6.00",       # same tag twice
    "Kmmsell10+Nmmsell10:0:6.00",       # zero contracts
    "Kmmsell10+Nmmsell10:6:0",          # zero dollars
    "Kmmsell10+Nmmsell10:six:6.00",     # junk
    "Kmmsell10+Nmmsell10+Jmmsell10:9:9.00",  # three tags
    "Kmmsell10+Kmmsell10_pt4:6:6.00",   # a paper twin can never be a live partner
])
def test_a_malformed_entry_is_dropped_so_the_stricter_gate_stays(settings, raw):
    settings.live_paper_twin_suffix = "_pt4"
    settings.live_strategies = "Kmmsell10"
    settings.mmsell_live_shared_market_pairs = raw
    assert settings.mmsell_live_shared_pairs == ()


def test_a_tag_named_by_two_pairs_drops_both(settings):
    settings.mmsell_live_shared_market_pairs = (
        "Kmmsell10+Nmmsell10:6:6.00;Kmmsell10+Jmmsell10:6:6.00")
    assert settings.mmsell_live_shared_pairs == ()


# ---------------------------------------------------------------------------
# Acceptance check 2 — empty list, or a tag outside the pair: XOS-000040 refused
# ---------------------------------------------------------------------------


def test_xos000040_double_clip_is_refused_with_no_pair_listed(settings):
    _settings(settings)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _account(session, 3)
        assert _enter(settings, session, client, "Nmmsell10") == "gate:held"
    assert client.placed == []


def test_xos000040_is_refused_for_a_pair_that_is_not_the_listed_one(settings):
    """The 2026-10-04 shape exactly: Hmmsell10 holds a 3-lot, Jmmsell10 wants a 3-lot. A
    listed K+N pair grants J nothing."""
    _settings(settings, mmsell_live_shared_market_pairs=PAIR,
              live_strategies="Hmmsell10,Jmmsell10,Kmmsell10,Nmmsell10")
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Hmmsell10", 3)
        _account(session, 3)
        assert _enter(settings, session, client, "Jmmsell10") == "gate:held"
        assert _enter(settings, session, client, "Nmmsell10") == "gate:held"
    assert client.placed == []


def test_a_resting_order_outside_the_pair_still_blocks(settings):
    _settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Jmmsell10", 1, status="resting", fill=False)
        assert _enter(settings, session, client, "Nmmsell10") == "gate:dedup"


# ---------------------------------------------------------------------------
# Acceptance check 3 — the listed pair shares, inside the combined bound
# ---------------------------------------------------------------------------


def test_the_listed_pair_can_hold_one_market_to_the_bound(settings):
    _settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _account(session, 3)
        assert _enter(settings, session, client, "Nmmsell10", contracts=3) == "placed"
    assert len(client.placed) == 1
    assert client.placed[0]["count"] == "3.00"


def test_one_contract_over_the_bound_is_refused(settings):
    _settings(settings, mmsell_live_shared_market_pairs="Kmmsell10+Nmmsell10:5:6.00")
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _account(session, 3)
        assert _enter(settings, session, client, "Nmmsell10", contracts=3) \
            == "gate:shared_bound"
        assert _enter(settings, session, client, "Nmmsell10", contracts=2) == "placed"
    assert [o["count"] for o in client.placed] == ["2.00"]


def test_the_dollar_bound_binds_independently(settings):
    # 3 held at 93c = $2.79, plus 3 at 92c = $2.76 -> $5.55, over a $5.50 ceiling
    _settings(settings, mmsell_live_shared_market_pairs="Kmmsell10+Nmmsell10:6:5.50")
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _account(session, 3)
        assert _enter(settings, session, client, "Nmmsell10") == "gate:shared_bound"
    assert client.placed == []


def test_the_partners_still_working_order_counts_toward_the_bound(settings):
    """Both books may rest at the same no-bid — and an unfilled order is still a
    commitment the bound must count, or two resting 3-lots become a 6-lot and then a 9."""
    _settings(settings, mmsell_live_shared_market_pairs="Kmmsell10+Nmmsell10:5:9.00")
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3, status="resting", fill=False)
        assert _enter(settings, session, client, "Nmmsell10", contracts=3) \
            == "gate:shared_bound"
        assert _enter(settings, session, client, "Nmmsell10", contracts=2) == "placed"


def test_the_partners_resting_order_does_not_block_entry(settings):
    _settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3, status="resting", fill=False)
        assert _enter(settings, session, client, "Nmmsell10") == "placed"


def test_a_tag_never_stacks_on_its_own_holding(settings):
    _settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Nmmsell10", 1, status="canceled")   # partial fill, then cancelled
        _account(session, 1)
        assert _enter(settings, session, client, "Nmmsell10") == "gate:held"


def test_contracts_no_pair_order_explains_still_refuse(settings):
    """The account holds 5 but the pair's fills explain 3: someone else's contracts."""
    _settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _account(session, 5)
        assert _enter(settings, session, client, "Nmmsell10") == "gate:held"


def test_a_third_holder_refuses_even_the_listed_pair(settings):
    _settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _hold(session, "Jmmsell10", 1)
        _account(session, 4)
        assert _enter(settings, session, client, "Nmmsell10") == "gate:held"


@pytest.mark.parametrize("over,code", [
    ({"kill_switch": True}, "gate:switches"),
    ({"mmsell_live_max_open_positions": 0}, "gate:open_cap"),
    ({"mmsell_live_max_spread_cents": 1}, "gate:spread"),
    ({"max_market_exposure": 1.0}, "gate:exposure"),
])
def test_every_other_gate_still_binds_on_the_pair(settings, over, code):
    _settings(settings, mmsell_live_shared_market_pairs=PAIR, **over)
    client = FakeLiveClient()
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _account(session, 3)
        assert _enter(settings, session, client, "Nmmsell10") == code
    assert client.placed == []


# ---------------------------------------------------------------------------
# Acceptance check 4 — closeout on a shared market is reduce-only by tag
# ---------------------------------------------------------------------------


def _closeout_settings(settings, **over):
    _settings(settings, mmsell_closeout_enabled=True, mmsell_closeout_strategies="Nmmsell10",
              mmsell_closeout_slippage_cents=3, **over)
    return settings


def test_a_shared_closeout_closes_only_the_selling_tags_own_contracts(settings):
    _closeout_settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeCloseoutClient(yes_ask=10)
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _hold(session, "Nmmsell10", 2)
        _account(session, 5)
        assert ex.close_mmsell_positions(session) == 1
    (order,) = client.placed
    assert order["count"] == "2.00", "must never sell the partner's three"
    assert order["client_order_id"].startswith(f"closeout:Nmmsell10:{TICKER}:")


def test_a_shared_closeout_is_skipped_without_the_pair(settings):
    _closeout_settings(settings)
    client = FakeCloseoutClient(yes_ask=10)
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _hold(session, "Nmmsell10", 2)
        _account(session, 5)
        assert ex.close_mmsell_positions(session) == 0
    assert client.placed == []


def test_a_shared_closeout_with_a_third_holder_is_skipped(settings):
    _closeout_settings(settings, mmsell_live_shared_market_pairs=PAIR)
    client = FakeCloseoutClient(yes_ask=10)
    ex = LiveExecutor(client, settings, RiskManager(settings))
    with db.session_scope() as session:
        _hold(session, "Kmmsell10", 3)
        _hold(session, "Nmmsell10", 2)
        _hold(session, "Jmmsell10", 1)
        _account(session, 6)
        assert ex.close_mmsell_positions(session) == 0
