"""Exit-rule replay semantics shared by the live exit manager.

This mirrors scripts/weather_exit_sweep.py::replay EXACTLY (TP / SL / break-even on a
recorded yes-bid path). The script keeps its own copy on purpose — it must stay
self-contained (stdlib + psycopg only) so it can run on the ops runner, which puts only
scripts/ on the path. This module is the package-side copy the live executor imports; the
two are kept in lockstep (same convention as buckets.py being mirrored into the scripts).

Break-even semantics: once the gain reaches `be`, the stop ARMS at entry; thereafter if the
bid falls back to <= entry the position exits. The armed state is recomputed from the bid
path every call, so it needs no persistence — the live manager rebuilds the path from
weather_bucket_snapshots each cycle and gets a deterministic decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def fee_cents(price_cents: float, enabled: bool = True) -> float:
    """Kalshi fee in cents at qty=1 (mirrors paper/engine.py::kalshi_fee)."""
    if not enabled or price_cents is None:
        return 0.0
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


def fee_per_contract(price_cents: float, qty: int, enabled: bool = True) -> float:
    """Kalshi fee in cents PER CONTRACT at a given size (only ceil-rounding amortizes)."""
    if not enabled or price_cents is None:
        return 0.0
    p = price_cents / 100.0
    return math.ceil(round(0.07 * qty * p * (1 - p) * 100, 9)) / qty


@dataclass
class Trade:
    entry_cents: int
    entry_fee_cents: float = 0.0
    resolved_value: int = 0          # only used by the offline P&L sweep, not live triggering
    bids: list[float] = field(default_factory=list)  # yes-bid path after entry, time order


def replay(
    trade: Trade, tp: float | None, sl: float | None,
    be: float | None = None, qty: int = 1,
) -> tuple[float, str]:
    """P&L in cents (per contract) and exit kind ('tp'|'sl'|'be'|'settle') for an exit rule.

    Trigger on (bid - entry); exit at that snapshot's bid minus the exit fee; settlement
    pays 0/100 with no exit fee. Break-even: once gain >= be the stop arms at entry, and the
    first later bid <= entry exits.
    """
    entry_fee = trade.entry_fee_cents if qty == 1 else fee_per_contract(trade.entry_cents, qty)

    def _exit(bid: float) -> float:
        ef = fee_cents(bid) if qty == 1 else fee_per_contract(bid, qty)
        return bid - trade.entry_cents - entry_fee - ef

    armed = False
    for bid in trade.bids:
        gain = bid - trade.entry_cents
        if tp is not None and gain >= tp:
            return (_exit(bid), "tp")
        if sl is not None and gain <= -sl:
            return (_exit(bid), "sl")
        if be is not None:
            if not armed:
                if gain >= be:
                    armed = True
            elif gain <= 0.0:
                return (_exit(bid), "be")
    return (trade.resolved_value - trade.entry_cents - entry_fee, "settle")


def should_exit(
    entry_cents: int, history_bids: list[float], current_bid: float,
    *, tp: float | None, sl: float | None, be: float | None,
) -> str | None:
    """Live trigger decision: should we close NOW at `current_bid`? Unlike `replay` (which
    finds the first historical trigger for a backtest), this only fires on the *current*
    bid — we cannot sell in the past. Break-even uses the history to derive the armed state
    (did the bid ever reach entry+be?) and the current bid to detect the fall-back.
    """
    gain = current_bid - entry_cents
    if tp is not None and gain >= tp:
        return "tp"
    if sl is not None and gain <= -sl:
        return "sl"
    if be is not None:
        armed = any((b - entry_cents) >= be for b in history_bids)
        if armed and gain <= 0.0:
            return "be"
    return None
