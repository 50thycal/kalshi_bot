"""Versioned, provider-independent desk contracts. Money is always Decimal dollars."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DeskId = Literal["chatgpt", "claude"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Evidence(Contract):
    url: str = Field(min_length=8, max_length=2048)
    retrieved_at: datetime
    excerpt: str = Field(min_length=1, max_length=12000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Decision(Contract):
    decision_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,100}$")
    desk_id: DeskId
    round_id: str = Field(min_length=1, max_length=100)
    ticker: str = Field(min_length=1, max_length=200)
    event_id: str = Field(min_length=1, max_length=200)
    side: Literal["yes", "no"]
    observed_price: Decimal = Field(gt=0, lt=1)
    quote_at: datetime
    max_price: Decimal = Field(gt=0, lt=1)
    max_spend: Decimal = Field(default=Decimal("1.00"), gt=0, le=1)
    probability: Decimal = Field(gt=0, lt=1)
    probability_low: Decimal = Field(ge=0, le=1)
    probability_high: Decimal = Field(ge=0, le=1)
    expected_net_profit: Decimal = Field(gt=0, le=100)
    settlement_source: str = Field(min_length=8, max_length=2048)
    settlement_rule: str = Field(min_length=10, max_length=12000)
    rules_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    thesis: str = Field(min_length=10, max_length=12000)
    counterargument: str = Field(min_length=10, max_length=12000)
    invalidation: str = Field(min_length=5, max_length=4000)
    edge_class: Literal["mechanics", "information", "base_rate", "structure", "liquidity"]
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    created_at: datetime
    expires_at: datetime
    author_model: str = Field(min_length=1, max_length=200)
    origin: Literal["session", "scheduled"] = "session"
    borrowed_from: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def consistent(self):
        dates = [self.created_at, self.quote_at, self.expires_at]
        dates.extend(e.retrieved_at for e in self.evidence)
        if any(d.tzinfo is None for d in dates):
            raise ValueError("timestamps must include a timezone")
        if not self.probability_low <= self.probability <= self.probability_high:
            raise ValueError("probability must lie in its uncertainty interval")
        if self.expires_at <= self.created_at or self.quote_at > self.created_at:
            raise ValueError("invalid decision time ordering")
        if any(e.retrieved_at > self.created_at for e in self.evidence):
            raise ValueError("evidence must exist before the decision")
        return self


class Quote(Contract):
    ticker: str
    event_id: str
    side: Literal["yes", "no"]
    ask: Decimal = Field(gt=0, lt=1)
    available_quantity: int = Field(ge=0)
    fetched_at: datetime
    closes_at: datetime
    rules_sha256: str
    fee_rate: Decimal = Field(ge=0, le=1)
    tick_size: Decimal = Field(default=Decimal("0.01"), gt=0, le=1)
    status: str = "open"


class OrderReport(Contract):
    client_order_id: str
    order_id: str | None = None
    status: Literal["pending", "unknown", "terminal"]
    filled_quantity: Decimal = Field(default=Decimal(0), ge=0)
    fill_cost: Decimal = Field(default=Decimal(0), ge=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)
    observed_at: datetime


class Settlement(Contract):
    ticker: str
    yes_payout: Decimal = Field(ge=0, le=1)
    settled_at: datetime
    source: str


class DeskError(ValueError):
    """A classified refusal safe to show to an authenticated operator."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)
