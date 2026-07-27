"""explore_markets: on-demand live Kalshi market discovery (read-only), so agents can
find NEW domains beyond weather. Proves formatting/caps, safe degradation, and that a
scan resurfaces in the agent's next heartbeat."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kalshi_bot.evo import budgets, llm, market_explore
from kalshi_bot.evo import models as em
from kalshi_bot.evo.cognition import ACTION_PROTOCOL, ScriptedCognition, build_user_prompt
from kalshi_bot.evo.cohorts import ensure_current_cohort
from kalshi_bot.evo.config import EvoSettings
from kalshi_bot.evo.constitution import PERMITTED_ACTIONS
from kalshi_bot.evo.evolution import create_agent
from kalshi_bot.evo.heartbeats import run_heartbeat
from kalshi_bot.evo.marketdata import Quote, StaticMarketData, quote_from_kalshi
from kalshi_bot.models import Base

NOW = datetime.now(timezone.utc)


class _FakeMD:
    """Market data like LiveMarketData: list_markets returns bare discovery rows (the
    LIST endpoint carries no live prices), and get_quote returns the per-market quote
    that explore() enriches with. The T60000 strike is the more liquid one so we can
    assert most-liquid-first ordering."""

    def __init__(self, *, quotes: bool = True):
        self.calls: list[dict] = []
        self._quotes = quotes

    def get_quote(self, ticker):
        if not self._quotes:
            return None  # backend that can discover but not quote → bare rows
        liquid = ticker.endswith("T60000")
        return Quote(
            ticker=ticker, captured_at=NOW, status="active",
            yes_bid=40 if liquid else 22, yes_ask=44 if liquid else 26,
            volume=100 if liquid else 30, open_interest=200 if liquid else 60,
            last_price=42 if liquid else 24, event_ticker=ticker.rsplit("-", 1)[0],
            category="Crypto", title="BTC above strike", close_time=NOW + timedelta(hours=1),
        )

    def list_markets(self, **params):
        self.calls.append(params)
        series = params.get("series_ticker") or "KXBTCD"
        # bare rows, prices omitted — as the real /markets list endpoint returns them
        return [
            {"ticker": f"{series}-26JUL2317-T61000", "event_ticker": f"{series}-26JUL2317",
             "close_time": NOW + timedelta(hours=1), "status": "active"},
            {"ticker": f"{series}-26JUL2317-T60000", "event_ticker": f"{series}-26JUL2317",
             "close_time": NOW + timedelta(hours=1), "status": "active"},
        ]


def _session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)()


def test_explore_enriches_with_live_quote_and_leads_with_liquidity():
    res = market_explore.explore(_FakeMD(), series="KXBTCD", status="open", limit=10)
    assert res["count"] == 2 and res["series"] == "KXBTCD" and res["priced"] == 2
    row = res["markets"][0]
    assert set(row) == set(market_explore._FIELDS)  # stable whitelisted shape
    assert row["ticker"].startswith("KXBTCD-")
    assert isinstance(row["close_time"], str)  # datetime serialized
    # most-liquid-first: the T60000 strike (volume 100) leads the T61000 (volume 30)
    assert row["ticker"].endswith("T60000") and row["volume"] == 100
    assert res["markets"][1]["volume"] == 30
    # live-quote fields the LIST endpoint lacked are now filled from get_quote
    assert row["yes_bid"] == 40 and row["yes_ask"] == 44
    assert row["yes_mid"] == 42.0 and row["spread"] == 4
    assert row["open_interest"] == 200 and row["last_price"] == 42
    assert row["category"] == "Crypto" and row["title"] == "BTC above strike"


def test_explore_degrades_to_bare_rows_without_quotes():
    # a backend that can discover but not quote still returns tickers, just unpriced
    res = market_explore.explore(_FakeMD(quotes=False), series="KXBTCD")
    assert res["count"] == 2 and res["priced"] == 0
    row = res["markets"][0]
    assert row["ticker"].startswith("KXBTCD-")
    assert row["yes_bid"] is None and row["yes_mid"] is None


def test_quote_from_kalshi_parses_dollars_and_fp_fields():
    # the live elections API sends prices as '<f>_dollars' and counts as '<f>_fp' — with
    # no orderbook, quote_from_kalshi must still produce a priced quote from those.
    market = {
        "ticker": "KXHIGHNY-26JUL24-T88", "status": "active", "title": "NYC high >88",
        "yes_bid_dollars": "0.0000", "yes_ask_dollars": "0.0200",
        "no_bid_dollars": "0.9800", "no_ask_dollars": "1.0000",
        "volume_fp": "941.68", "open_interest_fp": "941.68", "last_price_dollars": "0.0100",
    }
    q = quote_from_kalshi(market, None)
    assert q.yes_bid == 0 and q.yes_ask == 2  # yes_ask derived from no_bid (100-98)
    assert q.mid == 1.0 and q.spread == 2
    assert q.volume == 942 and q.open_interest == 942 and q.last_price == 1


def test_quote_from_kalshi_still_reads_legacy_cents_fields():
    q = quote_from_kalshi(
        {"ticker": "X", "yes_bid": 40, "yes_ask": 44, "volume": 100, "open_interest": 7}, None
    )
    assert q.yes_bid == 40 and q.yes_ask == 44 and q.volume == 100 and q.open_interest == 7


def test_explore_passes_series_and_caps_limit():
    md = _FakeMD()
    market_explore.explore(md, series="KXHIGHNY", limit=999)
    assert md.calls[0]["series_ticker"] == "KXHIGHNY"
    assert md.calls[0]["max_markets"] == market_explore.MAX_LIMIT


def test_explore_tolerates_backend_without_list_markets():
    # StaticMarketData has no list_markets — must degrade to empty, not raise
    res = market_explore.explore(StaticMarketData(), series="KXBTCD")
    assert res == {"series": "KXBTCD", "status": "open", "count": 0,
                   "priced": 0, "markets": []}


def test_explore_markets_permitted_and_documented():
    assert "explore_markets" in PERMITTED_ACTIONS
    assert "explore_markets" in ACTION_PROTOCOL
    assert "KXBTCD" in ACTION_PROTOCOL  # a concrete non-weather example is shown


def _agent(s, settings):
    llm.seed_model_prices(s, settings)
    cohort = ensure_current_cohort(s, settings)
    agent = create_agent(s, settings, cohort, random.Random(1),
                         origin="founder", slot_key="founder:0")
    budgets.ensure_budgets(s, agent.agent_uuid, cohort.id, settings)
    return agent, cohort


def test_explore_markets_action_records_scan_and_surfaces_next_heartbeat():
    s = _session()
    settings = EvoSettings(_env_file=None)
    agent, cohort = _agent(s, settings)
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "discover"},
        "actions": [{"type": "explore_markets", "series": "KXBTCD"}],
    })
    hb = run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="x1", cognition=cog, md=_FakeMD())
    assert hb.actions_json[0]["ok"] and hb.actions_json[0]["count"] == 2
    run = s.scalar(select(em.EvoSandboxRun).where(em.EvoSandboxRun.kind == "market_scan"))
    assert run is not None and run.dataset == "KXBTCD"
    assert budgets.remaining(s, agent.agent_uuid, cohort.id, "market_scans") == (
        settings.weekly_market_scans - 1
    )
    seen: dict = {}
    run_heartbeat(
        s, settings, agent=agent, cohort=cohort, kind="routine", slot_id="x2",
        cognition=ScriptedCognition(lambda ctx: seen.__setitem__("p", build_user_prompt(ctx))
                                    or {"journal": {"decision": "ok"}, "actions": []}),
        md=_FakeMD(),
    )
    assert "YOUR RECENT MARKET SCANS" in seen["p"] and "KXBTCD-" in seen["p"]


def test_explore_markets_rejects_bad_status():
    s = _session()
    settings = EvoSettings(_env_file=None)
    agent, cohort = _agent(s, settings)
    cog = ScriptedCognition(lambda ctx: {
        "journal": {"decision": "x"},
        "actions": [{"type": "explore_markets", "series": "KXBTCD", "status": "bogus"}],
    })
    hb = run_heartbeat(s, settings, agent=agent, cohort=cohort, kind="routine",
                       slot_id="xbad", cognition=cog, md=_FakeMD())
    assert "rejected" in hb.actions_json[0] and "status must be" in hb.actions_json[0]["rejected"]


# The EXACT orderbook payload the live Kalshi elections API returned for
# KXHIGHNY-26JUL27-B85.5, captured by scripts/evo_order_probe.py. Pinned verbatim:
# the previous parser looked for an "orderbook" envelope containing "yes"/"no",
# so against this real shape both level lists parsed to [] on EVERY market —
# Quote.taker_levels() was always empty and paper.evaluate_order could not fill
# ANY order at ANY price. Quotes still looked healthy (top-of-book prices come
# from the market object), so nothing surfaced it: 39 orders, zero fills, ever.
_LIVE_ORDERBOOK_B85_5 = {
    "orderbook_fp": {
        "no_dollars": [["0.8100", "1.00"], ["0.8300", "3.00"], ["0.8400", "53.00"],
                       ["0.8500", "25.00"], ["0.8600", "35.00"], ["0.8800", "10.05"],
                       ["0.9000", "135.00"], ["0.9100", "72.18"], ["0.9200", "129.00"],
                       ["0.9300", "52.00"]],
        "yes_dollars": [["0.0100", "1530.00"], ["0.0200", "381.00"], ["0.0300", "74.00"],
                        ["0.0400", "20.00"], ["0.0500", "31.00"], ["0.0600", "6.00"]],
    }
}


def test_quote_from_kalshi_parses_the_live_orderbook_fp_shape():
    market = {"ticker": "KXHIGHNY-26JUL27-B85.5", "status": "active",
              "yes_bid_dollars": "0.0600", "yes_ask_dollars": "0.0700",
              "no_bid_dollars": "0.9300"}
    q = quote_from_kalshi(market, _LIVE_ORDERBOOK_B85_5)

    # levels populate, best-first, with dollar prices converted to cents
    assert q.no_levels[0] == (93, 52)
    assert q.yes_levels[0] == (6, 6)
    # fractional sizes floor to whole contracts ("10.05" -> 10, "72.18" -> 72)
    assert (88, 10) in q.no_levels and (91, 72) in q.no_levels
    # and the derived taker cost matches the market's own quoted ask (100-93=7)
    assert q.best_taker_price("yes") == 7 == q.yes_ask
