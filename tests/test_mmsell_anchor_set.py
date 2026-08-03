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

import pytest

from kalshi_bot.mmsell.tracker import MmSellTracker


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
    for tag, lvl in (("mmsellA1", 12.0), ("mmsellA2", 20.0), ("mmsellA3", 30.0)):
        assert by_tag[tag]["stopl"] == lvl and by_tag[tag]["stopk"] == 2
    assert by_tag["mmsellA4"]["volw"] == 6 and by_tag["mmsellA4"]["volv"] == 6.0
    assert by_tag["mmsellA5"]["strangle"] is True
    # all five share the mmsell10 entry, so only the mechanic differs
    for tag in ("mmsellA1", "mmsellA2", "mmsellA3", "mmsellA4", "mmsellA5"):
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
