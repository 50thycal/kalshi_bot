"""Synthetic benchmark: broad-full versus selective catalog history (WS-023 / DEC-034).

Replays one synthetic market lifecycle workload into two fresh catalogs and reports future
revision bytes, file growth, upsert latency and Python peak memory; then compares whole-table
and per-series evaluation memory. It measures this code on this machine with stated
assumptions. It is NOT a production saving, a Railway RAM figure, or a statistical result.

    python scripts/catalog_selective_benchmark.py --markets 20000 --selected-share 0.05
"""

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kalshi_bot.catalog import selection  # noqa: E402
from kalshi_bot.catalog import store as store_module
from kalshi_bot.catalog.evaluators import refresh  # noqa: E402
from kalshi_bot.catalog.store import Store  # noqa: E402

# Shape of a real public market payload (KXBTCD-26AUG0717-T65999.99, 2026-10-10).
TEMPLATE = {
    "can_close_early": True,
    "close_time": "2026-08-07T21:00:00Z",
    "created_time": "2026-07-31T09:00:31.855033Z",
    "exchange_index": 0,
    "expected_expiration_time": "2026-08-07T21:05:00Z",
    "expiration_time": "2026-08-14T21:00:00Z",
    "last_price_dollars": "0.0100",
    "latest_expiration_time": "2026-08-14T21:00:00Z",
    "market_type": "binary",
    "no_ask_dollars": "1.0000",
    "no_bid_dollars": "0.0000",
    "notional_value_dollars": "1.0000",
    "occurrence_datetime": "2026-08-07T21:05:00Z",
    "open_interest_fp": "93320.36",
    "open_time": "2026-07-31T20:00:00Z",
    "previous_price_dollars": "0.0100",
    "previous_yes_ask_dollars": "1.0000",
    "previous_yes_bid_dollars": "0.0000",
    "price_level_structure": "linear_cent",
    "price_ranges": [{"end": "1.0000", "start": "0.0000", "step": "0.0100"}],
    "rules_primary": (
        "If the simple average of the sixty seconds of CF Benchmarks' Bitcoin Real-Time Index "
        "(BRTI) before 5 PM EDT is above {strike} at 5 PM EDT on Aug 7, 2026, then the market "
        "resolves to Yes."
    ),
    "rules_secondary": (
        "Not all cryptocurrency price data is the same. While checking a source like Google or "
        "Coinbase may help guide your decision, the price used to determine this market is based "
        "on CF Benchmarks' corresponding Real Time Index (RTI). At the last minute before "
        "expiration, 60 RTI prices are collected. The official and final value is the average "
        "of these prices."
    ),
    "settlement_bounds_type": "default",
    "settlement_timer_seconds": 60,
    "strike_type": "greater",
    "title": "Bitcoin price on Aug 7, 2026?",
    "volume_24h_fp": "0.00",
    "volume_fp": "202956.36",
    "yes_ask_dollars": "1.0000",
    "yes_ask_size_fp": "0.00",
    "yes_bid_dollars": "0.0000",
    "yes_bid_size_fp": "0.00",
}
LIFECYCLE = ("active", "closed", "determined", "finalized")


def payload(series, index, step, rng):
    ticker = f"{series}-26OCT{index % 28 + 1:02d}-T{index}"
    raw = {
        **TEMPLATE,
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "rules_primary": TEMPLATE["rules_primary"].format(strike=1000 + index),
        "floor_strike": 1000 + index,
        "yes_sub_title": f"{1000 + index} or above",
        "no_sub_title": f"{1000 + index} or above",
        "subtitle": f"{1000 + index} or above",
        "status": LIFECYCLE[min(step, len(LIFECYCLE) - 1)],
        "yes_bid_dollars": f"{rng.random():.4f}",
        "volume_fp": f"{rng.random() * 1e5:.2f}",
    }
    if raw["status"] == "finalized":
        raw.update(result="no", settlement_ts="2026-08-07T21:02:25Z",
                   settlement_value_dollars="0.0000", expiration_value="64937.47")
    return ticker, raw


def write_phase(path, policy, args):
    rng = random.Random(args.seed)
    with patch.object(selection, "POLICY", policy), \
            patch.object(store_module, "DICTIONARY_WRITES", args.dictionary):
        store = Store(path)
        while not selection.membership_ready_for(store):
            selection.bootstrap_page(store)
        series_names = [f"KXS{i:03d}" for i in range(args.series)]
        selected_series = set(series_names[: int(len(series_names) * args.selected_share)])
        for name in sorted(selected_series):
            with store.connect() as db:
                selection.select_in(db, "series", name, "research", "benchmark")
        before = os.path.getsize(path)
        latencies = []
        tracemalloc.start()
        for step in range(len(LIFECYCLE) + args.quote_updates):
            lifecycle_step = min(step, len(LIFECYCLE) - 1)
            for index in range(args.markets):
                series = series_names[index % len(series_names)]
                ticker, raw = payload(series, index, lifecycle_step, rng)
                started = time.perf_counter()
                store.upsert("market", ticker, raw, series)
                latencies.append(time.perf_counter() - started)
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        with store.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            revision_bytes, rows = db.execute(
                "SELECT coalesce(sum(length(CAST(document AS BLOB))),0),count(*) FROM revisions"
            ).fetchone()
        writes = store.status()["history_policy"]["revision_writes"]
    latencies.sort()
    return {
        "policy": policy,
        "revision_rows": rows,
        "revision_document_bytes": revision_bytes,
        "bytes_per_revision": revision_bytes / rows if rows else None,
        "file_growth_bytes": os.path.getsize(path) - before,
        "full_revisions": writes.get("full", 0),
        "compact_revisions": writes.get("compact", 0),
        "upsert_mean_ms": statistics.mean(latencies) * 1000,
        "upsert_p95_ms": latencies[int(len(latencies) * 0.95)] * 1000,
        "python_peak_bytes_during_writes": peak,
    }


def evaluation_phase(path, args):
    rng = random.Random(args.seed)
    store = Store(path)
    rows, ident = [], 0
    weights = [args.skew ** -i for i in range(args.evidence_series)]
    total = sum(weights)
    for i, weight in enumerate(weights):
        for _ in range(max(1, int(args.evidence_rows * weight / total))):
            ident += 1
            rows.append({
                "id": ident,
                "market_ticker": f"KXE{i:03d}-26OCT{ident % 28 + 1:02d}-T{ident % 97}",
                "strategy": f"mmsell{ident % 12}",
                "side": "no",
                "assumed_price": 80 + ident % 19,
                "quantity": 1 + ident % 3,
                "pnl": rng.choice((0.05, 0.10, -0.90)),
                "fees": 0.01,
                "status": "settled",
                "created_at": "2026-10-01T00:00:00+00:00",
                "closed_at": "2026-10-02T00:00:00+00:00",
                "fill_assumption": "maker",
                "experiment_deployment_arm_id": ident % 5,
                "notes": "x" * 200,  # pads to ~530 B, the production mean evidence document
            })
    for start in range(0, len(rows), 1000):
        store.evidence_page("paper", rows[start : start + 1000], {"after": start})
    largest = max(sum(1 for r in rows if r["market_ticker"].startswith(f"KXE{i:03d}-"))
                  for i in range(args.evidence_series))
    as_of = datetime(2026, 10, 11, tzinfo=timezone.utc)
    del rows
    results = {}
    for name, partitioned in (("whole_table", False), ("per_series", True)):
        tracemalloc.start()
        started = time.perf_counter()
        contexts = refresh(store, as_of, partitioned=partitioned)
        seconds = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results[name] = {"contexts": contexts, "seconds": seconds, "python_peak_bytes": peak}
    return {"evidence_rows": ident, "series": args.evidence_series,
            "largest_series_rows": largest, **results}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markets", type=int, default=20000)
    parser.add_argument("--series", type=int, default=200)
    parser.add_argument("--selected-share", type=float, default=0.05,
                        help="share of series explicitly selected (detailed history)")
    parser.add_argument("--quote-updates", type=int, default=2,
                        help="price/volume-only passes after the lifecycle (no new revision)")
    parser.add_argument("--dictionary", action="store_true",
                        help="enable preset-dictionary writes (production has the flag)")
    parser.add_argument("--evidence-rows", type=int, default=140000)
    parser.add_argument("--evidence-series", type=int, default=773)
    parser.add_argument("--skew", type=float, default=1.02,
                        help="geometric decay of evidence rows per series")
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--skip-evaluation", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        report = {
            "assumptions": {
                **vars(args),
                "lifecycle_revisions_per_market": len(LIFECYCLE),
                "payload_shape": "real public KXBTCD market fields, synthetic strikes/prices",
                "not_a_production_saving": True,
                "python_peak_is_not_railway_rss": True,
            },
            "broad_full": write_phase(Path(folder) / "full.db", "broad_full", args),
            "selective": write_phase(Path(folder) / "selective.db", "selective", args),
        }
        full, selective = report["broad_full"], report["selective"]
        report["revision_bytes_ratio_selective_over_full"] = (
            selective["revision_document_bytes"] / full["revision_document_bytes"]
        )
        report["file_growth_ratio_selective_over_full"] = (
            selective["file_growth_bytes"] / full["file_growth_bytes"]
        )
        if not args.skip_evaluation:
            report["evaluation"] = evaluation_phase(Path(folder) / "evidence.db", args)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
