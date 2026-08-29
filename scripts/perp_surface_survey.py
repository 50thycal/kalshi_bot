"""PERP-V1 Probe 0 — does Kalshi's perpetual-futures API surface actually exist,
and how much of it is readable without credentials?

Pre-registration: `docs/PERP_V1_THESIS.md` §6. Experiment: `perp-v1` (PROBE).

WHY THIS RUNS BEFORE ANY STRATEGY WORK
--------------------------------------
Everything PERP-V1 proposes rests on an API surface **nobody in this project has
read**. The development sandbox cannot reach Kalshi at all (outbound HTTPS to the
API and to `docs.kalshi.com` is blocked by the egress proxy), so every endpoint
path and field name in the thesis comes from an operator brief and is a claim,
not an observation.

There is direct precedent for that claim being wrong. `docs/RESEARCH_JOURNAL.md`,
PERPS SURVEY 2026-07-09: the perpetual product existed, and the API surface
assumed for it did not — 76 open Crypto events scanned, every candidate series
ticker resolving to zero markets. The recorded next step was to find the
perp-specific endpoint rather than guess series names against the event API. This
script is that step.

WHAT IT REPORTS, AND WHY IT REPORTS STATUS CODES VERBATIM
---------------------------------------------------------
For discovery the HTTP status IS the finding, and the three answers are
materially different decisions:

    404 / 400   the path does not exist -> the thesis names the wrong surface
    401 / 403   the path EXISTS and needs credentials -> the surface is real, but
                the ops channel cannot read it (this runner holds no Kalshi key),
                so the collector must run on the worker, which does
    200         readable unauthenticated -> the collector can be built and
                validated straight from here

A helper that collapses all of those to `None` — as the repository's shared
`_get` does, correctly, for its own purposes — would turn the one question this
probe exists to answer into a blank. So this script does its own fetching.

It also prints the FIELD NAMES actually present on any 200 response rather than
the ones the thesis assumes. A collector written against assumed field names is
the same mistake as a probe written against guessed series tickers.

READ-ONLY, stdlib only, no credentials, no orders, no database. Usage:
    {"type":"script","name":"perp_surface_survey"}
    {"type":"script","name":"perp_surface_survey","args":["--asset","BTC"]}
"""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request

BASE = "https://api.elections.kalshi.com/trade-api/v2"
_UA = "kalshi-bot-perp-surface-survey/1 (research; read-only)"

#: Candidate perp paths, ordered by how strongly the operator brief implies them.
#: Every one is a GUESS until this script prints its status — that is the point.
#: `{asset}` is substituted per asset; a path without it is probed once.
CANDIDATE_PATHS: tuple[tuple[str, str], ...] = (
    ("/margin/markets", "perp market list (brief: the /margin surface)"),
    ("/margin/markets?limit=5", "perp market list, paged"),
    ("/margin/positions", "perp positions (expected to need auth)"),
    ("/margin/balance", "perp balance (expected to need auth)"),
    ("/margin/fills", "perp fills (expected to need auth)"),
    ("/margin/funding_rates", "historical funding rates"),
    ("/margin/funding_rate_estimate", "live funding estimate for the open window"),
    ("/margin/fee_tiers", "fee tiers"),
    ("/perpetuals/markets", "alternative spelling"),
    ("/perps/markets", "alternative spelling"),
    ("/index_prices", "CF Benchmarks reference feed"),
    ("/reference_prices", "CF Benchmarks reference feed, alternative spelling"),
)

#: Probed per asset once a market-list path resolves, to learn the per-market
#: shapes the collector will need (book, candles, trades, funding).
PER_MARKET_PATHS: tuple[tuple[str, str], ...] = (
    ("/margin/markets/{ticker}", "market detail (mark price, OI, leverage)"),
    ("/margin/markets/{ticker}/orderbook", "order book"),
    ("/margin/markets/{ticker}/candlesticks", "candles"),
    ("/margin/markets/{ticker}/trades", "public trade tape"),
    ("/margin/markets/{ticker}/funding_rates", "per-market funding history"),
)

ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")


def fetch(path: str, timeout: float = 30.0) -> tuple[int, object | None, str]:
    """GET `BASE + path`. Returns (status, parsed_json_or_None, note).

    Status 0 means the request never got an HTTP answer (DNS, TLS, timeout) —
    which is NOT the same finding as a 404 and must not be reported as one.
    Retries only the transient classes, and only twice: this is discovery, and a
    long retry ladder over a path that does not exist is wasted runner time.
    """
    req = urllib.request.Request(
        BASE + path, headers={"User-Agent": _UA, "Accept": "application/json"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, json.loads(raw), ""
                except json.JSONDecodeError:
                    return resp.status, None, f"non-JSON body ({len(raw)} bytes)"
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2**attempt)
                continue
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:180].replace("\n", " ")
            except Exception:  # noqa: BLE001 — the body is a nicety, the code is the finding
                pass
            return exc.code, None, body
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as exc:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            return 0, None, f"{type(exc).__name__}: {exc}"
    return 0, None, "exhausted retries"


def _classify(status: int) -> str:
    if status == 200:
        return "READABLE"
    if status in (401, 403):
        return "EXISTS/AUTH"
    if status in (404, 400):
        return "ABSENT"
    if status == 0:
        return "NO-ANSWER"
    return f"HTTP-{status}"


def _shape(payload: object, depth: int = 0) -> str:
    """Field names actually present, one level into the first list element.

    Deliberately names rather than values: the collector needs to know that
    `mark_price` exists, and printing a price into a public result file is noise
    at best. Depth is capped because a survey result an operator will not read is
    the same as no survey.
    """
    if isinstance(payload, dict):
        keys = sorted(payload)
        out = "{" + ", ".join(keys[:20]) + ("…" if len(keys) > 20 else "") + "}"
        if depth == 0:
            for k in keys:
                v = payload[k]
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    out += f"\n        {k}[0] = {_shape(v[0], depth + 1)}"
                    break
        return out
    if isinstance(payload, list):
        return f"[{len(payload)} items]" + (
            f" first = {_shape(payload[0], depth + 1)}" if payload else ""
        )
    return type(payload).__name__


def _first_ticker(payload: object) -> str | None:
    """The first perp ticker in a market-list response, whatever the list is called."""
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        for key in ("ticker", "market_ticker", "symbol", "id"):
                            if isinstance(item.get(key), str):
                                return item[key]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--asset", default=None,
                    help="probe one asset only (default: the launch universe)")
    args = ap.parse_args(argv)
    assets = (args.asset.upper(),) if args.asset else ASSETS

    print("PERP-V1 Probe 0 — Kalshi perpetual API surface survey")
    print(f"  base: {BASE}")
    print("  read-only, unauthenticated. This runner holds no Kalshi credentials, so")
    print("  EXISTS/AUTH is a SUCCESSFUL discovery, not a failure: it proves the path")
    print("  is real and tells us the collector must run where the key lives.\n")

    print("--- candidate surface ---")
    resolved: dict[str, object] = {}
    for path, why in CANDIDATE_PATHS:
        status, payload, note = fetch(path)
        verdict = _classify(status)
        print(f"  {verdict:<12} {path}")
        print(f"               {why}")
        if note:
            print(f"               note: {note[:180]}")
        if status == 200 and payload is not None:
            resolved[path] = payload
            print(f"               fields: {_shape(payload)}")

    ticker = None
    for path, payload in resolved.items():
        if "markets" in path:
            ticker = _first_ticker(payload)
            if ticker:
                print(f"\n  resolved a live perp ticker from {path}: {ticker}")
                break

    if ticker:
        print("\n--- per-market surface ---")
        for tmpl, why in PER_MARKET_PATHS:
            path = tmpl.format(ticker=ticker)
            status, payload, note = fetch(path)
            print(f"  {_classify(status):<12} {path}  ({why})")
            if status == 200 and payload is not None:
                print(f"               fields: {_shape(payload)}")
            elif note:
                print(f"               note: {note[:180]}")
    else:
        print("\n--- per-market surface ---")
        print("  skipped: no market-list path returned a readable ticker.")

    print("\n--- assets named in the brief ---")
    print(f"  {', '.join(assets)}")
    print("  (not probed individually until a market-list path resolves — probing")
    print("   per-asset paths off an unresolved surface is the 2026-07-09 mistake)")

    print("\n=== VERDICT ===")
    if resolved:
        print("  A readable perp surface EXISTS. Record the field names above against the")
        print("  thesis's assumed names before writing the collector: a collector built on")
        print("  assumed fields is the same error as a probe built on guessed tickers.")
        print("  Next: PERP-V1 Probe 1 (read-only tape collector), then Probe 2 (scorers).")
    else:
        print("  NO perp path was readable from this runner. This is NOT yet a kill: an")
        print("  EXISTS/AUTH line above means the surface is real and needs the worker's")
        print("  Kalshi credentials, which the ops channel deliberately does not hold.")
        print("  Read the classifications: if every line is ABSENT, the thesis names the")
        print("  wrong surface and PERP-V1 stops at PROBE with BLOCKED_DATA — the same")
        print("  outcome as 2026-07-09, reached again for the cost of one ops request.")
    print("\n  Survey only. NOTHING is promoted by this script, and no gate result is")
    print("  recorded by it: a gate verdict is a recorded evaluator act, never a print.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
