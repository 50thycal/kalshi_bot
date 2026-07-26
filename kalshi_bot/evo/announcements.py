"""Operator announcements: the broadcast channel to the whole agent population.

When the operator makes a system change (a config change, a new rule, a fixed
bug), an announcement here is injected into EVERY agent's heartbeat context while
it is active — so all agents learn the same thing at the same time instead of
each having to rediscover it.

There is no live write path (agents and the read-only ops channel cannot write),
so announcing something matches the model-price / graveyard seed pattern: declare
the announcement here with a stable `key` and deploy; `seed_announcements()`
inserts it once, idempotently. To retire an announcement, let it expire
(`expires_in_days`) — seeding is insert-only, so editing an already-seeded row's
fields on a later deploy has no effect.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .models import EvoAnnouncement

# Each entry: key (stable, unique), title, body, category, expires_in_days
# (0 / omitted = never expires). Add an entry + deploy to broadcast; the whole
# population sees it on their next heartbeat.
ANNOUNCEMENTS: list[dict] = [
    dict(
        key="2026-07-inspect-data-capability",
        title="New capability: inspect_data — read ANY of our data, not just weather",
        category="capability",
        body=(
            "New from your operator: the inspect_data action now reads any data we have "
            "collected — you are NOT limited to weather. Sources: paper_trades, signals, "
            "crypto_ladders, crypto_spot, mmsell_ticks, game_tape, game_matches, "
            "market_snapshots, orderbook, polymarket. Use it to study a new domain, and "
            "especially to see what our OTHER live strategies did and copy them — e.g. "
            "inspect_data {\"source\":\"paper_trades\",\"filters\":{\"strategy\":\"mmsell\"}} "
            "shows mmsell's real trades + outcomes. Rows you pull appear under YOUR RECENT "
            "DATA READS on your NEXT heartbeat. Try it now: read one non-weather source "
            "this heartbeat and form a hypothesis from what you see."
        ),
        expires_in_days=14,
    ),
    dict(
        key="2026-07-crypto-backtest-dataset",
        title="New: backtest CRYPTO markets — run_backtest dataset=\"crypto\" (BTC/ETH)",
        category="capability",
        body=(
            "New from your operator: run_backtest now accepts dataset=\"crypto\". It replays "
            "Kalshi's BTC/ETH up-or-down markets (series KXBTC, KXBTCD, KXETH, KXETHD) over "
            "the live ladder snapshots we collected, with each market's outcome derived from "
            "the settling spot price vs its strike. Study the tape first with inspect_data "
            "{\"source\":\"crypto_ladders\"} and the spot with {\"source\":\"crypto_spot\"}. "
            "Coverage is the recent window where spot data exists and grows over time. "
            "Default dataset is still \"backfill_weather\"."
        ),
        expires_in_days=14,
    ),
    dict(
        key="2026-07-mmsell-backtest-dataset",
        title="New: backtest on mmsell's REAL markets — run_backtest dataset=\"mmsell\"",
        category="capability",
        body=(
            "New from your operator: run_backtest now accepts dataset=\"mmsell\". It replays "
            "the mmsell strategy's OWN settled markets over their captured live orderbook "
            "ticks, settled by the real outcome — so you can finally VALIDATE a non-weather "
            "edge, not just weather. To copy mmsell: express an mmsell-style spec (entry "
            "side=expensive/no, style=maker, exit mode=settlement) and run_backtest {\"spec\":"
            "...,\"dataset\":\"mmsell\"}. Study the tape first with inspect_data "
            "{\"source\":\"mmsell_ticks\"}. Default dataset is still \"backfill_weather\". "
            "Coverage grows as more mmsell markets settle."
        ),
        expires_in_days=14,
    ),
    dict(
        key="2026-07-instant-order-evaluation",
        title="submit_trade_intent now fills IMMEDIATELY — check the outcome's status",
        category="system_change",
        body=(
            "System change from your operator: submit_trade_intent now evaluates your "
            "order against the live quote RIGHT AWAY, in the same action outcome — it no "
            "longer waits for a later cycle. The outcome now includes \"status\" "
            "(filled|partial|open) and \"filled_quantity\"; \"ok\": true alone no longer "
            "tells you whether you actually got filled. If status=\"open\" for a taker "
            "order, the market moved past your limit before this could fill and it will "
            "keep resting at that exact price — it will NOT re-check itself against a new "
            "price later, so cancel_order it yourself if your thesis no longer holds "
            "rather than assuming it will eventually catch up. This closes the gap where "
            "orders you placed used to sit open for a full cycle (or longer) before ever "
            "being checked, by which point a thin/cheap contract's price had often already "
            "drifted past the exact touch you priced against."
        ),
        expires_in_days=21,
    ),
    dict(
        key="2026-07-cohort-full-week",
        title="Your cohort week now runs a full 7 days from when it was born",
        category="system_change",
        body=(
            "System change from your operator: each cohort now lasts exactly 7 days "
            "from the moment it is created, instead of ending on a fixed Monday-midnight "
            "boundary. Your current cohort has been extended so you get a full week — the "
            "earlier, sooner end time was only an artifact of when this system was first "
            "switched on, not a real deadline. Plan your research and trades against the "
            "cohort 'ends' timestamp shown in your heartbeat; it is now accurate. Nothing "
            "else about scoring, retirement, or reproduction has changed."
        ),
        expires_in_days=21,
    ),
]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def seed_announcements(session, *, now: datetime | None = None) -> int:
    """Insert any not-yet-seeded announcements. Idempotent on `key`. Returns rows added."""
    now = now or datetime.now(timezone.utc)
    existing = set(session.scalars(select(EvoAnnouncement.key)).all())
    added = 0
    for entry in ANNOUNCEMENTS:
        if entry["key"] in existing:
            continue
        days = entry.get("expires_in_days") or 0
        session.add(
            EvoAnnouncement(
                key=entry["key"],
                title=entry["title"],
                body=entry["body"],
                category=entry.get("category", "system_change"),
                effective_at=now,
                expires_at=(now + timedelta(days=days)) if days else None,
                active=True,
            )
        )
        added += 1
    session.flush()
    return added


def active_announcements(
    session, *, now: datetime | None = None, limit: int = 5
) -> list[EvoAnnouncement]:
    """Currently-broadcasting announcements, newest first: active, already effective,
    and not yet expired. Filtered in Python (the table is tiny) so it behaves the
    same on sqlite and Postgres regardless of stored-tz quirks."""
    now = now or datetime.now(timezone.utc)
    rows = session.scalars(
        select(EvoAnnouncement)
        .where(EvoAnnouncement.active.is_(True))
        .order_by(EvoAnnouncement.effective_at.desc())
    )
    out: list[EvoAnnouncement] = []
    for r in rows:
        if _aware(r.effective_at) > now:
            continue
        if r.expires_at is not None and _aware(r.expires_at) <= now:
            continue
        out.append(r)
        if len(out) >= limit:
            break
    return out


def summarize_for_prompt(rows: list[EvoAnnouncement], *, body_cap: int = 700) -> str:
    """Compact text block for the heartbeat prompt."""
    parts: list[str] = []
    for r in rows:
        body = r.body if len(r.body) <= body_cap else r.body[: body_cap - 1] + "…"
        parts.append(f"- [{r.category}] {r.title}\n  {body}")
    return "\n".join(parts)
