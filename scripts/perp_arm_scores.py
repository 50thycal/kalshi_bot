"""PERP-V1 Probe 2 — score the collected perp tape against the pre-registered arm gates.

Pre-registration: `docs/PERP_V1_THESIS.md` §4-§5. Package: `kalshi_bot/experiment_os/perp_v1.py`.
Metric declarations: `kalshi_bot/experiment_os/metrics.py` (all seven `perp_*` keys are
declared `provided=False` — this script is their named provider).

WHAT THIS IS AND IS NOT
-----------------------
It is an INSTRUMENT. It reads the tape the collector has written since 2026-08-30 and
computes the pre-registered quantities. It records nothing, transitions nothing and
authorizes nothing: a gate result is entered by the designated evaluator against the
registered gate, and a number printed here is an input to that, never a verdict.

It is also, deliberately, a report of what CANNOT be computed. Three pre-registered
inputs are missing from the surface as it actually is, and the wrong response to that is
to quietly compute something adjacent and print it under the registered metric key:

  1. FUNDING IS UNREADABLE. `/margin/funding_history` returns an empty list unscoped,
     scoped to KXAAVEPERP, and scoped twice to KXBTCPERP (the largest open interest) over
     a 7-day window; no funding field rides on the market row (24 keys across 252 live
     snapshots). See docs/RESEARCH_JOURNAL.md, PERP-V1 FUNDING 2026-08-30 (2), and
     WS-010 D4 CLOSED. Consequences, in order of how much they hurt:

       * `perp_net_edge_bps_per_trade` is DEFINED as net of "fees, slippage AND funding
         paid or received while holding". Without funding that quantity does not exist,
         so this script does not print one. It prints `..._ex_funding` beside it, under a
         different name, because a number that omits a cost the definition names is a
         different number and giving it the registered name would let it be read into a
         gate that asked for something else.
       * arm A's entry condition ("estimated funding rate agrees in sign with the
         premium") could not be evaluated, so arm A is scored on the premium z-score
         ALONE. That is a weaker filter than the one registered; a pre-registered
         condition that could not be evaluated is not the same experiment as one that was
         evaluated and passed, and arm A's result says so on its own line.
       * arm A's registered exit "max_hold_funding_windows: 1" has no clock. A wall-clock
         `--max-hold-min` substitutes for it and is printed as the substitution it is.
       * arm A's registered z-score conditioning ("matched time-to-funding bucket") also
         has no clock, so the z-score here is an unconditioned trailing one. Premium is
         mechanically dependent on where the funding cycle sits, which is exactly why the
         conditioning was registered — so this z is noisier than the registered one, in
         an unknown direction.
       * arm B (`perpcarry`) ranks its whole universe on funding. It is BLOCKED_DATA. It
         is not re-scoped to a proxy here: substituting premium for funding would be a
         different hypothesis wearing arm B's pre-registered gate.

  2. THE FEE IS UNKNOWN. `/margin/fee_tiers` needs credentials the ops runner does not
     hold. Rather than guess one and flatter or bury the edge, every arm reports its
     BREAKEVEN round-trip fee: the cost at which the measured edge is exactly zero. That
     is the number an operator can check against a real fee schedule.

  3. THE TAPE IS SLOWER THAN THE SIGNAL. Measured cadence is ~145 s (26 cycles/hour),
     against a configured `PERPS_INTERVAL_SECONDS=60`, because the collector shares the
     worker's loop. Arm C's registered forward horizons are 5/10/30/60/300 s; everything
     under one sampling interval is INVISIBLE, and this script refuses those horizons
     instead of interpolating them. An arm C null at 300 s is therefore a null about
     ~5-minute lead, not about the mechanism.

READ EVERY NUMBER AGAINST COVERAGE
----------------------------------
`perp_data_coverage_pct` is a gate clause on all three arms and a `hold_if` on the stop
gate, and it is computed against the INTENDED cadence, not the achieved one. Coverage
against what actually happened is 100% by construction and tells nobody anything.

Read-only, stdlib + psycopg. Runs locally or through the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/perp_arm_scores.py
    # {"type":"script","name":"perp_arm_scores","id":"pa-1"}
    # {"type":"script","name":"perp_arm_scores","id":"pa-2","args":["--hours","72"]}
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime

# The ops runner serves a `script` request with only `scripts/` on `sys.path` — the
# repo-root insertion in `ops_runner._dispatch` belongs to a different request type. So
# the repo root goes on the path here, before the import below, rather than being
# assumed. Under pytest the root is already on the path and this is a no-op, which is
# exactly why the first live run failed on an import a green test suite had exercised:
# the test environment was more generous than production. `tests/test_perp_arm_scores.py`
# now reproduces the runner's path instead of trusting pytest's.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# The floors are IMPORTED, never restated. A scorer that carried its own copy of the
# sample or coverage floor could drift from the registered gate and report a number
# against a bar nobody registered — which is the failure this whole apparatus exists to
# prevent. If this import fails the script must not run.
from kalshi_bot.experiment_os.perp_v1 import COVERAGE_FLOOR_PCT, SAMPLE_FLOOR  # noqa: E402

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=180000 "
    "-c idle_in_transaction_session_timeout=180000"
)

#: The configured collector interval (`Settings.perps_interval_seconds`). Coverage is
#: measured against THIS, not against the cadence the worker actually achieves.
INTENDED_INTERVAL_SEC = 60.0

#: Measured cadence, 26 cycles over an hour on 2026-08-30. Arm C horizons below this are
#: not measurable and are refused rather than reported as nulls.
MEASURED_CADENCE_SEC = 145.0

#: Registered arm C horizons (`perp_v1.ARMS`, arm `perplead`, `forward_horizons_sec`).
REGISTERED_HORIZONS_SEC = (5, 10, 30, 60, 300)

BLOCKED_ARM_B = (
    "arm B (perpcarry) is BLOCKED_DATA: its ranking input (estimated 8h funding rate) "
    "has no reachable source on this surface. Not scored, not re-scoped, not deleted — "
    "it reaches its gate and fails to produce evidence, which is the honest outcome."
)


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def asset_of(ticker: str) -> str | None:
    """`KXBTCPERP` -> `BTC`; ladder series `KXBTCD`/`KXBTC` -> `BTC`.

    Returns None rather than a guess when the shape is unfamiliar, so an unrecognised
    ticker drops out of the arm C join instead of silently joining the wrong asset.
    """
    t = (ticker or "").strip().upper()
    if not t.startswith("KX"):
        return None
    body = t[2:]
    for suffix in ("PERP", "15M", "D"):
        if body.endswith(suffix) and len(body) > len(suffix):
            body = body[: -len(suffix)]
            break
    return body or None


# ---------------------------------------------------------------------------
# Small statistics, written out rather than imported
# ---------------------------------------------------------------------------

def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def stdev(xs: list[float]) -> float | None:
    """Sample standard deviation. None below two points — a one-point window has no
    dispersion, and returning 0.0 there would make every z-score infinite."""
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Rank correlation, tie-corrected. Spearman rather than Pearson because a perp
    return distribution has tails that let three prints decide a Pearson IC."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def fmt(v: float | None, nd: int = 2) -> str:
    return "n/a" if v is None else f"{v:.{nd}f}"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_cycles(cur, hours: float) -> list[dict]:
    cur.execute(
        "SELECT started_at, finished_at, markets_seen, market_snapshots,"
        " orderbook_snapshots, funding_rows, errors"
        " FROM perp_collector_cycles"
        " WHERE started_at > now() - (%s || ' hours')::interval"
        " ORDER BY started_at",
        (str(hours),),
    )
    cols = ("started_at", "finished_at", "markets_seen", "market_snapshots",
            "orderbook_snapshots", "funding_rows", "errors")
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def load_snapshots(cur, hours: float) -> dict[str, list[dict]]:
    """Per-ticker market snapshots, oldest first.

    `settlement_mark_price` is selected beside `premium_bps` because the collector falls
    back to the last-traded `price` when the mark is absent, and it was absent on 24 of
    252 rows in the first live sample. On those rows `premium_bps` is a
    last-trade-vs-index number rather than a mark-vs-index one — a different quantity,
    and arm A's entire signal — so arm A conditions on the mark being present and the
    split is reported.
    """
    cur.execute(
        "SELECT ticker, captured_at, bid, ask, price, premium_bps, reference_price,"
        " settlement_mark_price, open_interest"
        " FROM perp_market_snapshots"
        " WHERE captured_at > now() - (%s || ' hours')::interval"
        " ORDER BY ticker, captured_at",
        (str(hours),),
    )
    cols = ("ticker", "captured_at", "bid", "ask", "price", "premium_bps",
            "reference_price", "settlement_mark_price", "open_interest")
    out: dict[str, list[dict]] = defaultdict(list)
    for row in cur.fetchall():
        r = dict(zip(cols, row, strict=True))
        out[r["ticker"]].append(r)
    return dict(out)


def load_ladder(cur, hours: float) -> dict[str, list[dict]]:
    """Theta's crypto-ladder tape, keyed by market ticker, oldest first.

    Only rows carrying both a mid and a model probability are loaded: arm C's metric is
    INCREMENTAL over theta, so a row where theta had no opinion cannot contribute to a
    comparison against theta.
    """
    cur.execute(
        "SELECT market_ticker, series, captured_at, mid_cents, yes_bid_cents,"
        " yes_ask_cents, model_p, model_excess_cents, minutes_to_close"
        " FROM crypto_ladder_snapshots"
        " WHERE captured_at > now() - (%s || ' hours')::interval"
        "   AND mid_cents IS NOT NULL AND model_excess_cents IS NOT NULL"
        "   AND market_ticker IS NOT NULL"
        " ORDER BY market_ticker, captured_at",
        (str(hours),),
    )
    cols = ("market_ticker", "series", "captured_at", "mid_cents", "yes_bid_cents",
            "yes_ask_cents", "model_p", "model_excess_cents", "minutes_to_close")
    out: dict[str, list[dict]] = defaultdict(list)
    for row in cur.fetchall():
        r = dict(zip(cols, row, strict=True))
        out[r["market_ticker"]].append(r)
    return dict(out)


# ---------------------------------------------------------------------------
# Coverage — `perp_data_coverage_pct`, experiment-scoped
# ---------------------------------------------------------------------------

def coverage(cycles: list[dict], hours: float, interval_sec: float) -> dict:
    """Share of the intended asset x time tape the collector actually captured.

    Two independent ways to miss tape, reported separately because they have different
    fixes: the collector can run too few times (cadence), or run and write fewer rows
    than it saw markets (per-cycle). The gate clause reads their product, because a tape
    that is half the cycles at half the markets is a quarter of the intended tape.
    """
    span_sec = hours * 3600.0
    expected_cycles = span_sec / interval_sec if interval_sec > 0 else 0.0
    observed = len(cycles)
    cadence_pct = (100.0 * observed / expected_cycles) if expected_cycles > 0 else None

    seen = sum(c["markets_seen"] or 0 for c in cycles)
    written = sum(c["market_snapshots"] or 0 for c in cycles)
    per_cycle_pct = (100.0 * written / seen) if seen else None

    combined = None
    if cadence_pct is not None and per_cycle_pct is not None:
        combined = cadence_pct * per_cycle_pct / 100.0
    elif cadence_pct is not None:
        combined = cadence_pct

    achieved_sec = None
    if observed >= 2:
        first, last = cycles[0]["started_at"], cycles[-1]["started_at"]
        if isinstance(first, datetime) and isinstance(last, datetime):
            gaps = (last - first).total_seconds()
            achieved_sec = gaps / (observed - 1) if observed > 1 else None

    return {
        "cycles": observed,
        "expected_cycles": expected_cycles,
        "cadence_pct": cadence_pct,
        "markets_seen": seen,
        "snapshots_written": written,
        "per_cycle_pct": per_cycle_pct,
        "perp_data_coverage_pct": combined,
        "achieved_interval_sec": achieved_sec,
        "errors": sum(c["errors"] or 0 for c in cycles),
    }


# ---------------------------------------------------------------------------
# Arm A — premium reversion
# ---------------------------------------------------------------------------

def half_spread_bps(row: dict) -> float | None:
    """One side's cost of crossing, in bps of mid. None when the book is one-sided —
    an absent quote is not a tight one, and defaulting it to zero would hand arm A a
    free round trip on exactly the illiquid names where it is most likely to trade."""
    bid, ask = row.get("bid"), row.get("ask")
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return 10_000.0 * (ask - bid) / 2.0 / mid


def score_arm_a(
    snaps: dict[str, list[dict]], *, window: int, entry_z: float, exit_z: float,
    exit_residual_bps: float, max_hold_min: float, seed: int,
) -> dict:
    """Registered rule, minus the two clauses funding was supposed to supply.

    Entry: |z| of `premium_bps` over a trailing `window` of the same ticker's own
    observations exceeds `entry_z`, direction AGAINST the premium (rich mark -> short).
    Exit: |z| back inside `exit_z`, |premium| under `exit_residual_bps`, or the hold cap.

    P&L is the signed convergence of the premium itself, in bps of notional: a short
    entered at +40 bps and closed at +5 bps earned 35 bps of convergence. That is the
    quantity the mechanism claims and it is measured on the two prices the collector
    paired inside ONE poll, never on prices re-paired across polls.

    The control (`perpctl`) takes these exact entries — same ticker, same instant, same
    hold — and randomises only direction, so the comparison isolates the mechanism from
    the crypto tape. It is seeded so a re-run of the same tape gives the same control.
    """
    rng = random.Random(seed)
    trades: list[dict] = []
    ctl: list[dict] = []
    skipped_no_mark = 0
    rows_seen = 0

    for ticker, rows in sorted(snaps.items()):
        usable = [r for r in rows if r["premium_bps"] is not None]
        rows_seen += len(usable)
        # Arm A's signal is mark-vs-index. Where the mark is absent the collector wrote a
        # last-trade-vs-index number under the same column; those rows are dropped rather
        # than mixed, and counted so the drop is visible.
        marked = [r for r in usable if r["settlement_mark_price"] is not None]
        skipped_no_mark += len(usable) - len(marked)
        if len(marked) <= window:
            continue

        i = window
        while i < len(marked):
            hist = [r["premium_bps"] for r in marked[i - window:i]]
            mu, sd = mean(hist), stdev(hist)
            row = marked[i]
            if mu is None or sd is None or sd <= 0:
                i += 1
                continue
            z = (row["premium_bps"] - mu) / sd
            if abs(z) < entry_z:
                i += 1
                continue

            direction = -1 if z > 0 else 1        # fade the premium
            entry_prem = row["premium_bps"]
            hs_in = half_spread_bps(row)
            if hs_in is None:
                i += 1
                continue

            j = i + 1
            exit_row = None
            while j < len(marked):
                cand = marked[j]
                held_min = (cand["captured_at"] - row["captured_at"]).total_seconds() / 60.0
                zj = (cand["premium_bps"] - mu) / sd
                if (abs(zj) <= exit_z or abs(cand["premium_bps"]) <= exit_residual_bps
                        or held_min >= max_hold_min):
                    exit_row = cand
                    break
                j += 1
            if exit_row is None:
                break                              # tape ends mid-trade: not a round trip

            hs_out = half_spread_bps(exit_row)
            if hs_out is None:
                i = j + 1
                continue

            convergence = direction * (exit_row["premium_bps"] - entry_prem)
            spread_cost = hs_in + hs_out
            held_min = (exit_row["captured_at"] - row["captured_at"]).total_seconds() / 60.0
            trades.append({
                "ticker": ticker, "entry_at": row["captured_at"], "z": z,
                "direction": direction, "gross_bps": convergence,
                "spread_bps": spread_cost, "net_ex_funding_bps": convergence - spread_cost,
                "held_min": held_min,
            })
            ctl_dir = rng.choice((-1, 1))
            ctl_conv = ctl_dir * (exit_row["premium_bps"] - entry_prem)
            ctl.append({
                "ticker": ticker, "entry_at": row["captured_at"], "direction": ctl_dir,
                "gross_bps": ctl_conv, "spread_bps": spread_cost,
                "net_ex_funding_bps": ctl_conv - spread_cost, "held_min": held_min,
            })
            i = j + 1

    return {
        "trades": trades, "control": ctl,
        "rows_with_premium": rows_seen, "rows_dropped_no_mark": skipped_no_mark,
    }


def summarize_trades(trades: list[dict]) -> dict:
    net = [t["net_ex_funding_bps"] for t in trades]
    return {
        "n": len(trades),
        "gross_bps": mean([t["gross_bps"] for t in trades]),
        "spread_bps": mean([t["spread_bps"] for t in trades]),
        "net_ex_funding_bps": mean(net),
        "sd_bps": stdev(net),
        "hold_min": mean([t["held_min"] for t in trades]),
        "win_rate": (100.0 * sum(1 for x in net if x > 0) / len(net)) if net else None,
    }


# ---------------------------------------------------------------------------
# Arm C — perp -> prediction lead/lag
# ---------------------------------------------------------------------------

def measurable_horizons(cadence_sec: float) -> tuple[list[int], list[int]]:
    """Split the registered horizons into what this tape can and cannot see.

    A forward move over a horizon shorter than one sampling interval is not a small
    signal, it is an unobserved one. Reporting a null for it would read as a kill on the
    mechanism at that horizon; refusing it says the instrument was never pointed there.
    """
    ok = [h for h in REGISTERED_HORIZONS_SEC if h >= cadence_sec]
    return ok, [h for h in REGISTERED_HORIZONS_SEC if h < cadence_sec]


def perp_features(rows: list[dict]) -> list[tuple[datetime, dict[str, float]]]:
    """Per-observation features, each timestamped at the instant of the LATER of the two
    observations it is built from — so a feature is never available before every input
    that made it. The MLBWX probe manufactured a +5.5c edge by taking direction from a
    price that had not happened yet; this is the structural answer to that."""
    out: list[tuple[datetime, dict[str, float]]] = []
    for prev, cur in zip(rows, rows[1:], strict=False):
        px_prev, px_cur = prev.get("price"), cur.get("price")
        feats: dict[str, float] = {}
        if px_prev and px_cur and px_prev > 0:
            feats["perp_return_bps"] = 10_000.0 * (px_cur - px_prev) / px_prev
        if prev.get("premium_bps") is not None and cur.get("premium_bps") is not None:
            feats["premium_impulse_bps"] = cur["premium_bps"] - prev["premium_bps"]
        if prev.get("open_interest") and cur.get("open_interest"):
            if prev["open_interest"] > 0:
                feats["oi_impulse_pct"] = (
                    100.0 * (cur["open_interest"] - prev["open_interest"])
                    / prev["open_interest"]
                )
        if feats:
            out.append((cur["captured_at"], feats))
    return out


def _latest_before(series: list[tuple[datetime, dict[str, float]]], at: datetime,
                   max_age_sec: float) -> dict[str, float] | None:
    best = None
    for ts, feats in series:
        if ts <= at:
            best = (ts, feats)
        else:
            break
    if best is None:
        return None
    if (at - best[0]).total_seconds() > max_age_sec:
        return None
    return best[1]


def score_arm_c(
    snaps: dict[str, list[dict]], ladder: dict[str, list[dict]], *,
    horizons: list[int], max_feature_age_sec: float, theta_entry_cents: float,
) -> dict:
    """Two questions, kept separate on purpose.

    IC (`perp_signal_ic`) asks whether a perp feature carries information about the
    forward move of the event contract. It is a diagnostic and is not gated, because the
    repository has twice now found a real signal that could not be traded through the
    spread (mmsell6, mmsell11).

    Incremental cents (`perp_incremental_cents_per_trade_vs_theta`) asks the gated
    question: taking theta's own decisions as the baseline, does REQUIRING the perp
    feature to agree add realizable cents per trade? Baseline and overlay are scored on
    the identical mark — forward mid move in the traded direction, less the half-spread
    paid to get in — so the difference is the overlay and nothing else.
    """
    by_asset_feats: dict[str, list[tuple[datetime, dict[str, float]]]] = {}
    for ticker, rows in snaps.items():
        asset = asset_of(ticker)
        if asset:
            by_asset_feats.setdefault(asset, []).extend(perp_features(rows))
    for asset in by_asset_feats:
        by_asset_feats[asset].sort(key=lambda p: p[0])

    ic_pairs: dict[tuple[str, int], tuple[list[float], list[float]]] = defaultdict(
        lambda: ([], []))
    baseline: dict[int, list[float]] = defaultdict(list)
    overlay: dict[int, list[float]] = defaultdict(list)
    matched = 0
    unmatched = 0

    for _mkt, rows in ladder.items():
        asset = asset_of(rows[0].get("series") or "")
        feats_series = by_asset_feats.get(asset or "")
        if not feats_series:
            continue
        for idx, row in enumerate(rows):
            feats = _latest_before(feats_series, row["captured_at"], max_feature_age_sec)
            if feats is None:
                unmatched += 1
                continue
            matched += 1
            for h in horizons:
                fut = None
                for later in rows[idx + 1:]:
                    dt = (later["captured_at"] - row["captured_at"]).total_seconds()
                    if dt >= h:
                        # Reject a "horizon" that is really a much later quote: a 300 s
                        # horizon filled by a 40-minute-old next row is not the horizon.
                        if dt <= h * 3:
                            fut = later
                        break
                if fut is None:
                    continue
                fwd = fut["mid_cents"] - row["mid_cents"]
                for name, val in feats.items():
                    xs, ys = ic_pairs[(name, h)]
                    xs.append(val)
                    ys.append(fwd)

                excess = row["model_excess_cents"]
                if abs(excess) < theta_entry_cents:
                    continue
                # theta sells richness: mid above model -> short YES.
                direction = -1 if excess > 0 else 1
                bid, ask = row.get("yes_bid_cents"), row.get("yes_ask_cents")
                cost = ((ask - bid) / 2.0) if (bid is not None and ask is not None
                                               and ask >= bid) else None
                if cost is None:
                    continue
                realized = direction * fwd - cost
                baseline[h].append(realized)
                ret = feats.get("perp_return_bps")
                if ret is None:
                    continue
                # The overlay: trade theta's idea only when the perp move agrees with the
                # direction theta wants. Same trade, same mark, fewer of them.
                if (ret > 0) == (direction > 0):
                    overlay[h].append(realized)

    ic = {}
    for (name, h), (xs, ys) in sorted(ic_pairs.items()):
        ic[(name, h)] = (spearman(xs, ys), len(xs))

    incr = {}
    for h in horizons:
        b, o = baseline[h], overlay[h]
        mb, mo = mean(b), mean(o)
        incr[h] = {
            "baseline_n": len(b), "baseline_cents": mb,
            "overlay_n": len(o), "overlay_cents": mo,
            "incremental_cents": (mo - mb) if (mb is not None and mo is not None) else None,
        }
    return {"ic": ic, "incremental": incr, "matched": matched, "unmatched": unmatched}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def breakeven_fee_bps(net_ex_funding: float | None) -> float | None:
    """The round-trip fee at which the measured edge is exactly zero. Negative means the
    edge is already under water before any fee."""
    return net_ex_funding


def report(cov: dict, a: dict, c: dict, args, refused: list[int]) -> None:
    print("=== PERP-V1 Probe 2 — arm scores ===")
    print(f"  window: last {args.hours:g}h   |   instrument only: records nothing, "
          "authorizes nothing")

    # -- coverage -----------------------------------------------------------
    print("\n--- perp_data_coverage_pct (experiment scope) ---")
    print(f"  cycles observed        {cov['cycles']}")
    print(f"  cycles intended        {cov['expected_cycles']:.0f}"
          f"  (at {args.interval_sec:g}s, the configured PERPS_INTERVAL_SECONDS)")
    print(f"  cadence coverage       {fmt(cov['cadence_pct'])}%")
    print(f"  per-cycle coverage     {fmt(cov['per_cycle_pct'])}%"
          f"  ({cov['snapshots_written']} written / {cov['markets_seen']} seen)")
    print(f"  achieved interval      {fmt(cov['achieved_interval_sec'], 1)}s")
    print(f"  collector errors       {cov['errors']}")
    print(f"  => perp_data_coverage_pct = {fmt(cov['perp_data_coverage_pct'])}%"
          f"   (floor {COVERAGE_FLOOR_PCT}%)")
    if cov["perp_data_coverage_pct"] is not None and \
            cov["perp_data_coverage_pct"] < COVERAGE_FLOOR_PCT:
        print("  [BELOW FLOOR] every promotion gate carries this clause and the stop gate"
              "\n    holds on it, so no arm can PASS on this tape whatever its edge. The"
              "\n    shortfall is CADENCE, not the collector failing: it shares the worker"
              "\n    loop, which takes ~145s per pass against a 60s interval. Closing it is"
              "\n    a platform change (its own collector cadence), not a scorer change —"
              "\n    and lowering the floor after seeing this number would be re-tuning a"
              "\n    pre-registered gate against results.")

    # -- arm A --------------------------------------------------------------
    s = summarize_trades(a["trades"])
    sc = summarize_trades(a["control"])
    print("\n--- arm A: perprevert (premium reversion) ---")
    print(f"  premium rows           {a['rows_with_premium']}"
          f"   (dropped, no mark: {a['rows_dropped_no_mark']})")
    print(f"  perp_probe_observations  {s['n']}   (floor {SAMPLE_FLOOR})")
    print(f"  gross convergence      {fmt(s['gross_bps'])} bps/trade")
    print(f"  spread paid            {fmt(s['spread_bps'])} bps/trade")
    print(f"  net EX FUNDING         {fmt(s['net_ex_funding_bps'])} bps/trade"
          f"  (sd {fmt(s['sd_bps'])}, win {fmt(s['win_rate'], 1)}%)")
    print(f"  mean hold              {fmt(s['hold_min'], 1)} min")
    print(f"  breakeven round fee    {fmt(breakeven_fee_bps(s['net_ex_funding_bps']))} bps")
    print(f"  control (perpctl)      n={sc['n']}, net ex funding "
          f"{fmt(sc['net_ex_funding_bps'])} bps/trade")
    if s["net_ex_funding_bps"] is not None and sc["net_ex_funding_bps"] is not None:
        print("  delta vs control       "
              f"{fmt(s['net_ex_funding_bps'] - sc['net_ex_funding_bps'])} bps/trade")
    print("  perp_net_edge_bps_per_trade  NOT PRODUCIBLE — its definition nets funding,"
          "\n    and no funding source is reachable. The number above omits that cost and"
          "\n    carries a different name for exactly that reason.")
    print("  DEVIATIONS FROM PRE-REGISTRATION, all caused by the funding gap:")
    print("    * entry condition 'estimated funding agrees in sign with the premium' was"
          "\n      NOT EVALUATED. Arm A ran on the z-score alone — a weaker filter than"
          "\n      the registered one.")
    print("    * z-score conditioning 'matched time-to-funding bucket' was NOT APPLIED;"
          f"\n      an unconditioned trailing window of {args.window} observations stands"
          "\n      in for it.")
    print(f"    * exit 'max_hold_funding_windows: 1' has no clock; --max-hold-min="
          f"{args.max_hold_min:g} substitutes.")

    # -- arm B --------------------------------------------------------------
    print("\n--- arm B: perpcarry (funding dispersion) ---")
    print(f"  [BLOCKED_DATA] {BLOCKED_ARM_B}")
    print("  perp_funding_capture_bps  NO INPUT. perp_beta_adjusted_net_edge_bps  NO INPUT.")

    # -- arm C --------------------------------------------------------------
    print("\n--- arm C: perplead (perp -> prediction lead/lag) ---")
    if refused:
        print(f"  horizons NOT MEASURABLE on this tape: {refused}s"
              f"  (sampling interval ~{MEASURED_CADENCE_SEC:g}s)")
        print("    A null at these horizons would be an artefact of the instrument, so"
              "\n    none is reported. A short lead is untested here, not absent.")
    print(f"  ladder rows matched to a perp feature: {c['matched']}"
          f"   (unmatched: {c['unmatched']})")
    if not c["ic"]:
        print("  no feature/forward pairs — either no overlapping ladder tape in the"
              "\n  window, or no measurable horizon survives the cadence bound.")
    for (name, h), (val, n) in sorted(c["ic"].items()):
        print(f"  perp_signal_ic  {name:<22} h={h:>4}s  IC={fmt(val, 4)}  n={n}")
    for h, d in sorted(c["incremental"].items()):
        print(f"  h={h:>4}s  theta baseline {fmt(d['baseline_cents'])}c "
              f"(n={d['baseline_n']})  overlay {fmt(d['overlay_cents'])}c "
              f"(n={d['overlay_n']})")
        print(f"          perp_incremental_cents_per_trade_vs_theta = "
              f"{fmt(d['incremental_cents'])} c/trade")

    # -- what an evaluator may and may not read ------------------------------
    print("\n=== GATE READABILITY (this script records nothing) ===")
    print("  probe_to_paper_perprevert  NOT READABLE — its first clause names"
          "\n    perp_net_edge_bps_per_trade, which cannot be produced without funding.")
    print("  probe_to_paper_perpcarry   NOT READABLE — arm B is BLOCKED_DATA.")
    print("  probe_to_paper_perplead    NOT READABLE — same funding clause; and its"
          "\n    incremental metric is measurable only at horizons above the cadence.")
    print("  perp_probe_stop            its fail clauses read the same unproducible"
          "\n    metric; its hold_if on coverage is readable and is the live constraint.")
    print("  The honest verdict this tape supports is HOLD on all three arms — on"
          "\n  coverage, on sample, and on a missing cost input. HOLD on thin evidence is"
          "\n  the correct verdict, not a failure of the probe.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PERP-V1 Probe 2 — arm scores")
    ap.add_argument("--hours", type=float, default=72.0, help="lookback (default 72)")
    ap.add_argument("--interval-sec", type=float, default=INTENDED_INTERVAL_SEC,
                    help="INTENDED collector interval, the coverage denominator")
    ap.add_argument("--window", type=int, default=20,
                    help="trailing observations in arm A's z-score (default 20)")
    ap.add_argument("--entry-z", type=float, default=2.5)
    ap.add_argument("--exit-z", type=float, default=0.5)
    ap.add_argument("--exit-residual-bps", type=float, default=5.0)
    ap.add_argument("--max-hold-min", type=float, default=60.0,
                    help="stands in for the registered 'one funding window' exit")
    ap.add_argument("--theta-entry-cents", type=float, default=3.0,
                    help="|model_excess_cents| at which the theta baseline trades")
    ap.add_argument("--max-feature-age-sec", type=float, default=MEASURED_CADENCE_SEC * 2,
                    help="oldest perp feature arm C will attach to a ladder quote")
    ap.add_argument("--seed", type=int, default=20260830,
                    help="control randomisation seed; fixed so re-runs reproduce")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO")
                        or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cycles = load_cycles(cur, args.hours)
            snaps = load_snapshots(cur, args.hours)
            ladder = load_ladder(cur, args.hours)

    cov = coverage(cycles, args.hours, args.interval_sec)
    a = score_arm_a(
        snaps, window=args.window, entry_z=args.entry_z, exit_z=args.exit_z,
        exit_residual_bps=args.exit_residual_bps, max_hold_min=args.max_hold_min,
        seed=args.seed,
    )
    cadence = cov["achieved_interval_sec"] or MEASURED_CADENCE_SEC
    horizons, refused = measurable_horizons(cadence)
    c = score_arm_c(
        snaps, ladder, horizons=horizons,
        max_feature_age_sec=args.max_feature_age_sec,
        theta_entry_cents=args.theta_entry_cents,
    )
    report(cov, a, c, args, refused)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
