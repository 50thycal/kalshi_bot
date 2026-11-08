"""Simulated fills for a resting shadow order — three explicitly-labelled fill models.

Paper fills are dangerous (the handoff's words, and this repository's own history:
`docs/MMSELL_FILL_MODEL.md`). Nothing here assumes an order at a touched price fills. Each
shadow leg is replayed against the real public trade tape and the real order-book deltas
under three models whose results are reported SEPARATELY and never averaged:

  optimistic   — a trade printed at our price or through it, on our side: full fill.
  conservative — a trade at our price fills us only after the visible depth that was ahead of
                 us AT PLACEMENT has been consumed by trades at that price (cancellations ahead
                 of us are ignored, so this model under-fills). Partial fills allowed.
  queue_aware  — like conservative, but the contracts ahead of us shrink when the level's
                 resting quantity shrinks below them (a cancel or a fill ahead of us), as the
                 execution telemetry's local book reconstructs it. Partial fills allowed.
  whole_order  — the paper strategies' stress model (DEC-044, lead 1), OPT-IN per leg: the
                 queue-aware model's timing, but the first trade that reaches us fills the
                 ENTIRE remaining order. Live evidence: 24 of 30 fills on orders of 50+
                 contracts took 100% of the order, so a partial-fill model understates the
                 cost of a large bid. Never applied to the two-sided policy pairs.

Price convention: legs carry their level on the YES scale (`LocalBook` convention) so a trade
frame — `yes_price_cents` + `taker_outcome_side` — can be matched without conversion:

  our YES bid at yes-price y is a maker on the YES side; it is hit when a taker SELLS yes,
  i.e. `taker_outcome_side == "no"`, at yes price <= y (a print below y means the taker
  swept past our level, which by price priority means our level was cleared first).
  our NO bid at no-price n rests at yes-price 100-n on the NO side; it is hit when a taker
  BUYS yes (`taker_outcome_side == "yes"`) at yes price >= 100-n.

The models run on in-memory state; fills, marks and outcomes are persisted. The raw replay
inputs (`incentive_book_events`, `incentive_shadow_events`) are OFF by default since 2026-10-07
(see config) — turn them back on before building a model that must be replayed over a tape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

MODEL_OPTIMISTIC = "optimistic"
MODEL_CONSERVATIVE = "conservative"
MODEL_QUEUE_AWARE = "queue_aware"
MODEL_WHOLE_ORDER = "whole_order"
FILL_MODELS = (MODEL_OPTIMISTIC, MODEL_CONSERVATIVE, MODEL_QUEUE_AWARE)
#: The one-sided paper arms carry the whole-order stress model as well (DEC-044).
ARM_FILL_MODELS = FILL_MODELS + (MODEL_WHOLE_ORDER,)

SIDE_YES = "yes"
SIDE_NO = "no"


@dataclass
class ShadowLeg:
    """One resting side of a shadow quote pair, simulated under every fill model at once."""

    side: str                      # "yes" | "no" — the side our bid rests on
    price_yes_scale: int           # our level on the YES scale
    quantity: float                # contracts we would rest
    placed_at: datetime
    queue_ahead_at_placement: float   # resting contracts at our level when we would have joined
    models: tuple[str, ...] = FILL_MODELS   # which fill models this leg is simulated under
    filled: dict[str, float] = field(default_factory=dict)   # model -> contracts filled so far
    first_fill_at: dict[str, datetime] = field(default_factory=dict)
    full_fill_at: dict[str, datetime] = field(default_factory=dict)
    volume_through: float = 0.0    # contracts traded at/through our level on our side since placement
    _ahead_conservative: float = field(default=0.0, init=False)
    _ahead_queue: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        for model in self.models:
            self.filled.setdefault(model, 0.0)
        self._ahead_conservative = max(0.0, float(self.queue_ahead_at_placement))
        self._ahead_queue = max(0.0, float(self.queue_ahead_at_placement))

    # -- queries -----------------------------------------------------------------------------
    def remaining(self, model: str) -> float:
        return max(0.0, self.quantity - self.filled.get(model, 0.0))

    def is_full(self, model: str) -> bool:
        return self.remaining(model) <= 1e-9

    def queue_ahead(self, model: str) -> float:
        if model == MODEL_CONSERVATIVE:
            return self._ahead_conservative
        if model in (MODEL_QUEUE_AWARE, MODEL_WHOLE_ORDER):
            return self._ahead_queue
        return 0.0

    # -- events ------------------------------------------------------------------------------
    def trade_hits_us(self, yes_price_cents: int | None, taker_outcome_side: str | None) -> bool:
        """Does a public trade at this yes price, with this taker side, reach our level?"""
        if yes_price_cents is None or taker_outcome_side not in (SIDE_YES, SIDE_NO):
            return False
        if self.side == SIDE_YES:
            return taker_outcome_side == SIDE_NO and yes_price_cents <= self.price_yes_scale
        return taker_outcome_side == SIDE_YES and yes_price_cents >= self.price_yes_scale

    def on_trade(self, *, yes_price_cents: int | None, count: float | None,
                 taker_outcome_side: str | None, at: datetime) -> dict[str, float]:
        """Apply one public trade. Returns {model: contracts newly filled} (empty when none)."""
        if not self.trade_hits_us(yes_price_cents, taker_outcome_side) or not count or count <= 0:
            return {}
        count = float(count)
        self.volume_through += count
        newly: dict[str, float] = {}
        # Optimistic: touched or crossed -> everything we have left fills.
        got = self._fill(MODEL_OPTIMISTIC, self.remaining(MODEL_OPTIMISTIC), at)
        if got:
            newly[MODEL_OPTIMISTIC] = got
        # Conservative / queue-aware: the print at our exact level (or through it) consumes what
        # is ahead first. A print THROUGH our level (deeper than us) means our level was already
        # exhausted by price priority — treat it as clearing the queue ahead entirely.
        through = yes_price_cents != self.price_yes_scale
        reached = False          # did this print get past the queue-aware queue to us?
        for model in (MODEL_CONSERVATIVE, MODEL_QUEUE_AWARE):
            ahead = self.queue_ahead(model)
            if through:
                consumed_ahead, spill = ahead, count
            else:
                consumed_ahead = min(ahead, count)
                spill = count - consumed_ahead
            self._set_ahead(model, ahead - consumed_ahead)
            if model == MODEL_QUEUE_AWARE:
                reached = spill > 0
            if spill > 0:
                got = self._fill(model, min(spill, self.remaining(model)), at)
                if got:
                    newly[model] = got
        # Whole order: same queue as queue-aware (it was just advanced), but reaching us at all
        # takes everything left.
        if MODEL_WHOLE_ORDER in self.models and reached:
            got = self._fill(MODEL_WHOLE_ORDER, self.remaining(MODEL_WHOLE_ORDER), at)
            if got:
                newly[MODEL_WHOLE_ORDER] = got
        return newly

    def on_level_quantity(self, level_qty_after: float | None) -> None:
        """The book's resting quantity at our level changed (delta stream). For the queue-aware
        model only: if the level now holds fewer contracts than we thought were ahead of us, the
        difference left the queue ahead of us (cancelled or filled)."""
        if level_qty_after is None:
            return
        if level_qty_after < self._ahead_queue:
            self._ahead_queue = max(0.0, float(level_qty_after))

    # -- internals ---------------------------------------------------------------------------
    def _set_ahead(self, model: str, value: float) -> None:
        value = max(0.0, value)
        if model == MODEL_CONSERVATIVE:
            self._ahead_conservative = value
        elif model == MODEL_QUEUE_AWARE:
            self._ahead_queue = value

    def _fill(self, model: str, qty: float, at: datetime) -> float:
        if qty <= 1e-9:
            return 0.0
        before = self.filled.get(model, 0.0)
        self.filled[model] = before + qty
        if before <= 1e-9:
            self.first_fill_at[model] = at
        if self.is_full(model):
            self.full_fill_at[model] = at
        return qty


# Outcome labels for a quote pair under one fill model (the handoff's "essential outcomes").
OUTCOME_NONE = "neither_filled"
OUTCOME_YES_ONLY = "yes_only"
OUTCOME_NO_ONLY = "no_only"
OUTCOME_BOTH = "both_filled"
OUTCOME_PARTIAL_YES = "partial_yes"
OUTCOME_PARTIAL_NO = "partial_no"
OUTCOME_PARTIAL_BOTH = "partial_both"


def pair_outcome(yes_leg: ShadowLeg, no_leg: ShadowLeg, model: str) -> str:
    """Classify the pair's fill state under one model. 'partial' means some but not all of a
    leg; 'yes_only'/'no_only' mean one leg fully filled and the other untouched."""
    y, n = yes_leg.filled.get(model, 0.0), no_leg.filled.get(model, 0.0)
    y_full, n_full = yes_leg.is_full(model), no_leg.is_full(model)
    if y <= 1e-9 and n <= 1e-9:
        return OUTCOME_NONE
    if y_full and n_full:
        return OUTCOME_BOTH
    if y > 1e-9 and n > 1e-9:
        return OUTCOME_PARTIAL_BOTH
    if y > 1e-9:
        return OUTCOME_YES_ONLY if y_full else OUTCOME_PARTIAL_YES
    return OUTCOME_NO_ONLY if n_full else OUTCOME_PARTIAL_NO
