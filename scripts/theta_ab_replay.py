"""Replay the Stage-4 selection-rule A/B over ONE common candidate stream.

`docs/RESEARCH_THETA_REMEDIATION.md` §4.2.3 derived its evidence floors from the *spliced model's
historical selected set* — the markets an excess-ranked rule happened to pick. That set does not
measure the proposed treatment arm, which ranks on `mid` ascending behind a model veto and
therefore draws from a different pool at a different cadence. Floors derived from one rule cannot
size an experiment that runs another.

This script fixes that by replaying **both proposed rules, as specified, over the same eligible
candidate stream**, on Kalshi's own settled results, and deriving the floors from what it
measures rather than from a superseded run.

What it does NOT do: choose a model, sweep a configuration, or promote anything. It runs the
frozen spliced configuration only (`tailmodel.FROZEN_FIT_DAYS` / `FROZEN_TAIL_Q`) because the
model question is settled and re-opening it here would cost an hour and answer nothing.

    {"type":"script","name":"theta_ab_replay","id":"ab-1"}
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cluster_stats as cs  # noqa: E402
import theta_tail_refit as ref  # noqa: E402

from kalshi_bot.theta import tailmodel as tm  # noqa: E402

# --- the rules, exactly as §4.2 specifies them ------------------------------------------------

BAND = (3.0, 20.0)          # yes mid, cents — eligibility, identical in both arms
MIN_VOLUME = 100.0
TTE = (10.0, 35.0)          # minutes to close
CONTROL_EDGE = 6.0          # cents; today's rule keeps its threshold, not just its ranking
TREATMENT_VETO_P = 0.10     # the model may REFUSE a candidate; it may never PROMOTE one
PER_EVENT_CAP = 3

# --- the power calculation --------------------------------------------------------------------

MIN_USEFUL_EFFECT = math.log(2.0)   # a halving of the tail miss: |log 0.5|
Z_ALPHA = 2.5758293035489004        # two-sided 99%
Z_POWER = 0.8416212335729143        # 80%
HORIZON_MULTIPLE = 1.5              # max evidence horizon over the promotion floor (#247)
EARLY_FAILURE_FLOOR = 300           # settled markets per arm; a stopping clause, not inflated


def tie_key(ticker: str) -> str:
    """The deterministic ticker hash `abarm` already uses (docs/MMSELL_OFFSET_AB.md), so equal
    scores break identically in both arms and a replay reproduces itself."""
    return hashlib.sha256(ticker.encode()).hexdigest()


def eligible(r: dict) -> bool:
    """The candidate stream. Identical in both arms — eligibility is NOT the treatment."""
    return bool(
        BAND[0] <= r["mid"] <= BAND[1]
        and (r["volume"] or 0) >= MIN_VOLUME
        and TTE[0] <= r["mtc"] <= TTE[1]
        and r.get("yes_bid") is not None and r.get("yes_ask") is not None
    )


def take(rows: list[dict], score, keep, cap: int = PER_EVENT_CAP) -> list[dict]:
    """Apply one arm's rule to the shared stream: filter, rank, cap per event."""
    by_event: dict[str, list[dict]] = {}
    for r in rows:
        if keep(r):
            by_event.setdefault(r["event"], []).append(r)
    out: list[dict] = []
    for _ev, cands in by_event.items():
        cands.sort(key=lambda r: (score(r), tie_key(r["ticker"])))
        out.extend(cands[:cap])
    return out


# --- reporting ---------------------------------------------------------------------------------

def arm_stats(name: str, sel: list[dict], p_key: str, days: float, seed: int) -> dict:
    prof = cs.cluster_profile(sel, "event")
    exp = sum(r[p_key] for r in sel)
    obs = sum(1 for r in sel if r["yes_resolved"])
    n = len(sel)
    ev = prof.get("clusters", 0)
    deff = cs.design_effect(sel, "event", lambda r, k=p_key: float(r["yes_resolved"]),
                            seed=seed) if n else {"deff": float("nan")}
    return {
        "name": name, "n": n, "events": ev,
        "markets_per_event": (n / ev) if ev else float("nan"),
        "expected": exp, "observed": obs,
        "r": (obs / exp) if exp > 0 else float("nan"),
        "lam_market": (exp / n) if n else float("nan"),
        "lam_event": (exp / ev) if ev else float("nan"),
        "per_day": (n / days) if days > 0 else float("nan"),
        "deff": deff["deff"],
        "kish": prof.get("kish_effective_clusters", float("nan")),
        "max_cluster": prof.get("max_size", 0),
    }


def print_arm_table(arms: list[dict]) -> None:
    print(f"  {'arm':<12} {'n':>7} {'events':>7} {'mkt/ev':>7} {'expected':>9} {'observed':>9} "
          f"{'R':>6} {'exp/mkt':>8} {'exp/event':>10} {'cand/day':>9} {'deff':>6}")
    for a in arms:
        print(f"  {a['name']:<12} {a['n']:>7,} {a['events']:>7,} {a['markets_per_event']:>7.2f} "
              f"{a['expected']:>9.2f} {a['observed']:>9,} {a['r']:>6.2f} "
              f"{a['lam_market']:>8.4f} {a['lam_event']:>10.4f} {a['per_day']:>9.2f} "
              f"{a['deff']:>6.2f}")


def requirement(ctrl: dict, trt: dict) -> dict:
    """Sample needed per arm to resolve `log(R_T / R_C)` at a halving, 80% power, two-sided 99%.

    Var(log R) ~= 1/observed for a Poisson count, so Var of the log ratio is 1/obs_T + 1/obs_C.
    Under the alternative the treatment's miss is HALF the control's, so its observed count is
    half of what its own exposure would otherwise produce. Each arm carries its OWN expected-loss
    rate, because the two rules draw from different pools — that is exactly the error §4.2.3 made
    by sizing both arms off one historical selected set.
    """
    se = MIN_USEFUL_EFFECT / (Z_ALPHA + Z_POWER)
    lam_c, lam_t = ctrl["lam_market"], trt["lam_market"]
    r_c = ctrl["r"] if ctrl["r"] == ctrl["r"] and ctrl["r"] > 0 else float("nan")
    r_t = 0.5 * r_c
    if not (lam_c > 0 and lam_t > 0 and r_c > 0):
        return {"ok": False}
    iid = (1.0 / (lam_t * r_t) + 1.0 / (lam_c * r_c)) / (se * se)
    deff = max(d for d in (ctrl["deff"], trt["deff"]) if d == d)
    floor = iid * deff
    return {
        "ok": True, "se": se, "iid": iid, "deff": deff, "floor": floor,
        "horizon": floor * HORIZON_MULTIPLE,
        "obs_c": iid * lam_c * r_c, "obs_t": iid * lam_t * r_t,
        "days_c": floor / ctrl["per_day"] if ctrl["per_day"] > 0 else float("nan"),
        "days_t": floor / trt["per_day"] if trt["per_day"] > 0 else float("nan"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-07-11")
    ap.add_argument("--seed", type=int, default=cs.DEFAULT_SEED)
    ap.add_argument("--vol-mult", type=float, default=2.0,
                    help="incumbent vol_mult for the sensitivity arm; theta4 runs 2.0")
    args = ap.parse_args(argv)

    print("command: theta_ab_replay " + " ".join(argv if argv is not None else sys.argv[1:]))
    print("commit:  " + (os.environ.get("GITHUB_SHA") or "unknown (not run from CI)"))
    print(f"seed:    {args.seed}")
    print(f"frozen model: fit_days={tm.FROZEN_FIT_DAYS:.0f} tail_q={tm.FROZEN_TAIL_Q:.2f}")
    print()

    url = ref._to_libpq_url(os.environ.get("DATABASE_URL_RO")
                            or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    spot_since = (dt.datetime.fromisoformat(args.since).replace(tzinfo=dt.timezone.utc)
                  - dt.timedelta(days=tm.FROZEN_FIT_DAYS + 1)).date().isoformat()
    with psycopg.connect(url, options=ref.RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            quotes = ref.load_quotes(cur, args.since, TTE[1])
            book = load_book(cur, args.since)
    print(f"fetching 1-minute closes from Coinbase since {spot_since}...")
    spot = ref.load_spot_coinbase(spot_since)

    for r in quotes:
        b = book.get(r["ticker"])
        r["yes_bid"], r["yes_ask"] = (b if b else (None, None))

    quotes, labels_ok = ref.real_labels(quotes)
    if not quotes or not labels_ok:
        print("labels did not clear their bar — nothing below can be scored", file=sys.stderr)
        return 1

    cache = ref.FitCache(spot, tm.FROZEN_TAIL_Q, tm.FROZEN_FIT_DAYS)
    scored = [r for r in ref.score(quotes, cache, args.vol_mult) if r["powered"]]

    ref.head("1. THE COMMON CANDIDATE STREAM")
    print(f"  Eligibility, identical in both arms: mid in {BAND[0]:.0f}..{BAND[1]:.0f}c, "
          f"volume >= {MIN_VOLUME:.0f}, minutes_to_close in {TTE[0]:.0f}..{TTE[1]:.0f}, "
          "two-sided book.")
    stream = [r for r in scored if eligible(r)]
    prof = cs.cluster_profile(stream, "event")
    dates = sorted({r["captured_at"].date() for r in stream})
    days = (dates[-1] - dates[0]).days + 1 if dates else 0
    print(f"  scored quotes (powered): {len(scored):,}")
    print(f"  ELIGIBLE CANDIDATES:     {len(stream):,} across {prof.get('clusters', 0):,} "
          f"distinct events")
    print(f"  markets per event:       {prof.get('mean_size', float('nan')):.2f} "
          f"(max {prof.get('max_size', 0)}, Kish {prof.get('kish_effective_clusters', 0):.1f})")
    print(f"  span:                    {dates[0]} .. {dates[-1]}  ({days} days, "
          f"{len(dates)} with data)")
    print(f"  candidate cadence:       {len(stream) / days:.1f} eligible candidates/day"
          if days else "")
    print()
    print("  Both arms are scored under the SAME probability model — the frozen spliced fit.")
    print("  The A/B isolates the SELECTION RULE, so letting the arms differ in their model too")
    print("  would confound the two changes and answer neither question.")

    ref.head("2. THE TWO ARMS, REPLAYED OVER THAT STREAM")
    print(f"  CONTROL   : excess = mid - 100*P >= {CONTROL_EDGE:.0f}c, ranked by excess "
          f"DESCENDING, cap {PER_EVENT_CAP}/event.")
    print(f"  TREATMENT : P <= {TREATMENT_VETO_P:.2f} (veto only), ranked by mid ASCENDING, "
          f"cap {PER_EVENT_CAP}/event.")
    print("  Ties broken by the deterministic ticker hash, identically in both arms.")
    print()

    ctrl = take(stream, lambda r: -(r["mid"] - 100.0 * r["p_new"]),
                lambda r: (r["mid"] - 100.0 * r["p_new"]) >= CONTROL_EDGE)
    trt = take(stream, lambda r: r["mid"], lambda r: r["p_new"] <= TREATMENT_VETO_P)

    a_ctrl = arm_stats("CONTROL", ctrl, "p_new", days, args.seed)
    a_trt = arm_stats("TREATMENT", trt, "p_new", days, args.seed)
    print_arm_table([a_ctrl, a_trt])

    c_t, t_t = {r["ticker"] for r in ctrl}, {r["ticker"] for r in trt}
    both = c_t & t_t
    union = c_t | t_t
    print()
    print(f"  OVERLAP: {len(both):,} markets are taken by BOTH arms "
          f"({(len(both) / len(union) * 100 if union else 0):.1f}% of the union; "
          f"{(len(both) / len(c_t) * 100 if c_t else 0):.1f}% of control, "
          f"{(len(both) / len(t_t) * 100 if t_t else 0):.1f}% of treatment)")
    print("  Overlap is not contamination — a market both rules pick is a market the treatment")
    print("  does not change. It IS the reason the arms are not independent samples, and it is")
    print("  why the primary estimand is bootstrapped over both arms together.")

    ref.head("3. SENSITIVITY — the control arm under the model it runs TODAY")
    print(f"  theta4 prices off the incumbent at vol_mult={args.vol_mult:.1f}. Section 2's")
    print("  control uses the spliced model so the arms differ only in RULE. This is the same")
    print("  rule under today's model, reported so the substitution is visible, not hidden.")
    print()
    ctrl_old = take(stream, lambda r: -(r["mid"] - 100.0 * r["p_old"]),
                    lambda r: (r["mid"] - 100.0 * r["p_old"]) >= CONTROL_EDGE)
    print_arm_table([arm_stats("CONTROL-inc", ctrl_old, "p_old", days, args.seed)])

    ref.head("4. EVIDENCE REQUIREMENT, DERIVED FROM THE REPLAY")
    req = requirement(a_ctrl, a_trt)
    if not req["ok"]:
        print("  An arm carries no expected loss on this stream. No requirement can be derived,")
        print("  and the A/B is NOT ready to register.")
        return 0
    print(f"  minimum useful effect          |log 0.5| = {MIN_USEFUL_EFFECT:.3f} (a halving)")
    print(f"  two-sided 99%, 80% power       z = {Z_ALPHA:.3f} + {Z_POWER:.3f}")
    print(f"  required SE of log(R_T/R_C)    {req['se']:.4f}")
    print(f"  observed losses needed         control {req['obs_c']:.0f}, "
          f"treatment {req['obs_t']:.0f}")
    print(f"  iid requirement per arm        {req['iid']:,.0f} settled markets")
    print(f"  design effect (max of arms)    {req['deff']:.2f}")
    print(f"  PROMOTION EVIDENCE FLOOR       {req['floor']:,.0f} settled markets per arm")
    print(f"  MAXIMUM EVIDENCE HORIZON       {req['horizon']:,.0f} settled markets per arm "
          f"({HORIZON_MULTIPLE:.1f}x the floor)")
    print(f"  early-failure floor            {EARLY_FAILURE_FLOOR} settled markets per arm "
          "(unchanged; a stopping clause is deliberately not inflated)")
    print()
    print(f"  CALENDAR TIME at the measured cadence: control {req['days_c']:,.0f} days "
          f"({req['days_c'] / 365.25:.1f} years), treatment {req['days_t']:,.0f} days "
          f"({req['days_t'] / 365.25:.1f} years)")
    print("  The binding arm is the SLOWER one — both must reach the floor for the comparison")
    print("  to be powered.")
    slow = max(req["days_c"], req["days_t"])
    print()
    print(f"  VERDICT: the slower arm needs {slow:,.0f} days ({slow / 365.25:.1f} years) to "
          "reach the")
    print("  promotion floor at the cadence this replay measured.")
    if slow > 365:
        print("  That is longer than the evidence is worth waiting for. The A/B is PROPOSED but")
        print("  NOT READY TO REGISTER at this cadence: either the candidate stream has to widen")
        print("  or the minimum useful effect has to be larger, and BOTH are design changes that")
        print("  belong in a new pre-registration rather than a footnote on this one.")
    return 0


def load_book(cur, since: str) -> dict[str, tuple[float | None, float | None]]:
    """The two-sided-book check needs the bid/ask at the SAME decision row `load_quotes` picks,
    so it repeats that row's selection rather than taking any quote for the market."""
    cur.execute(
        "SELECT DISTINCT ON (market_ticker) market_ticker, yes_bid_cents, yes_ask_cents"
        "  FROM crypto_ladder_snapshots"
        " WHERE captured_at >= %s AND spot IS NOT NULL AND minutes_to_close IS NOT NULL"
        "   AND minutes_to_close <= %s AND minutes_to_close >= 10"
        "   AND mid_cents IS NOT NULL AND mid_cents <= 40"
        " ORDER BY market_ticker, minutes_to_close DESC",
        (since, TTE[1]))
    return {t: (b, a) for t, b, a in cur.fetchall()}


if __name__ == "__main__":
    raise SystemExit(main())
