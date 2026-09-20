"""Durable desk-only research jobs, source provenance, and resource reservations."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .models import DeskBase


class ResearchJob(DeskBase):
    __tablename__ = "desk_research_jobs"
    job_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    desk_id: Mapped[str] = mapped_column(String(20), index=True)
    round_id: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24), index=True)
    claim_token: Mapped[str | None] = mapped_column(String(80))
    worker_id: Mapped[str | None] = mapped_column(String(200))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0)
    actual_microusd: Mapped[int | None] = mapped_column(Integer)
    budget_month: Mapped[str | None] = mapped_column(String(7))


class ResearchBudget(DeskBase):
    __tablename__ = "desk_research_budgets"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    limit_microusd: Mapped[int] = mapped_column(Integer)
    committed_microusd: Mapped[int] = mapped_column(Integer, default=0)


class ResearchSource(DeskBase):
    __tablename__ = "desk_research_sources"
    source_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(160), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


class ResearchBoard(DeskBase):
    __tablename__ = "desk_research_board"
    round_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text)
    pages_seen: Mapped[int] = mapped_column(Integer, default=0)
    completed_passes: Mapped[int] = mapped_column(Integer, default=0)
    bucket: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    claim_token: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchMarket(DeskBase):
    __tablename__ = "desk_research_markets"
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    round_id: Mapped[str] = mapped_column(String(80), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)
