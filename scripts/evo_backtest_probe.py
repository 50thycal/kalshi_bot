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


#: Fields whose value depends only on how long the machine took, never on the corpus.
_RUNTIME_ONLY_FIELDS = ("elapsed_ms",)

#: Aggregates whose value depends on the ORDER the adapter yielded markets in, not on
#: WHICH markets it yielded. The replay appends trades in market-iteration order and
#: walks that sequence to find the peak-to-trough drop, so a different tie order over the
#: same trade set moves this number and nothing else.
#:
#: This matters because `_weather_markets` orders by `close_time` alone, and every bucket
#: market in one weather event shares a close_time. That is not a total order, so Postgres
#: may return tied markets in a different order between two reads of the same snapshot.
#: Making the adapter's ORDER BY total would change what every existing caller's replay
#: reports and is therefore a shared replay-semantics change (WS-006 D7, Platform Change
#: Review) — not something this read-only probe may do. What the probe CAN do is name the
#: divergence precisely instead of reporting an unexplained fingerprint mismatch.
_ORDER_DEPENDENT_FIELDS = ("max_drawdown_usd",)


def _stable_result(result: dict) -> dict:
    """Drop runtime-only fields before comparing two identical historical replays."""
    return {k: v for k, v in result.items() if k not in _RUNTIME_ONLY_FIELDS}


def _corpus_result(result: dict) -> dict:
    """The stable payload minus the order-dependent aggregates.

    Two replays agreeing here selected the same markets and produced the same trade set;
    they may still disagree on the equity-curve ordering.
    """
    return {k: v for k, v in _stable_result(result).items() if k not in _ORDER_DEPENDENT_FIELDS}


def _hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _fingerprint(result: dict) -> str:
    return _hash(_stable_result(result))


def _corpus_fingerprint(result: dict) -> str:
    return _hash(_corpus_result(result))


def _differing_fields(results: list[dict]) -> list[str]:
    """Which stable evidence fields are not identical across every repetition."""
    if len(results) < 2:
        return []
    first = _stable_result(results[0])
    keys = set(first)
    for other in results[1:]:
        keys |= set(_stable_result(other))
    differing = []
    for key in sorted(keys):
        values = {_hash({key: _stable_result(r).get(key)}) for r in results}
        if len(values) > 1:
            differing.append(key)
    return differing


def _assess(results: list[dict], repeat: int) -> dict:
    """Turn a spec's repetitions into the pre-registered PASS conditions.

    Every condition is False unless every requested repetition actually returned a
    result: a spec that errored out has not been proven reproducible, it has been
    proven nothing.
    """
    ran = len(results) == repeat and repeat >= 1
    differing = _differing_fields(results) if ran else []
    return {
        "ran": ran,
        "non_empty": ran and all(r["n_trades"] for r in results),
        "reproducible": ran and len({_fingerprint(r) for r in results}) == 1,
        "corpus_reproducible": ran and len({_corpus_fingerprint(r) for r in results}) == 1,
        "untruncated": ran and not any(r["truncated"] for r in results),
        "empty_window": ran and all(not r["markets_considered"] for r in results),
        "differing_fields": differing,
    }


def _diagnose(a: dict) -> str | None:
    """One line naming WHY a spec failed, so a red run is actionable on its own.

    A bare `reproducible=False` tells the operator that D1 is not clean but not what to
    do next; these are the three distinguishable causes.
    """
    if not a["ran"]:
        return "the replay did not complete every repetition — see the errors above"
    if a["empty_window"]:
        return (
            "empty window — the adapter selected 0 markets. This is a corpus-coverage "
            "finding, not evidence about the strategy: check the dataset actually has "
            "settled markets in the requested date range before rerunning."
        )
    if not a["non_empty"]:
        return (
            "markets were replayed but no spec entered — check the entry bands against "
            "the recorded price paths, not the harness."
        )
    if not a["untruncated"]:
        return (
            "truncated — the replay hit sandbox_max_seconds or sandbox_max_rows and "
            "therefore selected a machine-speed-dependent prefix. Narrow the window."
        )
    if not a["reproducible"]:
        if a["corpus_reproducible"] and set(a["differing_fields"]) <= set(_ORDER_DEPENDENT_FIELDS):
            return (
                "ordering-only divergence in "
                f"{', '.join(a['differing_fields'])}: the same markets and the same trade "
                "set reproduced, but the adapter's ORDER BY is not total so tied markets "
                "replayed in a different sequence. Root cause and remedy: WS-006 D7 "
                "(a deterministic tiebreaker is a shared replay-semantics change and "
                "belongs to Platform Change Review)."
            )
        return (
            "the corpus itself did not reproduce; fields that differ: "
            f"{', '.join(a['differing_fields']) or 'unknown'}"
        )
    return None


def _line(result: dict) -> str:
    return (
        f"  markets_considered={result['markets_considered']} "
        f"rows={result['rows_processed']} truncated={result['truncated']}\n"
        f"  n_trades={result['n_trades']} wins={result['wins']} "
        f"win_rate={result['win_rate']}\n"
        f"  per_trade_usd={result['per_trade_usd']} "
        f"total_pnl_usd={result['total_pnl_usd']} "
        f"provenance={result['provenance']}\n"
        f"  fingerprint={_fingerprint(result)}\n"
        f"  corpus_fingerprint={_corpus_fingerprint(result)}"
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

    passes: list[bool] = []
    summary: list[str] = []
    for spec in _SPECS:
        results: list[dict] = []
        errors: list[str] = []
        e = spec["entry"]
        print(
            f"\n=== {spec['name']} (entry {e['side']}/{e['style']}, "
            f"exit {spec['exit']['mode']}) ==="
        )
        for repetition in range(1, args.repeat + 1):
            # Drop the ORM identity map between repetitions so repetition N re-executes
            # its SELECTs instead of replaying cached objects — otherwise "identical
            # fingerprints" could just mean "we never read the database twice".
            # Deliberately NOT a rollback: staying inside one read transaction keeps
            # every repetition on the same snapshot, which is what determinism means
            # here. A changing database is a different question.
            session.expunge_all()
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

        a = _assess(results, args.repeat)
        spec_passed = (
            a["non_empty"]
            and a["reproducible"]
            and (not args.require_complete or a["untruncated"])
        )
        passes.append(spec_passed)
        if errors:
            print(f"\n  errors={len(errors)}")
        print(
            f"\n  non_empty={a['non_empty']} reproducible={a['reproducible']} "
            f"corpus_reproducible={a['corpus_reproducible']} "
            f"untruncated={a['untruncated']}"
        )
        why = _diagnose(a)
        if why:
            print(f"  WHY: {why}")
        summary.append(
            f"  {spec['name']}: {'PASS' if spec_passed else 'FAIL'} "
            f"fingerprint={_fingerprint(results[0]) if results else 'none'}"
        )

    total = len(_SPECS)
    passed = all(passes) and len(passes) == total
    print(
        f"\n=== VERDICT: {'PASS' if passed else 'FAIL'} — "
        f"{sum(passes)}/{total} specs met every pre-registered condition "
        f"(non-empty, fingerprint-identical across {args.repeat} repetition(s)"
        + (", untruncated" if args.require_complete else "")
        + ") ==="
    )
    # Printed unconditionally so a LATER, independent ops run of the same request can be
    # compared against this one by eye: cross-process agreement is stronger evidence than
    # two repetitions inside a single process.
    print("\n# stable fingerprints (compare across independent runs of this request)")
    for row in summary:
        print(row)
    if passed:
        print(
            f"\n  PASS — dataset={args.dataset!r}, window={window!r} executes end-to-end "
            "on real settled markets and identical replays return identical evidence.\n"
            "  P&L above is reconciliation output only. It is not an edge claim, an "
            "experiment result, or authority to create a prospective cohort."
        )
        return 0
    print(
        "\n  FAIL — the real-dataset result is not a proving artifact. Every spec must be "
        "non-empty and fingerprint-identical across repetitions"
        + (", and untruncated" if args.require_complete else "")
        + ". See the WHY line under each failing spec."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
