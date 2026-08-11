"""Maker-fill calibration: what a RESTING order actually gets, measured live.

The sandbox used to assume a resting maker order fills whenever the market traded
through the limit that step. Live, on the mmsell books, that assumption is the single
largest source of paper->live divergence — and it is not a small one:

  * a resting mmsell sell fills ~70% of the time, not 100%; and
  * the ~30% it misses are the WINNERS. A passive bid only gets hit when someone
    actively takes the other side, so the quiet cheap longshots that drift to zero
    never trade against you and never get booked. Paper counts those free winners.
    Live cannot. On the exact same mmsell3 tickers at the same ~91c entry, paper's
    own P&L was -0.67c/trade on the ones live filled vs +3.77c/trade on the ones
    live tried and could never fill.

`scripts/mmsell_fill_model.py` measures that relationship off live ground truth
(`live_orders` fill status joined to the settled `paper_trades` for the same tickers):

    yes entry cent  ->  (n_fills, P[fill], realizable P&L per contract | fill)

This module is a **versioned snapshot** of that measurement, declared in code so the
sandbox is reproducible, offline and cheap (same seed-in-code pattern as
`announcements.py` / `graveyard_seed.py`). Refresh it by re-running the ops probe
    {"type": "script", "name": "mmsell_fill_model"}
and bumping CALIBRATION_VERSION with the new rows.

Two distinct uses, deliberately kept separate:

  fill gate   — `maker_fill_probability()` says HOW OFTEN a resting order at that
                price gets hit at all. Used to decide whether a backtest's maker
                entry happens on a given market.
  realizable  — `project_realizable()` says WHAT the fills you do get are worth,
                which is where the adverse-selection correction lives. Used to
                report a realizable per-contract number beside the optimistic one.

Coverage is never guessed. A price cell with fewer than MIN_CELL_FILLS live fills is
untrusted: the fill gate falls back to the old trade-through heuristic and the
realizable projection excludes those trades and reports coverage < 100%.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# Snapshot of the live mmsell3 calibration. Bump on refresh.
CALIBRATION_VERSION = "2026-08-11"
CALIBRATION_SOURCE = "mmsell3 live_orders x settled paper_trades (scripts/mmsell_fill_model.py)"

# A yes-cent cell needs at least this many FILLED live observations before we trust its
# fill rate / realizable P&L. Same threshold as the ops script, deliberately — the two
# must agree about which cells are real or the sandbox and the report disagree.
MIN_CELL_FILLS = 8


@dataclass(frozen=True)
class FillCell:
    """One measured price cell. `realizable_cents` is the mean per-contract P&L over the
    tickers live ACTUALLY filled at this price — the adversely-selected subset, which is
    why it can be (and mostly is) worse than the optimistic fill-everything number."""

    n_fills: int
    fill_rate: float
    realizable_cents: float

    @property
    def trusted(self) -> bool:
        return self.n_fills >= MIN_CELL_FILLS


# yes-equivalent entry cent -> measured cell.
# NB 12c is trusted but thin (10 fills): its +11.0c realizable carries wide error bars.
# 5c and 13c are below threshold and are treated as uncovered.
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
    NO bid at 92c and a resting YES bid at 8c are the same book event. mmsell rests NO
    bids, so its 91-92c entries are the 8-9c YES cells here."""
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


def _uniform(key: str) -> float:
    """A stable pseudo-uniform draw in [0, 1) from a string key. Deterministic by
    construction: the same spec replayed over the same history always fills the same
    orders, so a backtest stays reproducible and comparable across runs."""
    digest = hashlib.sha1(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:6], "big") / float(1 << 48)


def maker_order_fills(*, ticker: str, side: str, limit_price_cents: float,
                      nonce: str = "") -> bool | None:
    """Does a resting order at this price ever get hit on this market?

    Returns None when the price is uncovered (caller keeps its own heuristic), else a
    deterministic draw against the measured fill rate.

    This is decided ONCE PER MARKET, not per replay step. The calibration measures the
    lifetime fill rate of a resting order, so re-drawing every candle would compound to
    a near-certain fill (0.32 ** 20 ~ 0) and quietly restore the 100% assumption it
    exists to correct.
    """
    p = maker_fill_probability(side, limit_price_cents)
    if p is None:
        return None
    return _uniform(f"{ticker}|{side}|{int(round(float(limit_price_cents)))}|{nonce}") < p


def project_realizable(price_hist: dict[int, tuple[int, float]],
                       *, min_cell_fills: int = MIN_CELL_FILLS) -> dict:
    """Project a book's entry-price mix through the calibration.

    price_hist: yes-equivalent cent -> (n_trades, sum of optimistic P&L in cents/contract).
    Mirrors `project_realizable` in scripts/mmsell_fill_model.py — same contract, same
    coverage rule — so the sandbox's number and the ops report's number mean the same
    thing. Trades at uncovered prices are excluded from the estimate, never guessed.
    """
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
    """One word an agent can gate on, using the same language as the ops report.

    MIRAGE — the optimistic number is positive but the realizable one is not: the edge
             is an artifact of assuming fills we would never get. Do not trade it.
    REAL    — survives the fill correction.
    NEGATIVE— unprofitable even before the correction.
    UNCOVERED — no trusted calibration at these prices; the paper number is untested,
             not endorsed.
    """
    real = projection.get("est_realizable_cents")
    opt = projection.get("opt_cents")
    if real is None:
        return "UNCOVERED"
    if opt is not None and opt <= 0:
        return "NEGATIVE"
    return "REAL" if real > 0 else "MIRAGE"
