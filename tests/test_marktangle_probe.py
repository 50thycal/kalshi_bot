"""The MARKTANGLE Phase-A scanner's arithmetic, on sequences whose answers are
known by construction.

A probe that reports a fake edge is worse than no probe, and every failure mode
this file pins has an ancestor in the graveyard: the small-n mirage (the FLB
"pocket"), the lookahead bug (MLBWX's fake +5.5c), and pooling observations that
are not one sequence (the pre-2026-08-14 mmsell anchor legs).
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import marktangle_probe as mp  # noqa: E402


def _seq(text: str) -> list[str]:
    """'YYNY' -> ['yes','yes','no','yes'] — the tables read better this way."""
    return ["yes" if c == "Y" else "no" for c in text]


# ===========================================================================
# The conditional table
# ===========================================================================


def test_an_alternating_sequence_reverses_every_time_at_k_equals_one():
    table = mp.streak_table(_seq("YNYNYNYNYN"))
    n, rev = table[1]
    assert (n, rev) == (9, 9)
    assert table[2] == (0, 0), "there is never a run of 2 to condition on"


def test_a_constant_sequence_never_reverses_and_the_denominators_are_the_run():
    table = mp.streak_table(_seq("YYYYY"))
    assert [table[k] for k in (1, 2, 3, 4)] == [(1, 0), (1, 0), (1, 0), (1, 0)]
    assert table[5] == (0, 0), "the trailing run has no successor to observe"


def test_the_final_run_is_never_counted():
    """Counting it would score an outcome we have not seen — the lookahead shape
    that produced MLBWX's fake +5.5c."""
    for text in ("YYN", "YYNNNNNN", "YNYYNNNYN"):
        seq = _seq(text)
        observed = sum(n for n, _ in mp.streak_table(seq).values())
        assert observed == len(seq) - 1, (
            f"{text}: every position but the last must contribute exactly one "
            "observation, and the last must contribute none"
        )


def test_the_table_counts_positions_so_one_long_run_moves_several_rows():
    table = mp.streak_table(_seq("YYYYN"))
    assert [table[k] for k in (1, 2, 3, 4)] == [(1, 0), (1, 0), (1, 0), (1, 1)]


def test_at_least_k_matches_the_rule_an_arm_would_actually_trade():
    n, rev = mp.holdout_at_least(_seq("YYYYNYYYN"), 3)
    # positions with run >= 3: the 3rd and 4th Y (reversal on the 4th), and the
    # 3rd Y of the second block (reversal).
    assert (n, rev) == (3, 2)


# ===========================================================================
# Confidence, and the mirage it exists to catch
# ===========================================================================


def test_the_lower_bound_refuses_to_certify_a_small_perfect_record():
    assert mp.wilson_lower(10, 10) < 100.0, (
        "a Wald interval has zero width at a perfect record; that is the "
        "approximation error this probe cannot afford"
    )
    assert mp.wilson_lower(7, 10) < 50.0, (
        "70% on 10 observations must not clear a 50% bar — that is the FLB "
        "pocket mirage"
    )
    assert mp.wilson_lower(700, 1000) > 50.0


def test_a_zero_denominator_is_zero_not_an_exception():
    assert mp.wilson_lower(0, 0) == 0.0


# ===========================================================================
# Fitting reads TRAIN only
# ===========================================================================


def test_the_threshold_is_the_smallest_qualifying_k_not_the_best_looking():
    train = _seq("YN" * 200)          # every k=1 observation reverses
    fitted = mp.pick_threshold(train, min_n=30)
    assert fitted is not None
    assert fitted[0] == 1


def test_no_threshold_is_fitted_on_a_memoryless_sequence():
    """The honest answer to a fair coin is 'nothing here', at every k."""
    train = _seq("YYNYNNYNYYNNYYNYNNYN" * 20)
    fitted = mp.pick_threshold(train, min_n=30)
    assert fitted is None or fitted[1] > 50.0


def test_the_split_is_seventy_thirty_and_holdout_is_the_tail():
    train, hold = mp.split(list(range(100)))
    assert train == list(range(70)) and hold == list(range(70, 100))


# ===========================================================================
# Families are sequences, not piles
# ===========================================================================


def _m(event, ticker, close, result):
    return {"event": event, "ticker": ticker, "close": close, "result": result,
            "vol": 100.0}


def test_ladder_rungs_are_separate_families():
    """Pooling the rungs of one ladder into a single sequence would manufacture
    dependence out of the ladder's geometry."""
    a = _m("KXHIGHNY-25AUG29", "KXHIGHNY-25AUG29-B82.5", 1, "yes")
    b = _m("KXHIGHNY-25AUG29", "KXHIGHNY-25AUG29-B84.5", 1, "no")
    assert mp.family_key(a) != mp.family_key(b)
    assert mp.family_key(a) == "KXHIGHNY|B82.5"


def test_same_close_resolutions_are_dropped_rather_than_ordered_by_guess():
    rows = [_m("S-1", "S-1-A", 10, "yes"), _m("S-2", "S-2-A", 10, "no")]
    rows += [_m(f"S-{i}", f"S-{i}-A", 100 + i, "yes") for i in range(mp.MIN_FAMILY_N)]
    fams = mp.build_families(rows)
    seq = fams["S|A"]
    closes = [r["close"] for r in seq]
    assert len(closes) == len(set(closes))


def test_a_family_below_the_floor_is_not_reported_at_all():
    rows = [_m(f"S-{i}", f"S-{i}-A", i, "yes") for i in range(mp.MIN_FAMILY_N - 1)]
    assert mp.build_families(rows) == {}


# ===========================================================================
# Costs and the price side
# ===========================================================================


def test_the_taker_fee_is_the_worst_case_ceiling_and_peaks_at_the_middle():
    assert mp.taker_fee_c(50) == 2
    assert mp.taker_fee_c(95) == 1
    assert mp.taker_fee_c(1) >= 1


def test_the_reversal_side_is_priced_at_the_offer_it_would_have_lifted():
    candle = {"yes_ask": {"close": 60}, "yes_bid": {"close": 57}}
    assert mp.reversal_side_price_c(candle, reversal_is_yes=True) == 60
    assert mp.reversal_side_price_c(candle, reversal_is_yes=False) == 43


def test_an_unquotable_market_returns_no_price_rather_than_a_free_one():
    assert mp.reversal_side_price_c({"yes_ask": {"close": 0}}, True) is None
    assert mp.reversal_side_price_c({}, False) is None


def test_the_price_stage_scores_the_model_probability_not_the_holdout_answer(
    monkeypatch,
):
    """Scoring the rule with the holdout's own realized rate would grade it
    against its own answers."""
    rows = [_m(f"S-{i}", f"S-{i}-A", i, "yes") for i in range(4)]
    rows.append(_m("S-4", "S-4-A", 4, "no"))
    monkeypatch.setattr(mp, "decision_candle",
                        lambda *a, **k: {"yes_ask": {"close": 40},
                                         "yes_bid": {"close": 38}})
    out = mp.price_stage(rows, k=3, p_model=0.70, max_fetch=10)
    assert out["entries"] == 2
    assert out["priced"] == 2
    # reversal side is NO at 100-38 = 62c, fee ceil(7*.62*.38) = 2c
    assert out["mean_edge_c"] == pytest.approx(70.0 - 62.0 - 2.0)


def test_the_ops_channel_can_run_the_probe():
    import ops_runner

    assert "marktangle_probe" in ops_runner.ALLOWED_SCRIPTS


# ===========================================================================
# The balance screen and the two-stage fetch (added after the first
# exchange-wide run, ops mkt-probe-1 / mkt-diag-1 on 2026-08-29)
# ===========================================================================


def _family(result_pattern: str, n_repeat: int = 5):
    rows = []
    seq = _seq(result_pattern) * n_repeat
    for i, r in enumerate(seq):
        rows.append(_m(f"S-{i}", f"S-{i}-A", i, r))
    return rows


def test_a_constant_family_is_screened_out_not_analysed():
    """The first real run returned ~90 KXBTCD strike families that resolve NO
    100% of the time. They are not memoryless, they are CONSTANT: there is no
    conditional structure to find, and leaving them in buries everything else."""
    fams = mp.build_families(_family("YYYYYYYY", 6))
    kept, funnel = mp.screen_balance(fams)
    assert kept == {}
    assert funnel["constant"] == 1 and funnel["kept"] == 0


def test_a_lopsided_family_is_screened_out_with_its_own_count():
    """90/10 is not 'roughly balanced'. It is counted separately from constant,
    because the two say different things about the universe."""
    rows = _family("Y" * 9 + "N", 6)
    kept, funnel = mp.screen_balance(mp.build_families(rows))
    assert kept == {}
    assert funnel["outside_band"] == 1 and funnel["constant"] == 0


def test_a_balanced_family_survives_the_screen():
    kept, funnel = mp.screen_balance(mp.build_families(_family("YNYNYYNN", 6)))
    assert len(kept) == 1 and funnel["kept"] == 1


def test_the_screen_reports_a_funnel_that_adds_up():
    """A screen nobody can see is indistinguishable from a bug."""
    rows = _family("YYYYYYYY", 6) + [
        _m(f"B-{i}", f"B-{i}-A", 1000 + i, r)
        for i, r in enumerate(_seq("YNYNYYNN") * 6)
    ]
    _kept, funnel = mp.screen_balance(mp.build_families(rows))
    assert funnel["considered"] == funnel["constant"] + funnel["outside_band"] + funnel["kept"]


def _events_page(*series, cursor=""):
    return {"events": [{"series_ticker": s, "event_ticker": f"{s}-1"} for s in series],
            "cursor": cursor}


def test_discovery_enumerates_the_live_board_not_the_settled_listing(monkeypatch):
    """Run 3 found THREE series enumerating from settled markets, because
    whichever series has the most CLOSED markets crowds out the rest. Open
    events are an enumeration: every listing series appears once."""
    calls = []

    def fake_get(url):
        calls.append(url)
        return _events_page("KXBTCD", "KXBTCD", "KXHIGHNY")

    monkeypatch.setattr(mp.xl, "_get", fake_get)
    assert mp.discover_series(1, 0.0) == ["KXBTCD", "KXHIGHNY"]
    assert "/events?status=open" in calls[0], calls
    assert "settled" not in calls[0]


def test_discovery_falls_back_to_the_event_ticker_prefix(monkeypatch):
    """`series_ticker` is what we want; the prefix is what we accept when the
    payload omits it, rather than dropping the series silently."""
    monkeypatch.setattr(mp.xl, "_get",
                        lambda url: {"events": [{"event_ticker": "KXHIGHNY-25AUG29"}]})
    assert mp.discover_series(1, 0.0) == ["KXHIGHNY"]


def test_discovery_does_not_apply_the_volume_filter(monkeypatch):
    """min_vol belongs to the HISTORY query. Applying it to discovery would drop
    a thin-but-live series before its history was ever looked at."""
    monkeypatch.setattr(mp.xl, "_get", lambda url: _events_page("KXTHIN"))
    assert mp.discover_series(1, min_vol=1e9) == ["KXTHIN"]


def test_discovery_stops_when_the_cursor_runs_out(monkeypatch):
    pages = [_events_page("A", cursor="c1"), _events_page("B", cursor="")]
    monkeypatch.setattr(mp.xl, "_get", lambda url: pages.pop(0) if pages else None)
    assert sorted(mp.discover_series(10, 0.0)) == ["A", "B"]


# ===========================================================================
# Recurrence ranking (D3, operator decision 2026-08-30) — added after run 4
# ranked 2,441 correctly-enumerated series by the wrong quantity
# ===========================================================================


def test_ranking_prefers_the_series_that_actually_settles_often(monkeypatch):
    """Run 4's top 40 by concurrent open events came back KXNFLWINS 0 settled,
    KXNBAWINS 0 settled. Recurrence is not concurrency."""
    monkeypatch.setattr(mp, "fetch_settled", lambda *a, **k: [
        _m(f"KXBTCD-{i}", f"KXBTCD-{i}-A", i, "yes") for i in range(9)
    ] + [_m("KXHIGHNY-1", "KXHIGHNY-1-A", 99, "no")])
    assert mp.rank_by_recurrence(["KXNFLWINS", "KXHIGHNY", "KXBTCD"], 1, 0.0) == [
        "KXBTCD", "KXHIGHNY", "KXNFLWINS"
    ]


def test_a_series_absent_from_the_settled_sample_is_kept_at_the_back(monkeypatch):
    """Absence means 'did not appear in this sample', not 'never settles'.
    Dropping it would quietly narrow the universe on weak evidence."""
    monkeypatch.setattr(mp, "fetch_settled", lambda *a, **k: [])
    assert mp.rank_by_recurrence(["A", "B"], 1, 0.0) == ["A", "B"]


def test_ranking_never_invents_a_series_the_enumerator_did_not_list(monkeypatch):
    """The settled listing RANKS; it does not enumerate. A retired series with
    deep history but nothing currently listed is not tradeable."""
    monkeypatch.setattr(mp, "fetch_settled", lambda *a, **k: [
        _m("KXRETIRED-1", "KXRETIRED-1-A", 1, "yes"),
    ])
    assert mp.rank_by_recurrence(["KXLIVE"], 1, 0.0) == ["KXLIVE"]
