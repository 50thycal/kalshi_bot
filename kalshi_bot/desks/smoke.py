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
from .exchange import ExchangeWriteHTTPError

D = Decimal
MIN_ASK = D("0.10")
SMOKE_LIMIT = D("0.01")
MIN_BALANCE = D("30")
RECOVERY_DELAY = timedelta(minutes=5)


def smoke_ids(subaccount: int, version: int = 1) -> tuple[str, str, str]:
    if version not in (1, 2):
        raise DeskError("unsupported_smoke_version")
    stem = f"https://kalshi-bot/desks/chatgpt-live-smoke/v{version}/{subaccount}"
    return (
        str(uuid5(NAMESPACE_URL, stem + "/order")),
        str(uuid5(NAMESPACE_URL, stem + "/claim")),
        str(uuid5(NAMESPACE_URL, stem + "/result")),
    )


def smoke_error_id(subaccount: int, version: int) -> str:
    stem = f"https://kalshi-bot/desks/chatgpt-live-smoke/v{version}/{subaccount}/error"
    return str(uuid5(NAMESPACE_URL, stem))


def _publications(snapshot: dict) -> dict[str, dict]:
    return {row["record_id"]: row for row in snapshot.get("publications", [])}


def run_chatgpt_smoke(
    store,
    exchange,
    ticker: str,
    side: str,
    *,
    execute: bool = False,
    recovery_v2: bool = False,
    now: datetime | None = None,
) -> dict:
    """Preview or submit the single permitted ChatGPT write-path probe."""
    now = now or utcnow()
    if not ticker or side not in ("yes", "no"):
        raise DeskError("invalid_smoke_market")
    snapshot = store.snapshot(now)
    if snapshot.get("started_at") is not None:
        raise DeskError("smoke_after_common_start_forbidden")
    chatgpt = next((row for row in snapshot["desks"] if row["desk_id"] == "chatgpt"), None)
    if not chatgpt or not chatgpt["ready"] or chatgpt["paused"]:
        raise DeskError("chatgpt_not_smoke_ready")

    publications = _publications(snapshot)
    superseded = None
    version = 2 if recovery_v2 else 1
    if recovery_v2:
        old_client_id, old_claim_id, old_result_id = smoke_ids(exchange.subaccount, 1)
        if old_result_id in publications:
            return publications[old_result_id]["payload"]
        old_claim = publications.get(old_claim_id)
        if old_claim is None:
            raise DeskError("smoke_v2_requires_v1_claim")
        old_order = exchange.find_order(old_client_id)
        if old_order is not None:
            actual_ticker, report = old_order
            payload = {
                "protocol": "chatgpt-live-smoke-v1",
                "mode": "recovered",
                "ticker": actual_ticker,
                "client_order_id": old_client_id,
                "report": report.model_dump(mode="json"),
            }
            if report.status == "terminal":
                store.publish(
                    "chatgpt", "live_smoke_result", payload, now, record_id=old_result_id
                )
            return payload
        claimed_at = datetime.fromisoformat(old_claim["created_at"])
        if now < claimed_at + RECOVERY_DELAY:
            raise DeskError("smoke_v1_recovery_delay_active")
        superseded = {
            "protocol": "chatgpt-live-smoke-v1",
            "client_order_id": old_client_id,
            "claim_id": old_claim_id,
            "result_id": old_result_id,
            "order_absent_at": now.isoformat(),
        }

    client_order_id, claim_id, result_id = smoke_ids(exchange.subaccount, version)
    if result_id in publications:
        return publications[result_id]["payload"]

    existing = exchange.find_order(client_order_id)
    if existing is not None:
        actual_ticker, report = existing
        payload = {
            "protocol": f"chatgpt-live-smoke-v{version}",
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
    balance = D(isolation["balance"])
    if balance < MIN_BALANCE:
        raise DeskError("smoke_balance_below_initial_bankroll")
    exchange.check_clean_book()
    if recovery_v2 and balance != MIN_BALANCE:
        raise DeskError("smoke_v2_requires_unchanged_balance")
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
        "protocol": f"chatgpt-live-smoke-v{version}",
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
    if superseded is not None:
        preview["supersedes"] = superseded
    if not execute:
        return preview

    store.publish("chatgpt", "live_smoke_claim", preview, now, record_id=claim_id)
    # Exactly one POST. Any exception is an ambiguous outcome and is never retried.
    try:
        report = exchange.submit_ioc(client_order_id, ticker, side, 1, SMOKE_LIMIT)
    except ExchangeWriteHTTPError as exc:
        failure = {
            **preview,
            "mode": "exchange_http_error",
            **exc.operator_payload(),
        }
        store.publish(
            "chatgpt",
            "live_smoke_exchange_error",
            failure,
            max(utcnow(), now + timedelta(microseconds=1)),
            record_id=smoke_error_id(exchange.subaccount, version),
        )
        raise
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
