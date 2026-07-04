"""XGAME thesis validation probe (docs/IDEA_MODEL_20260704.md) — in-play PM->Kalshi
lead-lag on scoring shocks, measured on the COLLECTED cross-venue tapes.

Reads game_market_matches / game_tape_snapshots (populated by the ride-along xgame
collector), builds ~10-second bars per venue from the trades' own timestamps
(normalized to P(matched team) in cents), aligns each match's two series, and grades
the pre-registered predictions on every detected shock:

  P1  Directional lead exists: PM->Kalshi follow% > 55% AND same-bar co-move% < 40%.
      KILL if follow% <= 50% or same-bar >= 60% (simultaneous, no lag).
  P2  The lag clears cost: median Kalshi follow-through from the first post-shock bar,
      net of a round-trip worst-case taker fee at the traded band, >= 4c.
      KILL if < 2c (same verdict as averaged cross-venue divergence).
  P3  Asymmetry: PM->Kalshi follow% exceeds Kalshi->PM follow% by >= 10 pts
      (else both venues react to a shared third feed — no venue edge).
  P4  Actable at latency: median exploitable window (PM shock -> Kalshi has repriced
      >= 80% of the shock) >= 20s. Note-only if 10-20s (needs colocation).
  Decision: paper book `xgame` only if P1 AND P2 AND P3. P2 fail -> shelve. P1 fail ->
  the in-play frontier is closed and the lead-lag family fully ruled out.

No-lookahead: shocks are detected on bars <= t; entry is the Kalshi bar AT t (the
first quote you could see after the PM print); follow-through is measured strictly
after t. Self-contained (stdlib + psycopg). Usage:
    DATABASE_URL_RO=postgresql://... python scripts/xgame_tape_study.py
    # ops channel:
    {"type":"script","name":"xgame_tape_study","args":["--bar-s","10","--shock-c","3"]}
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from statistics import median

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

SIZE_BANDS = ((3.0, 5.0), (5.0, 10.0), (10.0, 1e9))
MIN_SHOCKS = 30  # banner threshold


def taker_fee_c(p_c: float) -> float:
    return math.ceil(7.0 * (p_c / 100.0) * (1 - p_c / 100.0))


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


# --- bars ---------------------------------------------------------------------------


def build_bars(trades: list[tuple[float, float]], bar_s: int) -> dict[int, float]:
    """{bar_index: last team_prob_cents in bar} from (unix_ts, prob_cents) rows."""
    out: dict[int, float] = {}
    for ts, p in trades:  # rows arrive time-ordered; last write wins per bar
        out[int(ts) // bar_s] = p
    return out


def align_bars(a: dict[int, float], b: dict[int, float]) -> tuple[list[float], list[float]]:
    """Forward-fill both onto the union bar grid over their overlapping span."""
    if not a or not b:
        return [], []
    lo = max(min(a), min(b))
    hi = min(max(a), max(b))
    if hi <= lo:
        return [], []
    xs, ys = [], []
    la = lb = None
    for t in range(lo, hi + 1):
        la = a.get(t, la)
        lb = b.get(t, lb)
        if la is not None and lb is not None:
            xs.append(la)
            ys.append(lb)
    return xs, ys


# --- shock records --------------------------------------------------------------------


def shock_records(leader: list[float], follower: list[float], shock_c: float,
                  horizon_bars: int, window_cap_bars: int, bar_s: int) -> list[dict]:
    """Every |1-bar leader jump| >= shock_c -> one record measuring the follower:
    same-bar co-move, follow-through over the horizon (gross + net of a round-trip
    worst-case taker fee at the entry band), and the exploitable window (seconds until
    the follower has repriced >= 80% of the shock; censored at the cap)."""
    recs: list[dict] = []
    n = len(leader)
    for t in range(1, n - horizon_bars):
        da = leader[t] - leader[t - 1]
        if abs(da) < shock_c:
            continue
        d = 1.0 if da > 0 else -1.0
        entry = follower[t]
        follow = (follower[t + horizon_bars] - entry) * d
        fee = 2.0 * taker_fee_c(min(99.0, max(1.0, entry)))
        window_s = None
        target = 0.8 * abs(da)
        for u in range(t + 1, min(n, t + window_cap_bars + 1)):
            if (follower[u] - entry) * d >= target:
                window_s = (u - t) * bar_s
                break
        recs.append({
            "size": abs(da),
            "same_bar": (follower[t] - follower[t - 1]) * d > 0,
            "follow": follow,
            "follow_net": follow - fee,
            "window_s": window_s,          # None = never converged within the cap
            "band": entry,
        })
    return recs


def _pct(part: int, whole: int) -> float:
    return 100.0 * part / whole if whole else float("nan")


def summarize(recs: list[dict], window_cap_s: int) -> dict:
    n = len(recs)
    if not n:
        return {"n": 0}
    follows = [r["follow"] for r in recs]
    nets = [r["follow_net"] for r in recs]
    windows = [r["window_s"] if r["window_s"] is not None else window_cap_s for r in recs]
    return {
        "n": n,
        "same_bar_pct": _pct(sum(1 for r in recs if r["same_bar"]), n),
        "follow_pct": _pct(sum(1 for r in recs if r["follow"] > 0), n),
        "med_follow": median(follows),
        "med_net": median(nets),
        "med_window_s": median(windows),
        "censored": sum(1 for r in recs if r["window_s"] is None),
    }


def _row(label: str, s: dict) -> str:
    if not s.get("n"):
        return f"    {label:>18}  (no shocks)"
    return (f"    {label:>18} {s['n']:5d} {s['same_bar_pct']:5.0f}% {s['follow_pct']:5.0f}%"
            f" {s['med_follow']:+7.2f}c {s['med_net']:+7.2f}c {s['med_window_s']:6.0f}s"
            f" ({s['censored']} uncvg)")


# --- main --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bar-s", type=int, default=10, help="bar size (seconds)")
    ap.add_argument("--shock-c", type=float, default=3.0, help="1-bar jump threshold (cents)")
    ap.add_argument("--horizon-s", type=int, default=90, help="follow-through window (seconds)")
    ap.add_argument("--window-cap-s", type=int, default=600,
                    help="exploitable-window search cap (seconds)")
    ap.add_argument("--min-bars", type=int, default=60,
                    help="skip matches with fewer aligned bars")
    args = ap.parse_args(argv)
    horizon_bars = max(1, args.horizon_s // args.bar_s)
    window_cap_bars = max(1, args.window_cap_s // args.bar_s)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1
    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, day, team, kalshi_ticker, status,"
                " kalshi_trades, pm_trades FROM game_market_matches ORDER BY id"
            )
            matches = cur.fetchall()
        tapes: dict[int, dict[str, list[tuple[float, float]]]] = {}
        with conn.cursor() as cur:
            cur.execute(
                "SELECT match_id, venue, EXTRACT(EPOCH FROM traded_at), team_prob_cents"
                " FROM game_tape_snapshots WHERE team_prob_cents IS NOT NULL"
                " ORDER BY traded_at ASC"
            )
            for match_id, venue, ts, prob in cur.fetchall():
                tapes.setdefault(match_id, {}).setdefault(venue, []).append(
                    (float(ts), float(prob))
                )

    print(f"=== XGAME tape study ({args.bar_s}s bars, shock>={args.shock_c:g}c, "
          f"follow={args.horizon_s}s) ===")
    print(f"  matched pairs: {len(matches)}")
    if not matches:
        print("  No matches collected yet — the xgame collector populates these tables "
              "once deployed; check the 'xgame collector' worker log lines.")
        return 0

    pm_kal: list[dict] = []
    kal_pm: list[dict] = []
    print(f"\n  {'day':>11} {'team':>14} {'kal_rows':>8} {'pm_rows':>8} "
          f"{'bars':>6} {'shkPM':>5} {'shkK':>5}")
    for mid, day, team, _ticker, _status, _kn, _pn in matches:
        tape = tapes.get(mid) or {}
        k_rows = tape.get("kalshi") or []
        p_rows = tape.get("polymarket") or []
        kal, pm = align_bars(build_bars(k_rows, args.bar_s), build_bars(p_rows, args.bar_s))
        if len(kal) < args.min_bars:
            print(f"  {day or '?':>11} {team[:14]:>14} {len(k_rows):8d} {len(p_rows):8d} "
                  f"{len(kal):6d}   (below --min-bars)")
            continue
        a = shock_records(pm, kal, args.shock_c, horizon_bars, window_cap_bars, args.bar_s)
        b = shock_records(kal, pm, args.shock_c, horizon_bars, window_cap_bars, args.bar_s)
        pm_kal.extend(a)
        kal_pm.extend(b)
        print(f"  {day or '?':>11} {team[:14]:>14} {len(k_rows):8d} {len(p_rows):8d} "
              f"{len(kal):6d} {len(a):5d} {len(b):5d}")

    print(f"\n  {'direction':>18} {'n':>5} {'same':>5} {'foll%':>5} {'med_fw':>8} "
          f"{'med_net':>8} {'window':>7}")
    s_pk = summarize(pm_kal, args.window_cap_s)
    s_kp = summarize(kal_pm, args.window_cap_s)
    print(_row("PM->Kalshi", s_pk))
    print(_row("Kalshi->PM", s_kp))
    for lo, hi in SIZE_BANDS:
        sub = [r for r in pm_kal if lo <= r["size"] < hi]
        label = f"PM->K shock {lo:.0f}-{hi:.0f}c" if hi < 1e9 else f"PM->K shock {lo:.0f}c+"
        print(_row(label, summarize(sub, args.window_cap_s)))

    print("\n  === PRE-REGISTERED VERDICTS (docs/IDEA_MODEL_20260704.md — XGAME) ===")
    if s_pk.get("n", 0) < MIN_SHOCKS:
        print(f"  *** SAMPLE TOO SMALL (PM->Kalshi shocks={s_pk.get('n', 0)} < {MIN_SHOCKS}) "
              "— verdicts are PROVISIONAL; keep collecting games. ***")
    if s_pk.get("n"):
        f, sb = s_pk["follow_pct"], s_pk["same_bar_pct"]
        v = ("PASS" if f > 55.0 and sb < 40.0 else
             "KILL" if f <= 50.0 or sb >= 60.0 else "GREY")
        print(f"  P1 follow%>55 AND same-bar<40: follow={f:.0f}% same-bar={sb:.0f}% -> {v}")
        net = s_pk["med_net"]
        v = "PASS" if net >= 4.0 else ("KILL" if net < 2.0 else "GREY (2-4c)")
        print(f"  P2 median net follow-through >= 4c: {net:+.2f}c "
              f"(gross {s_pk['med_follow']:+.2f}c) -> {v}")
        if s_kp.get("n"):
            gap = s_pk["follow_pct"] - s_kp["follow_pct"]
            v = "PASS" if gap >= 10.0 else "FAIL (symmetric — shared third feed)"
            print(f"  P3 PM->K exceeds K->PM by >= 10 pts: "
                  f"{s_pk['follow_pct']:.0f}% vs {s_kp['follow_pct']:.0f}% "
                  f"(gap {gap:+.0f}) -> {v}")
        w = s_pk["med_window_s"]
        v = ("PASS" if w >= 20.0 else
             "NOTE-ONLY (10-20s: needs colocation)" if w >= 10.0 else "FAIL")
        print(f"  P4 median exploitable window >= 20s: {w:.0f}s "
              f"({s_pk['censored']} never converged within {args.window_cap_s}s) -> {v}")
    else:
        print("  No PM->Kalshi shocks measured yet.")
    print("  Decision rule: paper book `xgame` only if P1 AND P2 AND P3; P2 fail -> "
          "shelve; P1 fail -> lead-lag family fully ruled out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
