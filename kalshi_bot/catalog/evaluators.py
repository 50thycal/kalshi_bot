"""Strategy plug-ins produce records; they cannot change trading admission."""

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .store import digest, now

EVALUATORS = {}


def register(strategy_id, evaluator):
    if strategy_id in EVALUATORS:
        raise ValueError("Evaluator already registered")
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
        created = timestamp(row.get("filled_at") or row.get("created_at"))
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
        times = [timestamp(r.get("filled_at") or r.get("created_at")) for r in rows]
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
    count = 0
    active_scopes = []
    for evaluator in EVALUATORS.values():
        for result in evaluator(records, as_of):
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
            active_scopes.append(scope)
            previous = store.state("assessment_input:" + scope)
            # No new snapshot on a no-op refresh; freshness of the job is separate.
            if previous != result["input_fingerprint"]:
                store.assessment(scope, result)
                store.set_state("assessment_input:" + scope, result["input_fingerprint"])
            count += 1
    with store.connect() as db:
        # Retain historical snapshots but remove aged-out windows from current selection.
        current = db.execute("SELECT scope FROM current_assessments").fetchall()
        active = set(active_scopes)
        for row in current:
            if row[0] not in active:
                db.execute("DELETE FROM current_assessments WHERE scope=?", (row[0],))
    store.set_state(
        "evaluation",
        {"last_success_at": now(), "contexts": count, "qualified": 0, "calibration": "pending"},
    )
    return count
