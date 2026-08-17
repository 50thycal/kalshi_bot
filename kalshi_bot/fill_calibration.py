"""Maker-fill calibration — the measured relationship a resting order actually gets.

Paper assumes a resting maker order always fills. Live, on the mmsell books, it
fills ~70% of the time and the ~30% it misses are disproportionately the winners:
a passive bid is only hit when someone actively takes the other side, so the quiet
cheap longshots that settle worthless never trade against you. Paper books them;
live cannot. Full analysis: `docs/MMSELL_FILL_MODEL.md`.

`scripts/mmsell_fill_model.py` measures that off live ground truth
(`live_orders` fill status joined to settled `paper_trades` for the same tickers):

    yes entry cent  ->  (n_fills, P[fill], realizable P&L per contract | fill)

This module is the **versioned snapshot** of that measurement, declared in code so
every consumer is reproducible, offline and cheap. It lives here — above both the
evo sandbox and the Experiment OS metrics engine — because three separate
consumers depend on the same numbers meaning the same thing:

  * `kalshi_bot.evo.fill_model` — the sandbox's fill gate and realizable report;
  * `kalshi_bot.experiment_os.metrics` — the canonical `realizable_cents_per_trade`
    provider that promotion gates read;
  * `scripts/mmsell_fill_model.py` — the ops report (stdlib-only, so it restates
    `project_realizable`; parity is pinned by tests).

A second copy of this table is a second answer to "is this book's edge real",
which is the drift Experiment OS exists to remove.

## This calibration is platform state, not a local constant

It is declared in the active `FILL_MODEL` platform revision
(`assumed_fill_plus_mmsell3_calibration`), and the measurement layer that applies
it is declared in `METRICS_ENGINE` (`pnl_scripts_2026_08`). Consumers therefore
record `CALIBRATION_VERSION` in their provenance: evidence computed under two
different calibrations is not poolable, and changing these numbers is a **new
FILL_MODEL revision** with an impact classification — never an edit in place.

Coverage is never guessed. A price cell with fewer than `MIN_CELL_FILLS` live
fills is untrusted: it is excluded from the projection and surfaced as coverage
below 100%, rather than filled in with a neighbouring cell's rate.
"""

from __future__ import annotations

from dataclasses import dataclass

# Snapshot of the live mmsell3 calibration. Bump on refresh — and refreshing is a
# FILL_MODEL platform revision, not a code tweak.
CALIBRATION_VERSION = "2026-08-11"
CALIBRATION_SOURCE = "mmsell3 live_orders x settled paper_trades (scripts/mmsell_fill_model.py)"

# A yes-cent cell needs at least this many FILLED live observations before we trust
# its fill rate / realizable P&L. Same threshold as the ops script, deliberately —
# the two must agree about which cells are real or the surfaces disagree.
MIN_CELL_FILLS = 8


@dataclass(frozen=True)
class FillCell:
    """One measured price cell. `realizable_cents` is the mean per-contract P&L over
    the tickers live ACTUALLY filled at this price — the adversely-selected subset,
    which is why it can be (and mostly is) worse than the optimistic number."""

    n_fills: int
    fill_rate: float
    realizable_cents: float

    @property
    def trusted(self) -> bool:
        return self.n_fills >= MIN_CELL_FILLS


# yes-equivalent entry cent -> measured cell.
# NB 12c is trusted but thin (10 fills): its +11.0c realizable carries wide error
# bars. 5c and 13c are below threshold and are treated as uncovered.
MAKER_FILL_CALIBRATION: dict[int, FillCell] = {
    5: FillCell(1, 0.250, 4.00),
    6: FillCell(62, 0.633, 1.77),
    7: FillCell(59, 0.797, 0.92),
    8: FillCell(64, 0.688, -0.81),
    9: FillCell(58, 0.682, -5.79),
    10: FillCell(75, 0.806, -1.67),
    11: FillCell(28, 0.609, -0.71),
    12: FillCell(10, 0.833, 11.00),
    13: FillCell(4, 0.500, 12.00),
}


def yes_equivalent_cents(side: str, limit_price_cents: float) -> int:
    """The calibration is keyed by the market's YES cent, because fillability is a
    property of the market's price cell rather than of which leg you took: a resting
    NO bid at 92c and a resting YES bid at 8c are the same book event. mmsell rests
    NO bids, so its 91-92c entries are the 8-9c YES cells here."""
    price = float(limit_price_cents)
    return int(round(price if side == "yes" else 100.0 - price))


def cell_for(side: str, limit_price_cents: float) -> FillCell | None:
    """The TRUSTED cell for this resting order, or None when we have no measurement
    there (out of range, or too few live fills to believe)."""
    cell = MAKER_FILL_CALIBRATION.get(yes_equivalent_cents(side, limit_price_cents))
    return cell if cell is not None and cell.trusted else None


def maker_fill_probability(side: str, limit_price_cents: float) -> float | None:
    cell = cell_for(side, limit_price_cents)
    return None if cell is None else cell.fill_rate


def realizable_cents(side: str, limit_price_cents: float) -> float | None:
    cell = cell_for(side, limit_price_cents)
    return None if cell is None else cell.realizable_cents


def project_realizable(price_hist: dict[int, tuple[int, float]],
                       *, min_cell_fills: int = MIN_CELL_FILLS) -> dict:
    """Project a book's entry-price mix through the calibration.

    price_hist: yes-equivalent cent -> (n_trades, sum of optimistic P&L in cents).
    Same contract and same coverage rule as `project_realizable` in
    `scripts/mmsell_fill_model.py` (which restates it to stay stdlib-only for the
    ops runner; parity is pinned by tests). Trades at uncovered prices are excluded
    from the estimate, never guessed."""
    total_n = sum(n for n, _ in price_hist.values())
    opt_sum = sum(s for _, s in price_hist.values())
    covered_n = 0
    fill_w = 0.0
    real_w = 0.0
    for yes_c, (n, _s) in price_hist.items():
        cell = MAKER_FILL_CALIBRATION.get(yes_c)
        if cell is None or cell.n_fills < min_cell_fills:
            continue
        covered_n += n
        fill_w += cell.fill_rate * n
        real_w += cell.realizable_cents * n
    return {
        "total_n": total_n,
        "covered_n": covered_n,
        "coverage": round(covered_n / total_n, 3) if total_n else None,
        "est_fill_rate": round(fill_w / covered_n, 4) if covered_n else None,
        "est_realizable_cents": round(real_w / covered_n, 3) if covered_n else None,
        "opt_cents": round(opt_sum / total_n, 3) if total_n else None,
    }


def verdict(projection: dict) -> str:
    """One word, in the same language as the ops report.

    MIRAGE  — the optimistic number is positive but the realizable one is not: the
              edge is an artifact of assuming fills we would never get.
    REAL    — survives the fill correction.
    NEGATIVE— unprofitable even before the correction.
    UNCOVERED — no trusted cell backs this book's price mix; no claim either way."""
    real = projection.get("est_realizable_cents")
    opt = projection.get("opt_cents")
    if real is None:
        return "UNCOVERED"
    if opt is not None and opt <= 0:
        return "NEGATIVE"
    return "REAL" if real > 0 else "MIRAGE"


__all__ = [
    "CALIBRATION_VERSION", "CALIBRATION_SOURCE", "MIN_CELL_FILLS", "FillCell",
    "MAKER_FILL_CALIBRATION", "yes_equivalent_cents", "cell_for",
    "maker_fill_probability", "realizable_cents", "project_realizable", "verdict",
]
