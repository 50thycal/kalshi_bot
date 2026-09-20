"""Cash performance, price discipline, and forecast skill are distinct measures.

Unfilled forecasts remain in calibration. Voids do not become binary outcomes.
Money is Decimal throughout; drawdown uses settled cash P&L, not inferred marks.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

D = Decimal
ZERO = D(0)


def _number(value):
    return D(str(value or 0))


def _payload(row):
    return row.get("payload") or row


def _settled(row):
    return bool(row.get("settled") or row.get("status") == "settled")


def _grade(row):
    if not _settled(row):
        return None
    payout = row.get("yes_payout")
    if payout is None:
        payout = (row.get("settlement") or {}).get("yes_payout")
    if payout is None or _number(payout) not in (ZERO, D(1)):
        return None
    payload = _payload(row)
    if payload.get("probability") is None:
        return None  # An absent estimate is missing evidence, never a fabricated 50%.
    probability = _number(payload["probability"])
    outcome = _number(payout)
    if payload.get("side", row.get("side")) == "no":
        outcome = 1 - outcome
    return probability, outcome


def _calibration(rows):
    grades = [grade for row in rows if (grade := _grade(row)) is not None]
    bins = defaultdict(list)
    for probability, outcome in grades:
        index = min(9, int(probability * 10))
        bins[index].append((probability, outcome))
    return {
        "settled_forecasts": len(grades),
        "brier_score": str(sum(((p - y) ** 2 for p, y in grades), ZERO) / len(grades))
        if grades
        else None,
        "mean_forecast": str(sum((p for p, _ in grades), ZERO) / len(grades)) if grades else None,
        "realized_win_rate": str(sum((y for _, y in grades), ZERO) / len(grades))
        if grades
        else None,
        "calibration_bins": [
            {
                "lower": str(D(i) / 10),
                "upper": str(D(i + 1) / 10),
                "count": len(items),
                "mean_forecast": str(sum((p for p, _ in items), ZERO) / len(items)),
                "observed_frequency": str(sum((y for _, y in items), ZERO) / len(items)),
            }
            for i, items in sorted(bins.items())
        ],
    }


def _drawdown(rows):
    def settled_time(pair):
        raw = (pair[1].get("settlement") or {}).get("settled_at") or pair[1].get("updated_at")
        at = datetime.fromisoformat(raw) if raw else datetime.min.replace(tzinfo=timezone.utc)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at.astimezone(timezone.utc), pair[0]

    ordered = sorted(enumerate(rows), key=settled_time)
    cumulative = peak = drawdown = ZERO
    for _, row in ordered:
        cumulative += _number(row.get("pnl"))
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    return drawdown


def comparison(snapshot: dict) -> dict:
    result = {}
    for desk in ("chatgpt", "claude"):
        rows = [r for r in snapshot.get("decisions", []) if r.get("desk_id") == desk]
        filled = [r for r in rows if _number(r.get("filled_quantity")) > 0]
        settled = [r for r in rows if _settled(r)]
        settled_filled = [r for r in filled if _settled(r)]
        spent = sum((_number(r.get("fill_cost")) + _number(r.get("fees")) for r in rows), ZERO)
        settled_spent = sum(
            (_number(r.get("fill_cost")) + _number(r.get("fees")) for r in settled_filled), ZERO
        )
        pnl = sum((_number(r.get("pnl")) for r in settled), ZERO)
        predicted_edges = [
            _number(_payload(r)["probability"]) * _number(r["filled_quantity"])
            - _number(r.get("fill_cost"))
            - _number(r.get("fees"))
            for r in filled
            if _payload(r).get("probability") is not None
        ]
        reviewed = {
            p.get("payload", {}).get("decision_id")
            for p in snapshot.get("publications", [])
            if p.get("desk_id") == desk and p.get("kind") == "postmortem"
        }
        backlog = [r for r in settled if r.get("decision_id") not in reviewed]
        losing_backlog = [
            r
            for r in backlog
            if _number(r.get("filled_quantity")) > 0 and _number(r.get("pnl")) < 0
        ]
        classes = defaultdict(list)
        for row in rows:
            classes[_payload(row).get("edge_class", "unknown")].append(row)
        edge_classes = {}
        for edge_class, group in sorted(classes.items()):
            edge_classes[edge_class] = {
                "decisions": len(group),
                **_calibration(group),
                "realized_pnl": str(
                    sum(
                        (
                            _number(r.get("pnl"))
                            for r in group
                            if _settled(r) and _number(r.get("filled_quantity")) > 0
                        ),
                        ZERO,
                    )
                ),
            }
        costs = snapshot.get("research", {}).get("costs", {}).get(desk, {})
        actual_cost = _number(costs["actual_usd"]) if costs.get("actual_usd") is not None else None
        unresolved = (
            _number(costs["unresolved_reserved_usd"])
            if costs.get("unresolved_reserved_usd") is not None
            else None
        )
        all_costs_known = actual_cost is not None and unresolved == ZERO
        result[desk] = {
            "filled_picks": len(filled),
            "decisions": len(rows),
            **_calibration(rows),
            "independent_settled_events": len(
                {_payload(r).get("event_id", r["decision_id"]) for r in settled_filled}
            ),
            "independent_idea_picks": sum(not _payload(r).get("borrowed_from") for r in filled),
            "borrowed_idea_picks": sum(bool(_payload(r).get("borrowed_from")) for r in filled),
            "actual_dollars_deployed": str(spent),
            "settled_dollars_deployed": str(settled_spent),
            "realized_pnl": str(pnl),
            "return_on_deployed": str(pnl / spent) if spent else None,
            "return_on_settled_deployed": str(pnl / settled_spent) if settled_spent else None,
            "realized_max_drawdown": str(_drawdown(settled)),
            "predicted_profit_at_actual_fills": str(sum(predicted_edges, ZERO)),
            "negative_estimated_edge_filled_picks": sum(edge < 0 for edge in predicted_edges),
            "fill_rate": len(filled) / len(rows) if rows else None,
            "unreviewed_settlements": len(backlog),
            "unreviewed_losses": len(losing_backlog),
            "losses_awaiting_postmortem": [r["decision_id"] for r in losing_backlog],
            "research_actual_cost": str(actual_cost) if actual_cost is not None else None,
            "research_unresolved_reservations": str(unresolved) if unresolved is not None else None,
            "pnl_after_known_research_cost": str(pnl - actual_cost)
            if actual_cost is not None
            else None,
            "all_in_pnl": str(pnl - actual_cost) if all_costs_known else None,
            "all_in_cost_scope": "Recorded desk research costs only; excludes shared hosting and subscriptions.",
            "edge_classes": edge_classes,
            "evidence_warning": "Exploratory sample; no automatic sizing or edge claim.",
        }
    return result
