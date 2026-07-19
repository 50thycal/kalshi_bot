"""Family tree + influence graph for the evolutionary agent system (read-only).

    {"type": "script", "name": "evo_tree"}

Prints the vertical family forest (founder -> children, with status/generation/
fitness history) and the horizontal influence graph (who adopted what from whom)
— spec §6: lineage and influence are separate structures.

Self-contained (stdlib + psycopg), read-only."""

from __future__ import annotations

import os
import sys

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


def main(argv: list[str] | None = None) -> int:
    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO", ""))
    if not url:
        print("DATABASE_URL_RO not set", file=sys.stderr)
        return 2
    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        cur.execute("""
            select agent_uuid, historical_name, surname, generation, status, origin,
                   parent_uuid, agent_code
            from evo_agents order by id""")
        agents = cur.fetchall()
        if not agents:
            print("No agents yet.")
            return 0
        cur.execute("""
            select agent_uuid, cohort_id, rank, selection_score from evo_fitness
            where kind='final' order by cohort_id""")
        fitness: dict[str, list[str]] = {}
        for au, cid, rank, sel in cur.fetchall():
            fitness.setdefault(au, []).append(f"c{cid}:#{rank}({sel:.0f})")

        by_uuid = {a[0]: a for a in agents}
        children: dict[str, list[str]] = {}
        roots: list[str] = []
        for a in agents:
            if a[6] and a[6] in by_uuid:
                children.setdefault(a[6], []).append(a[0])
            else:
                roots.append(a[0])

        print(f"FAMILY TREE — {len(agents)} agents, "
              f"{len({a[2] for a in agents})} families")
        print("=" * 78)

        def render(uuid: str, depth: int) -> None:
            a = by_uuid[uuid]
            marker = {"active": "*", "retired": "+", "suspended": "!"}.get(a[4], "?")
            hist = " ".join(fitness.get(uuid, []))[:40]
            print(f"{'  ' * depth}{marker} {a[1]} [{a[5]}] {hist}")
            for c in children.get(uuid, []):
                render(c, depth + 1)

        # group roots by surname for readability
        for uuid in sorted(roots, key=lambda u: (by_uuid[u][2], by_uuid[u][7])):
            render(uuid, 0)
        print("\n(* active, + retired, ! suspended; fitness = cohort:#rank(score))")

        cur.execute("""
            select i.created_at, sa.historical_name, ra.historical_name,
                   i.source_family, i.receiving_family, i.concept, i.result
            from evo_influences i
            join evo_agents sa on sa.agent_uuid = i.source_uuid
            join evo_agents ra on ra.agent_uuid = i.receiving_uuid
            order by i.id desc limit 40""")
        edges = cur.fetchall()
        print(f"\nINFLUENCE GRAPH — {len(edges)} most recent edges (horizontal learning;"
              " never changes lineage)")
        print("=" * 78)
        cross = 0
        for ts, src, dst, sf, rf, concept, result in edges:
            flag = " [cross-family]" if sf != rf else ""
            cross += 1 if sf != rf else 0
            print(f"  {ts:%m-%d %H:%M} {src[:24]} -> {dst[:24]}: {concept} "
                  f"({result}){flag}")
        if edges:
            print(f"\n  cross-family edges: {cross}/{len(edges)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
