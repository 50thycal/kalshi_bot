"""Market-making SELL book (BOT_MODE=mmsell) — forward-test the backtested maker edge.

The kalshi_mm backtest showed the resting (maker) side of Kalshi trades is +EV: SELLING yes on
overpriced cheap/underdog contracts (~5-40c) and holding to settlement nets a real edge that
survives worst-case fees, split-half OOS and off-sports. Selling yes = BUYING NO at the no-bid
(the maker price), so this book reuses the paper engine's buy/settle machinery: each cycle it
scans liquid open markets, and for every market whose yes MIDPOINT sits in the entry band it
opens a paper BUY-NO position at the no-bid, tagged strategy 'mmsell', held to settlement.

Honest limitation: paper ASSUMES the resting ask fills (enters at the maker no-bid price). It
therefore forward-tests whether the +EV persists out-of-sample on NEW markets — NOT queue/fill
realism, which only a small live test can prove. Diversification (many small positions) is the
risk control the exit study pointed to; hence the high max-open-positions default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close, market_volume

logger = logging.getLogger(__name__)


@dataclass
class MmSellCycleSummary:
    events_seen: int = 0
    markets_considered: int = 0
    in_band: int = 0
    opened: int = 0
    already_open: int = 0
    capped: int = 0
    skipped_illiquid: int = 0
    skipped_htc: int = 0
    per_series: dict[str, int] = field(default_factory=dict)


class MmSellTracker:
    STRATEGY = "mmsell"

    def __init__(self, client, settings: Settings):
        self.client = client
        self.settings = settings

    def _skip_series(self, series: str) -> bool:
        s = (series or "").upper()
        return any(s.startswith(p) for p in self.settings.mmsell_skip_series_list)

    def run_once(self, session) -> MmSellCycleSummary:
        s = self.settings
        summ = MmSellCycleSummary()

        # 1) collect liquid open events (skip parlays/weather), rank by volume, cap the scan.
        events: list[tuple[float, dict]] = []
        cursor = ""
        for _ in range(40):
            page = self.client.get_events(status="open", with_nested_markets=True,
                                          limit=200, cursor=cursor or None)
            evs = (page or {}).get("events") or []
            for e in evs:
                if self._skip_series(e.get("series_ticker") or ""):
                    continue
                vol = sum(market_volume(m) for m in e.get("markets") or [])
                if vol <= 0:
                    continue
                events.append((vol, e))
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not evs:
                break
        events.sort(key=lambda ev: -ev[0])
        events = events[: s.mmsell_top_events]
        summ.events_seen = len(events)

        open_count = repo.count_open_paper_positions(session, self.STRATEGY)

        # 2) per market: if yes-midpoint in the entry band + two-sided + liquid + htc window,
        #    open a maker BUY-NO at the no-bid, held to settlement.
        for _vol, event in events:
            for market in event.get("markets") or []:
                summ.markets_considered += 1
                ticker = market.get("ticker")
                if not ticker:
                    continue
                if market_volume(market) < s.mmsell_min_volume:
                    continue
                htc_s = compute_time_to_close(market.get("close_time"))
                htc = htc_s / 3600.0 if htc_s is not None else None   # seconds -> hours
                if htc is None or not (s.mmsell_min_hours_to_close <= htc
                                       <= s.mmsell_max_hours_to_close):
                    summ.skipped_htc += 1
                    continue
                try:
                    ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("mmsell: orderbook fetch failed",
                                   extra={"extra_fields": {"ticker": ticker, "error": str(exc)}})
                    continue
                metrics = compute_metrics(market, ob, top_n=s.orderbook_depth)
                if not metrics.two_sided or metrics.midpoint is None \
                        or metrics.best_no_bid is None:
                    summ.skipped_illiquid += 1
                    continue
                if not (s.mmsell_entry_lo_cents <= metrics.midpoint <= s.mmsell_entry_hi_cents):
                    continue
                summ.in_band += 1

                if repo.get_open_paper_position(session, ticker, self.STRATEGY) is not None:
                    summ.already_open += 1
                    continue
                if open_count >= s.mmsell_max_open_positions:
                    summ.capped += 1
                    continue

                # maker sells yes at the yes-ask == buys NO at the no-bid (100 - yes_ask)
                price = metrics.best_no_bid
                if not (0 < price < 100):
                    summ.skipped_illiquid += 1
                    continue
                qty = s.paper_order_size
                fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                sub = market.get("yes_sub_title") or market.get("subtitle") or ""
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
                        f"[mmsell] sell yes '{sub[:32]}' @ {100 - price}c "
                        f"(buy no @ {price}c, mid {metrics.midpoint:.0f}c)"
                    ),
                    entry_fee=fee,
                    model_probability=None,
                    edge=0.0,
                )
                repo.open_paper_position_for_trade(
                    session, ticker=ticker, strategy=self.STRATEGY, side="no",
                    quantity=qty, avg_price=price,
                )
                open_count += 1
                summ.opened += 1
                series = (event.get("series_ticker") or ticker.split("-")[0]).upper()
                summ.per_series[series] = summ.per_series.get(series, 0) + 1

        return summ
