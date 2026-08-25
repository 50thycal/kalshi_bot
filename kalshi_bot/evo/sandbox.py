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
    BackfillRegimeCandle,
    BackfillRegimeMarket,
    BackfillWeatherCandle,
    BackfillWeatherMarket,
    CryptoLadderSnapshot,
    CryptoSpotCandle,
    MmSellPositionTick,
    PaperTrade,
)
from ..paper.engine import kalshi_fee
from . import budgets, fill_model, signals
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
DATASETS = ("backfill_weather", "mmsell", "crypto", "econ")

# The regime label RegimeHistoryCapture stamps on economic-release markets. The same two
# backfill tables also hold NFL/MLB/Elections, so this is what keeps "econ" meaning econ.
ECON_REGIME = "Econ"

_PROVENANCE = {
    "backfill_weather": "kalshi_rest_backfill",
    "mmsell": "mmsell_live_ticks",
    "crypto": "crypto_ladder_spot_settled",
    "econ": "kalshi_rest_regime_backfill",
}

# Which EXTERNAL signal metrics (evo/signals.py) each dataset can actually reconstruct
# during replay. A dataset can only offer a signal it has the historical inputs for:
# crypto ladder snapshots carry strikes and the spot feed is preserved, but the Kalshi
# REST weather archive has no Polymarket join and mmsell's tick tape has neither.
#
# A spec using a metric its dataset cannot compute is REJECTED rather than run. Left
# unchecked, every such condition would silently evaluate to None -> fail -> zero
# trades, and the agent would read "no edge" from a backtest that never evaluated its
# hypothesis at all. That is the same shape of lie as assuming a maker order fills.
DATASET_SIGNALS = {
    "backfill_weather": frozenset(),
    "mmsell": frozenset(),
    "crypto": frozenset({"spot_vs_strike"}),
    # No external signal is reconstructable here yet: the official CPI/payroll ACTUALS the
    # tickets also asked for are not collected, so only the order book replays.
    "econ": frozenset(),
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
        fmr = res.get("fill_model") or {}
        row = {
            "run_id": r.id,
            "kind": r.kind,
            "fingerprint": fp,
            "times_run_recently": freq[fp],
            "n_trades": res.get("n_trades"),
            "win_rate": res.get("win_rate"),
            "total_pnl_usd": res.get("total_pnl_usd"),
            "per_trade_usd": res.get("per_trade_usd"),
        }
        # Only for maker specs, where the optimistic number is the one that lies.
        if fmr.get("n_maker_trades"):
            row["fill_model_verdict"] = fmr.get("verdict")
            row["realizable_cents_per_contract"] = fmr.get("realizable_cents_per_contract")
        out.append(row)
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


def _crypto_candle(
    ticker: str, closed_at: datetime, snap: CryptoLadderSnapshot,
    spot_at: float | None = None,
) -> _Candle:
    """One crypto ladder snapshot -> a Quote (close_time made wall-relative, no lookahead).

    `spot_at` is the underlying's close at THIS candle's timestamp (never later), so
    spot_vs_strike replays with the value live would have seen — computed by the same
    signals.spot_vs_strike the live path uses, not a parallel implementation."""
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
        spot_vs_strike=signals.spot_vs_strike(
            snap.strike_type, snap.floor_strike, snap.cap_strike, spot_at,
        ),
    )
    return _Candle(ts=ts, quote=quote, price_low=yb)


def _econ_markets(
    session, spec, date_from: str | None, date_to: str | None
) -> Iterator[_Market]:
    """econ adapter: settled Kalshi ECONOMIC-release markets (CPI, payrolls, PCE, GDP, Fed...)
    from the regime backfill, replayed over their recorded candle path.

    Built because the fleet asked for it — four tickets since 2026-07-22 wanting "settled CPI
    market corpus ... as a run_backtest dataset (like 'crypto')", which is an agent-generated
    non-weather thesis naming its own data requirement.

    Regime-backed rather than CPI-specific on purpose: `backfill_regime_markets` /
    `backfill_regime_candles` already exist, already carry `regime`, and are already filled by
    the ride-along RegimeHistoryCapture, so payrolls/PCE/Fed come free with CPI. The same tables
    hold NFL/MLB/Elections history, so the regime filter is load-bearing — without it an "econ"
    backtest would quietly replay football and report a fabricated result under the right name.

    Coverage measured before building (mmsell_regime_backtest over KXCPI/KXCPIYOY/KXPAYROLL/
    KXPCE): 102 settled markets, 102 with candles, median candle span 335h. A monthly print
    sounds too sparse to backtest, but each print is a LADDER quoted for weeks.
    """
    df, dt = _parse_date(date_from), _parse_date(date_to)
    q = select(BackfillRegimeMarket).where(
        BackfillRegimeMarket.regime == ECON_REGIME,
        BackfillRegimeMarket.result.in_(("yes", "no")),  # unlabelled cannot score a trade
        BackfillRegimeMarket.close_time.isnot(None),
    )
    if df:
        q = q.where(BackfillRegimeMarket.close_time >= df)
    if dt:
        q = q.where(BackfillRegimeMarket.close_time <= dt)
    for market in session.scalars(q.order_by(BackfillRegimeMarket.close_time)):
        if not spec.universe.admits_ticker(market.market_ticker):
            continue
        candles = list(
            session.scalars(
                select(BackfillRegimeCandle)
                .where(BackfillRegimeCandle.market_ticker == market.market_ticker)
                .order_by(BackfillRegimeCandle.end_period_ts)
            )
        )
        if not candles:
            continue  # no price path -> nothing to replay; skipping beats scoring a loss
        yield _Market(
            ticker=market.market_ticker,
            result=market.result,
            month=(market.close_time.isoformat()[:7] if market.close_time else ""),
            candles=[
                _Candle(
                    ts=c.end_period_ts,
                    quote=_quote_from_candle(market, c),
                    price_low=c.price_low,
                )
                for c in candles
            ],
        )


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
            # spot as of EACH snapshot's own timestamp — _spot_at bisects to the
            # latest close at/before it, so the replay never sees the future.
            candles=[
                _crypto_candle(
                    ticker, close_t, s,
                    _spot_at(spot, product,
                             s.captured_at if s.captured_at.tzinfo
                             else s.captured_at.replace(tzinfo=timezone.utc)),
                )
                for s in snaps
            ],
        )


_ADAPTERS = {
    "backfill_weather": _weather_markets,
    "mmsell": _mmsell_markets,
    "crypto": _crypto_markets,
    "econ": _econ_markets,
}


def register_dataset(
    name: str,
    adapter,
    *,
    provenance: str,
    signals_available: frozenset[str] = frozenset(),
) -> None:
    """Register an extra replay corpus under the `synthetic:` namespace.

    This exists so the population layer's deterministic proving fixtures run through
    THIS replay loop rather than a second copy of it — a proving run that exercised
    different code from the real datasets would prove nothing about them.

    Two guards keep it honest. The name must be namespaced, so a fixture can never be
    mistaken for measured history; and `DATASETS` (the built-in tuple the agent-facing
    path in `cognition.py` validates against) is deliberately not extended, so no evo
    agent can backtest against a synthetic corpus and read the result as evidence."""
    if not name.startswith("synthetic:"):
        raise ValueError(f"registered dataset {name!r} must be namespaced 'synthetic:*'")
    if name in DATASETS:
        raise ValueError(f"cannot shadow built-in dataset {name!r}")
    _ADAPTERS[name] = adapter
    _PROVENANCE[name] = provenance
    DATASET_SIGNALS[name] = signals_available


def available_datasets() -> tuple[str, ...]:
    """Every replayable corpus, built-in and registered."""
    return tuple(sorted(_ADAPTERS))


def _trade(
    market: _Market,
    pos: dict,
    pnl: float,
    exit_reason: str,
    *,
    win: bool,
    exited_at: datetime | None = None,
    exit_price_cents: float | None = None,
    exit_fee: float = 0.0,
    settled: bool = True,
    exit_time_exact: bool = True,
) -> dict:
    """One closed replay trade. `cents_per_contract` and `maker_yes_c` are what the
    realizable projection needs: the optimistic per-contract result, tagged with the
    calibration cell of the resting order that produced it (None for taker entries,
    which cross the spread and have no fill to miss).

    The entry/exit timestamps, prices and fees are what a per-run virtual ledger needs
    to reconstruct concurrency and exposure (`evo/population/replay.py`). They are
    additive: the aggregate result fields are computed from `pnl`/`month`/`exit` exactly
    as before.

    `exit_time_exact` distinguishes the two kinds of exit time. A rule-based exit happened
    AT the quote that triggered it, so its timestamp is exact. A settlement exit did not:
    the replay only knows the last candle it observed, and settlement occurs at or after
    that. Treating the last observation as the settlement time would close the position
    early and understate overlap, so it is published as a LOWER BOUND and flagged, and the
    ledger excludes inexact exits from exact concurrency accounting."""
    qty = pos["qty"] or 1
    return {
        "ticker": market.ticker,
        "month": market.month,
        "pnl": pnl,
        "exit": exit_reason,
        "win": win,
        "maker_yes_c": pos["maker_yes_c"],
        "cents_per_contract": 100.0 * pnl / qty,
        # --- ledger detail ---
        "side": pos["side"],
        "style": pos.get("style"),
        "quantity": pos["qty"],
        "entry_price_cents": pos["price"],
        "exit_price_cents": exit_price_cents,
        "entered_at": pos["entered_at"],
        "exited_at": exited_at,
        "exit_time_exact": exit_time_exact,
        "fees": round(float(pos["fee"]) + float(exit_fee), 6),
        "settled": settled,
    }


def _fill_model_report(trades: list[dict], *, applied: bool, blocked: int) -> dict:
    """Optimistic vs realizable, side by side, in the result an agent actually reads.

    The optimistic number is what the replay banked assuming every resting order it
    placed got hit. The realizable number projects the SAME trades' entry-price mix
    through the live fill calibration, so it carries the adverse-selection correction:
    what the fills a maker really gets are worth. `verdict` is the one-word gate —
    MIRAGE means the paper edge is an artifact of fills we would never receive."""
    hist: dict[int, list] = {}
    for t in trades:
        yes_c = t.get("maker_yes_c")
        if yes_c is None:
            continue
        cell = hist.setdefault(int(yes_c), [0, 0.0])
        cell[0] += 1
        cell[1] += t["cents_per_contract"]
    proj = fill_model.project_realizable({k: (v[0], v[1]) for k, v in hist.items()})
    return {
        "version": fill_model.CALIBRATION_VERSION,
        "source": fill_model.CALIBRATION_SOURCE,
        "applied": applied,
        "markets_blocked": blocked,
        "n_maker_trades": proj["total_n"],
        "coverage": proj["coverage"] if proj["coverage"] is not None else 0.0,
        "est_fill_rate": proj["est_fill_rate"],
        "optimistic_cents_per_contract": proj["opt_cents"],
        "realizable_cents_per_contract": proj["est_realizable_cents"],
        "verdict": fill_model.verdict(proj),
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
    return_trades: bool = False,
    skip_crossed_quotes: bool = False,
) -> tuple[dict | None, str | None]:
    """Replay the spec over settled history. Returns (result, None) or (None, err).
    Result: n, wins, gross/net P&L, per-trade, max drawdown, by-month split.

    persist=False skips writing the EvoSandboxRun row (and returns before any write), so
    the whole call is pure SELECT — used by the read-only ops probe against a read-only DB.

    return_trades=True adds the per-trade tape under `trades`. It is off by default
    because the tape is large and the agent-facing path only ever reads the aggregates;
    the population layer turns it on to build a per-run virtual ledger. The tape is
    never persisted into `EvoSandboxRun.result_json`.

    skip_crossed_quotes=True refuses to trade a step whose recorded book is crossed
    (bid >= ask). Off by default: skipping changes the replay result for every caller,
    which is a shared execution-semantics change and belongs to Platform Change Review.
    Crossed quotes are always COUNTED (`crossed_quotes` in the result) regardless, so
    the defect is visible to everyone without changing anyone's numbers."""
    if dataset not in _ADAPTERS:
        return None, f"unknown dataset {dataset!r} (available: {available_datasets()})"
    spec, err = validate_spec(spec_doc, max_bytes=settings.strategy_spec_max_bytes)
    if err:
        return None, err
    unsupported = sorted(
        {c.metric for c in spec.entry.conditions if c.metric in signals.SIGNAL_METRICS}
        - DATASET_SIGNALS.get(dataset, frozenset())
    )
    if unsupported:
        # Reject loudly: a silent zero-trade result would be read as "no edge".
        return None, (
            f"dataset {dataset!r} cannot replay {', '.join(unsupported)} — it has no "
            f"historical source for it, so this spec would evaluate to zero trades for "
            f"a reason unrelated to your hypothesis. Datasets providing it: "
            + (", ".join(sorted(d for d, s in DATASET_SIGNALS.items()
                                if set(unsupported) <= s)) or "none yet")
        )
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
    markets_blocked = 0  # resting orders the measured fill curve says would never be hit
    crossed_quotes = 0  # corrupt (bid >= ask) steps skipped; see the candle loop
    gate_applied = False
    for market in _ADAPTERS[dataset](session, spec, date_from, date_to):
        if time.monotonic() > deadline or rows_processed >= max_rows:
            truncated = True
            break
        markets_considered += 1
        rows_processed += len(market.candles)
        open_pos: dict | None = None
        maker_gate: bool | None = None  # calibrated verdict for THIS market
        maker_gate_decided = False
        for candle in market.candles:
            quote = candle.quote
            # A crossed book (bid at or above ask) is impossible in a real order book:
            # it means the recorded quote is corrupt, and trading against it would mint
            # risk-free P&L out of a data defect.
            #
            # Counting is unconditional and inert — it only adds a diagnostic to the
            # result. SKIPPING is opt-in, because refusing to trade a step changes what
            # every existing caller's replay returns, and that is a shared
            # replay/execution semantics change. It needs Platform Change Review before
            # it can become the default; until then existing callers keep their exact
            # current behavior and only the population layer opts in.
            if (
                quote.yes_bid is not None
                and quote.yes_ask is not None
                and quote.yes_bid >= quote.yes_ask
            ):
                crossed_quotes += 1
                if skip_crossed_quotes:
                    continue
            if open_pos is None:
                intent = entry_signal(spec, quote)
                if intent is None:
                    continue
                price = intent["limit_price_cents"]
                if intent["style"] == "maker":
                    if settings.sandbox_maker_fill_model and not maker_gate_decided:
                        # Decided ONCE per market: the calibration measures a resting
                        # order's lifetime fill rate, so re-drawing each candle would
                        # compound to a certain fill and restore the very assumption
                        # being corrected. See evo/fill_model.py.
                        maker_gate = fill_model.maker_order_fills(
                            ticker=market.ticker, side=intent["side"],
                            limit_price_cents=price,
                        )
                        maker_gate_decided = True
                        if maker_gate is not None:
                            gate_applied = True
                    if maker_gate is False:
                        markets_blocked += 1
                        break  # a maker never gets hit here — no entry on this market
                    if maker_gate is None:
                        # No trusted measurement at this price (or the model is off):
                        # keep the trade-through heuristic rather than guess a rate.
                        if candle.price_low is None or candle.price_low >= price:
                            continue
                qty = intent["quantity"]
                fee = kalshi_fee(price, qty)
                open_pos = {
                    "side": intent["side"],
                    "style": intent["style"],
                    "price": price,
                    "qty": qty,
                    "fee": fee,
                    "entered_at": candle.ts,
                    "mids": [],
                    "maker_yes_c": (
                        fill_model.yes_equivalent_cents(intent["side"], price)
                        if intent["style"] == "maker" else None
                    ),
                }
                continue
            # manage open position. The mid tape starts at entry and is what the
            # path-dependent exits (confirmed_stop / volatility_exit) read.
            if quote.mid is not None:
                open_pos["mids"].append(quote.mid)
            reason = exit_signal(
                spec, quote, side=open_pos["side"],
                entry_price_cents=open_pos["price"],
                held_hours=_hours_between(open_pos["entered_at"], candle.ts),
                mid_history=open_pos["mids"],
            )
            if reason is not None:
                bid = quote.best_exit_bid(open_pos["side"])
                if bid is None:
                    continue
                exit_fee = kalshi_fee(bid, open_pos["qty"])
                # NB: entry side cost basis — yes positions exit at yes_bid, no at no_bid
                gross = open_pos["qty"] * (bid - open_pos["price"]) / 100.0
                trades.append(_trade(
                    market, open_pos, gross - open_pos["fee"] - exit_fee, reason,
                    win=gross > 0, exited_at=candle.ts, exit_price_cents=bid,
                    exit_fee=exit_fee, settled=False, exit_time_exact=True,
                ))
                open_pos = None
                # One entry per market, matching the live runner's per-strategy/ticker
                # dedup. Without this the replay re-enters the moment its own stop
                # fires, so an exit-rule study would measure a re-entry policy rather
                # than the exit rule it is comparing against holding.
                break
        if open_pos is not None:
            won = open_pos["side"] == market.result
            value = 100 if won else 0
            gross = open_pos["qty"] * (value - open_pos["price"]) / 100.0
            trades.append(_trade(
                market, open_pos, gross - open_pos["fee"], "settlement", win=won,
                # A LOWER BOUND, not the settlement time: the tape ends at the last
                # observed candle and settlement happens at or after it. Flagged inexact
                # so the ledger does not use it for exact concurrency or exposure.
                exited_at=market.candles[-1].ts if market.candles else None,
                exit_price_cents=float(value), settled=True, exit_time_exact=False,
            ))

    n = len(trades)
    total = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    by_month: dict[str, dict] = {}
    by_exit: dict[str, int] = {}
    for t in trades:
        m = by_month.setdefault(t["month"], {"n": 0, "pnl": 0.0})
        m["n"] += 1
        m["pnl"] = round(m["pnl"] + t["pnl"], 4)
        by_exit[t["exit"]] = by_exit.get(t["exit"], 0) + 1
    result = {
        "dataset": dataset,
        "provenance": provenance,
        "markets_considered": markets_considered,
        "rows_processed": rows_processed,
        "crossed_quotes": crossed_quotes,
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
        # Which exit rule actually fired, and how often — an exit spec that never
        # triggers is otherwise indistinguishable from one that holds by design.
        "by_exit": by_exit,
        "fill_model": _fill_model_report(
            trades, applied=gate_applied, blocked=markets_blocked
        ),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    if not persist:
        if return_trades:
            result["trades"] = trades
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
    if return_trades:
        # After the row is built, so the persisted result_json stays the aggregate view.
        result["trades"] = trades
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


def deactivate_strategy(
    session, agent_uuid: str, strategy_id: int, *, reason: str = ""
) -> tuple[EvoStrategy | None, str | None]:
    """Stop a live strategy. The counterpart to activate_strategy.

    Without this an agent could deploy but never withdraw: the only path to
    'inactive' was activate_strategy demoting a same-named sibling, so a strategy
    the owner had *measured* as negative-EV kept executing every cycle forever.
    Live cost of the gap: 0bb6dd17 backtested its own mmsell books over 4,339
    settled trades at -$0.0476/trade and filed eighteen capability tickets in six
    days trying to switch them off while they kept trading.

    Reversible on purpose — 'inactive' is already an activatable state, so an
    agent can fix a spec and redeploy rather than being forced to mint a new one.
    """
    row = session.get(EvoStrategy, strategy_id)
    if row is None:
        return None, f"strategy {strategy_id} not found"
    if row.agent_uuid != agent_uuid:
        return None, "cannot deactivate another agent's strategy"
    if row.status != "active":
        return None, (
            f"strategy {strategy_id} is {row.status}, not active — nothing to stop"
        )
    row.status = "inactive"
    session.flush()
    audit(session, "strategy_deactivated", agent_uuid=agent_uuid, strategy_id=row.id,
          name=row.name, revision=row.revision, reason=str(reason)[:500])
    return row, None


def deactivate_agent_strategies(session, agent_uuid: str, *, reason: str) -> int:
    """Turn off every live strategy an agent owns. Used at retirement: a retired
    agent's strategies stayed `active`, so strategy_runner kept placing orders for
    a bot that no longer exists and no longer has any way to intervene."""
    n = 0
    for row in session.scalars(
        select(EvoStrategy).where(
            EvoStrategy.agent_uuid == agent_uuid, EvoStrategy.status == "active"
        )
    ):
        row.status = "inactive"
        n += 1
    if n:
        session.flush()
        audit(session, "strategies_deactivated_bulk", agent_uuid=agent_uuid,
              count=n, reason=reason)
    return n


def your_strategies(session, agent_uuid: str, *, limit: int = 12) -> list[dict]:
    """An agent's own strategies WITH their numeric ids, newest first.

    activate_strategy takes the integer strategy_id, but save_strategy's outcome
    (which carries it) is not re-fed to the agent in the same heartbeat, and
    nothing else in the prompt listed the agent's strategies — so an agent only
    retained the NAME it chose and had no way to learn the id. Observed live:
    9 activation attempts, 0 successes, one agent retrying the same name five
    times; 30 strategies sat 'validated' and never once reached 'active', so the
    autonomous strategy_runner never ran at all. This is the missing link."""
    rows = session.scalars(
        select(EvoStrategy)
        .where(EvoStrategy.agent_uuid == agent_uuid)
        .order_by(EvoStrategy.id.desc())
        .limit(limit)
    )
    return [
        {"strategy_id": r.id, "name": r.name, "status": r.status, "revision": r.revision}
        for r in rows
    ]


def active_strategies(session, agent_uuid: str) -> list[EvoStrategy]:
    return list(
        session.scalars(
            select(EvoStrategy).where(
                EvoStrategy.agent_uuid == agent_uuid, EvoStrategy.status == "active"
            )
        )
    )
