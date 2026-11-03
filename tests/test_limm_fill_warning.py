"""scripts/limm_fill_warning.py — pins the pre-registered warning, lift, gain and verdict
(docs/LIMM_FILL_WARNING.md) and runs main() end to end on synthetic rows, without the DB."""

from __future__ import annotations

import datetime as dt
import importlib.util
import math
import pathlib
import sys
import types

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_fill_warning", _SCRIPTS / "limm_fill_warning.py")
fw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fw)  # type: ignore[union-attr]

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 9, 20, 0, tzinfo=UTC)


def test_constants_match_the_preregistration():
    assert (fw.SINCE, fw.UNTIL) == ("2026-11-27T15:00:00+00:00", "2026-12-11T15:00:00+00:00")
    assert (fw.TRADES_5M_WARN, fw.DEPTH_DROP_WARN) == (1, 0.5)
    assert (fw.MIN_FILLS, fw.MIN_FILL_EVENTS, fw.MIN_WARNED_FILLS) == (30, 10, 20)
    assert (fw.LIFT_USEFUL, fw.LIFT_NO_SIGNAL) == (2.0, 1.5)


def test_warning_rules():
    assert fw.depth_drop(40, 100) == 0.6
    assert fw.depth_drop(120, 100) == 0.0
    assert fw.depth_drop(None, 100) is None and fw.depth_drop(10, 0) is None
    assert fw.primary_warning(1, None) and fw.primary_warning(0, 0.5)
    assert not fw.primary_warning(0, 0.49) and not fw.primary_warning(None, None)


def test_lift_and_gain():
    assert fw.lift(10, 100, 10, 1000) == 10.0
    assert fw.lift(1, 10, 0, 10) == math.inf
    assert fw.lift(0, 0, 1, 10) is None
    g = fw.cluster_sum({"a": 3.0, "b": 1.0})
    assert g["gain"] == 4.0 and math.isclose(g["se"], math.sqrt(2 * 2.0))


def test_verdict():
    good = {"gain": 10.0, "se": 1.0}
    assert fw.verdict(29, 12, 25, 3.0, good) == "HOLD (accrual)"
    assert fw.verdict(40, 12, 25, 3.0, good) == "USEFUL"
    assert fw.verdict(40, 12, 19, 3.0, good) == "HOLD"
    assert fw.verdict(40, 12, 25, 1.4, good) == "NO-SIGNAL"
    assert fw.verdict(40, 12, 25, 3.0, {"gain": -10.0, "se": 1.0}) == "NO-SIGNAL"


def test_main_end_to_end(monkeypatch, capsys):
    """12 events x 1 market: a print burst in the snapshot hour precedes a fill, marked down 6h
    later; quiet hours never fill."""
    start, end = T0 - dt.timedelta(days=30), T0 + dt.timedelta(days=30)
    close = T0 + dt.timedelta(days=10)
    snaps, trades = [], []
    for e in range(12):
        tk = f"KXGOOD-E{e}-M"
        for h in range(48):
            hr = T0 + dt.timedelta(hours=h)
            burst = h in (10, 20, 30)
            yes_bid = 1 if h in (17, 27, 37) else 5     # marked down 6h after each fill
            snaps.append((tk, hr, yes_bid, 94, 100, 100, 1000.0, 1000.0, True, True,
                          3 if burst else 0, 0, "KXGOOD", f"KXGOOD-E{e}", "liquidity", 4000.0,
                          start, end, close))
            if burst:
                at = hr + dt.timedelta(hours=1, minutes=3)
                trades.append((tk, int(at.timestamp() * 1000), at, 5, 1000, "no"))

    class Cur:
        q = None

        def execute(self, sql, params):
            self.q = "trade" if "incentive_trade_events" in sql else "snap"

        def fetchall(self):
            return trades if self.q == "trade" else snaps

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
    assert fw.main(["--since", T0.isoformat(), "--until", (T0 + dt.timedelta(hours=48)).isoformat()]) == 0
    out = capsys.readouterr().out
    assert "fills 36 (warned 36) in 12 events" in out
    assert "lift inf" in out
    assert "VERDICT (decides): USEFUL" in out


def test_probe_is_ops_allowlisted():
    import ops_runner
    assert "limm_fill_warning" in ops_runner.ALLOWED_SCRIPTS
