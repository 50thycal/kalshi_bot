"""The deconfounding study's estimators (scripts/mmsell_deconfound_study.py), pinned away from
the DB.

What has to hold for the study's conclusions to mean anything:

* the independent unit is the settled MARKET, so two trades on one market collapse to one
  observation — otherwise every interval is too narrow and every p too small;
* `welch` is oriented a - b, so a "haircut" reported as `unfilled minus filled` is POSITIVE when
  live filled the worse markets. An inverted sign would reverse the study's central claim;
* mmsell8's `only=` allowlist is matched as a case-insensitive series SUBSTRING, the same test
  the running book applies. A prefix test would silently change which markets count as
  "scheduled-settle eligible", which is the quantity §1 of the write-up turns on.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "mmsell_deconfound_study",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "mmsell_deconfound_study.py",
)
ds = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ds)


def row(book: str, ticker: str, pnl: float, *, entry: float = 6.0,
        series: str = "KXBTCD", asset: str | None = None) -> dict:
    return {
        "ticker": ticker, "series": series, "book": book, "status": "settled",
        "type": "price_strike", "mode": "scheduled", "regime": ds.regime_of(series),
        "asset": asset or ("crypto" if ds.regime_of(series) == "Crypto" else "noncrypto"),
        "entry_c": entry, "edge": None, "model_p": None, "pnl_c": pnl, "fees": 0.0,
        "hold_h": 1.0, "created": dt.datetime(2026, 8, 16), "is_twin": False,
        "mmsell8_eligible": any(k in series.upper() for k in ds.MMSELL8_ONLY),
    }


class TestPerMarket:
    def test_trades_in_one_market_collapse_to_one_observation(self):
        rows = [row("A", "T1", 10.0), row("A", "T1", 20.0), row("A", "T2", 0.0)]
        assert ds.per_market(rows) == pytest.approx([15.0, 0.0], abs=1e-9)

    def test_the_same_market_in_two_books_is_two_observations(self):
        # Two books settling the same ticker are two independent BOOK results, and the study
        # never pools books into one arm, so keying on (book, ticker) is the correct unit.
        rows = [row("A", "T1", 10.0), row("B", "T1", 30.0)]
        assert sorted(ds.per_market(rows)) == pytest.approx([10.0, 30.0])


class TestWelch:
    def test_orientation_is_a_minus_b(self):
        w = ds.welch([10.0, 12.0, 11.0], [1.0, 3.0, 2.0])
        assert w["delta"] == pytest.approx(9.0)

    def test_a_haircut_is_positive_when_live_filled_the_worse_markets(self):
        # unfilled (good) minus filled (bad) -> POSITIVE means fill selection hurt.
        unfilled, filled = [8.0, 10.0, 12.0], [-2.0, 0.0, 2.0]
        assert ds.welch(unfilled, filled)["delta"] > 0

    def test_identical_samples_give_a_zero_delta_and_a_ci_spanning_zero(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        w = ds.welch(xs, list(xs))
        assert w["delta"] == pytest.approx(0.0)
        assert w["lo"] < 0 < w["hi"]

    def test_a_single_observation_side_is_not_identified_rather_than_precise(self):
        w = ds.welch([5.0], [1.0, 2.0, 3.0])
        assert w["delta"] == pytest.approx(5.0 - 2.0)
        assert w["se"] is None and w["lo"] is None

    def test_an_empty_side_returns_no_delta_at_all(self):
        assert ds.welch([], [1.0, 2.0])["delta"] is None


class TestEligibility:
    @pytest.mark.parametrize("series,expected", [
        ("KXBTCD", True),            # the allowlist's own crypto daily
        ("KXETHD", True),            # matched by the ETH substring
        ("KXETH", True),
        ("KXMLBHRDERBYMATCHUP", True),   # substring, not prefix: HRDERBY sits mid-ticker
        ("KXMLBASGHR", True),
        ("KXMLBGAME", False),
        ("KXWTI", False),            # a NON-crypto scheduled series the allowlist misses
        ("KXNATGASD", False),
    ])
    def test_allowlist_is_a_case_insensitive_substring_test(self, series, expected):
        assert row("A", "T", 0.0, series=series)["mmsell8_eligible"] is expected

    def test_a_prefix_test_would_have_missed_the_derby_props(self):
        # Pinning the distinction the config's own parser makes: `only=` matches ANYWHERE in the
        # series, so HRDERBY admits KXMLBHRDERBY* even though the series starts with KXMLB.
        assert not "KXMLBHRDERBYMATCHUP".startswith("HRDERBY")
        assert row("A", "T", 0.0, series="KXMLBHRDERBYMATCHUP")["mmsell8_eligible"]


class TestBandAndAsset:
    def test_band_is_inclusive_at_both_ends(self):
        rows = [row("A", f"T{c}", 0.0, entry=c) for c in (4, 5, 6, 7, 8)]
        assert {r["entry_c"] for r in ds.band(rows, 5, 7)} == {5, 6, 7}

    def test_a_missing_entry_price_is_excluded_not_defaulted(self):
        r = row("A", "T", 0.0)
        r["entry_c"] = None
        assert ds.band([r], 0, 100) == []

    @pytest.mark.parametrize("series,asset", [
        ("KXBTCD", "crypto"), ("KXETHD", "crypto"), ("KXSOLD", "crypto"),
        ("KXMLBGAME", "noncrypto"), ("KXWTI", "noncrypto"), ("KXRAIN", "noncrypto"),
    ])
    def test_asset_class_follows_the_shared_regime_map(self, series, asset):
        assert row("A", "T", 0.0, series=series)["asset"] == asset


class TestSel:
    def test_none_filters_are_ignored_rather_than_matching_none(self):
        rows = [row("A", "T1", 1.0), row("B", "T2", 2.0)]
        assert ds.sel(rows, book=None) == rows

    def test_collection_values_match_any_member(self):
        rows = [row("A", "T1", 1.0), row("B", "T2", 2.0), row("C", "T3", 3.0)]
        assert {r["book"] for r in ds.sel(rows, book=("A", "C"))} == {"A", "C"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        name, pathlib.Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("script", ["mmsell_deconfound_study", "theta_tail_diagnosis",
                                   "theta_tail_refit"])
def test_ops_runner_allowlists_the_new_studies(script, tmp_path, monkeypatch):
    """A study nobody can run is a study nobody will re-run. Both are read-only and stdlib +
    psycopg only, which is the bar for the ops channel."""
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    monkeypatch.delenv("DATABASE_URL_RO", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    runner = _load("ops_runner")
    assert script in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": script, "args": []}))
    assert runner.main() == 1  # dispatches; exits 1 cleanly with no DB URL
