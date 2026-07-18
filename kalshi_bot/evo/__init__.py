"""Evolutionary population of autonomous Kalshi paper-trading agents.

Design contract: docs/EVOLUTIONARY_AGENT_SYSTEM.md. Everything in this package is
paper/shadow only — there is no code path to a real order (the evo worker runs under
BOT_MODE=evo, where KalshiClient.place_order hard-refuses; nothing here imports the
live executor).
"""
