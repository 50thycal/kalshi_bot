"""Market scanner orchestration — one scan cycle.

Targeting strategy: Kalshi market objects carry no `category`; category lives on
the **event**. So we page through open *events* (which include `category` and,
with nested markets, their `markets`), keep events whose category is in our target
set (or whose title matches an economics keyword), then deep-scan their markets.

Flow per cycle:
  1. Page open events with nested markets; record category distribution (diagnostic).
  2. For matched events, collect markets clearing the volume / open-interest floors.
  3. Rank pre-candidates by volume and cap at MAX_MARKETS_PER_SCAN.
  4. For each: fetch order book, compute metrics, persist market +
     market_snapshot + orderbook_snapshot, score a signal, persist it; run
     candidates through the Risk Manager and persist the risk_event.
  5. Return a ranked summary for logging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.client import KalshiClient
from ..risk.manager import RiskDecision, RiskManager
from .metrics import MarketMetrics, compute_metrics, market_open_interest, market_volume
from .signals import KEYWORDS, SignalResult, score_market

logger = logging.getLogger(__name__)

# Safety cap on how many events we page through per scan.
MAX_EVENTS_PAGED = 5000


@dataclass
class CandidateRow:
    ticker: str
    title: str
    label: str
    score: float
    spread: int | None
    midpoint: float | None
    top_depth: int
    volume: int
    risk_approved: bool | None = None
    risk_reasons: list[str] = field(default_factory=list)


@dataclass
class ScanSummary:
    events_scanned: int = 0
    markets_scanned: int = 0
    targets_considered: int = 0
    snapshots_written: int = 0
    candidates_found: int = 0
    filtered_low_volume: int = 0
    filtered_low_oi: int = 0
    filtered_not_open: int = 0
    filtered_no_orderbook: int = 0
    category_counts: dict[str, int] = field(default_factory=dict)
    candidates: list[CandidateRow] = field(default_factory=list)


def event_matches(event: dict, settings: Settings) -> bool:
    """In-scope if the event's category is targeted, or its title matches a keyword."""
    cat = (event.get("category") or "").strip().lower()
    if cat and cat in settings.target_category_list:
        return True
    text = f"{event.get('title') or ''} {event.get('sub_title') or ''}".lower()
    return any(k in text for k in KEYWORDS)


def _is_open(market: dict) -> bool:
    status = (market.get("status") or "").lower()
    return status in ("active", "open", "")


class MarketScanner:
    def __init__(self, client: KalshiClient, settings: Settings, risk: RiskManager):
        self.client = client
        self.settings = settings
        self.risk = risk

    def run_once(
        self, session, *, account_state: dict | None = None, paper_engine=None
    ) -> ScanSummary:
        s = self.settings
        summary = ScanSummary()
        now = datetime.now(timezone.utc)
        sample_logged = False

        # 1) Gather in-scope markets from open events.
        targets: list[dict] = []
        for event in self.client.iter_events(
            status="open", with_nested_markets=True, page_size=200, max_events=MAX_EVENTS_PAGED
        ):
            summary.events_scanned += 1
            cat = (event.get("category") or "uncategorized").strip().lower()
            summary.category_counts[cat] = summary.category_counts.get(cat, 0) + 1
            if not event_matches(event, s):
                continue

            for market in event.get("markets") or []:
                # Nested markets inherit close_time/category from the event when absent
                # (the market object itself carries no category).
                fallback = {}
                if not market.get("close_time") and event.get("close_time"):
                    fallback["close_time"] = event["close_time"]
                if not market.get("category") and event.get("category"):
                    fallback["category"] = event["category"]
                if fallback:
                    market = {**market, **fallback}
                summary.markets_scanned += 1
                vol = market_volume(market)
                oi = market_open_interest(market)

                if not sample_logged:
                    logger.info(
                        "sample market fields",
                        extra={"extra_fields": {
                            "ticker": market.get("ticker"),
                            "status": market.get("status"),
                            "parsed_volume": vol,
                            "parsed_open_interest": oi,
                            "volume_fp": market.get("volume_fp"),
                            "open_interest_fp": market.get("open_interest_fp"),
                            "yes_bid_dollars": market.get("yes_bid_dollars"),
                        }},
                    )
                    sample_logged = True

                if not _is_open(market):
                    summary.filtered_not_open += 1
                    continue
                if vol < s.min_volume:
                    summary.filtered_low_volume += 1
                    continue
                if oi < s.min_open_interest:
                    summary.filtered_low_oi += 1
                    continue
                targets.append(market)
        summary.targets_considered = len(targets)

        # 2) Rank pre-candidates by volume, cap.
        targets.sort(key=market_volume, reverse=True)
        targets = targets[: s.max_markets_per_scan]

        # 3) Per-market deep scan.
        ranked: list[tuple[SignalResult, MarketMetrics, dict, RiskDecision | None]] = []
        ob_sample_logged = False
        for market in targets:
            ticker = market.get("ticker")
            if not ticker:
                continue
            try:
                orderbook = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
            except Exception as exc:  # noqa: BLE001 - one bad book shouldn't kill the scan
                summary.filtered_no_orderbook += 1
                logger.warning(
                    "orderbook fetch failed",
                    extra={"extra_fields": {"ticker": ticker, "error": str(exc)}},
                )
                continue

            if not ob_sample_logged:
                inner = orderbook.get("orderbook") or orderbook.get("orderbook_fp") or orderbook
                logger.info(
                    "sample orderbook",
                    extra={"extra_fields": {
                        "ticker": ticker,
                        "top_keys": sorted(orderbook.keys()) if isinstance(orderbook, dict) else None,
                        "inner_keys": sorted(inner.keys()) if isinstance(inner, dict) else None,
                    }},
                )
                ob_sample_logged = True

            metrics = compute_metrics(market, orderbook, top_n=s.orderbook_depth, now=now)
            repo.upsert_market(session, market)
            snap = repo.insert_market_snapshot(session, market, metrics, captured_at=now)
            repo.insert_orderbook_snapshot(session, ticker, metrics, orderbook, captured_at=now)
            summary.snapshots_written += 1

            signal = score_market(metrics, market, settings=s)
            sig_row = repo.insert_signal(
                session, signal, metrics, snapshot_id=snap.id, bot_mode=s.bot_mode
            )

            decision: RiskDecision | None = None
            if signal.label in ("candidate", "paper_trade"):
                summary.candidates_found += 1
                if paper_engine is not None:
                    # Paper mode: the engine evaluates risk (for_paper) and records the
                    # risk_event, then opens a simulated trade if approved.
                    decision = paper_engine.consider(
                        session,
                        signal=signal,
                        signal_id=sig_row.id,
                        metrics=metrics,
                        market=market,
                        account_state=account_state,
                    )
                else:
                    decision = self.risk.evaluate(
                        signal=signal,
                        metrics=metrics,
                        account_state=account_state,
                        existing_exposure=0.0,
                    )
                    repo.insert_risk_event(session, sig_row.id, ticker, decision)
            ranked.append((signal, metrics, market, decision))

        # 4) Build the ranked candidate list (highest score first).
        ranked.sort(key=lambda t: t[0].score, reverse=True)
        for signal, metrics, market, decision in ranked:
            summary.candidates.append(
                CandidateRow(
                    ticker=signal.ticker,
                    title=market.get("title", "") or "",
                    label=signal.label,
                    score=signal.score,
                    spread=metrics.spread,
                    midpoint=metrics.midpoint,
                    top_depth=metrics.top_depth,
                    volume=metrics.volume,
                    risk_approved=(decision.approved if decision else None),
                    risk_reasons=(decision.reason_codes if decision else []),
                )
            )
        return summary
