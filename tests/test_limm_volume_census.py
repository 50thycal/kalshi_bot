"""scripts/limm_volume_census.py — pins the pre-registered measures and verdict
(docs/LIMM_VOLUME_CENSUS.md) and runs main() end to end on synthetic rows, without the DB."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import types

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_volume_census",
                                               _SCRIPTS / "limm_volume_census.py")
vc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vc)  # type: ignore[union-attr]

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 1, tzinfo=UTC)


def test_constants_match_the_preregistration():
    assert (vc.FOLLOW_UP_TOTAL_PER_DAY, vc.FOLLOW_UP_SERIES_PER_DAY) == (10.0, 2.0)
    assert vc.OVERLAP_DAYS == 30 and vc.HISTORY_SINCE.startswith("2026-09-01")


def test_pool_per_day_and_cap():
    assert vc.pool_per_day(70.0, T0, T0 + dt.timedelta(days=7)) == 10.0
    assert vc.pool_per_day(70.0, T0, T0) is None
    assert vc.pool_per_day(None, T0, T0 + dt.timedelta(days=1)) is None
    assert vc.cap_usd("250000") == 25.0
    assert vc.cap_usd(None) is None and vc.cap_usd("x") is None


def test_verdict():
    assert vc.verdict(0, 50.0, 50.0) == "INSTRUMENT GAP"
    assert vc.verdict(5, 10.0, 2.0) == "FOLLOW-UP"
    assert vc.verdict(5, 9.99, 5.0) == "NO-LEAD"
    assert vc.verdict(5, 20.0, 1.99) == "NO-LEAD"


def test_main_end_to_end(monkeypatch, capsys):
    calls = []

    class Cur:
        def execute(self, sql, params=None):
            calls.append(sql)

        def fetchall(self):
            if "GROUP BY 1" in calls[-1]:
                return [("liquidity", 900, 4000, 12000.0, T0, T0),
                        ("volume", 4, 10, 300.0, T0, T0)]
            return [("volume", "KXBTCD", "KXBTCD-A", 70.0, T0, T0 + dt.timedelta(days=7),
                     "Volume incentive", "250000"),
                    ("volume", "KXBTCD", "KXBTCD-B", 35.0, T0, T0 + dt.timedelta(days=7),
                     "Volume incentive", None),
                    ("volume", "KXETH", "KXETH-A", 7.0, T0, T0 + dt.timedelta(days=7),
                     None, None)]

        def fetchone(self):
            return (1,)

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
    assert vc.main([]) == 0
    out = capsys.readouterr().out
    assert "$16.00/day in all" in out           # (70 + 35 + 7) / 7
    assert "FOLLOW-UP" in out                    # total >= 10 and KXBTCD = 15/day >= 2
    assert "1 of 3 active" in out and "NOTE:" in out


def test_probe_is_ops_allowlisted():
    import ops_runner
    assert "limm_volume_census" in ops_runner.ALLOWED_SCRIPTS
