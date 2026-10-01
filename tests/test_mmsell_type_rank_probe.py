"""TYPE-RANK probe: the pure logic on inputs whose answers are known by construction
(docs/MMSELL_TYPE_RANK_THESIS.md). Pinned: readability floor, top-two rule, date-block
bootstrap determinism, and every verdict branch against its documented bar."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import mmsell_type_rank_probe as tr  # noqa: E402
import ops_runner  # noqa: E402


def _fills(t: str, n: int, val: float, day: str = "2026-09-10"):
    return [{"type": t, "realized": val, "day": day, "series": t, "ticker": t} for _ in range(n)]


def test_allowlisted():
    assert "mmsell_type_rank_probe" in ops_runner.ALLOWED_SCRIPTS


def test_type_of_known_and_unclassified():
    assert tr.type_of("KXMLBHR-26SEP27-X") == "player_prop"
    assert tr.type_of("KXNOSUCHSERIESEVER-1") == "unclassified"


def test_rank_cells_floor_and_order():
    fills = (_fills("h2h", 100, 1.0) + _fills("total", 120, 1.0) + _fills("spread", 99, 9.0)
             + _fills("unclassified", 500, 9.0) + _fills("player_prop", 100, -1.0))
    ranked = tr.rank_cells(fills)
    assert [t for t, _, _ in ranked] == ["total", "h2h", "player_prop"]   # tie -> more fills
    top, rest = tr.split_top_rest(ranked)
    assert top == {"total", "h2h"} and rest == {"player_prop"}


def test_score_and_bootstrap():
    fills = _fills("a", 10, 3.0, "d1") + _fills("b", 10, 1.0, "d1") + _fills("a", 10, 3.0, "d2") \
        + _fills("b", 10, 1.0, "d2")
    n_t, mt, n_r, mr, sep = tr.score(fills, {"a"}, {"b"})
    assert (n_t, mt, n_r, mr, sep) == (20, 3.0, 20, 1.0, 2.0)
    assert tr.date_block_lb(fills, {"a"}, {"b"}, n=200) == 2.0
    assert tr.date_block_lb([], {"a"}, {"b"}) is None


def test_verdict_bars():
    ok_cov = {"A": (1.0, 1.0), "B": (1.0, 1.0)}
    r_ok = (150, 1.0, 100, 0.5, 0.5)
    f_ok = (150, 2.0, 100, 0.0, 2.0)

    def v(cov=ok_cov, ca=5, cab=5, r=r_ok, f=f_ok, lb=0.1):
        return tr.verdict(cov, ca, cab, r, f, lb)[0]

    assert v() == "PROMOTE"
    assert v(cov={"A": (0.94, 1.0), "B": (1.0, 1.0)}) == "HOLD (instrument)"
    assert v(cov={"A": (1.0, 1.0), "B": (1.0, 0.94)}) == "HOLD (instrument)"
    assert v(ca=3) == "HOLD (instrument)"
    assert v(r=(150, 1.0, 100, 1.0, 0.0)) == "KILL"                       # T2
    assert v(r=(149, 1.0, 100, 1.0, -5.0), f=(0, None, 0, None, None)) == "HOLD (accrual)"
    assert v(f=(149, 2.0, 100, 0.0, 2.0)) == "HOLD (accrual)"             # T3
    assert v(f=(150, 2.0, 100, 2.0, 0.0)) == "KILL"                       # T4 kill
    assert v(f=(150, 2.0, 100, 1.0, 1.0)) == "HOLD (underpowered)"        # sep < 1.5
    assert v(lb=0.0) == "HOLD (underpowered)"                             # p5 not > 0
    assert v(f=(150, 0.9, 100, -0.6, 1.5)) == "HOLD (underpowered)"       # TOP < +1.0
