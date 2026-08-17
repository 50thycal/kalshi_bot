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

# The calibration itself lives in `kalshi_bot.fill_calibration`, above both this
# sandbox and the Experiment OS metrics engine, because the promotion gates and the
# agents' backtests must read the SAME numbers. A second copy here would be a second
# answer to "is this book's edge real". Re-exported so existing callers are unchanged.
from ..fill_calibration import (  # noqa: F401
    CALIBRATION_SOURCE,
    CALIBRATION_VERSION,
    MAKER_FILL_CALIBRATION,
    MIN_CELL_FILLS,
    FillCell,
    cell_for,
    maker_fill_probability,
    project_realizable,
    realizable_cents,
    verdict,
    yes_equivalent_cents,
)


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
