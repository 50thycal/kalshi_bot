"""SPOT-PERP-CARRY v1: funding-data census and total-capital hurdle, not a backtest.

Read-only public GETs; no credentials, orders, DB, environment writes or XOS actions.
Frozen specification: docs/SPOT_PERP_CARRY_CENSUS.md. Unknown rate units are never
guessed. Funding observations are printed raw; capital sensitivities are hypothetical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://external-api.kalshi.com/trade-api/v2/margin"
START = datetime(2026, 8, 14, tzinfo=timezone.utc)
END = datetime(2026, 9, 13, tzinfo=timezone.utc)
TICKERS = ("KXBTCPERP", "KXETHPERP")
MAX_BYTES = 2_000_000


def reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def fetch(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "kalshi-funding-census/1 read-only"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return {"status": response.status, "error": "response_too_large"}
            return {"status": response.status, "payload": json.loads(raw, parse_constant=reject_constant),
                    "response_sha256": hashlib.sha256(raw).hexdigest()}
    except urllib.error.HTTPError as exc:
        # Don't print account/proxy response bodies or retry access denials.
        return {"status": exc.code, "error": "http_error"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"status": 0, "error": type(exc).__name__}


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(timezone.utc)


def validate(payload, ticker, start, end):
    if not isinstance(payload, dict) or not isinstance(payload.get("funding_rates"), list):
        return [], ["missing_funding_rates_list"]
    rows, errors = [], []
    if payload.get("cursor") or payload.get("next_cursor"):
        errors.append("unexpected_pagination")
    for row in payload["funding_rates"]:
        try:
            if row["market_ticker"] != ticker:
                raise ValueError("ticker_mismatch")
            dt = parse_time(row["funding_time"])
            if not start <= dt < end:
                # Half-open ownership prevents duplicated payments at chunk boundaries.
                if dt == end:
                    continue
                raise ValueError("out_of_window")
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if (isinstance(row["funding_rate"], bool) or isinstance(row["mark_price"], bool)
                    or not math.isfinite(rate)
                    or not math.isfinite(mark) or mark <= 0):
                raise ValueError("invalid_rate_or_mark")
            rows.append({"ticker": ticker, "at": dt.isoformat(),
                         "rate_raw": rate, "mark_price": mark})
        except (KeyError, TypeError, ValueError, AttributeError):
            errors.append("invalid_funding_row")
    return rows, errors


def collect(ticker, get=fetch):
    rows, requests, errors = [], [], []
    left = START
    while left < END:
        right = min(left + timedelta(days=7), END)
        query = urllib.parse.urlencode({"ticker": ticker, "start_ts": int(left.timestamp()),
                                        "end_ts": int(right.timestamp())})
        url = BASE + "/funding_rates/historical?" + query
        response = get(url)
        requests.append({"url": url, **{k: v for k, v in response.items() if k != "payload"}})
        if response.get("status") != 200 or "error" in response:
            errors.append("history_request_failed")
            break
        batch, issues = validate(response.get("payload"), ticker, left, right)
        rows.extend(batch)
        errors.extend(issues)
        left = right
    rows.sort(key=lambda r: (r["at"], r["rate_raw"], r["mark_price"]))
    if len({r["at"] for r in rows}) != len(rows):
        errors.append("duplicate_payment_times")
    times = [parse_time(r["at"]) for r in rows]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    # Estimate is present-time context only: never retroactively selects historical positions.
    estimate = get(BASE + "/funding_rates/estimate?" + urllib.parse.urlencode({"ticker": ticker}))
    return {"ticker": ticker, "rows": rows, "errors": sorted(set(errors)),
            "requests": requests, "estimate_context_only": estimate,
            "count": len(rows), "utc_days": len({r["at"][:10] for r in rows}),
            "median_gap_seconds": statistics.median(gaps) if gaps else None,
            "max_gap_seconds": max(gaps) if gaps else None,
            "raw_negative_count": sum(r["rate_raw"] < 0 for r in rows),
            "raw_zero_count": sum(r["rate_raw"] == 0 for r in rows),
            "raw_positive_count": sum(r["rate_raw"] > 0 for r in rows),
            "dataset_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}


def capital_scenarios():
    out = []
    for capital in (1000, 2000, 4000):
        notional = capital * 0.4  # combined BTC+ETH, NOT this amount per asset
        # Fee scenarios are assumed bps of each leg's notional, not account quotes.
        costs = []
        for spot_fee, perp_fee in ((10, 2), (40, 12), (60, 12)):
            fees = notional * 2 * (spot_fee + perp_fee) / 10000
            stress = notional * 50 / 10000  # 50 bps combined basis/slippage stress
            costs.append({"spot_fee_bps_per_leg": spot_fee, "perp_fee_bps_per_leg": perp_fee,
                          "four_leg_fees_usd_flat_prices": fees,
                          "basis_slippage_stress_usd": stress,
                          "funding_needed_for_100_usd_month": 100 + fees + stress,
                          "funding_needed_pct_of_notional": 100 * (100 + fees + stress) / notional})
        out.append({"total_capital_usd": capital, "primary": capital == 2000,
                    "spot_usd": notional, "perp_collateral_usd": notional,
                    "cash_reserve_usd": capital * 0.2,
                    "per_asset_spot_usd": notional / 2,
                    "per_asset_perp_collateral_usd": notional / 2,
                    "minimum_net_annual_return_for_100_monthly_pct": 1200 / capital * 100,
                    "hypothetical_cost_scenarios": costs})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital-only", action="store_true", help="offline arithmetic; no GETs")
    args = ap.parse_args(argv)
    assets = [] if args.capital_only else [collect(t) for t in TICKERS]
    data_floor = len(assets) == 2 and all(
        not a["errors"] and a["count"] >= 30 and a["utc_days"] >= 14 for a in assets)
    print(json.dumps({"probe": "SPOT-PERP-CARRY-census-v1", "as_of": datetime.now(timezone.utc).isoformat(),
                      "window": [START.isoformat(), END.isoformat()], "assets": assets,
                      "capital_scenarios": capital_scenarios(), "data_floor_met": data_floor,
                      "funding_rate_units": "UNVERIFIED", "funding_sign_convention": "UNVERIFIED",
                      "realized_net_pnl_usd": None, "verdict": "HOLD",
                      "next_action": "Verify units/sign, complete payment schedule, executable spot/perp prices and account fees before a capital backtest."
                      if data_floor else "Historical funding is untested offline or incomplete/unavailable; no return claim."},
                     indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
