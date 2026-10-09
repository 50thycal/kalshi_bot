"""Conservative source-ledger economics; never account P&L or trading permission."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .store import digest

# Strategy-specific and versioned. Numeric floors require a calibration study, not defaults.
SCORING_REQUIREMENTS = {
    "mmsell": {
        "version": "evidence-bar-v2",
        "confidence_score": None,
        "calibration_status": "pending",
        "required": [
            "current_semantic_review",
            "verified_contract_document_binding",
            "verified_source_ids",
            "verified_exchange_fill_coverage",
            "attributable_live_net_economics",
            "actual_fee_coverage",
            "known_strategy_lineage",
            "verified_independent_outcomes",
            "forward_validation",
            "calibrated_sample_duration_and_precision",
        ],
        "minimum_independent_outcomes": None,
        "minimum_live_days": None,
        "maximum_uncertainty": None,
        "completion_is_confidence": False,
        "paper_and_live_pooling": False,
    }
}


def decimal(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite value")
    return result


def fill_time(row):
    raw = row.get("raw_fill_json") or {}
    value = row.get("filled_at") or raw.get("created_time") or raw.get("ts")
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (ValueError, OverflowError, OSError):
        return None


def canonical_direction(raw):
    """Canonical fields agree on exposure; deprecated action/side are not authoritative."""
    outcome, book = raw.get("outcome_side"), raw.get("book_side")
    if outcome not in ("yes", "no") or book != {"yes": "bid", "no": "ask"}[outcome]:
        raise ValueError("Missing or conflicting canonical direction")
    return outcome


class QuantityEvidenceError(ValueError):
    """Raw quantity is not explained by the known source projection."""


def exact_quantity(row):
    """Recover exact exchange count only when the source's rounding is reproducible.

    The executor persists int(round(float(count_fp))). A rounded zero is not an
    absent execution. Caller must first prove raw execution identity and direction.
    """
    raw = row.get("raw_fill_json") or {}
    try:
        quantity = decimal(raw.get("count_fp", raw.get("count", raw.get("quantity"))))
        stored = decimal(row["quantity"])
        if quantity <= 0 or quantity * 100 != (quantity * 100).to_integral_value():
            raise ValueError("Invalid fixed-point contract count")
        if stored != quantity and stored != int(round(float(quantity))):
            raise ValueError("Unexplained source quantity mismatch")
    except (KeyError, InvalidOperation, ValueError, TypeError, OverflowError) as error:
        raise QuantityEvidenceError("Raw quantity missing or inconsistent") from error
    return quantity


class ExecutionTimeEvidenceError(ValueError):
    """Exchange execution time is missing or conflicts with preserved evidence."""


def execution_time(row):
    """Prove execution time separately from the executor's collection timestamp."""
    raw = row.get("raw_fill_json") or {}
    try:
        stamp = datetime.fromisoformat(str(raw["created_time"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Exchange time requires an explicit timezone")
        epoch = decimal(raw["ts"])
        if epoch < 0 or epoch != epoch.to_integral_value() or epoch != int(stamp.timestamp()):
            raise ValueError("Exchange timestamp fields disagree")
        recorded = row.get("filled_at")
        collected = fill_time({"filled_at": recorded}) if recorded is not None else None
        if recorded is not None and (collected is None or collected < stamp):
            raise ValueError("Source collection precedes execution or is invalid")
    except (KeyError, InvalidOperation, ValueError, TypeError, OverflowError, OSError) as error:
        raise ExecutionTimeEvidenceError(
            "Exchange execution time missing or inconsistent"
        ) from error
    return stamp.astimezone(timezone.utc), collected


def ledger_fill(row):
    """Translate a proven exchange execution into the bot's held-contract vocabulary.

    Preserve source evidence. Canonical exposure alone cannot tell an entry from an
    exit: the joined order's intent supplies that distinction, with raw identity proof.
    Legacy exact matches remain supported; a mismatch never falls back to an assumption.
    """
    side, action = row.get("order_side"), row.get("order_action")
    if row.get("order_market_ticker") != row["market_ticker"]:
        raise ValueError("Order market mismatch")
    if side not in ("yes", "no") or action not in ("buy", "sell"):
        raise ValueError("Unknown order intent")
    raw = row.get("raw_fill_json") or {}
    canonical = "outcome_side" in raw or "book_side" in raw
    if not canonical:
        if (row.get("side"), row.get("action")) != (side, action):
            raise ValueError("Unverified legacy mismatch")
        return row
    order = row.get("raw_order_json") or {}
    expected = side if action == "buy" else {"yes": "no", "no": "yes"}[side]
    if canonical_direction(raw) != expected or canonical_direction(order) != expected:
        raise ValueError("Exchange direction disagrees with intent")
    order_id = row.get("kalshi_order_id")
    if not order_id or raw.get("order_id") != order_id or order.get("order_id") != order_id:
        raise ValueError("Exchange order identity unverified")
    if (raw.get("trade_id") or raw.get("fill_id")) != row.get("kalshi_fill_id"):
        raise ValueError("Exchange fill identity unverified")
    for payload in (raw, order):
        tickers = [payload[k] for k in ("ticker", "market_ticker") if payload.get(k)]
        if not tickers or any(t != row["market_ticker"] for t in tickers):
            raise ValueError("Exchange market identity unverified")
    # Validate the source column against its original price scale before changing legs.
    source_side = row.get("side")
    if source_side not in ("yes", "no"):
        raise ValueError("Unknown source price scale")
    if raw.get("side") is not None and raw["side"] != source_side:
        raise ValueError("Source side disagrees with raw fill")
    if raw.get("action") is not None and raw["action"] != row.get("action"):
        raise ValueError("Source action disagrees with raw fill")
    prices = {s: decimal(raw[s + "_price_dollars"]) for s in ("yes", "no")}
    if any(not 0 <= p <= 1 for p in prices.values()) or sum(prices.values()) != 1:
        raise ValueError("Unverified complementary prices")
    if prices[source_side] != decimal(row["price"]) / 100:
        raise ValueError("Source price disagrees with raw fill")
    quantity = exact_quantity(row)
    stamp, collected = execution_time(row)
    return {
        **row,
        "side": side,
        "action": action,
        "price": str(prices[side] * 100),
        "quantity": str(quantity),
        "source_quantity_rounding_restored": quantity != decimal(row["quantity"]),
        "filled_at": stamp.isoformat(),
        "source_recorded_at": row.get("filled_at"),
        "source_execution_time_restored": collected is not None and collected != stamp,
        "exchange_execution_time_verified": True,
    }


def live_economics(store, records, as_of):
    grouped = defaultdict(list)
    for source, row in records:
        if source == "live" and "mmsell" in (row.get("strategy") or "").lower():
            grouped[row["market_ticker"]].append(row)
    for ticker, rows in sorted(grouped.items()):
        source_rows = rows
        market = store.get("market", ticker)
        raw_market = (market or {}).get("raw", {})
        reasons = set()
        owners = {(r.get("strategy"), r.get("experiment_deployment_arm_id")) for r in rows}
        if len(owners) != 1 or any(
            r.get("market_owner_count") != 1 or r.get("order_owner_count") != 1 for r in rows
        ):
            reasons.add("ownership_ambiguous_or_unverified")
        try:
            rows = [ledger_fill(row) for row in rows]
        except QuantityEvidenceError:
            reasons.add("raw_quantity_missing_or_inconsistent")
        except ExecutionTimeEvidenceError:
            reasons.add("exchange_fill_time_missing_or_inconsistent")
        except (KeyError, InvalidOperation, ValueError, TypeError):
            reasons.add("order_fill_identity_inconsistent_or_unverified")
        sides = {r.get("side") for r in rows}
        if len(sides) != 1 or not sides <= {"yes", "no"}:
            reasons.add("mixed_or_unknown_side")
        if any(r.get("market_fill_count") != len(rows) for r in rows):
            reasons.add("source_market_fill_coverage_incomplete")
        ids = [r.get("kalshi_fill_id") for r in rows]
        if not all(ids) or len(set(ids)) != len(ids):
            reasons.add("exchange_fill_identity_missing_or_duplicate")
        if raw_market.get("status") not in ("settled", "finalized") or raw_market.get(
            "is_provisional"
        ):
            reasons.add("final_settlement_missing")
        settlement_time = fill_time({"filled_at": raw_market.get("settlement_ts")})
        if settlement_time is None or settlement_time > as_of:
            reasons.add("settlement_time_missing_or_future")
        result = raw_market.get("result")
        payout = None
        try:
            if result not in ("yes", "no") or raw_market.get("market_type") != "binary":
                raise ValueError("Only binary settlement is supported")
            notional = (
                decimal(raw_market["notional_value_dollars"])
                if raw_market.get("notional_value_dollars") is not None
                else decimal(raw_market["notional_value"]) / 100
            )
            if notional != 1:
                raise ValueError("Nonstandard notional")
            yes_value = decimal(raw_market.get("settlement_value_dollars", int(result == "yes")))
            if yes_value != int(result == "yes"):
                raise ValueError("Conflicting settlement")
            payout = yes_value if sides == {"yes"} else 1 - yes_value
        except (KeyError, InvalidOperation, ValueError, TypeError):
            reasons.add("unsupported_or_conflicting_settlement")
        parsed = []
        for row in rows:
            raw = row.get("raw_fill_json") or {}
            stamp = fill_time(row)
            collected = fill_time({"filled_at": row.get("source_recorded_at")})
            if stamp is None or stamp > as_of:
                reasons.add("fill_time_missing_or_future")
            if collected and collected > as_of:
                reasons.add("fill_time_missing_or_future")
            if stamp and settlement_time and stamp > settlement_time:
                reasons.add("fill_after_settlement")
            try:
                quantity = decimal(row["quantity"])
                price = decimal(row["price"]) / 100
                if quantity <= 0 or not 0 <= price <= 1 or row.get("action") not in ("buy", "sell"):
                    raise ValueError("Invalid fill")
                raw_count = raw.get("count_fp", raw.get("count", raw.get("quantity")))
                if raw_count is None or decimal(raw_count) != quantity:
                    reasons.add("raw_quantity_missing_or_inconsistent")
                field = str(row.get("side")) + "_price_dollars"
                raw_price = raw.get(field)
                if raw_price is None and raw.get(str(row.get("side")) + "_price") is not None:
                    raw_price = decimal(raw[str(row.get("side")) + "_price"]) / 100
                if raw_price is None or decimal(raw_price) != price:
                    reasons.add("raw_price_missing_or_inconsistent")
                # DB fee can be estimated. Only explicit exchange fee_cost is actual.
                fee = decimal(raw["fee_cost"])
                if fee < 0:
                    raise ValueError("Negative fee")
                parsed.append((stamp, row["id"], row["action"], quantity, price, fee))
            except (KeyError, InvalidOperation, ValueError, TypeError):
                reasons.add("actual_fill_costs_missing_or_invalid")
        inventory = Decimal(0)
        cash = Decimal(0)
        fees = Decimal(0)
        bought = Decimal(0)
        if not reasons:
            for _stamp, _source_id, action, quantity, price, fee in sorted(parsed):
                fees += fee
                if action == "buy":
                    bought += quantity
                    inventory += quantity
                    cash -= quantity * price
                else:
                    inventory -= quantity
                    cash += quantity * price
                if inventory < 0:
                    reasons.add("inventory_history_incomplete")
            if reasons:
                cash = None
        net = cash + inventory * payout - fees if not reasons else None
        times = [p[0] for p in parsed if p[0] is not None]
        owner = next(iter(owners)) if len(owners) == 1 else (None, None)
        yield {
            "schema_version": 1,
            "method_version": "exclusive-binary-ledger-v4",
            "direction_method": "verified-order-intent-canonical-exposure-v1",
            "quantity_method": "verified-fixed-point-source-rounding-v1",
            "execution_time_method": "verified-exchange-execution-time-v1",
            "exchange_execution_times_verified": all(
                r.get("exchange_execution_time_verified", False) for r in rows
            ),
            "source_execution_time_restored_fills": sum(
                bool(r.get("source_execution_time_restored")) for r in rows
            ),
            "source_quantity_rounding_restored_fills": sum(
                bool(r.get("source_quantity_rounding_restored")) for r in rows
            ),
            "market_ticker": ticker,
            "series_ticker": ticker.split("-", 1)[0],
            "strategy_version": owner[0],
            "deployment_arm_id": owner[1],
            "side": next(iter(sides)) if len(sides) == 1 else None,
            "status": "attributed_source_ledger" if net is not None else "blocked",
            "blocked_reasons": sorted(reasons),
            "source_fill_count": len(rows),
            "buy_contracts": str(bought) if net is not None else None,
            "net_pnl_dollars": str(net) if net is not None else None,
            "actual_fees_dollars": str(fees) if net is not None else None,
            "remaining_settlement_contracts": str(inventory) if net is not None else None,
            "first_fill_at": min(times).isoformat() if times else None,
            "last_fill_at": max(times).isoformat() if times else None,
            "settlement_result": result,
            "settlement_at": raw_market.get("settlement_ts"),
            "exchange_fill_coverage_verified": False,
            "independent_outcomes_verified": False,
            "confidence_score": None,
            "qualified": False,
            "input_fingerprint": digest({"rows": source_rows, "market": raw_market}),
        }


def ledger_assessments(documents, as_of):
    groups = defaultdict(list)
    for document in documents:
        if document["status"] != "attributed_source_ledger":
            continue
        key = tuple(
            document[k] for k in ("series_ticker", "strategy_version", "deployment_arm_id", "side")
        )
        groups[key].append(document)
    for (series, book, arm, side), rows in sorted(groups.items(), key=lambda x: str(x[0])):
        quantity = sum(decimal(r["buy_contracts"]) for r in rows)
        net = sum(decimal(r["net_pnl_dollars"]) for r in rows)
        first = min(fill_time({"filled_at": r["first_fill_at"]}) for r in rows)
        last = max(fill_time({"filled_at": r["last_fill_at"]}) for r in rows)
        yield {
            "schema_version": 1,
            "strategy_id": "mmsell",
            "evaluator_version": "exclusive-binary-ledger-v4",
            "strategy_version": book,
            "series_ticker": series,
            "deployment_arm_id": arm,
            "side": side,
            "entry_band_cents": None,
            "execution_policy": "live_settled_market_ledger",
            "evidence_source": "live",
            "window": "history",
            "as_of": as_of.isoformat(),
            "evidence_cutoff": max(r["last_fill_at"] for r in rows),
            "executions": sum(r["source_fill_count"] for r in rows),
            "contracts": float(quantity),
            "settled_markets": len(rows),
            "first_execution_at": first.isoformat(),
            "last_execution_at": last.isoformat(),
            "observation_span_days": (last - first).total_seconds() / 86400,
            "active_days": len({r["first_fill_at"][:10] for r in rows}),
            "active_days_definition": "distinct market-entry days; full ledger retained",
            "exchange_execution_times_verified": all(
                r["exchange_execution_times_verified"] for r in rows
            ),
            "independent_outcomes_verified": False,
            "net_pnl_dollars": str(net),
            "actual_fees_dollars": str(sum(decimal(r["actual_fees_dollars"]) for r in rows)),
            "edge_cents_per_contract": float(net * 100 / quantity) if quantity else None,
            "edge_definition": "total source-ledger net cents / purchased contracts; includes exits and settlement",
            "maturity": "live_attributed_source_ledger",
            "confidence_score": None,
            "qualified": False,
            "qualification_reasons": [
                "calibration_pending",
                "exchange_fill_coverage_unverified",
                "outcome_independence_unverified",
                "forward_validation_pending",
            ],
            "input_fingerprint": digest(rows),
        }
