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
    def __init__(self, events, books):
        self._events = events
        self._books = books

    def iter_events(self, **_kw):
        yield from self._events

    def get_orderbook(self, ticker, depth=None):
        return self._books[ticker]


def _events():
    return [
        {  # in-scope category; nested market is liquid + tight -> candidate
            "event_ticker": "FED-1",
            "category": "Economics",
            "title": "Fed rate decision",
            "close_time": "2030-01-01T00:00:00Z",
            "markets": [
                {
                    "ticker": "FED-1",
                    "title": "Fed rate above 3%",
                    "status": "active",
                    "volume": 5000,
                    "open_interest": 2000,
                    "close_time": "2030-01-01T00:00:00Z",
                    "rules_primary": "Resolves yes if ...",
                }
            ],
        },
        {  # wrong category, no keywords -> event skipped entirely
            "event_ticker": "WEATHER-1",
            "category": "Weather",
            "title": "Sunshine in Miami",
            "markets": [
                {
                    "ticker": "WEATHER-1",
                    "title": "Will it be sunny",
                    "status": "active",
                    "volume": 5000,
                    "open_interest": 2000,
                    "close_time": "2030-01-01T00:00:00Z",
                }
            ],
        },
        {  # in-scope category but nested market below volume floor -> filtered
            "event_ticker": "CPI-1",
            "category": "Economics",
            "title": "CPI inflation",
            "close_time": "2030-01-01T00:00:00Z",
            "markets": [
                {
                    "ticker": "CPI-2",
                    "title": "CPI inflation above 3%",
                    "status": "active",
                    "volume": 50,
                    "open_interest": 10,
                    "close_time": "2030-01-01T00:00:00Z",
                }
            ],
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
        FakeClient(_events(), _books()), settings, RiskManager(settings)
    )
    with db.session_scope() as session:
        summary = scanner.run_once(session, account_state={"cash_balance": 1000.0})

    assert summary.events_scanned == 3
    # Only the two Economics events' markets are examined (Weather is skipped).
    assert summary.markets_scanned == 2
    assert summary.targets_considered == 1  # FED-1 passes; CPI-2 below volume floor
    assert summary.filtered_low_volume == 1
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
