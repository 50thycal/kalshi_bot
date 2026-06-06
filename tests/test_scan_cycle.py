"""End-to-end scan cycle against a fake Kalshi client + sqlite.

Proves the worker's core behavior without real credentials: filtering, snapshot
persistence, signal scoring, risk evaluation, and the ranked candidate list.
"""

from __future__ import annotations

from sqlalchemy import func, select

from kalshi_bot import db
from kalshi_bot import models as m
from kalshi_bot.risk.manager import RiskManager
from kalshi_bot.scanner.scanner import MarketScanner


class FakeClient:
    def __init__(self, markets, books):
        self._markets = markets
        self._books = books

    def iter_markets(self, **_kw):
        yield from self._markets

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _markets():
    return [
        {  # in-scope, liquid, tight -> candidate
            "ticker": "FED-1",
            "title": "Fed rate above 3%",
            "category": "Economics",
            "status": "active",
            "volume": 5000,
            "open_interest": 2000,
            "close_time": "2030-01-01T00:00:00Z",
            "rules_primary": "Resolves yes if ...",
        },
        {  # wrong category, no keywords -> filtered before order book
            "ticker": "WEATHER-1",
            "title": "Will it be sunny",
            "category": "Weather",
            "status": "active",
            "volume": 5000,
            "open_interest": 2000,
            "close_time": "2030-01-01T00:00:00Z",
        },
        {  # in-scope but below volume floor -> filtered
            "ticker": "CPI-2",
            "title": "CPI inflation above 3%",
            "category": "Economics",
            "status": "active",
            "volume": 50,
            "open_interest": 10,
            "close_time": "2030-01-01T00:00:00Z",
        },
    ]


def _books():
    return {
        "FED-1": {"orderbook": {"yes": [[48, 300], [47, 200]], "no": [[49, 250], [48, 150]]}},
        "CPI-2": {"orderbook": {"yes": [[40, 10]], "no": [[40, 10]]}},
    }


def test_scan_cycle_persists_and_ranks(settings):
    db.init_engine(settings.database_url)
    db.create_all()

    scanner = MarketScanner(
        FakeClient(_markets(), _books()), settings, RiskManager(settings)
    )
    with db.session_scope() as session:
        summary = scanner.run_once(session, account_state={"cash_balance": 1000.0})

    assert summary.markets_scanned == 3
    assert summary.targets_considered == 1  # only FED-1 passes category + volume + OI
    assert summary.snapshots_written == 1
    assert summary.candidates_found == 1

    top = summary.candidates[0]
    assert top.ticker == "FED-1"
    assert top.label == "candidate"
    # Scanner mode -> risk manager blocks any real trade.
    assert top.risk_approved is False
    assert "MODE_NOT_LIVE" in top.risk_reasons

    with db.session_scope() as session:
        assert session.scalar(select(func.count()).select_from(m.MarketSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(m.OrderbookSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(m.Signal)) == 1
        assert session.scalar(select(func.count()).select_from(m.RiskEvent)) == 1
        snap = session.scalar(select(m.OrderbookSnapshot))
        assert snap.best_yes_ask == 51  # 100 - best_no_bid(49)


def test_db_roundtrip_smoke(settings):
    db.init_engine(settings.database_url)
    db.create_all()
    from kalshi_bot import repository as repo

    with db.session_scope() as session:
        run = repo.start_bot_run(session, "scanner")
        repo.finish_bot_run(
            session, run, status="completed", markets_scanned=0, candidates_found=0
        )
        assert run.id is not None

    with db.session_scope() as session:
        row = session.scalar(select(m.BotRun))
        assert row.status == "completed"
