"""TFAV book tracker (paper) — buy model-underpriced favorites on hourly crypto ladders.

The MIRROR of the theta book. theta sells model-OVERpriced tails (buy NO at the no-bid);
tfav buys model-UNDERpriced FAVORITES (taker buy YES at the ask). Same recurring KXBTC*/
KXETH* ladders, same empirical spot-vol model (kalshi_bot/theta/spot.py), same final-hour
edge window — only the side and the edge inequality flip:

  - theta gates on  mid - 100*p_model >= edge   (the market overprices the tail),
  - tfav gates on   100*p_model - yes_ask >= edge (the market underprices the favorite).

Entry is a TAKER buy of YES at the ask on 65-90c favorites (band on the yes ASK, since that
is the price actually paid) inside the final hour whose model prob beats the ask by the edge,
qty capped by resting ask depth. Held to settlement — the shared paper engine settles it
exactly like theta (tfav is in the engine's no-timeout hold-to-settlement set).

Forward-test of scripts/kalshi_favbuy_study.py, which is still EXPLORATORY (its P2/P4 gates
are unproven); running it as a paper book is how the settled-trade data that decides it
accumulates. tfav shares theta's persisted spot window (crypto_spot_candles), so once theta
has run this cycle the spot fetch here is a near-empty gap fetch.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..obs.series_fetch import (
    SeriesFetchResult,
    fetch_markets_by_series,
    warn_on_fetch_outcome,
)
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close
from ..theta.spot import CoinbaseSpotClient, SpotModel, refresh_spot_model

logger = logging.getLogger(__name__)


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


def _strike(v) -> float | None:
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


@dataclass
class TfavCycleSummary:
    products_ok: int = 0
    markets_seen: int = 0
    # gate counters are CONTROL-book scoped (stable across releases); entry-outcome
    # counters accumulate across control + revision books, with per_book detail.
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
    # The series-addressed fetch's own report (XOS-000004).
    fetch: SeriesFetchResult = field(default_factory=SeriesFetchResult)
    per_book: dict[str, int] = field(default_factory=dict)


class TfavTracker:
    STRATEGY = "tfav"

    def __init__(self, client, settings: Settings, spot_client: CoinbaseSpotClient | None = None):
        self.client = client
        self.settings = settings
        self.spot = spot_client or CoinbaseSpotClient()

    def _books(self) -> list[dict]:
        """The control book (base knobs, tag 'tfav' — NEVER reparameterized) followed by the
        configured revision books. All share the scan/model; only the gates differ."""
        s = self.settings
        control = {
            "tag": self.STRATEGY,
            "lo": float(s.tfav_price_lo_cents),
            "hi": float(s.tfav_price_hi_cents),
            "edge": float(s.tfav_min_edge_cents),
            "ttemin": float(s.tfav_entry_min_minutes),
            "ttemax": float(s.tfav_entry_max_minutes),
        }
        return [control, *s.tfav_variant_list]

    def run_once(self, session) -> TfavCycleSummary:
        s = self.settings
        summ = TfavCycleSummary()
        now_unix = int(time.time())

        models: dict[str, SpotModel | None] = {}
        for product in sorted(set(s.theta_series_map.values())):
            model = refresh_spot_model(session, self.spot, product, trail_days=s.theta_trail_days)
            models[product] = model
            if model is not None:
                summ.products_ok += 1

        books = self._books()
        open_count: dict[str, int] = {}
        open_tickers: dict[str, set[str]] = {}
        per_event_open: dict[str, dict[str, int]] = {}
        for b in books:
            tag = b["tag"]
            open_count[tag] = repo.count_open_paper_positions(session, tag)
            open_tickers[tag] = repo.open_paper_position_tickers(session, tag)
            per_event_open[tag] = {}
            for tk in open_tickers[tag]:
                ev = tk.rsplit("-", 1)[0]
                per_event_open[tag][ev] = per_event_open[tag].get(ev, 0) + 1

        # One combined fetch report across every configured series, so an
        # HTTP 200 carrying an empty list is counted rather than mistaken for a
        # healthy fetch, and the empty/failed distinction survives (XOS-000004).
        fetch = SeriesFetchResult()
        for series, product in s.theta_series_map.items():
            model = models.get(product)
            one = fetch_markets_by_series(
                self.client, [series], book="tfav", max_pages=4, log=logger, warn=False,
            )
            markets = one.markets
            fetch.markets.extend(one.markets)
            fetch.per_series.update(one.per_series)
            fetch.failed.extend(f for f in one.failed if f not in fetch.failed)

            for mkt in markets:
                summ.markets_seen += 1
                ticker = mkt.get("ticker") or ""
                if not ticker:
                    continue
                htc_s = compute_time_to_close(mkt.get("close_time"))
                if htc_s is None:
                    continue
                tte_min = htc_s / 60.0
                if tte_min <= 0:
                    continue
                ya = _price_c(mkt, "yes_ask")
                yb = _price_c(mkt, "yes_bid")
                if ya is None or yb is None or not (0 < ya <= 100) or not (0 <= yb <= 100):
                    continue
                mid = (yb + ya) / 2.0
                strike_type = (mkt.get("strike_type") or "").lower()
                floor_k = _strike(mkt.get("floor_strike"))
                cap_k = _strike(mkt.get("cap_strike"))

                p = None
                if model is not None:
                    p = model.p_yes(now_unix, max(1, int(tte_min)), strike_type, floor_k, cap_k)
                if p is not None:
                    summ.model_priced += 1

                # control-scoped gate counters (window / band on the yes ask)
                if s.tfav_entry_min_minutes <= tte_min <= s.tfav_entry_max_minutes:
                    summ.in_window += 1
                    if s.tfav_price_lo_cents <= ya <= s.tfav_price_hi_cents:
                        summ.in_band += 1

                if _volume(mkt) < s.tfav_min_volume:
                    summ.skipped_illiquid += 1
                    continue

                metrics = None  # lazy: fetched once for the first book that wants in
                event = ticker.rsplit("-", 1)[0]
                for book in books:
                    tag = book["tag"]
                    if not (book["ttemin"] <= tte_min <= book["ttemax"]):
                        continue
                    if not (book["lo"] <= ya <= book["hi"]):
                        continue
                    if p is None:
                        if tag == self.STRATEGY:
                            summ.skipped_no_model += 1
                        continue
                    edge = p * 100.0 - ya
                    if edge < book["edge"]:
                        continue
                    if tag == self.STRATEGY:
                        summ.edged += 1

                    if ticker in open_tickers[tag]:
                        summ.already_open += 1
                        continue
                    if open_count[tag] >= s.tfav_max_open_positions or \
                            per_event_open[tag].get(event, 0) >= s.tfav_max_per_event:
                        summ.capped += 1
                        continue

                    # taker buy of YES at the ask, qty capped by resting ask depth
                    if metrics is None:
                        try:
                            ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
                        except AuthError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "tfav: orderbook fetch failed",
                                extra={"extra_fields": {"ticker": ticker,
                                                        "error": str(exc)[:200]}},
                            )
                            break  # no book can enter this market this cycle
                        metrics = compute_metrics(mkt, ob, top_n=s.orderbook_depth)
                    if not metrics.two_sided or metrics.best_yes_ask is None:
                        summ.skipped_illiquid += 1
                        break
                    price = metrics.best_yes_ask
                    if not (0 < price < 100):
                        summ.skipped_illiquid += 1
                        break
                    qty = min(s.tfav_order_size, metrics.depth_at_best_ask or 0)
                    if qty <= 0:
                        summ.skipped_illiquid += 1
                        break

                    fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                    repo.create_paper_trade(
                        session,
                        signal_id=None,
                        ticker=ticker,
                        strategy=tag,
                        side="yes",
                        action="buy",
                        assumed_price=price,
                        quantity=qty,
                        fill_assumption=(
                            f"[{tag}] buy yes @ {price:.0f}c mid {mid:.0f} "
                            f"p {p * 100.0:.0f} tte {tte_min:.0f}m"
                        ),
                        entry_fee=fee,
                        model_probability=p,
                        edge=edge,
                    )
                    repo.open_paper_position_for_trade(
                        session, ticker=ticker, strategy=tag, side="yes",
                        quantity=qty, avg_price=price,
                    )
                    open_count[tag] += 1
                    per_event_open[tag][event] = per_event_open[tag].get(event, 0) + 1
                    open_tickers[tag].add(ticker)
                    summ.opened += 1
                    summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                    summ.per_series[series] = summ.per_series.get(series, 0) + 1

        summ.fetch = fetch
        warn_on_fetch_outcome("tfav", fetch, logger)
        return summ
