"""The paper shadow's database bound, against REAL PostgreSQL.

A mock cannot exhibit the behaviour that matters here. A PostgreSQL statement timeout **aborts
the transaction**: after it fires, every later statement on that connection fails with
`InFailedSqlTransaction` until someone rolls back. Catching the exception is not enough, and no
fake `load_spot_closes` that raises will ever show it. The shadow shares the trading loop's
session, so getting this wrong means a research query can stop the book from writing.

Skipped without a Postgres `DATABASE_URL`; CI provides one (`.github/workflows/ci.yml`).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from kalshi_bot import repository as repo

_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _URL.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")),
    reason="needs a real PostgreSQL DATABASE_URL; a mock cannot abort a transaction",
)


@pytest.fixture
def session():
    engine = create_engine(_URL.replace("postgresql://", "postgresql+psycopg://", 1),
                           future=True)
    with Session(engine) as s:
        # SQLAlchemy autobegins on first use; an explicit begin() would raise here.
        s.execute(text("SELECT 1"))
        yield s
        s.rollback()
    engine.dispose()


def _sleep(session, seconds: float = 3.0):
    session.execute(text(f"SELECT pg_sleep({seconds})"))


class TestTransactionSurvivesTheTimeout:
    def test_a_timed_out_shadow_read_leaves_the_outer_transaction_usable(self, session):
        """THE test. Without the savepoint the assertion after the timeout raises
        InFailedSqlTransaction, and in production that statement is a paper-trade write."""
        # The trading loop has already done work in this transaction.
        session.execute(text("SELECT 1"))

        with pytest.raises(Exception) as exc:                     # noqa: PT011 — driver-specific
            with repo.bounded_statement(session, 50):
                _sleep(session)
        assert "timeout" in str(exc.value).lower() or "cancel" in str(exc.value).lower()

        # The trading loop continues. This is the line that fails without the savepoint.
        assert session.execute(text("SELECT 42")).scalar() == 42
        assert session.execute(text("SELECT 7")).scalar() == 7

    def test_the_timeout_does_not_leak_to_the_rest_of_the_transaction(self, session):
        """`SET LOCAL` survives a savepoint RELEASE and, without one, survives to the end of the
        transaction. Either way the shadow's research budget would silently become a timeout on
        the trading loop's own queries for the rest of the cycle."""
        before = session.execute(text("SHOW statement_timeout")).scalar()
        with repo.bounded_statement(session, 50):
            session.execute(text("SELECT 1"))                     # succeeds inside the bound

        assert session.execute(text("SHOW statement_timeout")).scalar() == before
        # A query far longer than the shadow's bound must now be allowed to run.
        _sleep(session, 0.3)
        assert session.execute(text("SELECT 1")).scalar() == 1

    def test_an_unbounded_call_is_a_plain_passthrough(self, session):
        for timeout in (None, 0, -1):
            with repo.bounded_statement(session, timeout):
                assert session.execute(text("SELECT 1")).scalar() == 1
        assert session.execute(text("SELECT 1")).scalar() == 1

    def test_two_bounded_reads_in_a_row_both_recover(self, session):
        for _ in range(2):
            with pytest.raises(Exception):                        # noqa: PT011, B017
                with repo.bounded_statement(session, 50):
                    _sleep(session)
            assert session.execute(text("SELECT 1")).scalar() == 1


class TestTheShadowLoadIsBounded:
    def test_load_spot_closes_routes_its_timeout_through_the_savepoint(self, session,
                                                                       monkeypatch):
        """The loader must not hold its own `SET LOCAL`; it must hand the timeout to the helper
        that knows how to unwind an abort."""
        import datetime as dt

        seen = {}
        real = repo.bounded_statement

        def spy(sess, timeout_ms):
            seen["timeout"] = timeout_ms
            return real(sess, timeout_ms)

        monkeypatch.setattr(repo, "bounded_statement", spy)
        repo.load_spot_closes(session, "BTC",
                              dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                              statement_timeout_ms=750)
        assert seen["timeout"] == 750
        assert session.execute(text("SELECT 1")).scalar() == 1

    def test_the_loader_is_unbounded_when_no_timeout_is_given(self, session, monkeypatch):
        import datetime as dt

        seen = {}
        real = repo.bounded_statement
        monkeypatch.setattr(repo, "bounded_statement",
                            lambda sess, t: (seen.__setitem__("timeout", t), real(sess, t))[1])
        repo.load_spot_closes(session, "BTC",
                              dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
        assert seen["timeout"] is None


class TestCumulativeAuthorisationIsBounded:
    """The tracker side, exercised against the real session so the two halves are proved
    together: each load authorises only the remainder, and the total cannot exceed the budget."""

    def test_successive_loads_authorise_only_what_is_left(self, session, monkeypatch, settings):
        from kalshi_bot.theta import tracker as trk
        from kalshi_bot.theta.tracker import ThetaTracker

        budget = 400.0
        settings.theta_spliced_budget_ms = budget
        tracker = ThetaTracker(object(), settings, spot_client=object())

        seen: list[tuple[float, int | None]] = []
        real_load = repo.load_spot_closes

        def spy(sess, product, since, *, statement_timeout_ms=None):
            seen.append((tracker._shadow_ms, statement_timeout_ms))
            # Burn real wall-clock inside a bounded read, exactly as a slow load would.
            with repo.bounded_statement(sess, statement_timeout_ms):
                sess.execute(text("SELECT pg_sleep(0.05)"))
            return real_load(sess, product, since)

        monkeypatch.setattr(trk.repo, "load_spot_closes", spy, raising=False)
        tracker._refresh_shadow_spot(session, "BTC")
        tracker._refresh_shadow_spot(session, "ETH")

        assert len(seen) == 2
        for spent, authorised in seen:
            assert spent + authorised <= budget + 1e-6, "a load authorised more than remained"
        assert seen[1][1] < seen[0][1], "the second load saw a smaller remainder"
        assert session.execute(text("SELECT 1")).scalar() == 1
