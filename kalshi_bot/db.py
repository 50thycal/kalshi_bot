"""Database engine/session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def init_engine(database_url: str, echo: bool = False) -> Engine:
    """(Re)initialize the global engine and session factory."""
    global _engine, _SessionFactory
    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    _engine = create_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        future=True,
        connect_args=connect_args,
    )
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialized; call init_engine() first.")
    return _engine


def create_all() -> None:
    """Create any missing tables. Alembic migrations remain the source of truth;
    this is a safety net for fresh/ephemeral environments and tests."""
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionFactory is None:
        raise RuntimeError("Session factory not initialized; call init_engine() first.")
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
