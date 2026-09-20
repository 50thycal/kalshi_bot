"""Forecast accuracy cannot stand in for trade profitability or learning."""

from decimal import Decimal

from kalshi_bot.desks.scoreboard import comparison

D = Decimal


def row(
    ident,
    *,
    p="0.7",
    yes="1",
    qty="1",
    cost="0.76",
    fees="0.02",
    pnl="0.22",
    side="yes",
    settled=True,
    borrowed=None,
    edge="information",
    day="01",
):
    return {
        "decision_id": ident,
        "desk_id": "chatgpt",
        "status": "terminal",
        "settled": settled,
        "filled_quantity": qty,
        "fill_cost": cost,
        "fees": fees,
        "pnl": pnl,
        "yes_payout": yes,
        "settlement": {"yes_payout": yes, "settled_at": f"2026-10-{day}T00:00:00Z"},
        "payload": {
            "probability": p,
            "side": side,
            "event_id": "event-" + ident,
            "borrowed_from": borrowed or [],
            "edge_class": edge,
        },
    }


def scores(rows, publications=None, costs=None):
    snapshot = {"decisions": rows, "publications": publications or []}
    if costs is not None:
        snapshot["research"] = {"costs": {"chatgpt": costs}}
    return comparison(snapshot)["chatgpt"]


def test_winning_outcome_does_not_erase_bad_entry_price():
    result = scores([row("price-chased")])
    assert D(result["realized_win_rate"]) == 1
    assert D(result["realized_pnl"]) == D("0.22")
    assert D(result["predicted_profit_at_actual_fills"]) == D("-0.08")
    assert result["negative_estimated_edge_filled_picks"] == 1
    assert D(result["brier_score"]) == D("0.09")


def test_unfilled_forecasts_graded_without_hypothetical_profit_and_void_excluded():
    result = scores(
        [
            row("unfilled", p="0.8", yes="0", qty="0", cost="0", fees="0", pnl="0"),
            row("void", yes="0.5", cost="0.6", fees="0", pnl="-0.1"),
            row("no-side", p="0.9", yes="0", side="no", cost="0.5", fees="0", pnl="0.5"),
        ]
    )
    assert result["settled_forecasts"] == 2
    assert result["filled_picks"] == 2
    assert D(result["brier_score"]) == D("0.325")
    assert D(result["realized_pnl"]) == D("0.4")
    assert result["independent_settled_events"] == 2


def test_edge_calibration_borrowed_attribution_and_realized_drawdown():
    result = scores(
        [
            row("one", pnl="0.4", day="01"),
            row("two", yes="0", pnl="-0.7", day="02", borrowed=["claude-publication"]),
            row("three", pnl="0.2", day="03", edge="mechanics"),
            row("four", yes="0", pnl="-0.5", day="04", edge="mechanics"),
        ]
    )
    assert result["independent_idea_picks"] == 3
    assert result["borrowed_idea_picks"] == 1
    assert D(result["realized_max_drawdown"]) == D("1.0")
    assert result["edge_classes"]["information"]["settled_forecasts"] == 2
    assert D(result["edge_classes"]["information"]["brier_score"]) == D("0.29")
    assert result["calibration_bins"][0]["count"] == 4


def test_loss_postmortem_backlog_requires_own_desk_provenance():
    rows = [row("loss", yes="0", pnl="-0.78"), row("win")]
    peer_review = {"desk_id": "claude", "kind": "postmortem", "payload": {"decision_id": "loss"}}
    result = scores(rows, [peer_review])
    assert result["unreviewed_losses"] == 1
    assert result["losses_awaiting_postmortem"] == ["loss"]
    own_review = {"desk_id": "chatgpt", "kind": "postmortem", "payload": {"decision_id": "loss"}}
    result = scores(rows, [peer_review, own_review])
    assert result["unreviewed_losses"] == 0
    assert result["unreviewed_settlements"] == 1


def test_unresolved_model_billing_prevents_false_all_in_profit():
    result = scores([row("one")], costs={"actual_usd": "0.1", "unresolved_reserved_usd": "0.2"})
    assert result["all_in_pnl"] is None
    assert D(result["pnl_after_known_research_cost"]) == D("0.12")
    result = scores([row("one")], costs={"actual_usd": "0.1", "unresolved_reserved_usd": "0"})
    assert D(result["all_in_pnl"]) == D("0.12")
    assert scores([row("one")])["all_in_pnl"] is None


def test_missing_probability_is_not_silently_fifty_percent():
    missing = row("missing")
    missing["payload"].pop("probability")
    assert scores([missing])["settled_forecasts"] == 0
