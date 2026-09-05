"""Database write helpers used by the scanner.

Each helper takes an active session, adds/updates a row, flushes so the caller
gets a populated primary key, and returns the ORM object. Commit/rollback is
owned by the caller's `session_scope`.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text

from . import models as m
from .experiment_os import enforcement as xos_enforcement
from .risk.manager import RiskDecision
from .scanner.metrics import MarketMetrics, orderbook_levels, parse_dt
from .scanner.signals import SignalResult

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(obj: Any) -> Any:
    """Return obj if it is JSON-serializable, else a stringified fallback."""
    try:
        json.dumps(obj, default=str)
        return obj
    except (TypeError, ValueError):
        return {"_unserializable": str(obj)[:500]}


def start_bot_run(session, mode: str) -> m.BotRun:
    run = m.BotRun(started_at=_now(), mode=mode, status="running")
    session.add(run)
    session.flush()
    return run


def finish_bot_run(
    session,
    run: m.BotRun,
    *,
    status: str,
    markets_scanned: int,
    candidates_found: int,
    error_message: str | None = None,
) -> None:
    run.finished_at = _now()
    run.status = status
    run.markets_scanned = markets_scanned
    run.candidates_found = candidates_found
    run.error_message = error_message
    session.add(run)
    session.flush()


def upsert_market(session, market: dict) -> m.Market:
    ticker = market["ticker"]
    obj = session.scalar(select(m.Market).where(m.Market.ticker == ticker))
    if obj is None:
        obj = m.Market(ticker=ticker, created_at=_now())
        session.add(obj)
    obj.title = market.get("title")
    obj.category = market.get("category")
    obj.close_time = parse_dt(market.get("close_time"))
    obj.expiration_time = parse_dt(market.get("expiration_time"))
    obj.settlement_source = market.get("settlement_source") or market.get(
        "settlement_sources_text"
    )
    obj.rules_summary = market.get("rules_primary")
    obj.status = market.get("status")
    obj.updated_at = _now()
    session.flush()
    return obj


def insert_market_snapshot(
    session, market: dict, metrics: MarketMetrics, *, captured_at: datetime | None = None
) -> m.MarketSnapshot:
    snap = m.MarketSnapshot(
        market_ticker=metrics.ticker,
        captured_at=captured_at or _now(),
        yes_bid=metrics.best_yes_bid,
        yes_ask=metrics.best_yes_ask,
        no_bid=metrics.best_no_bid,
        no_ask=metrics.best_no_ask,
        last_price=metrics.last_price,
        volume=metrics.volume,
        open_interest=metrics.open_interest,
        spread=metrics.spread,
        midpoint=metrics.midpoint,
        liquidity_score=metrics.liquidity_score,
        raw_json=_safe_json(market),
    )
    session.add(snap)
    session.flush()
    return snap


def insert_orderbook_snapshot(
    session,
    ticker: str,
    metrics: MarketMetrics,
    orderbook: dict,
    *,
    captured_at: datetime | None = None,
) -> m.OrderbookSnapshot:
    yes_raw, no_raw = orderbook_levels(orderbook)
    snap = m.OrderbookSnapshot(
        market_ticker=ticker,
        captured_at=captured_at or _now(),
        yes_levels_json=_safe_json(yes_raw),
        no_levels_json=_safe_json(no_raw),
        best_yes_bid=metrics.best_yes_bid,
        best_yes_ask=metrics.best_yes_ask,
        best_no_bid=metrics.best_no_bid,
        best_no_ask=metrics.best_no_ask,
        top_depth=metrics.top_depth,
        raw_json=_safe_json(orderbook),
    )
    session.add(snap)
    session.flush()
    return snap


def insert_signal(
    session, signal: SignalResult, metrics: MarketMetrics, *, snapshot_id: int, bot_mode: str
) -> m.Signal:
    row = m.Signal(
        market_ticker=signal.ticker,
        created_at=_now(),
        signal_type="deterministic_scan",
        bot_mode=bot_mode,
        implied_probability=signal.implied_probability,
        model_probability=None,
        edge=None,
        confidence=signal.confidence,
        label=signal.label,
        reason=signal.reason,
        input_snapshot_id=snapshot_id,
    )
    session.add(row)
    session.flush()
    return row


def insert_risk_event(
    session, signal_id: int | None, ticker: str, decision: RiskDecision
) -> m.RiskEvent:
    row = m.RiskEvent(
        signal_id=signal_id,
        market_ticker=ticker,
        created_at=_now(),
        approved=decision.approved,
        reason_codes_json=decision.reason_codes,
        max_allowed_quantity=decision.max_allowed_quantity,
        max_allowed_price=decision.max_allowed_price,
    )
    session.add(row)
    session.flush()
    return row


def insert_account_snapshot(
    session,
    *,
    cash_balance: float | None,
    portfolio_value: float | None = None,
    total_exposure: float | None = None,
    raw: dict | None = None,
) -> m.AccountSnapshot:
    row = m.AccountSnapshot(
        captured_at=_now(),
        cash_balance=cash_balance,
        portfolio_value=portfolio_value,
        total_exposure=total_exposure,
        raw_json=_safe_json(raw or {}),
    )
    session.add(row)
    session.flush()
    return row


# --- paper trading -----------------------------------------------------------


def create_paper_trade(
    session,
    *,
    signal_id: int | None,
    ticker: str,
    strategy: str,
    side: str,
    action: str,
    assumed_price: int,
    quantity: int,
    fill_assumption: str,
    entry_fee: float,
    model_probability: float | None = None,
    edge: float | None = None,
) -> m.PaperTrade:
    # Experiment OS admission + lineage (spec §14): under NEW_ONLY/STRICT an
    # unregistered NEW tag raises LineageBlocked here — no new experimental
    # exposure without lineage. Registered (native or grandfathered) tags get their
    # deployment-arm stamp; under OFF/WARN nothing is ever blocked.
    arm_link_id = xos_enforcement.stamp_or_block(session, strategy, channel="paper")
    row = m.PaperTrade(
        signal_id=signal_id,
        market_ticker=ticker,
        strategy=strategy,
        created_at=_now(),
        side=side,
        action=action,
        assumed_price=assumed_price,
        quantity=quantity,
        model_probability=model_probability,
        edge=edge,
        # Clamp to the column width (String(64)): a long market subtitle in a book's
        # annotation must degrade to a truncated note, never abort the entry (Postgres
        # raises StringDataRightTruncation on overflow, killing the whole cycle).
        fill_assumption=(fill_assumption or "")[:64],
        status="open",
        fees=entry_fee,
        experiment_deployment_arm_id=arm_link_id,
    )
    session.add(row)
    session.flush()
    return row


def record_no_fill(
    session,
    *,
    signal_id: int | None,
    ticker: str,
    strategy: str,
    side: str,
    action: str,
    assumed_price: int | None,
    fill_assumption: str,
    model_probability: float | None = None,
    edge: float | None = None,
) -> m.PaperTrade:
    row = m.PaperTrade(
        signal_id=signal_id,
        market_ticker=ticker,
        strategy=strategy,
        created_at=_now(),
        side=side,
        action=action,
        assumed_price=assumed_price,
        quantity=0,
        model_probability=model_probability,
        edge=edge,
        fill_assumption=(fill_assumption or "")[:64],  # clamp: see create_paper_trade
        status="no_fill",
        closed_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


def recent_midpoints(
    session, ticker: str, lookback_hours: float
) -> list[tuple[datetime, float]]:
    """Midpoint history for a ticker over the lookback window, ascending by time."""
    cutoff = _now() - timedelta(hours=lookback_hours)
    rows = session.execute(
        select(m.MarketSnapshot.captured_at, m.MarketSnapshot.midpoint)
        .where(
            m.MarketSnapshot.market_ticker == ticker,
            m.MarketSnapshot.midpoint.is_not(None),
            m.MarketSnapshot.captured_at >= cutoff,
        )
        .order_by(m.MarketSnapshot.captured_at.asc())
    ).all()
    return [(captured_at, float(mid)) for captured_at, mid in rows]


def get_open_paper_trades(session) -> list[m.PaperTrade]:
    return list(
        session.scalars(select(m.PaperTrade).where(m.PaperTrade.status == "open")).all()
    )


def get_open_paper_position(session, ticker: str, strategy: str) -> m.PaperPosition | None:
    return session.scalar(
        select(m.PaperPosition).where(
            m.PaperPosition.market_ticker == ticker,
            m.PaperPosition.strategy == strategy,
            m.PaperPosition.status == "open",
        )
    )


def count_open_paper_positions(session, strategy: str | None = None) -> int:
    stmt = select(func.count()).select_from(m.PaperPosition).where(
        m.PaperPosition.status == "open"
    )
    if strategy is not None:
        stmt = stmt.where(m.PaperPosition.strategy == strategy)
    return int(session.scalar(stmt) or 0)


def open_paper_position_tickers(session, strategy: str) -> set[str]:
    return set(
        session.scalars(
            select(m.PaperPosition.market_ticker).where(
                m.PaperPosition.status == "open",
                m.PaperPosition.strategy == strategy,
            )
        )
    )


def open_paper_exposure(
    session, *, strategy: str, ticker: str | None = None
) -> float:
    """Open paper exposure (dollars) for a strategy, optionally one ticker."""
    stmt = select(m.PaperPosition).where(
        m.PaperPosition.status == "open", m.PaperPosition.strategy == strategy
    )
    if ticker is not None:
        stmt = stmt.where(m.PaperPosition.market_ticker == ticker)
    total = 0.0
    for pos in session.scalars(stmt).all():
        total += (pos.quantity or 0) * float(pos.avg_price or 0) / 100.0
    return total


def open_paper_position_for_trade(
    session, *, ticker: str, strategy: str, side: str, quantity: int, avg_price: int
) -> m.PaperPosition:
    pos = m.PaperPosition(
        market_ticker=ticker,
        strategy=strategy,
        side=side,
        quantity=quantity,
        avg_price=avg_price,
        status="open",
        opened_at=_now(),
    )
    session.add(pos)
    session.flush()
    return pos


def close_paper_trade(
    session,
    trade: m.PaperTrade,
    *,
    status: str,
    pnl: float,
    exit_price: int | None = None,
    resolved_value: int | None = None,
    exit_fee: float = 0.0,
) -> None:
    trade.status = status
    trade.exit_price = exit_price
    trade.resolved_value = resolved_value
    trade.pnl = pnl
    trade.fees = float(trade.fees or 0.0) + exit_fee
    trade.closed_at = _now()
    session.add(trade)

    pos = get_open_paper_position(session, trade.market_ticker, trade.strategy)
    if pos is not None:
        pos.status = status
        pos.pnl = pnl
        pos.unrealized_pnl = None
        pos.closed_at = _now()
        session.add(pos)
    session.flush()


_CLOSED_STATUSES = ("settled", "closed_timeout", "closed_tp", "closed_sl", "closed_void")


def paper_cycle_stats(session) -> dict:
    """Lightweight portfolio rollup for the per-cycle log."""
    open_positions = 0
    open_unrealized = 0.0
    for pos in session.scalars(
        select(m.PaperPosition).where(m.PaperPosition.status == "open")
    ).all():
        open_positions += 1
        open_unrealized += float(pos.unrealized_pnl or 0)

    realized_total = float(
        session.scalar(
            select(func.coalesce(func.sum(m.PaperTrade.pnl), 0)).where(
                m.PaperTrade.status.in_(_CLOSED_STATUSES)
            )
        )
        or 0
    )
    closed_count = int(
        session.scalar(
            select(func.count())
            .select_from(m.PaperTrade)
            .where(m.PaperTrade.status.in_(_CLOSED_STATUSES))
        )
        or 0
    )
    return {
        "open_positions": open_positions,
        "open_unrealized": round(open_unrealized, 4),
        "realized_total": round(realized_total, 4),
        "closed_trades": closed_count,
        "by_strategy": paper_stats_by_strategy(session),
    }


def paper_stats_by_strategy(session) -> dict[str, dict]:
    """Per-strategy rollup: open positions/unrealized + realized/closed."""
    stats: dict[str, dict] = {}
    for pos in session.scalars(
        select(m.PaperPosition).where(m.PaperPosition.status == "open")
    ).all():
        key = pos.strategy or "unknown"
        s = stats.setdefault(key, {"open": 0, "unrealized": 0.0, "closed": 0, "realized": 0.0})
        s["open"] += 1
        s["unrealized"] += float(pos.unrealized_pnl or 0)
    for strategy, count, pnl in session.execute(
        select(m.PaperTrade.strategy, func.count(), func.coalesce(func.sum(m.PaperTrade.pnl), 0))
        .where(m.PaperTrade.status.in_(_CLOSED_STATUSES))
        .group_by(m.PaperTrade.strategy)
    ).all():
        key = strategy or "unknown"
        s = stats.setdefault(key, {"open": 0, "unrealized": 0.0, "closed": 0, "realized": 0.0})
        s["closed"] = int(count)
        s["realized"] = round(float(pnl), 4)
    for s in stats.values():
        s["unrealized"] = round(s["unrealized"], 4)
    return stats


def weather_entered(session, event_ticker: str, strategy: str) -> bool:
    """Has a paper trade already been opened for this event under this window-strategy?
    Bucket tickers start with the event ticker, so we match on that prefix."""
    return (
        session.scalar(
            select(func.count())
            .select_from(m.PaperTrade)
            .where(
                m.PaperTrade.strategy == strategy,
                m.PaperTrade.market_ticker.like(f"{event_ticker}-%"),
            )
        )
        or 0
    ) > 0


def strategy_is_kept(strategy: str | None, keep_prefixes: tuple[str, ...]) -> bool:
    """Does this strategy tag belong to one of the book families we are keeping?

    A plain prefix test is WRONG for the mmsell family, which deliberately has no common
    prefix: the market-type books are tagged `Wmmsell*`/`Tmmsell*` so the band regime reads at
    a glance (docs/MMSELL_TYPE_BOOKS.md). Under a prefix test those books look FOREIGN, so
    `abandon_open_paper_trades` wiped every one of their open positions on each worker start —
    and because the entry scan's dedup guard keys off an OPEN position, each book then re-entered
    the same market on the very next cycle. Observed 2026-08-04 before this fix: Wmmsell6 held 9
    markets but had accumulated 47 `abandoned` rows across them in under four hours, and no book
    could ever have carried a position across a deploy.
    """
    tag = strategy or ""
    if tag.startswith(keep_prefixes):
        return True
    # The one family whose members are identified by substring rather than prefix.
    return "mmsell" in keep_prefixes and "mmsell" in tag


def abandon_open_paper_trades(session, keep_prefixes: tuple[str, ...]) -> int:
    """Close out open paper trades/positions whose strategy isn't in one of the kept book
    families (used to clear a prior experiment when switching modes). See strategy_is_kept —
    membership is NOT a plain prefix test."""
    closed = 0
    for trade in session.scalars(
        select(m.PaperTrade).where(m.PaperTrade.status == "open")
    ).all():
        if not strategy_is_kept(trade.strategy, keep_prefixes):
            trade.status = "abandoned"
            trade.closed_at = _now()
            session.add(trade)
            closed += 1
    for pos in session.scalars(
        select(m.PaperPosition).where(m.PaperPosition.status == "open")
    ).all():
        if not strategy_is_kept(pos.strategy, keep_prefixes):
            pos.status = "abandoned"
            pos.closed_at = _now()
            session.add(pos)
    session.flush()
    return closed


def insert_weather_forecast(
    session,
    *,
    city: str,
    series_ticker: str | None,
    event_ticker: str | None,
    target_date: str | None,
    station: str | None,
    forecast_high_f: float | None,
    source: str,
    kind: str = "high",
    raw: dict | None = None,
) -> m.WeatherForecast:
    row = m.WeatherForecast(
        captured_at=_now(),
        city=city,
        series_ticker=series_ticker,
        event_ticker=event_ticker,
        target_date=target_date,
        station=station,
        kind=kind,
        forecast_high_f=forecast_high_f,
        source=source,
        raw_json=_safe_json(raw or {}),
    )
    session.add(row)
    session.flush()
    return row


def weather_settlement_exists(session, event_ticker: str) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(m.WeatherSettlement)
            .where(m.WeatherSettlement.event_ticker == event_ticker)
        )
        or 0
    ) > 0


def insert_weather_settlement(
    session,
    *,
    event_ticker: str,
    city: str | None,
    series_ticker: str | None,
    target_date: str | None,
    winning_ticker: str | None,
    winning_subtitle: str | None,
    actual_low_f: float | None,
    actual_high_f: float | None,
    kind: str = "high",
    raw: dict | None = None,
) -> m.WeatherSettlement:
    row = m.WeatherSettlement(
        event_ticker=event_ticker,
        city=city,
        series_ticker=series_ticker,
        target_date=target_date,
        kind=kind,
        winning_ticker=winning_ticker,
        winning_subtitle=winning_subtitle,
        actual_low_f=actual_low_f,
        actual_high_f=actual_high_f,
        captured_at=_now(),
        raw_json=_safe_json(raw or {}),
    )
    session.add(row)
    session.flush()
    return row


def weather_city_bias(
    session, *, shrinkage: float = 3.0, min_events: int = 1
) -> dict[tuple[str, str], float]:
    """Per-(city, kind) forecast bias offset (degF) to ADD to a raw NWS forecast so it
    better matches the station's settled extreme.

    offset = shrink( mean(actual - earliest_forecast) ), where the shrink factor
    n/(n+shrinkage) pulls small samples toward 0 so a couple of events don't overcorrect.
    Uses the earliest (morning) forecast per event — the value the books trade on. Only
    (city, kind) pairs with >= min_events settled+forecast pairs are returned. Highs and
    lows are learned independently (a station can run cool on highs but not on lows).
    """
    diffs: dict[tuple[str, str], list[float]] = {}
    for st in session.scalars(select(m.WeatherSettlement)).all():
        low, high = st.actual_low_f, st.actual_high_f
        if low is not None and high is not None:
            actual = (low + high) / 2.0
        elif high is not None:
            actual = high
        elif low is not None:
            actual = low
        else:
            continue
        fc = session.scalar(
            select(m.WeatherForecast.forecast_high_f)
            .where(
                m.WeatherForecast.event_ticker == st.event_ticker,
                m.WeatherForecast.forecast_high_f.is_not(None),
                # NWS only — HRRR (source='openmeteo_hrrr') is collected/graded separately
                # and must not pollute the NWS-anchored `cal` bias. NULL/'nws' => NWS.
                func.coalesce(m.WeatherForecast.source, "nws") != "openmeteo_hrrr",
            )
            .order_by(m.WeatherForecast.captured_at.asc())
            .limit(1)
        )
        if fc is None or not st.city:
            continue
        diffs.setdefault((st.city, st.kind or "high"), []).append(actual - float(fc))
    out: dict[tuple[str, str], float] = {}
    for key, ds in diffs.items():
        n = len(ds)
        if n < min_events:
            continue
        raw = sum(ds) / n
        out[key] = round(raw * n / (n + shrinkage), 2)
    return out


def insert_weather_observation(
    session,
    *,
    city: str,
    station: str | None,
    target_date: str | None,
    running_max_f: float | None,
    running_min_f: float | None,
    obs_count: int,
    last_obs_at,
) -> m.WeatherObservation:
    row = m.WeatherObservation(
        captured_at=_now(),
        city=city,
        station=station,
        target_date=target_date,
        running_max_f=running_max_f,
        running_min_f=running_min_f,
        obs_count=obs_count,
        last_obs_at=last_obs_at,
    )
    session.add(row)
    session.flush()
    return row


def latest_weather_observation(
    session, city: str, target_date: str
) -> m.WeatherObservation | None:
    return session.scalar(
        select(m.WeatherObservation)
        .where(
            m.WeatherObservation.city == city,
            m.WeatherObservation.target_date == target_date,
        )
        .order_by(m.WeatherObservation.captured_at.desc())
        .limit(1)
    )


def insert_weather_ensemble(
    session,
    *,
    city: str,
    target_date: str | None,
    kind: str,
    model: str | None,
    members: list[float],
) -> m.WeatherEnsemble:
    n = len(members)
    mean = sum(members) / n if n else None
    std = (sum((x - mean) ** 2 for x in members) / n) ** 0.5 if n else None
    row = m.WeatherEnsemble(
        captured_at=_now(),
        city=city,
        target_date=target_date,
        kind=kind,
        model=model,
        member_count=n,
        mean_f=round(mean, 2) if mean is not None else None,
        std_f=round(std, 2) if std is not None else None,
        members_json=members,
    )
    session.add(row)
    session.flush()
    return row


def latest_weather_forecast_at(
    session, event_ticker: str, source: str, kind: str = "high"
):
    """Most recent captured_at for a stored forecast of this (event, source, kind), for
    throttling repeat fetches (e.g. HRRR). None when none stored yet."""
    return session.scalar(
        select(func.max(m.WeatherForecast.captured_at)).where(
            m.WeatherForecast.event_ticker == event_ticker,
            m.WeatherForecast.source == source,
            m.WeatherForecast.kind == kind,
        )
    )


def latest_weather_ensemble_at(session, city: str, target_date: str):
    return session.scalar(
        select(func.max(m.WeatherEnsemble.captured_at)).where(
            m.WeatherEnsemble.city == city,
            m.WeatherEnsemble.target_date == target_date,
        )
    )


def latest_weather_ensemble_members(
    session, city: str, target_date: str, kind: str
) -> dict[str, list[float]]:
    """Most recent ensemble member list per model for (city, date, kind) — the live
    forecast distribution the `dist` book prices buckets against."""
    rows = session.scalars(
        select(m.WeatherEnsemble)
        .where(
            m.WeatherEnsemble.city == city,
            m.WeatherEnsemble.target_date == target_date,
            m.WeatherEnsemble.kind == kind,
        )
        .order_by(m.WeatherEnsemble.captured_at.desc())
    ).all()
    out: dict[str, list[float]] = {}
    for row in rows:
        model = row.model or "?"
        if model not in out and row.members_json:
            out[model] = [float(v) for v in row.members_json if v is not None]
    return out


def insert_weather_bucket_snapshots(session, rows: list[dict]) -> int:
    now = _now()
    for row in rows:
        session.add(m.WeatherBucketSnapshot(captured_at=now, **row))
    session.flush()
    return len(rows)


def latest_bucket_snapshot_at(session, event_ticker: str):
    return session.scalar(
        select(func.max(m.WeatherBucketSnapshot.captured_at)).where(
            m.WeatherBucketSnapshot.event_ticker == event_ticker
        )
    )


def mark_paper_position(session, ticker: str, strategy: str, unrealized_pnl: float) -> None:
    pos = get_open_paper_position(session, ticker, strategy)
    if pos is not None:
        pos.unrealized_pnl = unrealized_pnl
        session.add(pos)
        session.flush()


def insert_mmsell_tick(
    session, ticker: str, metrics: MarketMetrics, *, captured_at: datetime | None = None
) -> None:
    """Record one price tick for a market holding an open mmsell position (the intraday tape the
    exit study replays). Cheap: reuses the orderbook metrics manage_open_positions already has."""
    session.add(m.MmSellPositionTick(
        market_ticker=ticker,
        captured_at=captured_at or _now(),
        yes_bid=metrics.best_yes_bid,
        yes_ask=metrics.best_yes_ask,
        no_bid=metrics.best_no_bid,
        no_ask=metrics.best_no_ask,
        mid=metrics.midpoint,
        volume=metrics.volume,
    ))
    session.flush()


def _strike_value(raw) -> float | None:
    """Kalshi strike fields arrive as numbers, numeric strings, or absent. Anything unparseable
    stores NULL rather than 0.0 — a strike of zero is a real line (a 0-run handicap), so a
    coerced default would be indistinguishable from data."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def insert_mmsell_candidate_tick(
    session, ticker: str, metrics: MarketMetrics, *, series: str | None = None,
    hours_to_close: float | None = None, captured_at: datetime | None = None,
    hours_to_expiration: float | None = None, market: dict | None = None,
) -> None:
    """Record one orderbook tick for an IN-BAND mmsell CANDIDATE (opened this cycle or not) — the
    pre-entry price path a per-ticker fill replay needs ('would a resting buy-NO at the no-bid have
    been lifted before close?'). Complements insert_mmsell_tick (held positions only). Cheap:
    reuses the orderbook metrics the entry scan already fetched; deliberately NOT flushed per row
    (committed with the cycle) so bulk candidate capture doesn't flush hundreds of times.

    `hours_to_expiration` is the forward-looking resolution clock (see the model docstring — for
    in-play markets `hours_to_close` is a far-future fallback and measures nothing). `market` is
    the raw payload the scan already holds; its strike fields are recorded so a book can later be
    cut by the contract's LINE (a 3-run handicap vs a 1-run one) rather than only by its type."""
    mkt = market or {}
    session.add(m.MmSellCandidateTick(
        market_ticker=ticker,
        captured_at=captured_at or _now(),
        series=series,
        hours_to_close=hours_to_close,
        hours_to_expiration=hours_to_expiration,
        # Clamped to the column widths: a long subtitle must never abort a whole cycle's
        # candidate capture (the tape is a diagnostic — it may lose detail, never rows).
        strike_type=(str(mkt.get("strike_type"))[:16] if mkt.get("strike_type") else None),
        floor_strike=_strike_value(mkt.get("floor_strike")),
        cap_strike=_strike_value(mkt.get("cap_strike")),
        yes_sub_title=(str(sub)[:64] if (sub := (mkt.get("yes_sub_title")
                                                 or mkt.get("subtitle"))) else None),
        yes_bid=metrics.best_yes_bid,
        yes_ask=metrics.best_yes_ask,
        no_bid=metrics.best_no_bid,
        no_ask=metrics.best_no_ask,
        mid=metrics.midpoint,
        volume=metrics.volume,
        # Depth at the touch — the capacity ceiling a taker entry runs into. getattr-guarded
        # because the only consequence of a metrics object without them is a NULL column, and a
        # diagnostic tape must never be the thing that breaks an entry scan.
        depth_at_best_bid=getattr(metrics, "depth_at_best_bid", None),
        depth_at_best_ask=getattr(metrics, "depth_at_best_ask", None),
    ))


# --- prior-quote lookups for the live hot-market check (live/sizing.py's is_hot_entry) ---
#
# One per live book, because each book keeps its own tape: mmsell writes mmsell_candidate_ticks,
# theta writes crypto_ladder_snapshots. Both return the SAME normalized shape —
# `(no_bid_cents, captured_at)`, or `(None, None)` when the market has no prior quote — so
# is_hot_entry stays a pure decision function that never learns which book called it. The caller
# passes its own book's reader explicitly; see the note in live/sizing.py about why nothing here
# is defaulted.
#
# `before` excludes the current cycle's own just-written row from being compared against itself.
# Neither reader applies a lower time bound: is_hot_entry decides what an OLD quote means, and
# "no row at all" must stay distinguishable from "a stale row".


def latest_mmsell_no_bid_before(
    session, ticker: str, *, before: datetime
) -> tuple[int | None, datetime | None]:
    """mmsell's prior quote, from the in-band candidate tape it writes every cycle."""
    row = session.execute(
        select(m.MmSellCandidateTick.no_bid, m.MmSellCandidateTick.captured_at)
        .where(
            m.MmSellCandidateTick.market_ticker == ticker,
            m.MmSellCandidateTick.captured_at < before,
        )
        .order_by(m.MmSellCandidateTick.captured_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None, None
    return (int(row[0]) if row[0] is not None else None), row[1]


def latest_theta_no_bid_before(
    session, ticker: str, *, before: datetime
) -> tuple[int | None, datetime | None]:
    """theta's prior quote, from the crypto ladder tape.

    The ladder stores the YES side, so the NO bid theta's maker entry rests at is derived the
    same way the entry itself derives it: `no_bid = 100 - yes_ask`. Served by
    ix_crypto_ladder_mkt_time (alembic b8c9d0e1f2a3) — without it this is a sequential scan
    inside the live trading loop."""
    row = session.execute(
        select(m.CryptoLadderSnapshot.yes_ask_cents, m.CryptoLadderSnapshot.captured_at)
        .where(
            m.CryptoLadderSnapshot.market_ticker == ticker,
            m.CryptoLadderSnapshot.captured_at < before,
        )
        .order_by(m.CryptoLadderSnapshot.captured_at.desc())
        .limit(1)
    ).first()
    if row is None or row[0] is None:
        return None, (row[1] if row is not None else None)
    return int(round(100.0 - float(row[0]))), row[1]


def recent_candidate_mids(session, ticker: str, limit: int) -> list[float]:
    """The last `limit` yes-mid values taped for this in-band CANDIDATE, oldest-first.

    Feeds the anchor set's volatility ENTRY gate (docs/MMSELL_ANCHOR_SET.md): the range over these
    is 'how much has this market already moved before we rest an order on it'. Returns fewer than
    `limit` (possibly none) when the ticker is newly in-band — the caller decides what to do with
    thin history, and the gate deliberately does NOT fire on it so the A/B stays clean."""
    rows = session.scalars(
        select(m.MmSellCandidateTick.mid)
        .where(m.MmSellCandidateTick.market_ticker == ticker,
               m.MmSellCandidateTick.mid.isnot(None))
        .order_by(m.MmSellCandidateTick.captured_at.desc())
        .limit(max(1, limit))
    ).all()
    return [float(x) for x in reversed(rows)]


def ensure_mmsell_settlement_meta(session, *, market_ticker: str, event_ticker: str | None,
                                  series_ticker: str | None, close_time: datetime) -> None:
    """Record a candidate market's settlement metadata the first time it is seen; a no-op on
    every later cycle. Insert-only (like the regime-history capture's upsert): a market's close
    time never changes, and re-writing it would just be wasted work on every subsequent cycle
    the market stays a candidate."""
    existing = session.scalar(
        select(m.MmSellSettlementMeta.id).where(
            m.MmSellSettlementMeta.market_ticker == market_ticker
        )
    )
    if existing is not None:
        return
    session.add(m.MmSellSettlementMeta(
        market_ticker=market_ticker, event_ticker=event_ticker,
        series_ticker=series_ticker, close_time=close_time,
    ))
    session.flush()


def open_positions_settlement_summary(
    session, strategy: str, close_date, ticker: str
) -> tuple[int, Counter[str]]:
    """(count, event ticker -> open rungs) of `strategy`'s OTHER currently-open positions settling
    on `close_date` (a UTC calendar date) — the settlement-date concentration cap's read.
    `ticker` is EXCLUDED so a position already open on the candidate's own market (the
    `already_open` path handles that case separately) can never count against its own cap.

    A Counter rather than a set so the caller can also cap rungs WITHIN one event (the
    non-mutually-exclusive ladder case, see Settings.mmsell_event_rung_cap). `len()` and `in`
    behave identically to the set this used to return, so the distinct-EVENT cap that reads it
    is unaffected.

    Filters with an explicit UTC datetime RANGE rather than a DB-side date() function: SQLite
    (used by the test suite) and Postgres (production) parse timestamp strings differently
    enough that a portable comparison is worth the extra two lines."""
    day_start = datetime(close_date.year, close_date.month, close_date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    rows = session.execute(
        select(m.PaperPosition.market_ticker, m.MmSellSettlementMeta.event_ticker)
        .join(m.MmSellSettlementMeta,
              m.MmSellSettlementMeta.market_ticker == m.PaperPosition.market_ticker)
        .where(
            m.PaperPosition.strategy == strategy,
            m.PaperPosition.status == "open",
            m.PaperPosition.market_ticker != ticker,
            m.MmSellSettlementMeta.close_time >= day_start,
            m.MmSellSettlementMeta.close_time < day_end,
        )
    ).all()
    return len(rows), Counter(r[1] for r in rows if r[1])


def open_positions_correlation_rows(
    session, strategy: str, ticker: str
) -> list[tuple[str | None, str | None]]:
    """`(series_ticker, event_ticker)` for every one of `strategy`'s OTHER currently-open
    positions — the correlation cap's read (docs/MMSELL_CORRELATION_CAP.md, XOS-000020).

    Returns raw rows rather than a Counter of keys, deliberately: the mapping from a market to
    its unit of correlation is strategy semantics that belongs in `mmsell.correlation`, and
    keeping it out of here is what stops the repository from growing a second, drifting copy of
    the taxonomy.

    NOT scoped to a settlement date, unlike `open_positions_settlement_summary`. A game's
    markets do not all share a UTC calendar date — an F5 total closes hours before the full-game
    total, and a late start crosses midnight — so a date-scoped read would let exactly the
    clustering this cap exists to prevent slip through whenever a slate straddles the boundary.
    The open book is bounded by `mmsell_max_open_positions`, so reading all of it is cheap.

    `ticker` is EXCLUDED so a position already open on the candidate's own market cannot count
    against its own cap; the `already_open` path handles that case."""
    return list(session.execute(
        select(m.MmSellSettlementMeta.series_ticker, m.MmSellSettlementMeta.event_ticker)
        .join(m.PaperPosition,
              m.PaperPosition.market_ticker == m.MmSellSettlementMeta.market_ticker)
        .where(
            m.PaperPosition.strategy == strategy,
            m.PaperPosition.status == "open",
            m.PaperPosition.market_ticker != ticker,
        )
    ).all())


def event_has_strangle_leg(session, strategy: str, event_ticker: str, side: str) -> bool:
    """True when `strategy` has ALREADY entered a `side` ('no' or 'yes') leg on this event —
    any status, not just open, since this dedups against the event's own outcome rather than
    against currently-held risk. Caps the anchor set's strangle (mmsellA5) to one leg per side
    per event; see MmSellTracker._strangle_leg_taken for why that cap has to exist."""
    return session.scalar(
        select(m.PaperTrade.id)
        .join(m.MmSellSettlementMeta,
              m.MmSellSettlementMeta.market_ticker == m.PaperTrade.market_ticker)
        .where(
            m.PaperTrade.strategy == strategy,
            m.PaperTrade.side == side,
            m.MmSellSettlementMeta.event_ticker == event_ticker,
        )
        .limit(1)
    ) is not None


def recent_position_yes_bids(session, ticker: str, limit: int) -> list[float]:
    """The last `limit` yes-BID values taped for this HELD position, oldest-first.

    Feeds the anchor set's executing catastrophic stop. The trigger is the BID, not the mid or ask:
    at these prices thin books quote wide, and a mid- or ask-triggered stop fires on quotes with no
    real buyer behind them (docs/MMSELL_CRYPTO_STUDY.md measured a mid-triggered K=1 stop exiting
    ~100% of positions — a pure artifact). A rising bid is real buying interest at that level."""
    rows = session.scalars(
        select(m.MmSellPositionTick.yes_bid)
        .where(m.MmSellPositionTick.market_ticker == ticker,
               m.MmSellPositionTick.yes_bid.isnot(None))
        .order_by(m.MmSellPositionTick.captured_at.desc())
        .limit(max(1, limit))
    ).all()
    return [float(x) for x in reversed(rows)]


# --- live/paper twin harness (docs/LIVE_PAPER_TWIN.md) ---


def get_twin_epoch(session, twin_tag: str) -> m.LivePaperTwin | None:
    return session.scalar(
        select(m.LivePaperTwin).where(m.LivePaperTwin.twin_tag == twin_tag)
    )


def active_twin_epochs(session) -> list[m.LivePaperTwin]:
    return list(session.scalars(
        select(m.LivePaperTwin).where(m.LivePaperTwin.ended_at.is_(None))
        .order_by(m.LivePaperTwin.started_at)
    ).all())


def sync_twin_epoch(
    session, *, twin_tag: str, live_tag: str, params: dict
) -> tuple[m.LivePaperTwin, bool]:
    """Get-or-create the twin's epoch row; returns (row, params_drifted).

    `started_at` is written ONCE and never moved — a redeploy must not silently restart the
    epoch, because the whole point is that both sides of the comparison are scoped to the same
    window. If the live parameters have since changed, the stored snapshot is left intact and
    `params_drifted=True` is returned so the caller can flag it: the comparison is no longer
    apples-to-apples and the honest fix is a NEW twin tag, not a quiet re-parameterization."""
    row = get_twin_epoch(session, twin_tag)
    if row is None:
        row = m.LivePaperTwin(
            twin_tag=twin_tag[:24],
            live_tag=live_tag[:32],
            started_at=_now(),
            params_json=params,
        )
        session.add(row)
        session.flush()
        return row, False
    drifted = (row.params_json or {}) != params
    return row, drifted


def end_twin_epoch(session, twin_tag: str, *, notes: str | None = None) -> bool:
    """Retire a twin epoch (stops nothing by itself — the config switch does that; this marks the
    window closed so parity reports scope to it and stop treating it as running)."""
    row = get_twin_epoch(session, twin_tag)
    if row is None or row.ended_at is not None:
        return False
    row.ended_at = _now()
    if notes:
        row.notes = notes
    session.flush()
    return True


def reconcile_stale_twin_epochs(
    session, *, live_strategy_prefixes: list[str], configured_pairs: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Close every open twin epoch that no longer corresponds to a live book we are actually
    running, so the dashboard never shows a dead pair as running. Two distinct ways an epoch goes
    stale, both seen in production:

    1. **The live book was retired** — its tag no longer matches any configured LIVE_STRATEGIES
       prefix (e.g. mmsell10 dropped in favor of the mmsell10a/mmsell10b queue-position A/B).
       Prefix matching mirrors TwinHarness.active_for exactly, so this and the harness's live-cycle
       gating can never disagree about what counts as "still live".
    2. **The twin tag was REPLACED while its live book kept running** — the prescribed response to
       a PARAM DRIFT warning is literally "start a new twin tag", so an operator bumps the suffix
       (`mmsell10a_pt` -> `mmsell10a_pt2`). The old epoch's live_tag is still armed, so rule 1 does
       not catch it, and the orphan sits open forever accruing nothing. Observed: three orphaned
       `_pt` epochs (1, 4 and 28 trades) still marked open beside their live `_pt2` successors.

    `configured_pairs` is settings.live_paper_twin_pairs — the (live_tag, twin_tag) list currently
    in force. Omit it to check only rule 1 (the original behaviour).

    Scoped to configuration only — NOT the master switches (LIVE_ENABLED, KILL_SWITCH), which
    toggle for temporary/operational reasons and must not trigger a retirement note on every pause.
    Called once at startup: both changes are deliberate, infrequent operator actions that always
    come with a redeploy, not conditions needing sub-minute detection.

    Returns the closed twin_tags, for the caller to log."""
    # twin tag currently configured for each live tag; a DIFFERENT open epoch on that live tag is
    # a superseded twin rather than a retired book, and gets its own note.
    current_twin: dict[str, str] = dict(configured_pairs or [])
    closed: list[str] = []
    for row in active_twin_epochs(session):
        live_ok = any(row.live_tag.startswith(p) for p in live_strategy_prefixes)
        if not live_ok:
            note = f"{row.live_tag} removed from LIVE_STRATEGIES — no longer trading"
        elif configured_pairs is not None and current_twin.get(row.live_tag, row.twin_tag) != row.twin_tag:
            note = (f"superseded by {current_twin[row.live_tag]} — {row.live_tag} is still live, "
                    f"but this twin tag was replaced")
        else:
            continue
        end_twin_epoch(session, row.twin_tag, notes=note)
        closed.append(row.twin_tag)
    return closed


def insert_parity_event(
    session, *, twin_tag: str, live_tag: str, ticker: str, series: str | None = None,
    hours_to_close: float | None = None,
    parent_outcome: str | None = None, parent_price: int | None = None,
    twin_outcome: str | None = None, twin_price: int | None = None,
    twin_quantity: int | None = None,
    live_outcome: str | None = None, live_price: int | None = None,
    live_quantity: int | None = None,
    yes_mid: float | None = None, no_bid: int | None = None, no_ask: int | None = None,
    recorded_at: datetime | None = None,
) -> None:
    """Record one candidate-market decision across the three actors (incumbent paper book, fresh
    twin, real live attempt). Deliberately NOT flushed per row — committed with the cycle, so
    bulk per-candidate recording doesn't flush hundreds of times."""
    session.add(m.LivePaperParityEvent(
        recorded_at=recorded_at or _now(),
        twin_tag=twin_tag[:24],
        live_tag=live_tag[:32],
        market_ticker=ticker,
        series=(series or None) and series[:32],
        hours_to_close=hours_to_close,
        parent_outcome=(parent_outcome or None) and parent_outcome[:24],
        parent_price=parent_price,
        twin_outcome=(twin_outcome or None) and twin_outcome[:24],
        twin_price=twin_price,
        twin_quantity=twin_quantity,
        live_outcome=(live_outcome or None) and live_outcome[:32],
        live_price=live_price,
        live_quantity=live_quantity,
        yes_mid=yes_mid,
        no_bid=no_bid,
        no_ask=no_ask,
    ))


def log_system_event(
    session, *, level: str, component: str, message: str, raw: dict | None = None
) -> m.SystemEvent:
    row = m.SystemEvent(
        created_at=_now(),
        level=level,
        component=component,
        message=message,
        raw_json=_safe_json(raw or {}),
    )
    session.add(row)
    session.flush()
    return row


# --- Kalshi history backfill (separate provenance from live-collected tables) ----


def upsert_backfill_market(session, *, market_ticker: str, **fields) -> bool:
    """Insert a backfilled settled market if unseen; returns True when created."""
    existing = session.scalar(
        select(m.BackfillWeatherMarket).where(
            m.BackfillWeatherMarket.market_ticker == market_ticker
        )
    )
    if existing is not None:
        return False
    session.add(m.BackfillWeatherMarket(market_ticker=market_ticker, fetched_at=_now(), **fields))
    session.flush()
    return True


def pending_backfill_markets(session, *, limit: int) -> list:
    """Backfilled markets still awaiting candlesticks, newest settlements first."""
    return list(
        session.scalars(
            select(m.BackfillWeatherMarket)
            .where(m.BackfillWeatherMarket.candles_fetched.is_(False))
            .order_by(m.BackfillWeatherMarket.close_time.desc().nulls_last())
            .limit(limit)
        )
    )


def count_backfill_pending(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(m.BackfillWeatherMarket)
            .where(m.BackfillWeatherMarket.candles_fetched.is_(False))
        )
        or 0
    )


def mark_backfill_fetched(session, market, *, candle_count: int) -> None:
    market.candles_fetched = True
    market.candle_count = candle_count
    session.add(market)
    session.flush()


def replace_backfill_candles(session, market_ticker: str, rows: list[dict]) -> int:
    """Idempotent per-market candle store: drop any prior rows, insert the new set."""
    session.query(m.BackfillWeatherCandle).filter(
        m.BackfillWeatherCandle.market_ticker == market_ticker
    ).delete(synchronize_session=False)
    for row in rows:
        session.add(m.BackfillWeatherCandle(**row))
    session.flush()
    return len(rows)


# --- mmsell regime settled-history capture (rolling; Kalshi retains only ~70 days) -----


def upsert_regime_market(session, *, market_ticker: str, **fields) -> bool:
    """Insert a captured settled market if unseen; returns True when created.

    Insert-only on purpose. Enumeration re-runs every few hours over an overlapping window, so
    the same market is seen many times; re-writing it would keep resetting candles_fetched and
    the capture would re-fetch the same candles forever."""
    existing = session.scalar(
        select(m.BackfillRegimeMarket).where(
            m.BackfillRegimeMarket.market_ticker == market_ticker
        )
    )
    if existing is not None:
        return False
    session.add(m.BackfillRegimeMarket(market_ticker=market_ticker, fetched_at=_now(), **fields))
    session.flush()
    return True


def pending_regime_markets(session, *, limit: int) -> list:
    """Captured markets still awaiting candles, OLDEST settlements first.

    Oldest-first is the load-bearing choice, and it is the opposite of what this originally did.
    The first real run made the difference concrete: the initial enumeration queued 11,361
    markets, of which 9,986 were MLB — a series that settles daily and sits comfortably inside
    Kalshi's retention window — while 1,361 were NBA/NHL markets from a season that has ENDED and
    will never produce another row. Newest-first put ~10 hours of replaceable MLB work ahead of
    the irreplaceable set, which was aging toward the wall the whole time.

    The earlier rationale ("a fresh market is likeliest to still have a fetchable path") confused
    most-likely-to-SUCCEED with most-valuable-to-ATTEMPT. A market that settled today will still
    be fetchable tomorrow; one from two months ago may not be. Attempting the endangered rows
    first costs one request each, and `mark_regime_fetched` retires a market even when zero
    candles come back, so a genuinely-expired block drains immediately instead of wedging.

    In steady state the queue is shallow and the order barely matters — this ordering is about
    draining a backlog from the edge of the wall inward."""
    return list(
        session.scalars(
            select(m.BackfillRegimeMarket)
            .where(m.BackfillRegimeMarket.candles_fetched.is_(False))
            .order_by(m.BackfillRegimeMarket.close_time.asc().nulls_last())
            .limit(limit)
        )
    )


def count_regime_pending(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(m.BackfillRegimeMarket)
            .where(m.BackfillRegimeMarket.candles_fetched.is_(False))
        )
        or 0
    )


def mark_regime_fetched(session, market, *, candle_count: int) -> None:
    """Mark a market done. Called even when zero candles came back, so a market whose path
    Kalshi no longer serves cannot wedge the queue and starve fresh settlements."""
    market.candles_fetched = True
    market.candle_count = candle_count
    session.add(market)
    session.flush()


def replace_regime_candles(session, market_ticker: str, rows: list[dict]) -> int:
    """Idempotent per-market candle store: drop any prior rows, insert the new set."""
    session.query(m.BackfillRegimeCandle).filter(
        m.BackfillRegimeCandle.market_ticker == market_ticker
    ).delete(synchronize_session=False)
    for row in rows:
        session.add(m.BackfillRegimeCandle(**row))
    session.flush()
    return len(rows)


# --- forecast->settlement validation dataset (weather_forecast_outcomes) ----------


def event_outcomes_exist(session, event_ticker: str) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(m.WeatherForecastOutcome)
            .where(m.WeatherForecastOutcome.event_ticker == event_ticker)
        )
        or 0
    ) > 0


def pending_outcome_settlements(session, *, limit: int) -> list:
    """Settled events with no materialized outcome rows yet (anti-join), newest first —
    the validation backfill work queue. Newest target_date drains first so fresh
    settlements are graded promptly."""
    has_rows = (
        select(m.WeatherForecastOutcome.id)
        .where(m.WeatherForecastOutcome.event_ticker == m.WeatherSettlement.event_ticker)
        .exists()
    )
    return list(
        session.scalars(
            select(m.WeatherSettlement)
            .where(~has_rows)
            .order_by(m.WeatherSettlement.target_date.desc().nulls_last())
            .limit(limit)
        )
    )


def count_pending_outcome_settlements(session) -> int:
    has_rows = (
        select(m.WeatherForecastOutcome.id)
        .where(m.WeatherForecastOutcome.event_ticker == m.WeatherSettlement.event_ticker)
        .exists()
    )
    return (
        session.scalar(
            select(func.count()).select_from(m.WeatherSettlement).where(~has_rows)
        )
        or 0
    )


def replace_event_outcomes(session, event_ticker: str, rows: list[dict]) -> int:
    """Idempotent per-event store: drop any prior rows, insert the rebuilt set (mirrors
    replace_backfill_candles). Each dict is a full set of WeatherForecastOutcome kwargs."""
    session.query(m.WeatherForecastOutcome).filter(
        m.WeatherForecastOutcome.event_ticker == event_ticker
    ).delete(synchronize_session=False)
    for row in rows:
        session.add(m.WeatherForecastOutcome(materialized_at=_now(), **row))
    session.flush()
    return len(rows)


# --- Polymarket cross-market signal snapshots (separate provenance) --------------


def insert_polymarket_snapshots(session, rows: list[dict]) -> int:
    # `now` is hoisted so every bucket of one capture shares a timestamp and the ladder stays
    # recoverable as a unit — matching insert_weather_bucket_snapshots. Stamping per row (the
    # previous behaviour) gave all 451,198 stored rows a distinct microsecond, which silently
    # broke any "newest ladder" selection done by equality against max(captured_at): it matches
    # exactly one bucket. That is the defect behind weather_validation's pm_err=9.38F — see
    # docs/PMDIV_THESIS.md. Historical rows keep their per-row stamps and must be clustered.
    now = _now()
    for row in rows:
        session.add(m.PolymarketSnapshot(captured_at=now, source="polymarket_gamma", **row))
    session.flush()
    return len(rows)


def latest_polymarket_snapshot_at(session, city: str, kind: str, target_date: str):
    return session.scalar(
        select(func.max(m.PolymarketSnapshot.captured_at)).where(
            m.PolymarketSnapshot.city == city,
            m.PolymarketSnapshot.kind == kind,
            m.PolymarketSnapshot.target_date == target_date,
        )
    )


# --- live execution: orders / fills / positions ------------------------------------
# Order status lifecycle. COMMITTED = a real attempt that blocks a duplicate (event,strategy)
# entry. NON_TERMINAL = still in flight, reconcile/recover must resolve it. Excluded from the
# entry-dedup: "not_landed" (never reached the exchange -> may retry) and "canceled".
LIVE_COMMITTED_STATUSES = (
    "pending", "unknown", "submitted", "resting", "partial", "filled", "rejected",
)
LIVE_NONTERMINAL_STATUSES = ("pending", "unknown", "submitted", "resting", "partial")


def create_live_order(
    session,
    *,
    signal_id: int | None,
    ticker: str,
    event_ticker: str | None,
    strategy: str,
    side: str,
    action: str,
    limit_price: int | None,
    quantity: int,
    status: str,
    client_order_id: str,
    raw_order_json: Any | None = None,
) -> m.LiveOrder:
    # Same admission + lineage rule as create_paper_trade: a live order for an
    # unregistered NEW tag fails closed under NEW_ONLY/STRICT.
    arm_link_id = xos_enforcement.stamp_or_block(session, strategy, channel="live")
    row = m.LiveOrder(
        signal_id=signal_id,
        kalshi_order_id=None,
        client_order_id=client_order_id,
        market_ticker=ticker,
        event_ticker=event_ticker,
        strategy=strategy,
        created_at=_now(),
        side=side,
        action=action,
        limit_price=limit_price,
        quantity=quantity,
        status=status,
        experiment_deployment_arm_id=arm_link_id,
        raw_order_json=_safe_json(raw_order_json),
    )
    session.add(row)
    session.flush()
    return row


def update_live_order_status(
    session, order: m.LiveOrder, *, status: str,
    kalshi_order_id: str | None = None, cancel_reason: str | None = None, raw: Any | None = None,
) -> None:
    order.status = status
    if kalshi_order_id is not None:
        order.kalshi_order_id = kalshi_order_id
    if cancel_reason is not None:
        order.cancel_reason = cancel_reason[:300]
    if raw is not None:
        order.raw_order_json = _safe_json(raw)
    session.add(order)
    session.flush()


def live_order_exists(session, event_ticker: str, strategy: str) -> bool:
    """A committed live order already exists for this (event, strategy) — entry dedup."""
    return session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.event_ticker == event_ticker,
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.status.in_(LIVE_COMMITTED_STATUSES),
            m.LiveOrder.action == "buy",
        )
    ) > 0


def live_buy_exists_for_ticker(session, ticker: str, strategy: str) -> bool:
    """A committed live BUY already exists for this (market, strategy) — per-TICKER entry dedup
    for the mmsell books, which open one position per market (markets share an event, so the
    per-EVENT live_order_exists would wrongly block a second market in the same event).

    Note `LIVE_COMMITTED_STATUSES` deliberately excludes `canceled`, so a cancelled attempt does
    NOT block a fresh one — that is what makes the entry retry in mmsell/tracker.py possible."""
    return session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.status.in_(LIVE_COMMITTED_STATUSES),
            m.LiveOrder.action == "buy",
        )
    ) > 0


def live_attempt_stats(session, ticker: str, strategy: str) -> tuple[int, int | None]:
    """`(attempts, first_limit_price)` for this book's live BUY orders on `ticker`.

    Counts EVERY attempt regardless of status — a cancelled order is still an attempt — so a
    retry cap cannot be walked past by orders that never filled. The first attempt's limit price
    is the drift anchor: a retry is only worth making while the market is still near the price
    the original entry was sized against. Feeds mmsell/tracker.py's _maybe_retry_live."""
    rows = session.execute(
        select(m.LiveOrder.limit_price)
        .where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.action == "buy",
        )
        .order_by(m.LiveOrder.id)
    ).all()
    if not rows:
        return 0, None
    first = rows[0][0]
    return len(rows), (int(first) if first is not None else None)


def count_live_book_open(session, strategy: str) -> int:
    """Count a book's still-open live footprint: distinct tickers with a committed live BUY for
    `strategy` that have NOT settled flat. A resting/unfilled order (no snapshot yet) counts as
    open; a filled position counts; a settled (net-flat) position does not. Rejected/canceled
    orders are excluded. Used to cap concurrent live mmsell positions."""
    rows = session.execute(
        select(m.LiveOrder.market_ticker).where(
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.action == "buy",
            m.LiveOrder.status.in_(LIVE_NONTERMINAL_STATUSES + ("filled",)),
        ).distinct()
    ).all()
    n = 0
    for (ticker,) in rows:
        snap = latest_position_snapshot(session, ticker)
        if snap is not None:
            qty = snap.quantity_fp if snap.quantity_fp is not None else snap.quantity
            if qty is not None and abs(float(qty)) <= 0.01:
                continue  # settled / flat
        n += 1
    return n


def event_has_open_live_position(session, event_ticker: str, *, exclude_strategy: str | None = None) -> bool:
    """True if any committed live BUY on this event still holds an open position (Kalshi-truth:
    its latest position snapshot is net non-zero). Used to cap exposure to one position per
    event-day — a later-window entry is skipped while an earlier-window one is still open.
    `exclude_strategy` ignores the entry's own strategy (so it never blocks itself)."""
    rows = session.execute(
        select(m.LiveOrder.market_ticker, m.LiveOrder.strategy).where(
            m.LiveOrder.event_ticker == event_ticker,
            m.LiveOrder.action == "buy",
            m.LiveOrder.status.in_(LIVE_COMMITTED_STATUSES),
        )
    ).all()
    seen: set[str] = set()
    for ticker, strategy in rows:
        if (exclude_strategy and strategy == exclude_strategy) or ticker in seen:
            continue
        seen.add(ticker)
        snap = latest_position_snapshot(session, ticker)
        if snap is None:
            continue
        qty = snap.quantity_fp if snap.quantity_fp is not None else snap.quantity
        if qty is not None and abs(float(qty)) > 0.01:
            return True
    return False


def live_exit_order_exists(session, ticker: str, strategy: str) -> bool:
    """DEPRECATED for exit dedup (a rejected exit would permanently block re-attempts). Use
    live_exit_in_flight (in-flight guard) + count_exit_attempts (ladder/cap) instead."""
    return session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.client_order_id.like("exit:%"),
            m.LiveOrder.status.in_(LIVE_COMMITTED_STATUSES),
        )
    ) > 0


def live_exit_in_flight(session, ticker: str, strategy: str) -> bool:
    """An exit order is still working (non-terminal) — don't place a second one this cycle.
    A rejected/terminal exit does NOT count, so re-attempts can proceed."""
    return session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.client_order_id.like("exit:%"),
            m.LiveOrder.status.in_(LIVE_NONTERMINAL_STATUSES),
        )
    ) > 0


def count_exit_attempts(session, ticker: str, strategy: str) -> int:
    """All exit attempts (any status) for this position today — drives the price-escalation
    ladder and the bounded per-day attempt cap."""
    midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.client_order_id.like("exit:%"),
            m.LiveOrder.created_at >= midnight,
        )
    ) or 0)


def live_open_order_exists(session, ticker: str) -> bool:
    """Any in-flight live order on this market (for the risk existing_open_order gate)."""
    return session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.status.in_(LIVE_NONTERMINAL_STATUSES),
        )
    ) > 0


def get_live_order_by_client_id(session, client_order_id: str) -> m.LiveOrder | None:
    return session.scalar(
        select(m.LiveOrder).where(m.LiveOrder.client_order_id == client_order_id)
        .order_by(m.LiveOrder.id.desc())
    )


def get_nonterminal_live_orders(session) -> list[m.LiveOrder]:
    return list(session.scalars(
        select(m.LiveOrder).where(m.LiveOrder.status.in_(LIVE_NONTERMINAL_STATUSES))
    ).all())


def get_resting_live_orders(session, strategies: list[str] | None = None) -> list[m.LiveOrder]:
    """Live orders currently WORKING on the book — the drain target and the queue-sample set.

    `resting` specifically, not the whole non-terminal set: `pending`/`submitted`/`unknown` rows
    are in flight or unresolved, and cancelling one is either a no-op or a race against a fill
    the next reconcile would have picked up cleanly. Only a `resting` row is known to be sitting
    on the book with a Kalshi order id to act on.

    `strategies=None` means every book — which is what a kill-switch drain wants."""
    stmt = select(m.LiveOrder).where(m.LiveOrder.status == "resting")
    if strategies is not None:
        stmt = stmt.where(m.LiveOrder.strategy.in_(strategies))
    return list(session.scalars(stmt).all())


def insert_queue_tick(
    session, *, live_order_id: int | None, kalshi_order_id: str | None, strategy: str | None,
    ticker: str | None, queue_position: int | None, contracts_ahead: int | None,
    limit_price: int | None, rest_seconds: int | None, raw_json: Any | None = None,
) -> m.LiveOrderQueueTick:
    """Append one queue sample. A null `queue_position` is stored rather than dropped so a
    parse/API failure is COUNTABLE — silently writing nothing would make a broken sampler look
    exactly like a book with no resting orders."""
    row = m.LiveOrderQueueTick(
        live_order_id=live_order_id, kalshi_order_id=kalshi_order_id, strategy=strategy,
        market_ticker=ticker, queue_position=queue_position, contracts_ahead=contracts_ahead,
        limit_price=limit_price, rest_seconds=rest_seconds, raw_json=_safe_json(raw_json),
    )
    session.add(row)
    session.flush()
    return row


def fill_exists(session, kalshi_fill_id: str) -> bool:
    return session.scalar(
        select(func.count()).select_from(m.Fill).where(m.Fill.kalshi_fill_id == kalshi_fill_id)
    ) > 0


def insert_fill(
    session, *, kalshi_fill_id: str | None, kalshi_order_id: str | None, ticker: str,
    filled_at: datetime | None, side: str | None, action: str | None,
    price: int | None, quantity: int | None, fee: float | None, raw_fill_json: Any | None = None,
) -> m.Fill:
    row = m.Fill(
        kalshi_fill_id=kalshi_fill_id,
        kalshi_order_id=kalshi_order_id,
        market_ticker=ticker,
        filled_at=filled_at or _now(),
        side=side,
        action=action,
        price=price,
        quantity=quantity,
        fee=fee,
        raw_fill_json=_safe_json(raw_fill_json),
    )
    session.add(row)
    session.flush()
    return row


def fills_for_ticker(session, ticker: str) -> list[m.Fill]:
    return list(session.scalars(
        select(m.Fill).where(m.Fill.market_ticker == ticker).order_by(m.Fill.filled_at)
    ).all())


def fills_for_order(session, kalshi_order_id: str) -> list[m.Fill]:
    """Fills belonging to a given exchange order id (how a v1 IOC close is matched to its
    fill — the v1 order isn't visible via the v2 orders endpoint, but the fill carries it)."""
    if not kalshi_order_id:
        return []
    return list(session.scalars(
        select(m.Fill).where(m.Fill.kalshi_order_id == kalshi_order_id)
    ).all())


def insert_position_snapshot(
    session, *, ticker: str, side: str | None, quantity: int | None, avg_price: float | None,
    quantity_fp: float | None = None, market_exposure: float | None = None,
    realized_pnl: float | None = None, unrealized_pnl: float | None = None,
    raw_json: Any | None = None,
) -> m.Position:
    row = m.Position(
        market_ticker=ticker,
        captured_at=_now(),
        side=side,
        quantity=quantity,
        quantity_fp=quantity_fp,
        avg_price=avg_price,
        market_exposure=market_exposure,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        raw_json=_safe_json(raw_json),
    )
    session.add(row)
    session.flush()
    return row


def latest_position_snapshot(session, ticker: str) -> m.Position | None:
    return session.scalar(
        select(m.Position).where(m.Position.market_ticker == ticker)
        .order_by(m.Position.captured_at.desc())
    )


def _entry_order_for(session, ticker: str):
    """The most recent YES entry buy for a ticker (any status) — for strategy/entry price."""
    return session.scalar(
        select(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.action == "buy", m.LiveOrder.side == "yes",
        ).order_by(m.LiveOrder.created_at.desc())
    )


def open_live_positions(session) -> list[tuple]:
    """Open YES positions to manage exits for, driven by KALSHI TRUTH (the latest position
    snapshot per ticker with a net-long YES position), so a position is managed even if its
    local entry order row is corrupted (e.g. a 409 recorded as rejected). Strategy/entry come
    from the most recent YES entry order for the ticker. Returns
    (ticker, strategy, entry_price_cents, entry_at, qty)."""
    midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    snaps = session.scalars(
        select(m.Position).where(m.Position.captured_at >= midnight)
        .order_by(m.Position.captured_at.desc())
    ).all()
    seen: set[str] = set()
    out: list[tuple] = []
    for snap in snaps:
        tkr = snap.market_ticker
        if tkr in seen:
            continue
        seen.add(tkr)
        # Use the fractional size so a sub-1-share long (e.g. a 0.12 residual) is still managed.
        qty_fp = float(snap.quantity_fp) if snap.quantity_fp is not None else float(snap.quantity or 0)
        if qty_fp < 0.01:  # flat / net-NO / sub-0.01 dust -> nothing to close on the YES side
            continue
        qty = int(snap.quantity or 0)
        entry = _entry_order_for(session, tkr)
        strategy = (entry.strategy if entry else None) or "live"
        entry_price = int(entry.limit_price) if (entry and entry.limit_price) \
            else int(round(snap.avg_price or 0))
        entry_at = entry.created_at if entry else snap.captured_at
        out.append((tkr, strategy, entry_price, entry_at, qty))
    return out


def _no_entry_order_for(session, ticker: str):
    """The most recent NO entry buy for a ticker (any status) — mmsell's maker entries are
    recorded side='no' (unlike weather's side='yes'), so open_live_positions' YES-only lookup
    doesn't find them."""
    return session.scalar(
        select(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.action == "buy", m.LiveOrder.side == "no",
        ).order_by(m.LiveOrder.created_at.desc())
    )


def open_live_no_positions(session, strategy_prefix: str) -> list[tuple]:
    """Open NO positions to close out for a given strategy prefix (e.g. 'mmsell3'), driven by
    KALSHI TRUTH (the latest position snapshot per ticker, net-short-YES/net-long-NO). Unlike
    open_live_positions this has NO date bound — it's a one-shot end-of-strategy closeout query
    that must find every still-open position regardless of how long ago it was entered (mmsell
    positions can be held up to mmsell_max_hours_to_close, up to 14 days). Returns
    (ticker, strategy, entry_price_no_cents, entry_at, qty)."""
    tickers = session.scalars(
        select(m.LiveOrder.market_ticker).where(
            m.LiveOrder.strategy.like(f"{strategy_prefix}%"),
            m.LiveOrder.action == "buy", m.LiveOrder.side == "no",
        ).distinct()
    ).all()
    out: list[tuple] = []
    for tkr in tickers:
        snap = latest_position_snapshot(session, tkr)
        if snap is None:
            continue
        qty_fp = float(snap.quantity_fp) if snap.quantity_fp is not None else float(snap.quantity or 0)
        if qty_fp > -0.01:  # flat / net-YES / dust -> nothing to close on the NO side
            continue
        entry = _no_entry_order_for(session, tkr)
        strategy = (entry.strategy if entry else None) or strategy_prefix
        entry_price = int(entry.limit_price) if (entry and entry.limit_price) \
            else int(round(abs(snap.avg_price or 0)))
        entry_at = entry.created_at if entry else snap.captured_at
        # snap.quantity is SIGNED (negative for a NO position, via _to_count on the raw signed
        # position_fp) -- unlike open_live_positions' YES-only qty_fp>=0.01 gate, this branch's
        # qty_fp is always negative here, so snap.quantity is too; abs() both or `qty <= 0` in
        # the caller silently skips every real position (found live: 0 closeouts on 50 open).
        qty = abs(int(snap.quantity or round(qty_fp)))
        out.append((tkr, strategy, entry_price, entry_at, qty))
    return out


def closeout_attempt_count(session, ticker: str, strategy_prefix: str) -> int:
    """How many end-of-strategy close orders `strategy_prefix` has already fired at `ticker`.

    Bounds the closeout retry loop. `open_live_no_positions` re-derives its work list from
    Kalshi's position snapshot every cycle, so a position that can never be closed comes back
    every cycle and is tried again — the 2026-07-19 mmsell3 wind-down reached 650 attempts on
    one ticker. Counts EVERY attempt, not just the failures: a marketable IOC that keeps not
    filling leaves the position just as open as a rejection does, and both want a human.

    Matched on the '<strategy>_closeout' tag the closeout path writes (autoescape so the
    literal underscore isn't a LIKE wildcard)."""
    return session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.strategy.like(f"{strategy_prefix}%"),
            m.LiveOrder.strategy.endswith("_closeout", autoescape=True),
        )
    ) or 0


def other_live_no_strategies_on_ticker(
    session, ticker: str, exclude_prefix: str
) -> list[str]:
    """Distinct OTHER strategy tags (not matching `exclude_prefix`) that have ever placed a live
    NO-buy on this ticker.

    Exists because `open_live_no_positions`'s `qty` is the FULL Kalshi account-level position on
    a ticker — Kalshi has no notion of our internal per-book tags, so if two live books both hold
    contracts on the same market, one book's closeout would sell the OTHER book's contracts too,
    labeled as if they were its own. A non-empty result here means the position can't be safely
    attributed to `exclude_prefix` alone; the caller must skip the ticker rather than close it."""
    rows = session.scalars(
        select(m.LiveOrder.strategy).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.action == "buy", m.LiveOrder.side == "no",
            m.LiveOrder.strategy.is_not(None),
            m.LiveOrder.strategy.notlike(f"{exclude_prefix}%"),
        ).distinct()
    ).all()
    return sorted(rows)


def bucket_bid_path(session, ticker: str, *, after: datetime | None = None) -> list[float]:
    """The recorded yes-bid path for a bucket since `after` (for live exit evaluation),
    mirroring how the offline exit sweep reconstructs paths from weather_bucket_snapshots."""
    stmt = select(m.WeatherBucketSnapshot.yes_bid_cents).where(
        m.WeatherBucketSnapshot.market_ticker == ticker,
        m.WeatherBucketSnapshot.yes_bid_cents.is_not(None),
    )
    if after is not None:
        stmt = stmt.where(m.WeatherBucketSnapshot.captured_at > after)
    stmt = stmt.order_by(m.WeatherBucketSnapshot.captured_at)
    return [float(b) for b in session.scalars(stmt).all() if b is not None]


def live_realized_pnl_today(session) -> float:
    """Realized P&L (dollars) since UTC midnight — input to the max_daily_loss circuit
    breaker. Kalshi reports realized_pnl cumulatively per market, so we take the LATEST
    snapshot per ticker today and sum across markets (never sum repeated snapshots)."""
    midnight = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = session.scalars(
        select(m.Position).where(
            m.Position.captured_at >= midnight,
            m.Position.realized_pnl.is_not(None),
        ).order_by(m.Position.captured_at.desc())
    ).all()
    latest: dict[str, float] = {}
    for row in rows:
        if row.market_ticker not in latest:
            latest[row.market_ticker] = float(row.realized_pnl or 0.0)
    return sum(latest.values())


def live_total_exposure(session, *, lookback_hours: float = 48.0) -> float:
    """Total cost basis at risk across every OPEN live position, in dollars — the input to the
    `max_total_exposure` breaker.

    Denominated identically to the per-market check (`LiveExecutor._market_exposure`):
    `|quantity| x avg_price / 100`, i.e. what we PAID, not the current mark. For the NO books
    (mmsell/theta) `avg_price` is the cost basis on the held side, so a 94c NO contract counts
    as $0.94 of capital at risk — which is exactly the real downside, since a losing tail-sell
    settles at zero.

    Takes the LATEST snapshot per ticker (Kalshi reports positions cumulatively) and counts a
    position on EITHER side — `quantity_fp` is signed, so `abs()` is what makes a NO position
    count at all. `lookback_hours` bounds the scan; a ticker with no snapshot in that window is
    not an open position we are still tracking."""
    since = _now() - timedelta(hours=lookback_hours)
    rows = session.scalars(
        select(m.Position).where(m.Position.captured_at >= since)
        .order_by(m.Position.captured_at.desc())
    ).all()
    seen: set[str] = set()
    total = 0.0
    for row in rows:
        if row.market_ticker in seen:
            continue
        seen.add(row.market_ticker)
        qty = float(row.quantity_fp) if row.quantity_fp is not None else float(row.quantity or 0)
        if abs(qty) < 0.01:  # flat / dust — nothing at risk
            continue
        total += abs(qty) * float(row.avg_price or 0.0) / 100.0
    return total


# --- theta book (crypto spot + ladder snapshots) ---------------------------------


def insert_spot_candles(session, product: str, closes: dict[int, float]) -> int:
    """Insert new 1-min spot closes ({unix_minute: close}); skips minutes already stored.
    Bounded input (the tracker fetches only the gap since the latest stored minute)."""
    if not closes:
        return 0
    existing = {
        ts.replace(tzinfo=ts.tzinfo or timezone.utc)
        for ts in session.scalars(
            select(m.CryptoSpotCandle.minute_ts).where(
                m.CryptoSpotCandle.product == product,
                m.CryptoSpotCandle.minute_ts
                >= datetime.fromtimestamp(min(closes), tz=timezone.utc),
            )
        )
    }
    n = 0
    for ts_unix, close in closes.items():
        dt = datetime.fromtimestamp(ts_unix // 60 * 60, tz=timezone.utc)
        if dt in existing:
            continue
        session.add(m.CryptoSpotCandle(product=product, minute_ts=dt, close=float(close)))
        n += 1
    session.flush()
    return n


def latest_spot_minute(session, product: str) -> datetime | None:
    return session.scalar(
        select(func.max(m.CryptoSpotCandle.minute_ts)).where(
            m.CryptoSpotCandle.product == product
        )
    )


def oldest_spot_minute(session, product: str) -> datetime | None:
    """The earliest stored close, i.e. how far back the history currently reaches.

    Mirror of `latest_spot_minute`, and the input the BACKWARD backfill needs: forward gap
    filling asks "where did I stop", backfilling asks "where did I start"."""
    return session.scalar(
        select(func.min(m.CryptoSpotCandle.minute_ts)).where(
            m.CryptoSpotCandle.product == product
        )
    )


@contextlib.contextmanager
def bounded_statement(session, timeout_ms: int | None):
    """Run reads under a DATABASE statement timeout, confined to a SAVEPOINT.

    Exists for the paper shadow, which loads tens of thousands of rows on the trading loop's own
    thread. An application-side deadline can only notice a slow query after it returns, so the
    bound has to be enforced by Postgres — and enforcing it there brings two hazards a bare
    `SET LOCAL` plus `try/except` does not handle. Both were live defects:

    1. **A statement timeout ABORTS the transaction.** Catching the exception without rolling
       back leaves the session in a failed state, and every later statement on it — including
       the trading loop's own writes — fails with `InFailedSqlTransaction`. Research would take
       the book down with it. The savepoint confines the abort: rolling it back restores the
       enclosing transaction to a usable state.
    2. **`SET LOCAL` survives a savepoint RELEASE**, and without a savepoint it survives to the
       end of the transaction. Either way the shadow's research budget would silently become a
       timeout on the trading loop's own queries for the rest of the cycle. It is reset to
       DEFAULT before release; on the failure path the rollback reverts it.

    On a backend without statement timeouts (SQLite, in tests) the block runs plain — the bound
    is a production concern and its absence must not change behaviour. Proved against real
    Postgres by `tests/test_theta_shadow_postgres.py`; a mock cannot exhibit transaction abort.
    """
    if not timeout_ms or timeout_ms <= 0 or session.get_bind().dialect.name != "postgresql":
        yield
        return
    sp = session.begin_nested()
    try:
        session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
        yield
        session.execute(text("SET LOCAL statement_timeout = DEFAULT"))
        sp.commit()
    except BaseException:
        sp.rollback()
        raise


def load_spot_closes(session, product: str, since: datetime,
                     *, statement_timeout_ms: int | None = None) -> dict[int, float]:
    """{unix_minute: close} for the model's trailing window.

    `statement_timeout_ms` bounds the query at the database. See `bounded_statement` for why
    that needs a savepoint rather than a bare `SET LOCAL`, and `ThetaTracker._refresh_shadow_spot`
    for why the caller must pass the REMAINING cycle budget rather than the configured total.
    """
    with bounded_statement(session, statement_timeout_ms):
        rows = session.execute(
            select(m.CryptoSpotCandle.minute_ts, m.CryptoSpotCandle.close).where(
                m.CryptoSpotCandle.product == product,
                m.CryptoSpotCandle.minute_ts >= since,
            )
        ).all()
    out: dict[int, float] = {}
    for ts, close in rows:
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        out[int(ts.timestamp()) // 60 * 60] = float(close)
    return out


def prune_spot_candles(session, product: str, older_than: datetime) -> int:
    rows = session.scalars(
        select(m.CryptoSpotCandle).where(
            m.CryptoSpotCandle.product == product,
            m.CryptoSpotCandle.minute_ts < older_than,
        )
    ).all()
    for r in rows:
        session.delete(r)
    session.flush()
    return len(rows)


def insert_crypto_ladder_snapshots(session, rows: list[dict]) -> int:
    now = _now()
    for row in rows:
        session.add(m.CryptoLadderSnapshot(captured_at=now, **row))
    session.flush()
    return len(rows)


# --- XGAME in-play tape collection (game_market_matches / game_tape_snapshots) --------


def upsert_game_match(session, **fields) -> tuple[m.GameMarketMatch, bool]:
    """Get-or-create a matched game-market pair keyed by (kalshi_ticker, pm_token_id).
    Refreshes close_time on an existing row (Kalshi moves it as the game ends).
    Returns (row, created)."""
    row = session.scalar(
        select(m.GameMarketMatch).where(
            m.GameMarketMatch.kalshi_ticker == fields["kalshi_ticker"],
            m.GameMarketMatch.pm_token_id == fields["pm_token_id"],
        )
    )
    if row is not None:
        if fields.get("close_time") is not None:
            row.close_time = fields["close_time"]
        session.flush()
        return row, False
    row = m.GameMarketMatch(**fields)
    session.add(row)
    session.flush()
    return row, True


def active_game_matches(session, limit: int | None = None) -> list[m.GameMarketMatch]:
    stmt = (
        select(m.GameMarketMatch)
        .where(m.GameMarketMatch.status == "active")
        .order_by(m.GameMarketMatch.created_at.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def count_active_game_matches(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(m.GameMarketMatch)
            .where(m.GameMarketMatch.status == "active")
        )
        or 0
    )


def insert_game_tape_trades(session, match_id: int, venue: str, rows: list[dict]) -> int:
    """Insert tape rows, skipping (venue, trade_id) duplicates — polls overlap on
    purpose so no trade is lost between cycles. Portable dedup (sqlite tests +
    Postgres prod): pre-query existing ids in chunks, then guard in-batch dups."""
    if not rows:
        return 0
    ids = [r["trade_id"] for r in rows]
    existing: set[str] = set()
    for i in range(0, len(ids), 400):
        existing.update(
            session.scalars(
                select(m.GameTapeSnapshot.trade_id).where(
                    m.GameTapeSnapshot.venue == venue,
                    m.GameTapeSnapshot.trade_id.in_(ids[i : i + 400]),
                )
            )
        )
    now = _now()
    inserted = 0
    for r in rows:
        if r["trade_id"] in existing:
            continue
        existing.add(r["trade_id"])
        session.add(
            m.GameTapeSnapshot(captured_at=now, match_id=match_id, venue=venue, **r)
        )
        inserted += 1
    session.flush()
    return inserted


def recent_game_tape(
    session, match_id: int, venue: str, since: datetime
) -> list[tuple[datetime, float]]:
    """(traded_at, team_prob_cents) rows for a match+venue traded at or after `since`,
    ascending by trade time — the live signal window the XGAME book reads each cycle."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    rows = session.execute(
        select(m.GameTapeSnapshot.traded_at, m.GameTapeSnapshot.team_prob_cents)
        .where(
            m.GameTapeSnapshot.match_id == match_id,
            m.GameTapeSnapshot.venue == venue,
            m.GameTapeSnapshot.team_prob_cents.is_not(None),
            m.GameTapeSnapshot.traded_at >= since,
        )
        .order_by(m.GameTapeSnapshot.traded_at.asc())
    ).all()
    return [(ta, float(tp)) for ta, tp in rows]


def open_paper_trades_with_prefix(session, prefix: str) -> list[m.PaperTrade]:
    """Open paper trades whose strategy starts with `prefix` — used by self-managing books
    (XGAME) that own their exit rather than deferring to the shared paper engine."""
    return list(
        session.scalars(
            select(m.PaperTrade).where(
                m.PaperTrade.status == "open",
                m.PaperTrade.strategy.like(f"{prefix}%"),
            )
        )
    )
