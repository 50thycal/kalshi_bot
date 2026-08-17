"""Control Tower rendering — tests target MISREADINGS, not formatting.

The Tower's numbers were already right; a fresh Claude session still drew four
wrong conclusions from them. Each is pinned here:

  * `INACTIVE` read as "dead for 69 days" (it means "not part of this
    deployment") and then blamed for six unrelated blocked gates;
  * `BLOCKED_DATA` shown without its cause, so the reader inferred one from
    whatever else was on screen;
  * every block routed to Platform Change Review, when a missing metric
    provider is experiment/metrics work and changes no shared semantic;
  * a 7-day retirement lookback described as "the last cycle".

A report that is correct but easy to misread is not finished.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.experiment_os import control_tower as ct
from kalshi_bot.experiment_os import service as svc
from kalshi_bot.models import PaperTrade

UTC = timezone.utc
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _trade(s, tag, *, pnl=0.05, at=None, status="settled"):
    s.add(PaperTrade(
        market_ticker=f"T-{tag}-{id(object())}", strategy=tag, status=status,
        pnl=pnl, quantity=1, created_at=at or (T0 + timedelta(hours=1)),
    ))


def _experiment(s, key, *, spec, tag=None, state="PAPER"):
    tag = tag or f"{key}-t"
    exp = svc.create_experiment(s, key=key, origin="operator")
    ver = svc.create_experiment_version(
        s, exp, hypothesis="h", independent_variable="lever", now=T0,
        control_exemption_reason="test shape: absolute threshold, no control arm",
    )
    svc.add_arm(s, ver, arm_key="treatment", role="treatment", strategy_tag=tag)
    svc.freeze_version(s, ver, now=T0)
    epoch = svc.open_epoch(s, ver, reason="initial", started_at=T0)
    svc.register_deployment(
        s, epoch, deployment_key=f"{key}-paper-1", stage="PAPER", kind="paper",
        arms={"treatment": tag}, started_at=T0,
    )
    svc.transition_experiment(s, exp, "PROBE", actor="operator")
    svc.transition_experiment(s, exp, "PAPER", actor="operator")
    gate = svc.register_gate(
        s, ver, gate_key="paper_to_live_canary", kind="promotion", spec=spec,
        from_state="PAPER", to_state="LIVE_CANARY", registered_at=T0,
    )
    svc.mark_gate_evidence_started(s, gate, at=T0)
    s.commit()
    return exp, ver, epoch, gate, tag


# A gate whose clause has no canonical provider — the real production shape.
UNPROVIDED_SPEC = {
    "pass_all": [{"metric": "realized_tail_hit_ratio_vs_modeled", "arm": "treatment",
                  "op": ">", "value": 0}],
}
COMPUTABLE_SPEC = {
    "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
    "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                  "op": ">", "value": 0}],
}


# ---------------------------------------------------------------------------
# INACTIVE is informational, not an outage
# ---------------------------------------------------------------------------


def test_inactive_collector_is_not_a_warning_and_never_an_action(xos_session,
                                                                 xos_platform):
    s = xos_session
    health = ct._data_health(s)
    by_name = {c["collector"]: c for c in health}
    # Every collector reports whether it is a PROBLEM, separately from its label.
    for c in health:
        assert "warning" in c, c
        assert c["warning"] is (c["status"] in ct.COLLECTOR_WARNING_STATUSES)
    assert "market_snapshots" in by_name

    rep = ct.build_report(s, evaluate=False)
    rep.data_health = [
        {"collector": "market_snapshots", "status": "INACTIVE", "warning": False,
         "age_min": 99704.2, "age_days": 69.2, "cadence_min": 60,
         "note": "scanner-mode table; the live worker does not write it"},
        {"collector": "weather_forecasts", "status": "fresh", "warning": False,
         "age_min": 1.0, "age_days": 0.0, "cadence_min": 15, "note": None},
    ]
    rep.recommendations = []
    ct._derive_actions(rep)
    joined = " ".join(rep.recommendations)
    assert "market_snapshots" not in joined, (
        "an INACTIVE collector must not generate a health action — that is what "
        "invites a fresh reader to invent a cause for it"
    )


@pytest.mark.parametrize("status", sorted(ct.COLLECTOR_WARNING_STATUSES))
def test_real_collector_problems_are_still_surfaced_prominently(xos_session,
                                                                xos_platform,
                                                                status):
    s = xos_session
    rep = ct.build_report(s, evaluate=False)
    rep.data_health = [{"collector": "weather_observations", "status": status,
                        "warning": True, "age_min": 900.0, "age_days": 0.6,
                        "cadence_min": 15, "note": None}]
    rep.recommendations = []
    ct._derive_actions(rep)
    text = " ".join(rep.recommendations)
    assert "weather_observations" in text and status in text
    assert "Live Ops" in text
    out = ct.render(rep)
    assert f"weather_observations  {status}" in out or "weather_observations" in out


def test_render_separates_inactive_and_says_what_it_means(xos_session, xos_platform):
    s = xos_session
    rep = ct.build_report(s, evaluate=False)
    rep.data_health = [
        {"collector": "weather_forecasts", "status": "fresh", "warning": False,
         "age_min": 1.0, "age_days": 0.0, "cadence_min": 15, "note": None},
        {"collector": "market_snapshots", "status": "INACTIVE", "warning": False,
         "age_min": 99704.2, "age_days": 69.2, "cadence_min": 60,
         "note": "scanner-mode table; the live worker does not write it"},
    ]
    out = ct.render(rep)
    assert "NOT ACTIVE IN THIS DEPLOYMENT" in out
    assert "informational, NOT an outage" in out
    assert "not expected to be active in the current deployment" in out
    assert "Only STALE / EMPTY / UNAVAILABLE warrant collector-health" in out
    assert "scanner-mode table" in out
    # The freshness table itself no longer mixes the two.
    table = out.split("=== DATA COLLECTORS ===")[1].split("NOT ACTIVE")[0]
    assert "weather_forecasts" in table and "market_snapshots" not in table


# ---------------------------------------------------------------------------
# BLOCKED_DATA leads with the evaluator's own cause
# ---------------------------------------------------------------------------


def test_blocked_data_names_the_missing_provider(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "exp-unprovided", spec=UNPROVIDED_SPEC)
    _trade(s, "exp-unprovided-t")
    s.commit()

    rep = ct.build_report(s, evaluate=True)
    assert len(rep.blocked) == 1
    b = rep.blocked[0]
    assert b["verdict"] == "BLOCKED_DATA"
    assert b["missing_metrics"] == ["realized_tail_hit_ratio_vs_modeled"]
    assert any("no canonical provider" in r for r in b["reasons"])

    out = ct.render(rep)
    assert "=== BLOCKED EVIDENCE ===" in out
    assert "missing provider: realized_tail_hit_ratio_vs_modeled" in out
    # And READY/DUE leads with the cause rather than the bare verdict.
    ready = [r for r in rep.ready_due if "BLOCKED_DATA" in r]
    assert ready and "realized_tail_hit_ratio_vs_modeled" in ready[0]


def test_the_cause_comes_from_the_recorded_result_when_one_exists(xos_session,
                                                                  xos_platform):
    """The official record outranks a dry run — and carries the same provenance."""
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    _, _, _, gate, _ = _experiment(s, "exp-recorded", spec=UNPROVIDED_SPEC)
    _trade(s, "exp-recorded-t")
    s.commit()
    evaluator.evaluate_gate(s, gate)  # records BLOCKED_DATA
    s.commit()

    rep = ct.build_report(s, evaluate=False)  # no dry run at all
    assert len(rep.blocked) == 1
    b = rep.blocked[0]
    assert b["source"] == "recorded"
    assert b["missing_metrics"] == ["realized_tail_hit_ratio_vs_modeled"]
    assert "missing provider: realized_tail_hit_ratio_vs_modeled" in ct.render(rep)


def test_a_computable_gate_is_not_listed_as_blocked(xos_session, xos_platform):
    s = xos_session
    _experiment(s, "exp-ok", spec=COMPUTABLE_SPEC)
    for _ in range(10):
        _trade(s, "exp-ok-t")
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert rep.blocked == []
    assert "=== BLOCKED EVIDENCE ===\n    (none)" in ct.render(rep)


def test_missing_metrics_extraction_reads_the_evaluator_record(xos_session):
    """One extraction shared by every surface — no second opinion on the cause."""
    from kalshi_bot.experiment_os.evaluator import missing_metrics

    clauses = [
        {"clause": {"metric": "pnl_cents_per_trade"}, "missing": False},
        {"clause": {"metric": "clean_pairs"}, "missing": True},
        {"clause": {"metric": "realizable_cents_per_trade"}, "missing": True},
        {"clause": {"metric": "clean_pairs"}, "missing": True},  # deduped
        "not a dict",
    ]
    assert missing_metrics(clauses) == ["clean_pairs", "realizable_cents_per_trade"]
    assert missing_metrics(None) == []


# ---------------------------------------------------------------------------
# Routing: a missing provider is not a platform change
# ---------------------------------------------------------------------------


def test_blocked_data_routes_to_research_lab_not_platform_change_review(
    xos_session, xos_platform
):
    s = xos_session
    _experiment(s, "exp-route-data", spec=UNPROVIDED_SPEC)
    _trade(s, "exp-route-data-t")
    s.commit()
    rep = ct.build_report(s, evaluate=True)

    owner = rep.blocked[0]["owner"]
    assert "Research Lab" in owner
    assert "NOT a Platform Revision" in owner

    handoffs = [r for r in rep.recommendations if "blocked evidence" in r]
    assert handoffs, rep.recommendations
    assert "Research Lab" in handoffs[0]
    # No unqualified platform-review handoff was manufactured for this block.
    assert not any(
        r.strip().endswith("Platform Change Review") for r in rep.recommendations
    )


def test_blocked_platform_routes_to_platform_change_review():
    assert "Platform Change Review" in ct.BLOCK_OWNER["BLOCKED_PLATFORM"]
    assert "Platform Change Review" not in ct.BLOCK_OWNER["BLOCKED_DATA"].split(
        "NOT a Platform Revision")[0]
    entry = ct._blocked_gate_entry(
        {"key": "e"},
        {"gate_key": "g", "latest_result": {"verdict": "BLOCKED_PLATFORM",
                                            "blocking_reasons": ["snapshot moved"],
                                            "missing_metrics": []}},
    )
    assert entry["owner"] == ct.BLOCK_OWNER["BLOCKED_PLATFORM"]
    assert ct._block_cause_line(entry) == "snapshot moved"


def test_blocked_integrity_routing_names_the_cause_not_one_role():
    owner = ct.BLOCK_OWNER["BLOCKED_INTEGRITY"]
    for role in ("Live Ops", "Platform Change Review", "Research Lab"):
        assert role in owner


def test_integrity_event_recommendation_uses_the_event_kind(xos_session,
                                                            xos_platform):
    s = xos_session
    exp, _, _, _, _ = _experiment(s, "exp-integ", spec=COMPUTABLE_SPEC)
    svc.record_integrity_event(
        s, exp, kind="EXPERIMENT_CONFIG_DRIFT",
        description="deployed config differs from the registered arm",
    )
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    rec = [r for r in rep.recommendations if "exp-integ" in r]
    assert rec and "EXPERIMENT_CONFIG_DRIFT" in rec[0]
    assert ct.INTEGRITY_OWNER["EXPERIMENT_CONFIG_DRIFT"] in rec[0]


def test_render_states_the_routing_rule_explicitly(xos_session, xos_platform):
    out = ct.render(ct.build_report(xos_session, evaluate=False))
    assert "Route the write by what is actually blocking" in out
    assert "This is NOT automatically a Platform Revision." in out
    assert "BLOCKED_PLATFORM → Platform Change Review" in out
    assert ("Recommend Platform Change Review only when shared platform semantics"
            in out)


# ---------------------------------------------------------------------------
# The retirement window is stated, not implied
# ---------------------------------------------------------------------------


def test_recently_retired_states_its_real_lookback(xos_session, xos_platform):
    s = xos_session
    rep = ct.build_report(s, evaluate=False, retired_days=7)
    assert rep.retired_days == 7
    out = ct.render(rep)
    assert "=== RECENTLY RETIRED — last 7 days ===" in out
    assert "cycle" not in out.split("RECENTLY RETIRED")[1].split("===")[0]

    rep30 = ct.build_report(s, evaluate=False, retired_days=30)
    assert "=== RECENTLY RETIRED — last 30 days ===" in ct.render(rep30)


def test_empty_retired_section_still_states_the_window(xos_session, xos_platform):
    out = ct.render(ct.build_report(xos_session, evaluate=False, retired_days=14))
    assert "(none retired in the last 14 days)" in out


def test_retired_window_selects_on_when_the_book_died(xos_session, xos_platform):
    """The fresh-session finding: filtering on the RETIRED transition put the
    whole graveyard inside every window, because the legacy import stamped all of
    them at one instant. 17 shown, 10 actually retired that week. A stated window
    that is wrong is worse than an unstated one."""
    s = xos_session
    now = datetime.now(UTC)
    import_instant = now - timedelta(hours=1)  # when the import wrote every row

    for key, retired_at in (
        ("gone-recently", now - timedelta(days=2)),
        ("gone-long-ago", now - timedelta(days=90)),
        ("gone-undated", None),
    ):
        exp = svc.create_experiment(s, key=key, origin="operator")
        svc.transition_experiment(s, exp, "PROBE", actor="import",
                                  occurred_at=import_instant)
        svc.transition_experiment(s, exp, "RETIRED", actor="import",
                                  reason="killed", occurred_at=import_instant)
        exp.retired_at = retired_at
    s.commit()

    rep = ct.build_report(s, evaluate=False, retired_days=7)
    keys = [r["key"] for r in rep.retired_recent]
    assert "gone-recently" in keys
    assert "gone-long-ago" not in keys, (
        "a book retired 90 days ago is not recent just because its transition row "
        "was written during today's import"
    )
    # Undated records fall back to the transition, but say so rather than pretend.
    undated = next(r for r in rep.retired_recent if r["key"] == "gone-undated")
    assert undated["dated"] is False
    assert rep.retired_undated == ["gone-undated"]

    out = ct.render(rep)
    assert "[no retired_at — transition date]" in out
    assert "carry NO retired_at" in out
    assert "not by when its record was written" in out


# ---------------------------------------------------------------------------
# A PASS must say which KIND of PASS it is
# ---------------------------------------------------------------------------


def test_a_recorded_pass_is_not_labelled_a_dry_run(xos_session, xos_platform):
    """The second fresh-session finding. READY/DUE hard-coded "(dry-run)" on every
    promotion PASS while sourcing the verdict from `live_verdict or latest_result`,
    so a real recorded PASS — the only thing that can authorize a transition —
    read as "a RECORDED evaluator PASS is still required". A reader triaging off
    that line would conclude there was no decision to make."""
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    _, _, _, gate, tag = _experiment(s, "exp-passing", spec=COMPUTABLE_SPEC)
    for _ in range(10):
        _trade(s, tag, pnl=0.05)
    s.commit()

    # Dry run only: nothing recorded yet.
    rep = ct.build_report(s, evaluate=True)
    line = next(r for r in rep.ready_due if "GATE PASS" in r)
    assert "dry-run only" in line
    assert "nothing is authorized yet" in line

    # Now record it. The same evidence, but an official result exists.
    out = evaluator.evaluate_gate(s, gate)
    s.commit()
    assert out.verdict == "PASS"

    rep = ct.build_report(s, evaluate=True)
    line = next(r for r in rep.ready_due if "GATE PASS" in r)
    assert "RECORDED" in line and "dry-run" not in line
    assert "system" in line  # provenance, so the reader can check it
    assert "CAN authorize" in line
    # And it still refuses to imply the promotion happens by itself.
    assert "operator act" in line and "not automatic" in line


def test_a_recorded_fail_is_not_labelled_a_dry_run(xos_session, xos_platform):
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    _, _, _, gate, tag = _experiment(s, "exp-failing", spec={
        "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
        "pass_all": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": ">", "value": 0}],
        "fail_any": [{"metric": "pnl_cents_per_trade", "arm": "treatment",
                      "op": "<=", "value": 0}],
    })
    for _ in range(10):
        _trade(s, tag, pnl=-0.30)
    s.commit()

    rep = ct.build_report(s, evaluate=True)
    assert "GATE FAIL (dry-run)" in next(r for r in rep.ready_due if "GATE FAIL" in r)

    evaluator.evaluate_gate(s, gate)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert "GATE FAIL (recorded)" in next(r for r in rep.ready_due if "GATE FAIL" in r)


# ---------------------------------------------------------------------------
# A realizable-driven PASS states the claim it actually makes
# ---------------------------------------------------------------------------


REALIZABLE_SPEC = {
    "sample": {"treatment": {"metric": "settled_trades", "op": ">=", "value": 5}},
    "pass_all": [{"metric": "realizable_cents_per_trade", "arm": "treatment",
                  "op": ">", "value": 0}],
}


def _priced_trade(s, tag, *, side="yes", price=6, pnl, at=None):
    s.add(PaperTrade(
        market_ticker=f"T-{tag}-{id(object())}", strategy=tag, status="settled",
        side=side, assumed_price=price, pnl=pnl, quantity=1,
        created_at=at or (T0 + timedelta(hours=1)),
    ))


def _sign_disagreeing_experiment(s, key="exp-signgap"):
    """The mmsell-price-ceiling shape: entries in the cheap 6c cell, whose measured
    realizable is +1.77c, while the book's OWN paper result over the window is
    negative. Projection positive, observation negative, 100% covered."""
    exp, ver, epoch, gate, tag = _experiment(s, key, spec=REALIZABLE_SPEC)
    for _ in range(20):
        _priced_trade(s, tag, side="yes", price=6, pnl=-0.02)
    s.commit()
    return exp, gate, tag


def test_a_realizable_pass_shows_projection_observation_and_coverage(xos_session,
                                                                     xos_platform):
    from kalshi_bot import fill_calibration as fc
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    _, gate, _ = _sign_disagreeing_experiment(s)
    out = evaluator.evaluate_gate(s, gate)  # record it
    s.commit()
    assert out.verdict == "PASS"

    rep = ct.build_report(s, evaluate=False)
    assert len(rep.realizable_context) == 1
    ctx = rep.realizable_context[0]
    assert ctx["source"] == "recorded" and ctx["verdict"] == "PASS"
    row = ctx["rows"][0]
    assert row["projected_cents"] == fc.MAKER_FILL_CALIBRATION[6].realizable_cents
    assert row["observed_paper_cents"] == -2.0        # the book's own number
    assert (row["covered_n"], row["total_n"]) == (20, 20)
    assert row["calibration_version"] == fc.CALIBRATION_VERSION
    assert ctx["any_sign_disagreement"] is True

    out_text = ct.render(rep)
    assert "=== REALIZABLE-PROJECTION CONTEXT ===" in out_text
    assert "realizable projection +1.770c/trade" in out_text
    assert "observed paper -2.000c/trade" in out_text
    assert "calibration coverage 20/20" in out_text  # counts, not just a percentage
    assert "NOT a claim that observed paper P&L was positive" in out_text
    assert "CAUTION: calibrated realizable projection and observed paper P&L "\
           "disagree in sign" in out_text


def test_the_sign_caution_never_changes_the_verdict(xos_session, xos_platform):
    """Informational only. A pre-registered gate is not re-decided because its
    result is uncomfortable to read."""
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    _, gate, _ = _sign_disagreeing_experiment(s)
    evaluator.evaluate_gate(s, gate)
    s.commit()

    rep = ct.build_report(s, evaluate=True)
    assert rep.realizable_context[0]["any_sign_disagreement"] is True
    # The verdict everywhere is still PASS — not HOLD, FAIL or BLOCKED_*.
    assert rep.blocked == []
    view = next(v for vs in rep.by_state.values() for v in vs
                if v["key"] == "exp-signgap")
    assert ct._verdict_of(view) == "PASS"
    assert (view["gates"][0]["latest_result"]["verdict"]) == "PASS"


def test_the_pass_line_states_the_exact_claim(xos_session, xos_platform):
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    _, gate, _ = _sign_disagreeing_experiment(s)
    evaluator.evaluate_gate(s, gate)
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    line = next(r for r in rep.ready_due if "GATE PASS" in r)
    assert "RECORDED" in line                      # still authoritative
    assert "not that the book is broadly profitable" in line
    assert "historical cautions have lapsed" in line
    assert "CAUTION" in line and "observed paper P&L is negative" in line


def test_agreeing_signs_get_context_without_the_caution(xos_session, xos_platform):
    from kalshi_bot.experiment_os import evaluator

    s = xos_session
    _, _, _, gate, tag = _experiment(s, "exp-agree", spec=REALIZABLE_SPEC)
    for _ in range(20):
        _priced_trade(s, tag, side="yes", price=6, pnl=0.03)
    s.commit()
    evaluator.evaluate_gate(s, gate)
    s.commit()
    rep = ct.build_report(s, evaluate=False)
    ctx = next(c for c in rep.realizable_context if c["experiment"] == "exp-agree")
    assert ctx["any_sign_disagreement"] is False
    text = ct.render(rep)
    assert "NOT a claim that observed paper P&L was positive" in text
    assert "disagree in sign" not in text


def test_an_unmeasured_realizable_clause_says_so_rather_than_showing_a_number(
    xos_session, xos_platform
):
    s = xos_session
    _experiment(s, "exp-uncov", spec=REALIZABLE_SPEC)
    for _ in range(20):
        _priced_trade(s, "exp-uncov-t", side="yes", price=40, pnl=0.20)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    ctx = next(c for c in rep.realizable_context if c["experiment"] == "exp-uncov")
    assert ctx["rows"][0]["missing"] is True
    assert "realizable UNMEASURED" in ct.render(rep)


def test_a_gate_that_does_not_use_realizable_gets_no_context_block(xos_session,
                                                                   xos_platform):
    s = xos_session
    _experiment(s, "exp-plain", spec=COMPUTABLE_SPEC)
    _trade(s, "exp-plain-t")
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    assert rep.realizable_context == []
    assert "REALIZABLE-PROJECTION CONTEXT" not in ct.render(rep)
