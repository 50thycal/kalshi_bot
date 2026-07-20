"""Regression guard for evo_selftest's L-2 heartbeat-health probe.

The probe must be *recency-aware*: an infra blip (credit exhausted / no key /
no price) that already recovered inside the window must NOT read as BROKEN — it
is only stop-the-line when infra is the latest signal (nothing completed since).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load("evo_selftest")


class FakeCursor:
    """Answers the four queries c_heartbeat_health issues, dispatched by SQL text.

    comp_6h / deg_6h are counts; last_comp / last_infra are minutes-ago (or None
    when there is no such row, mirroring max()->NULL from the real DB).
    """

    def __init__(self, comp_6h, deg_6h, last_comp, last_infra):
        self.comp_6h = comp_6h
        self.deg_6h = deg_6h
        self.last_comp = last_comp
        self.last_infra = last_infra
        self._last = ""

    def execute(self, sql, params=()):
        self._last = sql

    def fetchone(self):
        sql = self._last
        if "max(created_at)" in sql and "status='completed'" in sql:
            return (self.last_comp,)
        if "max(created_at)" in sql:  # the infra_pred query
            return (self.last_infra,)
        if "count(*)" in sql and "status='completed'" in sql:
            return (self.comp_6h,)
        if "count(*)" in sql and "status='degraded'" in sql:
            return (self.deg_6h,)
        return (None,)


def _verdict(**kw):
    return st.c_heartbeat_health(FakeCursor(**kw))[3]


def test_recovered_infra_blip_is_not_broken():
    # The exact bug: many degraded rows in 6h, but the outage recovered and the
    # most recent heartbeats completed. Old code flagged BROKEN off the 6h top
    # reason; recency-aware code must PASS.
    assert _verdict(comp_6h=3, deg_6h=14, last_comp=5.0, last_infra=180.0) == st.PASS


def test_active_infra_outage_with_no_completion_is_broken():
    assert _verdict(comp_6h=0, deg_6h=10, last_comp=None, last_infra=2.0) == st.BROKEN


def test_active_infra_outage_newer_than_last_completion_is_broken():
    # last infra failure (3 min ago) is more recent than the last completion
    # (200 min ago) -> the outage is the latest signal.
    assert _verdict(comp_6h=2, deg_6h=10, last_comp=200.0, last_infra=3.0) == st.BROKEN


def test_no_heartbeats_is_not_yet():
    assert _verdict(comp_6h=0, deg_6h=0, last_comp=None, last_infra=None) == st.NOT_YET


def test_clean_run_is_pass():
    assert _verdict(comp_6h=20, deg_6h=1, last_comp=2.0, last_infra=None) == st.PASS


def test_stale_completion_no_infra_is_info_not_broken():
    # No infra failure at all, but the last completion is old and the rate is
    # mediocre (non-infra degradation) -> INFO context, never BROKEN.
    assert _verdict(comp_6h=3, deg_6h=5, last_comp=120.0, last_infra=None) == st.INFO
