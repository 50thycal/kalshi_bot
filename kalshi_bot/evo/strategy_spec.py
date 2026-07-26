"""Declarative trading-strategy specification DSL (spec §15).

Design decision (documented in docs/EVOLUTIONARY_AGENT_SYSTEM.md §14): agent
strategies are typed DATA interpreted by vetted engine code — never agent-authored
Python. Isolation, credential/network bans, resource bounds, static + interface
validation, versioning and reproducibility hold by construction.

A spec:
  universe   — which markets (series prefixes / categories / liquidity floors)
  entry      — approved condition operators over market metrics + edge vs a
               reference price, side selection, order style, sizing
  exit       — settlement | tp_sl | timed
  risk       — concurrent positions, per-event cap, cost caps

The same interpreter runs live paper trading (orchestrator), probes and backtests
(sandbox.py), so paper and backtest behavior can never diverge."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError, field_validator

from .marketdata import Quote

# Condition metric vocabulary (subset of listener metrics that exist on a Quote /
# candle-derived quote — keeps backtests and live evaluation identical).
METRICS = (
    "yes_bid", "yes_ask", "no_bid", "no_ask", "spread", "mid", "last_price",
    "volume", "open_interest", "hours_to_close",
)
OPS = ("<", "<=", ">", ">=", "==", "!=")


class Condition(BaseModel):
    model_config = {"extra": "forbid"}
    metric: str
    op: str
    value: float

    @field_validator("metric")
    @classmethod
    def _metric(cls, v: str) -> str:
        if v not in METRICS:
            raise ValueError(f"unknown metric {v!r} (approved: {METRICS})")
        return v

    @field_validator("op")
    @classmethod
    def _op(cls, v: str) -> str:
        if v not in OPS:
            raise ValueError(f"unknown op {v!r}")
        return v


class UniverseSpec(BaseModel):
    model_config = {"extra": "forbid"}
    series_prefixes: list[str] = Field(default_factory=list, max_length=24)
    exclude_series_prefixes: list[str] = Field(default_factory=list, max_length=24)
    categories: list[str] = Field(default_factory=list, max_length=12)
    min_volume: float = Field(default=0.0, ge=0)
    max_spread_cents: int = Field(default=99, ge=1, le=99)
    min_hours_to_close: float = Field(default=0.0, ge=0)
    max_hours_to_close: float = Field(default=24 * 30, ge=0)

    def admits_ticker(self, ticker: str) -> bool:
        t = ticker.upper()
        if any(t.startswith(p.upper()) for p in self.exclude_series_prefixes):
            return False
        if self.series_prefixes:
            return any(t.startswith(p.upper()) for p in self.series_prefixes)
        return True


class EntrySpec(BaseModel):
    model_config = {"extra": "forbid"}
    side: str = "yes"  # yes | no | cheap | expensive
    style: str = "taker"  # taker | maker
    conditions: list[Condition] = Field(default_factory=list, max_length=8)
    # price band on the ENTRY price the agent would pay (its own cost basis)
    min_price_cents: int = Field(default=1, ge=1, le=99)
    max_price_cents: int = Field(default=99, ge=1, le=99)
    maker_offset_cents: int = Field(default=0, ge=0, le=10)  # rest this far inside the bid
    size_contracts: int = Field(default=5, ge=1, le=500)

    @field_validator("side")
    @classmethod
    def _side(cls, v: str) -> str:
        if v not in ("yes", "no", "cheap", "expensive"):
            raise ValueError("side must be yes|no|cheap|expensive")
        return v

    @field_validator("style")
    @classmethod
    def _style(cls, v: str) -> str:
        if v not in ("taker", "maker"):
            raise ValueError("style must be taker|maker")
        return v


class ExitSpec(BaseModel):
    model_config = {"extra": "forbid"}
    mode: str = "settlement"  # settlement | tp_sl | timed
    take_profit_cents: int | None = Field(default=None, ge=1, le=99)
    stop_loss_cents: int | None = Field(default=None, ge=1, le=99)
    max_hold_hours: float | None = Field(default=None, gt=0)

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in ("settlement", "tp_sl", "timed"):
            raise ValueError("mode must be settlement|tp_sl|timed")
        return v


class RiskSpec(BaseModel):
    model_config = {"extra": "forbid"}
    max_concurrent_positions: int = Field(default=10, ge=1, le=200)
    max_per_event: int = Field(default=1, ge=1, le=10)
    max_cost_per_position_usd: float = Field(default=50.0, gt=0, le=1000)


class StrategySpec(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=3, max_length=48)
    family: str = Field(default="unassigned", max_length=48)
    description: str = Field(default="", max_length=2000)
    universe: UniverseSpec = Field(default_factory=UniverseSpec)
    entry: EntrySpec = Field(default_factory=EntrySpec)
    exit: ExitSpec = Field(default_factory=ExitSpec)
    risk: RiskSpec = Field(default_factory=RiskSpec)


_SECTION_MODELS = {"universe": UniverseSpec, "entry": EntrySpec, "exit": ExitSpec, "risk": RiskSpec}
# field name -> the ONE section it actually belongs to (no collisions across
# sections in this schema, so a bare name uniquely identifies its home).
_FIELD_HOME = {
    field: section for section, model in _SECTION_MODELS.items() for field in model.model_fields
}


def _hint_misplaced_fields(exc: ValidationError) -> str:
    """Pydantic's raw message names the rejected field but never says WHERE it
    actually belongs. Seen live, two variants of the same mistake: a field valid
    on a DIFFERENT section (e.g. max_spread_cents submitted under "entry" when
    it's a universe field — intuitive, since a spread filter reads like an
    entry-time decision even though the schema treats it as a universe
    pre-filter concept; or the reverse, entry's own max_price_cents submitted
    under "universe"), and a whole section nested one level too deep (e.g.
    {"entry": {"universe": {...}}} instead of "universe" as its own top-level
    key next to "entry"). Append a concrete pointer for either, so the
    rejection is something the agent can act on next attempt."""
    hints = []
    for err in exc.errors():
        if err.get("type") != "extra_forbidden":
            continue
        loc = err.get("loc", ())
        if len(loc) < 2:
            continue
        section, field = str(loc[0]), str(loc[-1])
        home = _FIELD_HOME.get(field)
        if home and home != section:
            hints.append(f"{field!r} belongs under {home!r}, not {section!r}")
        elif field in _SECTION_MODELS and len(loc) == 2:
            hints.append(
                f"{field!r} is a top-level spec section — put it next to "
                f"{section!r}, not nested inside it"
            )
    base = str(exc)[:1500]
    return f"{base}\nHINT: " + "; ".join(hints) if hints else base


def validate_spec(doc: dict, *, max_bytes: int = 40_000) -> tuple[StrategySpec | None, str | None]:
    try:
        raw = json.dumps(doc)
    except (TypeError, ValueError):
        return None, "spec is not JSON-serializable"
    if len(raw) > max_bytes:
        return None, f"spec too large ({len(raw)} bytes > {max_bytes})"
    try:
        return StrategySpec.model_validate(doc), None
    except ValidationError as exc:
        return None, _hint_misplaced_fields(exc)


# ---------------------------------------------------------------------------
# Interpretation (shared by live paper trading and backtests)
# ---------------------------------------------------------------------------


def _metric_value(quote: Quote, metric: str) -> float | None:
    if metric == "spread":
        return None if quote.spread is None else float(quote.spread)
    if metric == "mid":
        return quote.mid
    if metric == "hours_to_close":
        return quote.hours_to_close()
    v = getattr(quote, metric, None)
    return None if v is None else float(v)


_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def resolve_side(spec: StrategySpec, quote: Quote) -> str | None:
    """yes/no directly; 'cheap' = the side with the lower taker cost; 'expensive'
    the higher. None when quotes are missing."""
    if spec.entry.side in ("yes", "no"):
        return spec.entry.side
    yes_cost = quote.best_taker_price("yes")
    no_cost = quote.best_taker_price("no")
    if yes_cost is None or no_cost is None:
        return None
    if spec.entry.side == "cheap":
        return "yes" if yes_cost <= no_cost else "no"
    return "yes" if yes_cost > no_cost else "no"


def entry_signal(spec: StrategySpec, quote: Quote) -> dict | None:
    """Evaluate the entry against a quote. Returns an intent dict
    {side, style, price_cents(limit), quantity} or None. Missing data => None
    (fail closed)."""
    if quote.is_terminal() or not spec.universe.admits_ticker(quote.ticker):
        return None
    htc = quote.hours_to_close()
    if htc is None or not (
        spec.universe.min_hours_to_close <= htc <= spec.universe.max_hours_to_close
    ):
        return None
    if quote.volume is not None and quote.volume < spec.universe.min_volume:
        return None
    if quote.spread is not None and quote.spread > spec.universe.max_spread_cents:
        return None
    for cond in spec.entry.conditions:
        value = _metric_value(quote, cond.metric)
        if value is None or not _OPS[cond.op](value, cond.value):
            return None
    side = resolve_side(spec, quote)
    if side is None:
        return None
    if spec.entry.style == "taker":
        price = quote.best_taker_price(side)
        if price is None:
            return None
        limit = min(99, price)  # marketable-limit at the displayed level
    else:
        # maker: rest inside the side's own bid by the offset (never pay through)
        own_bid = quote.best_exit_bid(side)
        if own_bid is None:
            return None
        limit = min(99, max(1, own_bid + spec.entry.maker_offset_cents))
        ask = quote.best_taker_price(side)
        if ask is not None:
            limit = min(limit, max(1, ask - 1))
        price = limit
    if not (spec.entry.min_price_cents <= price <= spec.entry.max_price_cents):
        return None
    return {
        "side": side,
        "style": spec.entry.style,
        "limit_price_cents": limit,
        "quantity": spec.entry.size_contracts,
    }


def exit_signal(
    spec: StrategySpec, quote: Quote, *, side: str, entry_price_cents: float,
    held_hours: float,
) -> str | None:
    """None = hold; else a reason string ('tp'|'sl'|'timed'). Settlement itself is
    handled by the paper engine's mark_and_settle."""
    if spec.exit.mode == "settlement":
        return None
    bid = quote.best_exit_bid(side)
    if bid is None:
        return None
    gain = bid - entry_price_cents
    if spec.exit.mode == "tp_sl":
        if spec.exit.take_profit_cents is not None and gain >= spec.exit.take_profit_cents:
            return "tp"
        if spec.exit.stop_loss_cents is not None and gain <= -spec.exit.stop_loss_cents:
            return "sl"
        return None
    if spec.exit.max_hold_hours is not None and held_hours >= spec.exit.max_hold_hours:
        return "timed"
    return None
