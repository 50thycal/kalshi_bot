"""Phase 0A — active incentive-program discovery, persisted as versioned terms.

One poll = `GET /incentive_programs?status=active` paged to the end. For each program the
TERMS (everything Kalshi can change: reward, Target Size, Discount Factor, dates, type,
description, paid_out, per-account cap) are hashed; a program whose hash matches its current
row only gets `last_seen_at` bumped, a program whose hash changed gets its current row
`superseded_at` and a NEW row, and a current row no longer listed gets `disappeared_at`.
Nothing is overwritten, so "did the pool change on day 3" is a query, not a guess.

The market's title/status/close_time and its SERIES fee rule are resolved once per new terms
row (`GET /markets/{t}`, `GET /series/{s}`) and stored beside the terms — a fee rule is a
fact about the market at the time, not something recomputed from a later schedule.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .. import models as m
from ..execution.parse import fp_to_float, int_or_none
from .fees import (
    MAKER_RATE_DEFAULT,
    MAKER_RATE_PUBLISHED,
    SOURCE_DEFAULT,
    SOURCE_SERIES_OVERRIDE,
    TAKER_RATE_DEFAULT,
    FeeRule,
)
from .scoring import period_reward_usd

logger = logging.getLogger(__name__)

PERIOD_REWARD_UNIT = "centi_cents"     # OpenAPI 3.30.0: "Total reward for the period in centi-cents"
TERM_FIELDS = ("market_ticker", "incentive_type", "incentive_description", "start_date",
               "end_date", "period_reward", "paid_out", "discount_factor_bps",
               "target_size_fp", "max_reward_per_account")


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def terms_hash(program: dict) -> str:
    payload = {k: program.get(k) for k in TERM_FIELDS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def series_of(ticker: str | None) -> str | None:
    return ticker.split("-", 1)[0] if isinstance(ticker, str) and ticker else None


def fee_rule_from_series(series: dict | None, *, series_ticker: str | None = None) -> FeeRule:
    """`GET /series/{t}` -> FeeRule. `quadratic` = taker-only at 7% x multiplier;
    `*_with_maker_fees` = the published maker coefficient x multiplier; `flat` and unknown
    types keep the default and say so in `detail` (rate unknown = not zero, flagged)."""
    if not isinstance(series, dict):
        return FeeRule(maker_rate=MAKER_RATE_DEFAULT, taker_rate=TAKER_RATE_DEFAULT,
                       source=SOURCE_DEFAULT, detail="series lookup unavailable")
    fee_type = series.get("fee_type")
    mult = series.get("fee_multiplier")
    try:
        mult = float(mult) if mult is not None else 1.0
    except (TypeError, ValueError):
        mult = 1.0
    if fee_type in ("quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"):
        maker = MAKER_RATE_PUBLISHED * mult
    elif fee_type == "quadratic":
        maker = 0.0
    else:
        maker = MAKER_RATE_DEFAULT
    return FeeRule(maker_rate=round(maker, 6), taker_rate=round(TAKER_RATE_DEFAULT * mult, 6),
                   source=SOURCE_SERIES_OVERRIDE, fee_type=fee_type if isinstance(fee_type, str) else None,
                   detail=f"series={series_ticker} fee_multiplier={mult}")


@dataclass
class DiscoveryResult:
    cycle_id: int | None
    current: list[m.IncentiveProgram]
    new_terms: int = 0
    changed_terms: int = 0
    disappeared: int = 0
    errors: int = 0


def current_programs(session, *, now: datetime | None = None,
                     liquidity_only: bool = True) -> list[m.IncentiveProgram]:
    """Current (not superseded, not disappeared) rows, optionally liquidity-type only."""
    stmt = select(m.IncentiveProgram).where(
        m.IncentiveProgram.superseded_at.is_(None), m.IncentiveProgram.disappeared_at.is_(None))
    if liquidity_only:
        stmt = stmt.where(m.IncentiveProgram.incentive_type == "liquidity")
    rows = list(session.scalars(stmt).all())
    if now is not None:
        rows = [r for r in rows if r.end_date is None or _aware(r.end_date) > now]
    return rows


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run_discovery(client, session, *, now: datetime | None = None,
                  resolve_market: bool = True) -> DiscoveryResult:
    """One poll. Never raises: a failed fetch is a cycle row with `errors` and notes."""
    now = now or datetime.now(timezone.utc)
    cycle = m.IncentiveDiscoveryCycle(started_at=now)
    session.add(cycle)
    session.flush()
    notes: dict[str, Any] = {}
    listed: list[dict] = []
    pages = 0
    try:
        for prog in client.iter_incentive_programs(status="active", incentive_type="all"):
            listed.append(prog)
        pages = 1 if listed else 0
    except Exception as exc:  # noqa: BLE001 — the cycle row is the record of the failure
        cycle.errors += 1
        notes["fetch"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        logger.warning("incentive discovery fetch failed: %s", notes["fetch"])
        cycle.finished_at = datetime.now(timezone.utc)
        cycle.notes_json = notes
        return DiscoveryResult(cycle_id=cycle.id, current=current_programs(session), errors=cycle.errors)

    existing = {(r.program_id, r.terms_hash): r for r in session.scalars(
        select(m.IncentiveProgram).where(m.IncentiveProgram.superseded_at.is_(None))).all()}
    by_program: dict[str, m.IncentiveProgram] = {}
    for (pid, _h), r in existing.items():
        by_program[pid] = r
    seen_ids: set[str] = set()
    result = DiscoveryResult(cycle_id=cycle.id, current=[])
    total_usd = 0.0
    for prog in listed:
        pid = str(prog.get("id") or "")
        ticker = prog.get("market_ticker")
        if not pid or not ticker:
            cycle.errors += 1
            continue
        seen_ids.add(pid)
        h = terms_hash(prog)
        itype = prog.get("incentive_type")
        if itype == "liquidity":
            cycle.liquidity_programs += 1
        elif itype == "volume":
            cycle.volume_programs += 1
        usd = period_reward_usd(int_or_none(prog.get("period_reward")))
        if usd and itype == "liquidity":
            total_usd += usd
        row = existing.get((pid, h))
        if row is not None:
            row.last_seen_at = now
            if row.disappeared_at is not None:
                row.disappeared_at = None
                notes.setdefault("reappeared", []).append(pid)
            continue
        prev = by_program.get(pid)
        if prev is not None:
            prev.superseded_at = now
            result.changed_terms += 1
        else:
            result.new_terms += 1
        market_raw, series_raw = None, None
        if resolve_market:
            try:
                market_raw = (client.get_market(ticker) or {}).get("market") or None
            except Exception as exc:  # noqa: BLE001
                notes.setdefault("market_lookup_failed", []).append(f"{ticker}: {type(exc).__name__}")
            s_ticker = series_of(ticker)
            if s_ticker:
                try:
                    series_raw = (client.get_series(s_ticker) or {}).get("series") or None
                except Exception as exc:  # noqa: BLE001
                    notes.setdefault("series_lookup_failed", []).append(f"{s_ticker}: {type(exc).__name__}")
        rule = fee_rule_from_series(series_raw, series_ticker=series_of(ticker))
        extra = {k: v for k, v in prog.items() if k not in TERM_FIELDS and k not in ("id", "market_id")}
        new = m.IncentiveProgram(
            program_id=pid, market_id=prog.get("market_id"), market_ticker=ticker,
            event_ticker=(market_raw or {}).get("event_ticker"), series_ticker=series_of(ticker),
            incentive_type=itype, incentive_description=prog.get("incentive_description"),
            start_date=_parse_dt(prog.get("start_date")), end_date=_parse_dt(prog.get("end_date")),
            period_reward_raw=int_or_none(prog.get("period_reward")),
            period_reward_unit=PERIOD_REWARD_UNIT, period_reward_usd=usd,
            target_size=fp_to_float(prog.get("target_size_fp")),
            discount_factor_bps=int_or_none(prog.get("discount_factor_bps")),
            paid_out=prog.get("paid_out") if isinstance(prog.get("paid_out"), bool) else None,
            status_observed="active", extra_params_json=extra or None,
            market_title=(market_raw or {}).get("title"),
            market_status=(market_raw or {}).get("status"),
            close_time=_parse_dt((market_raw or {}).get("close_time")),
            fee_rule_json=rule.as_dict(), market_raw_json=market_raw,
            terms_hash=h, raw_json=prog, first_seen_at=now, last_seen_at=now,
        )
        session.add(new)
        session.flush()
        by_program[pid] = new
    for pid, row in by_program.items():
        if pid not in seen_ids and row.disappeared_at is None and row.superseded_at is None:
            row.disappeared_at = now
            result.disappeared += 1
    cycle.programs_listed = len(listed)
    cycle.new_terms, cycle.changed_terms, cycle.disappeared = (
        result.new_terms, result.changed_terms, result.disappeared)
    cycle.total_period_reward_usd = round(total_usd, 4)
    cycle.pages = pages
    cycle.finished_at = datetime.now(timezone.utc)
    cycle.notes_json = notes or None
    session.flush()
    result.current = current_programs(session)
    return result
