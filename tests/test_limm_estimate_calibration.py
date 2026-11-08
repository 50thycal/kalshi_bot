"""The reward-estimate calibration script (`scripts/limm_estimate_calibration.py`): pure parts."""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import limm_estimate_calibration as cal  # noqa: E402

from kalshi_bot.liquidity_incentive import runner  # noqa: E402

T0 = dt.datetime(2026, 10, 20, 12, tzinfo=dt.timezone.utc)
START, END = T0 - dt.timedelta(days=2), T0 + dt.timedelta(days=5)
PROGRAM = (70.0, START, END, 300.0, 5000)          # reward $, start, end, target, discount bps
YES = [[8, 400.0], [7, 900.0], [5, 2000.0]]
NO = [[90, 1500.0], [89, 800.0]]


def _snap(at, yes=YES, no=NO):
    return {"at": at, "yes": yes, "no": no}


def test_rate_matches_the_runner_estimate_for_a_held_bid():
    """Same function, same inputs: the script's rate equals `runner.est_reward_per_hour` with the
    bid passed as the book's own order, so the calibration measures the live estimate."""
    scoring = cal.load_scoring()
    prog_row = SimpleNamespace(
        market_ticker="KXTRUMPMENTION-26OCT27-SAVE", id=1, event_ticker="KXTRUMPMENTION-26OCT27",
        target_size=300.0, discount_factor_bps=5000, end_date=END, start_date=START,
        close_time=END, period_reward_usd=70.0, market_title="", series_ticker="KXTRUMPMENTION")
    book = {"orderbook": {"yes": YES, "no": NO}}
    c = runner.candidate_from_book(prog_row, book, now=T0)
    want = runner.est_reward_per_hour(c, yes_price=7, no_price=None, qty=150.0,
                                      own_yes={7: 150.0})
    got = cal.rate_for(scoring, _snap(T0), PROGRAM, "yes", 7, 150.0)
    assert want > 0
    assert abs(got - want) < 1e-9


def test_summary_integrates_rate_over_resting_time_and_compares_to_payouts():
    scoring = cal.load_scoring()
    t = "KXTRUMPMENTION-26OCT27-SAVE"
    snaps = {t: [_snap(T0 + dt.timedelta(minutes=10 * i)) for i in range(7)]}
    programs = {t: [(START,) + PROGRAM]}
    end = T0 + dt.timedelta(hours=1)
    orders = [(1, t, T0, "canceled", "yes", 7, 150, "c1", end, None)]
    rate = cal.rate_for(scoring, snaps[t][0], PROGRAM, "yes", 7, 150.0)
    s = cal.summarise(orders, programs, snaps, [("2026-10-28", 200, "KXTRUMPMENTION-26OCT27")],
                      scoring, now=end)
    ev = s["events"][0]
    assert ev["event"] == "KXTRUMPMENTION-26OCT27"
    assert abs(ev["est"] - round(rate, 2)) < 0.011          # one hour at a flat rate
    assert ev["covered_h"] == 1.0 and ev["rest_h"] == 1.0
    assert ev["paid"] == 2.0 and ev["paid_recorded"]


def test_a_long_snapshot_gap_is_uncovered_not_extrapolated():
    scoring = cal.load_scoring()
    t = "KXTRUMPMENTION-26OCT27-SAVE"
    snaps = {t: [_snap(T0)]}
    orders = [(1, t, T0, "canceled", "yes", 7, 150, "c1", T0 + dt.timedelta(hours=3), None)]
    s = cal.summarise(orders, {t: [(START,) + PROGRAM]}, snaps, [], scoring,
                      now=T0 + dt.timedelta(hours=3))
    ev = s["events"][0]
    assert ev["rest_h"] == 3.0
    assert ev["covered_h"] == cal.MAX_GAP_SECONDS / 3600.0


def test_allowlisted_and_imports_nothing_heavy():
    import ops_runner

    assert "limm_estimate_calibration" in ops_runner.ALLOWED_SCRIPTS
    src = ROOT / "scripts" / "limm_estimate_calibration.py"
    mods = set()
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    assert not mods & {"kalshi_bot", "sqlalchemy", "pydantic"}
