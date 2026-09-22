"""One-shot operator smoke for the isolated ChatGPT exchange path.

This is not a research decision or a substitute for the common desk start. It
proves restricted-key write access with one deliberately non-marketable IOC and
records an immutable claim/result in the desk database.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from .contracts import DeskError, utcnow

D = Decimal
MIN_ASK = D("0.10")
SMOKE_LIMIT = D("0.01")
MIN_BALANCE = D("30")


def smoke_ids(subaccount: int) -> tuple[str, str, str]:
    stem = f"https://kalshi-bot/desks/chatgpt-live-smoke/v1/{subaccount}"
    return (
        str(uuid5(NAMESPACE_URL, stem + "/order")),
        str(uuid5(NAMESPACE_URL, stem + "/claim")),
        str(uuid5(NAMESPACE_URL, stem + "/result")),
    )


def _publications(snapshot: dict) -> dict[str, dict]:
    return {row["record_id"]: row for row in snapshot.get("publications", [])}


def run_chatgpt_smoke(
    store,
    exchange,
    ticker: str,
    side: str,
    *,
    execute: bool = False,
    now: datetime | None = None,
) -> dict:
    """Preview or submit the single permitted ChatGPT write-path probe."""
    now = now or utcnow()
    if not ticker or side not in ("yes", "no"):
        raise DeskError("invalid_smoke_market")
    client_order_id, claim_id, result_id = smoke_ids(exchange.subaccount)
    snapshot = store.snapshot(now)
    if snapshot.get("started_at") is not None:
        raise DeskError("smoke_after_common_start_forbidden")
    chatgpt = next((row for row in snapshot["desks"] if row["desk_id"] == "chatgpt"), None)
    if not chatgpt or not chatgpt["ready"] or chatgpt["paused"]:
        raise DeskError("chatgpt_not_smoke_ready")

    publications = _publications(snapshot)
    if result_id in publications:
        return publications[result_id]["payload"]

    existing = exchange.find_order(client_order_id)
    if existing is not None:
        actual_ticker, report = existing
        payload = {
            "protocol": "chatgpt-live-smoke-v1",
            "mode": "recovered",
            "ticker": actual_ticker,
            "client_order_id": client_order_id,
            "report": report.model_dump(mode="json"),
        }
        if report.status == "terminal":
            store.publish("chatgpt", "live_smoke_result", payload, now, record_id=result_id)
        return payload

    # A prior claim with no visible order is ambiguous: the POST may have reached
    # Kalshi even if the order index has not exposed it yet. Never submit again.
    if claim_id in publications:
        raise DeskError("smoke_submission_ambiguous")

    isolation = exchange.check_isolation()
    if D(isolation["balance"]) < MIN_BALANCE:
        raise DeskError("smoke_balance_below_initial_bankroll")
    exchange.check_clean_book()
    quote = exchange.quote(ticker, side)
    if quote.status not in ("open", "active"):
        raise DeskError("smoke_market_not_open")
    if quote.ask < MIN_ASK:
        raise DeskError("smoke_price_gap_too_small")
    if quote.available_quantity < 1:
        raise DeskError("smoke_market_has_no_offer")
    if quote.closes_at <= now + timedelta(minutes=10):
        raise DeskError("smoke_market_closes_too_soon")

    preview = {
        "protocol": "chatgpt-live-smoke-v1",
        "mode": "preview" if not execute else "claimed",
        "ticker": ticker,
        "side": side,
        "client_order_id": client_order_id,
        "limit_price": str(SMOKE_LIMIT),
        "quantity": 1,
        "observed_ask": str(quote.ask),
        "quote_at": quote.fetched_at.isoformat(),
        "rules_sha256": quote.rules_sha256,
        "subaccount": exchange.subaccount,
    }
    if not execute:
        return preview

    store.publish("chatgpt", "live_smoke_claim", preview, now, record_id=claim_id)
    # Exactly one POST. Any exception is an ambiguous outcome and is never retried.
    report = exchange.submit_ioc(client_order_id, ticker, side, 1, SMOKE_LIMIT)
    result = {
        **preview,
        "mode": "submitted",
        "report": report.model_dump(mode="json"),
    }
    if report.status == "terminal":
        store.publish(
            "chatgpt",
            "live_smoke_result",
            result,
            max(utcnow(), now + timedelta(microseconds=1)),
            record_id=result_id,
        )
    return result
