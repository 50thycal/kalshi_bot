"""Recompute the bounded coverage census from retained, outcome-masked evidence.

Offline only: no credentials, requests, trades, signals, fees or P&L. Use Python
3.10+. The main report distinguishes candle-period alignment from exchange
quote-update time, and raw event tickers from independent issuer-calls.
"""

import collections
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FORBIDDEN = {"result", "expiration_value", "last_price", "last_price_dollars"}


def read(name):
    return json.loads((ROOT / name).read_text())


def main():
    with gzip.open(ROOT / "markets.jsonl.gz", "rt") as stream:
        markets = [json.loads(line) for line in stream]
    assert len({m["ticker"] for m in markets}) == len(markets)
    assert all(not FORBIDDEN.intersection(m) for m in markets)
    requests = read("requests.json")
    settled = [m for m in markets if m["status"] in ("settled", "finalized")]
    tiers = {
        tier: {m["ticker"] for m in markets if tier in m["source_tiers"]}
        for tier in ("historical", "live")
    }
    assert sum(r["row_count"] for r in requests) == sum(map(len, tiers.values()))
    quotes = []
    for event in read("quote-audit.json"):
        decision = event["decision_ts"]
        call = dt.datetime.fromisoformat(event["scheduled_start"])
        assert decision == int(call.timestamp()) - 3600
        aligned = 0
        for sample in event["samples"]:
            valid = []
            for candle in sample["candlesticks"]:
                age = decision - candle["end_period_ts"]
                assert age >= 0, "Future candle in decision-time audit"
                bid = candle["yes_bid"]
                ask = candle["yes_ask"]
                bid = bid.get("close_dollars", bid.get("close"))
                ask = ask.get("close_dollars", ask.get("close"))
                # Both observed API schemas encode these fields in dollars.
                if bid is not None and ask is not None:
                    assert 0 <= float(bid) <= 1 and 0 <= float(ask) <= 1
                    if age <= 60 and 0 <= float(bid) <= float(ask) < 1:
                        valid.append({"age_seconds": age, "bid": bid, "ask": ask})
                # Trade volume and open interest are deliberately not quote size.
                assert not any("size" in k or "depth" in k for k in candle)
            assert valid == sample["eligible_quote_rows"]
            aligned += bool(valid)
        quotes.append({
            "event": event["event"],
            "listed_contracts": event["listed_contracts"],
            "sampled_contracts": len(event["samples"]),
            "aligned_bid_ask_candle_closes": aligned,
            "contracts_with_observed_resting_size": 0,
        })
    archives = read("msft-archive-audit.json")
    for archive in archives:
        assert archive["capture_timestamp"] < "20260729203000"
        assert archive["qa_marker"] and archive["fiscal_title"]
        assert archive["head_status"] == 200
        assert archive["capture_timestamp"] in archive["final_url"]
        assert "error" not in archive
    receipt_times = [r["fetched_at"] for q in requests for r in q["receipts"]]
    summary = {
        "retrieval_start": min(receipt_times),
        "retrieval_end": max(receipt_times),
        "series_discovered": len(read("series.json")),
        "market_page_requests": len(receipt_times),
        "request_errors": sum(bool(q["error"]) for q in requests),
        "truncated_queries": sum(q["truncated"] for q in requests),
        "returned_rows_before_dedup": sum(q["row_count"] for q in requests),
        "unique_markets": len(markets),
        "historical_tier_markets": len(tiers["historical"]),
        "live_tier_markets": len(tiers["live"]),
        "tier_overlap": len(tiers["historical"] & tiers["live"]),
        "unique_settled_markets": len(settled),
        "raw_settled_event_tickers": len({m["event_ticker"] for m in settled}),
        "raw_settled_series": len({m["series"] for m in settled}),
        "settlement_timestamp_range": [min(m["settlement_ts"] for m in settled),
                                       max(m["settlement_ts"] for m in settled)],
        "settled_contracts_by_settlement_month_not_call_month": dict(sorted(
            collections.Counter(m["settlement_ts"][:7] for m in settled).items()
        )),
        "quote_fixtures": quotes,
        "prior_msft_source_versions_retrieved": len(archives),
        "strict_C0_fully_qualified_calls_demonstrated": 0,
        "verdict": "HOLD: resting size unavailable; full independent-call join incomplete",
    }
    print(json.dumps(summary, indent=2))
    manifest = read("sha256.json")
    for name, expected in manifest.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
