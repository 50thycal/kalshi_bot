"""Read-only review packets and strategy-specific calibration preparation.

Inputs are source-recorded live fills, including blocked outcomes. They are not
the assigned opportunity population, independent samples, or a frozen study.
"""

from copy import deepcopy
from datetime import datetime

from .economics import SCORING_REQUIREMENTS
from .store import RULE_FIELDS, SEMANTIC_FIELDS, digest, now

METHOD = "review-calibration-inputs-v1"
PROVIDERS = {}
CHECK_PROVIDERS = {}


def register_readiness(strategy_id, provider, check_provider=None):
    """Opt in explicitly; a new evaluator never inherits the MMSELL evidence bar.

    Provider(store, series) yields current live ledger records with immutable
    record_id/input_fingerprint and the same context/status/time keys as MMSELL.
    Register its own SCORING_REQUIREMENTS through evaluators.register first.
    """
    if strategy_id in PROVIDERS or strategy_id not in SCORING_REQUIREMENTS:
        raise ValueError("Readiness needs a unique strategy with its own evidence requirements")
    if not callable(provider) or (check_provider is not None and not callable(check_provider)):
        raise ValueError("Readiness provider must be callable")
    PROVIDERS[strategy_id] = provider
    CHECK_PROVIDERS[strategy_id] = check_provider


def mmsell_checks(store, record, market_review, series_review):
    return {
        "current_semantic_review": bool(market_review["review_id"] and series_review["review_id"]),
        "verified_source_ids": store.state("source_coverage:live", {}).get("ids_match") is True,
        "verified_exchange_fill_coverage": False,
        "attributable_live_net_economics": record.get("status") == "attributed_source_ledger",
        "actual_fee_coverage": record.get("actual_fees_dollars") is not None,
        "known_strategy_lineage": False,
        "verified_independent_outcomes": False,
        "forward_validation": False,
        "calibrated_sample_duration_and_precision": False,
    }


register_readiness(
    "mmsell",
    lambda store, series: store.iter_pipeline_items("live-economics", series),
    mmsell_checks,
)


def envelope(strategy, limit, offset):
    if strategy not in PROVIDERS:
        raise ValueError("Strategy has no readiness provider")
    if not 1 <= limit <= 50 or offset < 0:
        raise ValueError("Readiness page must have 1–50 items and nonnegative offset")
    return {
        "schema_version": 1,
        "method_version": METHOD,
        "captured_at": now(),
        "strategy_id": strategy,
        "requirements": deepcopy(SCORING_REQUIREMENTS[strategy]),
        "evidence_population": "source_recorded_live_fills_including_blocked_outcomes",
        "opportunity_coverage_verified": False,
        "independent_outcomes_verified": False,
        "forward_boundary": None,
        "confidence_score": None,
        "qualified": False,
        "advisory_only": True,
        "snapshot_frozen": False,
        "limit": limit,
        "offset": offset,
    }


def review_facts(document):
    semantics = (document or {}).get("semantics") or dict.fromkeys(SEMANTIC_FIELDS)
    missing = [field for field in SEMANTIC_FIELDS if semantics.get(field) is None]
    review = (document or {}).get("review") or {}
    return {
        "kind": (document or {}).get("kind"),
        "ticker": (document or {}).get("ticker"),
        "rules_hash": (document or {}).get("rules_hash"),
        "parent_rules_hashes": (document or {}).get("parent_rules_hashes", {}),
        "parent_rules_changed": (document or {}).get("parent_rules_changed", False),
        "listing_available": document is not None,
        "review_status": (document or {}).get("review_status", "listing_missing"),
        "review_id": review.get("id"),
        "semantics": semantics,
        "missing_fields": missing,
        "known_field_count": len(SEMANTIC_FIELDS) - len(missing),
        "required_field_count": len(SEMANTIC_FIELDS),
        "known_fields_percent": round(
            100 * (len(SEMANTIC_FIELDS) - len(missing)) / len(SEMANTIC_FIELDS)
        ),
        "rules": {key: (document or {}).get("raw", {}).get(key) for key in RULE_FIELDS},
        "timing": (document or {}).get("timing"),
    }


def context_key(record):
    key = tuple(record.get(field) for field in ("strategy_version", "deployment_arm_id", "side"))
    return (*key, record["market_ticker"] if any(value is None for value in key) else None)


def empty_context(key):
    # Unknown lineage remains explicit; it never becomes a verified experiment epoch.
    return {
        "strategy_version": key[0],
        "deployment_arm_id": key[1],
        "side": key[2],
        "unresolved_context_market": key[3] if len(key) > 3 else None,
        "canonical_epoch_verified": False,
        "live_markets": 0,
        "attributed_markets": 0,
        "blocked_markets": 0,
        "source_fill_count": 0,
        "verified_clock_markets": 0,
        "first_verified_execution_at": None,
        "last_verified_execution_at": None,
        "verified_execution_span_days": None,
        "confidence_score": None,
    }


def add_context(summary, record):
    summary["live_markets"] += 1
    summary[
        "attributed_markets"
        if record.get("status") == "attributed_source_ledger"
        else "blocked_markets"
    ] += 1
    summary["source_fill_count"] += record.get("source_fill_count", 0)
    if record.get("exchange_execution_times_verified"):
        summary["verified_clock_markets"] += 1
        for target, key, choose in (
            ("first_verified_execution_at", "first_fill_at", min),
            ("last_verified_execution_at", "last_fill_at", max),
        ):
            if record.get(key):
                stamp = datetime.fromisoformat(record[key])
                previous = summary[target]
                summary[target] = (
                    choose(stamp, datetime.fromisoformat(previous)).isoformat()
                    if previous
                    else stamp.isoformat()
                )
        first, last = summary["first_verified_execution_at"], summary["last_verified_execution_at"]
        if first and last:
            summary["verified_execution_span_days"] = (
                datetime.fromisoformat(last) - datetime.fromisoformat(first)
            ).total_seconds() / 86400


def review_packets(store, strategy="mmsell", series=None, limit=20, offset=0):
    result = envelope(strategy, limit, offset)
    migrations = {
        r["series_ticker"]: r for r in store.iter_pipeline_items("review-migrations", series)
    }
    groups = {}
    for record in PROVIDERS[strategy](store, series):
        group = groups.setdefault(
            record["series_ticker"],
            {"summary": empty_context((None, None, None)), "contexts": {}, "samples": {}},
        )
        add_context(group["summary"], record)
        key = context_key(record)
        add_context(group["contexts"].setdefault(key, empty_context(key)), record)
        # Retain bounded representatives, not all ledgers, while streaming the universe.
        samples = group["samples"]
        status = record.get("status", "blocked")
        if status not in samples:
            samples[status] = record
        if len([key for key in samples if key.startswith("extra:")]) < 3:
            samples["extra:" + record["market_ticker"]] = record
    tickers = sorted(
        set(migrations) | set(groups),
        key=lambda ticker: (
            -groups.get(ticker, {}).get("summary", {}).get("live_markets", 0),
            -bool(migrations.get(ticker, {}).get("historical_signature_present")),
            ticker,
        ),
    )
    items = []
    for ticker in tickers[offset : offset + limit]:
        group = groups.get(
            ticker, {"summary": empty_context((None, None, None)), "contexts": {}, "samples": {}}
        )
        migration = migrations.get(ticker)
        samples = []
        # Show a blocked and an attributable listing when present, then another ticker.
        ordered = list({r["market_ticker"]: r for r in group["samples"].values()}.values())
        chosen = []
        for status in ("blocked", "attributed_source_ledger"):
            sample = next((r for r in ordered if r.get("status") == status), None)
            if sample:
                chosen.append(sample)
        chosen += [r for r in ordered if r not in chosen][: 3 - len(chosen)]
        for record in chosen:
            samples.append(
                {
                    "market_ticker": record["market_ticker"],
                    "ledger_record_id": record["record_id"],
                    "economics_status": record.get("status"),
                    "blocked_reasons": record.get("blocked_reasons", []),
                    "review": review_facts(store.get("market", record["market_ticker"])),
                }
            )
        items.append(
            {
                "series_ticker": ticker,
                "series_review": review_facts(store.get("series", ticker)),
                "legacy_provenance": migration,
                "legacy_is_semantic_approval": False,
                **{
                    key: group["summary"][key]
                    for key in ("live_markets", "attributed_markets", "blocked_markets")
                },
                "contexts": [
                    value
                    for key, value in sorted(
                        group["contexts"].items(), key=lambda item: str(item[0])
                    )[:50]
                ],
                "contexts_total": len(group["contexts"]),
                "contexts_truncated": len(group["contexts"]) > 50,
                "sample_markets": samples,
                "market_samples_are_exhaustive": False,
            }
        )
    return {
        **result,
        "items": items,
        "total": len(tickers),
        "priority_basis": "live_market_volume_then_historical_signature_then_ticker",
    }


def calibration_inputs(store, strategy="mmsell", series=None, limit=20, offset=0):
    result = envelope(strategy, limit, offset)
    items, total = [], 0
    source_ids = store.state("source_coverage:live", {})
    for record in PROVIDERS[strategy](store, series):
        if offset <= total < offset + limit:
            market = store.get("market", record["market_ticker"])
            series_doc = store.get("series", record["series_ticker"])
            market_review, series_review = review_facts(market), review_facts(series_doc)
            candidate = (market or {}).get("raw", {}).get("event_ticker")
            checker = CHECK_PROVIDERS[strategy]
            checks = checker(store, record, market_review, series_review) if checker else {}
            # A future provider's requirements fail closed unless this contract proves them.
            required_checks = {
                key: checks.get(key) is True for key in result["requirements"]["required"]
            }
            item = {
                "ledger": record,
                "market_review": market_review,
                "series_review": series_review,
                "candidate_event_group": candidate,
                "candidate_group_verified_independent": False,
                "reviewed_shared_exposure_key": market_review["semantics"].get(
                    "shared_exposure_key"
                ),
                "partition": "unassigned",
                "requirements_met": required_checks,
                "missing_requirements": [key for key, met in required_checks.items() if not met],
                "source_id_coverage_checked_at": source_ids.get("checked_at"),
                "confidence_score": None,
                "qualified": False,
            }
            item["input_id"] = digest(
                {
                    "method": METHOD,
                    "strategy": strategy,
                    "requirements": result["requirements"],
                    "ledger_record_id": record["record_id"],
                    "market_hash": market_review["rules_hash"],
                    "market_review": market_review["review_id"],
                    "series_hash": series_review["rules_hash"],
                    "series_review": series_review["review_id"],
                    "market_parent_rules_changed": market_review["parent_rules_changed"],
                    "series_parent_rules_changed": series_review["parent_rules_changed"],
                    "candidate_event_group": candidate,
                    "source_coverage": {
                        key: value for key, value in source_ids.items() if key != "checked_at"
                    },
                }
            )
            items.append(item)
        total += 1
    return {**result, "items": items, "total": total}
