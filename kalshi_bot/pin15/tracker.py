"""PIN15 book tracker (paper) — endgame settlement-average observation-pin on 15-min crypto.

Kalshi's 15-minute crypto up/down markets (KXBTC15M/KXETH15M) settle on the 60-SECOND AVERAGE of
the CF Benchmarks index over the final minute — not the last tick. So as a window closes the settle
is a partially-observed, DETERMINISTIC number: 2-3 min out, a spot displacement of >= a few bp from
the window's target ("Target Price" = the floor strike ~= spot at open) already pins the outcome
(validation: settles the drift way 96-100%, holding across vol quartiles), while the retail quote —
anchored to the flashing last-tick price — still prices the near-certain favorite at ~93-95c.

PIN15 takes the drift-favored side: buy YES (at the yes-ask) if spot > target, buy NO (at the
no-ask) if spot < target, inside the final few minutes, qty capped by resting depth, held to
settlement (the shared paper engine settles it, like theta/tfav — pin15 is in the engine's
no-timeout hold-to-settlement set). This is a TAKER book (it crosses to the favorite), so there is
no maker adverse-selection haircut; the one execution risk is depth at the ask, which paper fills
measure. The other risk — whether the ~300s worker loop actually lands in the T~120-180s window —
is recorded as T-at-entry in the fill_assumption so the paper P&L can be sliced by entry latency.

Unlike theta/tfav this needs no vol MODEL — the signal is the raw spot-vs-target displacement — so
it fetches only the CURRENT spot each cycle (a tiny direct Coinbase read, no DB write / no pruning,
keeping it decoupled from theta's persisted crypto_spot_candles window).

Forward-test of scripts/kalshi_pin15_study.py, whose P1/P2/P4 gates PASSED in-sample (P3's SPIKEFADE
mechanism failed — the edge is plain drift-favorite underpricing 2-3 min out, not a spike fade).
Correlation caution: it is a favorite-BUY, the family tfav died in; the material difference is the
now-vol-validated live spot-pin selection.
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
    warn_on_empty_series,
)
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close
from ..theta.spot import CoinbaseSpotClient

logger = logging.getLogger(__name__)


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
class Pin15CycleSummary:
    products_ok: int = 0
    markets_seen: int = 0
    in_window: int = 0        # in the final-minutes entry window
    priced: int = 0           # had a target + a current spot
    pinned: int = 0           # |displacement| cleared the threshold
    opened: int = 0
    already_open: int = 0
    capped: int = 0
    skipped_no_spot: int = 0
    skipped_illiquid: int = 0
    per_series: dict[str, int] = field(default_factory=dict)
    # The series-addressed fetch's own report (XOS-000004).
    fetch: SeriesFetchResult = field(default_factory=SeriesFetchResult)


class Pin15Tracker:
    STRATEGY = "pin15"

    def __init__(self, client, settings: Settings, spot_client: CoinbaseSpotClient | None = None):
        self.client = client
        self.settings = settings
        self.spot = spot_client or CoinbaseSpotClient()

    def _current_spot(self, product: str) -> float | None:
        """Latest 1-min close for `product` (a small direct Coinbase read; no DB write / prune, so
        it stays decoupled from theta's persisted spot window). None on any fetch failure."""
        now = int(time.time())
        closes = self.spot.candles(product, now - 600, now)  # last ~10 min
        if not closes:
            return None
        return closes[max(closes)]

    def run_once(self, session) -> Pin15CycleSummary:
        s = self.settings
        summ = Pin15CycleSummary()

        spots: dict[str, float | None] = {}
        for product in sorted(set(s.pin15_series_map.values())):
            sp = self._current_spot(product)
            spots[product] = sp
            if sp is not None:
                summ.products_ok += 1

        open_count = repo.count_open_paper_positions(session, self.STRATEGY)
        open_tickers = repo.open_paper_position_tickers(session, self.STRATEGY)
        per_event_open: dict[str, int] = {}
        for tk in open_tickers:
            ev = tk.rsplit("-", 1)[0]
            per_event_open[ev] = per_event_open.get(ev, 0) + 1

        # One combined fetch report across every configured series, so the
        # empty-series warning is emitted once per cycle rather than once per
        # series, and an HTTP 200 carrying an empty list is counted rather than
        # mistaken for a healthy fetch (XOS-000004).
        fetch = SeriesFetchResult()
        for series, product in s.pin15_series_map.items():
            spot = spots.get(product)
            one = fetch_markets_by_series(
                self.client, [series], book="pin15", max_pages=4, log=logger, warn=False,
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
                # entry window is in SECONDS (final few minutes), not the theta/tfav minutes
                if not (s.pin15_entry_min_seconds <= htc_s <= s.pin15_entry_max_seconds):
                    continue
                summ.in_window += 1

                target = _strike(mkt.get("floor_strike"))
                if target is None or target <= 0 or spot is None:
                    if spot is None:
                        summ.skipped_no_spot += 1
                    continue
                summ.priced += 1

                disp_bps = (spot - target) / target * 1e4  # signed; >0 => Up favored
                if abs(disp_bps) < s.pin15_min_disp_bps:
                    continue
                summ.pinned += 1
                side_up = disp_bps > 0
                side = "yes" if side_up else "no"

                if _volume(mkt) < s.pin15_min_volume:
                    summ.skipped_illiquid += 1
                    continue

                event = ticker.rsplit("-", 1)[0]
                if ticker in open_tickers:
                    summ.already_open += 1
                    continue
                if open_count >= s.pin15_max_open_positions or \
                        per_event_open.get(event, 0) >= s.pin15_max_per_event:
                    summ.capped += 1
                    continue

                # taker buy of the favored side at its ASK, qty capped by resting depth
                try:
                    ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "pin15: orderbook fetch failed",
                        extra={"extra_fields": {"ticker": ticker, "error": str(exc)[:200]}},
                    )
                    continue
                metrics = compute_metrics(mkt, ob, top_n=s.orderbook_depth)
                if not metrics.two_sided:
                    summ.skipped_illiquid += 1
                    continue
                # buy YES fills against NO-bid depth (depth_at_best_ask); buy NO fills against
                # YES-bid depth (depth_at_best_bid) — see scanner/metrics.py.
                if side_up:
                    price = metrics.best_yes_ask
                    depth = metrics.depth_at_best_ask or 0
                else:
                    price = metrics.best_no_ask
                    depth = metrics.depth_at_best_bid or 0
                if price is None or not (0 < price < 100):
                    summ.skipped_illiquid += 1
                    continue
                qty = min(s.pin15_order_size, depth)
                if qty <= 0:
                    summ.skipped_illiquid += 1
                    continue

                fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                repo.create_paper_trade(
                    session,
                    signal_id=None,
                    ticker=ticker,
                    strategy=self.STRATEGY,
                    side=side,
                    action="buy",
                    assumed_price=price,
                    quantity=qty,
                    fill_assumption=(
                        f"[pin15] buy {side} @ {price:.0f}c disp {disp_bps:+.1f}bp "
                        f"T-{htc_s:.0f}s spot {spot:.2f} tgt {target:.2f}"
                    ),
                    entry_fee=fee,
                    model_probability=None,
                    edge=abs(disp_bps),
                )
                repo.open_paper_position_for_trade(
                    session, ticker=ticker, strategy=self.STRATEGY, side=side,
                    quantity=qty, avg_price=price,
                )
                open_count += 1
                per_event_open[event] = per_event_open.get(event, 0) + 1
                open_tickers.add(ticker)
                summ.opened += 1
                summ.per_series[series] = summ.per_series.get(series, 0) + 1

        summ.fetch = fetch
        warn_on_empty_series("pin15", fetch, logger)
        return summ
