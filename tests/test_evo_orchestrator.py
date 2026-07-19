"""Regression test for a live production bug: one-time seeding (model prices,
graveyard, data sources) must survive a failure in a LATER phase of the same
orchestrator cycle.

What actually happened: seeding and founder-bootstrap shared one DB transaction,
and the in-memory `_bootstrapped` flag was set to True before that transaction
committed. When founder-bootstrap failed later in the same cycle, the whole
transaction rolled back (including the seeded rows) but the flag stayed True in
the running process — so seeding was never retried and evo_model_prices stayed
empty for the process's lifetime, degrading every subsequent heartbeat with
"no price configured". Fixed by giving seeding its own transaction and only
flipping the flag after it commits (kalshi_bot/evo/orchestrator.py)."""

from __future__ import annotations

from sqlalchemy import select

import kalshi_bot.db as db
from kalshi_bot.evo import orchestrator
from kalshi_bot.evo.models import EvoModelPrice
from kalshi_bot.models import Base


def _init_sqlite_engine():
    db.init_engine("sqlite://")
    Base.metadata.create_all(db.get_engine())


def test_seeding_survives_a_later_bootstrap_failure(monkeypatch):
    _init_sqlite_engine()
    runtime = orchestrator.EvoRuntime(kalshi_client=object())

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated founder-bootstrap failure")

    monkeypatch.setattr(orchestrator, "bootstrap_founders", _boom)

    try:
        orchestrator.run_evo_cycle(runtime)
    except RuntimeError:
        pass  # the later phase is expected to still raise; that's fine

    with db.session_scope() as session:
        prices = list(session.scalars(select(EvoModelPrice)))
    assert len(prices) == 2, "seeding must commit even though bootstrap_founders raised"
    assert runtime._bootstrapped is True


def test_seeding_retries_if_its_own_transaction_fails(monkeypatch):
    _init_sqlite_engine()
    runtime = orchestrator.EvoRuntime(kalshi_client=object())

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated seeding failure")

    monkeypatch.setattr(orchestrator, "seed_graveyard", _boom)
    monkeypatch.setattr(orchestrator, "bootstrap_founders", lambda *a, **k: [])

    try:
        orchestrator.run_evo_cycle(runtime)
    except RuntimeError:
        pass

    # the flag must NOT be set when seeding itself failed, so the next cycle retries
    assert runtime._bootstrapped is False
    with db.session_scope() as session:
        prices = list(session.scalars(select(EvoModelPrice)))
    assert prices == []
