"""Live read-only smoke test for the mmsell backtest dataset (PR3).

Answers "can an agent actually run_backtest dataset='mmsell' and get a real result?" by
running the REAL production code path — kalshi_bot.evo.sandbox.run_backtest — over the
REAL production database (read-only), for a couple of mmsell-style specs, and printing
the resulting trade counts and P&L.

Nothing is written: run_backtest is called with persist=False + charge_budget=False, so
the whole call is pure SELECT and safe against the read-only DB role. This is a testing
harness, not one of the stdlib-only analysis scripts: the real sandbox imports the app's
stack (SQLAlchemy, pydantic, httpx via paper.engine), so the probe pip-installs
requirements.txt at runtime — only when THIS script runs, so other ops requests are
unaffected.

Usage (via the ops channel):
    {"type":"script","name":"evo_backtest_probe"}
    {"type":"script","name":"evo_backtest_probe","args":["--dataset","mmsell"]}
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# mmsell-style specs (entry the expensive/NO side; exit at settlement). The taker variant
# enters whenever a market is in-band, so it exercises the full replay+settlement path on
# every replayable market; the maker variant is the realistic mmsell resting-order path.
_SPECS = [
    {
        "name": "mmsell_probe_taker",
        "family": "mmsell",
        "universe": {"series_prefixes": [], "min_volume": 0, "max_spread_cents": 99,
                     "min_hours_to_close": 0.0, "max_hours_to_close": 100000},
        "entry": {"side": "no", "style": "taker", "min_price_cents": 1,
                  "max_price_cents": 99, "size_contracts": 5},
        "exit": {"mode": "settlement"},
        "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
                 "max_cost_per_position_usd": 50},
    },
    {
        "name": "mmsell_probe_maker",
        "family": "mmsell",
        "universe": {"series_prefixes": [], "min_volume": 0, "max_spread_cents": 99,
                     "min_hours_to_close": 0.0, "max_hours_to_close": 100000},
        "entry": {"side": "no", "style": "maker", "min_price_cents": 1,
                  "max_price_cents": 99, "size_contracts": 5},
        "exit": {"mode": "settlement"},
        "risk": {"max_concurrent_positions": 5, "max_per_event": 1,
                 "max_cost_per_position_usd": 50},
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
            url = "postgresql+psycopg://" + url[len(scheme):]
            break
    engine = create_engine(url)
    return sessionmaker(bind=engine)()


def _line(result: dict) -> str:
    return (
        f"  markets_considered={result['markets_considered']} "
        f"rows={result['rows_processed']} truncated={result['truncated']}\n"
        f"  n_trades={result['n_trades']} wins={result['wins']} "
        f"win_rate={result['win_rate']}\n"
        f"  per_trade_usd={result['per_trade_usd']} "
        f"total_pnl_usd={result['total_pnl_usd']} provenance={result['provenance']}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="mmsell", help="dataset to backtest (default mmsell)")
    args = ap.parse_args(argv)

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
    print(f"# evo backtest live probe — REAL sandbox.run_backtest(dataset={args.dataset!r}, "
          f"persist=False) over the production DB (read-only, nothing written)")

    produced = 0
    for spec in _SPECS:
        result, err = sandbox.run_backtest(
            session, settings, agent_uuid="probe", cohort_id=0, spec_doc=spec,
            dataset=args.dataset, charge_budget=False, persist=False,
        )
        e = spec["entry"]
        print(f"\n=== {spec['name']} (entry {e['side']}/{e['style']}, "
              f"exit {spec['exit']['mode']}) ===")
        if err:
            print(f"  ERROR: {err}")
            continue
        print(_line(result))
        if result["n_trades"]:
            produced += 1

    print(f"\n=== VERDICT: {produced}/{len(_SPECS)} specs produced trades on real "
          f"{args.dataset} markets ===")
    if produced:
        print(f"  PASS — an agent's run_backtest dataset='{args.dataset}' executes end-to-end "
              "on the real settled markets and returns a P&L result.")
        return 0
    print("  No trades produced — the replay ran but no spec entered (check entry bands vs "
          "the live price paths, or coverage is still too thin).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
