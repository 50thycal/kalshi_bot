"""Operational digest for the evolutionary agent system — the human dashboard
(docs/EVOLUTIONARY_AGENT_SYSTEM.md §35), pulled via the ops channel:

    {"type": "script", "name": "evo_digest"}
    {"type": "script", "name": "evo_digest", "args": ["--hours", "48"]}

Sections:
  COHORT      current cohort, time remaining, population counts
  HEALTH      heartbeat throughput, degraded/abandoned counts, LLM spend, data health
  LEADERBOARD delayed standings (the same 6h-delayed view agents see) + projected groups
  PORTFOLIOS  per-agent cohort NAV / realized / drawdown / open positions
  ACTIVITY    orders, fills, listener firings, revisions, influences in the window
  FAMILIES    family + strategy-family concentration
  TICKETS     the human review queue (supporters, urgency, age)
  ANOMALIES   stuck heartbeats, unfilled-order pileups, integrity events, stale data

Self-contained (stdlib + psycopg), read-only (SELECT role + read-only transaction).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _rows(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def _one(cur, sql: str, params: tuple = ()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0, help="activity window")
    args = ap.parse_args()

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO", ""))
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 2
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)
    print(f"EVO DIGEST @ {now.isoformat(timespec='seconds')} (window {args.hours:.0f}h)")
    print("=" * 78)

    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        # --- COHORT ---
        cohort = _rows(cur, """
            select id, number, starts_at, ends_at, status, wildcard_cohort
            from evo_cohorts order by number desc limit 1""")
        if not cohort:
            print("No cohorts yet — the evo worker has not bootstrapped.")
            return 0
        cid, number, starts, ends, status, wildcard = cohort[0]
        remaining_h = (ends - now).total_seconds() / 3600.0
        n_active = _one(cur, "select count(*) from evo_agents where status='active'")
        n_retired = _one(cur, "select count(*) from evo_agents where status='retired'")
        n_families = _one(cur, "select count(distinct surname) from evo_agents")
        print(f"\nCOHORT #{number} [{status}]  {starts:%Y-%m-%d} -> {ends:%Y-%m-%d}"
              f"  remaining {remaining_h:.1f}h  wildcard_next={wildcard}")
        print(f"  population: {n_active} active, {n_retired} retired, {n_families} families")

        # --- HEALTH ---
        hb = _rows(cur, """
            select status, count(*) from evo_heartbeats
            where created_at > now() - make_interval(hours => %s)
            group by status order by 2 desc""", (args.hours,))
        spend = _rows(cur, """
            select coalesce(sum(cost_usd),0), coalesce(sum(input_tokens+output_tokens),0)
            from evo_llm_usage
            where created_at > now() - make_interval(hours => %s)""", (args.hours,))
        cohort_spend = _one(cur, """
            select coalesce(sum(cost_usd),0) from evo_llm_usage where cohort_id=%s""",
            (cid,))
        print("\nHEALTH")
        print("  heartbeats: " + (", ".join(f"{s}={n}" for s, n in hb) or "none in window"))
        print(f"  llm spend: ${float(spend[0][0]):.4f} / {int(spend[0][1])} tokens in window;"
              f" ${float(cohort_spend or 0):.2f} this cohort")
        dh = _rows(cur, """
            select source_name, kind, count(*) from evo_data_health_events
            where resolved_at is null group by 1,2 order by 3 desc limit 5""")
        if dh:
            print("  data health (unresolved): "
                  + "; ".join(f"{s}:{k}x{n}" for s, k, n in dh))

        # --- LEADERBOARD (delayed view + projected groups) ---
        board = _rows(cur, """
            select f.agent_uuid, a.display_name, f.selection_score, f.cohort_score, f.rank
            from evo_fitness f
            join evo_agents a on a.agent_uuid = f.agent_uuid
            where f.cohort_id=%s and f.visible_after <= now()
              and f.computed_at = (select max(f2.computed_at) from evo_fitness f2
                                   where f2.cohort_id=f.cohort_id
                                     and f2.agent_uuid=f.agent_uuid
                                     and f2.visible_after <= now())
            order by f.selection_score desc""", (cid,))
        print(f"\nDELAYED LEADERBOARD ({len(board)} scored, 6h-delayed view)")
        n = len(board)
        n_bottom = round(n * 0.30)
        n_top = round(n * 0.30)
        for i, (_uuid, name, sel, coh, _rank) in enumerate(board, start=1):
            group = ("TOP" if i <= n_top else
                     "BOTTOM" if i > n - n_bottom else "middle")
            print(f"  {i:>3}. {name[:44]:<46} sel={sel:6.2f} cohort={coh:6.2f}  [{group}]")

        # --- PORTFOLIOS ---
        pf = _rows(cur, """
            select a.display_name, p.cash_usd, p.realized_pnl_usd, p.fees_usd,
                   p.peak_nav_usd,
                   (select count(*) from evo_positions pos
                    where pos.agent_uuid=a.agent_uuid and pos.ledger=p.ledger
                      and pos.status='open') as open_pos,
                   (select coalesce(sum(pos.quantity*coalesce(pos.last_mark_cents,
                                                              pos.avg_price_cents)/100.0),0)
                    from evo_positions pos
                    where pos.agent_uuid=a.agent_uuid and pos.ledger=p.ledger
                      and pos.status='open') as pos_value
            from evo_portfolios p join evo_agents a on a.agent_uuid=p.agent_uuid
            where p.ledger = %s and a.status='active'
            order by (p.cash_usd + (select coalesce(sum(pos.quantity*
                      coalesce(pos.last_mark_cents,pos.avg_price_cents)/100.0),0)
                      from evo_positions pos where pos.agent_uuid=a.agent_uuid
                        and pos.ledger=p.ledger and pos.status='open')) desc""",
            (f"cohort:{cid}",))
        print(f"\nPORTFOLIOS (cohort ledger, {len(pf)} active)")
        for name, cash, realized, fees, peak, open_pos, pos_value in pf:
            nav = float(cash) + float(pos_value)
            dd = (float(peak) - nav) / float(peak) * 100 if peak and float(peak) > 0 else 0
            print(f"  {name[:44]:<46} nav=${nav:8.2f} real=${float(realized):+8.2f} "
                  f"fees=${float(fees):6.2f} dd={dd:4.1f}% open={open_pos}")

        # --- ACTIVITY ---
        orders = _rows(cur, """
            select status, count(*) from evo_orders
            where created_at > now() - make_interval(hours => %s)
            group by status order by 2 desc""", (args.hours,))
        fills = _one(cur, """
            select count(*) from evo_fills
            where created_at > now() - make_interval(hours => %s)""", (args.hours,))
        fired = _one(cur, """
            select count(*) from evo_listener_events
            where created_at > now() - make_interval(hours => %s)""", (args.hours,))
        revs = _one(cur, """
            select count(*) from evo_genomes
            where created_at > now() - make_interval(hours => %s) and material""",
            (args.hours,))
        infl = _one(cur, """
            select count(*) from evo_influences
            where created_at > now() - make_interval(hours => %s)""", (args.hours,))
        print("\nACTIVITY (window)")
        print("  orders: " + (", ".join(f"{s}={n}" for s, n in orders) or "none")
              + f" | fills={fills} listener_fires={fired}"
              f" material_revisions={revs} influences={infl}")

        # --- FAMILIES / CONCENTRATION ---
        fam = _rows(cur, """
            select surname, count(*) from evo_agents where status='active'
            group by surname order by 2 desc limit 8""")
        sf = _rows(cur, """
            select coalesce(spec_json->>'family','?'), count(*) from evo_strategies
            where status='active' group by 1 order by 2 desc limit 8""")
        print("\nFAMILIES  " + ", ".join(f"{s}x{n}" for s, n in fam))
        print("STRATEGY FAMILIES  " + (", ".join(f"{s}x{n}" for s, n in sf) or "none active"))

        # --- TICKETS ---
        tix = _rows(cur, """
            select t.id, t.category, t.capability, t.urgency, t.status,
                   (select count(*) from evo_ticket_supporters s where s.ticket_id=t.id),
                   extract(epoch from (now() - t.created_at))/86400.0
            from evo_tickets t where t.status in ('open','in_review')
            order by 6 desc, t.id limit 10""")
        print(f"\nTICKETS ({len(tix)} open)")
        for tid, cat, cap, urg, st, sup, age in tix:
            print(f"  #{tid} [{cat}/{urg}/{st}] +{sup} supporters, {age:.1f}d old: {cap[:70]}")

        # --- ANOMALIES ---
        print("\nANOMALIES")
        anomalies = 0
        stuck_hb = _one(cur, """
            select count(*) from evo_heartbeats
            where status='running' and created_at < now() - interval '30 minutes'""")
        if stuck_hb:
            anomalies += 1
            print(f"  !! {stuck_hb} heartbeats stuck 'running' > 30min")
        abandoned = _one(cur, """
            select count(*) from evo_heartbeats
            where status='abandoned'
              and created_at > now() - make_interval(hours => %s)""", (args.hours,))
        if abandoned:
            anomalies += 1
            print(f"  !! {abandoned} abandoned heartbeats in window (worker restarts?)")
        integ = _rows(cur, """
            select agent_uuid, kind, count(*) from evo_audit_events
            where severity='integrity'
              and created_at > now() - make_interval(hours => %s)
            group by 1,2""", (args.hours,))
        for au, kind, cnt in integ:
            anomalies += 1
            print(f"  !! integrity: agent {au[:8]} {kind} x{cnt}")
        old_open = _one(cur, """
            select count(*) from evo_orders
            where status in ('open','partial')
              and created_at < now() - interval '24 hours'""")
        if old_open:
            anomalies += 1
            print(f"  !! {old_open} orders open > 24h (fills stalled or maker never through)")
        degraded = _one(cur, """
            select count(*) from evo_heartbeats where status='degraded'
              and created_at > now() - make_interval(hours => %s)""", (args.hours,))
        if degraded:
            anomalies += 1
            print(f"  !  {degraded} degraded heartbeats in window (LLM key/budget?)")
        if not anomalies:
            print("  all clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
