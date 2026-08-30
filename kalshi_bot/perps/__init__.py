"""PERP-V1 Probe 1 — the read-only perpetual-futures tape.

Contract: `docs/PERP_V1_THESIS.md`. Experiment: `perp-v1` (PROBE).

This package READS. It places no orders, holds no position, carries no strategy
tag and registers no deployment — there is no perp order path anywhere in this
repository. One collector serves all three arms, which is the reason PERP-V1 is
one experiment rather than three (`DEC-008`).
"""

from .collector import PerpsCollector

__all__ = ["PerpsCollector"]
