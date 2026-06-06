"""Market scanner orchestration — one scan cycle.

Flow per cycle:
  1. Page through open markets, keep those matching target categories and basic
     liquidity floors (volume / open interest).
  2. Rank pre-candidates by volume and cap at MAX_MARKETS_PER_SCAN.
  3. For each: fetch the order book, compute metrics, persist market +
     market_snapshot + orderbook_snapshot, score a signal, persist it; run
     candidates through the Risk Manager and persist the risk_event.
  4. Return a ranked summary for logging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.client import KalshiClient
from ..risk.manager import RiskDecision, RiskManager
from .metrics import MarketMetrics, compute_metrics
from .signals import KEYWORDS, SignalResult, score_market

logger = logging.getLogger(__name__)

# Safety cap on how many markets we page through per scan.
MAX_MARKETS_PAGED = 2000


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
    markets_scanned: int = 0
    targets_considered: int = 0
    snapshots_written: int = 0
    candidates_found: int = 0
    candidates: list[CandidateRow] = field(default_factory=list)


def matches_target(market: dict, settings: Settings) -> bool:
    """Decide whether a market is in scope before we spend an order-book call.

    Prefer the market's `category`; fall back to configured series prefixes, then
    to title keyword matching (category is often absent on the market object)."""
    cat = (market.get("category") or "").strip().lower()
    if cat and cat in settings.target_category_list:
        return True
    ticker = (market.get("ticker") or "").upper()
    if any(ticker.startswith(prefix) for prefix in settings.target_series_prefix_list):
        return True
    title = (market.get("title") or "").lower()
    return any(k in title for k in KEYWORDS)


def _is_open(market: dict) -> bool:
    status = (market.get("status") or "").lower()
    return status in ("active", "open", "")


class MarketScanner:
    def __init__(self, client: KalshiClient, settings: Settings, risk: RiskManager):
        self.client = client
        self.settings = settings
        self.risk = risk

    def run_once(self, session, *, account_state: dict | None = None) -> ScanSummary:
        s = self.settings
        summary = ScanSummary()
        now = datetime.now(timezone.utc)

        # 1) Gather in-scope markets.
        targets: list[dict] = []
        for market in self.client.iter_markets(
            status="open", page_size=200, max_markets=MAX_MARKETS_PAGED
        ):
            summary.markets_scanned += 1
            if not _is_open(market):
                continue
            if not matches_target(market, s):
                continue
            if int(market.get("volume") or 0) < s.min_volume:
                continue
            if int(market.get("open_interest") or 0) < s.min_open_interest:
                continue
            targets.append(market)
        summary.targets_considered = len(targets)

        # 2) Rank pre-candidates by volume, cap.
        targets.sort(key=lambda mkt: int(mkt.get("volume") or 0), reverse=True)
        targets = targets[: s.max_markets_per_scan]

        # 3) Per-market deep scan.
        ranked: list[tuple[SignalResult, MarketMetrics, dict, RiskDecision | None]] = []
        for market in targets:
            ticker = market.get("ticker")
            if not ticker:
                continue
            try:
                orderbook = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
            except Exception as exc:  # noqa: BLE001 - one bad book shouldn't kill the scan
                logger.warning(
                    "orderbook fetch failed",
                    extra={"extra_fields": {"ticker": ticker, "error": str(exc)}},
                )
                continue

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
                decision = self.risk.evaluate(
                    signal=signal,
                    metrics=metrics,
                    account_state=account_state,
                    existing_exposure=0.0,
                )
                repo.insert_risk_event(session, sig_row.id, ticker, decision)
                summary.candidates_found += 1
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
