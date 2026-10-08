"""Conservative source-ledger economics; never account P&L or trading permission."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .store import digest

# Strategy-specific and versioned. Numeric floors require a calibration study, not defaults.
SCORING_REQUIREMENTS = {
    "mmsell": {
        "version": "evidence-bar-v1",
        "confidence_score": None,
        "calibration_status": "pending",
        "required": [
            "current_semantic_review",
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


def live_economics(store, records, as_of):
    grouped = defaultdict(list)
    for source, row in records:
        if source == "live" and "mmsell" in (row.get("strategy") or "").lower():
            grouped[row["market_ticker"]].append(row)
    for ticker, rows in sorted(grouped.items()):
        market = store.get("market", ticker)
        raw_market = (market or {}).get("raw", {})
        reasons = set()
        owners = {(r.get("strategy"), r.get("experiment_deployment_arm_id")) for r in rows}
        sides = {r.get("side") for r in rows}
        if len(owners) != 1 or any(
            r.get("market_owner_count") != 1 or r.get("order_owner_count") != 1 for r in rows
        ):
            reasons.add("ownership_ambiguous_or_unverified")
        if len(sides) != 1 or not sides <= {"yes", "no"}:
            reasons.add("mixed_or_unknown_side")
        if any(r.get("market_fill_count") != len(rows) for r in rows):
            reasons.add("source_market_fill_coverage_incomplete")
        ids = [r.get("kalshi_fill_id") for r in rows]
        if not all(ids) or len(set(ids)) != len(ids):
            reasons.add("exchange_fill_identity_missing_or_duplicate")
        if raw_market.get("status") != "settled" or raw_market.get("is_provisional"):
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
            if stamp is None or stamp > as_of:
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
            "method_version": "exclusive-binary-ledger-v1",
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
            "input_fingerprint": digest({"rows": rows, "market": raw_market}),
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
            "evaluator_version": "exclusive-binary-ledger-v1",
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
