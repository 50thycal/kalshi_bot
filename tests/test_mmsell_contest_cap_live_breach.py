"""The two defects the 2026-09-06 live canary found, pinned against real tickers.

`Emmsell10` traded real money for 69 minutes and breached its declared cap of one
position per contest on three separate contests. Both causes are here, both keyed
off the ACTUAL market tickers and timings production produced — not invented
shapes, because invented shapes are what let these through the first time.

The audit script (`scripts/mmsell_contest_cap_audit.py`) calls the same
`contest_key_of` the tracker does, deliberately, so that the check cannot disagree
with the mechanism. The cost of that choice, learned here: when the key is wrong
the audit is wrong the same way, and it under-reported the breach it caught (2
positions on ATL/PHI when the book held 5). Defect 1's test therefore fixes the
key for both at once.
"""

from __future__ import annotations

import pytest

from kalshi_bot.mmsell.regimes import contest_key_of

# The exact live footprint on Braves/Phillies at 19:24Z, from `live_orders`.
ATLPHI = [
    "KXMLBTOTAL-26SEP061310ATLPHI-13",
    "KXMLBGAME-26SEP061310ATLPHI-ATL",
    "KXMLBHR-26SEP061310ATLPHI-ATLRACUNA13-1",
    "KXMLBHR-26SEP061310ATLPHI-PHITTURNER7-1",
    "KXMLBHR-26SEP061310ATLPHI-ATLARILEY27-1",
]


# --- defect 1: the key split one game into several "contests" ---------------


def test_every_listing_on_one_game_shares_ONE_contest_key():
    """The breach that mattered most, because it was invisible. Player-prop tickers
    carry a fourth segment (the batter), `event_ticker_of` strips only the last
    one, so the player rode along INSIDE the contest token and every batter became
    his own contest. The live book held five positions on this game under a cap of
    one."""
    keys = {contest_key_of(t) for t in ATLPHI}

    assert keys == {"MLB:26SEP061310ATLPHI"}, (
        f"one game must be one key; got {sorted(keys)}"
    )


def test_a_four_segment_player_prop_keys_to_the_GAME_not_the_player():
    assert contest_key_of("KXMLBHR-26SEP061310ATLPHI-ATLRACUNA13-1") == (
        contest_key_of("KXMLBTOTAL-26SEP061310ATLPHI-13")
    )


def test_the_tennis_and_cubs_breaches_collapse_too():
    """The other two contests the live book doubled up on."""
    assert contest_key_of("KXATPMATCH-26SEP06PAUALC-PAU") == \
        contest_key_of("KXATPEXACTMATCH-26SEP06PAUALC-PAU31")
    assert contest_key_of("KXMLBGAME-26SEP061340CHCMIA-CHC") == \
        contest_key_of("KXMLBSPREAD-26SEP061340CHCMIA-MIA5") == \
        contest_key_of("KXMLBHR-26SEP061340CHCMIA-CHCCKELLY15-1")


def test_UNGROUPED_regimes_are_byte_identical_to_before():
    """The fix must not widen grouping. Outside CONTEST_GROUPED_REGIMES the key is
    still the event ticker, so two econ prints sharing a month stay independent —
    the failure the original docstring warned about, where a cap refuses good
    trades invisibly."""
    assert contest_key_of("KXPAYROLLS-26SEP") == "KXPAYROLLS-26SEP"
    assert contest_key_of("KXBTCD-26AUG1717-B1") == "KXBTCD-26AUG1717"
    assert contest_key_of("KXBTCD-26AUG1717-B1") != contest_key_of("KXETHD-26AUG1717-B1")


def test_two_different_games_still_key_apart():
    """Collapsing to the first token must not over-group either."""
    assert contest_key_of("KXMLBGAME-26SEP061310ATLPHI-ATL") != \
        contest_key_of("KXMLBGAME-26SEP061340CHCMIA-CHC")


# --- defect 2: the mutually-exclusive exemption did not belong here ---------


def _cap_source() -> str:
    import inspect

    from kalshi_bot.mmsell.tracker import MmSellTracker

    src = inspect.getsource(MmSellTracker._settlement_cap_blocks)
    return src.split("# Fourth cap: the CONTEST", 1)[1]


def test_the_contest_cap_does_NOT_exempt_mutually_exclusive_events():
    """`mutually_exclusive` asserts that at most one rung of ONE EVENT resolves YES.
    The rung cap is entitled to that exemption; this cap groups ACROSS events, where
    the property says nothing.

    Carrying it here inverted the cap exactly where it mattered: the game-winner
    market is the most game-correlated contract listed AND is always mutually
    exclusive, so the exemption waved through precisely the entry the cap exists to
    refuse. All three live breaches were KXMLBGAME/KXATPMATCH second legs.
    """
    body = _cap_source()

    assert "not mutually_exclusive" not in body, (
        "the CONTEST cap must not exempt mutually-exclusive events"
    )
    assert "if cap_n is not None:" in body


def test_the_RUNG_cap_KEEPS_its_exemption():
    """The fix is scoped. Within one event a disjoint bucket ladder really is a
    hedge, and removing that exemption would refuse trades that are fine."""
    import inspect

    from kalshi_bot.mmsell.tracker import MmSellTracker

    src = inspect.getsource(MmSellTracker._settlement_cap_blocks)
    rung = src.split("# Fourth cap: the CONTEST", 1)[0]

    assert "not mutually_exclusive" in rung, (
        "the within-event rung cap must still exempt mutually-exclusive events"
    )


@pytest.mark.parametrize("second_leg", [
    "KXMLBGAME-26SEP061310ATLPHI-ATL",
    "KXMLBGAME-26SEP061340CHCMIA-CHC",
    "KXATPMATCH-26SEP06PAUALC-PAU",
])
def test_each_live_breach_would_now_be_refused(second_leg, monkeypatch):
    """End to end through the real guard: a book holding one position on a contest
    must refuse a second, EVEN when the candidate's event is mutually exclusive.
    These are the three exact second legs that got through on real money."""
    from collections import Counter

    from kalshi_bot import repository as repo
    from kalshi_bot.mmsell.tracker import MmSellCycleSummary, MmSellTracker

    held = contest_key_of(second_leg)
    monkeypatch.setattr(repo, "open_positions_contest_summary",
                        lambda session, tag, ticker: Counter({held: 1}))
    monkeypatch.setattr(repo, "open_positions_settlement_summary",
                        lambda *a, **k: (0, {}))

    class _S:
        mmsell_settlement_cap_enabled = True
        mmsell_settlement_cap_pct = 0.25
        mmsell_settlement_correlated_regimes_list = []
        mmsell_settlement_event_cap = 5
        mmsell_event_rung_cap_enabled = True
        mmsell_event_rung_cap = 3
        mmsell_contest_cap_enabled = False
        mmsell_contest_cap = 1

    import datetime as dt
    summ = MmSellCycleSummary()

    class _Self:
        _note = staticmethod(lambda *a, **k: None)

    blocked = MmSellTracker._settlement_cap_blocks(
        _Self(), object(), _S(), book_cap=40, tag="Emmsell10", ticker=second_leg,
        close_dt=dt.datetime(2026, 9, 9, tzinfo=dt.timezone.utc),
        series=second_leg.split("-")[0], event_ticker="-".join(second_leg.split("-")[:2]),
        mutually_exclusive=True,          # the property that let these through
        summ=summ, recorder=None, contest_cap=1,
    )

    assert blocked is True, f"{second_leg} must be refused — one position already on {held}"
    assert summ.skipped_contest_cap == 1
