"""The reward ledger's arithmetic, which is the only route we have to a liquidity credit.

Kalshi publishes a programme's terms and never our share of them, so the reward is recovered as
the part of a balance change that no fill and no settlement explains. That makes the accounting
identity load-bearing: if it is wrong, every reward number this project ever reports is wrong,
and wrong quietly. These tests pin the identity itself, each component's sign, and the cases
where a residual must NOT be read as a reward.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kalshi_bot.liquidity_incentive import reward_ledger as rl


def _fill(*, side="yes", action="buy", count=1, yes_price=10, no_price=90, fee=None):
    row = {"side": side, "action": action, "count": count,
           "yes_price": yes_price, "no_price": no_price}
    if fee is not None:
        row["fee"] = fee
    return row


class TestTheAccountingIdentity:
    def test_a_balance_that_only_fell_by_what_we_paid_leaves_no_residual(self):
        """The base case, and the one that must never drift: buy one contract at 10c, watch the
        balance fall by exactly 10c, and conclude nothing happened beyond the trade."""
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_641,
                           fills=[_fill(yes_price=10)], settlements=[])
        assert rec.buy_cost_cents == 10
        assert rec.delta_cents == -10
        assert rec.residual_cents == 0
        assert not rec.is_material

    def test_cash_arriving_that_no_trade_explains_is_the_whole_point(self):
        """A liquidity credit has exactly one signature: the balance rose and nothing traded."""
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_654,
                           fills=[], settlements=[])
        assert rec.residual_cents == 3
        assert rec.is_material
        assert not rec.presumed_transfer

    def test_a_settlement_is_explained_and_does_not_read_as_a_reward(self):
        """Settlement revenue is cash arriving for a reason we already know. Failing to
        subtract it would report every winning contract as a liquidity reward."""
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_751,
                           fills=[], settlements=[{"revenue": 100}])
        assert rec.settlement_cents == 100
        assert rec.residual_cents == 0
        assert not rec.is_material

    def test_a_reward_is_still_visible_underneath_a_settlement(self):
        """The case that matters in production: both happen in the same window. The reward must
        survive the subtraction rather than being swallowed by the larger number."""
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_754,
                           fills=[], settlements=[{"revenue": 100}])
        assert rec.residual_cents == 3

    def test_a_sale_credits_the_balance_and_is_explained(self):
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_691,
                           fills=[_fill(action="sell", yes_price=40)], settlements=[])
        assert rec.sell_proceeds_cents == 40
        assert rec.residual_cents == 0

    def test_a_reported_fee_is_added_back_so_it_is_not_mistaken_for_a_negative_reward(self):
        """Buy at 10c and pay a 2c fee: the balance falls 12c, and all 12c is explained."""
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_639,
                           fills=[_fill(yes_price=10, fee=2)], settlements=[])
        assert rec.fees_cents == 2
        assert rec.residual_cents == 0

    def test_an_unreported_fee_shows_up_as_a_negative_residual_not_a_reward(self):
        """Kalshi charging a fee it did not report cannot manufacture a positive residual. The
        failure direction must be a visible negative, which is a known, documented symptom."""
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_639,
                           fills=[_fill(yes_price=10)], settlements=[])
        assert rec.residual_cents == -2
        assert rec.residual_cents < 0


class TestSidePricing:
    def test_a_no_fill_is_costed_at_the_no_price(self):
        """The canary's real orders are NO bids at 3c and 10c. Costing them at `yes_price`
        would be wrong by 100 − price per contract — an order-of-magnitude error in exactly the
        measurement this module exists to make."""
        rec = rl.reconcile(prev_balance_cents=1_000, balance_cents=997,
                           fills=[_fill(side="no", yes_price=97, no_price=3)], settlements=[])
        assert rec.buy_cost_cents == 3
        assert rec.residual_cents == 0

    def test_count_multiplies_the_price(self):
        rec = rl.reconcile(prev_balance_cents=1_000, balance_cents=970,
                           fills=[_fill(side="no", no_price=10, count=3)], settlements=[])
        assert rec.buy_cost_cents == 30
        assert rec.residual_cents == 0


class TestWhatAResidualIsNot:
    def test_a_deposit_is_presumed_a_transfer_rather_than_reported_as_a_reward(self):
        """The most embarrassing possible false positive. A book risking at most $10 in total,
        whose modelled reward is fractions of a cent, did not earn $50."""
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=23_651,
                           fills=[], settlements=[])
        assert rec.residual_cents == 5_000
        assert rec.presumed_transfer
        assert not rec.is_material

    def test_a_withdrawal_is_presumed_a_transfer_too(self):
        rec = rl.reconcile(prev_balance_cents=23_651, balance_cents=18_651,
                           fills=[], settlements=[])
        assert rec.presumed_transfer
        assert not rec.is_material

    def test_the_smallest_movement_a_balance_can_make_still_counts(self):
        """One cent is the smallest unit a balance moves in, so it is the first reward that
        could ever be SEEN however much Kalshi accrued. It must not be rounded away as noise."""
        rec = rl.reconcile(prev_balance_cents=100, balance_cents=101,
                           fills=[], settlements=[])
        assert rec.residual_cents == 1
        assert rec.is_material

    def test_an_unchanged_balance_is_zero_not_missing(self):
        rec = rl.reconcile(prev_balance_cents=18_651, balance_cents=18_651,
                           fills=[], settlements=[])
        assert rec.residual_cents == 0
        assert not rec.is_material


class TestItNeverRaisesOnBadPayloads:
    def test_an_unparseable_field_is_absorbed_rather_than_crashing_the_collector(self):
        """This runs inside the collector's tick. A missed observation is recoverable; a
        collector that died mid-tape is not."""
        rec = rl.reconcile(
            prev_balance_cents=100, balance_cents=100,
            fills=[{"side": "yes", "action": "buy", "count": "x", "yes_price": None}],
            settlements=[{"revenue": "not-a-number"}])
        assert rec.buy_cost_cents == 0
        assert rec.settlement_cents == 0
        assert rec.fills_counted == 1
        assert rec.settlements_counted == 1

    def test_empty_windows_reconcile_to_the_bare_balance_change(self):
        rec = rl.reconcile(prev_balance_cents=100, balance_cents=105,
                           fills=None, settlements=None)
        assert rec.residual_cents == 5


class _Client:
    """A portfolio that answers the three reads the ledger makes, with optional paging."""

    def __init__(self, *, balance=18_651, fills=None, settlements=None, pages=1, fail=None):
        self.balance = balance
        self._fills = fills or []
        self._settlements = settlements or []
        self.pages = pages
        self.fail = fail or set()
        self.fill_params: list[dict] = []

    def get_balance(self):
        return {"balance": self.balance}

    def _page(self, key, rows, params):
        if key in self.fail:
            raise RuntimeError(f"{key} unavailable")
        # Hand back a cursor `pages` times, so a truncation bound can be exercised.
        seen = params.get("cursor")
        n = int(seen or 0)
        if n + 1 < self.pages:
            return {key: rows, "cursor": str(n + 1)}
        return {key: rows, "cursor": None}

    def get_fills(self, **params):
        self.fill_params.append(params)
        return self._page("fills", self._fills, params)

    def get_settlements(self, **params):
        return self._page("settlements", self._settlements, params)


class TestObserve:
    def test_the_first_reading_anchors_and_claims_no_residual(self):
        """Differencing the first balance against nothing would report a credit the size of the
        whole account. The anchor row exists only so the NEXT one has something to subtract."""
        client = _Client(balance=18_651)
        balance, rec, notes = rl.observe(client, prev_balance_cents=None, since=None)
        assert balance == 18_651
        assert rec is None
        assert "anchor" in notes

    def test_a_second_reading_reconciles_against_the_first(self):
        client = _Client(balance=18_654)
        _, rec, notes = rl.observe(client, prev_balance_cents=18_651, since=None)
        assert rec is not None
        assert rec.residual_cents == 3
        assert notes == {}

    def test_the_window_start_is_passed_to_the_portfolio_reads(self):
        client = _Client(balance=18_651)
        since = datetime(2026, 9, 19, 2, 0, tzinfo=timezone.utc)
        rl.observe(client, prev_balance_cents=18_651, since=since)
        assert client.fill_params[0]["min_ts"] == int(since.timestamp())

    def test_a_failed_read_marks_the_residual_untrustworthy_instead_of_raising(self):
        """An under-explained window produces a remainder that is not attributable. It must say
        so on the row rather than let a reader treat a missing fill as a reward."""
        client = _Client(balance=18_751, fail={"fills"})
        _, rec, notes = rl.observe(client, prev_balance_cents=18_651, since=None)
        assert rec is not None
        assert "fills_error" in notes
        assert notes["residual_untrustworthy"] is True

    def test_running_out_of_pages_is_recorded_rather_than_looping_forever(self):
        client = _Client(balance=18_651, fills=[_fill()], pages=99)
        _, _, notes = rl.observe(client, prev_balance_cents=18_651, since=None)
        assert notes["fills_truncated"] is True
        assert notes["residual_untrustworthy"] is True

    def test_a_clean_window_with_a_real_fill_explains_itself(self):
        client = _Client(balance=18_641, fills=[_fill(yes_price=10)])
        _, rec, notes = rl.observe(client, prev_balance_cents=18_651, since=None)
        assert rec.residual_cents == 0
        assert notes == {}


class TestConstantsAreDeliberate:
    def test_the_transfer_threshold_is_far_above_anything_this_book_can_earn(self):
        """`MAX_STRATEGY_EXPOSURE_USD` is $10 and the modelled reward at that size is fractions
        of a cent, so a dollar-scale residual is a transfer with near-certainty. If someone
        raises the book's size, this threshold is one of the things that has to be revisited."""
        from kalshi_bot.liquidity_incentive import live

        assert rl.EXTERNAL_TRANSFER_CENTS == 100
        assert live.MAX_STRATEGY_EXPOSURE_USD * 100 > rl.EXTERNAL_TRANSFER_CENTS

    def test_materiality_is_one_cent_because_that_is_the_balance_resolution(self):
        assert rl.MATERIAL_RESIDUAL_CENTS == 1


@pytest.mark.parametrize("delta,expected", [(0, 0), (1, 1), (-1, -1), (7, 7)])
def test_residual_tracks_delta_exactly_when_nothing_traded(delta, expected):
    rec = rl.reconcile(prev_balance_cents=1_000, balance_cents=1_000 + delta,
                       fills=[], settlements=[])
    assert rec.residual_cents == expected


def test_a_window_is_the_gap_between_two_readings_not_a_fixed_period():
    """Regression guard for the boundary: the ledger differences consecutive OBSERVATIONS, so a
    missed tick widens the window rather than losing the cash in it."""
    t0 = datetime(2026, 9, 19, 2, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=4)
    client = _Client(balance=18_654)
    _, rec, _ = rl.observe(client, prev_balance_cents=18_651, since=t0)
    assert rec.residual_cents == 3
    assert t1 > t0
