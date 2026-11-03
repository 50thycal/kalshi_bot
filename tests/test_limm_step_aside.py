"""scripts/limm_step_aside.py — pins the pre-registered band split, pools, verdict and the
end-to-end aggregation (docs/LIMM_STEP_ASIDE.md) without the DB."""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import sys
import types

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("limm_step_aside", _SCRIPTS / "limm_step_aside.py")
sa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sa)  # type: ignore[union-attr]

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 5, 0, tzinfo=UTC)


def _row(hours_to_close, **kw):
    hr = T0
    base = {"hr": hr, "start": hr - dt.timedelta(days=10), "end": hr + dt.timedelta(days=10),
            "close": hr + dt.timedelta(hours=hours_to_close), "yes_meets": True,
            "no_meets": True, "itype": "liquidity"}
    base.update(kw)
    return base


def test_constants_match_the_preregistration():
    assert (sa.SINCE, sa.UNTIL) == ("2026-11-03T03:00:00+00:00", "2026-11-17T15:00:00+00:00")
    assert (sa.BAND_LO_H, sa.BAND_HI_H) == (24.0, 48.0)
    assert (sa.MIN_BAND_MARKETS, sa.MIN_BAND_EVENTS) == (20, 8)
    assert sa.DECIDING == "conservative"


def test_window_split():
    assert sa.window_of(_row(47.9)) == "band"
    assert sa.window_of(_row(24.0)) == "band"
    assert sa.window_of(_row(23.9)) is None
    assert sa.window_of(_row(48.0)) == "base"
    assert sa.window_of(_row(30, no_meets=False)) is None
    assert sa.window_of(_row(30, itype="volume")) is None
    assert sa.window_of(_row(30, close=None)) is None
    assert sa.window_of(_row(30, end=T0)) is None          # programme already over


def test_pools():
    assert sa.pools_of("KXTRUMPMENTION") == ["all", "live", "mention"]
    assert sa.pools_of("KXYTVIEWSHIGH") == ["all", "live", "youtube"]
    assert sa.pools_of("KXAAAGASM") == ["all"]


def test_verdict():
    assert sa.verdict({"R": 0.5, "se": 0.1}, 25, 9) == "NOMINATE 24H"
    assert sa.verdict({"R": -0.5, "se": 0.1}, 25, 9) == "KEEP 48H"
    assert sa.verdict({"R": 0.1, "se": 0.1}, 25, 9) == "HOLD"
    assert sa.verdict({"R": 0.5, "se": 0.1}, 19, 9) == "HOLD (accrual)"
    assert sa.verdict({"R": 0.5, "se": 0.1}, 25, 7) == "HOLD (accrual)"
    assert sa.verdict(None, 0, 0) == "HOLD (accrual)"


def test_main_end_to_end(monkeypatch, capsys):
    """10 mention events x 3 markets: band hours earn, then one market per event is filled in
    the band and marked down; base hours never fill."""
    start, end = T0 - dt.timedelta(days=30), T0 + dt.timedelta(days=30)
    snaps, trades = [], []
    for e in range(10):
        close = T0 + dt.timedelta(hours=72)
        for m in range(3):
            tk = f"KXTRUMPMENTION-E{e}-W{m}"
            for h in range(72):
                hr = T0 + dt.timedelta(hours=h)
                snaps.append((tk, hr, 5, 94, 100, 100, 1000.0, 1000.0, True, True,
                              "KXTRUMPMENTION", f"KXTRUMPMENTION-E{e}", "liquidity", 4000.0,
                              start, end, close))
            if m == 0:   # filled 30h before close (in the band), bid collapses afterwards
                at = T0 + dt.timedelta(hours=42, minutes=5)
                trades.append((tk, int(at.timestamp() * 1000), at, 5, 1000, "no"))
                for h in range(43, 72):
                    hr = T0 + dt.timedelta(hours=h)
                    snaps.append((tk, hr, 1, 98, 100, 100, 1000.0, 1000.0, True, True,
                                  "KXTRUMPMENTION", f"KXTRUMPMENTION-E{e}", "liquidity",
                                  4000.0, start, end, close))

    class Cur:
        q = None

        def execute(self, sql, params):
            self.q = "trade" if "incentive_trade_events" in sql else "snap"

        def fetchall(self):
            if self.q == "trade":
                return trades
            seen = {}
            for r in snaps:
                seen[(r[0], r[1])] = r
            return list(seen.values())

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
    assert sa.main(["--since", T0.isoformat(), "--until",
                    (T0 + dt.timedelta(hours=72)).isoformat()]) == 0
    out = capsys.readouterr().out
    rows = {tuple(ln.split()[:2]): ln.split() for ln in out.splitlines()
            if ln.strip().startswith(("live", "mention", "youtube", "all"))}
    band = rows[("live", "band")]
    base = rows[("live", "base")]
    assert int(band[2]) == 30 and int(band[3]) == 10 and int(band[6]) == 10   # 10 band fills
    assert int(base[6]) == 0
    assert "VERDICT" in out
    assert any(v in out for v in ("NOMINATE 24H", "KEEP 48H", "HOLD"))


def test_probe_is_ops_allowlisted():
    import ops_runner
    assert "limm_step_aside" in ops_runner.ALLOWED_SCRIPTS
