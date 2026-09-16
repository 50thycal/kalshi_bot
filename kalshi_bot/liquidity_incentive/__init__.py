"""Liquidity-incentive shadow market maker — Phase 0 research instrument.

Strategy name: `liquidity_incentive_mm` (short tag prefix `limm`). Separate from MMSELL by
construction: its own tables, its own collector thread, its own dashboard section, and no
strategy state shared with any other book. Shared infrastructure only — the Kalshi client
(GET-only wrapper), the execution-telemetry local book and payload parsers, the fee
coefficients.

Phase 0 places NO orders. Every "quote", "fill" and "P&L" in this package is a simulation
against the real order book and trade tape, labelled by fill model so the sensitivity to the
fill assumption is visible rather than averaged away. Thesis and pre-registration:
`docs/LIQUIDITY_INCENTIVE_THESIS.md`.
"""

STRATEGY_NAME = "liquidity_incentive_mm"
TAG_PREFIX = "limm"
