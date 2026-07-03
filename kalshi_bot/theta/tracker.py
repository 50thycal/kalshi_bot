"""Theta book tracker (paper) — sell model-overpriced tails on hourly crypto ladders.

Validated basis (docs/THETA_THESIS.md, scripts/kalshi_theta_study.py, 2026-07-03):
selling every tail at the posted quote is ~0 EV (P1 FAIL — the quotes are calibrated),
but (a) the realized maker-SELL flow on these series nets +5.2c/contract with the edge
concentrated inside the final hour (P2), and (b) a trailing spot-vol model separates the
dead tails from the live ones: selling only model-overpriced 3-40c tails at the ask
netted +4.4c/contract net of worst-case fees, while model-fair tails lost (P3). So the
book trades ONLY:
  - markets with <= entry_max_minutes to settlement (the final-hour edge window),
  - yes-mid in the tail band (3-40c),
  - model excess (mid - 100*P_model) >= min_edge_cents.
Entry is the maker-sell convention the mmsell book uses: sell YES at the ask == buy NO
at the no-bid, held the <1h to settlement (the shared paper engine settles it).

Every cycle also snapshots the near-settlement ladders WITH the model probability into
crypto_ladder_snapshots — the accumulating research dataset — and maintains the rolling
1-min spot window in crypto_spot_candles.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close
from .spot import CoinbaseSpotClient, SpotModel

logger = logging.getLogger(__name__)


@dataclass
class ThetaCycleSummary:
    products_ok: int = 0
    markets_seen: int = 0
    snapshot_rows: int = 0
    in_window: int = 0
    in_band: int = 0
    model_priced: int = 0
    edged: int = 0
    opened: int = 0
    already_open: int = 0
    capped: int = 0
    skipped_no_model: int = 0
    skipped_illiquid: int = 0
    per_series: dict[str, int] = field(default_factory=dict)


def _price_c(market: dict, key: str) -> float | None:
    """Read a market-object price as cents, tolerating int-cent vs dollar-string fields."""
    v = market.get(f"{key}_dollars")
    if v not in (None, ""):
        try:
            return float(v) * 100.0
        except (TypeError, ValueError):
            return None
    v = market.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _volume(market: dict) -> float:
    for k in ("volume_fp", "volume"):
        v = market.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


class ThetaTracker:
    STRATEGY = "theta"

    def __init__(self, client, settings: Settings, spot_client: CoinbaseSpotClient | None = None):
        self.client = client
        self.settings = settings
        self.spot = spot_client or CoinbaseSpotClient()

    # -- spot maintenance ---------------------------------------------------
    def _refresh_spot(self, session, product: str) -> SpotModel | None:
        s = self.settings
        now = datetime.now(timezone.utc)
        trail_start = now - timedelta(days=s.theta_trail_days)
        latest = repo.latest_spot_minute(session, product)
        # Gap fetch: from the latest stored minute (or the full trailing window on first
        # run) up to now. Coinbase serves ~300 candles/call; the client chunks.
        fetch_from = trail_start if latest is None else latest
        if fetch_from.tzinfo is None:
            fetch_from = fetch_from.replace(tzinfo=timezone.utc)
        start_unix = int(fetch_from.timestamp())
        end_unix = int(now.timestamp())
        if end_unix - start_unix >= 60:
            closes = self.spot.candles(product, start_unix, end_unix)
            if closes:
                repo.insert_spot_candles(session, product, closes)
        repo.prune_spot_candles(session, product, trail_start - timedelta(days=1))
        stored = repo.load_spot_closes(session, product, trail_start)
        if len(stored) < SpotModel.MIN_SAMPLES:
            logger.warning(
                "theta: spot window too thin; model disabled this cycle",
                extra={"extra_fields": {"product": product, "minutes": len(stored)}},
            )
            return None
        return SpotModel(stored, trail_days=s.theta_trail_days)

    # -- one cycle ------------------------------------------------------------
    def run_once(self, session) -> ThetaCycleSummary:
        s = self.settings
        summ = ThetaCycleSummary()
        now_unix = int(time.time())

        models: dict[str, SpotModel | None] = {}
        for product in sorted(set(s.theta_series_map.values())):
            models[product] = self._refresh_spot(session, product)
            if models[product] is not None:
                summ.products_ok += 1

        open_count = repo.count_open_paper_positions(session, self.STRATEGY)
        open_tickers = repo.open_paper_position_tickers(session, self.STRATEGY)
        per_event_open: dict[str, int] = {}
        for tk in open_tickers:
            ev = tk.rsplit("-", 1)[0]
            per_event_open[ev] = per_event_open.get(ev, 0) + 1

        snapshot_rows: list[dict] = []
        for series, product in s.theta_series_map.items():
            model = models.get(product)
            markets: list[dict] = []
            cursor: str | None = None
            for _ in range(4):
                try:
                    page = self.client.get_markets(
                        status="open", series_ticker=series, limit=200, cursor=cursor
                    )
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001 — a series fetch must not kill the cycle
                    logger.warning(
                        "theta: markets fetch failed",
                        extra={"extra_fields": {"series": series, "error": str(exc)[:200]}},
                    )
                    break
                markets.extend((page or {}).get("markets") or [])
                cursor = (page or {}).get("cursor") or None
                if not cursor:
                    break

            for mkt in markets:
                summ.markets_seen += 1
                ticker = mkt.get("ticker") or ""
                if not ticker:
                    continue
                htc_s = compute_time_to_close(mkt.get("close_time"))
                if htc_s is None:
                    continue
                tte_min = htc_s / 60.0
                if tte_min > s.theta_snapshot_max_minutes or tte_min <= 0:
                    continue
                yb = _price_c(mkt, "yes_bid")
                ya = _price_c(mkt, "yes_ask")
                if yb is None or ya is None or not (0 <= yb <= 100) or not (0 < ya <= 100):
                    continue
                mid = (yb + ya) / 2.0
                strike_type = (mkt.get("strike_type") or "").lower()
                floor_k, cap_k = mkt.get("floor_strike"), mkt.get("cap_strike")

                p = None
                if model is not None:
                    p = model.p_yes(now_unix, max(1, int(tte_min)), strike_type, floor_k, cap_k)
                if p is not None:
                    summ.model_priced += 1
                excess = (mid - p * 100.0) if p is not None else None

                snapshot_rows.append({
                    "series": series,
                    "event_ticker": ticker.rsplit("-", 1)[0],
                    "market_ticker": ticker,
                    "strike_type": strike_type or None,
                    "floor_strike": float(floor_k) if floor_k not in (None, "") else None,
                    "cap_strike": float(cap_k) if cap_k not in (None, "") else None,
                    "yes_bid_cents": yb,
                    "yes_ask_cents": ya,
                    "mid_cents": mid,
                    "volume": _volume(mkt),
                    "minutes_to_close": tte_min,
                    "spot": model.spot_at(now_unix) if model is not None else None,
                    "model_p": p,
                    "model_excess_cents": excess,
                })

                # ---- entry rule (all gates validated in the probe) ----
                if not (s.theta_entry_min_minutes <= tte_min <= s.theta_entry_max_minutes):
                    continue
                summ.in_window += 1
                if not (s.theta_price_lo_cents <= mid <= s.theta_price_hi_cents):
                    continue
                summ.in_band += 1
                if _volume(mkt) < s.theta_min_volume:
                    summ.skipped_illiquid += 1
                    continue
                if p is None:
                    summ.skipped_no_model += 1
                    continue
                if excess is None or excess < s.theta_min_edge_cents:
                    continue
                summ.edged += 1

                if ticker in open_tickers:
                    summ.already_open += 1
                    continue
                event = ticker.rsplit("-", 1)[0]
                if open_count >= s.theta_max_open_positions or \
                        per_event_open.get(event, 0) >= s.theta_max_per_event:
                    summ.capped += 1
                    continue

                # confirm the book two-sided + get the maker entry price (buy NO at no-bid
                # == sell YES at the ask), qty capped by resting depth like every paper book
                try:
                    ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "theta: orderbook fetch failed",
                        extra={"extra_fields": {"ticker": ticker, "error": str(exc)[:200]}},
                    )
                    continue
                metrics = compute_metrics(mkt, ob, top_n=s.orderbook_depth)
                if not metrics.two_sided or metrics.best_no_bid is None:
                    summ.skipped_illiquid += 1
                    continue
                price = metrics.best_no_bid
                if not (0 < price < 100):
                    summ.skipped_illiquid += 1
                    continue
                qty = min(s.theta_order_size, metrics.depth_at_best_bid or 0)
                if qty <= 0:
                    summ.skipped_illiquid += 1
                    continue

                fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                repo.create_paper_trade(
                    session,
                    signal_id=None,
                    ticker=ticker,
                    strategy=self.STRATEGY,
                    side="no",
                    action="buy",
                    assumed_price=price,
                    quantity=qty,
                    fill_assumption=(
                        f"[theta] sell yes @ {100 - price:.0f}c mid {mid:.0f} "
                        f"p {p * 100.0:.0f} tte {tte_min:.0f}m"
                    ),
                    entry_fee=fee,
                    model_probability=p,
                    edge=excess,
                )
                repo.open_paper_position_for_trade(
                    session, ticker=ticker, strategy=self.STRATEGY, side="no",
                    quantity=qty, avg_price=price,
                )
                open_count += 1
                per_event_open[event] = per_event_open.get(event, 0) + 1
                open_tickers.add(ticker)
                summ.opened += 1
                summ.per_series[series] = summ.per_series.get(series, 0) + 1

        if snapshot_rows:
            summ.snapshot_rows = repo.insert_crypto_ladder_snapshots(
                session, snapshot_rows[: s.theta_snapshot_rows_cap]
            )
        return summ
