"""Database write helpers used by the scanner.

Each helper takes an active session, adds/updates a row, flushes so the caller
gets a populated primary key, and returns the ORM object. Commit/rollback is
owned by the caller's `session_scope`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from . import models as m
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


def abandon_open_paper_trades(session, keep_prefixes: tuple[str, ...]) -> int:
    """Close out open paper trades/positions whose strategy doesn't start with one of
    keep_prefixes (used to clear a prior experiment when switching modes)."""
    closed = 0
    for trade in session.scalars(
        select(m.PaperTrade).where(m.PaperTrade.status == "open")
    ).all():
        if not (trade.strategy or "").startswith(keep_prefixes):
            trade.status = "abandoned"
            trade.closed_at = _now()
            session.add(trade)
            closed += 1
    for pos in session.scalars(
        select(m.PaperPosition).where(m.PaperPosition.status == "open")
    ).all():
        if not (pos.strategy or "").startswith(keep_prefixes):
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


def insert_mmsell_candidate_tick(
    session, ticker: str, metrics: MarketMetrics, *, series: str | None = None,
    hours_to_close: float | None = None, captured_at: datetime | None = None,
) -> None:
    """Record one orderbook tick for an IN-BAND mmsell CANDIDATE (opened this cycle or not) — the
    pre-entry price path a per-ticker fill replay needs ('would a resting buy-NO at the no-bid have
    been lifted before close?'). Complements insert_mmsell_tick (held positions only). Cheap:
    reuses the orderbook metrics the entry scan already fetched; deliberately NOT flushed per row
    (committed with the cycle) so bulk candidate capture doesn't flush hundreds of times."""
    session.add(m.MmSellCandidateTick(
        market_ticker=ticker,
        captured_at=captured_at or _now(),
        series=series,
        hours_to_close=hours_to_close,
        yes_bid=metrics.best_yes_bid,
        yes_ask=metrics.best_yes_ask,
        no_bid=metrics.best_no_bid,
        no_ask=metrics.best_no_ask,
        mid=metrics.midpoint,
        volume=metrics.volume,
    ))


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
    for row in rows:
        session.add(m.PolymarketSnapshot(captured_at=_now(), source="polymarket_gamma", **row))
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
    per-EVENT live_order_exists would wrongly block a second market in the same event)."""
    return session.scalar(
        select(func.count()).select_from(m.LiveOrder).where(
            m.LiveOrder.market_ticker == ticker,
            m.LiveOrder.strategy == strategy,
            m.LiveOrder.status.in_(LIVE_COMMITTED_STATUSES),
            m.LiveOrder.action == "buy",
        )
    ) > 0


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


def load_spot_closes(session, product: str, since: datetime) -> dict[int, float]:
    """{unix_minute: close} for the model's trailing window."""
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
