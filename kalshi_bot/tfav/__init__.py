"""TFAV book — the mirror of theta on Kalshi's recurring hourly crypto ladders.

theta SELLS the model-overpriced tails; tfav BUYS the model-underpriced FAVORITES
(taker buy YES at the ask, held to settlement). Probe: scripts/kalshi_favbuy_study.py.
"""

from .tracker import TfavCycleSummary, TfavTracker

__all__ = ["TfavCycleSummary", "TfavTracker"]
