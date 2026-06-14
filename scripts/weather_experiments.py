"""Experiment digest: run all three offline weather research experiments in one shot.

This is the "experiment results" command — a single ops request that produces the
structured output of every research probe, so they stay in one rotation and can be
re-read as data accumulates:

  1. MODEL CHECK   (weather_model_check) — does the ensemble forecast beat the
     market's implied distribution? Brier/log-loss + hypothetical EV. (--no-live
     here: the per-event live-disagreement dump is skipped to keep the digest tight.)
  2. EXIT SWEEP    (weather_exit_sweep) — would a TP/SL exit rule beat holding to
     settlement? Grid replayed on each trade's recorded price path.
  3. ENTRY STUDY   (weather_entry_study) — calibration, price-band P&L, obs-confirmed
     entry, and limit-entry fills — four ways to attack the entry.

Each sub-script is self-contained (opens its own read-only connection) and prints its
own tables; this just sequences them with banners. Read-only, stdlib + psycopg.

Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_experiments.py
    # or via the ops channel:
    {"type": "script", "name": "weather_experiments"}
"""

from __future__ import annotations

import os
import sys

# scripts/ is on sys.path under the ops runner; ensure it locally too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import weather_entry_study  # noqa: E402
import weather_exit_sweep  # noqa: E402
import weather_model_check  # noqa: E402
import weather_strategy_compare  # noqa: E402
import weather_window_sweep  # noqa: E402

SECTIONS = (
    ("MODEL CHECK — ensemble forecast vs market", weather_model_check, ["--no-live"]),
    ("EXIT SWEEP — TP/SL vs hold to settlement", weather_exit_sweep, []),
    ("WINDOW SWEEP — best entry hours-to-close", weather_window_sweep, []),
    ("ENTRY STUDY — calibration / bands / obs / limit", weather_entry_study, []),
    ("STRATEGY COMPARE — backfill vs live vs TP/SL (cross-validation)",
     weather_strategy_compare, []),
)


def main(argv: list[str] | None = None) -> int:
    rc = 0
    for i, (title, module, args) in enumerate(SECTIONS):
        if i:
            print("\n")
        print("#" * 72)
        print(f"# {i + 1}/{len(SECTIONS)}  {title}")
        print("#" * 72)
        try:
            rc |= module.main(args)
        except Exception as exc:  # noqa: BLE001 — one failing probe shouldn't sink the rest
            print(f"  !! {module.__name__} failed: {exc}", file=sys.stderr)
            rc |= 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
