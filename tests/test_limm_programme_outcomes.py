"""scripts/limm_programme_outcomes.py — pins the pre-registered live-rule pricing, the dashboard
reward estimator with its thin-book cut, the settlement score and the series labels
(docs/LIMM_PROGRAMME_OUTCOMES.md) without the DB or the network."""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_programme_outcomes",
                                               _SCRIPTS / "limm_programme_outcomes.py")
lo = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lo)  # type: ignore[union-attr]

UTC = dt.timezone.utc


def test_cheap_side_is_p1():
    assert lo.cheap_side(3, 90) == ("yes", 3)
    assert lo.cheap_side(90, 4) == ("no", 4)
    assert lo.cheap_side(5, 5) == ("yes", 5)
    assert lo.cheap_side(11, 50) is None
    assert lo.cheap_side(0, None) is None


def test_live_qty_follows_the_size_up_rule():
    assert lo.live_qty(1) == 500      # $10 / 1c = 1000, capped at 500
    assert lo.live_qty(3) == 333
    assert lo.live_qty(4) == 500      # $20 / 4c
    assert lo.live_qty(10) == 200


def test_hourly_reward_is_the_dashboard_estimator():
    start = dt.datetime(2026, 10, 1, tzinfo=UTC)
    end = start + dt.timedelta(days=4)
    usd, share = lo.hourly_reward(96.0, start, end, 100, 300)   # $1/h pool, 25% share
    assert math.isclose(share, 0.25) and math.isclose(usd, 0.125)
    assert lo.hourly_reward(None, start, end, 100, 300) is None
    assert lo.hourly_reward(96.0, end, start, 100, 300) is None


def test_qualifies_needs_targets_activity_and_step_aside():
    start = dt.datetime(2026, 10, 1, tzinfo=UTC)
    base = {"hr": start + dt.timedelta(hours=1), "start": start,
            "end": start + dt.timedelta(days=5), "close": start + dt.timedelta(days=10),
            "yes_meets": True, "no_meets": True, "itype": "liquidity"}
    assert lo.qualifies(base)
    assert not lo.qualifies({**base, "no_meets": False})
    assert not lo.qualifies({**base, "itype": "volume"})
    assert not lo.qualifies({**base, "close": base["hr"] + dt.timedelta(hours=47)})
    assert not lo.qualifies({**base, "hr": start - dt.timedelta(hours=1)})


def test_live_universe_and_majority_side():
    assert lo.in_live_universe("KXTRUMPMENTION") and lo.in_live_universe("KXYTVIEWSHIGH")
    assert not lo.in_live_universe("KXCPI")
    assert lo.market_cheap_side([("yes", 4), ("yes", 6), ("no", 2)]) == ("yes", 5.0)


def _g(n, events, mean):
    return {"n": n, "events": events, "mean": mean, "se": 0.5}


def test_labels():
    assert lo.label(19, 5.0, _g(500, 100, 0.0)) == "THIN"
    assert lo.label(30, 0.6, _g(150, 40, 0.0)) == "ADD-CANDIDATE"
    assert lo.label(30, 0.6, _g(150, 40, -3.0)) == "COSTLY-REWARD"
    assert lo.label(30, 0.6, _g(50, 40, 0.0)) == "REWARD-ONLY"
    assert lo.label(30, 0.1, _g(150, 40, -3.0)) == "AVOID"
    assert lo.label(30, 0.1, _g(150, 40, 0.5)) == "NO-CALL"


def test_cluster_mean_groups_by_event():
    a = lo.cluster_mean([("e1", 1.0), ("e1", 1.0), ("e2", -1.0), ("e2", -1.0)])
    assert a["events"] == 2 and a["mean"] == 0.0


def test_allowlisted_on_the_ops_runner():
    import ops_runner

    assert "limm_programme_outcomes" in ops_runner.ALLOWED_SCRIPTS


def test_reward_only_label():
    assert lo.label_reward(19, 5.0) == "THIN"
    assert lo.label_reward(20, 0.5) == "REWARD-LEAD"
    assert lo.label_reward(20, 0.49) == "NO-LEAD"
