"""What the fleet is ASKING the operator for, surfaced so it can't be missed.

The data was always there — `_requests` has fed the fleet payload since the
dashboard shipped — but it rendered as capability + category + a count, buried
inside a collapsed "System components & capability requests" panel, with the
`problem` text fetched and then never displayed. So the single most actionable
signal the fleet produced went unread for a week:

  agent 0bb6dd17 filed EIGHTEEN tickets in six days asking to switch off two
  strategies its own backtest had measured at -$0.0476/trade over 4,339 trades.

It escaped ticket dedup because `find_duplicate` keys on (category, capability)
and the agent kept varying BOTH — cycling performance -> bug_report ->
infrastructure -> platform_deployment -> permissions looking for one that would
land. Eighteen near-identical rows is also what buried 25e446f0's genuinely
distinct request (a CPI backtest corpus) underneath them.

So the panel groups across category by the same token signature the deduper
uses, and repetition becomes the headline rather than the noise: one row saying
"asked 18 times over 6 days" is the operator signal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_bot.dashboard import data as dash
from kalshi_bot.evo import tickets
from tests.test_evo_foundations import _mk_agent

NOW = datetime.now(timezone.utc)


def _ask(session, agent_uuid, capability, *, category="other", urgency="normal",
         problem="it is losing money"):
    row, err, _dedup = tickets.submit_ticket(
        session, agent_uuid=agent_uuid, category=category, capability=capability,
        problem=problem, urgency=urgency,
    )
    assert err is None, err
    return row


def test_repeated_asks_collapse_into_one_row_with_a_count(evo_session):
    """The live pattern: same ask, different category each time."""
    agent = _mk_agent(evo_session)
    for cat in ("performance", "bug_report", "infrastructure", "platform_deployment",
                "permissions"):
        _ask(evo_session, agent.agent_uuid, "deactivate strategy 49 and 50",
             category=cat, urgency="high")

    rows = dash._requests(evo_session)
    assert len(rows) == 1, f"expected one grouped ask, got {len(rows)}: {rows}"
    assert rows[0]["times_asked"] == 5
    assert rows[0]["urgency"] == "high"


def test_a_distinct_ask_is_not_swallowed_by_a_noisy_one(evo_session):
    """25e446f0's CPI request must survive next to 18 deactivation tickets."""
    a, b = _mk_agent(evo_session), _mk_agent(evo_session, surname="Voss")
    for cat in ("performance", "bug_report", "infrastructure"):
        _ask(evo_session, a.agent_uuid, "deactivate negative EV strategies",
             category=cat, urgency="high")
    _ask(evo_session, b.agent_uuid,
         "add a settled CPI market corpus as a run_backtest dataset",
         category="data_collection", urgency="low",
         problem="cannot validate any CPI thesis without settled data")

    rows = dash._requests(evo_session)
    caps = " | ".join(r["request"].lower() for r in rows)
    assert "cpi" in caps, f"the distinct request was swallowed: {caps}"
    assert len(rows) == 2


def test_the_reason_reaches_the_operator(evo_session):
    """`problem` was queried and then dropped on the floor by the renderer. It is
    the whole point — the capability line alone does not say why."""
    agent = _mk_agent(evo_session)
    _ask(evo_session, agent.agent_uuid, "deactivate strategy 49",
         problem="backtest 1855: 4339 trades at -$0.0476/trade")
    row = dash._requests(evo_session)[0]
    assert "1855" in row["reason"]


def test_rows_carry_who_asked_and_how_long_it_has_been_open(evo_session):
    agent = _mk_agent(evo_session)
    t = _ask(evo_session, agent.agent_uuid, "deactivate strategy 49")
    t.created_at = NOW - timedelta(days=6)
    evo_session.flush()

    row = dash._requests(evo_session)[0]
    assert row["agent"] == agent.agent_uuid[:8]
    assert row["age_days"] >= 6


def test_loudest_and_oldest_asks_sort_to_the_top(evo_session):
    """An operator scanning the panel should hit the thing being screamed about
    first, not whatever happened to be filed last."""
    a, b = _mk_agent(evo_session), _mk_agent(evo_session, surname="Voss")
    _ask(evo_session, b.agent_uuid, "add a CPI backtest dataset please",
         category="data_collection", urgency="low")
    for cat in ("performance", "bug_report", "infrastructure"):
        _ask(evo_session, a.agent_uuid, "deactivate the losing strategies now",
             category=cat, urgency="high")

    rows = dash._requests(evo_session)
    assert rows[0]["times_asked"] == 3
    assert "deactivate" in rows[0]["request"].lower()


def test_fleet_payload_exposes_an_open_request_count(evo_session, evo_settings):
    """A number the operator can see without expanding anything — the old panel
    was collapsed by default, which is why 18 tickets went unnoticed."""
    agent = _mk_agent(evo_session)
    for cat in ("performance", "bug_report"):
        _ask(evo_session, agent.agent_uuid, "deactivate strategy 49", category=cat)
    _ask(evo_session, agent.agent_uuid, "add a CPI dataset", category="data_collection")

    payload = dash.build_fleet(evo_session, evo_settings, now=NOW)
    assert payload["request_count"] == 2, "distinct asks, not raw ticket rows"
    assert payload["requests"]


def test_no_open_tickets_is_a_clean_empty(evo_session, evo_settings):
    assert dash._requests(evo_session) == []
    assert dash.build_fleet(evo_session, evo_settings, now=NOW)["request_count"] == 0
