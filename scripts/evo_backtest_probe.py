"""Live read-only smoke/proving test for Evo historical backtest datasets.

Runs the REAL production path — `kalshi_bot.evo.sandbox.run_backtest` — over the
REAL production database for two mmsell-style specs and prints bounded evidence.
Nothing is written: every call uses `persist=False` and `charge_budget=False`.

The ordinary one-pass form is a connectivity smoke test. A D1 proving run must use
an explicit closed historical window, repeat it, and require complete (untruncated)
replay; otherwise the sandbox's wall-clock guard can select a machine-speed-dependent
prefix of a large corpus.

Usage (via the ops channel):
    {"type":"script","name":"evo_backtest_probe"}
    {"type":"script","name":"evo_backtest_probe","args":["--dataset","mmsell"]}
    {"type":"script","name":"evo_backtest_probe","args":[
      "--dataset","backfill_weather",
      "--date-from","2026-08-01","--date-to","2026-08-03",
      "--repeat","2","--require-complete"
    ]}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mmsell-style specs (entry the expensive/NO side; exit at settlement). The taker variant
# enters whenever a market is in-band, so it exercises the full replay+settlement path on
# every replayable market; the maker variant is the realistic mmsell resting-order path.
_SPECS = [
    {
        "name": "mmsell_probe_taker",
        "family": "mmsell",
        "universe": {
            "series_prefixes": [],
            "min_volume": 0,
            "max_spread_cents": 99,
            "min_hours_to_close": 0.0,
            "max_hours_to_close": 100000,
        },
        "entry": {
            "side": "no",
            "style": "taker",
            "min_price_cents": 1,
            "max_price_cents": 99,
            "size_contracts": 5,
        },
        "exit": {"mode": "settlement"},
        "risk": {
            "max_concurrent_positions": 5,
            "max_per_event": 1,
            "max_cost_per_position_usd": 50,
        },
    },
    {
        "name": "mmsell_probe_maker",
        "family": "mmsell",
        "universe": {
            "series_prefixes": [],
            "min_volume": 0,
            "max_spread_cents": 99,
            "min_hours_to_close": 0.0,
            "max_hours_to_close": 100000,
        },
        "entry": {
            "side": "no",
            "style": "maker",
            "min_price_cents": 1,
            "max_price_cents": 99,
            "size_contracts": 5,
        },
        "exit": {"mode": "settlement"},
        "risk": {
            "max_concurrent_positions": 5,
            "max_per_event": 1,
            "max_cost_per_position_usd": 50,
        },
    },
]


def _ensure_deps() -> None:
    req = os.path.join(_ROOT, "requirements.txt")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "-r", req],
        stdout=subprocess.DEVNULL,
    )


def _session(url: str):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # SQLAlchemy's default postgres driver is psycopg2 (not installed); force psycopg v3.
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            url = "postgresql+psycopg://" + url[len(scheme) :]
            break
    engine = create_engine(url)
    return sessionmaker(bind=engine)()


def _stable_result(result: dict) -> dict:
    """Remove runtime-only fields before comparing two identical historical replays."""
    return {key: value for key, value in result.items() if key != "elapsed_ms"}


def _fingerprint(result: dict) -> str:
    payload = json.dumps(
        _stable_result(result),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _line(result: dict) -> str:
    return (
        f"  markets_considered={result['markets_considered']} "
        f"rows={result['rows_processed']} truncated={result['truncated']}\n"
        f"  n_trades={result['n_trades']} wins={result['wins']} "
        f"win_rate={result['win_rate']}\n"
        f"  per_trade_usd={result['per_trade_usd']} "
        f"total_pnl_usd={result['total_pnl_usd']} "
        f"provenance={result['provenance']}\n"
        f"  fingerprint={_fingerprint(result)}"
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="mmsell", help="dataset to backtest (default mmsell)")
    ap.add_argument("--date-from", help="inclusive historical date (YYYY-MM-DD)")
    ap.add_argument("--date-to", help="inclusive historical date (YYYY-MM-DD)")
    ap.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="repeat each identical replay 1-3 times and compare fingerprints",
    )
    ap.add_argument(
        "--require-complete",
        action="store_true",
        help="fail when any replay hits the row or wall-clock truncation guard",
    )
    args = ap.parse_args(argv)

    if bool(args.date_from) != bool(args.date_to):
        ap.error("--date-from and --date-to must be supplied together")
    if args.date_from:
        try:
            start = date.fromisoformat(args.date_from)
            end = date.fromisoformat(args.date_to)
        except ValueError as exc:
            ap.error(f"invalid ISO date: {exc}")
        if start > end:
            ap.error("--date-from must be on or before --date-to")
    if not 1 <= args.repeat <= 3:
        ap.error("--repeat must be between 1 and 3")
    if args.require_complete and not args.date_from:
        ap.error("--require-complete requires an explicit closed date window")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    url = os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL")
    if not url:
        print("no DATABASE_URL_RO in env", file=sys.stderr)
        return 1

    _ensure_deps()
    sys.path.insert(0, _ROOT)
    from kalshi_bot.evo import sandbox
    from kalshi_bot.evo.config import EvoSettings

    session = _session(url)
    settings = EvoSettings(_env_file=None)
    window = (
        f"{args.date_from}..{args.date_to}" if args.date_from else "open/default bounds"
    )
    print(
        f"# evo backtest live probe — REAL sandbox.run_backtest("
        f"dataset={args.dataset!r}, persist=False) over the production DB "
        f"(read-only, nothing written)"
    )
    print(
        f"# window={window}; repeats={args.repeat}; "
        f"require_complete={args.require_complete}"
    )

    produced = 0
    reproducible = 0
    complete = 0
    for spec in _SPECS:
        results: list[dict] = []
        errors: list[str] = []
        e = spec["entry"]
        print(
            f"\n=== {spec['name']} (entry {e['side']}/{e['style']}, "
            f"exit {spec['exit']['mode']}) ==="
        )
        for repetition in range(1, args.repeat + 1):
            result, err = sandbox.run_backtest(
                session,
                settings,
                agent_uuid="probe",
                cohort_id=0,
                spec_doc=spec,
                dataset=args.dataset,
                date_from=args.date_from,
                date_to=args.date_to,
                charge_budget=False,
                persist=False,
            )
            print(f"\n  -- repetition {repetition}/{args.repeat} --")
            if err:
                errors.append(str(err))
                print(f"  ERROR: {err}")
                continue
            results.append(result)
            print(_line(result))

        if len(results) == args.repeat and all(result["n_trades"] for result in results):
            produced += 1
        fingerprints = {_fingerprint(result) for result in results}
        if len(results) == args.repeat and len(fingerprints) == 1:
            reproducible += 1
        if len(results) == args.repeat and not any(
            result["truncated"] for result in results
        ):
            complete += 1
        if errors:
            print(f"  errors={len(errors)}")
        print(
            f"  reproducible={len(results) == args.repeat and len(fingerprints) == 1} "
            f"complete={len(results) == args.repeat and not any(result['truncated'] for result in results)}"
        )

    total = len(_SPECS)
    passed = (
        produced == total
        and reproducible == total
        and (not args.require_complete or complete == total)
    )
    print(
        f"\n=== VERDICT: {'PASS' if passed else 'FAIL'} — "
        f"trades {produced}/{total}, reproducible {reproducible}/{total}, "
        f"complete {complete}/{total} ==="
    )
    if passed:
        print(
            f"  PASS — dataset={args.dataset!r}, window={window!r} executes end-to-end "
            "on real settled markets and identical replays return identical evidence."
        )
        return 0
    print(
        "  FAIL — the real-dataset result is not yet a proving artifact. Require a "
        "non-empty, identical, untruncated result for every spec."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
