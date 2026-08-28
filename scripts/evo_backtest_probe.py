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
    """A session pinned to ONE read-only REPEATABLE READ snapshot for the whole run.

    READ COMMITTED — Postgres's default, and what a plain session gives — takes a NEW
    snapshot for every statement. Two repetitions would then read whatever the database
    happened to hold at each moment, and "identical fingerprints" would be a claim about
    a quiet database rather than about deterministic replay. REPEATABLE READ fixes the
    snapshot at the transaction's first read and holds it, so every repetition genuinely
    sees the same rows. READ ONLY is belt-and-braces on top of the read-only DB role.

    The caller must therefore never commit or roll back mid-run: the snapshot dies with
    the transaction. `session.expunge_all()` between repetitions is safe precisely because
    it clears the identity map WITHOUT touching the transaction.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # SQLAlchemy's default postgres driver is psycopg2 (not installed); force psycopg v3.
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            url = "postgresql+psycopg://" + url[len(scheme) :]
            break
    engine = create_engine(url).execution_options(
        isolation_level="REPEATABLE READ",
        postgresql_readonly=True,
    )
    return sessionmaker(bind=engine)()


def _snapshot_ok(session) -> tuple[bool, str]:
    """Confirm the transaction really is READ ONLY REPEATABLE READ before proving anything.

    Asked of the server, not assumed from the engine options — a driver or pool that
    silently ignored them would otherwise leave the whole run resting on a false premise.
    This is also the statement that OPENS the transaction, and therefore the moment the
    snapshot is taken: everything read afterwards belongs to it.
    """
    from sqlalchemy import text

    iso = session.execute(text("SHOW transaction_isolation")).scalar()
    ro = session.execute(text("SHOW transaction_read_only")).scalar()
    ok = str(iso).lower() == "repeatable read" and str(ro).lower() == "on"
    return ok, f"transaction_isolation={iso} transaction_read_only={ro}"


#: Fields whose value depends only on how long the machine took, never on the corpus.
_RUNTIME_ONLY_FIELDS = ("elapsed_ms",)

#: Aggregates whose value depends on the ORDER the adapter yielded markets in, not on
#: WHICH markets it yielded. The replay appends trades in market-iteration order and walks
#: that sequence to find the peak-to-trough drop, so a different tie order over the same
#: trade set moves this number and nothing else.
#:
#: WS-006 D7 is now RESOLVED — every adapter orders totally (`close_time, market_ticker`
#: and the sibling tiebreakers) — so an ordering-only divergence should no longer be
#: possible. The category is kept because a claim that it cannot happen is worth testing
#: on real data rather than asserting from a diff.
_ORDER_DEPENDENT_FIELDS = ("max_drawdown_usd",)

#: Per-trade fields that identify WHICH trade this is and what it did. Hashing these —
#: not the aggregates computed from them — is what makes "the same trade set" a claim
#: about actual trades. Runtime-varying or derived fields are excluded.
_TRADE_IDENTITY_FIELDS = (
    "ticker",
    "side",
    "style",
    "quantity",
    "entry_price_cents",
    "exit_price_cents",
    "entered_at",
    "exited_at",
    "exit",
    "settled",
    "win",
    "pnl",
    "fees",
)

#: Cents are floats through the replay, so bit-identical equality would make the
#: fingerprint hostage to representation rather than to evidence.
_MONEY_DP = 6


def _stable_result(result: dict) -> dict:
    """Drop runtime-only fields, and the trade tape, before comparing two replays.

    The tape is fingerprinted separately (`_trade_fingerprint`); leaving it inside the
    aggregate hash would conflate "the same summary" with "the same trades".
    """
    skip = set(_RUNTIME_ONLY_FIELDS) | {"trades"}
    return {k: v for k, v in result.items() if k not in skip}


def _hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _fingerprint(result: dict) -> str:
    """The strict aggregate fingerprint: every reported number except elapsed time."""
    return _hash(_stable_result(result))


def _canonical_trade(trade: dict) -> list:
    row = []
    for field in _TRADE_IDENTITY_FIELDS:
        value = trade.get(field)
        if isinstance(value, float):
            value = round(value, _MONEY_DP)
        row.append(str(value))
    return row


def _trade_fingerprint(result: dict) -> str | None:
    """Identity hash over the ACTUAL trade tape, canonicalized and order-independent.

    Sorting before hashing is the whole point: it answers "did the replay produce the
    same set of trades?" separately from "did it produce them in the same order?".
    Returns None when the tape was not requested, so a missing tape can never be
    silently read as agreement.
    """
    trades = result.get("trades")
    if trades is None:
        return None
    return _hash(sorted(_canonical_trade(t) for t in trades))


def _differing_fields(results: list[dict]) -> list[str]:
    """Which stable aggregate fields are not identical across every repetition."""
    if len(results) < 2:
        return []
    keys: set[str] = set()
    for r in results:
        keys |= set(_stable_result(r))
    return [k for k in sorted(keys) if len({_hash({k: _stable_result(r).get(k)}) for r in results}) > 1]


def _market_manifest(session, dataset: str, date_from: str | None, date_to: str | None):
    """Hash the market rows the window selects, read directly rather than via the replay.

    This is the leg the aggregates cannot supply. `markets_considered` is a COUNT; two
    different market sets of the same size produce the same count, so a matching count
    proves nothing about identity. Reading the tickers isolates the QUESTION "did the
    database return the same rows twice?" from everything the replay does with them.

    Returns (fingerprint, n_markets) or (None, None) when the dataset has no manifest
    query here — in which case the run says the manifest leg is UNCOVERED rather than
    treating its absence as a pass.
    """
    from sqlalchemy import select

    from kalshi_bot.models import BackfillWeatherMarket

    # Only backfill_weather is covered: the manifest must mirror its adapter's selection
    # EXACTLY, and a near-miss (e.g. omitting the econ adapter's regime filter) would be
    # worse than no manifest — it would report agreement about the wrong row set.
    if dataset != "backfill_weather":
        return None, None
    model = BackfillWeatherMarket
    q = select(model.market_ticker).where(
        model.result.in_(("yes", "no")), model.candles_fetched.is_(True)
    )
    if date_from:
        q = q.where(model.target_date >= date_from, model.target_date <= date_to)
    tickers = sorted(session.scalars(q.order_by(model.market_ticker)))
    return _hash(tickers), len(tickers)


def _assess(results: list[dict], repeat: int, manifests: list) -> dict:
    """Turn a spec's repetitions into the pre-registered PASS conditions.

    Every condition is False unless every requested repetition actually returned a
    result: a spec that errored out has not been proven reproducible, it has been
    proven nothing.
    """
    ran = len(results) == repeat and repeat >= 1
    trade_fps = {_trade_fingerprint(r) for r in results} if ran else set()
    covered = bool(manifests) and None not in manifests
    manifest_fps = set(manifests)
    return {
        "ran": ran,
        "non_empty": ran and all(r["n_trades"] for r in results),
        "reproducible": ran and len({_fingerprint(r) for r in results}) == 1,
        # None (tape not requested) can never count as agreement.
        "trades_reproducible": ran and None not in trade_fps and len(trade_fps) == 1,
        "manifest_covered": covered,
        "manifest_reproducible": covered and len(manifest_fps) == 1,
        "untruncated": ran and not any(r["truncated"] for r in results),
        "empty_window": ran and all(not r["markets_considered"] for r in results),
        "differing_fields": _differing_fields(results) if ran else [],
    }


def _diagnose(a: dict) -> str | None:
    """One line naming WHY a spec failed, so a red run is actionable on its own."""
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
    if a["manifest_covered"] and not a["manifest_reproducible"]:
        return (
            "the market manifest itself differed between repetitions: the same window "
            "returned different market rows. Nothing downstream of that is evidence."
        )
    if not a["manifest_covered"]:
        return (
            "the market-manifest leg is UNCOVERED for this dataset — the identity of the "
            "selected markets was not independently verified, so matching aggregates and "
            "tapes are consistent-with, not proven. Treat as a diagnostic, not a proof."
        )
    if not a["trades_reproducible"]:
        return (
            "the trade tape differed between repetitions — a genuine replay defect, not "
            "an ordering artifact. Compare the per-trade tapes, not the aggregates."
        )
    if not a["reproducible"]:
        if set(a["differing_fields"]) <= set(_ORDER_DEPENDENT_FIELDS):
            return (
                "ordering-only divergence in "
                f"{', '.join(a['differing_fields'])}. This is now a PROVEN claim, not an "
                "inference: the market manifest and the canonicalized trade tape both "
                "matched, so the same markets produced the same trades in a different "
                "sequence. D7 was supposed to make this impossible — a total ORDER BY is "
                "not holding, and that is the defect to chase."
            )
        return (
            "the aggregates differ beyond the order-dependent set; fields that differ: "
            f"{', '.join(a['differing_fields']) or 'unknown'}"
        )
    return None


def _line(result: dict, manifest, n_markets) -> str:
    tfp = _trade_fingerprint(result)
    return (
        f"  markets_considered={result['markets_considered']} "
        f"rows={result['rows_processed']} truncated={result['truncated']}\n"
        f"  n_trades={result['n_trades']} wins={result['wins']} "
        f"win_rate={result['win_rate']}\n"
        f"  per_trade_usd={result['per_trade_usd']} "
        f"total_pnl_usd={result['total_pnl_usd']} "
        f"provenance={result['provenance']}\n"
        f"  fingerprint={_fingerprint(result)}\n"
        f"  trade_fingerprint={tfp or 'UNAVAILABLE (tape not returned)'}\n"
        f"  market_manifest={manifest or 'UNCOVERED for this dataset'}"
        + (f" (n_markets={n_markets})" if manifest else "")
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

    # Opens the transaction, and with it the snapshot every repetition below will read.
    snapshot_ok, snapshot_detail = _snapshot_ok(session)
    print(f"# snapshot: {snapshot_detail}")
    if not snapshot_ok and args.require_complete:
        print(
            "\n=== VERDICT: FAIL — the session is not a read-only REPEATABLE READ "
            "transaction, so repetitions would not share one snapshot. A proving run "
            "refuses to start on that footing. ==="
        )
        return 1

    passes: list[bool] = []
    summary: list[str] = []
    manifest_uncovered = False
    for spec in _SPECS:
        results: list[dict] = []
        manifests: list = []
        errors: list[str] = []
        e = spec["entry"]
        print(
            f"\n=== {spec['name']} (entry {e['side']}/{e['style']}, "
            f"exit {spec['exit']['mode']}) ==="
        )
        for repetition in range(1, args.repeat + 1):
            # Drop the ORM identity map so repetition N re-executes its SELECTs instead of
            # replaying cached objects. Deliberately NOT a rollback: the snapshot lives in
            # the transaction, and ending it would defeat the whole point.
            session.expunge_all()
            manifest, n_markets = _market_manifest(
                session, args.dataset, args.date_from, args.date_to
            )
            manifests.append(manifest)
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
                # The tape is the evidence. Aggregates cannot distinguish "the same
                # trades" from "different trades that happen to sum the same".
                return_trades=True,
            )
            print(f"\n  -- repetition {repetition}/{args.repeat} --")
            if err:
                errors.append(str(err))
                print(f"  ERROR: {err}")
                continue
            results.append(result)
            print(_line(result, manifest, n_markets))

        a = _assess(results, args.repeat, manifests)
        spec_passed = (
            a["non_empty"]
            and a["reproducible"]
            and a["trades_reproducible"]
            and a["manifest_reproducible"]
            and (not args.require_complete or a["untruncated"])
        )
        passes.append(spec_passed)
        manifest_uncovered = manifest_uncovered or not a["manifest_covered"]
        if errors:
            print(f"\n  errors={len(errors)}")
        print(
            f"\n  non_empty={a['non_empty']} reproducible={a['reproducible']} "
            f"trades_reproducible={a['trades_reproducible']} "
            f"manifest_reproducible={a['manifest_reproducible']} "
            f"(covered={a['manifest_covered']}) untruncated={a['untruncated']}"
        )
        why = _diagnose(a)
        if why:
            print(f"  WHY: {why}")
        summary.append(
            f"  {spec['name']}: {'PASS' if spec_passed else 'FAIL'}\n"
            f"    result={_fingerprint(results[0]) if results else 'none'}\n"
            f"    trades={(_trade_fingerprint(results[0]) if results else None) or 'none'}\n"
            f"    manifest={manifests[0] if manifests and manifests[0] else 'uncovered'}"
        )

    total = len(_SPECS)
    passed = all(passes) and len(passes) == total and snapshot_ok
    print(
        f"\n=== VERDICT: {'PASS' if passed else 'FAIL'} — "
        f"{sum(passes)}/{total} specs met every pre-registered condition "
        f"(non-empty; identical market manifest, trade tape and aggregates across "
        f"{args.repeat} repetition(s) on one snapshot"
        + (", untruncated" if args.require_complete else "")
        + ") ==="
    )
    # Printed unconditionally so a LATER, independent ops run of the same request can be
    # compared against this one by eye. Cross-process agreement is the half of the claim
    # that repetitions inside a single transaction cannot give.
    print("\n# stable fingerprints (compare across independent runs of this request)")
    for row in summary:
        print(row)
    if passed:
        print(
            f"\n  PASS — dataset={args.dataset!r}, window={window!r} executes end-to-end "
            "on real settled markets; the same window returned the same market rows and "
            "the same trades on every repetition of one fixed snapshot."
            + (
                "\n  CAVEAT: the market-manifest leg is uncovered for this dataset — "
                "market identity was not independently verified."
                if manifest_uncovered
                else ""
            )
            + "\n  P&L above is reconciliation output only. It is not an edge claim, an "
            "experiment result, or authority to create a prospective cohort."
        )
        return 0
    print(
        "\n  FAIL — the real-dataset result is not a proving artifact. Every spec must be "
        "non-empty, and identical across repetitions in market manifest, trade tape and "
        "aggregates"
        + (", and untruncated" if args.require_complete else "")
        + ". See the WHY line under each failing spec."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
