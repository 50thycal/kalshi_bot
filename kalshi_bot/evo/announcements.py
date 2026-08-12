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
        key="2026-07-order-repricing-correction",
        title="Correction: your open orders DO keep re-checking every cycle",
        category="system_change",
        body=(
            "Correction to the instant-order-evaluation notice: an earlier version said "
            "a still-open taker order 'will NOT re-check itself against a new price later' "
            "— that was wrong. Every order still open or partial gets re-evaluated against "
            "a fresh quote EVERY cycle (this was already true before the instant-evaluation "
            "change; that change only closed the gap on the FIRST check). So a resting order "
            "will fill automatically the moment the market moves back to its price, with no "
            "action needed from you. Cancel it only when your thesis itself is stale (you no "
            "longer believe the trade), not because you think it's 'stuck' waiting to be "
            "checked — it isn't."
        ),
        expires_in_days=21,
    ),
    dict(
        key="2026-07-strategy-spec-common-mistakes",
        title="save_strategy / run_backtest spec rejections now explain WHERE a field belongs",
        category="system_change",
        body=(
            "System change: spec validation rejections now include a HINT, not just "
            "a bare 'Extra inputs are not permitted'. Three real mistakes seen most "
            "often: (1) max_spread_cents / min_hours_to_close / max_hours_to_close go "
            "under \"universe\" (market-selection pre-filter), NOT \"entry\" — a spread "
            "check feels entry-time but isn't; the reverse (entry's max_price_cents "
            "under \"universe\") also happens. (2) universe/entry/exit/risk are "
            "TOP-LEVEL keys, never nested inside each other. (3) There is no "
            "min_open_interest on \"universe\" — filter open_interest via an "
            "entry.conditions condition instead: {\"metric\": \"open_interest\", "
            "\"op\": \">=\", \"value\": 500}. Same schema for save_strategy + run_backtest."
        ),
        expires_in_days=21,
    ),
    dict(
        key="2026-07-fill-engine-was-broken",
        title="IMPORTANT: the fill engine was broken — re-test anything you killed on an unfilled order",
        category="system_change",
        body=(
            "Correction from your operator. Until now the fill engine could NEVER "
            "fill ANY order, at any price, on any market: a market-data bug made "
            "every orderbook parse as empty, so orders rested forever no matter how "
            "good the price. A second bug zeroed out 1-contract maker orders. BOTH "
            "ARE FIXED — the first real fill has landed. What this means for you: if "
            "you concluded an experiment was negative, or that a market or strategy "
            "'has no edge', BECAUSE your orders never filled — that is INVALID "
            "EVIDENCE, the test never ran. Re-test those hypotheses before trusting "
            "the conclusion. And stop reflexively cancelling resting orders: each one "
            "is re-checked every cycle and now fills for real."
        ),
        expires_in_days=21,
    ),
    dict(
        key="2026-07-how-your-exits-work",
        title="Know how your exits actually work — an ad-hoc position will not sell itself",
        category="system_change",
        body=(
            "Important mechanics. A position you open with submit_trade_intent does "
            "NOT manage itself — nothing will ever sell it for you. Your only exits: "
            "(a) hold to settlement deliberately, (b) submit a sell yourself on a "
            "later heartbeat, up to an hour away, so a stop-loss you intend this way "
            "is NOT immediate, or (c) create_listener with effect=trigger_heartbeat "
            "to wake yourself when a price condition hits. By contrast an ACTIVE "
            "strategy whose exit mode is tp_sl or timed is evaluated EVERY cycle by "
            "the engine: its take-profit/stop-loss fires immediately, no heartbeat "
            "needed. If you want managed exits rather than hold-to-settlement, put "
            "the trade in a strategy with an exit rule and activate it."
        ),
        expires_in_days=21,
    ),
    dict(
        key="2026-07-order-mechanics-limit-and-sell",
        title="Two order mistakes are burning your actions: limit_price_cents 0, and sell-to-short",
        category="system_change",
        body=(
            "From your operator, after reviewing every rejected order. Both are "
            "avoidable. (1) limit_price_cents must be 1-99. To trade at the "
            "current market price, OMIT the field entirely — a taker order with "
            "no limit is marketable at the touch. Writing 0 to mean 'no limit' "
            "is rejected (one heartbeat lost SIX orders this way), and a 0c bid "
            "could never fill anyway. (2) action 'sell' only CLOSES a position "
            "you already hold — it cannot open a short. To bet AGAINST a side, "
            "BUY the opposite side: bet against yes => buy no. Rejections now "
            "spell out both fixes, but you should not need to see them."
        ),
        expires_in_days=21,
    ),
    dict(
        key="2026-07-price-payoff-asymmetry",
        title="Check risk/reward before you size: at 96c you risk 96 to win 4",
        category="research",
        body=(
            "From your operator, computed from the fleet's OWN settled trades. "
            "Buying favorites at 90-98c won most of the time but earned pennies: "
            "+$0.36, +$0.75, +$1.25. One loss at 96c cost -$24.00 and erased all "
            "of it. That is arithmetic, not luck — buying at price P risks P to "
            "win (100-P), so at 96c you need ~96% accuracy just to break even. "
            "The fleet's best results came from mid-range entries at 74-77c "
            "(+$5.75, +$6.50, +$12.50). Uniform sizing makes it worse: 25 "
            "contracts risks $24 to win $1 at 96c, but $18.50 to win $6.50 at "
            "74c. Compute risk/reward BEFORE sizing, and size down as price goes up."
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
    dict(
        key="2026-08-operator-mirror",
        title="New: read your operator's research library AND its live scoreboard",
        category="capability",
        body=(
            "New from your operator, who researches strategies in this same repo: "
            "you can now read ALL of it. (1) read_doc {\"name\": \"BOOK_REGISTRY\"} "
            "— the operator's own docs (theses, studies, postmortems; full list in "
            "your action protocol). BOOK_REGISTRY indexes every operator book: tag, "
            "status, edge, pre-registered gate. (2) inspect_data {\"source\": "
            "\"book_performance\"} — the LIVE scoreboard for those tags (n, win%, "
            "per-trade P&L, open positions). Read them as a PAIR: a thesis whose "
            "scoreboard is negative is a documented failure to learn from, not a "
            "strategy to copy — several operator books are proven mirages. Cite the "
            "doc + the numbers when you copy or reject an idea."
        ),
        expires_in_days=21,
    ),
    dict(
        key="2026-08-external-signals",
        title="New metrics: gate on INFORMATION, not just price",
        category="capability",
        body=(
            "Every metric you had is a property of Kalshi's order book, so every spec "
            "you could write stated a PRICE PATTERN — and those earn the spread minus "
            "two fees. Two new entry metrics change that. pm_divergence: Polymarket's "
            "implied probability minus our mid, in cents, same weather bucket "
            "(positive => our YES is cheap vs the other venue). spot_vs_strike: "
            "percent from BTC/ETH spot to a crypto market's boundary, positive = YES "
            "winning. Both are None when no fresh signal exists, and None FAILS the "
            "condition — never read it as zero. See your action protocol for per-"
            "dataset backtest support. Also: registering a source we do not collect "
            "now files a real operator ticket instead of doing nothing."
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
    session, *, now: datetime | None = None, limit: int = 14
) -> list[EvoAnnouncement]:
    """Currently-broadcasting announcements, newest first: active, already effective,
    and not yet expired. Filtered in Python (the table is tiny) so it behaves the
    same on sqlite and Postgres regardless of stored-tz quirks.

    Ties on effective_at are real: every announcement seeded in the SAME deploy
    shares the exact same `now`, and the roster has grown past `limit` — without
    a deterministic tiebreaker, which announcement lands in the truncated top-N
    is arbitrary, so a still-valid one can silently vanish from every agent's
    prompt for no visible reason. id.desc() breaks ties by insertion order
    (favor what was most recently added to the roster)."""
    now = now or datetime.now(timezone.utc)
    rows = session.scalars(
        select(EvoAnnouncement)
        .where(EvoAnnouncement.active.is_(True))
        .order_by(EvoAnnouncement.effective_at.desc(), EvoAnnouncement.id.desc())
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
