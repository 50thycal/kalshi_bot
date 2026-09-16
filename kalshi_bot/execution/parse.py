"""Shape-tolerant parsing of Kalshi WebSocket / REST payloads into plain values.

Kalshi's house style is fixed-point STRINGS (`_dollars` for prices, `_fp` for contract counts)
and it has changed payload shapes under us twice. Every helper here returns `None` for a value
it cannot read rather than a zero: "we could not read this" must never reach the database as a
confident claim about the market.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def dollars_to_cents(value: Any) -> int | None:
    """'0.9300' -> 93. Integer input is taken as cents already (legacy shape)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def fp_to_float(value: Any) -> float | None:
    """'2028.55' -> 2028.55, preserving the fraction (Kalshi allows 0.01-contract lots)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def ms_to_dt(ts_ms: Any) -> datetime | None:
    ms = int_or_none(ts_ms)
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def levels(raw: Any) -> list[tuple[int, float]]:
    """`[["0.9300", "12.00"], ...]` -> [(93, 12.0), ...]; unreadable entries are skipped."""
    out: list[tuple[int, float]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        price = dollars_to_cents(entry[0])
        qty = fp_to_float(entry[1])
        if price is None or qty is None:
            continue
        out.append((price, qty))
    return out


def envelope(message: Any) -> tuple[str | None, dict, int | None, int | None, int | None]:
    """(type, msg, sid, seq, id) from one WebSocket frame; `msg` is `{}` when absent."""
    if not isinstance(message, dict):
        return None, {}, None, None, None
    msg = message.get("msg")
    return (
        message.get("type") if isinstance(message.get("type"), str) else None,
        msg if isinstance(msg, dict) else {},
        int_or_none(message.get("sid")),
        int_or_none(message.get("seq")),
        int_or_none(message.get("id")),
    )
