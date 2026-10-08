"""Strategy plug-ins produce records; they cannot change trading admission."""

import json
import logging
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .economics import SCORING_REQUIREMENTS, fill_time, ledger_assessments, live_economics
from .store import digest, now

EVALUATORS = {}


def register(strategy_id, evaluator, scoring_requirements=None):
    if strategy_id in EVALUATORS:
        raise ValueError("Evaluator already registered")
    if scoring_requirements is not None:
        if not isinstance(scoring_requirements, dict) or not scoring_requirements.get("version"):
            raise ValueError("A strategy evidence bar requires its own version")
        SCORING_REQUIREMENTS[strategy_id] = dict(scoring_requirements)
    EVALUATORS[strategy_id] = evaluator


def timestamp(value):
    if not value:
        return None
    value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def mmsell(records, as_of):
    """Own-context descriptive economics, never a gate verdict or calibrated confidence.

    Deployment arms are deliberately separated more strictly than epochs; legacy rows
    keep their book tag and execution assumptions. No prior or cross-series pooling.
    """
    groups = defaultdict(list)
    for source, row in records:
        if "mmsell" not in (row.get("strategy") or "").lower() or row.get("is_twin"):
            continue
        if source == "paper":
            if row.get("status") not in ("settled", "closed_sl"):
                continue
            price = row.get("assumed_price")
            policy = row.get("fill_assumption") or "legacy_unknown"
        else:
            if row.get("action") != "buy":
                continue
            price = row.get("price")
            policy = "live_recorded_fill"
        if price is None or not row.get("quantity") or float(row["quantity"]) <= 0:
            continue
        created = fill_time(row) if source == "live" else timestamp(row.get("created_at"))
        if not created or created > as_of:
            continue
        series = row["market_ticker"].split("-", 1)[0]
        key = (
            series,
            row["strategy"],
            row.get("experiment_deployment_arm_id"),
            row.get("side"),
            int(float(price) // 10) * 10,
            policy,
            source,
        )
        for window in ("history", "recent_30d"):
            if window == "history" or created >= as_of - timedelta(days=30):
                groups[(*key, window)].append(row)
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        series, book, arm, side, band, policy, source, window = key
        times = [fill_time(r) if source == "live" else timestamp(r.get("created_at")) for r in rows]
        clusters = defaultdict(list)
        for row in rows:
            # A conservative event token is a grouping hint, not verified independence.
            cluster = row.get("event_ticker") or row["market_ticker"].rsplit("-", 1)[0]
            if source == "paper" and row.get("pnl") is not None:
                clusters[cluster].append(float(row["pnl"]) * 100 / float(row["quantity"]))
            else:
                clusters[cluster]  # actual fills count even without attributable P&L
        pnls = [x for values in clusters.values() for x in values]
        means = [statistics.mean(values) for values in clusters.values() if values]
        se = statistics.stdev(means) / (len(means) ** 0.5) if len(means) > 1 else None
        result = {
            "schema_version": 1,
            "strategy_id": "mmsell",
            "evaluator_version": "descriptive-v1",
            "strategy_version": book,
            "series_ticker": series,
            "deployment_arm_id": arm,
            "side": side,
            "entry_band_cents": [band, band + 10],
            "execution_policy": policy,
            "evidence_source": source,
            "window": window,
            "as_of": as_of.isoformat(),
            "evidence_cutoff": max(times).isoformat(),
            "executions": len(rows),
            "contracts": sum(float(r["quantity"]) for r in rows),
            "active_days": len({t.date() for t in times}),
            "first_execution_at": min(times).isoformat(),
            "last_execution_at": max(times).isoformat(),
            "observation_span_days": (max(times) - min(times)).total_seconds() / 86400,
            "provisional_event_groups": len(clusters),
            "independent_outcomes_verified": False,
            "maturity": "paper_provisional" if source == "paper" else "live_provisional",
            "edge_cents_per_contract": statistics.mean(pnls) if pnls else None,
            "edge_definition": "mean source-reported net cents per one-contract execution",
            "cluster_mean_cents": statistics.mean(means) if means else None,
            "cluster_standard_error_cents": se,
            "confidence_score": None,
            "qualified": False,
            "qualification_reasons": [
                "calibration_pending",
                "outcome_independence_unverified",
                "forward_validation_pending",
                "semantic_review_not_bound",
            ],
            "opportunity_edge": None,
            "fill_probability": None,
            "max_loss_return": None,
            "capital_duration_hours": None,
            "input_fingerprint": digest([(source, r["id"], r) for r in rows]),
        }
        if source == "live":
            result["qualification_reasons"].append("attributed_live_net_pnl_missing")
        if arm is None:
            result["qualification_reasons"].append("legacy_version_epoch_lineage_unknown")
        if any(r.get("fees" if source == "paper" else "fee") is None for r in rows):
            result["qualification_reasons"].append("fee_coverage_incomplete")
        yield result


register("mmsell", mmsell)


def refresh(store, as_of=None):
    as_of = as_of or datetime.now(timezone.utc)
    records = store.evidence()
    economics = list(live_economics(store, records, as_of))
    store.save_live_economics(economics)
    facts = {}
    coverage = {
        source: store.state("source_coverage:" + source, {}) for source in ("paper", "live")
    }

    def results():
        streams = [(name, evaluator(records, as_of)) for name, evaluator in EVALUATORS.items()]
        streams.append(("mmsell", ledger_assessments(economics, as_of)))
        for strategy_id, stream in streams:
            for result in stream:
                if result["strategy_id"] != strategy_id:
                    raise ValueError("Evaluator cannot publish another strategy's assessments")
                # Confidence publication awaits the separately validated calibration release.
                result["qualified"] = False
                result["confidence_score"] = None
                series = result["series_ticker"]
                if series not in facts:
                    facts[series] = store.get("series", series)
                document = facts[series] or {}
                reviewed = document.get("review_status") == "reviewed"
                result["review_completion_percent"] = document.get("review_completion_pct", 0)
                result["bound_series_rules_hash"] = document.get("rules_hash") if reviewed else None
                result["scoring_requirements_version"] = SCORING_REQUIREMENTS.get(
                    result["strategy_id"], {}
                ).get("version")
                reasons = result.setdefault("qualification_reasons", [])
                if result["scoring_requirements_version"] is None:
                    reasons.append("strategy_scoring_requirements_not_registered")
                if "calibration_pending" not in reasons:
                    reasons.append("calibration_pending")
                if not reviewed and "semantic_review_not_bound" not in reasons:
                    reasons.append("semantic_review_not_bound")
                elif reviewed and "semantic_review_not_bound" in reasons:
                    reasons.remove("semantic_review_not_bound")
                # A reviewed series is not proof that every traded market's rules match it.
                reasons.append("market_semantic_review_not_bound")
                if not coverage.get(result["evidence_source"], {}).get("ids_match"):
                    reasons.append("source_id_coverage_unverified")
                if (
                    result.get("deployment_arm_id") is None
                    and "legacy_version_epoch_lineage_unknown" not in reasons
                ):
                    reasons.append("legacy_version_epoch_lineage_unknown")
                result["evidence_bar"] = {
                    "current_series_semantic_review": reviewed,
                    "current_market_semantic_review": False,
                    "source_ids_verified": bool(
                        coverage.get(result["evidence_source"], {}).get("ids_match")
                    ),
                    "live_economics_attributed": result["evaluator_version"]
                    == "exclusive-binary-ledger-v4",
                    "exchange_fill_coverage_verified": False,
                    "exchange_execution_times_verified": result.get(
                        "exchange_execution_times_verified", False
                    ),
                    "independent_outcomes_verified": False,
                    "strategy_lineage_known": result.get("deployment_arm_id") is not None,
                    "forward_validation_complete": False,
                    "numeric_requirements_calibrated": False,
                }
                result["input_fingerprint"] = digest(
                    {
                        "evidence": result["input_fingerprint"],
                        "review_hash": document.get("rules_hash"),
                        "review_status": document.get("review_status"),
                        "review": document.get("review"),
                        "qualification_reasons": reasons,
                        "requirements": result["scoring_requirements_version"],
                    }
                )
                scope = digest(
                    {
                        key: result[key]
                        for key in (
                            "strategy_id",
                            "evaluator_version",
                            "strategy_version",
                            "series_ticker",
                            "deployment_arm_id",
                            "side",
                            "entry_band_cents",
                            "execution_policy",
                            "evidence_source",
                            "window",
                        )
                    }
                )
                yield scope, result

    count = store.assessment_batch(results())
    summary = {
        "markets": len(economics),
        "attributed": sum(r["status"] == "attributed_source_ledger" for r in economics),
        "blocked": sum(r["status"] == "blocked" for r in economics),
        "blocked_reason_counts": dict(
            Counter(reason for r in economics for reason in r["blocked_reasons"])
        ),
        "exchange_fill_coverage_verified": False,
        "source_quantity_rounding_restored_fills": sum(
            r["source_quantity_rounding_restored_fills"] for r in economics
        ),
        "source_execution_time_restored_fills": sum(
            r["source_execution_time_restored_fills"] for r in economics
        ),
    }
    store.set_state("live_economics_summary", summary)
    # Counts and fixed reason codes only; no P&L, credentials or fill payloads.
    logging.getLogger("market_catalog").info("catalog_live_economics=%s", json.dumps(summary))
    # Public market tickers only, bounded samples to investigate rare evidence conflicts.
    samples = {}
    for document in economics:
        for reason in document["blocked_reasons"]:
            if (
                reason
                in {
                    "fill_after_settlement",
                    "exchange_fill_time_missing_or_inconsistent",
                    "ownership_ambiguous_or_unverified",
                    "raw_quantity_missing_or_inconsistent",
                    "actual_fill_costs_missing_or_invalid",
                }
                and len(samples.setdefault(reason, [])) < 5
            ):
                samples[reason].append(document["market_ticker"])
    logging.getLogger("market_catalog").info(
        "catalog_evidence_exception_markets=%s", json.dumps(samples)
    )
    store.set_state(
        "evaluation",
        {
            "last_success_at": now(),
            "contexts": count,
            "qualified": 0,
            "calibration": "pending",
        },
    )
    return count
