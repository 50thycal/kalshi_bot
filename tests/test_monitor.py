"""WS-026 global monitor: the snapshot contract, the health labels, the expect
contract and the page.

The acceptance checks this file pins:
  1. every ACTIVE experiment and every registered external source is listed;
  2. a deliberately silent paper arm is SILENT and a blocked gate is BLOCKED;
  3. the summary numbers are the Control Tower's for the same report;
  4. the top table is driven by one SUMMARY list in the page;
  plus: a book that traded and then stopped is STALE (the case the Tower's
  zero-evidence detector cannot see), packages declare `expect`, and the
  notify decision repeats what is broken but not what is chronic.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timedelta, timezone

from kalshi_bot.experiment_os import control_tower as ct
from kalshi_bot.monitor import expectations, page, snapshot, sources
from tests.test_experiment_os_silent_arm import (
    UNPROVIDED_SPEC,
    _live_canary_book,
    _live_order,
    _trade,
    _two_arm_paper_book,
)

UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(UTC)


def _snap(s, external=None):
    """Build through the real read path, with external probes stubbed so a test
    never touches the network."""
    rep = ct.build_report(s, evaluate=True)
    tower = json.loads(json.dumps(dataclasses.asdict(rep), default=str))
    extras = snapshot._read_extras(s, rep, rep.generated_at)
    return snapshot.assemble(tower, extras, external or [], rep.generated_at), rep


def _row(snap, key):
    return next(e for e in snap["experiments"] if e["key"] == key)


# ---------------------------------------------------------------------------
# 1. Coverage
# ---------------------------------------------------------------------------


def test_every_active_experiment_is_listed(xos_session, xos_platform):
    s = xos_session
    _two_arm_paper_book(s, "book-a", armed_hours_ago=30)
    _two_arm_paper_book(s, "book-b", armed_hours_ago=2)
    _live_canary_book(s, "canary-c", armed_hours_ago=30)
    snap, rep = _snap(s)
    tower_keys = {v["key"] for views in rep.by_state.values() for v in views}
    assert tower_keys == {"book-a", "book-b", "canary-c"}
    missing = tower_keys - {e["key"] for e in snap["experiments"]}
    assert not missing, f"active experiments missing from the snapshot: {missing}"
    assert snap["schema"] == snapshot.SCHEMA


def test_every_registered_external_source_is_listed(xos_session, xos_platform):
    """Probes run for real against the sqlite session: tables absent there read
    UNKNOWN, a missing desk token reads UNKNOWN — never a silent omission and
    never a guessed OK."""
    s = xos_session
    rows = sources.run_probes(s, _now(), environ={})
    assert [r["key"] for r in rows] == [src.key for src in sources.SOURCES]
    desks = next(r for r in rows if r["key"] == "desks")
    assert desks["health"] == sources.UNKNOWN
    snap, _ = _snap(s, external=rows)
    listed = {x["key"] for x in snap["external"]}
    assert {src.key for src in sources.SOURCES} <= listed
    # The Tower's collectors are carried too, as external rows.
    assert any(k.startswith("collector:") for k in listed)


def test_worker_probe_reads_bot_runs(xos_session, xos_platform):
    from kalshi_bot.models import BotRun

    s = xos_session
    s.add(BotRun(mode="live", status="ok", started_at=_now() - timedelta(minutes=5)))
    s.commit()
    src = next(x for x in sources.SOURCES if x.key == "worker")
    # sqlite has no `interval`; the probe must degrade to UNKNOWN, not raise.
    row = sources.run_probes(s, _now(), environ={}, sources=(src,))[0]
    assert row["health"] in (sources.OK, sources.UNKNOWN)


def test_freshness_rule():
    now = _now()
    ok = sources._freshness(now, now - timedelta(minutes=10), 0.5, what="cycle")
    stale = sources._freshness(now, now - timedelta(hours=2), 0.5, what="cycle")
    never = sources._freshness(now, None, 0.5, what="cycle")
    assert (ok["health"], stale["health"], never["health"]) == ("OK", "STALE", "ERROR")
    # A row stamped a moment after `now` (clock skew) is "just now", never negative.
    ahead = sources._freshness(now, now + timedelta(minutes=5), 0.5, what="cycle")
    assert ahead["health"] == "OK" and ahead["age_hours"] == 0.0


# ---------------------------------------------------------------------------
# 2. Health labels
# ---------------------------------------------------------------------------


def test_a_silent_paper_arm_is_silent(xos_session, xos_platform):
    """The 2026-09-05 shape: armed 30h, zero rows, while the platform trades."""
    s = xos_session
    _two_arm_paper_book(s, "silent-book", armed_hours_ago=30)
    _trade(s, "mmsell10", _now() - timedelta(hours=20), n=50)
    s.commit()
    snap, _ = _snap(s)
    row = _row(snap, "silent-book")
    assert row["health"] == snapshot.SILENT
    assert any("zero evidence" in r for r in row["health_reasons"])


def test_a_blocked_gate_is_blocked(xos_session, xos_platform):
    s = xos_session
    book = _two_arm_paper_book(s, "blocked-book", armed_hours_ago=2,
                               spec=UNPROVIDED_SPEC)
    _trade(s, book["control_tag"], _now() - timedelta(hours=1), n=3)
    _trade(s, book["treatment_tag"], _now() - timedelta(hours=1), n=3)
    s.commit()
    snap, rep = _snap(s)
    assert any(b["experiment"] == "blocked-book" for b in rep.blocked)
    assert _row(snap, "blocked-book")["health"] == snapshot.BLOCKED


def test_a_book_that_stopped_trading_is_stale(xos_session, xos_platform):
    """The case the Tower cannot see: the book HAS evidence, so it is not at
    zero, but nothing new arrived for longer than its declared silence."""
    s = xos_session
    book = _two_arm_paper_book(s, "stopped-book", armed_hours_ago=96)
    for tag in (book["control_tag"], book["treatment_tag"]):
        _trade(s, tag, _now() - timedelta(hours=60), n=5)
    s.commit()
    snap, rep = _snap(s)
    assert not [f for f in rep.silent_arms if f["experiment"] == "stopped-book"]
    row = _row(snap, "stopped-book")
    assert row["health"] == snapshot.STALE
    assert row["entries_7d"] == 10 and row["entries_24h"] == 0


def test_a_trading_book_is_ok_and_counts_entries(xos_session, xos_platform):
    s = xos_session
    book = _two_arm_paper_book(s, "busy-book", armed_hours_ago=30)
    for tag in (book["control_tag"], book["treatment_tag"]):
        _trade(s, tag, _now() - timedelta(hours=1), n=4)
        _trade(s, tag, _now() - timedelta(days=3), n=2)
    s.commit()
    snap, _ = _snap(s)
    row = _row(snap, "busy-book")
    assert row["health"] == snapshot.OK
    assert (row["entries_24h"], row["entries_7d"]) == (8, 12)
    assert row["last_evidence_at"] is not None
    assert row["expect"]["max_silence_hours"] == ct.SILENT_ARM_HOURS
    assert row["expect_source"] == "default"


def test_a_declared_slower_expectation_is_honoured(xos_session, xos_platform):
    s = xos_session
    book = _two_arm_paper_book(s, "slow-book", armed_hours_ago=96)
    for tag in (book["control_tag"], book["treatment_tag"]):
        _trade(s, tag, _now() - timedelta(hours=36), n=2)
    s.commit()
    rep = ct.build_report(s, evaluate=True)
    tower = json.loads(json.dumps(dataclasses.asdict(rep), default=str))
    extras = snapshot._read_extras(s, rep, rep.generated_at)
    assert snapshot.assemble(tower, extras, [], rep.generated_at)["experiments"][0][
        "health"] == snapshot.STALE
    extras["experiments"]["slow-book"]["expect"] = {"max_silence_hours": 48}
    assert _row(snapshot.assemble(tower, extras, [], rep.generated_at),
                "slow-book")["health"] == snapshot.OK


def test_a_live_canary_reports_live_entries(xos_session, xos_platform):
    s = xos_session
    book = _live_canary_book(s, "canary-x", armed_hours_ago=30)
    _live_order(s, book["live_tags"][0], _now() - timedelta(hours=2), n=3)
    _trade(s, book["twin_tags"][0], _now() - timedelta(hours=2), n=3)
    s.commit()
    snap, _ = _snap(s)
    row = _row(snap, "canary-x")
    assert row["kind"] == "live" and row["owner_role"] == "LIVE_OPS"
    live = next(a for a in row["arms"] if a["kind"] == "live")
    assert live["entries_24h"] == 3
    assert snap["summary"]["live"] == 1
    # The canonical provider answered (no settled positions → zero, not missing).
    assert row["live_realized_30d_usd"] == 0.0


# ---------------------------------------------------------------------------
# 3. The summary is the Control Tower's
# ---------------------------------------------------------------------------


def test_summary_numbers_match_the_control_tower(xos_session, xos_platform):
    s = xos_session
    _two_arm_paper_book(s, "ok-book", armed_hours_ago=30)
    _two_arm_paper_book(s, "blocked-book", armed_hours_ago=30, spec=UNPROVIDED_SPEC)
    _trade(s, "ok-book-c", _now() - timedelta(hours=1), n=5)
    _trade(s, "ok-book-t", _now() - timedelta(hours=1), n=5)
    s.commit()
    snap, rep = _snap(s)
    summ = snap["summary"]
    non_paused = sum(len(v) for st, v in rep.by_state.items() if st != "PAUSED")
    assert summ["active"] == non_paused
    assert summ["blocked"] == len({b["experiment"] for b in rep.blocked})
    assert summ["blocked_gates"] == len(rep.blocked)
    assert summ["closed_recent"] == len(rep.retired_recent)
    assert summ["realized"]["paper_30d_usd"] == rep.portfolio["paper_realized_30d_usd"]
    assert summ["realized"]["tower_gap_paper_basis_usd"] == \
        rep.portfolio["gap_to_north_star_usd"]
    assert summ["realized"]["north_star_usd_per_month"] == ct.NORTH_STAR_USD_PER_MONTH


def test_assemble_is_pure_over_a_saved_tower_report(xos_session, xos_platform):
    """A bootstrap (or a test) can feed a saved `control-tower --json` result."""
    s = xos_session
    _two_arm_paper_book(s, "book", armed_hours_ago=30)
    rep = ct.build_report(s, evaluate=True)
    tower = json.loads(json.dumps(dataclasses.asdict(rep), default=str))
    snap = snapshot.assemble(tower, None, None, str(rep.generated_at))
    assert {e["key"] for e in snap["experiments"]} == {"book"}
    json.dumps(snap)  # serializable as-is


def test_an_already_applied_promotion_is_not_a_decision():
    line = ("GATE PASS (RECORDED, system @ x) canary-x · paper_to_live_canary "
            "PAPER→LIVE_CANARY — this result CAN authorize the transition.")
    assert snapshot._already_there(line, {"canary-x": "LIVE_CANARY"})
    assert not snapshot._already_there(line, {"canary-x": "PAPER"})


# ---------------------------------------------------------------------------
# The expect contract
# ---------------------------------------------------------------------------


def test_every_experiment_package_declares_a_valid_expect():
    """A new package that registers an experiment must say what 'running' means
    for it. Repair and re-cut packages act on someone else's experiment."""
    from kalshi_bot.experiment_os.experiment_commands import _packages

    declared = expectations.package_expectations()
    missing, invalid = [], {}
    for name, pkg in _packages().items():
        if not expectations.creates_experiment(pkg):
            continue
        expect = declared.get(pkg.experiment_key)
        if expect is None:
            missing.append(name)
        elif expectations.validate_expect(expect):
            invalid[name] = expectations.validate_expect(expect)
    assert not missing, f"packages without MONITOR_EXPECT: {missing}"
    assert not invalid, invalid


def test_expect_resolution_order():
    pkgs = {"k": {"max_silence_hours": 48}}
    assert expectations.resolve_expect("k", {"expect": {"max_silence_hours": 6}}, pkgs) \
        == ({"max_silence_hours": 6}, "version")
    assert expectations.resolve_expect("k", {"silent_arm": "x"}, pkgs) \
        == ({"max_silence_hours": 48}, "package")
    assert expectations.resolve_expect("other", None, pkgs)[1] == "default"
    # An invalid declaration never wins over a valid fallback.
    assert expectations.resolve_expect("k", {"expect": {"bogus": 1}}, pkgs)[1] == "package"


def test_validate_expect_refuses_unknown_and_bad_values():
    assert expectations.validate_expect({"max_silence_hours": 24}) == []
    assert expectations.validate_expect({"max_silence_hours": 0})
    assert expectations.validate_expect({"min_entries_per_day": -1})
    assert expectations.validate_expect({"silence": 3})
    assert expectations.validate_expect("24h")


# ---------------------------------------------------------------------------
# 4. The page
# ---------------------------------------------------------------------------


def _summary_block(tpl: str) -> str:
    return re.search(r"const SUMMARY = \[(.*?)\n\];", tpl, re.S).group(1)


def test_the_top_table_is_one_config_list_at_the_top_of_the_page():
    tpl = page.TEMPLATE.read_text()
    assert tpl.index("const SUMMARY") < tpl.index("<style>"), \
        "the config block must be the first thing an editor sees"
    block = _summary_block(tpl)
    labels = re.findall(r'label: "([^"]+)"', block)
    for want in ("Active", "Blocked", "Closed", "Live $ / 30d", "Paper $ / 30d",
                 "Goal: $100/mo", "Needs you"):
        assert want in labels
    # Rows are drawn from the list and nowhere else: no label is hard-coded in
    # the markup or the drawing code.
    rest = tpl.replace(block, "")
    for label in labels:
        assert f">{label}<" not in rest


def test_changing_one_summary_entry_changes_only_the_config():
    tpl = page.TEMPLATE.read_text()
    edited = tpl.replace('label: "Closed"', 'label: "Retired"', 1)
    assert edited != tpl
    assert _summary_block(edited) != _summary_block(tpl)
    assert edited.replace(_summary_block(edited), "") == tpl.replace(_summary_block(tpl), "")


def test_render_embeds_and_round_trips_the_snapshot():
    snap = {"schema": snapshot.SCHEMA, "generated_at": "2026-10-15 12:00:00+00:00",
            "note": "a </script> inside a string must not end the tag"}
    html = page.render(snap)
    assert page.PLACEHOLDER not in html
    assert "</script> inside" not in html
    assert page.extract_embedded(html) == snap


def test_load_snapshot_reads_an_ops_result_file():
    body = json.dumps({"schema": snapshot.SCHEMA, "summary": {}})
    text = "# ops request [READ]\n# type=xos id=mon-1\n" + body + "\n"
    assert page.load_snapshot(text)["schema"] == snapshot.SCHEMA


def _mini(health="OK", needs=(), fps=(), issues=(), ext="OK"):
    return {
        "generated_at": "t",
        "experiments": [{"key": "b", "health": health, "health_reasons": ["why"],
                         "next_verdict": None}],
        "external": [{"key": "worker", "title": "Worker", "health": ext}],
        "needs_you": list(needs),
        "issues": [{"issue_key": k, "title": k} for k in issues],
        "untracked": [{"fingerprint": f, "title": f} for f in fps],
    }


def test_broken_things_notify_every_day():
    prev = _mini(health="STALE")
    assert page.attention(_mini(health="STALE"), prev)["notify"]
    assert page.attention(_mini(ext="ERROR"), _mini(ext="ERROR"))["notify"]


def test_chronic_conditions_stay_quiet_and_new_ones_notify():
    chronic = _mini(health="BLOCKED", needs=["decide X"], fps=["f1"], issues=["XOS-1"])
    assert not page.attention(chronic, chronic)["notify"]
    assert page.attention(_mini(health="BLOCKED"), _mini())["notify"]
    assert page.attention(_mini(needs=["decide Y"]), _mini())["notify"]
    assert page.attention(_mini(fps=["f2"]), _mini())["notify"]
    assert page.attention(_mini(issues=["XOS-2"]), _mini())["notify"]
    assert not page.attention(_mini(), _mini())["notify"]
