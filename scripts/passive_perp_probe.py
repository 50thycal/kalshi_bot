"""PASSIVE-PERP census v1: price-return screen, never a fill or net-P&L claim.

Frozen specification: docs/PASSIVE_PERP_CENSUS.md. Read-only retained collector
tape, fixed UTC window, prior-only signal, no orders or Experiment OS mutations.
Only dependency beyond stdlib is psycopg, imported when main connects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import statistics
from collections import Counter
from datetime import datetime, timezone

TICKERS = ("KXBTCPERP", "KXETHPERP")
START = datetime(2026, 8, 30, tzinfo=timezone.utc)
END = datetime(2026, 9, 3, tzinfo=timezone.utc)
WINDOW = 20
MAX_GAP_SEC = 600
MAX_HOLD_SEC = 3600
SEED = 20260912
MIN_TRADES = 30
FIELDS = ("ticker", "captured_at", "bid", "ask", "premium_bps",
          "reference_price", "settlement_mark_price")
RO_OPTIONS = "-c default_transaction_read_only=on -c statement_timeout=60000"


def number(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def usable(row):
    b, a, m, ref = (row[k] for k in
                    ("bid", "ask", "settlement_mark_price", "reference_price"))
    return (row["premium_bps"] is not None and all(x is not None and x > 0
            for x in (b, a, m, ref)) and a >= b)


def returns(entry, exit_row, direction):
    """Linear price P&L per entry notional; fee sensitivity scales BOTH notionals.

    maker assumes same-side quotes fill instantly at both decisions. Deliberately
    optimistic scenario, not a bound on all possible passive selection policies.
    """
    result = {}
    for mode in ("mid", "maker", "taker"):
        if mode == "mid":
            pin = (entry["bid"] + entry["ask"]) / 2
            pout = (exit_row["bid"] + exit_row["ask"]) / 2
        else:
            buy_entry = direction == 1
            entry_side = "bid" if buy_entry == (mode == "maker") else "ask"
            exit_side = "ask" if entry_side == "bid" else "bid"
            pin, pout = entry[entry_side], exit_row[exit_side]
        gross = direction * (pout / pin - 1) * 10000
        result[f"{mode}_gross_bps"] = gross
        if mode != "mid":
            # HISTORICAL SCENARIOS ONLY: 2 bps/leg maker, 12 bps/leg taker.
            per_leg = 2 if mode == "maker" else 12
            result[f"{mode}_fee_scenario_ex_funding_bps"] = gross - per_leg * (1 + pout / pin)
    result["premium_convergence_bps"] = direction * (
        exit_row["premium_bps"] - entry["premium_bps"])
    return result


def score(rows):
    rng = random.Random(SEED)
    trades, censored, counts = [], [], Counter()
    for ticker in TICKERS:
        tape = [r for r in rows if r["ticker"] == ticker]
        i = WINDOW
        while i < len(tape):
            history, entry = tape[i - WINDOW:i], tape[i]
            segment = history + [entry]
            if not all(usable(r) for r in segment):
                counts["ineligible_missing_or_invalid"] += 1
                i += 1
                continue
            if any(not 0 < (b["captured_at"] - a["captured_at"]).total_seconds()
                   <= MAX_GAP_SEC for a, b in zip(segment, segment[1:], strict=False)):
                counts["ineligible_gap"] += 1
                i += 1
                continue
            mu = statistics.mean(r["premium_bps"] for r in history)
            sd = statistics.stdev(r["premium_bps"] for r in history)
            z = (entry["premium_bps"] - mu) / sd if sd else 0
            if abs(z) < 2.5:
                i += 1
                continue
            direction = -1 if z > 0 else 1
            base = {"ticker": ticker, "entry_at": entry["captured_at"].isoformat(),
                    "direction": direction, "entry_z": z}
            reason, j = "end_of_tape", i + 1
            while j < len(tape):
                row = tape[j]
                gap = (row["captured_at"] - tape[j - 1]["captured_at"]).total_seconds()
                if not 0 < gap <= MAX_GAP_SEC or not usable(row):
                    reason = "gap_or_invalid_exit_path"
                    break
                held = (row["captured_at"] - entry["captured_at"]).total_seconds()
                if (abs((row["premium_bps"] - mu) / sd) <= 0.5
                        or abs(row["premium_bps"]) <= 5 or held >= MAX_HOLD_SEC):
                    ctl = returns(entry, row, rng.choice((-1, 1)))
                    trades.append({**base, "exit_at": row["captured_at"].isoformat(),
                                   "held_seconds": held, **returns(entry, row, direction),
                                   "control_maker_gross_bps": ctl["maker_gross_bps"]})
                    reason = None
                    break
                j += 1
            if reason:
                censored.append({**base, "reason": reason})
            i = j + 1  # no overlapping positions within an asset
    return trades, censored, dict(counts)


def summarize(rows):
    trades, censored, excluded = score(rows)
    assets = {}
    for ticker in TICKERS:
        tape = [r for r in rows if r["ticker"] == ticker]
        ts = [r for r in trades if r["ticker"] == ticker]
        cs = [r for r in censored if r["ticker"] == ticker]
        means = {k: statistics.mean(t[k] for t in ts) if ts else None
                 for k in ("mid_gross_bps", "maker_gross_bps", "taker_gross_bps",
                           "maker_fee_scenario_ex_funding_bps",
                           "taker_fee_scenario_ex_funding_bps",
                           "premium_convergence_bps", "control_maker_gross_bps")}
        days = sorted({t["entry_at"][:10] for t in ts})
        assets[ticker] = {"rows": len(tape), "usable_rows": sum(usable(r) for r in tape),
                          "first": tape[0]["captured_at"].isoformat() if tape else None,
                          "last": tape[-1]["captured_at"].isoformat() if tape else None,
                          "n": len(ts), "censored": len(cs), "entry_days": days,
                          "snapshot_coverage_pct_at_60s": 100 * len(tape) / ((END - START).total_seconds() / 60),
                          "daily_maker_gross_bps": {
                              d: statistics.mean(t["maker_gross_bps"] for t in ts
                                                 if t["entry_at"].startswith(d)) for d in days},
                          **means}
    adequate = all(a["n"] >= MIN_TRADES and len(a["entry_days"]) >= 3
                   and a["censored"] / (a["n"] + a["censored"]) <= 0.10
                   for a in assets.values())
    if not adequate:
        verdict, action = "HOLD", "Insufficient complete paths; no parameter sweep or paper promotion."
    elif all(a["maker_gross_bps"] <= 0 for a in assets.values()):
        verdict, action = "KILL_LEANING", "Stop this fixed historical candidate: both assets lose even in the zero-fee instant-maker scenario. Not a family-wide proof."
    elif all(a["maker_fee_scenario_ex_funding_bps"] > 0
             and a["maker_gross_bps"] > a["control_maker_gross_bps"] for a in assets.values()):
        verdict, action = "HOLD", "Price screen survives; next establish funding, fee and passive-fill testability before a prospective contract."
    else:
        verdict, action = "HOLD", "Mixed or fee/control-negative price screen; do not select an asset or retune on this tape."
    manifest = json.dumps(rows, default=lambda v: v.isoformat(), sort_keys=True, separators=(",", ":"))
    return {"probe": "PASSIVE-PERP-census-v1", "window": [START.isoformat(), END.isoformat()],
            "source": "perp_market_snapshots/live-collected; retrospective selected BTC/ETH",
            "dataset_sha256": hashlib.sha256(manifest.encode()).hexdigest(),
            "assets": assets, "excluded": excluded, "trades": trades, "censored_paths": censored,
            "net_pnl_bps": None, "funding": "UNMEASURED", "passive_fills": "UNPROVEN",
            "fee_rates": "historical sensitivities, not verified current account rates",
            "verdict": verdict, "next_action": action}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary-only", action="store_true", help="omit per-path output only")
    args = ap.parse_args(argv)
    url = os.environ.get("DATABASE_URL_RO", "").strip()
    if not url:
        print('HOLD: DATABASE_URL_RO is required; no writable-URL fallback.')
        return 1
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    import psycopg
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SELECT ticker, captured_at, bid, ask, premium_bps, reference_price,"
                        " settlement_mark_price FROM perp_market_snapshots"
                        " WHERE ticker IN (%s, %s) AND captured_at >= %s AND captured_at < %s"
                        " ORDER BY ticker, captured_at", (*TICKERS, START, END))
            rows = [dict(zip(FIELDS, r, strict=True)) for r in cur.fetchall()]
    for r in rows:
        r["captured_at"] = r["captured_at"].astimezone(timezone.utc)
        for k in FIELDS[2:]:
            r[k] = number(r[k])
    # Deterministic ties; duplicate timestamps remain visible and make paths ineligible.
    rows.sort(key=lambda r: (r["ticker"], r["captured_at"],
                            json.dumps(r, default=str, sort_keys=True)))
    report = summarize(rows)
    if args.summary_only:
        report.pop("trades")
        report.pop("censored_paths")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
