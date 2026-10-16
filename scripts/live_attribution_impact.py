"""What activating `METRICS_ENGINE:live_fill_attribution_2026_10` would do to every live gate.

WS-027 acceptance check 5: "the platform revision's impact on every live gate is listed before
activation, and no verdict moves silently". Read-only. For every eligible gate of every
experiment with a LIVE deployment, it evaluates the gate TWICE as a dry run — once with the
shipped per-position provider (what production records today) and once with fill attribution
forced on in THIS process only — and prints the verdict and every clause value that differs.
Nothing is persisted; the database is opened through DATABASE_URL_RO when set; the active
revision set is not touched (the switch is a process-local override of
`live_attribution.fill_attribution_active`).

Then it reconciles every settled market a live tag traded in the window: the per-tag
fill-attributed P&L against the account's `positions.realized_pnl`, so the mismatch events
the revision would start recording are known before it does.

    {"type": "script", "name": "live_attribution_impact", "id": "..."}
    {"type": "script", "name": "live_attribution_impact", "args": ["--days", "30"]}
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@contextmanager
def _forced(active: bool):
    from kalshi_bot.experiment_os import live_attribution as la

    real = la.fill_attribution_active
    la.fill_attribution_active = lambda session: active  # type: ignore[assignment]
    try:
        yield
    finally:
        la.fill_attribution_active = real  # type: ignore[assignment]


def _clauses(outcome) -> dict:
    out = {}
    for c in outcome.clauses:
        key = (c.list_name, (c.clause or {}).get("metric"), c.scope)
        out[key] = (c.value, c.n, c.passed)
    return out


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def gate_impact(session) -> list[dict]:
    from kalshi_bot.experiment_os import read
    from kalshi_bot.experiment_os.evaluator import evaluate_gate
    from kalshi_bot.experiment_os.gate_runner import eligible_gates

    rows = []
    for item in eligible_gates(session):
        exp, gate, epoch = item["experiment"], item["gate"], item["epoch"]
        if not any(d.kind == "live" for d in read.deployments_for(session, epoch)):
            continue
        try:
            with _forced(False):
                before = evaluate_gate(session, gate, epoch=epoch, persist=False)
            with _forced(True):
                after = evaluate_gate(session, gate, epoch=epoch, persist=False)
        except Exception as exc:  # noqa: BLE001 — one gate must not stop the report
            session.rollback()
            rows.append({"experiment": exp.key, "gate": gate.gate_key,
                         "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            continue
        b, a = _clauses(before), _clauses(after)
        moved = [
            {"clause": k, "before": b.get(k), "after": a.get(k)}
            for k in sorted(set(b) | set(a), key=str) if b.get(k) != a.get(k)
        ]
        rows.append({"experiment": exp.key, "state": exp.state, "gate": gate.gate_key,
                     "before": before.verdict, "after": after.verdict,
                     "verdict_moves": before.verdict != after.verdict, "moved": moved})
    return rows


def reconciliation(session, days: int) -> dict:
    from sqlalchemy import select

    from kalshi_bot.experiment_os import live_attribution as la
    from kalshi_bot.models import LiveOrder

    since = datetime.now(timezone.utc) - timedelta(days=days)
    tickers = sorted(set(session.scalars(
        select(LiveOrder.market_ticker).where(LiveOrder.created_at >= since)
    ).all()))
    out = {"markets": 0, "ok": 0, "mismatch": [], "unchecked": 0, "shared": 0}
    for t in tickers:
        rec = la.reconcile_market(session, t)
        if rec.ok is None:
            out["unchecked"] += 1
            continue
        out["markets"] += 1
        if len([k for k in rec.per_tag_usd if k]) > 1:
            out["shared"] += 1
        if rec.ok:
            out["ok"] += 1
        else:
            out["mismatch"].append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=45,
                    help="reconciliation look-back over live orders (default 45)")
    args = ap.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from kalshi_bot.config import normalize_database_url
    from kalshi_bot.experiment_os import live_attribution as la

    url = normalize_database_url(os.environ.get("DATABASE_URL_RO")
                                 or os.environ.get("DATABASE_URL"))
    if not url:
        print("set DATABASE_URL_RO or DATABASE_URL", file=sys.stderr)
        return 2
    with Session(create_engine(url, future=True)) as session:
        print(f"# live attribution impact — {la.REVISION_REF}")
        print(f"# revision active now: {la.fill_attribution_active(session)}")
        print(f"# provider fingerprint (expect_metrics_fingerprint): {la.metrics_fingerprint()}")
        print()
        rows = gate_impact(session)
        moves = [r for r in rows if r.get("verdict_moves")]
        print(f"== LIVE GATES: {len(rows)} evaluated, {len(moves)} verdict(s) would move ==")
        for r in rows:
            if "error" in r:
                print(f"  {r['experiment']:36s} {r['gate']:24s} ERROR {r['error']}")
                continue
            flag = "  <-- VERDICT MOVES" if r["verdict_moves"] else ""
            print(f"  {r['experiment']:36s} {r['gate']:24s} {r['before']:>16s} -> "
                  f"{r['after']:<16s}{flag}")
            for m in r["moved"]:
                lst, metric, scope = m["clause"]
                bv = m["before"] or (None, None, None)
                av = m["after"] or (None, None, None)
                print(f"      {lst:8s} {metric:32s} {_fmt(bv[0]):>10s} (n={_fmt(bv[1])}, "
                      f"pass={_fmt(bv[2])}) -> {_fmt(av[0]):>10s} (n={_fmt(av[1])}, "
                      f"pass={_fmt(av[2])})  [{scope}]")
        print()
        rec = reconciliation(session, args.days)
        print(f"== RECONCILIATION (live orders, last {args.days}d) ==")
        print(f"  settled markets checked {rec['markets']}, reconcile {rec['ok']}, "
              f"shared by two tags {rec['shared']}, not checkable {rec['unchecked']}, "
              f"MISMATCH {len(rec['mismatch'])} (tolerance ${la.RECONCILE_TOLERANCE_USD})")
        for r in rec["mismatch"]:
            print(f"    {r.ticker}: per-tag {r.per_tag_usd} sum {r.attributed_usd} vs account "
                  f"{r.position_realized_usd} (diff {r.diff_usd}) {r.reason}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
