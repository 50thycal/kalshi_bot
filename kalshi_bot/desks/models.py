"""Isolated desk schema: never imports the bot or Experiment OS metadata.

Decision, publication, fill, settlement, and audit rows are immutable evidence.
Mutable execution rows are merely projections of that evidence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class DeskBase(DeclarativeBase):
    pass


class DeskRound(DeskBase):
    __tablename__ = "desk_rounds"
    round_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rules: Mapped[dict] = mapped_column(JSON)


class DeskBook(DeskBase):
    __tablename__ = "desk_books"
    __table_args__ = (UniqueConstraint("round_id", "desk_id"),)
    key: Mapped[str] = mapped_column(String(220), primary_key=True)
    round_id: Mapped[str] = mapped_column(ForeignKey("desk_rounds.round_id"))
    desk_id: Mapped[str] = mapped_column(String(20))
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    pause_reason: Mapped[str | None] = mapped_column(Text)
    subaccount: Mapped[int | None] = mapped_column(Integer)
    initial_cents: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    cash_cents: Mapped[Decimal] = mapped_column(Numeric(20, 8))


class DeskDecision(DeskBase):
    __tablename__ = "desk_decisions"
    decision_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    book_key: Mapped[str] = mapped_column(ForeignKey("desk_books.key"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


class DeskExecution(DeskBase):
    __tablename__ = "desk_executions"
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("desk_decisions.decision_id"), primary_key=True
    )
    book_key: Mapped[str] = mapped_column(ForeignKey("desk_books.key"), index=True)
    event_ticker: Mapped[str] = mapped_column(String(200), index=True)
    trading_day: Mapped[str] = mapped_column(String(10), index=True)
    first_fill_day: Mapped[str | None] = mapped_column(String(10), index=True)
    state: Mapped[str] = mapped_column(String(32))
    client_order_id: Mapped[str] = mapped_column(String(100), unique=True)
    exchange_order_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    reserved_cents: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    quantity: Mapped[int] = mapped_column(Integer)
    limit_price_cents: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    cost_cents: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    fee_cents: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=0)
    claim_token: Mapped[str | None] = mapped_column(String(80))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled: Mapped[bool] = mapped_column(Boolean, default=False)


class DeskFill(DeskBase):
    __tablename__ = "desk_fills"
    fill_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("desk_decisions.decision_id"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


class DeskSettlement(DeskBase):
    __tablename__ = "desk_settlements"
    decision_id: Mapped[str] = mapped_column(
        ForeignKey("desk_decisions.decision_id"), primary_key=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[str] = mapped_column(String(16))
    payout_cents: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    payload: Mapped[dict] = mapped_column(JSON)


class DeskPublication(DeskBase):
    __tablename__ = "desk_publications"
    publication_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    book_key: Mapped[str] = mapped_column(ForeignKey("desk_books.key"), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


class DeskAudit(DeskBase):
    __tablename__ = "desk_audit"
    audit_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    book_key: Mapped[str] = mapped_column(String(220), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


IMMUTABLE_TABLES = (DeskDecision, DeskFill, DeskSettlement, DeskPublication, DeskAudit)
