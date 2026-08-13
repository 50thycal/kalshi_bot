"""mmsell QUOTE PARITY — may the entry scan trust the event page's inline quote?

THE QUESTION
------------
The mmsell scan makes ~650-1,600 `GET /markets/{t}/orderbook` calls per cycle (measured
2026-08-10/11), at a burst rate of ~6-25 req/sec against a Kalshi read bucket that allows 20/sec
on the Basic tier. That cost is the ONLY reason the scan is capped at the top 150 events by
volume, and therefore the reason ~1,700 eligible events go unscanned every cycle.

But `GET /events?with_nested_markets=true` — the one call the scan already makes to enumerate
the universe — returns a top-of-book quote on every nested market, for free. If that quote
agrees with the orderbook, the scan can pre-filter on it and fetch orderbooks only for markets
actually in band (~220-450/cycle): a 3-7x reduction that would make a WIDER scan cheaper than
today's narrow one.

WHY IT CANNOT JUST BE ASSUMED
-----------------------------
A pre-filter that wrongly rejects a market never fetches its orderbook, so the candidate simply
stops existing — silently, for every book including the live one. The events endpoint may serve
a cached quote, and our `yes_ask` is DERIVED (`100 - no_bid`) where Kalshi's is reported. So the
worker scores the inline quote against the orderbook it already fetched, every cycle, at zero
API cost; this reads that back.

WHAT TO LOOK AT, IN ORDER
-------------------------
1. `miss` in the DECISION TABLE. This is the whole result: in-band markets a pre-filter would
   have thrown away. It must reach 0 at some margin, and `no-quote` misses must be ~0 — those
   are unrecoverable at ANY margin.
2. `fetch` at that same margin, against `ob_in` and against today's `compared`. The saving is
   `compared -> fetch`; if the safe margin is so wide that `fetch` approaches `compared`, the
   pre-filter buys nothing and the idea is dead.
3. The TIGHT band before the wide one. Tight mirrors `mmsell10`, the live book — the one a bad
   pre-filter would actually cost money on.
4. AGREEMENT histograms + `inline_missing`, for diagnosing WHY when the table looks bad.

Also prints RATE LIMITS: retryable Kalshi responses by HTTP status, which the worker now
snapshots into the same telemetry. A non-zero 429 count means the current scan is already over
our API tier and the pre-filter is not optional.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_quote_parity.py
    # or:  {"type": "script", "name": "mmsell_quote_parity"}
    #      {"type": "script", "name": "mmsell_quote_parity", "args": ["--hours", "72"]}
"""

from __future__ import annotations

import argparse
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

BUCKETS = ("0", "1", "2", "3-5", "6-10", ">10")

# Pre-registered gate (docs/MMSELL_QUOTE_PARITY.md). Stated here so the verdict cannot be
# re-scoped after seeing the numbers.
MIN_COMPARED = 50_000       # markets scored
MIN_CYCLES = 100            # spread across enough cycles to cross regimes, not one quiet hour
MAX_SAFE_MARGIN = 3.0       # a margin wider than this is not a pre-filter, it is the band
MAX_NO_QUOTE_RATE = 0.001   # unrecoverable misses, as a share of in-band truths


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_rows(cur, hours: float) -> list[dict]:
    cur.execute(
        "SELECT created_at, raw_json FROM system_events"
        " WHERE component = 'mmsell_quote_parity' AND created_at > now() - (%s || ' hours')::interval"
        " ORDER BY created_at",
        (str(hours),),
    )
    return [{"at": at, **(raw or {})} for at, raw in cur.fetchall()]


def load_transient(cur, hours: float) -> list[dict]:
    """Cumulative-since-process-start transient counters off the scan-funnel rows. Cumulative
    means a RESTART resets them, so the newest row is not simply the total — the caller sums
    per-segment growth instead of differencing endpoints."""
    cur.execute(
        "SELECT created_at, raw_json->'transient' FROM system_events"
        " WHERE component = 'mmsell_scan' AND created_at > now() - (%s || ' hours')::interval"
        " ORDER BY created_at",
        (str(hours),),
    )
    return [{"at": at, **(raw or {})} for at, raw in cur.fetchall() if raw]


def load_limits(cur) -> dict | None:
    """The account's Kalshi API tier, recorded once per worker start by main._probe_api_limits.
    Newest wins. Without it a 429 count is a numerator with no denominator — 2 rate limits means
    something very different against a 20/sec ceiling than against a 30/sec one."""
    cur.execute(
        "SELECT raw_json FROM system_events WHERE component = 'kalshi_limits'"
        " ORDER BY created_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    return (row[0] or None) if row else None


def _merge(rows: list[dict]) -> dict:
    """Sum the per-cycle summaries into one. Counts add; `max_*` take the max; the per-band
    decision tables add elementwise and `worst_margin` takes the max (the smallest safe margin
    over the whole population is the worst single case in it)."""
    out: dict = {"compared": 0, "inline_missing": 0, "ob_not_two_sided": 0,
                 "max_mid_delta": 0.0, "max_ask_delta": 0.0,
                 "bid_hist": {}, "ask_hist": {}, "mid_hist": {}, "bands": {}, "margins": []}
    for r in rows:
        for k in ("compared", "inline_missing", "ob_not_two_sided"):
            out[k] += int(r.get(k) or 0)
        for k in ("max_mid_delta", "max_ask_delta"):
            out[k] = max(out[k], float(r.get(k) or 0.0))
        for h in ("bid_hist", "ask_hist", "mid_hist"):
            for bucket, n in (r.get(h) or {}).items():
                out[h][bucket] = out[h].get(bucket, 0) + int(n or 0)
        if r.get("margins"):
            out["margins"] = list(r["margins"])
        for name, b in (r.get("bands") or {}).items():
            acc = out["bands"].setdefault(
                name, {"ob_in": 0, "miss_no_quote": 0, "worst_margin": 0.0,
                       "fetch_at": {}, "miss_at": {}})
            acc["ob_in"] += int(b.get("ob_in") or 0)
            acc["miss_no_quote"] += int(b.get("miss_no_quote") or 0)
            acc["worst_margin"] = max(acc["worst_margin"], float(b.get("worst_margin") or 0.0))
            for key in ("fetch_at", "miss_at"):
                for m, n in (b.get(key) or {}).items():
                    acc[key][m] = acc[key].get(m, 0) + int(n or 0)
    return out


def _hist_line(label: str, hist: dict, total: int) -> str:
    cells = []
    for b in BUCKETS:
        n = int(hist.get(b) or 0)
        cells.append(f"{b}:{n:>7,d} ({(100.0 * n / total if total else 0.0):4.1f}%)")
    return f"  {label:<10s} " + "  ".join(cells)


def _transient_totals(rows: list[dict]) -> dict[str, int]:
    """Growth of each cumulative counter, summed across restart segments. A drop in a counter
    means the worker restarted, so that row starts a new segment rather than being differenced
    against the previous one (which would subtract to a bogus negative)."""
    totals: dict[str, int] = {}
    prev: dict[str, int] = {}
    for r in rows:
        cur = {k: int(v or 0) for k, v in r.items() if k != "at"}
        for k, v in cur.items():
            before = prev.get(k, 0)
            totals[k] = totals.get(k, 0) + (v - before if v >= before else v)
        prev = cur
    return totals


def _merge_ex_inplay(rows: list[dict]) -> tuple[int, dict]:
    """Sum the in-play-EXCLUDED decision tables. Same arithmetic as _merge's band section, over
    the shadow tables the worker keeps beside the blended ones."""
    compared = 0
    bands: dict = {}
    for r in rows:
        compared += int(r.get("compared_ex_inplay") or 0)
        for name, b in (r.get("bands_ex_inplay") or {}).items():
            acc = bands.setdefault(name, {"ob_in": 0, "miss_no_quote": 0, "worst_margin": 0.0,
                                          "fetch_at": {}, "miss_at": {}})
            acc["ob_in"] += int(b.get("ob_in") or 0)
            acc["miss_no_quote"] += int(b.get("miss_no_quote") or 0)
            acc["worst_margin"] = max(acc["worst_margin"], float(b.get("worst_margin") or 0.0))
            for key in ("fetch_at", "miss_at"):
                for m, n in (b.get(key) or {}).items():
                    acc[key][m] = acc[key].get(m, 0) + int(n or 0)
    return compared, bands


def _print_ex_inplay(rows: list[dict], agg: dict, margins: list[str]) -> None:
    """Does carving IN-PLAY markets out of the pre-filter actually help?

    The proposed rule is: trust the inline quote for scheduled/discrete markets, always fetch
    for in_play ones. It is decidable from the series alone, so it costs nothing to apply — the
    only question is whether it works. This is the shadow test: the same decision table,
    restricted to markets that are NOT in_play, scored on live data with no fetch skipped.

    Read the MISS column against the blended one printed above. If the carve-out is the right
    mechanism the miss rate should collapse; if it barely moves, settle mode is the wrong proxy
    for "this quote is stale" and the rule buys nothing."""
    compared, bands = _merge_ex_inplay(rows)
    print("\n=== THE IN-PLAY CARVE-OUT — same table, in_play markets excluded ===")
    if not compared:
        print("  no data yet — the worker records this from the 2026-08-13 build onward."
              "\n  (An UNCLASSIFIED series is scored into the blended table only, so this"
              "\n  population is markets we positively know are scheduled or discrete.)")
        return
    blended = agg.get("compared") or 1
    print(f"  {compared:,d} of {blended:,d} scored markets are known non-in-play"
          f" ({100.0 * compared / blended:.1f}%)")
    for name, b in sorted(bands.items()):
        ob_in = b["ob_in"]
        base = (agg.get("bands") or {}).get(name, {})
        print(f"\n  --- band {name!r}: {ob_in:,d} in-band non-in-play markets ---")
        print(f"    {'margin':>7} {'fetch':>10} {'miss':>8} {'miss%':>7}   {'blended miss%':>14}")
        for m in margins:
            f = int(b["fetch_at"].get(m) or 0)
            miss = int(b["miss_at"].get(m) or 0)
            b_in = int(base.get("ob_in") or 0)
            b_miss = int((base.get("miss_at") or {}).get(m) or 0)
            print(f"    {m:>6}c {f:>10,d} {miss:>8,d}"
                  f" {(100.0 * miss / ob_in if ob_in else 0.0):>6.2f}%"
                  f"   {(100.0 * b_miss / b_in if b_in else 0.0):>13.2f}%")
        print(f"    smallest margin that misses nothing recoverable: {b['worst_margin']:.1f}c"
              f"   (blended {float(base.get('worst_margin') or 0.0):.1f}c)")


def _vol_bucket(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "?"
    for hi, label in ((100, "<100"), (500, "100-500"), (2_000, "500-2k"), (10_000, "2k-10k")):
        if v < hi:
            return label
    return ">=10k"


def _htc_bucket(h) -> str:
    try:
        h = float(h)
    except (TypeError, ValueError):
        return "?"
    for hi, label in ((1, "<1h"), (6, "1-6h"), (24, "6-24h"), (72, "1-3d")):
        if h < hi:
            return label
    return ">3d"


def _print_outliers(rows: list[dict], compared: int) -> None:
    """Do the large disagreements form an EXCLUDABLE CLASS?

    The pre-filter's decision hinges on this. Its miss rate has an irreducible floor around 1%
    that no margin removes, and that floor is made of quotes off by more than a few cents — one
    was 90c. If those cluster in something the scan can recognise WITHOUT an orderbook (a thin
    book, a particular series, a market near expiry), the pre-filter can decline to trust the
    inline quote there and fetch anyway, and the loss largely disappears. If they are spread
    uniformly, the ~1% is the honest price of the idea and the decision stays a judgement call.

    Read the RATE column, not the count: a bucket holding 40% of the outliers is meaningless if
    it also holds 40% of all markets. Only a bucket where outliers concentrate is a class."""
    total = sum(int(r.get("outliers") or 0) for r in rows)
    samples = [s for r in rows for s in (r.get("outlier_samples") or [])]
    threshold = next((r.get("outlier_cents") for r in rows if r.get("outlier_cents")), 5.0)

    print(f"\n=== LARGE DISAGREEMENTS (>{threshold:g}c on midpoint or ask) ===")
    if not total:
        print("  none recorded yet — either the capture predates this deploy, or the tape is"
              "\n  unusually clean. Re-check once a few cycles have run.")
        return
    print(f"  {total:,d} of {compared:,d} scored markets ({100.0 * total / compared:.2f}%)"
          f" — {len(samples):,d} sampled for characterization")
    if not samples:
        return

    for label, keyfn in (("series", lambda s: s.get("series") or "?"),
                         ("volume", lambda s: _vol_bucket(s.get("vol"))),
                         ("hrs to close", lambda s: _htc_bucket(s.get("htc"))),
                         ("bid depth", lambda s: _vol_bucket(s.get("d_bid")))):
        counts: dict[str, int] = {}
        for s in samples:
            k = keyfn(s)
            counts[k] = counts.get(k, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:6]
        cells = "  ".join(f"{k}:{n} ({100.0 * n / len(samples):.0f}%)" for k, n in top)
        print(f"  by {label:<13s} {cells}")

    worst = sorted(samples, key=lambda s: -max(s.get("d_mid") or 0, s.get("d_ask") or 0))[:5]
    print("\n  worst sampled disagreements:")
    for s in worst:
        print(f"    {str(s.get('ticker', '?'))[:34]:<34s} d_mid={s.get('d_mid')}"
              f" d_ask={s.get('d_ask')} ob={s.get('ob_bid')}/{s.get('ob_ask')}"
              f" inline={s.get('in_bid')}/{s.get('in_ask')} vol={s.get('vol')}"
              f" htc={s.get('htc')}")
    print("\n  A class is a bucket where outliers CONCENTRATE relative to that bucket's share of"
          "\n  all markets — not merely a bucket holding many of them. If one exists, the"
          "\n  pre-filter can distrust the inline quote there and fetch the orderbook anyway.")


def _print_tier(limits: dict | None) -> None:
    if not limits:
        print("  tier: UNKNOWN — no `kalshi_limits` row yet. The worker records it at startup"
              "\n        (main._probe_api_limits); redeploy, or the probe is failing.")
        return
    # Kalshi has renamed these fields before, so search rather than assume one schema, and fall
    # back to printing the payload — an unrecognised shape must not hide the answer.
    # Measured 2026-08-12, the live shape is:
    #   {"usage_tier": "basic", "grants": [],
    #    "read":  {"refill_rate": 200, "bucket_capacity": 400},
    #    "write": {"refill_rate": 100, "bucket_capacity": 100}}
    tier = next((limits[k] for k in ("usage_tier", "tier", "api_usage_level", "level", "name")
                 if isinstance(limits.get(k), str)), None)
    print(f"  tier: {tier or '(unnamed)'}")
    # The rate numbers live one level down, so a scalars-only filter drops exactly the part that
    # matters. Flatten one level, and convert refill rate into the unit we actually reason in:
    # requests/sec at Kalshi's default 10 tokens per request.
    for side in ("read", "write"):
        cfg = limits.get(side)
        if isinstance(cfg, dict):
            refill = cfg.get("refill_rate")
            burst = cfg.get("bucket_capacity")
            rps = f" = {refill / 10:.0f} req/sec" if isinstance(refill, (int, float)) else ""
            print(f"  {side:<5s} refill={refill} tokens/sec{rps}  burst={burst}")
    scalars = {k: v for k, v in limits.items()
               if isinstance(v, (int, float, bool)) or (isinstance(v, str) and k != tier)}
    extra = {k: v for k, v in scalars.items() if k not in ("usage_tier", "tier")}
    if extra:
        print("  " + "  ".join(f"{k}={v}" for k, v in list(extra.items())[:8]))


def report(rows: list[dict], transient: list[dict], hours: float,
           limits: dict | None = None) -> None:
    if not rows:
        print(f"=== mmsell quote parity: NO CYCLES in the last {hours:g}h ===")
        print("  Either the worker is not running its mmsell cycle, this predates the experiment"
              "\n  (added 2026-08-11), or mmsell_quote_parity is off. Check the worker is alive"
              "\n  before reading anything into it.")
        # The tier still prints: it is recorded at worker STARTUP, so it is available in exactly
        # the window (just after a deploy, before the first scan) where this branch fires — and
        # that is when it is most wanted.
        print("\n=== RATE LIMITS ===")
        _print_tier(limits)
        return

    agg = _merge(rows)
    compared = agg["compared"]
    margins = agg["margins"] or sorted(
        {m for b in agg["bands"].values() for m in b["fetch_at"]}, key=float)

    print(f"=== mmsell inline-quote parity — {len(rows)} cycles, {compared:,d} markets scored,"
          f" last {hours:g}h ===")
    print(f"  markets with a two-sided orderbook (the population)  {compared:>10,d}")
    print(f"  ...of which the inline quote was absent/one-sided    {agg['inline_missing']:>10,d}"
          f"  ({(100.0 * agg['inline_missing'] / compared if compared else 0.0):.2f}%)")
    print(f"  skipped: orderbook not two-sided (no ground truth)   {agg['ob_not_two_sided']:>10,d}")

    print("\n=== AGREEMENT — |inline - orderbook|, cents ===")
    print(_hist_line("yes-bid", agg["bid_hist"], sum(agg["bid_hist"].values())))
    print(_hist_line("yes-ask", agg["ask_hist"], sum(agg["ask_hist"].values())))
    print(_hist_line("midpoint", agg["mid_hist"], sum(agg["mid_hist"].values())))
    print(f"  worst single disagreement: midpoint {agg['max_mid_delta']:.1f}c,"
          f" ask {agg['max_ask_delta']:.1f}c")
    print("  The ASK is the derived side (ours is 100 - no_bid, Kalshi's is reported), and it is"
          "\n  what `maxyes` gates on — so it can break the tight band on its own.")

    print("\n=== DECISION TABLE — what a pre-filter would actually do ===")
    print("  `fetch` = orderbook calls the pre-filter would make (today: every `compared` market)")
    print("  `miss`  = IN-BAND markets it would throw away. This is the number that decides it.")
    for name, b in sorted(agg["bands"].items()):
        ob_in = b["ob_in"]
        print(f"\n  --- band {name!r}: {ob_in:,d} in-band markets out of {compared:,d} scored ---")
        print(f"    {'margin':>7} {'fetch':>10} {'vs today':>9} {'miss':>8} {'miss%':>7}")
        for m in margins:
            f = int(b["fetch_at"].get(m) or 0)
            miss = int(b["miss_at"].get(m) or 0)
            save = (100.0 * (1 - f / compared)) if compared else 0.0
            print(f"    {m:>6}c {f:>10,d} {save:>8.1f}% {miss:>8,d}"
                  f" {(100.0 * miss / ob_in if ob_in else 0.0):>6.2f}%")
        nq = b["miss_no_quote"]
        print(f"    smallest margin that misses nothing recoverable: {b['worst_margin']:.1f}c")
        print(f"    unrecoverable (no inline quote at all): {nq:,d}"
              f"  ({(100.0 * nq / ob_in if ob_in else 0.0):.3f}% of in-band)")

    _print_ex_inplay(rows, agg, margins)
    _print_outliers(rows, compared)

    print("\n=== RATE LIMITS — retryable Kalshi responses by status ===")
    _print_tier(limits)
    if transient:
        tot = _transient_totals(transient)
        # Cycles in THIS window — the denominator that makes the count comparable across runs.
        n_cycles = max(1, len(rows))
        if tot:
            for status, n in sorted(tot.items(), key=lambda kv: -kv[1]):
                label = {"0": "network error", "429": "RATE LIMITED"}.get(status, f"HTTP {status}")
                print(f"  {label:<16s} {n:>8,d}")
        # Judge the RATE, not the raw count. The count grows with the window and with the
        # number of cycles, so a flat "any 429 is over tier" banner shouted through a 44%
        # improvement when the ADVANCED grant landed (11.6 -> 6.5 per cycle while the scan got
        # 50% deeper). What matters operationally is how much of a cycle's request budget is
        # being spent on backoffs, which is per-cycle and comparable across windows.
        rate_limited = int(tot.get("429") or 0)
        per_cycle = rate_limited / max(1, n_cycles)
        # ~5s of forced backoff per cycle at 2s each. Below that the scan absorbs it; above it
        # the delay starts eating into a 30-minute cadence that also carries live order
        # management, and candidates begin getting dropped on exhausted retries.
        if per_cycle >= 25:
            print(f"  [OVER TIER] {per_cycle:.1f} rate-limits per cycle ({rate_limited:,d} over"
                  f" {n_cycles} cycles). At 2s of backoff each that is ~{per_cycle * 2:.0f}s"
                  "\n          lost per scan, and on exhausted retries the tracker drops that"
                  "\n          market's candidates silently. Stop widening the scan.")
        elif per_cycle >= 2:
            print(f"  [WATCH] {per_cycle:.1f} rate-limits per cycle ({rate_limited:,d} over"
                  f" {n_cycles} cycles) — absorbed by the retry loop, but this is the number to"
                  "\n          re-check before each step up in scan depth.")
        elif rate_limited:
            print(f"  [OK] {rate_limited:,d} rate-limits over {n_cycles} cycles"
                  f" ({per_cycle:.2f}/cycle) — negligible.")
        else:
            print("  [OK] no 429s — we are inside our Kalshi tier at the current scan size.")
    else:
        print("  no transient counters yet (worker predates the 2026-08-11 build)")

    print("\n=== VERDICT ===")
    if compared < MIN_COMPARED or len(rows) < MIN_CYCLES:
        print(f"  [WAIT] {compared:,d}/{MIN_COMPARED:,d} markets over {len(rows)}/{MIN_CYCLES}"
              " cycles. Not enough to decide — this is a data-maturity wait, not a result.")
        return
    for name, b in sorted(agg["bands"].items()):
        ob_in = b["ob_in"] or 1
        nq_rate = b["miss_no_quote"] / ob_in
        safe = b["worst_margin"]
        f = int(b["fetch_at"].get(f"{safe:g}") or 0)
        if nq_rate > MAX_NO_QUOTE_RATE:
            print(f"  [FAIL {name}] {100 * nq_rate:.2f}% of in-band markets have NO inline quote."
                  " No margin recovers those — a pre-filter would lose them outright.")
        elif safe > MAX_SAFE_MARGIN:
            print(f"  [FAIL {name}] needs a {safe:.1f}c margin to miss nothing, over the"
                  f" {MAX_SAFE_MARGIN:.0f}c cap. That is not a pre-filter, it is the band again.")
        elif compared and f >= 0.75 * compared:
            print(f"  [FAIL {name}] safe at {safe:.1f}c, but that still fetches {f:,d} of"
                  f" {compared:,d} orderbooks — too little saving to be worth the risk.")
        else:
            print(f"  [PASS {name}] a {safe:.1f}c-margin pre-filter misses nothing and fetches"
                  f" {f:,d} instead of {compared:,d} orderbooks"
                  f" ({100.0 * (1 - f / compared):.0f}% fewer).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=72.0, help="lookback window (default 72)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            rows = load_rows(cur, args.hours)
            transient = load_transient(cur, args.hours)
            limits = load_limits(cur)
    report(rows, transient, args.hours, limits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
