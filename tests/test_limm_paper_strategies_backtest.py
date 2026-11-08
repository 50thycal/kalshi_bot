"""scripts/limm_paper_strategies_backtest.py — pins the pre-registered windows and runs the
replay end to end on synthetic snapshots and prints (docs/LIMM_PAPER_STRATEGIES_BACKTEST.md)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
from collections import defaultdict

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_paper_strategies_backtest",
                                               _SCRIPTS / "limm_paper_strategies_backtest.py")
bt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bt)  # type: ignore[union-attr]

from kalshi_bot.liquidity_incentive import paper_arms as pa  # noqa: E402

UTC = dt.timezone.utc
H0 = dt.datetime(2026, 9, 20, 0, tzinfo=UTC)


def test_windows_match_the_preregistration():
    assert bt.SINCE == "2026-09-08T00:00:00+00:00"
    assert bt.RUN1_UNTIL == "2026-10-30T16:00:00+00:00"          # study 5's window opens
    assert bt.MAX_UNTIL == "2026-11-08T15:00:00+00:00"           # before the forward arms
    assert pa.ARM_S1 not in bt.ARMS and len(bt.ARMS) == 6
    early = dt.datetime(2026, 11, 10, tzinfo=UTC)
    late = dt.datetime(2026, 11, 15, tzinfo=UTC)
    assert bt.check_window(bt._aware(bt.RUN1_UNTIL), early) is None
    assert "study 5" in bt.check_window(bt._aware(bt.MAX_UNTIL), early)
    assert bt.check_window(bt._aware(bt.MAX_UNTIL), late) is None
    assert "forward arms" in bt.check_window(dt.datetime(2026, 11, 9, tzinfo=UTC), late)


def test_full_window_is_refused_before_study_5_reads(capsys, monkeypatch):
    class _Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 11, 9, tzinfo=UTC)

    monkeypatch.setattr(bt.dt, "datetime", _Now)
    assert bt.main(["--until", bt.MAX_UNTIL]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_reward_estimator_and_one_tick_behind():
    r, s = bt.side_reward(2.0, 500, 500)
    assert (r, s) == (0.5, 0.5)
    r2, s2 = bt.side_reward(2.0, 500, 500, mult=0.5)
    assert abs(s2 - 250 / 750) < 1e-12 and r2 < r


def _snap(tk, hr, *, yb=3, nb=95, fy=2000.0, fn=5000.0, start=H0 - dt.timedelta(hours=1),
          series=None, pool=240.0, days=10, depth=2000.0, close_days=30):
    end = start + dt.timedelta(days=days)
    return (tk, hr, yb, nb, fy, fn, True, True, [[yb, depth]], [[nb, 5000.0]], series or tk.split("-")[0],
            tk.rsplit("-", 1)[0], "liquidity", pool, start, end, start + dt.timedelta(days=close_days), 5000)


def test_end_to_end_replay_scores_through_the_read_script():
    snaps = []
    tickers = [f"KXE{i}-26-A" for i in range(12)] + ["KXWAAEROEMP-27FEB-T1", "KXAAAGASM-26-3.20"]
    for h in range(0, 48):
        hr = H0 + dt.timedelta(hours=h)
        for tk in tickers:
            series = "KXWAAEROEMP" if "EMP" in tk else ("KXAAAGASM" if "GAS" in tk else None)
            snaps.append(_snap(tk, hr, series=series))
    rows, books = bt.build_rows(snaps)
    tbh = defaultdict(lambda: defaultdict(list))
    hit = H0 + dt.timedelta(hours=3)
    tbh["KXE9-26-A"][hit].append((hit, 3, 2500, "no"))   # 500 past 2,000 ahead; KXE9 is selected
    quotes, outcomes, marks, hours = bt.replay(rows, books, tbh)
    arms = {q[1] for q in quotes}
    assert pa.ARM_S1 not in arms and {pa.ARM_S2_BEST, pa.ARM_S2_BEHIND, pa.ARM_S3_CONTROL} <= arms
    # S3-aside sits out the gas window (12-16Z) that S3-control quotes through.
    aside = [q for q in quotes if q[1] == pa.ARM_S3_ASIDE and q[2] == "KXAAAGASM-26-3.20"]
    control = [q for q in quotes if q[1] == pa.ARM_S3_CONTROL and q[2] == "KXAAAGASM-26-3.20"]
    assert len(control) > len(aside) and all(not (12 <= q[3].hour < 16) for q in aside)
    # behind is one tick back
    assert {q[6] for q in quotes if q[1] == pa.ARM_S2_BEHIND} == {2}
    filled = [o for o in outcomes if o[3] is not None]
    assert filled and {o[1] for o in filled} >= {"whole_order", "queue_aware"}
    assert marks                                                    # 24 h later snapshot exists
    res = bt.rd.analyse(quotes, outcomes, marks, hours)
    assert res["arms"]["S2-best"]["models"]["conservative"]["bid_days_10usd"] > 0
    assert "S2-best - S2-behind [conservative]" in res["pairs"]


def test_in_window_bls_release_pulls_s3_aside():
    sel = pa.ArmSelector()
    rel = bt.HIST_BLS_RELEASES[0]
    c = bt.candidate({"tk": "KXWAAEROEMP-27FEB-T1", "hr": rel, "yb": 3, "nb": 95, "fy": 2000.0,
                      "fn": 5000.0, "ym": True, "nm": True, "series": "KXWAAEROEMP", "event": None,
                      "pool": 240.0, "start": rel - dt.timedelta(days=5),
                      "end": rel + dt.timedelta(days=20), "close": rel + dt.timedelta(days=40)})
    s = pa.Selection(c.ticker, c.event, "yes", rel, c.start)
    assert bt.desired(sel, pa.ARM_S3_ASIDE, s, c, rel).reason == pa.PULL_BLS
    assert isinstance(bt.desired(sel, pa.ARM_S3_CONTROL, s, c, rel), pa.ArmQuote)
