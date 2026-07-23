"""Strategy sandbox: validation, historical replay/backtest, walk-forward
(spec §15). Runs the SAME interpreter as live paper trading (strategy_spec) over
the repo's provenance-labeled history tables, with hard row/time bounds and a
budget charge per run.

Datasets:
  backfill_weather — backfill_weather_markets + backfill_weather_candles
    (Kalshi REST archive; result labels; hourly candles). The largest settled
    corpus in the DB, and clearly provenance-separated per repo convention.

No-lookahead construction: the replay cursor walks candles in time order; entry
decisions see only the current candle's quote; settlement applies only after the
market's close_time."""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..models import (
    BackfillWeatherCandle,
    BackfillWeatherMarket,
    CryptoLadderSnapshot,
    CryptoSpotCandle,
    MmSellPositionTick,
    PaperTrade,
)
from ..paper.engine import kalshi_fee
from . import budgets
from .audit import audit
from .config import EvoSettings
from .marketdata import Quote
from .models import EvoSandboxRun, EvoStrategy
from .strategy_spec import entry_signal, exit_signal, validate_spec

logger = logging.getLogger(__name__)

# Each backtestable dataset maps to an adapter (see _ADAPTERS) that yields settled markets
# with an ordered, no-lookahead candle path. Weather is the reference adapter; mmsell
# replays the live orderbook tick path of settled mmsell paper trades (settlement from
# paper_trades.resolved_value). The replay loop itself is dataset-agnostic.
DATASETS = ("backfill_weather", "mmsell", "crypto")

_PROVENANCE = {
    "backfill_weather": "kalshi_rest_backfill",
    "mmsell": "mmsell_live_ticks",
    "crypto": "crypto_ladder_spot_settled",
}


@dataclass
class _Candle:
    """One normalized replay step: a quote at a point in time plus that interval's YES-cents
    low, used to model whether a resting maker order would have filled (price traded through)."""

    ts: datetime
    quote: Quote
    price_low: float | None


@dataclass
class _Market:
    """A settled market to replay: ordered candles + the realized outcome ('yes'|'no')."""

    ticker: str
    result: str
    month: str  # grouping key, YYYY-MM
    candles: list[_Candle]


def spec_fingerprint(spec_doc: dict | None) -> str:
    """Short stable hash of a strategy spec so a heartbeat prompt can show when the
    agent has already run an IDENTICAL backtest (same fingerprint => same result)."""
    try:
        canonical = json.dumps(spec_doc or {}, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = str(spec_doc)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:10]


def recent_runs(session, agent_uuid: str, *, limit: int = 8) -> list[dict]:
    """Compact view of the agent's most recent sandbox runs for the heartbeat prompt,
    each tagged with the spec fingerprint and how many of these recent runs share it
    (times_run_recently >= 2 => a repeated identical backtest — a KNOWN result, not new
    information). Makes self-repetition impossible to miss."""
    rows = list(
        session.scalars(
            select(EvoSandboxRun)
            .where(
                EvoSandboxRun.agent_uuid == agent_uuid,
                EvoSandboxRun.kind.in_(("backtest", "walkforward", "probe")),
            )
            .order_by(EvoSandboxRun.created_at.desc())
            .limit(limit)
        )
    )
    freq: dict[str, int] = {}
    prepared: list[tuple] = []
    for r in rows:
        spec = (
            (r.params_json or {}).get("spec") if isinstance(r.params_json, dict) else None
        )
        fp = spec_fingerprint(spec)
        freq[fp] = freq.get(fp, 0) + 1
        prepared.append((r, fp))
    out: list[dict] = []
    for r, fp in prepared:
        res = r.result_json or {}
        out.append(
            {
                "run_id": r.id,
                "kind": r.kind,
                "fingerprint": fp,
                "times_run_recently": freq[fp],
                "n_trades": res.get("n_trades"),
                "win_rate": res.get("win_rate"),
                "total_pnl_usd": res.get("total_pnl_usd"),
                "per_trade_usd": res.get("per_trade_usd"),
            }
        )
    return out


def _quote_from_candle(
    market: BackfillWeatherMarket, candle: BackfillWeatherCandle
) -> Quote:
    """Candle -> conservative Quote. Depth is unknown in candle history, so a
    deep synthetic ladder is used and fills are capped by the spec's size — the
    conservative element is the recorded bid/ask spread itself.

    close_time is translated to wall-relative (now + remaining-at-candle-time) so
    the interpreter's hours_to_close gates — which compare against wall clock —
    see the horizon the strategy would have seen live (no lookahead: `remaining`
    uses only the candle's own timestamp)."""
    ts = candle.end_period_ts if candle.end_period_ts.tzinfo else candle.end_period_ts.replace(
        tzinfo=timezone.utc
    )
    wall_close = None
    if market.close_time is not None:
        mc = market.close_time if market.close_time.tzinfo else market.close_time.replace(
            tzinfo=timezone.utc
        )
        wall_close = datetime.now(timezone.utc) + (mc - ts)
    yes_bid = candle.yes_bid_close
    yes_ask = candle.yes_ask_close
    if yes_bid is None and candle.price_close is not None:
        yes_bid = max(1.0, candle.price_close - 1)
    if yes_ask is None and candle.price_close is not None:
        yes_ask = min(99.0, candle.price_close + 1)
    yes_bid_i = int(yes_bid) if yes_bid is not None else None
    yes_ask_i = int(yes_ask) if yes_ask is not None else None
    no_bid_i = 100 - yes_ask_i if yes_ask_i is not None else None
    q = Quote(
        ticker=market.market_ticker,
        captured_at=ts,
        source="backfill_weather",
        status="active",
        result="",
        yes_bid=yes_bid_i,
        yes_ask=yes_ask_i,
        no_bid=no_bid_i,
        no_ask=100 - yes_bid_i if yes_bid_i is not None else None,
        yes_levels=[(yes_bid_i, 500)] if yes_bid_i else [],
        no_levels=[(no_bid_i, 500)] if no_bid_i else [],
        last_price=int(candle.price_close) if candle.price_close is not None else None,
        volume=candle.volume,
        open_interest=candle.open_interest,
        close_time=wall_close,
    )
    return q


def _hours_between(a: datetime, b: datetime) -> float:
    aa = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
    bb = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return (bb - aa).total_seconds() / 3600.0


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(str(s)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _weather_markets(
    session, spec, date_from: str | None, date_to: str | None
) -> Iterator[_Market]:
    """Reference adapter: settled backfill_weather markets + their hourly candles."""
    q = select(BackfillWeatherMarket).where(
        BackfillWeatherMarket.result.in_(("yes", "no")),
        BackfillWeatherMarket.candles_fetched.is_(True),
    )
    if date_from:
        q = q.where(BackfillWeatherMarket.target_date >= date_from)
    if date_to:
        q = q.where(BackfillWeatherMarket.target_date <= date_to)
    for market in session.scalars(q.order_by(BackfillWeatherMarket.close_time)):
        if not spec.universe.admits_ticker(market.market_ticker):
            continue
        candles = session.scalars(
            select(BackfillWeatherCandle)
            .where(BackfillWeatherCandle.market_ticker == market.market_ticker)
            .order_by(BackfillWeatherCandle.end_period_ts)
        )
        yield _Market(
            ticker=market.market_ticker,
            result=market.result,
            month=(market.target_date or "")[:7],
            candles=[
                _Candle(
                    ts=c.end_period_ts,
                    quote=_quote_from_candle(market, c),
                    price_low=c.price_low,
                )
                for c in candles
            ],
        )


def _market_result_from_trade(side: str | None, resolved_value: int | None) -> str | None:
    """Market outcome ('yes'/'no') from a settled paper trade. resolved_value is the
    settlement value of the HELD side (100 = that side paid out), NOT the market's YES
    value — so the market resolved YES iff a yes-side paid out or a no-side did not.
    (Verified against the live crypto ladder: side-adjusted resolved_value matches the
    spot-vs-strike outcome 231/231.) Returns None if the side is unknown."""
    if resolved_value not in (0, 100):
        return None
    if side == "yes":
        return "yes" if resolved_value == 100 else "no"
    if side == "no":
        return "yes" if resolved_value == 0 else "no"
    return None


def _mmsell_candle(ticker: str, closed_at: datetime, tick: MmSellPositionTick) -> _Candle:
    """One mmsell orderbook tick -> a Quote, with close_time made wall-relative (now +
    remaining-to-settlement at this tick) so the interpreter's hours_to_close gates see the
    horizon the live strategy would have (no lookahead: uses only the tick's own timestamp)."""
    ts = tick.captured_at if tick.captured_at.tzinfo else tick.captured_at.replace(
        tzinfo=timezone.utc
    )
    wall_close = datetime.now(timezone.utc) + (closed_at - ts)
    yes_bid, yes_ask = tick.yes_bid, tick.yes_ask
    no_bid = tick.no_bid if tick.no_bid is not None else (
        100 - yes_ask if yes_ask is not None else None
    )
    no_ask = tick.no_ask if tick.no_ask is not None else (
        100 - yes_bid if yes_bid is not None else None
    )
    quote = Quote(
        ticker=ticker,
        captured_at=ts,
        source="mmsell",
        status="active",
        result="",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        yes_levels=[(yes_bid, 500)] if yes_bid else [],
        no_levels=[(no_bid, 500)] if no_bid else [],
        last_price=int(tick.mid) if tick.mid is not None else None,
        volume=tick.volume,
        close_time=wall_close,
    )
    # No OHLC low in a point tick; the YES bid is a conservative maker fill-through proxy.
    return _Candle(ts=ts, quote=quote, price_low=yes_bid)


def _mmsell_markets(
    session, spec, date_from: str | None, date_to: str | None
) -> Iterator[_Market]:
    """mmsell adapter: each SETTLED mmsell paper trade is a settled market. mmsell trades
    the NO side, so the market outcome is side-adjusted from resolved_value (via
    _market_result_from_trade): a NO-side resolved_value=100 means NO paid out -> the market
    resolved NO. Price path is that ticker's captured orderbook ticks (mmsell_position_ticks).
    One market per ticker (settlement is a property of the market, not the trade)."""
    df, dt = _parse_date(date_from), _parse_date(date_to)
    q = select(PaperTrade).where(
        PaperTrade.strategy.like("mmsell%"),
        PaperTrade.status == "settled",
        PaperTrade.resolved_value.in_((0, 100)),
        PaperTrade.assumed_price.isnot(None),
        PaperTrade.created_at.isnot(None),
        PaperTrade.closed_at.isnot(None),
    )
    if df:
        q = q.where(PaperTrade.created_at >= df)
    if dt:
        q = q.where(PaperTrade.created_at <= dt)
    seen: set[str] = set()
    for tr in session.scalars(q.order_by(PaperTrade.closed_at)):
        if tr.market_ticker in seen or not spec.universe.admits_ticker(tr.market_ticker):
            continue
        result = _market_result_from_trade(tr.side, tr.resolved_value)
        if result is None:  # unknown side -> can't determine the market outcome
            continue
        seen.add(tr.market_ticker)
        ticks = list(
            session.scalars(
                select(MmSellPositionTick)
                .where(
                    MmSellPositionTick.market_ticker == tr.market_ticker,
                    MmSellPositionTick.mid.isnot(None),
                )
                .order_by(MmSellPositionTick.captured_at)
            )
        )
        if not ticks:
            continue
        closed_at = tr.closed_at if tr.closed_at.tzinfo else tr.closed_at.replace(
            tzinfo=timezone.utc
        )
        yield _Market(
            ticker=tr.market_ticker,
            result=result,
            month=(tr.created_at.isoformat()[:7] if tr.created_at else ""),
            candles=[_mmsell_candle(tr.market_ticker, closed_at, t) for t in ticks],
        )


def _crypto_product(series: str | None) -> str | None:
    """Map a Kalshi crypto series to its Coinbase spot product (only BTC/ETH have spot)."""
    s = series or ""
    if s.startswith("KXBTC"):
        return "BTC-USD"
    if s.startswith("KXETH"):
        return "ETH-USD"
    return None


def _settle_crypto(
    strike_type: str | None, floor: float | None, cap: float | None, spot: float | None
) -> str | None:
    """Crypto market outcome from the settling spot vs its strike. Validated against real
    settled outcomes (side-adjusted paper_trades) at 231/231. None if undecidable."""
    if spot is None:
        return None
    if strike_type == "greater":
        return None if floor is None else ("yes" if spot > floor else "no")
    if strike_type == "less":
        return None if cap is None else ("yes" if spot < cap else "no")
    if strike_type == "between":
        if floor is None or cap is None:
            return None
        return "yes" if floor <= spot <= cap else "no"
    return None


def _load_spot(session) -> dict[str, tuple[list[datetime], list[float]]]:
    """Preload crypto spot candles into {product: (sorted minute_ts[], close[])} for fast
    in-memory settlement lookup (the whole table is small — a few days x 2 products)."""
    out: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    rows = session.execute(
        select(CryptoSpotCandle.product, CryptoSpotCandle.minute_ts, CryptoSpotCandle.close)
        .order_by(CryptoSpotCandle.product, CryptoSpotCandle.minute_ts)
    )
    for product, ts, close in rows:
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        tslist, closelist = out[product]
        tslist.append(ts)
        closelist.append(close)
    return out


def _spot_at(spot: dict, product: str, when: datetime) -> float | None:
    """Latest spot close at/ before `when` for `product` (bisect on the sorted ts list)."""
    entry = spot.get(product)
    if not entry:
        return None
    tslist, closelist = entry
    i = bisect.bisect_right(tslist, when) - 1
    return closelist[i] if i >= 0 else None


def _crypto_candle(ticker: str, closed_at: datetime, snap: CryptoLadderSnapshot) -> _Candle:
    """One crypto ladder snapshot -> a Quote (close_time made wall-relative, no lookahead)."""
    ts = snap.captured_at if snap.captured_at.tzinfo else snap.captured_at.replace(
        tzinfo=timezone.utc
    )
    wall_close = datetime.now(timezone.utc) + (closed_at - ts)
    yb = int(snap.yes_bid_cents) if snap.yes_bid_cents is not None else None
    ya = int(snap.yes_ask_cents) if snap.yes_ask_cents is not None else None
    nb = 100 - ya if ya is not None else None
    quote = Quote(
        ticker=ticker,
        captured_at=ts,
        source="crypto",
        status="active",
        result="",
        yes_bid=yb,
        yes_ask=ya,
        no_bid=nb,
        no_ask=100 - yb if yb is not None else None,
        yes_levels=[(yb, 500)] if yb else [],
        no_levels=[(nb, 500)] if nb else [],
        last_price=int(snap.mid_cents) if snap.mid_cents is not None else None,
        volume=int(snap.volume) if snap.volume is not None else None,
        close_time=wall_close,
    )
    return _Candle(ts=ts, quote=quote, price_low=yb)


def _crypto_markets(
    session, spec, date_from: str | None, date_to: str | None
) -> Iterator[_Market]:
    """crypto adapter: each distinct crypto ladder market is a settled market whose outcome
    is DERIVED from the underlying spot vs the strike at close (crypto has no result column).
    Price path = that ticker's ladder snapshots. Only markets whose close falls within spot
    coverage are settleable (spot collection is recent), newest first."""
    spot = _load_spot(session)
    bounds = {p: (ts[0], ts[-1]) for p, (ts, _) in spot.items() if ts}
    if not bounds:
        return
    cov_min = min(lo for lo, _ in bounds.values())
    cov_max = max(hi for _, hi in bounds.values())
    df, dt = _parse_date(date_from), _parse_date(date_to)

    candidates = (
        select(
            CryptoLadderSnapshot.market_ticker,
            func.max(CryptoLadderSnapshot.series),
            func.max(CryptoLadderSnapshot.strike_type),
            func.max(CryptoLadderSnapshot.floor_strike),
            func.max(CryptoLadderSnapshot.cap_strike),
            func.max(CryptoLadderSnapshot.captured_at),
            func.min(CryptoLadderSnapshot.minutes_to_close),
        )
        .where(CryptoLadderSnapshot.market_ticker.isnot(None))
        .group_by(CryptoLadderSnapshot.market_ticker)
        .having(func.max(CryptoLadderSnapshot.captured_at) >= cov_min)
        .order_by(func.max(CryptoLadderSnapshot.captured_at).desc())
    )
    for ticker, series, st, floor, cap, last_cap, min_mtc in session.execute(candidates):
        if not spec.universe.admits_ticker(ticker):
            continue
        product = _crypto_product(series)
        if product is None:
            continue
        last_cap = last_cap if last_cap.tzinfo else last_cap.replace(tzinfo=timezone.utc)
        close_t = last_cap + timedelta(minutes=float(min_mtc or 0.0))
        if close_t > cov_max:  # closes after spot coverage -> not settleable yet
            continue
        if (df and close_t < df) or (dt and close_t > dt):
            continue
        result = _settle_crypto(st, floor, cap, _spot_at(spot, product, close_t))
        if result is None:
            continue
        snaps = list(
            session.scalars(
                select(CryptoLadderSnapshot)
                .where(
                    CryptoLadderSnapshot.market_ticker == ticker,
                    CryptoLadderSnapshot.mid_cents.isnot(None),
                )
                .order_by(CryptoLadderSnapshot.captured_at)
            )
        )
        if not snaps:
            continue
        yield _Market(
            ticker=ticker,
            result=result,
            month=close_t.isoformat()[:7],
            candles=[_crypto_candle(ticker, close_t, s) for s in snaps],
        )


_ADAPTERS = {
    "backfill_weather": _weather_markets,
    "mmsell": _mmsell_markets,
    "crypto": _crypto_markets,
}


def run_backtest(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    cohort_id: int,
    spec_doc: dict,
    dataset: str = "backfill_weather",
    date_from: str | None = None,
    date_to: str | None = None,
    strategy_id: int | None = None,
    heartbeat_id: int | None = None,
    kind: str = "backtest",
    charge_budget: bool = True,
    persist: bool = True,
) -> tuple[dict | None, str | None]:
    """Replay the spec over settled history. Returns (result, None) or (None, err).
    Result: n, wins, gross/net P&L, per-trade, max drawdown, by-month split.

    persist=False skips writing the EvoSandboxRun row (and returns before any write), so
    the whole call is pure SELECT — used by the read-only ops probe against a read-only DB."""
    if dataset not in DATASETS:
        return None, f"unknown dataset {dataset!r} (available: {DATASETS})"
    spec, err = validate_spec(spec_doc, max_bytes=settings.strategy_spec_max_bytes)
    if err:
        return None, err
    if charge_budget and not budgets.spend(
        session, agent_uuid, cohort_id, "sandbox_runs", 1
    ):
        return None, "sandbox-run budget exhausted"

    started = time.monotonic()
    deadline = started + settings.sandbox_max_seconds
    max_rows = settings.sandbox_max_rows
    provenance = _PROVENANCE[dataset]

    trades: list[dict] = []
    rows_processed = 0
    truncated = False
    markets_considered = 0
    for market in _ADAPTERS[dataset](session, spec, date_from, date_to):
        if time.monotonic() > deadline or rows_processed >= max_rows:
            truncated = True
            break
        markets_considered += 1
        rows_processed += len(market.candles)
        open_pos: dict | None = None
        for candle in market.candles:
            quote = candle.quote
            if open_pos is None:
                intent = entry_signal(spec, quote)
                if intent is None:
                    continue
                price = intent["limit_price_cents"]
                if intent["style"] == "maker":
                    # conservative: a resting maker order fills only if the market
                    # trades through the limit this step — approximated by price_low < limit
                    continue_fill = candle.price_low is not None and (
                        candle.price_low < price
                    )
                    if not continue_fill:
                        continue
                qty = intent["quantity"]
                fee = kalshi_fee(price, qty)
                open_pos = {
                    "side": intent["side"],
                    "price": price,
                    "qty": qty,
                    "fee": fee,
                    "entered_at": candle.ts,
                }
                continue
            # manage open position
            reason = exit_signal(
                spec, quote, side=open_pos["side"],
                entry_price_cents=open_pos["price"],
                held_hours=_hours_between(open_pos["entered_at"], candle.ts),
            )
            if reason is not None:
                bid = quote.best_exit_bid(open_pos["side"])
                if bid is None:
                    continue
                exit_fee = kalshi_fee(bid, open_pos["qty"])
                # NB: entry side cost basis — yes positions exit at yes_bid, no at no_bid
                gross = open_pos["qty"] * (bid - open_pos["price"]) / 100.0
                trades.append({
                    "ticker": market.ticker,
                    "month": market.month,
                    "pnl": gross - open_pos["fee"] - exit_fee,
                    "exit": reason,
                    "win": gross > 0,
                })
                open_pos = None
        if open_pos is not None:
            won = open_pos["side"] == market.result
            value = 100 if won else 0
            gross = open_pos["qty"] * (value - open_pos["price"]) / 100.0
            trades.append({
                "ticker": market.ticker,
                "month": market.month,
                "pnl": gross - open_pos["fee"],
                "exit": "settlement",
                "win": won,
            })

    n = len(trades)
    total = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    by_month: dict[str, dict] = {}
    for t in trades:
        m = by_month.setdefault(t["month"], {"n": 0, "pnl": 0.0})
        m["n"] += 1
        m["pnl"] = round(m["pnl"] + t["pnl"], 4)
    result = {
        "dataset": dataset,
        "provenance": provenance,
        "markets_considered": markets_considered,
        "rows_processed": rows_processed,
        "truncated": truncated,
        "n_trades": n,
        "wins": wins,
        "win_rate": round(wins / n, 3) if n else None,
        "total_pnl_usd": round(total, 4),
        "per_trade_usd": round(total / n, 4) if n else None,
        "per_trade_cents_per_contract": (
            round(100.0 * total / sum(spec.entry.size_contracts for _ in trades)
                  / 1.0, 3) if n else None
        ),
        "max_drawdown_usd": round(max_dd, 4),
        "by_month": by_month,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    if not persist:
        return result, None
    run = EvoSandboxRun(
        agent_uuid=agent_uuid,
        heartbeat_id=heartbeat_id,
        strategy_id=strategy_id,
        kind=kind,
        params_json={"spec": spec_doc, "date_from": date_from, "date_to": date_to},
        dataset=dataset,
        provenance=provenance,
        status="completed",
        result_json=result,
        rows_processed=rows_processed,
        elapsed_ms=result["elapsed_ms"],
    )
    session.add(run)
    session.flush()
    return result, None


def run_walkforward(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    cohort_id: int,
    spec_doc: dict,
    split_date: str,
    dataset: str = "backfill_weather",
    heartbeat_id: int | None = None,
) -> tuple[dict | None, str | None]:
    """Two-window walk-forward: fit-window read + held-out window read (the repo's
    split-half OOS convention). One budget charge covers both legs."""
    if not budgets.spend(session, agent_uuid, cohort_id, "sandbox_runs", 1):
        return None, "sandbox-run budget exhausted"
    in_sample, err = run_backtest(
        session, settings, agent_uuid=agent_uuid, cohort_id=cohort_id,
        spec_doc=spec_doc, dataset=dataset, date_to=split_date, heartbeat_id=heartbeat_id,
        kind="walkforward", charge_budget=False,
    )
    if err:
        return None, err
    out_sample, err = run_backtest(
        session, settings, agent_uuid=agent_uuid, cohort_id=cohort_id,
        spec_doc=spec_doc, dataset=dataset, date_from=split_date, heartbeat_id=heartbeat_id,
        kind="walkforward", charge_budget=False,
    )
    if err:
        return None, err
    return {"in_sample": in_sample, "out_of_sample": out_sample,
            "split_date": split_date}, None


# ---------------------------------------------------------------------------
# Strategy artifact lifecycle
# ---------------------------------------------------------------------------


def save_strategy(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    spec_doc: dict,
    heartbeat_id: int | None = None,
    graveyard_check: dict | None = None,
    forked_from_uuid: str | None = None,
) -> tuple[EvoStrategy | None, str | None]:
    spec, err = validate_spec(spec_doc, max_bytes=settings.strategy_spec_max_bytes)
    if err:
        return None, err
    prev = session.scalar(
        select(EvoStrategy)
        .where(EvoStrategy.agent_uuid == agent_uuid, EvoStrategy.name == spec.name)
        .order_by(EvoStrategy.revision.desc())
        .limit(1)
    )
    row = EvoStrategy(
        agent_uuid=agent_uuid,
        name=spec.name,
        revision=(prev.revision + 1) if prev else 1,
        spec_json=spec.model_dump(),
        validation_json={"validated": True},
        status="validated",
        heartbeat_id=heartbeat_id,
        forked_from_uuid=forked_from_uuid,
        graveyard_check_json=graveyard_check,
    )
    session.add(row)
    session.flush()
    return row, None


def activate_strategy(
    session, agent_uuid: str, strategy_id: int
) -> tuple[EvoStrategy | None, str | None]:
    """Autonomous activation (spec §15): approved interfaces + validation passed +
    no shared deployment required — all true by construction for a saved spec."""
    row = session.get(EvoStrategy, strategy_id)
    if row is None:
        return None, f"strategy {strategy_id} not found"
    if row.agent_uuid != agent_uuid:
        return None, "cannot activate another agent's strategy"
    if row.status not in ("validated", "inactive"):
        return None, f"strategy is {row.status}, not activatable"
    # one active revision per (agent, name): deactivate siblings
    for sibling in session.scalars(
        select(EvoStrategy).where(
            EvoStrategy.agent_uuid == agent_uuid,
            EvoStrategy.name == row.name,
            EvoStrategy.status == "active",
        )
    ):
        sibling.status = "inactive"
    row.status = "active"
    session.flush()
    audit(session, "strategy_activated", agent_uuid=agent_uuid, strategy_id=row.id,
          name=row.name, revision=row.revision)
    return row, None


def active_strategies(session, agent_uuid: str) -> list[EvoStrategy]:
    return list(
        session.scalars(
            select(EvoStrategy).where(
                EvoStrategy.agent_uuid == agent_uuid, EvoStrategy.status == "active"
            )
        )
    )
