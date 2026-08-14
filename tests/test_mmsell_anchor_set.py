"""The mmsell ANCHOR SET — three exit/entry mechanics forward-tested as paper books
(docs/MMSELL_ANCHOR_SET.md). Every anchor book sits on the mmsell10 base so ENTRY is held
constant and each varies exactly one mechanic; mmsell10 itself is the control.

What these tests pin, in order of what would silently break the experiment:
  * the CONTROL must stay inert — mmsell10 (and every pre-anchor book) must gain no stop, no vol
    gate and no mirror leg, or the A/B has no baseline.
  * the stop triggers on the yes-BID with a K-consecutive confirm, never on a wide mid/ask.
  * the vol gate does NOT fire on thin history, so a gated book differs from the control only by
    the entries the gate actually rejected.
  * a strangle's two legs are mutually exclusive, and a lone tail is never entered as a "strangle".
  * the strangle pairing gate reads the LIVE nested-market payload shape (`*_dollars`), not just
    the documented integer-cent keys — reading only the latter is what kept A5 at zero trades.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kalshi_bot import repository as repo
from kalshi_bot.mmsell.tracker import MmSellTracker
from kalshi_bot.models import Base, MmSellSettlementMeta, PaperTrade


def _settings(settings):
    settings.bot_mode = "mmsell"
    return settings


def _book(**over):
    b = {"tag": "mmsellX", "lo": 5.0, "hi": 10.0, "htcmin": 1.0, "htcmax": 336.0,
         "skip": [], "only": [], "maxyes": 7.0,
         "stopl": None, "stopk": 2, "volw": None, "volv": None, "strangle": False}
    b.update(over)
    return b


# ------------------------------------------------------- config / control integrity

def test_anchor_books_parse_with_their_mechanics(settings):
    by_tag = {v["tag"]: v for v in settings.mmsell_variant_list}
    for tag in ("mmsellA1", "mmsellA2", "mmsellA3"):
        # A1-A3 RETIRED 2026-08-12 — the gate failed on the half that mattered: the stop
        # fires on 52% of positions and makes the 5th-pctile tail WORSE than holding
        # (A1 -4.16c/trade vs the mmsell10 control's +3.14c; p5 -19.0 vs +5.0). Asserted as
        # absence so a re-add has to argue with a test. See docs/MMSELL_ANCHOR_SET.md.
        assert tag not in by_tag, "retired stop-loss book is configured again"
    assert by_tag["mmsellA4"]["volw"] == 6 and by_tag["mmsellA4"]["volv"] == 6.0
    assert by_tag["mmsellA5"]["strangle"] is True
    # the SURVIVING anchors share the mmsell10 entry, so only the mechanic differs
    for tag in ("mmsellA4", "mmsellA5"):
        assert (by_tag[tag]["lo"], by_tag[tag]["hi"], by_tag[tag]["maxyes"]) == (5.0, 10.0, 7.0)


def test_control_and_legacy_books_carry_no_anchor_mechanic(settings):
    """The baseline must stay a pure hold-to-settlement book, or every anchor comparison is void."""
    for v in settings.mmsell_variant_list:
        if v["tag"].startswith("mmsellA"):
            continue
        assert v["stopl"] is None, f"{v['tag']} grew a stop"
        assert v["volw"] is None and v["volv"] is None, f"{v['tag']} grew a vol gate"
        assert v["strangle"] is False, f"{v['tag']} grew a strangle leg"
    assert settings.mmsell_book_by_tag("mmsell10")["stopl"] is None


# --------------------------------------------------------------- volatility entry gate

def test_vol_gate_blocks_a_market_whose_tape_already_moved(monkeypatch, settings):
    monkeypatch.setattr("kalshi_bot.repository.recent_candidate_mids",
                        lambda *_a, **_k: [6.0, 7.0, 12.0, 13.0])       # range 7 >= 6
    assert MmSellTracker._vol_gate_blocks(None, _book(volw=6, volv=6), "T") is True


def test_vol_gate_admits_a_calm_market(monkeypatch, settings):
    monkeypatch.setattr("kalshi_bot.repository.recent_candidate_mids",
                        lambda *_a, **_k: [6.0, 7.0, 6.0, 8.0])          # range 2 < 6
    assert MmSellTracker._vol_gate_blocks(None, _book(volw=6, volv=6), "T") is False


def test_vol_gate_does_not_fire_on_thin_history(monkeypatch, settings):
    """A newly in-band market has no tape. The gate must pass it through exactly as the control
    would, so the gated book differs ONLY by entries the gate genuinely rejected."""
    monkeypatch.setattr("kalshi_bot.repository.recent_candidate_mids",
                        lambda *_a, **_k: [6.0, 40.0])                   # huge range, but n=2
    assert MmSellTracker._vol_gate_blocks(None, _book(volw=6, volv=6), "T") is False


def test_vol_gate_is_a_noop_without_the_spec(monkeypatch, settings):
    def _boom(*_a, **_k):
        raise AssertionError("must not query history for a book with no vol gate")
    monkeypatch.setattr("kalshi_bot.repository.recent_candidate_mids", _boom)
    assert MmSellTracker._vol_gate_blocks(None, _book(), "T") is False


def test_vol_gate_fails_soft_and_enters(monkeypatch, settings):
    monkeypatch.setattr("kalshi_bot.repository.recent_candidate_mids",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db down")))
    assert MmSellTracker._vol_gate_blocks(None, _book(volw=6, volv=6), "T") is False


# ------------------------------------------------------------------------- strangle
#
# Every case below runs against BOTH nested-market payload shapes. The live events endpoint
# sends only the `_dollars` strings; the integer-cent keys these tests originally used exist
# in the docs but not in the live response. Testing one shape is what let A5 ship reading
# `mk.get("yes_bid")` -> None on every market -> gate always False -> zero trades, ever.

_SHAPES = ("cents", "dollars")


def _mk(mid, shape):
    yb, ya = mid - 1, mid + 1
    if shape == "cents":
        return {"yes_bid": yb, "yes_ask": ya}
    return {"yes_bid_dollars": f"{yb / 100:.2f}", "yes_ask_dollars": f"{ya / 100:.2f}"}


def _ev(*mids, shape="cents"):
    return {"markets": [_mk(m, shape) for m in mids]}


@pytest.mark.parametrize("shape", _SHAPES)
def test_strangle_requires_both_tails_in_one_event(shape):
    # a high strike (yes ~5c) AND a low strike (yes ~95c) -> a real strangle exists
    assert MmSellTracker._event_has_both_tails(_ev(5, 95, 50, shape=shape), 7.0) is True


@pytest.mark.parametrize("shape", _SHAPES)
def test_strangle_rejects_an_event_with_only_the_cheap_yes_tail(shape):
    """A lone tail is an ordinary mmsell trade. Entering it as a 'strangle' would silently make
    A5 a duplicate of mmsell10 and destroy the low-volatility pairing the thesis rests on."""
    assert MmSellTracker._event_has_both_tails(_ev(5, 4, 50, shape=shape), 7.0) is False


@pytest.mark.parametrize("shape", _SHAPES)
def test_strangle_rejects_an_event_with_only_the_cheap_no_tail(shape):
    assert MmSellTracker._event_has_both_tails(_ev(95, 96, 50, shape=shape), 7.0) is False


@pytest.mark.parametrize("shape", _SHAPES)
def test_strangle_respects_the_price_ceiling(shape):
    # yes 12c / 88c are both outside a 7c cap -> neither counts as a cheap tail
    assert MmSellTracker._event_has_both_tails(_ev(12, 88, shape=shape), 7.0) is False
    assert MmSellTracker._event_has_both_tails(_ev(12, 88, shape=shape), 15.0) is True


@pytest.mark.parametrize("shape", _SHAPES)
def test_strangle_tolerates_missing_quotes(shape):
    ev = {"markets": [{"yes_bid": None, "yes_ask": None},
                      _mk(5, shape), _mk(95, shape)]}
    assert MmSellTracker._event_has_both_tails(ev, 7.0) is True


def test_strangle_reads_the_live_events_payload_shape():
    """The A5 regression, stated directly: the live payload the scan actually holds carries
    `yes_bid_dollars`/`yes_ask_dollars` and NO integer-cent keys. A gate that reads the cent
    keys raw sees None everywhere and is silently always-False -- which is why mmsellA5 sat at
    zero rows from the day it shipped rather than accruing slowly as its thesis predicted."""
    live_event = {"markets": [
        {"ticker": "KX-HI", "yes_bid_dollars": "0.04", "yes_ask_dollars": "0.06"},
        {"ticker": "KX-LO", "yes_bid_dollars": "0.94", "yes_ask_dollars": "0.96"},
    ]}
    assert all("yes_bid" not in mk for mk in live_event["markets"])  # the shape that broke it
    assert MmSellTracker._event_has_both_tails(live_event, 7.0) is True


def test_strangle_legs_are_mutually_exclusive():
    """The structural claim: the cheap-YES leg loses only if the market settles YES, the cheap-NO
    leg only if it settles NO. One settlement can never lose both."""
    for settle_yes in (True, False):
        upper_lost = settle_yes            # sold the YES tail on a high strike
        lower_lost = not settle_yes        # sold the NO tail on a low strike
        assert not (upper_lost and lower_lost)


# ------------------------------------------------- strangle: one leg per side per event
#
# `_event_has_both_tails` only certifies a pair exists SOMEWHERE among an event's markets.
# On a multi-strike ladder (NFL spread/total...) several markets can independently clear the
# SAME side's band, and without a per-side cap every one of them opened its own leg -- same-side
# legs on one ladder are positively correlated (a bad game result moves several strikes
# together), not the mutually exclusive pair the thesis requires. Found 2026-08-14 once NFL
# supply made the ladder shape common enough to see: one event took four cheap-NO legs and zero
# cheap-YES legs.

def _sqlite_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


@pytest.fixture
def db_session():
    session = _sqlite_session()
    yield session
    session.close()


def _seed_leg(session, *, ticker, event_ticker, strategy, side):
    session.add(MmSellSettlementMeta(
        market_ticker=ticker, event_ticker=event_ticker, series_ticker="KXTEST",
        close_time=dt.datetime(2026, 8, 14, tzinfo=dt.timezone.utc),
    ))
    session.add(PaperTrade(market_ticker=ticker, strategy=strategy, side=side, status="open"))
    session.commit()


def test_event_has_strangle_leg_true_after_a_leg_is_recorded(db_session):
    _seed_leg(db_session, ticker="KXEV-HI", event_ticker="KXEV", strategy="mmsellA5", side="no")
    assert repo.event_has_strangle_leg(db_session, "mmsellA5", "KXEV", "no") is True


def test_event_has_strangle_leg_false_for_a_fresh_event(db_session):
    assert repo.event_has_strangle_leg(db_session, "mmsellA5", "KXEV", "no") is False


def test_event_has_strangle_leg_is_side_specific(db_session):
    """A 'no' leg already open must not block the OPPOSITE side -- the mirror leg is exactly the
    trade that completes the pair and must still be allowed in."""
    _seed_leg(db_session, ticker="KXEV-HI", event_ticker="KXEV", strategy="mmsellA5", side="no")
    assert repo.event_has_strangle_leg(db_session, "mmsellA5", "KXEV", "yes") is False


def test_event_has_strangle_leg_is_strategy_specific(db_session):
    """A different book's leg on the same event must not block mmsellA5 -- each anchor book's
    exposure is independent."""
    _seed_leg(db_session, ticker="KXEV-HI", event_ticker="KXEV", strategy="mmsell10", side="no")
    assert repo.event_has_strangle_leg(db_session, "mmsellA5", "KXEV", "no") is False


def test_event_has_strangle_leg_true_even_after_the_leg_settles(db_session):
    """Dedup is against the event's own outcome, not against currently-held risk: a leg that has
    already settled or stopped still means this event already got a 'no' leg."""
    session = db_session
    session.add(MmSellSettlementMeta(
        market_ticker="KXEV-HI", event_ticker="KXEV", series_ticker="KXTEST",
        close_time=dt.datetime(2026, 8, 14, tzinfo=dt.timezone.utc),
    ))
    session.add(PaperTrade(market_ticker="KXEV-HI", strategy="mmsellA5", side="no",
                           status="settled", pnl=0.06))
    session.commit()
    assert repo.event_has_strangle_leg(session, "mmsellA5", "KXEV", "no") is True


def test_strangle_leg_taken_true_when_a_leg_already_exists(db_session):
    _seed_leg(db_session, ticker="KXEV-HI", event_ticker="KXEV", strategy="mmsellA5", side="no")
    assert MmSellTracker._strangle_leg_taken(db_session, "mmsellA5", "KXEV", "no") is True


def test_strangle_leg_taken_false_for_a_fresh_event(db_session):
    assert MmSellTracker._strangle_leg_taken(db_session, "mmsellA5", "KXEV", "no") is False


def test_strangle_leg_taken_is_a_noop_without_an_event_ticker(monkeypatch):
    """No event key -> can't dedup -> fail OPEN like the other anchor-set gates, and don't even
    query for it."""
    def _boom(*_a, **_k):
        raise AssertionError("must not query without an event_ticker")
    monkeypatch.setattr("kalshi_bot.repository.event_has_strangle_leg", _boom)
    assert MmSellTracker._strangle_leg_taken(None, "mmsellA5", "", "no") is False
    assert MmSellTracker._strangle_leg_taken(None, "mmsellA5", None, "no") is False


def test_strangle_leg_taken_fails_soft_and_enters(monkeypatch):
    monkeypatch.setattr(
        "kalshi_bot.repository.event_has_strangle_leg",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    assert MmSellTracker._strangle_leg_taken(None, "mmsellA5", "KXEV", "no") is False
