#!/usr/bin/env python3
"""LIMM payout truth (docs/LIMM_PAYOUT_TRUTH.md): score a screenshot-free reward rule on the
balance ledger against the owner's Rewards-page screenshots.

Inputs are the two read-only `db` results named in the doc, saved as text tables:
  residuals: at | residual_cents | presumed_transfer | untrust   (residual >= 100c, to 11-10)
  ends:      market_ticker | end_date   (liquidity programmes on markets Alimm1 ordered on)

Usage: python tools/review/limm/payout_truth.py RESIDUALS.txt ENDS.txt
Offline, stdlib only, plus the dashboard's own `confirmed_reward` test.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, os.pardir))
sys.path.insert(0, ROOT)

from kalshi_bot.liquidity_incentive.reward_ledger import (  # noqa: E402
    CONFIRMED_REWARD_PAYOUTS,
    confirmed_reward,
)

# --- pre-registered (docs/LIMM_PAYOUT_TRUTH.md §3, §5) -------------------------------------
MIN_CENTS = 100
CAPS = {"R-200": 20_000, "R-25": 2_500}
DECIDING = "R-200"
WINDOW = timedelta(hours=24)
UNTIL = datetime.fromisoformat("2026-11-10T23:59:59+00:00")
AUTOMATE, ESTIMATE = 0.90, 0.75


def _rows(path: str) -> list[list[str]]:
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#") or line.startswith("-") or line.startswith("("):
                continue
            parts = line.split()
            if parts[0] in ("at", "market_ticker"):
                continue
            out.append(parts)
    return out


def _ts(date: str, time: str) -> datetime:
    return datetime.fromisoformat(f"{date}T{time}")


def kind(ticker: str) -> str:
    if ticker.startswith("KXYTVIEWS"):
        return "youtube"
    if "MENTION" in ticker or "SAY" in ticker:
        return "mention"
    return "other"


def is_reward(cents: int, at: datetime, ends: list[tuple[datetime, str]], cap: int) -> bool:
    if cents < MIN_CENTS or cents > cap or cents % 100 == 0:
        return False
    return any(timedelta(0) <= at - end <= WINDOW for end, _t in ends)


def main(res_path: str, ends_path: str) -> int:
    residuals = []
    for p in _rows(res_path):
        at = _ts(p[0], p[1])
        untrust = len(p) > 4 and p[4].lower() in ("true", "1")
        if at <= UNTIL and not untrust:
            residuals.append((at, int(p[2])))
    ends = [(_ts(p[1], p[2]), p[0]) for p in _rows(ends_path)]

    total_confirmed = sum(c for _d, c, _e in CONFIRMED_REWARD_PAYOUTS)
    conf_steps = [(at, c) for at, c in residuals if confirmed_reward(c, at)]
    seen = sum(c for _at, c in conf_steps)
    coverage = seen / total_confirmed if total_confirmed else 0.0
    print(f"trusted residuals >= {MIN_CENTS}c to {UNTIL:%Y-%m-%d}: {len(residuals)}; "
          f"programme ends: {len(ends)}")
    print(f"confirmed payouts: {len(CONFIRMED_REWARD_PAYOUTS)} rows, ${total_confirmed / 100:.2f}; "
          f"seen as confirmed steps: {len(conf_steps)} steps, ${seen / 100:.2f} "
          f"-> coverage {coverage:.1%}")
    print()
    print("at                         cents  confirmed  " + "  ".join(CAPS))
    for at, c in residuals:
        marks = "  ".join(f"{'yes' if is_reward(c, at, ends, cap) else '-':>5}" for cap in CAPS.values())
        print(f"{at:%Y-%m-%d %H:%M:%SZ}  {c:7d}  {'yes' if confirmed_reward(c, at) else '-':>9}  {marks}")
    print()
    verdict = None
    for name, cap in CAPS.items():
        pos = [(at, c) for at, c in residuals if is_reward(c, at, ends, cap)]
        pos_c = sum(c for _at, c in pos)
        true_c = sum(c for at, c in pos if confirmed_reward(c, at))
        recall = true_c / seen if seen else 0.0
        precision = true_c / pos_c if pos_c else 0.0
        false_steps = [(at, c) for at, c in pos if not confirmed_reward(c, at)]
        missed = [(at, c) for at, c in conf_steps if not is_reward(c, at, ends, cap)]
        print(f"{name}{' (deciding)' if name == DECIDING else ' (reference)'}: "
              f"positive {len(pos)} steps ${pos_c / 100:.2f}; recall {recall:.1%}; precision {precision:.1%}")
        for at, c in false_steps:
            print(f"    counted, not paid: {at:%Y-%m-%d %H:%MZ} {c}c")
        for at, c in missed:
            print(f"    paid, not counted: {at:%Y-%m-%d %H:%MZ} {c}c")
        if name == DECIDING:
            if coverage >= AUTOMATE and recall >= AUTOMATE and precision >= AUTOMATE:
                verdict = "AUTOMATE"
            elif recall >= ESTIMATE and precision >= ESTIMATE:
                verdict = "ESTIMATE"
            else:
                verdict = "FAIL"
            types = []
            for at, c in pos:
                if not confirmed_reward(c, at):
                    continue
                ks = sorted({kind(t) for end, t in ends if timedelta(0) <= at - end <= WINDOW})
                types.append(ks)
                print(f"    step {at:%Y-%m-%d %H:%MZ} {c}c: programmes ending in window: {','.join(ks)}")
            single = sum(1 for ks in types if len(ks) == 1)
            print(f"    single-type steps: {single} of {len(types)}")
    print()
    print(f"VERDICT ({DECIDING}): {verdict}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
