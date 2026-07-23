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
    in_band: int = 0        # control-book scoped (stable across releases)
    opened: int = 0         # across control + revision books
    already_open: int = 0
    capped: int = 0
    skipped_illiquid: int = 0
    skipped_htc: int = 0
    per_series: dict[str, int] = field(default_factory=dict)
    per_book: dict[str, int] = field(default_factory=dict)


class MmSellTracker:
    STRATEGY = "mmsell"

    def __init__(self, client, settings: Settings, live_executor=None):
        self.client = client
        self.settings = settings
        # Set only in live mode (BOT_MODE=live); mirrors allowlisted paper entries into real
        # resting maker NO-buys. None in paper/weather mode -> the book stays paper-only.
        self.live_executor = live_executor
        # Set by the live cycle before run_once so the live mirror can pass it to the balance gate.
        self._account_state: dict | None = None

    def _skip_series(self, series: str) -> bool:
        s = (series or "").upper()
        return any(s.startswith(p) for p in self.settings.mmsell_skip_series_list)

    def _books(self) -> list[dict]:
        """Control book (base knobs, tag 'mmsell' — NEVER reparameterized) then the
        configured revision books. All share the scan/orderbook; only the band + htc differ."""
        s = self.settings
        control = {
            "tag": self.STRATEGY,
            "lo": float(s.mmsell_entry_lo_cents),
            "hi": float(s.mmsell_entry_hi_cents),
            "htcmin": s.mmsell_min_hours_to_close,
            "htcmax": s.mmsell_max_hours_to_close,
            "skip": [],  # the control never filters by series (global mmsell_skip_series applies)
            "only": [],
            "maxyes": None,  # the control has no entry-price ceiling
        }
        return [control, *s.mmsell_variant_list]

    @staticmethod
    def _book_admits_series(book: dict, series: str) -> bool:
        """Per-variant series filter: a book with a `skip` list drops any series containing one of
        its substrings; a book with an `only` list trades ONLY series containing one of its
        substrings. Matched case-insensitively against the (already-uppercased) series prefix.
        Empty lists (the control + band-only variants) admit everything."""
        skip = book.get("skip") or []
        if any(tok in series for tok in skip):
            return False
        only = book.get("only") or []
        if only and not any(tok in series for tok in only):
            return False
        return True

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

        books = self._books()
        open_count = {b["tag"]: repo.count_open_paper_positions(session, b["tag"]) for b in books}
        captured = 0  # per-cycle candidate-tick writes (bounded by mmsell_candidate_capture_max)

        # 2) per market: for each book whose band+htc admit it, open a maker BUY-NO at the
        #    no-bid (== sell yes at the ask), held to settlement. Books share one orderbook
        #    fetch; only the band (and htc) differ.
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
                # control htc gate scopes the shared work + the skipped_htc counter; a variant
                # with a wider htc than the control is not supported (control is the widest).
                if htc is None or not (s.mmsell_min_hours_to_close <= htc
                                       <= s.mmsell_max_hours_to_close):
                    summ.skipped_htc += 1
                    continue

                metrics = None  # lazy: fetched once for the first book that clears the band
                series = (event.get("series_ticker") or ticker.split("-")[0]).upper()
                for book in books:
                    tag = book["tag"]
                    if not (book["htcmin"] <= htc <= book["htcmax"]):
                        continue
                    if not self._book_admits_series(book, series):
                        continue  # per-variant series skip/allow filter
                    if metrics is None:
                        try:
                            ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
                        except AuthError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "mmsell: orderbook fetch failed",
                                extra={"extra_fields": {"ticker": ticker, "error": str(exc)}})
                            break  # no book can enter this market this cycle
                        metrics = compute_metrics(market, ob, top_n=s.orderbook_depth)
                    if not metrics.two_sided or metrics.midpoint is None \
                            or metrics.best_no_bid is None:
                        summ.skipped_illiquid += 1
                        break
                    if not (book["lo"] <= metrics.midpoint <= book["hi"]):
                        continue
                    # Entry-price ceiling: the band gates the MIDPOINT, but P&L is driven by the
                    # actual sell price (yes-ask = 100 - no-bid), which is always >= the midpoint.
                    # maxyes caps that directly — the live decomposition found the edge lives only
                    # in the cheapest longshots (yes <=7c +2.3c; 8-11c net negative).
                    maxyes = book.get("maxyes")
                    if maxyes is not None and (100 - metrics.best_no_bid) > maxyes:
                        continue
                    if tag == self.STRATEGY:
                        summ.in_band += 1
                        # Candidate capture: tape one orderbook snapshot per in-band candidate
                        # per cycle (off the book we already fetched — no extra API), whether or
                        # not a position opens, for the offline fill replay. Scoped to the control
                        # book so each market is taped once; fail-soft and per-cycle capped.
                        if s.mmsell_capture_candidates \
                                and captured < s.mmsell_candidate_capture_max:
                            try:
                                repo.insert_mmsell_candidate_tick(
                                    session, ticker, metrics, series=series, hours_to_close=htc)
                                captured += 1
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "mmsell: candidate tick capture failed",
                                    extra={"extra_fields": {"ticker": ticker, "error": str(exc)}})

                    if repo.get_open_paper_position(session, ticker, tag) is not None:
                        summ.already_open += 1
                        continue
                    if open_count[tag] >= s.mmsell_max_open_positions:
                        summ.capped += 1
                        continue

                    # maker sells yes at the yes-ask == buys NO at the no-bid (100 - yes_ask)
                    price = metrics.best_no_bid
                    if not (0 < price < 100):
                        summ.skipped_illiquid += 1
                        break
                    qty = s.paper_order_size
                    fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                    sub = market.get("yes_sub_title") or market.get("subtitle") or ""
                    # fill_assumption is String(64); the repo layer also clamps, but keep the
                    # subtitle short and the prices first so truncation only costs subtitle chars.
                    assumption = (
                        f"[{tag}] sell yes '{sub[:24]}' @ {100 - price}c "
                        f"(no@{price}c mid{metrics.midpoint:.0f}c)"
                    )[:64]
                    repo.create_paper_trade(
                        session,
                        signal_id=None,
                        ticker=ticker,
                        strategy=tag,
                        side="no",
                        action="buy",
                        assumed_price=price,
                        quantity=qty,
                        fill_assumption=assumption,
                        entry_fee=fee,
                        model_probability=None,
                        edge=0.0,
                    )
                    repo.open_paper_position_for_trade(
                        session, ticker=ticker, strategy=tag, side="no",
                        quantity=qty, avg_price=price,
                    )
                    # Mirror the entry into a real resting maker NO-buy for allowlisted books
                    # (inert unless LIVE_STRATEGIES lists this tag). Self-guarded; a live failure
                    # never rolls back the paper record above (the paper book is the shadow).
                    if self.live_executor is not None:
                        try:
                            self.live_executor.mirror_mmsell_entry(
                                session, strategy=tag, event_ticker=event.get("event_ticker"),
                                ticker=ticker, metrics=metrics, no_price=price,
                                account_state=self._account_state,
                            )
                        except AuthError:
                            raise
                        except Exception:  # noqa: BLE001 — paper record stays intact
                            logger.exception("mmsell live mirror_entry failed (paper unaffected)")
                    open_count[tag] += 1
                    summ.opened += 1
                    summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                    summ.per_series[series] = summ.per_series.get(series, 0) + 1

        return summ
