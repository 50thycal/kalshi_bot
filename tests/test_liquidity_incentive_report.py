"""Read-only report layer + livedash routes over the shadow tables."""

from __future__ import annotations

import json
from datetime import timedelta

from kalshi_bot import db
from kalshi_bot.liquidity_incentive import report as rp
from tests.test_liquidity_incentive_collector import (
    _bring_up,
    _Clock,
    _db,
    _delta,
    _snapshot,
    _trade,
)


def _populate(settings):
    _db(settings)
    clock = _Clock()
    st = _bring_up(settings, clock, liquidity_incentive_capital_tiers="25,100")
    st.handle_message(_snapshot("KXT-A", yes=[(46, 900), (40, 50)], no=[(48, 900), (60, 30)]))
    st.tick()
    clock.tick(10)
    st.handle_message(_trade("KXT-A", 46, 30, "no", "t1"))
    clock.tick(300)
    st.tick()
    st.handle_message(_delta("KXT-A", "yes", 46, -900, seq=2))
    st.handle_message(_delta("KXT-A", "yes", 44, 900, seq=3))
    st.tick(clock.tick(60))
    return st, clock


def test_active_history_headline_payloads(settings):
    _st, clock = _populate(settings)
    now = clock.now + timedelta(seconds=1)
    with db.session_scope() as s:
        active = rp.build_active(s, policy="A_break_even", tier=100, now=now)
        history = rp.build_history(s, days=3, now=now)
        head = rp.build_headline(s, days=3, now=now)
    json.dumps(active), json.dumps(history), json.dumps(head)     # serialisable
    assert active["n_programs"] == 1 and active["collector"]["alive"] is True
    row = active["rows"][0]
    assert row["ticker"] == "KXT-A" and row["target_size"] == 1000 and row["period_reward_usd"] == 100
    assert row["reward_per_day_usd"] == 100.0          # 24h program
    assert row["quote"]["yes_bid"] == 44 and row["state"] in ("IGNORE", "WATCH", "SHADOW", "POC_CANDIDATE")
    assert {"est_reward_per_day_usd", "est_single_leg_cost_per_day_usd", "capital_cost_per_day_usd",
            "est_net_per_day_usd", "state_reason"} <= set(row)
    assert row["conservative"]["n"] == 1 and row["optimistic"]["n"] == 1
    # history: per-day landscape + cells per model, never summed across models
    day = list(history["by_day"].values())[-1]
    assert day["programs"] == 1 and day["reward_per_day_usd"] == 100.0 and day["median_target_size"] == 1000
    models = {(c["policy"], c["tier"], c["fill_model"]) for c in history["cells"]}
    assert ("A_break_even", 100, "optimistic") in models and ("A_break_even", 100, "conservative") in models
    assert history["outcome_mix"]["optimistic"].get("yes_only", 0) >= 2
    assert history["both_given_one"]["optimistic"]["p_both_given_one"] == 0.0
    assert any(mk["horizon_seconds"] == 300 for mk in history["marks"])
    assert history["end_reasons"] == {"cancel_market_moved": 6}
    # headline: one row per policy x tier x model, with span
    assert head["span_days"] > 0 and len(head["table"]) == 3 * 2 * 3
    opt = next(r for r in head["table"] if r["fill_model"] == "optimistic" and r["policy"] == "A_break_even" and r["tier"] == 25)
    assert opt["n_outcomes"] == 1 and opt["net_per_day_usd"] is not None


def test_active_sort_keys_and_unknown_sort(settings):
    _populate(settings)
    with db.session_scope() as s:
        for key in rp.SORT_KEYS + ("nonsense",):
            payload = rp.build_active(s, sort=key)
            assert payload["sort"] in rp.SORT_KEYS


def test_livedash_routes_are_read_only_and_answer(settings):
    _populate(settings)
    from kalshi_bot.livedash import server as srv

    captured = {}

    class _H(srv.LiveDashHandler):
        def __init__(self):
            self._responded = False
            self._bytes_sent = 0

        def _json(self, payload, code=200):
            captured["code"], captured["payload"] = code, payload

        def _send(self, code, body, content_type):
            captured["code"], captured["body"], captured["ct"] = code, body, content_type

    h = _H()
    h._dispatch("/incentives", {})
    assert captured["code"] == 200 and b"Liquidity incentive shadow" in captured["body"]
    for path in ("/api/incentives/active", "/api/incentives/history", "/api/incentives/headline"):
        h._dispatch(path, {"policy": ["A_break_even"], "tier": ["100"], "days": ["3"]})
        assert captured["code"] == 200 and "generated_at" in captured["payload"], path
    h._dispatch("/api/incentives/active", {"policy": ["../evil"]})
    assert captured["payload"]["policy"] == "A_break_even"
    assert not hasattr(srv.LiveDashHandler, "do_POST")
