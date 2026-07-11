"""PIN15 book — endgame settlement-average observation-pin on Kalshi's 15-minute crypto up/down.

These markets (KXBTC15M/KXETH15M) settle on the 60-SECOND AVERAGE of the CF index over the final
minute, so 2-3 min before close a spot displacement from the target already pins the outcome while
the retail quote stays anchored to the last-tick price. PIN15 is a TAKER buy of the drift-favored
side (YES if spot>target, NO if spot<target), held to settlement. Thesis + validation:
docs/PIN15_THESIS.md, scripts/kalshi_pin15_study.py.
"""

from .tracker import Pin15CycleSummary, Pin15Tracker

__all__ = ["Pin15CycleSummary", "Pin15Tracker"]
