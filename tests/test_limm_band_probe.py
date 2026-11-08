"""scripts/limm_band_probe.py — pins the pre-registered band, policies, markout and verdict
(docs/LIMM_BAND_PROBE.md) and runs main() end to end on synthetic rows, without the DB."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import types

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_band_probe", _SCRIPTS / "limm_band_probe.py")
bp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bp)  # type: ignore[union-attr]

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 9, 20, 0, tzinfo=UTC)


def test_constants_match_the_preregistration():
    assert (bp.SINCE, bp.UNTIL) == ("2026-11-09T00:00:00+00:00", "2026-12-03T15:00:00+00:00")
    assert (bp.BAND_LO, bp.BAND_HI, bp.LEG_USD_CENTS) == (11, 50, 1000)
    assert (bp.MARK_HOURS, bp.MARK_HOURS_DESC, bp.UNMARKED_FRACTION) == (24, 6, 0.5)
    assert (bp.MIN_LEG_DAYS, bp.MIN_EVENTS, bp.BAR_USD, bp.Z_DECIDE) == (100, 10, 0.15, 2.39)
    assert bp.POLICIES == ("CHEAP", "DEAR", "BOTH")


def test_band_never_overlaps_the_cheap_side_studies():
    assert not bp.in_band(10, 88) and not bp.in_band(88, 10)    # study 5/6/8 territory
    assert bp.in_band(11, 87) and bp.in_band(50, 48) and bp.in_band(60, 38)
    assert not bp.in_band(None, 40) and not bp.in_band(51, 52)
    assert bp.band_of(20, 78) == "11-25" and bp.band_of(70, 29) == "26-40"
    assert bp.band_of(45, 53) == "41-50"


def test_legs_and_size():
    assert bp.legs_for("CHEAP", 30, 68) == [("yes", 30)]
    assert bp.legs_for("DEAR", 30, 68) == [("no", 68)]
    assert bp.legs_for("BOTH", 70, 28) == [("no", 28), ("yes", 70)]
    assert bp.legs_for("CHEAP", 45, 45) == [("yes", 45)]
    assert bp.leg_qty(25) == 40 and bp.leg_qty(45) == 22


def test_markout():
    marks = {T0 + dt.timedelta(hours=25): (30, 68)}
    assert bp.mark_bid(marks, "yes", T0, 24) == 30
    assert bp.mark_bid(marks, "no", T0 - dt.timedelta(hours=1), 24) == 68   # +1 h search
    assert bp.mark_bid({}, "yes", T0, 24) is None
    assert bp.markout_usd(40, 25, 20) == 2.0
    assert bp.markout_usd(40, 25, None) == 5.0
    assert bp.markout_usd(0, 25, 1) == 0.0


def test_verdict():
    good = {"R": 0.40, "se": 0.05}
    assert bp.verdict(99, 12, 0.5, good) == "HOLD (accrual)"
    assert bp.verdict(200, 9, 0.5, good) == "HOLD (accrual)"
    assert bp.verdict(200, 12, 0.5, good) == "PROMISING"
    assert bp.verdict(200, 12, 0.5, {"R": -0.1, "se": 0.05}) == "FILL-TRAP"
    assert bp.verdict(200, 12, 0.05, {"R": 0.01, "se": 0.02}) == "NO-LEAD"
    assert bp.verdict(200, 12, 0.5, {"R": 0.20, "se": 0.2}) == "HOLD"


def test_main_end_to_end(monkeypatch, capsys):
    """12 events x 1 market at 30/68 for 10 days, deep pool and a thin field: no prints, so no
    fills; every policy earns reward and the deciding verdict is PROMISING."""
    start, end = T0 - dt.timedelta(days=30), T0 + dt.timedelta(days=60)
    close = T0 + dt.timedelta(days=60)
    snaps = []
    for e in range(12):
        tk = f"KXECON-E{e}-M"
        for h in range(240):
            hr = T0 + dt.timedelta(hours=h)
            snaps.append((tk, hr, 30, 68, 50.0, 50.0, 2000.0, 2000.0, True, True,
                          "KXECON", f"KXECON-E{e}", "liquidity", 3000.0, start, end, close))

    class Cur:
        q = None

        def execute(self, sql, params):
            self.q = "trade" if "incentive_trade_events" in sql else "snap"

        def fetchall(self):
            return [] if self.q == "trade" else snaps

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self):
            return Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: Conn()))
    monkeypatch.setenv("DATABASE_URL_RO", "postgresql://u:p@h/db")
    assert bp.main(["--since", T0.isoformat(),
                    "--until", (T0 + dt.timedelta(hours=240)).isoformat()]) == 0
    out = capsys.readouterr().out
    assert "qualifying band hours 2880; markets 12" in out
    assert "BOTH markets where both legs filled: 0 of 12" in out
    tail = out.split("== VERDICT")[1]
    assert "CHEAP  PROMISING" in tail and "BOTH   PROMISING" in tail


def test_probe_is_ops_allowlisted():
    import ops_runner
    assert "limm_band_probe" in ops_runner.ALLOWED_SCRIPTS
