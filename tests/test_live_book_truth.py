"""`scripts/live_book_truth.py` — the real-money read that XOS-000031 asked for.

The defect this script exists to prevent is not a crash: it is a plausible,
confidently-reported NUMBER that is wrong by more than the book's whole P&L. So
the tests here pin the ARITHMETIC against the exact production shape that caused
the wrong report, and pin the status/epsilon constants against the enforcing code
they are duplicated from.

Duplication is deliberate — ops scripts run on a runner that never installs
`kalshi_bot`, and `repository` imports SQLAlchemy — so the guard against drift
has to be a test, which is this one.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib

import pytest

from kalshi_bot import repository as repo

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "live_book_truth.py"
_spec = importlib.util.spec_from_file_location("_live_book_truth", _PATH)
lbt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lbt)

SINCE = dt.datetime(2026, 9, 7, 2, 1, 35, tzinfo=dt.timezone.utc)


# --- the constants are copies; a copy that drifts is the whole bug -----------


def test_committed_statuses_match_count_live_book_open():
    """`count_live_book_open` counts exactly these, and a resting order is OPEN.

    If the enforcing tuple gains a status and this copy does not, the script
    under-counts the open footprint against a bound that still counts it.
    """
    assert set(lbt.COMMITTED_BUY_STATUSES) == set(
        repo.LIVE_NONTERMINAL_STATUSES + ("filled",)
    )


def test_flat_epsilon_matches_the_enforcing_source():
    source = pathlib.Path(repo.__file__).read_text()
    body = source[source.index("def count_live_book_open"):]
    body = body[:body.index("\ndef ", 1)]
    assert "0.01" in body
    assert lbt.FLAT_EPSILON == 0.01


def test_the_script_is_on_the_ops_allowlist():
    """An ops script nobody can run is not a fix."""
    runner = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "ops_runner.py").read_text()
    assert '"live_book_truth"' in runner


# --- a stub cursor: the SQL is psycopg-flavoured, the arithmetic is ours -----


class _Cursor:
    """Answers by matching a distinctive fragment of each statement.

    Not a database. The point is to drive `report()` with the exact production
    shape and assert what it PRINTS, which is where the wrong number appeared.
    """

    def __init__(self, *, ordered, filled, snaps, paper, twin_rows, day):
        self.ordered, self.filled, self.snaps = ordered, filled, snaps
        self.paper, self.twin_rows, self.day = paper, twin_rows, day
        self._result = None

    def execute(self, sql, params=()):
        if "date_trunc" in sql:
            self._result = self.day
        elif "live_paper_twins" in sql:
            self._result = (SINCE, "Fmmsell10_pt4")
        elif "from live_orders" in sql and "distinct market_ticker" in sql:
            committed = "status = any" in sql
            self._result = [
                (t,) for t, st in self.ordered.items()
                if not committed or st in lbt.COMMITTED_BUY_STATUSES
            ]
        elif "from fills" in sql:
            self._result = [(t, q, fee) for t, (q, fee) in self.filled.items()]
        elif "from positions" in sql and "distinct on" in sql:
            self._result = [(t, *v) for t, v in self.snaps.items()]
        elif "from paper_trades" in sql:
            tag = params[0]
            rows = self.twin_rows if tag.endswith("_pt4") else self.paper
            subset = params[3] if len(params) > 3 else None
            if subset is not None:
                inside = set(subset)
                keep = (lambda t: t not in inside) if "not (market_ticker" in sql \
                    else (lambda t: t in inside)
                rows = [r for r in rows if keep(r[0])]
            self._result = (sum(r[1] for r in rows), len(rows))
        else:  # pragma: no cover — a new query must be taught to the stub
            raise AssertionError(f"unstubbed query: {sql[:80]}")

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._result


def _production_shape():
    """The 2026-09-10 canary, reduced to the smallest shape with the same defect.

    Two tickers filled and settled to a real LOSS; one ticker rested, was
    CANCELLED without ever filling, and the simulator recorded a WIN on it.
    Reading `paper_trades` therefore reports a profitable book that lost money —
    which is exactly what happened at full scale (+$2.56 reported, -$1.41 real).

    The cancelled status is not incidental. It is how an mmsell order ordinarily
    fails to fill, and the first shipped version filtered the never-filled set to
    committed statuses, so it reported the phantom as n=0 on a book with 65
    cancelled orders. `test_a_CANCELLED_never_filled_order_is_still_phantom`
    pins that.
    """
    return _Cursor(
        ordered={"A": "filled", "B": "filled", "NEVER": "canceled"},
        filled={"A": (1.0, 0.01), "B": (1.0, 0.01)},
        snaps={
            "A": (0.0, -1.00, 0.0),     # settled, real loss
            "B": (0.0, -0.41, 0.0),     # settled, real loss
            "NEVER": (None, None, None),  # ordered, never filled, no snapshot
        },
        paper=[("A", -0.90), ("B", -0.30), ("NEVER", 3.76)],
        twin_rows=[("A", -0.20), ("B", 0.10), ("NEVER", 3.70)],
        day=(-0.50, 35),
    )


def _run(cur, capsys, tag="Fmmsell10", twin=None, since=SINCE):
    assert lbt.report(cur, tag, twin, since) == 0
    return capsys.readouterr().out


def test_realized_is_the_exchange_figure_not_paper_trades(capsys):
    out = _run(_production_shape(), capsys)
    assert "realized              :   -1.4100   settled=2" in out
    assert "REAL MONEY for Fmmsell10 is -1.4100" in out


def test_the_phantom_half_is_named_and_quantified(capsys):
    """The never-filled rows are the entire reason the wrong number looked fine."""
    out = _run(_production_shape(), capsys)
    assert "ordered, NEVER filled :    3.7600   n=1   <- lost AT THE FILL" in out
    assert "live FILLED it        :   -1.2000   n=2" in out
    assert "overstates real money by +3.9700" in out


def test_paper_trades_would_have_reported_a_PROFIT_on_a_losing_book(capsys):
    """The failure in one assertion: opposite signs, same tag, same window."""
    out = _run(_production_shape(), capsys)
    assert "all rows              :    2.5600   n=3" in out
    realized = -1.41
    assert realized < 0 < 2.56


def test_a_CANCELLED_order_is_phantom_but_NOT_open(capsys):
    """The two sets are different, and conflating them breaks one or the other.

    A cancelled rest holds nothing, so it must not occupy a slot under the open
    cap — and it never filled, so its simulated row is phantom. The first shipped
    version used the committed set for both and reported the phantom as n=0.
    """
    out = _run(_production_shape(), capsys)
    assert "ordered, NEVER filled :    3.7600   n=1   <- lost AT THE FILL" in out
    assert "open by the cap rule  :         0   (count_live_book_open semantics)" in out


def test_a_RESTING_unfilled_order_counts_as_OPEN_and_is_still_phantom(capsys):
    """`count_live_book_open` counts a committed order with no snapshot as open.

    A `paper_trades` row count would say 4 here and a fills count 2; the bound
    governs neither.
    """
    cur = _production_shape()
    cur.ordered["REST"] = "resting"
    cur.snaps["REST"] = (None, None, None)
    cur.paper.append(("REST", 0.50))
    out = _run(cur, capsys)
    assert "open by the cap rule  :         1   (count_live_book_open semantics)" in out
    assert "ordered, NEVER filled :    4.2600   n=2   <- lost AT THE FILL" in out
    assert "NEVER filled          :         2" in out


def test_every_simulated_row_falls_in_one_half_or_the_other(capsys):
    """The partition guard. If a row lands in neither half the phantom is a lie
    by omission, so the report says so instead of printing a clean split."""
    out = _run(_production_shape(), capsys)
    assert "in NO bucket" not in out


def test_a_market_live_NEVER_ORDERED_is_its_own_bucket(capsys):
    """The third bucket, and the one the second production run surfaced.

    48 simulated rows sat on markets `Fmmsell10` never placed a single order on —
    a live gate (the contest cap, the open cap, the price ceiling) refused them
    before an order existed. They are phantom too, but for a different reason than
    a failed fill, so conflating the two answers the wrong question: one measures
    what execution costs, the other what the caps cost.
    """
    cur = _production_shape()
    cur.paper.append(("GATED", 9.99))          # never ordered at all
    out = _run(cur, capsys)
    assert "NEVER ordered         :    9.9900   n=1   <- a live GATE refused it" in out
    assert "ordered, NEVER filled :    3.7600   n=1   <- lost AT THE FILL" in out
    assert "phantom total         :   13.7500" in out
    assert "in NO bucket" not in out


def test_the_partition_guard_FIRES_when_a_bucket_under_reports(monkeypatch, capsys):
    """The invariant, exercised by breaking a bucket rather than the data.

    With three buckets the data cannot fall outside them, so the guard now guards
    the queries: if one of them silently returns fewer rows than it should, the
    report must call the phantom a floor instead of printing a clean split.
    """
    real = lbt._paper_pnl

    def short(cur, tag, since, tickers=None, *, exclude=False):
        total, n = real(cur, tag, since, tickers, exclude=exclude)
        return (total, n - 1) if exclude else (total, n)

    monkeypatch.setattr(lbt, "_paper_pnl", short)
    out = _run(_production_shape(), capsys)
    assert "!! 1 simulated row(s) in NO bucket" in out
    assert "treat the phantom figure as a floor" in out


def test_a_still_open_filled_position_is_open_and_not_realized(capsys):
    cur = _production_shape()
    cur.snaps["B"] = (1.0, None, 0.93)   # filled, still held
    out = _run(cur, capsys)
    assert "realized              :   -1.0000   settled=1" in out
    assert "open cost basis       :    0.9300" in out
    assert "open by the cap rule  :         1" in out


def test_twin_gap_is_reported_with_the_selection_caveat(capsys):
    out = _run(_production_shape(), capsys, twin="Fmmsell10_pt4")
    assert "realized              :    3.6000   n=3" in out
    assert "twin - live           : +5.0100" in out
    assert "what the caps and gates cost" in out


def test_breaker_line_says_it_is_portfolio_wide(capsys):
    """Reading the portfolio breaker as one book's day overstates the headroom."""
    out = _run(_production_shape(), capsys)
    assert "realized today        :   -0.5000   markets=35" in out
    assert "PORTFOLIO-wide" in out


def test_epoch_is_taken_from_live_paper_twins_when_no_since_given(capsys):
    """Without the epoch boundary every figure silently pools the prior epoch."""
    out = _run(_production_shape(), capsys, since=None)
    assert SINCE.isoformat() in out
    assert "(live_paper_twins)" in out
    assert "twin        : Fmmsell10_pt4" in out


def test_no_fills_at_all_reports_zero_rather_than_dividing_by_zero(capsys):
    cur = _Cursor(ordered={}, filled={}, snaps={}, paper=[], twin_rows=[], day=(0.0, 0))
    out = _run(cur, capsys)
    assert "tickers ordered       :         0" in out
    assert "realized              :    0.0000   settled=0" in out


@pytest.mark.parametrize("forbidden", ["unrealized", "mark_no_bid"])
def test_it_does_not_invent_an_unrealized_figure(forbidden):
    """`positions.unrealized_pnl` is never written by the live path; deriving a
    mark here would be a third implementation of the dashboard's."""
    body = _PATH.read_text().lower()
    assert f"select {forbidden}" not in body
