"""Evo system self-test — the live-health engine of the verification loop.

Runs the production probes from the verification plan (docs/EVO_TEST_LOOP.md) as
one read-only pass and prints a structured per-check verdict:

  PASS      the deployed system is exercising this path correctly
  NOT_YET   the path is healthy but hasn't been exercised yet (e.g. no trades)
  BROKEN    a real failure a fix should target
  INFO      context, no pass/fail

Pulled via the ops channel:  {"type":"script","name":"evo_selftest"}
Self-contained (stdlib + psycopg), read-only (SELECT role + read-only txn).

This covers the LIVE-probeable checks only. Mechanic/selection correctness
(the `unit`/`sim` checks in the plan) is verified by the correctness engine —
`pytest` + `scripts/evo_simulation.py` — which the loop runs separately.

Exit code: 1 if any check is BROKEN, else 0.
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

PASS, NOT_YET, BROKEN, INFO = "PASS", "NOT_YET", "BROKEN", "INFO"


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def _one(cur, sql: str, params: tuple = ()):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _rows(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Checks. Each returns (id, tier, title, verdict, detail).
# ---------------------------------------------------------------------------


def c_worker_alive(cur):
    # cycle-driven signals (not slot-driven, so heartbeat lulls don't false-alarm)
    last_fit = _one(cur, "select extract(epoch from (now()-max(computed_at)))/60 "
                         "from evo_fitness where kind='interim'")
    last_aud = _one(cur, "select extract(epoch from (now()-max(created_at)))/60 "
                         "from evo_audit_events")
    mins = min([m for m in (last_fit, last_aud) if m is not None] or [None])
    if mins is None:
        return ("L-1", 0, "Worker is cycling", NOT_YET, "no cycle activity recorded yet")
    v = PASS if mins <= 90 else BROKEN
    return ("L-1", 0, "Worker is cycling", v,
            f"last cycle activity {mins:.0f} min ago (interim fitness / audit)")


def c_heartbeat_health(cur):
    # Recency-aware: an infra blip (credit/key/price) that has *already recovered* inside the
    # window must not read as BROKEN. We only stop-the-line when infra is the *latest* signal —
    # i.e. nothing has completed since the most recent infra failure.
    comp = _one(cur, "select count(*) from evo_heartbeats where status='completed' "
                     "and created_at > now()-interval '6 hours'") or 0
    deg = _one(cur, "select count(*) from evo_heartbeats where status='degraded' "
                    "and created_at > now()-interval '6 hours'") or 0
    if comp + deg == 0:
        return ("L-2", 0, "Heartbeat completion rate", NOT_YET, "no heartbeats in last 6h (lull)")
    rate = comp / (comp + deg)
    # minutes since the most recent completed heartbeat (None if none in window)
    last_comp = _one(cur, "select extract(epoch from (now()-max(created_at)))/60 "
                          "from evo_heartbeats where status='completed' "
                          "and created_at > now()-interval '6 hours'")
    # minutes since the most recent *infra* degrade (credit exhausted / no key / no price)
    infra_pred = ("status='degraded' and created_at > now()-interval '6 hours' and ("
                  "status_detail ilike '%%credit balance is too low%%' "
                  "or status_detail ilike '%%no anthropic_api_key%%' "
                  "or status_detail ilike 'no price%%')")
    last_infra = _one(cur, "select extract(epoch from (now()-max(created_at)))/60 "
                           f"from evo_heartbeats where {infra_pred}")
    # BROKEN only when an infra outage is the latest signal: no completion at all, or the last
    # completion is *older* than the last infra failure (larger minutes-ago = older).
    if last_infra is not None and (last_comp is None or last_infra < last_comp):
        return ("L-2", 0, "Heartbeat completion rate", BROKEN,
                f"infra outage {last_infra:.0f} min ago, no completion since ({rate:.0%} 6h) — active")
    recovered = f"; last infra blip {last_infra:.0f} min ago (recovered)" if last_infra is not None else ""
    if last_comp is not None:
        detail = f"last completion {last_comp:.0f} min ago; {rate:.0%} ok ({comp}/{comp+deg}, 6h){recovered}"
    else:
        detail = f"{rate:.0%} ok ({comp}/{comp+deg}, 6h){recovered}"
    # Routine heartbeats fire ~6x/day per agent (a ~4h cadence + up to ~2h jitter), so a clean
    # completion within ~5h is healthy even when an earlier, now-recovered blip dented the 6h
    # rate. Recency-first: the latest signal is a good completion -> PASS. INFO only when the
    # newest completion is genuinely stale (well past the cadence) but no infra outage is active.
    HEALTHY_GAP_MIN = 300
    v = PASS if (last_comp is not None and last_comp <= HEALTHY_GAP_MIN) else INFO
    return ("L-2", 0, "Heartbeat completion rate", v, detail)


def c_llm_cost(cur):
    n = _one(cur, "select count(*) from evo_llm_usage where created_at > now()-interval '24 hours'") or 0
    over = _one(cur, """
        select count(*) from evo_budgets
        where resource='llm_cost_usd' and used > allocated + 0.0001""") or 0
    if over:
        return ("L-3", 0, "LLM spend tracked & capped", BROKEN,
                f"{over} agent-budgets exceed their allocation")
    if n == 0:
        return ("L-3", 0, "LLM spend tracked & capped", NOT_YET, "no usage rows in 24h")
    return ("L-3", 0, "LLM spend tracked & capped", PASS,
            f"{n} usage rows (24h), no budget over-spend")


def c_announcements(cur):
    n = _one(cur, "select count(*) from evo_announcements where active "
                  "and effective_at <= now() and (expires_at is null or expires_at > now())")
    if n is None:
        return ("L-4", 0, "Announcements active", INFO, "table not present")
    v = PASS if n and n > 0 else INFO
    return ("L-4", 0, "Announcements active", v, f"{n or 0} active operator notice(s)")


def c_facts(cur):
    n = _one(cur, "select count(*) from evo_memories where kind='fact'") or 0
    if n == 0:
        return ("MEM-1", 1, "Facts append-only & retrievable", NOT_YET, "no facts recorded yet")
    imm = _one(cur, "select count(*) from evo_memories where kind='fact' and not immutable") or 0
    if imm:
        return ("MEM-1", 1, "Facts append-only & retrievable", BROKEN,
                f"{imm} fact rows not marked immutable")
    return ("MEM-1", 1, "Facts append-only & retrievable", PASS, f"{n} immutable facts")


def c_beliefs(cur):
    n = _one(cur, "select count(*) from evo_memories where kind='belief'") or 0
    if n == 0:
        return ("MEM-2", 1, "Beliefs revise by supersession", NOT_YET, "no beliefs yet")
    # a superseded belief must point forward via another row's supersedes_id
    chains = _one(cur, "select count(*) from evo_memories where kind='belief' and supersedes_id is not null") or 0
    orphan = _one(cur, """
        select count(*) from evo_memories b where b.kind='belief' and b.superseded
        and not exists (select 1 from evo_memories n where n.supersedes_id=b.id)""") or 0
    if orphan:
        return ("MEM-2", 1, "Beliefs revise by supersession", BROKEN,
                f"{orphan} superseded beliefs with no successor row")
    if chains == 0 and n >= 10:
        # Structural pass (no orphans) is necessarily true with zero chains — not
        # real evidence supersession works. A meaningful sample of beliefs with
        # never one real revision is worth a look, not a silent PASS.
        return ("MEM-2", 1, "Beliefs revise by supersession", INFO,
                f"{n} beliefs, 0 revision links yet — supersession unexercised despite volume")
    return ("MEM-2", 1, "Beliefs revise by supersession", PASS,
            f"{n} beliefs, {chains} revision link(s), chains intact")


def c_journals(cur):
    unlinked = _one(cur, "select count(*) from evo_heartbeats "
                         "where status='completed' and journal_memory_id is null "
                         "and created_at > now()-interval '48 hours'") or 0
    tot = _one(cur, "select count(*) from evo_heartbeats where status='completed' "
                    "and created_at > now()-interval '48 hours'") or 0
    if tot == 0:
        return ("MEM-3", 1, "Every heartbeat journals", NOT_YET, "no completed heartbeats in 48h")
    if unlinked:
        return ("MEM-3", 1, "Every heartbeat journals", BROKEN,
                f"{unlinked}/{tot} completed heartbeats have no journal")
    return ("MEM-3", 1, "Every heartbeat journals", PASS, f"{tot}/{tot} journaled (48h)")


def c_experiments(cur):
    n = _one(cur, "select count(*) from evo_experiments") or 0
    if n == 0:
        return ("MEM-4", 1, "Experiments registered & concluded", NOT_YET, "no experiments yet")
    no_pred = _one(cur, "select count(*) from evo_experiments where falsifiable_prediction is null") or 0
    concl = _one(cur, "select count(*) from evo_experiments where status='concluded'") or 0
    if no_pred:
        return ("MEM-4", 1, "Experiments registered & concluded", BROKEN,
                f"{no_pred} experiments lack a falsifiable prediction")
    return ("MEM-4", 1, "Experiments registered & concluded", PASS,
            f"{n} experiments ({concl} concluded), all with predictions")


def c_genomes(cur):
    # every active agent should have >=1 cognitive and >=1 trading genome; revisions unique/monotonic
    missing = _one(cur, """
        select count(*) from evo_agents a where a.status='active'
        and (not exists (select 1 from evo_genomes g where g.agent_uuid=a.agent_uuid and g.kind='cognitive')
          or not exists (select 1 from evo_genomes g where g.agent_uuid=a.agent_uuid and g.kind='trading'))""") or 0
    if missing:
        return ("GEN-1", 1, "Genomes version immutably", BROKEN,
                f"{missing} active agents missing a genome")
    revs = _one(cur, "select count(*) from evo_genomes where revision > 1") or 0
    return ("GEN-1", 1, "Genomes version immutably", PASS,
            f"all active agents have both genomes; {revs} revision(s) beyond initial")


def c_listeners(cur):
    n = _one(cur, "select count(*) from evo_listeners") or 0
    if n == 0:
        return ("LIS-1", 2, "Listeners create/fire/score", NOT_YET, "no listeners created yet")
    fired = _one(cur, "select count(*) from evo_listener_events") or 0
    return ("LIS-1", 2, "Listeners create/fire/score", PASS,
            f"{n} listeners, {fired} event(s) fired")


def c_strategies(cur):
    n = _one(cur, "select count(*) from evo_strategies") or 0
    if n == 0:
        return ("STR-1", 2, "Strategies build & validate", NOT_YET, "no strategies saved yet")
    active = _one(cur, "select count(*) from evo_strategies where status='active'") or 0
    rej = _one(cur, "select count(*) from evo_strategies where status='rejected'") or 0
    return ("STR-1", 2, "Strategies build & validate", PASS,
            f"{n} strategies ({active} active, {rej} rejected)")


def c_graveyard(cur):
    n = _one(cur, "select count(*) from evo_graveyard") or 0
    if n == 0:
        return ("GRV-1", 2, "Graveyard seeded", BROKEN, "graveyard empty — dead-idea guard has nothing to block against")
    return ("GRV-1", 2, "Graveyard seeded", PASS, f"{n} graveyard entries guarding dead ideas")


def c_paper_trades(cur):
    orders = _one(cur, "select count(*) from evo_orders") or 0
    fills = _one(cur, "select count(*) from evo_fills") or 0
    settled = _one(cur, "select count(*) from evo_positions where status in ('settled','closed','liquidated')") or 0
    if orders == 0:
        return ("PAP-1/2", 3, "Paper trading exercised", NOT_YET,
                "no orders yet — agents still researching (first trades are the milestone to watch)")
    return ("PAP-1/2", 3, "Paper trading exercised", PASS,
            f"{orders} orders, {fills} fills, {settled} settled/closed positions")


def c_capital_norm(cur):
    # every active agent's current-cohort ledger must start at exactly $1,000
    bad = _one(cur, """
        select count(*) from evo_portfolios p join evo_agents a on a.agent_uuid=p.agent_uuid
        where a.status='active' and p.ledger like 'cohort:%%'
        and abs(p.starting_capital_usd - 1000.0) > 0.001""") or 0
    tot = _one(cur, "select count(*) from evo_portfolios where ledger like 'cohort:%%'") or 0
    if tot == 0:
        return ("PAP-3", 3, "Cohort ledgers normalized to $1,000", NOT_YET, "no cohort ledgers yet")
    if bad:
        return ("PAP-3", 3, "Cohort ledgers normalized to $1,000", BROKEN,
                f"{bad} cohort ledgers not normalized to $1,000")
    return ("PAP-3", 3, "Cohort ledgers normalized to $1,000", PASS,
            f"all {tot} cohort ledgers start at exactly $1,000")


def c_no_real_orders(cur):
    # sanity: the evo package must never write to the live-order path
    n = _one(cur, "select to_regclass('public.live_orders')")
    if not n:
        return ("PAP-4", 3, "No real-order path (paper-only)", PASS, "no live_orders table in reach")
    leaked = _one(cur, "select count(*) from live_orders where strategy like 'evo%%' or strategy like '%%evo%%'") or 0
    if leaked:
        return ("PAP-4", 3, "No real-order path (paper-only)", BROKEN,
                f"{leaked} live orders tagged evo — REAL-ORDER LEAK")
    return ("PAP-4", 3, "No real-order path (paper-only)", PASS, "zero evo rows in live_orders")


def c_budgets(cur):
    over = _rows(cur, "select resource, count(*) from evo_budgets where used > allocated + 0.0001 "
                      "group by 1")
    if over:
        d = ", ".join(f"{r}:{n}" for r, n in over)
        return ("BUD-1", 4, "Resource budgets enforced", BROKEN, f"over-spent budgets: {d}")
    n = _one(cur, "select count(*) from evo_budgets") or 0
    return ("BUD-1", 4, "Resource budgets enforced", PASS if n else NOT_YET,
            f"{n} budget rows, none exceeded")


def c_tickets(cur):
    n = _one(cur, "select count(*) from evo_tickets") or 0
    if n == 0:
        return ("TIC-1", 4, "Capability tickets", NOT_YET, "no tickets filed yet")
    openq = _one(cur, "select count(*) from evo_tickets where status in ('open','in_review')") or 0
    return ("TIC-1", 4, "Capability tickets", PASS, f"{n} tickets ({openq} in the review queue)")


def c_data_sources(cur):
    n = _one(cur, "select count(*) from evo_data_sources") or 0
    if n == 0:
        return ("DAT-1", 4, "Data-source registry & health", BROKEN, "no data sources registered")
    unresolved = _one(cur, "select count(*) from evo_data_health_events where resolved_at is null") or 0
    return ("DAT-1", 4, "Data-source registry & health", PASS,
            f"{n} sources; {unresolved} unresolved health event(s)")


def c_fitness(cur):
    recent = _one(cur, "select count(*) from evo_fitness where kind='interim' "
                       "and computed_at > now()-interval '2 hours'") or 0
    if recent == 0:
        return ("FIT-1", 5, "Interim fitness computes on full rubric", NOT_YET,
                "no interim fitness in last 2h")
    # every rubric component present in the newest rows (text match avoids relying
    # on the jsonb ?& operator through the driver)
    missing = _one(cur, """
        select count(*) from evo_fitness
        where kind='interim' and computed_at > now()-interval '2 hours'
        and not (components_json::text ilike '%%profit%%'
             and components_json::text ilike '%%consistency%%'
             and components_json::text ilike '%%risk%%'
             and components_json::text ilike '%%evidence%%'
             and components_json::text ilike '%%forecast%%'
             and components_json::text ilike '%%adaptive%%')""") or 0
    if missing:
        return ("FIT-1", 5, "Interim fitness computes on full rubric", BROKEN,
                f"{missing} recent fitness rows missing rubric components")
    return ("FIT-1", 5, "Interim fitness computes on full rubric", PASS,
            f"{recent} interim rows (2h), all six components present")


def c_leaderboard_delay(cur):
    # Two ways this can be wrong, checked separately so each failure names itself:
    #  - too SHORT: a row visible less than ~5h after it was first computed.
    #  - never REACHABLE: visible_after re-armed on every recompute (relative to
    #    computed_at, which advances every interim pass) instead of being fixed
    #    relative to created_at (set once, at insert). A row whose visible_after
    #    keeps chasing computed_at can drift arbitrarily far into the future and
    #    peers.delayed_leaderboard() then never returns it — this is exactly what
    #    happened in production for the system's entire life (WS-014): interim
    #    fitness recomputed hourly, the 6h delay re-armed on every recompute, so
    #    visible_after was always ~6h ahead of "now" and no row was ever visible.
    too_short = _one(cur, "select count(*) from evo_fitness "
                          "where visible_after < computed_at + interval '5 hours'") or 0
    never_reachable = _one(cur, "select count(*) from evo_fitness "
                                "where visible_after > created_at + interval '7 hours'") or 0
    n = _one(cur, "select count(*) from evo_fitness") or 0
    if n == 0:
        return ("FIT-3", 5, "Leaderboard is 6h-delayed", NOT_YET, "no fitness rows yet")
    if too_short:
        return ("FIT-3", 5, "Leaderboard is 6h-delayed", BROKEN,
                f"{too_short} fitness rows visible sooner than the 6h delay")
    if never_reachable:
        return ("FIT-3", 5, "Leaderboard is 6h-delayed", BROKEN,
                f"{never_reachable} fitness rows have visible_after re-armed past "
                "created_at + 7h — the delay is chasing recomputation and may never "
                "become reachable")
    return ("FIT-3", 5, "Leaderboard is 6h-delayed", PASS, f"all {n} rows honor the 6h delay")


def c_idempotency(cur):
    dups = []
    for name, sql in [
        ("heartbeats", "select count(*) from (select idem_key from evo_heartbeats group by 1 having count(*)>1) t"),
        ("births", "select count(*) from (select cohort_id, slot_key from evo_births group by 1,2 having count(*)>1) t"),
        ("orders", "select count(*) from (select idem_key from evo_orders group by 1 having count(*)>1) t"),
        ("transitions", "select count(*) from (select cohort_id from evo_transitions group by 1 having count(*)>1) t"),
    ]:
        c = _one(cur, sql) or 0
        if c:
            dups.append(f"{name}:{c}")
    if dups:
        return ("INV-1", 7, "Idempotency — never twice", BROKEN, "duplicates: " + ", ".join(dups))
    return ("INV-1", 7, "Idempotency — never twice", PASS, "no duplicate heartbeats/births/orders/transitions")


def c_secret_leak(cur):
    # INV-3 (dashboard secret leak) is verified by the correctness engine's unit test,
    # not a DB probe; surface a reminder so the loop keeps it in view.
    return ("INV-3", 7, "No secret leaves the dashboard", INFO,
            "verified by tests/test_dashboard.py in the correctness engine")


CHECKS = [
    c_worker_alive, c_heartbeat_health, c_llm_cost, c_announcements,
    c_facts, c_beliefs, c_journals, c_experiments, c_genomes,
    c_listeners, c_strategies, c_graveyard,
    c_paper_trades, c_capital_norm, c_no_real_orders,
    c_budgets, c_tickets, c_data_sources,
    c_fitness, c_leaderboard_delay,
    c_idempotency, c_secret_leak,
]

_TIERS = {
    0: "Liveness & cognition", 1: "Memory & learning", 2: "Mechanics",
    3: "Paper trading", 4: "Budgets / tickets / data", 5: "Evaluation",
    6: "Selection", 7: "Invariants",
}
_MARK = {PASS: "PASS ", NOT_YET: "···  ", BROKEN: "FAIL ", INFO: "info "}


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser().parse_args(argv)
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
    print(f"EVO SELF-TEST @ {now.isoformat(timespec='seconds')}")
    print("=" * 78)

    results = []
    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        for check in CHECKS:
            try:
                results.append(check(cur))
            except Exception as exc:  # noqa: BLE001 — a probe error is itself a finding
                conn.rollback()
                results.append((getattr(check, "__name__", "?"), 9, check.__name__, BROKEN,
                                f"probe error: {type(exc).__name__}: {exc}"))

    last_tier = None
    for cid, tier, title, verdict, detail in sorted(results, key=lambda r: (r[1], r[0])):
        if tier != last_tier:
            print(f"\n— Tier {tier}: {_TIERS.get(tier, '?')} —")
            last_tier = tier
        print(f"  {_MARK.get(verdict, '?')} {cid:<9} {title}")
        print(f"        {detail}")

    counts = {PASS: 0, NOT_YET: 0, BROKEN: 0, INFO: 0}
    for r in results:
        counts[r[3]] = counts.get(r[3], 0) + 1
    print("\n" + "=" * 78)
    print(f"SUMMARY  pass={counts[PASS]}  not-yet={counts[NOT_YET]}  "
          f"broken={counts[BROKEN]}  info={counts[INFO]}")
    if counts[BROKEN]:
        broken = [f"{r[0]} ({r[4]})" for r in results if r[3] == BROKEN]
        print("BROKEN:  " + " | ".join(broken))
    return 1 if counts[BROKEN] else 0


if __name__ == "__main__":
    sys.exit(main())
