"""Regression guard for evo_selftest's MEM-2 (belief supersession) probe.

The probe's only structural check (no orphaned superseded belief lacking a
successor) is vacuously true when zero supersessions have ever happened — so it
PASSed even though, live, 40+ beliefs had accumulated with 0 revision links ever
formed (agents could not reference their own memory ids for supersedes_id). A
meaningful sample with never one real chain deserves a look, not a silent PASS.
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
    """Answers c_beliefs' three queries (belief count, chain count, orphan count),
    dispatched by distinctive SQL substrings, in the order c_beliefs issues them."""

    def __init__(self, n, chains, orphan):
        self.n = n
        self.chains = chains
        self.orphan = orphan
        self._last = ""

    def execute(self, sql, params=()):
        self._last = sql

    def fetchone(self):
        sql = self._last
        if "supersedes_id is not null" in sql:
            return (self.chains,)
        if "not exists" in sql:
            return (self.orphan,)
        if "count(*)" in sql:
            return (self.n,)
        return (None,)


def _verdict(**kw):
    return st.c_beliefs(FakeCursor(**kw))[3]


def test_no_beliefs_is_not_yet():
    assert _verdict(n=0, chains=0, orphan=0) == st.NOT_YET


def test_real_chains_pass():
    assert _verdict(n=12, chains=3, orphan=0) == st.PASS


def test_orphaned_superseded_belief_is_broken():
    assert _verdict(n=12, chains=3, orphan=1) == st.BROKEN


def test_many_beliefs_zero_chains_is_info_not_pass():
    # The exact live shape: 44+ beliefs accumulated, never one real supersession.
    assert _verdict(n=44, chains=0, orphan=0) == st.INFO


def test_few_beliefs_zero_chains_still_passes():
    # Too early to expect a revision yet — not worth flagging.
    assert _verdict(n=3, chains=0, orphan=0) == st.PASS
