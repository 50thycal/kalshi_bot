from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture(scope="session")
def rsa_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return key, pem


@pytest.fixture
def base_env(rsa_keypair, monkeypatch, tmp_path):
    _, pem = rsa_keypair
    monkeypatch.setenv("KALSHI_ENV", "demo")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", pem)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    from kalshi_bot.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(base_env):
    from kalshi_bot.config import Settings

    return Settings(_env_file=None)


# --- evolutionary agent system (kalshi_bot/evo) fixtures ---


@pytest.fixture
def evo_session():
    """In-memory sqlite session over the full schema (legacy + evo tables)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from kalshi_bot.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture
def evo_settings():
    from kalshi_bot.evo.config import EvoSettings

    return EvoSettings(_env_file=None, fill_latency_ms=0)


@pytest.fixture
def evo_agent(evo_session, evo_settings):
    """One founder agent joined to the current cohort, with default genomes,
    budgets and both ledgers. Returns (agent, cohort)."""
    import random

    from kalshi_bot.evo import paper
    from kalshi_bot.evo.cohorts import ensure_current_cohort
    from kalshi_bot.evo.evolution import create_agent

    cohort = ensure_current_cohort(evo_session, evo_settings)
    agent = create_agent(
        evo_session, evo_settings, cohort, random.Random(7),
        origin="founder", slot_key="founder:test",
    )
    paper.ensure_portfolio(
        evo_session, agent.agent_uuid, paper.LIFETIME, evo_settings.starting_capital_usd
    )
    return agent, cohort
