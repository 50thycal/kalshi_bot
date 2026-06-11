"""Model check: does the ensemble forecast distribution beat the market's implied
distribution on settled daily-temperature events — and would trading the gap have
made money after fees?

For each settled event and each entry window (default 20/14/8 hours-to-close):
  1. take the bucket-ladder snapshot at the window (the market's implied
     distribution at the moment the books trade),
  2. take the latest ensemble captured AT OR BEFORE that snapshot (strictly no
     lookahead) and turn its members into P(temperature lands in bucket) via a
     Gaussian kernel around each member (sigma=0 -> raw member counts),
  3. grade both distributions against the actual winning bucket (Brier score /
     log-loss), and
  4. simulate cost-aware trades on every bucket where the model disagrees with
     the price by more than --min-edge-cents (YES at the ask, NO at 100-bid,
     Kalshi fee applied) to get the hypothetical P&L.

Also prints the model's LIVE disagreements on currently-open events (what it
would trade right now) and a data-readiness section, since settled events only
become gradeable once they have both ladder and ensemble coverage.

Read-only and self-contained (stdlib + psycopg only) so it can run on the ops
runner. Bucket parsing mirrors kalshi_bot/weather/buckets.py; the fee mirrors
kalshi_bot/paper/engine.py::kalshi_fee.

Usage:
    DATABASE_URL_RO=postgresql://... python scripts/weather_model_check.py
    # or via the ops channel:
    {"type": "script", "name": "weather_model_check", "args": ["--sigma", "2"]}
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from statistics import mean, stdev

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

PROB_FLOOR = 1e-4  # keeps log-loss finite when a distribution zeroes the winner
MIN_EVENT_WINDOWS = 40  # below this, scores are noise — banner it
WINDOW_TOLERANCE_H = 2.0  # a "20h" grade must come from a snapshot taken >= 18h out
ENSEMBLE_MAX_AGE_H = 6.0  # don't grade with an ensemble staler than this

# --- bucket parsing (mirrors kalshi_bot/weather/buckets.py) --------------------

_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_DATE_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})")


def parse_bucket_range(subtitle: str | None) -> tuple[float | None, float | None]:
    if not subtitle:
        return (None, None)
    s = subtitle.lower()
    nums = [float(x) for x in _NUM.findall(subtitle)]
    if any(w in s for w in ("below", "or less", "under", "<")):
        return (None, nums[0] if nums else None)
    if any(w in s for w in ("above", "or more", "over", ">")):
        return (nums[0] if nums else None, None)
    if len(nums) >= 2:
        return (min(nums[:2]), max(nums[:2]))
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (None, None)


def event_target_date(event_ticker: str | None) -> str | None:
    """ISO target date parsed from tickers like KXHIGHNY-26JUN08."""
    match = _DATE_RE.search(event_ticker or "")
    if not match:
        return None
    month = _MONTHS.get(match.group(2))
    if not month:
        return None
    try:
        return date(2000 + int(match.group(1)), month, int(match.group(3))).isoformat()
    except ValueError:
        return None


# --- probability math -----------------------------------------------------------


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def member_bucket_prob(
    member_f: float, low: float | None, high: float | None, sigma: float
) -> float:
    """P(this member's true integer temperature lands in [low, high]).

    Buckets settle on integer-degree climate reports, so bucket [74, 75] covers the
    continuous range [73.5, 75.5). sigma is the kernel width around the member value
    (forecast error beyond ensemble spread); sigma=0 degrades to the raw indicator
    using the same rounding as kalshi_bot/weather/buckets.py::forecast_in_bucket.
    """
    if sigma <= 0:
        r = round(member_f)
        if low is not None and r < low:
            return 0.0
        if high is not None and r > high:
            return 0.0
        return 1.0
    upper = _phi((high + 0.5 - member_f) / sigma) if high is not None else 1.0
    lower = _phi((low - 0.5 - member_f) / sigma) if low is not None else 0.0
    return max(0.0, upper - lower)


def model_bucket_probs(
    members_by_model: dict[str, list[float]],
    buckets: list[tuple[float | None, float | None]],
    sigma: float,
    floor: float = PROB_FLOOR,
) -> list[float] | None:
    """Blend per-model bucket probabilities (equal model weight), floored + normalized."""
    per_model: list[list[float]] = []
    for members in members_by_model.values():
        members = [float(v) for v in members if v is not None]
        if not members:
            continue
        per_model.append(
            [mean(member_bucket_prob(v, lo, hi, sigma) for v in members) for lo, hi in buckets]
        )
    if not per_model:
        return None
    blend = [max(mean(col), floor) for col in zip(*per_model, strict=True)]
    total = sum(blend)
    return [p / total for p in blend]


def normalize_mids(mids: list[float | None], floor: float = PROB_FLOOR) -> list[float] | None:
    """The market's implied distribution: bucket mid prices normalized to sum to 1."""
    vals = [float(v) if v is not None and v > 0 else 0.0 for v in mids]
    total = sum(vals)
    if total < 20.0:  # degenerate / mostly-empty ladder
        return None
    probs = [max(v / total, floor) for v in vals]
    total = sum(probs)
    return [p / total for p in probs]


def brier(probs: list[float], winner_idx: int) -> float:
    return sum((p - (1.0 if i == winner_idx else 0.0)) ** 2 for i, p in enumerate(probs))


def log_loss(probs: list[float], winner_idx: int, floor: float = PROB_FLOOR) -> float:
    return -math.log(max(probs[winner_idx], floor))


def fee_cents(price_cents: float, enabled: bool = True) -> float:
    """Kalshi fee in cents at qty=1 (mirrors paper/engine.py::kalshi_fee)."""
    if not enabled:
        return 0.0
    p = price_cents / 100.0
    return float(math.ceil(0.07 * p * (1 - p) * 100))


# --- data shapes ------------------------------------------------------------------


@dataclass
class Bucket:
    market_ticker: str | None
    subtitle: str | None
    low_f: float | None
    high_f: float | None
    yes_bid: float | None
    yes_ask: float | None
    mid: float | None


@dataclass
class Cycle:
    captured_at: datetime
    hours_to_close: float | None
    buckets: list[Bucket]


@dataclass
class GradedWindow:
    event_ticker: str
    city: str
    kind: str
    window_h: float
    snapshot_htc: float
    models_used: list[str]
    ens_brier: float
    mkt_brier: float
    ens_ll: float
    mkt_ll: float
    ens_hit: bool
    mkt_hit: bool
    per_model_brier: dict[str, float]
    trades: list[tuple[str, str, float, float]]  # (side, subtitle, edge, pnl)


@dataclass
class Readiness:
    settled: int = 0
    with_ladder: int = 0
    with_ensemble: int = 0
    gradeable: int = 0
    skipped: dict[str, int] = field(default_factory=dict)


# --- selection helpers -------------------------------------------------------------


def cluster_cycles(rows: list[dict], gap_seconds: float = 90.0) -> list[Cycle]:
    """Group ladder rows into per-cycle snapshots (rows of one insert batch land
    within microseconds of each other; cycles are >= minutes apart)."""
    rows = sorted(rows, key=lambda r: r["captured_at"])
    cycles: list[Cycle] = []
    group: list[dict] = []
    for r in rows:
        if group and (r["captured_at"] - group[-1]["captured_at"]).total_seconds() > gap_seconds:
            cycles.append(_make_cycle(group))
            group = []
        group.append(r)
    if group:
        cycles.append(_make_cycle(group))
    return cycles


def _make_cycle(group: list[dict]) -> Cycle:
    by_ticker: dict[str | None, dict] = {}
    for r in group:  # keep the last row per bucket if dupes sneak in
        by_ticker[r["market_ticker"]] = r
    buckets = [
        Bucket(
            market_ticker=r["market_ticker"],
            subtitle=r["subtitle"],
            low_f=r["low_f"],
            high_f=r["high_f"],
            yes_bid=r["yes_bid_cents"],
            yes_ask=r["yes_ask_cents"],
            mid=r["mid_cents"],
        )
        for r in by_ticker.values()
    ]
    buckets.sort(key=lambda b: (b.low_f if b.low_f is not None else -1e9))
    htcs = [r["hours_to_close"] for r in group if r["hours_to_close"] is not None]
    return Cycle(
        captured_at=max(r["captured_at"] for r in group),
        hours_to_close=max(htcs) if htcs else None,
        buckets=buckets,
    )


def pick_cycle_for_window(
    cycles: list[Cycle], window_h: float, tolerance_h: float = WINDOW_TOLERANCE_H
) -> Cycle | None:
    """The first snapshot after the window opened (htc <= window), mirroring when the
    books actually enter. None if data only starts much later than the window."""
    eligible = [c for c in cycles if c.hours_to_close is not None and c.hours_to_close <= window_h]
    if not eligible:
        return None
    best = max(eligible, key=lambda c: c.hours_to_close)
    if window_h - best.hours_to_close > tolerance_h:
        return None
    return best


def pick_ensembles_at(
    rows: list[dict], at_time: datetime, max_age_h: float = ENSEMBLE_MAX_AGE_H
) -> dict[str, list[float]]:
    """Latest members per model captured at or before at_time (no lookahead)."""
    latest: dict[str, tuple[datetime, list[float]]] = {}
    for r in rows:
        cap = r["captured_at"]
        if cap > at_time or (at_time - cap).total_seconds() > max_age_h * 3600:
            continue
        model = r["model"] or "?"
        if model not in latest or cap > latest[model][0]:
            latest[model] = (cap, r["members_json"] or [])
    return {model: members for model, (_, members) in latest.items() if members}


def find_winner_idx(buckets: list[Bucket], winning_ticker, winning_subtitle) -> int | None:
    for i, b in enumerate(buckets):
        if winning_ticker and b.market_ticker == winning_ticker:
            return i
    for i, b in enumerate(buckets):
        if winning_subtitle and b.subtitle == winning_subtitle:
            return i
    return None


def ev_trades(
    probs: list[float],
    buckets: list[Bucket],
    winner_idx: int | None,
    min_edge_cents: float,
) -> list[tuple[str, str, float, float]]:
    """Cost-aware hypothetical trades: buy YES at ask / NO at 100-bid wherever the
    model's probability beats the price by min_edge after the fee. pnl is per
    contract held to settlement (no exit fee). winner_idx=None -> edges only, pnl=nan."""
    trades = []
    for i, (b, p) in enumerate(zip(buckets, probs, strict=True)):
        won = winner_idx is not None and i == winner_idx
        sub = b.subtitle or "?"
        if b.yes_ask is not None and 1 <= b.yes_ask <= 99:
            cost = b.yes_ask + fee_cents(b.yes_ask)
            edge = p * 100.0 - cost
            if edge >= min_edge_cents:
                pnl = ((100.0 if won else 0.0) - cost) if winner_idx is not None else math.nan
                trades.append(("yes", sub, edge, pnl))
        if b.yes_bid is not None and 1 <= b.yes_bid <= 99:
            no_ask = 100.0 - b.yes_bid
            cost = no_ask + fee_cents(no_ask)
            edge = (1.0 - p) * 100.0 - cost
            if edge >= min_edge_cents:
                pnl = ((0.0 if won else 100.0) - cost) if winner_idx is not None else math.nan
                trades.append(("no", sub, edge, pnl))
    return trades


# --- grading ------------------------------------------------------------------------


def grade_settled(
    settlements: list[dict],
    ladders: dict[str, list[dict]],
    ensembles: dict[tuple[str, str, str], list[dict]],
    windows: list[float],
    sigma: float,
    min_edge_cents: float,
) -> tuple[list[GradedWindow], Readiness]:
    graded: list[GradedWindow] = []
    ready = Readiness(settled=len(settlements))

    def skip(reason: str) -> None:
        ready.skipped[reason] = ready.skipped.get(reason, 0) + 1

    for st in settlements:
        ev = st["event_ticker"]
        kind = st["kind"] or "high"
        target = st["target_date"] or event_target_date(ev)
        rows = ladders.get(ev) or []
        if not rows:
            skip("no ladder snapshots")
            continue
        ready.with_ladder += 1
        cycles = cluster_cycles(rows)
        ens_rows = ensembles.get((st["city"] or "?", target or "?", kind)) or []
        if ens_rows:
            ready.with_ensemble += 1
        event_graded = False

        for w in windows:
            cycle = pick_cycle_for_window(cycles, w)
            if cycle is None:
                skip(f"no snapshot near h{int(w)}")
                continue
            members = pick_ensembles_at(ens_rows, cycle.captured_at)
            if not members:
                skip(f"no ensemble before h{int(w)} snapshot")
                continue
            ranges = [(b.low_f, b.high_f) for b in cycle.buckets]
            ens_probs = model_bucket_probs(members, ranges, sigma)
            mkt_probs = normalize_mids([b.mid for b in cycle.buckets])
            if ens_probs is None or mkt_probs is None:
                skip("degenerate distribution")
                continue
            winner = find_winner_idx(cycle.buckets, st["winning_ticker"], st["winning_subtitle"])
            if winner is None:
                skip("winner not in ladder")
                continue
            per_model = {
                name: brier(model_bucket_probs({name: mem}, ranges, sigma), winner)
                for name, mem in members.items()
            }
            graded.append(
                GradedWindow(
                    event_ticker=ev,
                    city=st["city"] or "?",
                    kind=kind,
                    window_h=w,
                    snapshot_htc=cycle.hours_to_close,
                    models_used=sorted(members),
                    ens_brier=brier(ens_probs, winner),
                    mkt_brier=brier(mkt_probs, winner),
                    ens_ll=log_loss(ens_probs, winner),
                    mkt_ll=log_loss(mkt_probs, winner),
                    ens_hit=max(range(len(ens_probs)), key=ens_probs.__getitem__) == winner,
                    mkt_hit=max(range(len(mkt_probs)), key=mkt_probs.__getitem__) == winner,
                    per_model_brier=per_model,
                    trades=ev_trades(ens_probs, cycle.buckets, winner, min_edge_cents),
                )
            )
            event_graded = True
        if event_graded:
            ready.gradeable += 1
    return graded, ready


def sigma_sensitivity(
    settlements, ladders, ensembles, windows, sigmas, min_edge_cents
) -> list[tuple[float, int, float, float, float]]:
    """(sigma, n, ens_brier, mkt_brier, ev_pnl) pooled over all graded windows."""
    out = []
    for sg in sigmas:
        graded, _ = grade_settled(settlements, ladders, ensembles, windows, sg, min_edge_cents)
        if not graded:
            out.append((sg, 0, math.nan, math.nan, math.nan))
            continue
        pnl = sum(t[3] for g in graded for t in g.trades if not math.isnan(t[3]))
        out.append(
            (sg, len(graded), mean(g.ens_brier for g in graded), mean(g.mkt_brier for g in graded), pnl)
        )
    return out


# --- report --------------------------------------------------------------------------


def _fmt(x, nd=3, width=7):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return " " * (width - 3) + "n/a"
    return f"{x:{width}.{nd}f}"


def report_graded(graded: list[GradedWindow], ready: Readiness, args) -> None:
    print(f"=== Ensemble model vs market — settled events "
          f"(sigma={args.sigma}, min_edge={args.min_edge_cents}c) ===")
    n = len(graded)
    n_events = len({g.event_ticker for g in graded})
    print(f"  graded: {n_events} events / {n} event-windows")
    if n < MIN_EVENT_WINDOWS:
        print(f"  *** SAMPLE TOO SMALL (have {n} event-windows, want >= {MIN_EVENT_WINDOWS}) ***")
        print("  *** treat everything below as a plumbing check, not evidence of edge ***")
    if not graded:
        return

    for kind in ("high", "low"):
        rows = [g for g in graded if g.kind == kind]
        if not rows:
            continue
        print(f"\n  --- {kind.upper()} — Brier by window (lower = better) ---")
        print("  window    n   ens_brier  mkt_brier  diff(e-m)   ens_ll  mkt_ll  ens_hit  mkt_hit")
        for w in sorted({g.window_h for g in rows}, reverse=True):
            wrows = [g for g in rows if g.window_h == w]
            eb, mb = mean(g.ens_brier for g in wrows), mean(g.mkt_brier for g in wrows)
            print(
                f"  h{int(w):<3}   {len(wrows):4d}   {_fmt(eb)}    {_fmt(mb)}   {_fmt(eb - mb)}"
                f"   {_fmt(mean(g.ens_ll for g in wrows))} {_fmt(mean(g.mkt_ll for g in wrows))}"
                f"  {sum(g.ens_hit for g in wrows):3d}/{len(wrows):<3d}"
                f"  {sum(g.mkt_hit for g in wrows):3d}/{len(wrows):<3d}"
            )

    diffs = [g.mkt_brier - g.ens_brier for g in graded]  # positive => ensemble better
    line = f"  mean diff (mkt-ens): {mean(diffs):+.4f}"
    if len(diffs) >= 2:
        line += f" ± {stdev(diffs) / math.sqrt(len(diffs)):.4f} (stderr)"
    wins = sum(1 for d in diffs if d > 0)
    print("\n  --- Paired comparison (per event-window) ---")
    print(line)
    print(f"  ensemble beats market in {wins}/{len(diffs)} windows")

    models = sorted({name for g in graded for name in g.per_model_brier})
    if models:
        print("\n  --- Per-model Brier (pooled) ---")
        for name in models:
            vals = [g.per_model_brier[name] for g in graded if name in g.per_model_brier]
            print(f"  {name:16s} {_fmt(mean(vals))}  (n={len(vals)})")
        print(f"  {'blend':16s} {_fmt(mean(g.ens_brier for g in graded))}  (n={n})")

    all_trades = [(g, t) for g in graded for t in g.trades]
    print(f"\n  --- Hypothetical EV (qty=1, fees on, edge >= {args.min_edge_cents}c) ---")
    if not all_trades:
        print("  (no bucket cleared the edge threshold)")
    else:
        for w in sorted({g.window_h for g, _ in all_trades}, reverse=True):
            for side in ("yes", "no"):
                ts = [t for g, t in all_trades if g.window_h == w and t[0] == side]
                if not ts:
                    continue
                pnl = sum(t[3] for t in ts)
                print(
                    f"  h{int(w):<3} {side:3s}  trades={len(ts):3d}  avg_edge={mean(t[2] for t in ts):5.1f}c"
                    f"  pnl={pnl / 100.0:+8.2f}$  per-trade={pnl / len(ts):+5.1f}c"
                )
        total = sum(t[3] for _, t in all_trades)
        print(f"  TOTAL trades={len(all_trades)}  pnl={total / 100.0:+.2f}$")


def report_live(live_rows: list[dict], ensembles, args, now: datetime) -> None:
    print(f"\n=== Live model disagreements (open events, edge >= {args.min_edge_cents}c) ===")
    by_event: dict[str, list[dict]] = {}
    for r in live_rows:
        by_event.setdefault(r["event_ticker"], []).append(r)
    if not by_event:
        print("  (no recent ladder snapshots)")
        return
    found = 0
    for ev in sorted(by_event):
        rows = by_event[ev]
        cycle = cluster_cycles(rows)[-1]
        city = rows[-1]["city"] or "?"
        kind = rows[-1]["kind"] or "high"
        target = event_target_date(ev) or "?"
        members = pick_ensembles_at(ensembles.get((city, target, kind)) or [], now)
        if not members:
            continue
        ranges = [(b.low_f, b.high_f) for b in cycle.buckets]
        probs = model_bucket_probs(members, ranges, args.sigma)
        if probs is None:
            continue
        edges = ev_trades(probs, cycle.buckets, None, args.min_edge_cents)
        model_pick = cycle.buckets[max(range(len(probs)), key=probs.__getitem__)].subtitle
        mids = normalize_mids([b.mid for b in cycle.buckets])
        mkt_pick = (
            cycle.buckets[max(range(len(mids)), key=mids.__getitem__)].subtitle if mids else "?"
        )
        htc = f"{cycle.hours_to_close:.1f}h" if cycle.hours_to_close is not None else "?"
        flag = "  <-- disagree" if model_pick != mkt_pick else ""
        print(f"  {ev}  ({city} {kind}, {htc} to close)")
        print(f"    model pick: '{model_pick}'  market pick: '{mkt_pick}'{flag}")
        for side, sub, edge, _ in sorted(edges, key=lambda t: -t[2])[:4]:
            print(f"    {side.upper():3s} '{sub}'  model edge {edge:+5.1f}c after fees")
            found += 1
    if not found:
        print("  (model and market currently agree everywhere within the threshold)")


def report_readiness(ready: Readiness, sens, args) -> None:
    print("\n=== Data readiness ===")
    print(f"  settled events: {ready.settled}   with ladder: {ready.with_ladder}"
          f"   with ensemble: {ready.with_ensemble}   gradeable: {ready.gradeable}")
    for reason, count in sorted(ready.skipped.items()):
        print(f"    skipped — {reason}: {count}")
    print("  (events settled before collection began can never be graded; coverage only grows)")
    if sens:
        print("\n=== Kernel sigma sensitivity (pooled) ===")
        print("  sigma   n    ens_brier  mkt_brier  hypothetical_pnl")
        for sg, n, eb, mb, pnl in sens:
            pnl_s = "n/a" if math.isnan(pnl) else f"{pnl / 100.0:+8.2f}$"
            print(f"  {sg:5.2f} {n:4d}   {_fmt(eb)}    {_fmt(mb)}   {pnl_s}")


# --- data access -----------------------------------------------------------------------


def fetch_all(conn, windows: list[float]) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_ticker, city, target_date, kind, winning_ticker, winning_subtitle,"
            " actual_low_f, actual_high_f FROM weather_settlements ORDER BY target_date, event_ticker"
        )
        cols = [d.name for d in cur.description]
        settlements = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        tickers = [s["event_ticker"] for s in settlements]
        ladders: dict[str, list[dict]] = {}
        if tickers:
            cur.execute(
                "SELECT event_ticker, market_ticker, city, kind, subtitle, low_f, high_f,"
                " yes_bid_cents, yes_ask_cents, mid_cents, hours_to_close, captured_at"
                " FROM weather_bucket_snapshots WHERE event_ticker = ANY(%s) ORDER BY captured_at",
                (tickers,),
            )
            cols = [d.name for d in cur.description]
            for r in cur.fetchall():
                row = dict(zip(cols, r, strict=True))
                ladders.setdefault(row["event_ticker"], []).append(row)

        cur.execute(
            "SELECT event_ticker, market_ticker, city, kind, subtitle, low_f, high_f,"
            " yes_bid_cents, yes_ask_cents, mid_cents, hours_to_close, captured_at"
            " FROM weather_bucket_snapshots WHERE captured_at >= %s ORDER BY captured_at",
            (datetime.now(timezone.utc) - timedelta(hours=3),),
        )
        cols = [d.name for d in cur.description]
        settled_set = set(tickers)
        live_rows = [
            row for r in cur.fetchall()
            if (row := dict(zip(cols, r, strict=True)))["event_ticker"] not in settled_set
        ]

        dates = {s["target_date"] or event_target_date(s["event_ticker"]) for s in settlements}
        dates |= {event_target_date(r["event_ticker"]) for r in live_rows}
        dates.discard(None)
        ensembles: dict[tuple[str, str, str], list[dict]] = {}
        if dates:
            cur.execute(
                "SELECT city, target_date, kind, model, members_json, captured_at"
                " FROM weather_ensembles WHERE target_date = ANY(%s)",
                (sorted(dates),),
            )
            cols = [d.name for d in cur.description]
            for r in cur.fetchall():
                row = dict(zip(cols, r, strict=True))
                key = (row["city"], row["target_date"], row["kind"])
                ensembles.setdefault(key, []).append(row)

    return {"settlements": settlements, "ladders": ladders, "live": live_rows, "ensembles": ensembles}


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sigma", type=float, default=1.5,
                    help="kernel width (degF) around each ensemble member; 0 = raw counts")
    ap.add_argument("--min-edge-cents", type=float, default=5.0,
                    help="minimum model-vs-price edge (after fees) to count a hypothetical trade")
    ap.add_argument("--windows", default="20,14,8",
                    help="hours-to-close entry windows to grade (match WEATHER_ENTRY_HOURS)")
    ap.add_argument("--sigma-grid", default="0,1,1.5,2,3",
                    help="sigmas for the sensitivity table ('' to skip)")
    ap.add_argument("--no-live", action="store_true", help="skip the live-edges section")
    args = ap.parse_args(argv)
    windows = [float(w) for w in args.windows.split(",") if w.strip()]

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg  # deferred so the pure-math helpers import without the driver

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        data = fetch_all(conn, windows)

    graded, ready = grade_settled(
        data["settlements"], data["ladders"], data["ensembles"],
        windows, args.sigma, args.min_edge_cents,
    )
    report_graded(graded, ready, args)
    if not args.no_live:
        report_live(data["live"], data["ensembles"], args, now=datetime.now(timezone.utc))
    sigmas = [float(s) for s in args.sigma_grid.split(",") if s.strip()]
    sens = (
        sigma_sensitivity(
            data["settlements"], data["ladders"], data["ensembles"],
            windows, sigmas, args.min_edge_cents,
        )
        if sigmas and ready.gradeable
        else []
    )
    report_readiness(ready, sens, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
