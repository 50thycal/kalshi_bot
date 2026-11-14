"""The S2 live test's frozen rules (`kalshi_bot/liquidity_incentive/s2_live.py`): selection from
the paper arm's open S2-best rows, the bid, the stops and the read bars. Also asserts the module
is NOT wired into the worker, which is what makes merging it unable to change live behaviour."""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.liquidity_incentive import live as limm
from kalshi_bot.liquidity_incentive import paper_arms as pa
from kalshi_bot.liquidity_incentive import s2_live as s2

ROOT = pathlib.Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 25, tzinfo=timezone.utc)


def row(t, ev=None, rate=0.01, price=4, side="no", hours=200.0, series="KXAAAGASM"):
    return s2.PaperS2Row(ticker=t, event=ev or t.rsplit("-", 1)[0], series=series, side=side,
                         price=price, rate_per_hour_usd=rate, hours_to_step_aside=hours)


# ------------------------------------------------------------------ the envelope


def test_the_envelope_is_what_the_owner_approved():
    r = s2.DEFAULT_RULES
    assert (r.max_markets, r.max_per_event, r.bid_usd, r.max_resting_usd) == (5, 1, 10.0, 50.0)
    assert r.max_markets * r.bid_usd <= r.max_resting_usd
    assert r.duration_days == 14.0
    assert r.fill_loss_stop_usd == 30.0 and r.worst_case_loss_usd == 80.0
    assert r.min_projected_usd >= 1.0                  # Kalshi's $1 minimum, plus a margin
    assert not r.reenter_after_fill and r.hold_fills_to_settlement


def test_it_copies_the_paper_arm_and_never_retypes_its_numbers():
    assert s2.SOURCE_ARM == pa.ARM_S2_BEST
    r, p = s2.DEFAULT_RULES, pa.DEFAULT_RULES
    assert (r.cheap_max_cents, r.step_aside_hours, r.calibration) == (
        p.cheap_max_cents, p.step_aside_hours, p.calibration)
    assert r.max_contracts == p.qty_cap(pa.ARM_S2_BEST)


def test_tags_are_fresh_prefix_safe_and_not_alimm1s():
    tags = (s2.PAPER_TAG, s2.LIVE_TAG, s2.TWIN_TAG)
    assert len(set(tags)) == 3 and max(map(len, tags)) <= 24
    for a in (s2.LIVE_TAG, s2.PAPER_TAG):
        for b in (limm.LIVE_TAG, limm.PAPER_TAG, limm.TWIN_TAG):
            assert not a.startswith(b) and not b.startswith(a)
    assert not s2.LIVE_TAG.startswith(s2.PAPER_TAG) and not s2.PAPER_TAG.startswith(s2.LIVE_TAG)
    assert s2.TWIN_TAG.startswith(s2.LIVE_TAG)
    # Alimm1's exit-skip must not swallow this book (it has its own: hold to settlement).
    assert not limm.owns_tag(s2.LIVE_TAG) and not limm.owns_tag(s2.TWIN_TAG)


def test_the_owners_youtube_pause_still_binds():
    assert limm.ENTRY_PAUSED_SERIES <= s2.EXCLUDED_SERIES


def test_only_the_s2_runner_executor_and_package_use_the_rules():
    """The rules reach trading code through exactly three places: the S2 runner, the executor's
    gated `place_s2_bid` / tag helper, and the Experiment OS package. Anything else importing
    them would be a path nobody reviewed for this book."""
    import re

    allowed = {"s2_live.py", "s2_runner.py", "limm_s2_live.py", "executor.py",
               "experiment_commands.py", "main.py", "config.py"}
    pat = re.compile(r"\bs2_(live|runner)\b")
    for path in (ROOT / "kalshi_bot").rglob("*.py"):
        if path.name in allowed:
            continue
        assert not pat.search(path.read_text()), f"{path} references the S2 live rules"


# ------------------------------------------------------------------ parsing and the bid


def test_parse_arm_reason():
    assert s2.parse_arm_reason("S2-best:yes@4x250") == ("S2-best", "yes", 4, 250)
    assert s2.parse_arm_reason("S2-best:no@1x1000") == ("S2-best", "no", 1, 1000)
    for bad in (None, "", "edge_ok", "S2-best:maybe@4x2", "S2-best:yes@ax2"):
        assert s2.parse_arm_reason(bad) is None


@pytest.mark.parametrize("price,qty,cost", [(1, 1000, 10.0), (3, 333, 9.99), (4, 250, 10.0),
                                            (10, 100, 10.0)])
def test_bid_is_ten_dollars_at_the_paper_price(price, qty, cost):
    b = s2.bid_for(row("KXAAAGASM-26NOV30-3.10", price=price))
    assert (b.price, b.contracts, b.cost_usd) == (price, qty, cost)


@pytest.mark.parametrize("price", [0, 11, 50])
def test_no_bid_off_the_cheap_side(price):
    assert s2.bid_for(row("X-A-1", price=price)) is None


# ------------------------------------------------------------------ selection


def test_top_five_by_rate_one_per_event():
    rows = [row(f"E{e}-M-{k}", ev=f"E{e}", rate=0.01 * (10 - e) + 0.001 * k)
            for e in range(8) for k in range(3)]
    sel = s2.select_live(rows)
    assert len(sel.bids) == 5
    assert len({b.event for b in sel.bids}) == 5
    assert [b.event for b in sel.bids] == ["E0", "E1", "E2", "E3", "E4"]
    assert all(b.ticker.endswith("-2") for b in sel.bids)          # best market of each event
    assert sum(b.cost_usd for b in sel.bids) <= 50.0
    assert sel.skipped["E0-M-1"] == s2.SKIP_EVENT_TAKEN


def test_payout_floor_respects_the_one_dollar_minimum():
    # 0.001/h x 100h x 1.34 = $0.134 < $1.50: never entered
    sel = s2.select_live([row("A-B-1", rate=0.001, hours=100.0)])
    assert not sel.bids and sel.skipped["A-B-1"] == s2.SKIP_UNDER_FLOOR
    # exactly at the floor is enough
    r = row("A-B-2", rate=1.50 / (100.0 * 1.34), hours=100.0)
    assert s2.select_live([r]).bids


def test_excluded_held_and_filled_markets_are_never_taken():
    rows = [row("KXYTVIEWSHIGH-X-1", series="KXYTVIEWSHIGH", rate=1.0),
            row("H-H-1", rate=0.9), row("F-F-1", rate=0.8), row("OK-O-1", rate=0.1)]
    sel = s2.select_live(rows, account_held={"H-H-1"}, filled_before={"F-F-1"})
    assert [b.ticker for b in sel.bids] == ["OK-O-1"]
    assert sel.skipped == {"KXYTVIEWSHIGH-X-1": s2.SKIP_EXCLUDED, "H-H-1": s2.SKIP_HELD,
                           "F-F-1": s2.SKIP_FILLED_BEFORE}


def test_selection_is_sticky_and_drops_what_the_paper_arm_left():
    rows = [row(f"E{e}-M-1", ev=f"E{e}", rate=0.01 * e) for e in range(1, 8)]
    current = {"E1-M-1", "E2-M-1", "GONE-M-1"}
    sel = s2.select_live(rows, current=current)
    tickers = {b.ticker for b in sel.bids}
    # the two low-rate incumbents stay; a better market never evicts them
    assert {"E1-M-1", "E2-M-1"} <= tickers and len(tickers) == 5
    assert {"E7-M-1", "E6-M-1", "E5-M-1"} <= tickers
    assert sel.dropped == ["GONE-M-1"]


def test_a_held_fill_keeps_its_slot_and_its_event():
    rows = [row(f"E{e}-M-{k}", ev=f"E{e}", rate=0.01 * (10 - e) + 0.001 * k)
            for e in range(8) for k in range(2)]
    held = {"E0-M-1": ("E0", 10.0), "E1-M-1": ("E1", 10.0)}
    sel = s2.select_live(rows, filled_before=set(held), held_fills=held)
    assert len(sel.bids) == 3
    assert {b.event for b in sel.bids} == {"E2", "E3", "E4"}       # E0, E1 still taken
    assert sum(b.cost_usd for b in sel.bids) + 20.0 <= 50.0


def test_a_held_incumbent_is_dropped():
    sel = s2.select_live([row("A-A-1", rate=0.05)], current={"A-A-1"}, account_held={"A-A-1"})
    assert not sel.bids and sel.dropped == ["A-A-1"]


# ------------------------------------------------------------------ stops


def test_stops():
    assert s2.stop_reason(fill_loss_usd=29.99, started_at=T0, now=T0 + timedelta(days=3)) is None
    assert s2.stop_reason(fill_loss_usd=30.0, started_at=T0, now=T0) == s2.STOP_LOSS
    assert s2.stop_reason(fill_loss_usd=0, started_at=T0,
                          now=T0 + timedelta(days=14)) == s2.STOP_DURATION


# ------------------------------------------------------------------ the read


def labels(**kw):
    base = dict(paid_usd=60.0, fill_loss_usd=20.0, estimated_uncalibrated_usd=45.0,
                bid_days=70.0, programmes=8)
    return s2.read_labels(**(base | kw))


def test_read_scale_candidate():
    out = labels()
    assert out["net_usd"] == 40.0 and out["ratio_label"] == s2.RATIO_LABEL_HOLDS
    assert out["primary"] == s2.LABEL_SCALE


def test_read_keep_when_net_positive_but_ratio_weak_or_net_small():
    assert labels(estimated_uncalibrated_usd=90.0)["primary"] == s2.LABEL_KEEP   # ratio 0.67
    assert labels(paid_usd=30.0, fill_loss_usd=15.0)["primary"] == s2.LABEL_KEEP  # net $15


def test_read_stop_on_loss_or_stop_fired():
    assert labels(paid_usd=10.0)["primary"] == s2.LABEL_STOP
    assert labels(stopped_on_loss=True)["primary"] == s2.LABEL_STOP


def test_read_extend_under_floors():
    assert labels(bid_days=39.0)["primary"] == s2.LABEL_EXTEND
    assert labels(programmes=3)["primary"] == s2.LABEL_EXTEND


def test_ratio_labels():
    assert s2.ratio_label(None) == s2.RATIO_LABEL_NONE
    assert s2.ratio_label(1.34) == s2.RATIO_LABEL_HOLDS
    assert s2.ratio_label(0.7) == s2.RATIO_LABEL_WEAK
    assert s2.ratio_label(0.2) == s2.RATIO_LABEL_FAILS
    assert s2.paid_ratio(5.0, 0.0) is None
