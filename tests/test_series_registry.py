"""The series registry: the manifest ledger, the arrival observer, and the report that joins
them (`kalshi_bot/registry/`, `scripts/series_registry_review.py`).

The load-bearing test here is `test_matches_pre_registry_behaviour`. The registry replaced a
frozenset that gates a LIVE canary's universe, so the one thing that must be provable is that
no series changed side in the move. Everything else is new surface.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kalshi_bot import registry
from kalshi_bot.mmsell import universe
from kalshi_bot.mmsell.market_types import SERIES_TYPES

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import series_registry_review as report  # noqa: E402

# --- the manifest --------------------------------------------------------------------------

def test_manifest_parses_and_every_state_is_known():
    rows = registry.rows()
    assert rows, "the manifest is empty"
    assert {r["state"] for r in rows} <= registry.MANIFEST_STATES


def test_manifest_rows_carry_a_defined_reason():
    """A row's `reason` has to resolve to prose, or the ledger records a decision nobody can
    read back — which is the whole failure the registry exists to fix."""
    for r in registry.rows():
        code = r.get("reason")
        if code:
            assert registry.reason_text(code) != code, f"{r['series']}: undefined reason {code}"


def test_grandfathered_rows_are_unreviewed_and_are_the_backlog():
    """PR #338's seed proved we have DATA about a contract, never that anyone read how it
    settles. Recording that honestly is the point of the two-part bar: the rows trade live and
    they are simultaneously the audit debt."""
    debt = set(registry.unreviewed_graduated())
    for r in registry.rows():
        if r.get("reason") == "grandfathered-pr338":
            assert r["rules_reviewed_at"] is None
            assert r["series"] in debt


# --- states and admission ------------------------------------------------------------------

def test_unclassified_is_a_legacy_spelling_of_identified():
    """Deployed book specs and `mmsell_live_min_tier` use the old name; if it stopped parsing,
    a book that reads as gated would silently admit everything."""
    assert registry.canonical_state("unclassified") == registry.IDENTIFIED
    assert registry.canonical_state("UNCLASSIFIED") == registry.IDENTIFIED
    assert registry.canonical_state("nonsense") is None
    assert registry.canonical_state(None) is None


def test_taxonomy_gap_outranks_a_manifest_row():
    """A series with no market-type entry cannot be graduated by a stray prefix row: we would
    still not know how it settles."""
    assert registry.state_of("KXTOTALLYMADEUPSERIES") == registry.IDENTIFIED


def test_a_book_naming_nothing_is_unaffected():
    for s in ("KXMLBGAME", "KXTOTALLYMADEUPSERIES"):
        assert registry.admits(s, None) is True


def test_barred_refuses_even_a_book_that_opted_into_nothing(monkeypatch):
    """A veto that only binds books which opted in is decorative."""
    _install_manifest(monkeypatch, [
        {"series": "KXMLBGAME", "state": "barred", "rules_reviewed_at": None},
    ])
    assert registry.state_of("KXMLBGAME") == registry.BARRED
    assert registry.admits("KXMLBGAME", None) is False
    assert registry.admits("KXMLBGAME", "identified") is False


def test_longest_prefix_wins_so_one_series_can_be_barred_under_a_graduated_family(monkeypatch):
    _install_manifest(monkeypatch, [
        {"series": "KXMLB", "state": "graduated", "rules_reviewed_at": "2026-09-06"},
        {"series": "KXMLBGAME", "state": "barred", "rules_reviewed_at": "2026-09-06"},
    ])
    assert registry.state_of("KXMLBSPREAD") == registry.GRADUATED
    assert registry.state_of("KXMLBGAME") == registry.BARRED


def test_barred_survives_a_taxonomy_gap(monkeypatch):
    """An explicit refusal must not be rescued by the series falling out of the taxonomy."""
    _install_manifest(monkeypatch, [
        {"series": "KXNOSUCHTAXONOMY", "state": "barred", "rules_reviewed_at": None},
    ])
    assert registry.state_of("KXNOSUCHTAXONOMYX") == registry.BARRED


def test_duplicate_series_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="twice"):
        _install_manifest(monkeypatch, [
            {"series": "KXMLBGAME", "state": "graduated"},
            {"series": "KXMLBGAME", "state": "barred"},
        ])


def test_unknown_state_is_rejected(monkeypatch):
    with pytest.raises(ValueError, match="unknown state"):
        _install_manifest(monkeypatch, [{"series": "KXMLBGAME", "state": "probably_fine"}])


# --- the thing that must not have changed ---------------------------------------------------

#: The 138 series PR #338 graduated, read back out of the shipped manifest. Pinned as data
#: rather than re-derived so this test fails loudly if a future PR edits the manifest without
#: meaning to move the live universe.
def _pr338_graduated() -> frozenset[str]:
    doc = json.loads(registry.MANIFEST_PATH.read_text())
    return frozenset(r["series"] for r in doc["series"]
                     if r.get("reason") == "grandfathered-pr338")


def _pre_registry_tier(series: str) -> str:
    """PR #338's `tier_of`, reproduced exactly, with its bottom rung renamed to the registry's
    spelling. This is the behaviour the canary was armed against."""
    from kalshi_bot.mmsell.market_types import UNCLASSIFIED as UNCLASSIFIED_TYPE
    from kalshi_bot.mmsell.market_types import classify
    s = (series or "").upper()
    if classify(s) == UNCLASSIFIED_TYPE:
        return registry.IDENTIFIED
    if any(s.startswith(p) for p in _pr338_graduated()):
        return registry.GRADUATED
    return registry.IN_REVIEW


def test_matches_pre_registry_behaviour():
    """No series changed side when the frozenset became a manifest.

    The registry gates which series a LIVE book may enter. A silent widening here puts real
    money into contracts nobody reviewed; a silent narrowing stops the canary collecting. So
    every taxonomy prefix, every graduated series, and a batch of near-misses are checked
    against the pre-registry implementation at every minimum a book can name."""
    samples = {p for p, _, _ in SERIES_TYPES} | set(_pr338_graduated())
    samples |= {p + "X" for p in sorted(samples)[:80]}
    samples |= {"KXNCAAFSPREAD", "KXEPLTOTAL", "KXWEIRD", "", "kxmlbgame"}
    for s in sorted(samples):
        assert registry.state_of(s) == _pre_registry_tier(s), s
        for floor in (None, "unclassified", "identified", "in_review", "graduated"):
            assert registry.admits(s, floor) is universe.admits(s, floor), (s, floor)


def test_mmsell_surface_still_exports_what_config_and_tracker_import():
    assert universe.TIER_ORDER == registry.STATE_ORDER
    assert universe.tier_of("KXMLBGAME") == registry.GRADUATED
    assert universe.UNCLASSIFIED == "unclassified"


# --- arrival observation ---------------------------------------------------------------------

@pytest.fixture
def db_session():
    """In-memory sqlite over the full schema."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from kalshi_bot.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


def test_series_of_matches_the_trackers_derivation():
    from kalshi_bot.registry.observe import series_of
    assert series_of({"ticker": "KXMLBGAME-25SEP06-ATH"}) == "KXMLBGAME"
    assert series_of({"ticker": "X-1"}, {"series_ticker": "kxfoo"}) == "KXFOO"
    assert series_of({"ticker": "", "series_ticker": ""}) == ""


def test_observer_counts_breadth_not_a_running_total(db_session):
    from kalshi_bot.models import SeriesObservation
    from kalshi_bot.registry.observe import SeriesObserver

    obs = SeriesObserver()
    for t in ("KXA-1", "KXA-2", "KXA-3"):
        obs.observe({"ticker": t, "title": "a market"})
    assert obs.flush(db_session) == (1, 1)
    first_seen = db_session.get(SeriesObservation, "KXA").first_seen_at

    # A later cycle offering FEWER markets must not shrink the recorded breadth, and must not
    # accumulate either: this is "how wide has this series ever been", not "how many rows".
    obs = SeriesObserver()
    obs.observe({"ticker": "KXA-1", "title": "a market"})
    assert obs.flush(db_session) == (1, 0)

    row = db_session.get(SeriesObservation, "KXA")
    assert row.markets_seen == 3
    assert row.first_seen_at == first_seen, "arrival date must never be rewritten"


def test_observer_records_the_state_at_first_sighting(db_session):
    from kalshi_bot.models import SeriesObservation
    from kalshi_bot.registry.observe import SeriesObserver

    obs = SeriesObserver()
    obs.observe({"ticker": "KXMLBGAME-1"})
    obs.observe({"ticker": "KXNOSUCHSERIESATALL-1"})
    assert obs.flush(db_session) == (2, 2)
    assert db_session.get(SeriesObservation, "KXMLBGAME").state_at_first_seen == "graduated"
    assert (db_session.get(SeriesObservation, "KXNOSUCHSERIESATALL").state_at_first_seen
            == "identified")


def test_flush_never_raises_into_the_scan():
    """Observation is telemetry. A broken session must delay a review, never stop trading."""
    from kalshi_bot.registry.observe import SeriesObserver

    class Exploding:
        def scalars(self, *a, **k):
            raise RuntimeError("db is down")

        def rollback(self):
            pass

    obs = SeriesObserver()
    obs.observe({"ticker": "KXA-1"})
    assert obs.flush(Exploding()) == (0, 0)


def test_empty_flush_is_free(db_session):
    from kalshi_bot.registry.observe import SeriesObserver
    assert SeriesObserver().flush(db_session) == (0, 0)


# --- the report ------------------------------------------------------------------------------

def test_report_reads_the_same_manifest_the_worker_does():
    """Not a copy — the point of a JSON ledger is that there is one file."""
    assert report.MANIFEST_PATH == registry.MANIFEST_PATH
    assert set(report.load_manifest()) == {r["series"] for r in registry.rows()}


def test_report_state_names_match_the_package():
    assert (report.IDENTIFIED, report.IN_REVIEW, report.GRADUATED) == registry.STATE_ORDER
    assert report.BARRED == registry.BARRED


def test_report_prefix_rule_matches_the_packages():
    m = report.load_manifest()
    for s in ("KXMLBGAME", "KXMLBGAMEX", "KXNOSUCHSERIES"):
        pkg = registry.entry_for(s)
        rep = report.manifest_entry(m, s)
        assert (pkg or {}).get("series") == (rep or {}).get("series"), s


def test_backlog_ranks_live_exposure_first(capsys):
    """A live cell that has barely traded outranks a large paper-only one: the review protects
    real money, and that ordering is the only reason the backlog is usable at 138 rows."""
    manifest = {
        "KXLIVETINY": {"series": "KXLIVETINY", "state": "graduated", "rules_reviewed_at": None},
        "KXPAPERHUGE": {"series": "KXPAPERHUGE", "state": "graduated", "rules_reviewed_at": None},
        "KXREVIEWED": {"series": "KXREVIEWED", "state": "graduated",
                       "rules_reviewed_at": "2026-09-06"},
    }
    activity = {
        "KXLIVETINY": {"settled": 3, "markets": set(), "books": {"a"},
                       "live_books": {"D"}, "live_orders": 2, "pnl": -1.0},
        "KXPAPERHUGE": {"settled": 5000, "markets": set(), "books": {"a"},
                        "live_books": set(), "live_orders": 0, "pnl": -900.0},
    }
    report.report_backlog(manifest, activity, top=10)
    out = capsys.readouterr().out
    assert out.index("KXLIVETINY") < out.index("KXPAPERHUGE")
    # A reviewed series is not debt.
    assert "KXREVIEWED" not in out
    assert "(2; 1 traded by a live book)" in out


def test_arrivals_lists_only_series_no_manifest_row_governs(capsys):
    now = datetime.now(timezone.utc)
    manifest = {"KXKNOWN": {"series": "KXKNOWN", "state": "graduated"}}
    observations = {
        "KXKNOWN": {"series": "KXKNOWN", "first_seen_at": now, "last_seen_at": now,
                    "markets_seen": 4, "sample_ticker": "KXKNOWN-1",
                    "sample_title": "known", "state_at_first_seen": "graduated"},
        "KXBRANDNEW": {"series": "KXBRANDNEW", "first_seen_at": now, "last_seen_at": now,
                       "markets_seen": 9, "sample_ticker": "KXBRANDNEW-1",
                       "sample_title": "a new season", "state_at_first_seen": "identified"},
        "KXOLDER": {"series": "KXOLDER", "first_seen_at": now - timedelta(days=30),
                    "last_seen_at": now, "markets_seen": 2, "sample_ticker": "KXOLDER-1",
                    "sample_title": "older", "state_at_first_seen": "identified"},
    }
    report.report_arrivals(manifest, observations, {}, top=10)
    out = capsys.readouterr().out
    assert "KXKNOWN" not in out
    assert "no manifest row (2;" in out
    # With no activity on either, recency breaks the tie and the newer one leads.
    assert out.index("KXBRANDNEW") < out.index("KXOLDER")


def test_candidates_exclude_graduated_and_barred(capsys):
    manifest = {
        "KXDONE": {"series": "KXDONE", "state": "graduated"},
        "KXNO": {"series": "KXNO", "state": "barred"},
    }
    activity = {s: {"settled": 100, "markets": set(), "books": {"a"},
                    "live_books": set(), "pnl": 0.0}
                for s in ("KXDONE", "KXNO", "KXREADY")}
    report.report_candidates(manifest, activity, min_settled=20, top=10)
    out = capsys.readouterr().out
    assert "KXREADY" in out
    assert "KXDONE" not in out and "KXNO" not in out


def test_report_survives_a_missing_observations_table():
    """The backlog is the section with money behind it and needs no observations at all, so an
    unmigrated database must degrade rather than fail the whole read."""
    class Cur:
        def execute(self, *a, **k):
            raise RuntimeError('relation "series_observations" does not exist')

    assert report.load_observations(Cur()) == {}


# --- helpers ---------------------------------------------------------------------------------

def _install_manifest(monkeypatch, rows: list[dict]) -> None:
    """Swap the manifest for a fixture and clear the module's cache.

    Touches the private cache deliberately: the loader memoizes on purpose (it is read on every
    entry decision) and there is no public way to invalidate it, which is correct for
    production and has to be worked around exactly here."""
    monkeypatch.setattr(registry, "_manifest", None, raising=False)
    monkeypatch.setattr(registry, "_reasons", {}, raising=False)
    payload = json.dumps({"manifest_version": 1, "reasons": {}, "series": rows})

    class FakePath:
        def read_text(self):
            return payload

    monkeypatch.setattr(registry, "MANIFEST_PATH", FakePath())
    registry._load()


def test_assembled_sql_survives_psycopgs_placeholder_parser():
    """`tests/test_ops_script_sql.py` scans string LITERALS, but this script builds its WHERE
    clause from a list at runtime, so the literal it would find is a fragment. psycopg scans the
    WHOLE assembled query — comments included — for placeholders, and a lone `%` aborts the run
    in production. Both branches of `--days` are exercised because they assemble differently."""
    from psycopg._queries import _split_query

    class Cur:
        def execute(self, sql, params=None):
            _split_query(sql.encode(), "format")

        def fetchall(self):
            return []

    report.load_observations(Cur())
    report.load_series_activity(Cur(), 30, 30)
    report.load_series_activity(Cur(), None, 7)


def test_live_exposure_comes_from_the_order_tape_not_from_book_lineage():
    """Regression on the first production run (2026-09-06), which marked 137 of 138 series
    LIVE and so ordered the audit by |P&L| alone.

    The bug: ask which STRATEGIES ever placed a live order, then flag a series if any PAPER
    trade in it came from such a book. Over all time 23-37 books touch a typical series and
    nearly all carry some live lineage, so the flag was always true. A book's live arm and its
    paper arm trade different universes; the flag has to mean money was in THIS series.

    Here `Bpaper` placed a live order in a DIFFERENT series and paper-traded this one. Under the
    old rule that made KXPAPERONLY live. It must not."""
    class Cur:
        def __init__(self):
            self.calls = 0

        def execute(self, sql, params=None):
            self.calls += 1
            self.sql = sql

        def fetchall(self):
            if "paper_trades" in self.sql:
                return [("KXPAPERONLY-1", "Bpaper", 1.0, 1),
                        ("KXREALMONEY-1", "Alive", 1.0, 1)]
            return [("KXREALMONEY-9", "Alive", None)]

    activity = report.load_series_activity(Cur(), None, 30)
    assert activity["KXREALMONEY"]["live_books"] == {"Alive"}
    assert activity["KXREALMONEY"]["live_orders"] == 1
    assert activity["KXPAPERONLY"]["live_books"] == set()
    assert activity["KXPAPERONLY"]["live_orders"] == 0


def test_a_series_with_live_orders_but_no_settled_history_still_appears():
    """Real money can be in a series we have no settled paper history for. It must not vanish
    from the exposure read just because the paper join found nothing."""
    class Cur:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return [] if "paper_trades" in self.sql else [("KXFRESH-1", "Alive", None)]

    activity = report.load_series_activity(Cur(), None, 30)
    assert activity["KXFRESH"]["live_orders"] == 1
    assert activity["KXFRESH"]["settled"] == 0


# --- the hook has to be on the path production actually runs -----------------------------------

def test_arrival_detection_is_hooked_into_the_mmsell_tracker_sweep():
    """The registry's observation ledger was DEAD IN PRODUCTION when it shipped.

    `SeriesObserver` was hooked into `ScannerService.run_once`, which `main` reaches only
    through `_run_cycle` — the `else` branch after live/weather/mmsell/evo. Production runs
    `BOT_MODE=live`, so it could never fire. `main._run_cycle`'s own comment warns about exactly
    this ("a hook placed there silently never runs under BOT_MODE=live/..., which is how the
    first deploy of the gate evaluator recorded nothing"), and the symptom is silent: the
    registry review printed `observed series: 0` and read like a table awaiting its migration.

    So the hook must live on a sweep the live path actually performs. `MmSellTracker.run_once`
    is one — it is called from `_run_live_cycle`, `_run_weather_cycle` and `_run_mmsell_cycle`.
    """
    import inspect

    from kalshi_bot.mmsell import tracker as mmsell_tracker

    src = inspect.getsource(mmsell_tracker.MmSellTracker.run_once)
    assert "SeriesObserver()" in src, "the tracker no longer creates an observer"
    assert "observer.observe(" in src, "the tracker no longer observes the listing sweep"
    assert "observer.flush(session)" in src, "the tracker no longer persists what it observed"


def test_every_bot_mode_cycle_reaches_a_sweep_that_observes():
    """A structural guard on the class of bug above, not just on this instance.

    `main` dispatches one cycle function per BOT_MODE. Each of the modes that scan Kalshi must
    reach a sweep carrying the observer, or the registry goes blind again for that mode without
    anything failing."""
    import inspect

    from kalshi_bot import main as bot_main
    from kalshi_bot.mmsell.tracker import MmSellTracker

    tracker_observes = "observer.flush(session)" in inspect.getsource(MmSellTracker.run_once)
    assert tracker_observes

    for fn_name in ("_run_live_cycle", "_run_weather_cycle", "_run_mmsell_cycle"):
        src = inspect.getsource(getattr(bot_main, fn_name))
        assert "tracker.run_once(" in src, (
            f"{fn_name} no longer runs a tracker sweep — arrival detection may be dead for "
            "this BOT_MODE, which fails silently")


def test_the_observer_sees_a_series_the_tracker_would_skip(db_session):
    """`_skip_series` is a trading-scope decision. Observing after it would hide precisely the
    series a reviewer most needs to see: the ones we have decided not to trade and never
    revisited."""
    from kalshi_bot.models import SeriesObservation
    from kalshi_bot.registry.observe import SeriesObserver

    obs = SeriesObserver()
    obs.observe({"ticker": "KXSKIPPED-1", "title": "a declined market"},
                {"series_ticker": "KXSKIPPED"})
    assert obs.flush(db_session) == (1, 1)
    assert db_session.get(SeriesObservation, "KXSKIPPED") is not None


# --- arrivals ordering -------------------------------------------------------------------------

def _obs(series, days_ago=0, mkts=1):
    now = datetime.now(timezone.utc)
    return {"series": series, "first_seen_at": now - timedelta(days=days_ago),
            "last_seen_at": now, "markets_seen": mkts, "sample_ticker": f"{series}-1",
            "sample_title": series.title(), "state_at_first_seen": "identified"}


def _act(settled=0, books=0):
    return {"settled": settled, "markets": set(), "books": {f"b{i}" for i in range(books)},
            "live_books": set(), "live_orders": 0, "pnl": 0.0}


def test_arrivals_put_what_we_are_trading_above_what_merely_appeared(capsys):
    """The first populated production run (2026-09-06) is the regression here.

    `first_seen_at` is not backfilled, so on the first cycle after deploy all 3,845 rows shared
    a timestamp and a recency sort carried no information. `KXUAEPLTOTAL` — 22 settled trades
    across 8 books, no manifest row — printed NINTH, below `GOVPARTY*` rows nothing had ever
    touched. Recency is not what makes an arrival urgent; exposure is."""
    manifest = {}
    observations = {s: _obs(s) for s in ("GOVPARTYAL", "GOVPARTYAR", "KXUAEPLTOTAL")}
    activity = {"KXUAEPLTOTAL": _act(settled=22, books=8)}
    report.report_arrivals(manifest, observations, activity, top=10)
    out = capsys.readouterr().out
    assert out.index("KXUAEPLTOTAL") < out.index("GOVPARTYAL")
    assert "1 already traded" in out


def test_recency_still_breaks_ties_so_a_genuinely_new_listing_stays_visible(capsys):
    """Activity-first must not bury a brand-new series under thousands of untouched old ones:
    they all tie at zero activity, and the date decides."""
    manifest = {}
    observations = {"KXOLD": _obs("KXOLD", days_ago=90), "KXNEWTODAY": _obs("KXNEWTODAY")}
    report.report_arrivals(manifest, observations, {}, top=10)
    out = capsys.readouterr().out
    assert out.index("KXNEWTODAY") < out.index("KXOLD")


def test_a_traded_series_outranks_a_wider_untraded_one(capsys):
    """Breadth is a weaker signal than our own money being in it."""
    manifest = {}
    observations = {"KXWIDE": _obs("KXWIDE", mkts=500), "KXTRADED": _obs("KXTRADED", mkts=2)}
    report.report_arrivals(manifest, observations, {"KXTRADED": _act(settled=1, books=1)}, top=10)
    out = capsys.readouterr().out
    assert out.index("KXTRADED") < out.index("KXWIDE")


def test_arrivals_ordering_survives_a_missing_arrival_date(capsys):
    """A row written before the column existed, or a partial upsert, must not crash the sort —
    the queue is read on every cycle and one bad row would take the whole section down."""
    manifest = {}
    observations = {"KXNULL": _obs("KXNULL"), "KXOK": _obs("KXOK")}
    observations["KXNULL"]["first_seen_at"] = None
    report.report_arrivals(manifest, observations, {}, top=10)
    out = capsys.readouterr().out
    assert "KXNULL" in out and "KXOK" in out
    assert out.index("KXOK") < out.index("KXNULL")


def test_a_series_the_manifest_governs_never_appears_in_arrivals(capsys):
    """Unchanged by the reordering: arrivals is 'no manifest row governs it', not 'unreviewed'."""
    manifest = {"KXKNOWN": {"series": "KXKNOWN", "state": "graduated"}}
    observations = {"KXKNOWN": _obs("KXKNOWN"), "KXNEW": _obs("KXNEW")}
    report.report_arrivals(manifest, observations, {"KXKNOWN": _act(settled=99, books=9)}, top=10)
    out = capsys.readouterr().out
    assert "KXKNOWN" not in out
    assert "KXNEW" in out
