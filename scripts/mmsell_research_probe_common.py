"""Offline MMSELL diagnostics: existing read-only SQL exports -> reproducible reports.

Contracts: docs/MMSELL_CHATGPT_PROBES_20261004.md. No trading imports, order APIs,
environment writes, or database mutations. SQL exports execute through the existing
ops `db` channel; this module does not require a runner allowlist change.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from mmsell_market_types import classify

SINCE = "2026-09-16T00:00:00Z"
UNTIL = "2026-10-04T15:30:00Z"
SPLIT = dt.datetime(2026, 9, 24, tzinfo=dt.timezone.utc).timestamp()
SLOW = {"event_stat", "mention", "exact_score", "outright", "game_prop"}
BOOT_N = 5000
BOOT_SEED = 20261004
ORDER_FIELDS = (
    "tag", "ticker", "created", "limit", "quantity", "status", "filled", "fill_price",
    "fee", "first_fill", "settle", "settled_at", "context", "hot", "offset", "start",
    "end", "ticks", "valid_ticks", "first_tick", "last_tick", "trades", "control_at",
    "touch_at", "through_at", "open_count", "open_cap", "cancel_at", "close_at",
)
SLOT_FIELDS = (
    "tag", "ticker", "first_at", "first_cap_at", "placed", "open_cap", "paper_cap",
    "contest_cap", "tier", "paused", "cap_price", "outcomes", "repeated_rows", "settle",
)
WITHDRAWAL_FIELDS = (
    "tag", "ticker", "created", "paired_ticks", "coarse_windows", "clean_windows",
    "first_coarse", "signal_at", "removed", "levels", "yes_volume", "baseline_depth",
    "depth", "baseline_ask", "ask",
)


def export_sql(kind: str, since: str = SINCE, until: str = UNTIL) -> str:
    """A single SELECT with bounded base64 cells, safe for db_query's 200-char cap."""
    for value in (since, until):
        dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if "'" in value:
            raise ValueError("invalid timestamp")
    root = Path(__file__).parent / "sql"
    orders = (root / "mmsell_probe_orders.sql").read_text()
    if kind == "orders":
        query = orders
    elif kind == "slots":
        query = (root / "mmsell_probe_slots.sql").read_text()
    elif kind == "withdrawal":
        facts = orders.split(", rows AS (", 1)[0]
        query = (root / "mmsell_probe_withdrawal.sql").read_text().replace("__FACTS__", facts)
    else:
        raise ValueError(kind)
    query = query.replace("__SINCE__", since).replace("__UNTIL__", until)
    return (
        "WITH source AS (" + query + "), data AS (SELECT "
        "replace(encode(convert_to(coalesce(json_agg(row),'[]')::text,'UTF8'),"
        "'base64'),chr(10),'') AS b64 FROM source) "
        "SELECT n AS part, substring(b64 FROM 1+(n-1)*180 FOR 180) AS payload "
        "FROM data CROSS JOIN LATERAL generate_series(1,ceil(length(b64)/180.0)::int) n "
        "ORDER BY n"
    )


def decode_export(text: str) -> list[list]:
    """Refuse missing/duplicate/out-of-order parts and capped or incomplete exports."""
    if text.lstrip().startswith("["):
        return json.loads(text)
    if "output capped" in text or "Traceback" in text:
        raise ValueError("failed or truncated ops export")
    parts = []
    for line in text.splitlines():
        m = re.fullmatch(r"\s*(\d+)\s+([A-Za-z0-9+/=]+)\s*", line)
        if m:
            parts.append((int(m[1]), m[2]))
    if not parts or [p[0] for p in parts] != list(range(1, len(parts) + 1)):
        raise ValueError("missing, duplicate or unordered export chunks")
    return json.loads(base64.b64decode("".join(p[1] for p in parts), validate=True))


def read_rows(path: str, fields: tuple[str, ...]) -> list[dict]:
    rows = decode_export(Path(path).read_text())
    if any(len(r) != len(fields) for r in rows):
        raise ValueError("unexpected export schema")
    return [dict(zip(fields, r, strict=True)) for r in rows]


def market_type(ticker: str) -> str:
    return classify(ticker.split("-", 1)[0])[0]


def date_of(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).date().isoformat()


def bootstrap(rows: list[tuple[str, float]]) -> dict:
    """Settlement-date blocks include all retries/markets/contracts sharing a date."""
    blocks = defaultdict(list)
    for day, value in rows:
        if day is not None:
            blocks[day].append(value)
    vals = list(blocks.values())
    if len(vals) < 10:
        return {"dates": len(vals), "p05": None, "p95": None}
    rng = random.Random(BOOT_SEED)
    means = []
    sums = [(sum(v), len(v)) for v in vals]
    for _ in range(BOOT_N):
        chosen = [sums[rng.randrange(len(sums))] for _ in sums]
        means.append(sum(v[0] for v in chosen) / sum(v[1] for v in chosen))
    means.sort()
    return {"dates": len(vals), "p05": means[int(BOOT_N * .05)],
            "p95": means[int(BOOT_N * .95)]}


def actual_net(o: dict) -> float | None:
    if o["settle"] not in (0, 100):
        return None
    filled = float(o["filled"] or 0)
    if filled == 0:
        return 0.0 if o["status"] in ("canceled", "cancelled", "rejected", "filled", "executed") else None
    if o["fill_price"] is None or o["fee"] is None:
        return None
    return (o["settle"] - o["fill_price"] - o["fee"]) * min(filled, 1)


def normal_order(o: dict) -> bool:
    return bool(o["context"] and o["hot"] is False and o["offset"] == 0 and o["limit"] is not None)


def group_orders(orders: list[dict]):
    for tag in sorted({o["tag"] for o in orders}):
        yield tag, [o for o in orders if o["tag"] == tag]
        if tag == "Fmmsell10":
            yield tag + "/before-09-24", [o for o in orders if o["tag"] == tag and o["created"] < SPLIT]
            yield tag + "/from-09-24", [o for o in orders if o["tag"] == tag and o["created"] >= SPLIT]


def inverse_report(orders: list[dict]) -> dict:
    out = {"probe": "INVERSE-OFFSET", "offset_cents": -1,
           "scope": "retrospective scenarios; no executable fill guarantee", "groups": {}}
    for name, population in group_orders(orders):
        normal = [o for o in population if normal_order(o)]
        cohort = [o for o in normal if actual_net(o) is not None]
        filled = [o for o in cohort if o["filled"] > 0]
        pairs = Counter(o["ticker"] for o in cohort)
        result = {"orders": len(population), "normal_context_orders": len(normal),
                  "settled_terminal_fee_known_orders": len(cohort), "actual_filled_orders": len(filled),
                  "unique_markets": len(pairs), "retry_orders": sum(n - 1 for n in pairs.values()),
                  "missing_settlements": sum(o["settle"] is None for o in normal),
                  "actual_net_usd": sum(actual_net(o) for o in cohort) / 100,
                  "actual_losing_filled_orders": sum(o["settle"] == 0 for o in filled),
                  "orders_with_valid_book_tick": sum(o["valid_ticks"] > 0 for o in cohort),
                  "orders_with_yes_taker_print": sum(o["trades"] > 0 for o in cohort),
                  "control_print_recall": (sum(o["control_at"] is not None for o in filled) / len(filled)) if filled else None,
                  "scenarios": {}}
        result["tape_activity_order_share"] = (result["orders_with_yes_taker_print"] / len(cohort)) if cohort else 0
        result["tape_coverage_identified"] = False
        for label, field, fee in (("control_touch", "control_at", .02),
                                  ("lower_touch", "touch_at", .02),
                                  ("lower_strict_through", "through_at", .02),
                                  ("lower_touch_fee_0.10c", "touch_at", .10)):
            price_delta = 0 if label == "control_touch" else -1
            hits = [o for o in cohort if o[field] is not None]
            deltas = []
            total = 0.0
            for o in cohort:
                policy = (o["settle"] - (o["limit"] + price_delta) - fee) if o[field] is not None else 0.0
                total += policy
                deltas.append((date_of(o["settled_at"]), policy - actual_net(o)))
            interval = bootstrap(deltas)
            mean_delta = sum(v for _, v in deltas) / len(deltas) if deltas else None
            screen = (result["tape_coverage_identified"] and len(hits) >= 100 and interval["dates"] >= 10 and total > 0
                      and mean_delta is not None and mean_delta >= .5
                      and interval["p05"] is not None and interval["p05"] > 0)
            result["scenarios"][label] = {
                "proxy_hits": len(hits), "proxy_losses": sum(o["settle"] == 0 for o in hits),
                "net_usd": total / 100, "delta_usd": (total - result["actual_net_usd"] * 100) / 100,
                "delta_cents_per_order": mean_delta, "interval": interval,
                "positive_scenario_screen": bool(screen),
            }
        result["verdict"] = "HOLD(model): alternative queue and later fills unidentified"
        out["groups"][name] = result
    return out


def slots_report(candidates: list[dict]) -> dict:
    out = {"probe": "SLOT-PRIORITY", "scope": "capacity census, not hypothetical live P&L", "groups": {}}
    for tag in sorted({c["tag"] for c in candidates}):
        rows = [c for c in candidates if c["tag"] == tag]
        slow = [c for c in rows if market_type(c["ticker"]) in SLOW]
        missed = [c for c in slow if c["open_cap"] and not c["placed"]]
        delays = [c for c in slow if c["open_cap"] and c["placed"]]
        days = len({date_of(c["first_at"]) for c in rows})
        ratio = len(missed) / len(slow) if slow else 0
        counts = Counter()
        for row in rows:
            counts.update(row["outcomes"])
        paper = [c["settle"] - c["cap_price"] - .02 for c in missed
                 if c["settle"] is not None and c["cap_price"] is not None]
        if days < 7 or len(rows) < 200:
            verdict = "HOLD(accrual): fewer than 7 days or 200 distinct candidates"
        elif len(missed) < 20 or ratio < .05:
            verdict = "UNSUPPORTED(capacity): insufficient missed priority-cell open-cap supply"
        else:
            verdict = "HOLD(policy): capacity exists, cell validation and occupancy/fill replay required"
        out["groups"][tag] = {
            "distinct_candidates": len(rows), "creation_dates": days,
            "parity_rows": sum(c["repeated_rows"] for c in rows),
            "priority_candidates": len(slow), "priority_placed": sum(c["placed"] for c in slow),
            "priority_open_cap_never_placed": len(missed),
            "priority_open_cap_later_placed": len(delays), "missed_priority_share": ratio,
            "all_open_cap_candidates": sum(c["open_cap"] for c in rows),
            "priority_contest_cap": sum(c["contest_cap"] for c in slow),
            "priority_tier": sum(c["tier"] for c in slow),
            "priority_paused": sum(c["paused"] for c in slow),
            "priority_paper_cap": sum(c["paper_cap"] for c in slow),
            "distinct_market_outcome_counts": dict(counts),
            "missed_paper_scenario_count": len(paper),
            "missed_paper_scenario_usd": sum(paper) / 100,
            "verdict": verdict,
        }
    return out


def withdrawal_delta(o: dict, signal_at: float | None) -> float | None:
    """An observed signal cannot cancel a fill at or before the 2-second latency."""
    actual = actual_net(o)
    if actual is None:
        return None
    if signal_at is None or not o["filled"]:
        return 0.0
    if signal_at < o["start"] or signal_at >= o["end"]:
        raise ValueError("withdrawal signal outside observed unfilled window")
    if o["first_fill"] is None:
        return None
    return -actual if o["first_fill"] >= signal_at + 2 else 0.0


def withdrawal_report(orders: list[dict], traces: list[dict]) -> dict:
    keyed = {(t["tag"], t["ticker"], t["created"]): t for t in traces}
    out = {"probe": "WITHDRAWAL", "scope": "cancel-only replay, no credit for replacement fills", "groups": {}}
    for name, population in group_orders(orders):
        normal = [o for o in population if normal_order(o)]
        cohort = [o for o in normal if actual_net(o) is not None]
        covered = [o for o in cohort if keyed.get((o["tag"], o["ticker"], o["created"]), {}).get("paired_ticks", 0) > 0]
        coverage = len(covered) / len(cohort) if cohort else 0
        result = {"settled_terminal_orders": len(cohort), "paired_window_orders": len(covered),
                  "paired_window_coverage": coverage,
                  "raw_trade_coverage_independently_verified": False,
                  "actual_net_usd": sum(actual_net(o) for o in cohort) / 100,
                  "signal_variants": {}}
        result["covered_filled_orders"] = sum(o["filled"] > 0 for o in covered)
        result["covered_losing_fills"] = sum(o["filled"] > 0 and o["settle"] == 0 for o in covered)
        result["all_losing_fills"] = sum(o["filled"] > 0 and o["settle"] == 0 for o in cohort)
        for label, field in (("verified_withdrawal", "signal_at"), ("coarse_price_depth_only", "first_coarse")):
            deltas, avoid, triggered = [], [], []
            total_actual = 0.0
            for o in covered:
                t = keyed[(o["tag"], o["ticker"], o["created"])]
                signal = t[field]
                delta = withdrawal_delta(o, signal)
                if delta is None:
                    continue
                total_actual += actual_net(o)
                deltas.append((date_of(o["settled_at"]), delta))
                if signal is not None:
                    triggered.append(o)
                    if o["filled"] and o["first_fill"] is not None and o["first_fill"] >= signal + 2:
                        avoid.append(o)
            delta = sum(v for _, v in deltas)
            avg_avoided = sum(actual_net(o) for o in avoid) / len(avoid) if avoid else None
            interval = bootstrap(deltas)
            fills = sum(o["filled"] > 0 for o in covered)
            retention = 1 - len(avoid) / fills if fills else None
            if label == "coarse_price_depth_only":
                verdict = "DESCRIPTIVE ONLY: does not identify withdrawal without prints"
            elif coverage < .8:
                verdict = "HOLD(instrument): paired-window order coverage below 80%"
            elif not result["raw_trade_coverage_independently_verified"]:
                verdict = "HOLD(instrument): absence of prints cannot certify complete trade coverage"
            elif len(avoid) < 40 or interval["dates"] < 10:
                verdict = "HOLD(accrual): fewer than 40 avoidable fills or 10 dates"
            elif avg_avoided >= 0:
                verdict = "UNSUPPORTED(policy): cancellation discards nonnegative fills"
            elif delta / len(deltas) >= .5 and interval["p05"] > 0 and retention >= .4:
                verdict = "SUPPORTS FORWARD RESEARCH ONLY"
            else:
                verdict = "HOLD(economics): positive screen not met"
            result["signal_variants"][label] = {
                "triggered_orders": len(triggered), "avoidable_fills": len(avoid),
                "avoided_winners": sum(o["settle"] == 100 for o in avoid),
                "avoided_losers": sum(o["settle"] == 0 for o in avoid),
                "avoided_mean_cents": avg_avoided, "delta_usd": delta / 100,
                "policy_net_usd": (total_actual + delta) / 100,
                "covered_actual_net_usd": total_actual / 100,
                "actual_fill_retention": retention, "interval": interval, "verdict": verdict,
                "observed_economic_screen": ("UNSUPPORTED: cancellation discards nonnegative fills"
                                              if avg_avoided is not None and avg_avoided >= 0
                                              else "NOT MET"),
                "triggered_filled_orders": sum(o["filled"] > 0 for o in triggered),
                "triggered_losing_fills": sum(o["filled"] > 0 and o["settle"] == 0 for o in triggered),
                "untriggered_filled_orders": sum(o["filled"] > 0 for o in covered) - sum(o["filled"] > 0 for o in triggered),
                "untriggered_losing_fills": sum(o["filled"] > 0 and o["settle"] == 0 for o in covered) - sum(o["filled"] > 0 and o["settle"] == 0 for o in triggered),
            }
        result["coarse_windows"] = sum(keyed[(o["tag"], o["ticker"], o["created"])]["coarse_windows"] for o in covered)
        result["clean_raw_windows"] = sum(keyed[(o["tag"], o["ticker"], o["created"])]["clean_windows"] for o in covered)
        out["groups"][name] = result
    return out


def run_probe(kind: str, argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only MMSELL " + kind + " diagnostic")
    ap.add_argument("--input", help="sanitized JSON rows or complete existing ops export text")
    ap.add_argument("--orders", help="order export required by withdrawal")
    ap.add_argument("--export-request", help="print a read-only ops request with this unique id")
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--until", default=UNTIL)
    args = ap.parse_args(argv)
    if args.export_request:
        print(json.dumps({"type": "db", "sql": export_sql(kind, args.since, args.until),
                          "max_rows": 10000, "id": args.export_request}))
        return 0
    if not args.input:
        ap.error("--input or --export-request is required")
    if kind == "orders":
        result = inverse_report(read_rows(args.input, ORDER_FIELDS))
    elif kind == "slots":
        result = slots_report(read_rows(args.input, SLOT_FIELDS))
    else:
        if not args.orders:
            ap.error("withdrawal requires --orders")
        result = withdrawal_report(read_rows(args.orders, ORDER_FIELDS),
                                   read_rows(args.input, WITHDRAWAL_FIELDS))
    result["contract"] = "docs/MMSELL_CHATGPT_PROBES_20261004.md"
    result["window"] = {"since": args.since, "until": args.until}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0
