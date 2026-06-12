"""Tests for scripts/weather_polymarket_align.py — pure parsing/matching (no network)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


pa = _load("weather_polymarket_align")
NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def test_city_and_kind_matching():
    assert pa.match_city("Highest temperature in NYC on June 12?") == "NYC"
    assert pa.match_city("Lowest temperature in Los Angeles today") == "LAX"
    assert pa.match_city("Will it rain in Seattle?") is None
    assert pa.parse_kind("Highest temperature in NYC") == "high"
    assert pa.parse_kind("Lowest temperature in Denver") == "low"
    assert pa.parse_kind("Temperature in Miami") is None


def test_date_parsing_with_year_wrap():
    assert pa.parse_date("Highest temperature in NYC on June 12?", now=NOW) == "2026-06-12"
    # A month already well in the past wraps to next year.
    assert pa.parse_date("Highest temp in NYC on January 3?", now=NOW) == "2027-01-03"
    assert pa.parse_date("no date here", now=NOW) is None


def test_bucket_label_parsing():
    assert pa.parse_bucket("75-76°F") == (75.0, 76.0)
    assert pa.parse_bucket("80°F or above") == (80.0, None)
    assert pa.parse_bucket("70°F or below") == (None, 70.0)
    assert pa.parse_bucket("≥ 90°F") == (90.0, None)
    assert pa.parse_bucket("74°F") == (74.0, 74.0)
    assert pa.parse_bucket("no numbers") is None


def test_bucket_widths_and_source_snippet():
    buckets = [(74.0, 75.0), (76.0, 77.0), (80.0, None)]  # open end excluded from widths
    assert pa.bucket_widths(buckets) == [1.0, 1.0]
    desc = "This market is fun. Resolves to the high as reported by the National Weather "\
           "Service at LaGuardia Airport. Have fun."
    snip = pa.resolution_snippet(desc)
    assert "National Weather Service" in snip and "Airport" in snip


def _event(title, labels, description="per NWS at the airport station"):
    return {
        "title": title,
        "slug": "x",
        "endDate": "2026-06-12T23:59:00Z",
        "markets": [
            {"groupItemTitle": lbl, "description": description} for lbl in labels
        ],
    }


def test_summarize_and_verdict_aligned_vs_mismatch():
    # Kalshi-like 1-degree ladder + named source -> ALIGNED?
    s = pa.summarize_event(
        _event("Highest temperature in NYC on June 12?", ["73-74°F", "75-76°F", "77-78°F"]),
        now=NOW,
    )
    assert s["city"] == "NYC" and s["kind"] == "high" and s["date"] == "2026-06-12"
    assert s["n_buckets"] == 3 and s["widths"] == [1.0]
    assert pa.verdict(s).startswith("ALIGNED")

    # Single wide threshold scheme -> MISMATCH
    s2 = pa.summarize_event(
        _event("Highest temperature in Chicago on June 12?", ["Above 85°F", "Below 85°F"],
               description="resolves per some source"),
        now=NOW,
    )
    assert s2["open_ended"] == 2
    assert pa.verdict(s2).startswith("MISMATCH")

    # Non-temperature event -> dropped
    assert pa.summarize_event(_event("Will the Fed cut rates?", ["Yes"]), now=NOW) is None


def test_ops_runner_allowlists_polymarket_align(tmp_path, monkeypatch):
    import json

    req = tmp_path / "request.json"
    monkeypatch.setenv("OPS_REQUEST_PATH", str(req))
    runner = _load("ops_runner")
    assert "weather_polymarket_align" in runner.ALLOWED_SCRIPTS
    req.write_text(json.dumps({"type": "script", "name": "evil"}))
    assert runner.main() == 1  # non-allowlisted rejected
