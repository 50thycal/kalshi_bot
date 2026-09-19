"""The discretionary desk's board read — pure helpers pinned, no network.

The failure modes pinned here have ancestors in the record: a fee model that ignores the
per-order round-up (docs/MMSELL_FEE_RECON.md), a price field read in the wrong unit when
Kalshi moved from integer cents to fixed-point dollar strings, and a `hours_to_close`
that lets an already-closed market onto the board.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import kalshi_desk_board as board  # noqa: E402

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _iso(hours: float) -> str:
    return (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _market(**kw) -> dict:
    base = {
        "ticker": "KXTEST-26SEP20-T1", "event_ticker": "KXTEST-26SEP20", "title": "Test market",
        "yes_sub_title": "Above 1", "status": "open", "yes_bid": 40, "yes_ask": 44, "no_bid": 56,
        "no_ask": 60, "last_price": 42, "volume": 1200, "volume_24h": 300, "open_interest": 900,
        "close_time": _iso(24),
    }
    base.update(kw)
    return base


def test_the_script_is_allowlisted_on_the_ops_channel():
    import ops_runner
    assert "kalshi_desk_board" in ops_runner.ALLOWED_SCRIPTS


# --- fees: the published taker formula, rounded UP per order --------------------------

def test_taker_fee_peaks_at_the_coin_flip_and_rounds_up():
    assert board.taker_fee_cents(50) == 2          # 1.75c -> 2c
    assert board.taker_fee_cents(7) == 1           # 0.4557c -> 1c: a 1-lot always pays a cent
    assert board.taker_fee_cents(93) == 1
    assert board.taker_fee_cents(10, qty=10) == 7  # 6.3c -> 7c: the ceiling amortises over size
    assert board.taker_fee_cents(0) == 0 and board.taker_fee_cents(100) == 0
    assert board.taker_fee_cents(None) == 0


def test_breakeven_is_the_all_in_cost_in_cents():
    assert board.breakeven_win_pct(7) == 8.0       # 7c + 1c fee: YES must win 8% of the time
    assert board.breakeven_win_pct(50) == 52.0
    assert board.breakeven_win_pct(None) is None


# --- prices: both payload spellings, dollars preferred ---------------------------------

def test_cents_prefers_the_fixed_point_dollar_string():
    assert board.cents({"yes_bid": 40, "yes_bid_dollars": "0.4100"}, "yes_bid") == 41
    assert board.cents({"yes_bid": 40}, "yes_bid") == 40
    assert board.cents({"yes_bid_dollars": "junk", "yes_bid": 39}, "yes_bid") == 39
    assert board.cents({}, "yes_bid") is None


def test_row_derives_the_missing_ask_from_the_other_side():
    row = board.row_of(_market(yes_ask=None, no_ask=None), {"category": "Economics"}, NOW)
    assert row["yes_ask"] == 44 and row["no_ask"] == 60
    assert row["spread"] == 4
    assert row["fee_yes"] == 2 and row["fee_no"] == 2
    assert row["series"] == "KXTEST" and row["category"] == "Economics"
    assert abs(row["htc"] - 24.0) < 1e-6


# --- filters: closed markets never reach the board -------------------------------------

def _keep(row, **over):
    kw = dict(hours=96.0, min_volume=200, category="", series="", search="", max_spread=None)
    kw.update(over)
    return board.keep(row, **kw)


def test_scan_filters():
    live = board.row_of(_market(), {"category": "Economics"}, NOW)
    assert _keep(live)
    assert not _keep(board.row_of(_market(close_time=_iso(-1)), {}, NOW))          # already closed
    assert not _keep(board.row_of(_market(close_time=_iso(200)), {}, NOW))         # too far out
    assert _keep(board.row_of(_market(close_time=_iso(200)), {}, NOW), hours=240)
    assert not _keep(board.row_of(_market(volume=10, volume_24h=10), {}, NOW))     # too thin
    assert _keep(live, category="econ") and not _keep(live, category="sports")
    assert _keep(live, series="kxtest") and not _keep(live, series="KXHIGH")
    assert _keep(live, search="above 1") and not _keep(live, search="fed")
    assert _keep(live, max_spread=4) and not _keep(live, max_spread=3)


def test_format_row_is_one_bounded_line():
    row = board.row_of(_market(title="x" * 200), {"category": "Economics"}, NOW)
    line = board.format_row(row)
    assert "\n" not in line and len(line) < 200
    assert "KXTEST-26SEP20-T1" in line and " 40/ 44" in line


def test_book_levels_sort_best_first_and_tolerate_dollar_strings():
    book = {"yes": [[40, 10], [42, 5], ["0.41", "7"]], "no": [[56, 3]]}
    assert board._book_levels(book, "yes", 6) == [(42, 5), (41, 7), (40, 10)]
    assert board._book_levels(book, "no", 6) == [(56, 3)]
    assert board._book_levels({}, "yes", 6) == []


def test_main_scan_uses_the_public_events_endpoint_only(monkeypatch):
    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        if path == "/events":
            return {"events": [{"event_ticker": "KXTEST-26SEP20", "title": "Test", "category": "Economics",
                                "markets": [_market()]}], "cursor": ""}
        return {}

    monkeypatch.setattr(board, "_get", fake_get)
    monkeypatch.setattr(board, "datetime", _FrozenDatetime)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = board.main(["--hours", "48", "--min-volume", "100"])
    assert rc == 0
    assert calls == ["/events"]
    text = out.getvalue()
    assert "KXTEST-26SEP20-T1" in text and "by category" in text


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return NOW if tz else NOW.replace(tzinfo=None)
