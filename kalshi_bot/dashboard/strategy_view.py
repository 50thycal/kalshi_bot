"""Deterministic, human-readable rendering of what an evolution bot is testing.

Every description here is computed in application code from data that already
exists in the database — `evo_strategies.spec_json` (the declarative StrategySpec
DSL in evo/strategy_spec.py) and `evo_genomes.document_json` (the TradingGenome in
evo/genomes.py). No LLM call is made: the dashboard must never depend on a model
request to explain a bot, and the same spec must always render the same words.

The raw spec is still returned (under "raw") so an operator can expand it for
debugging, but it is never the only thing shown.

Security: this module only reformats fields that data.py already treats as
publishable (agent-authored structured strategy data). It never touches prompts,
hidden reasoning, credentials or heartbeat error detail.
"""

from __future__ import annotations

from sqlalchemy import select

from ..evo.models import EvoAgent, EvoGenome, EvoStrategy

# Plain-English label for every metric the entry DSL can reference.
_METRIC_LABELS = {
    "yes_bid": "yes bid",
    "yes_ask": "yes ask",
    "no_bid": "no bid",
    "no_ask": "no ask",
    "spread": "spread",
    "mid": "mid price",
    "last_price": "last price",
    "volume": "volume",
    "open_interest": "open interest",
    "hours_to_close": "hours to close",
}
_CENT_METRICS = {"yes_bid", "yes_ask", "no_bid", "no_ask", "spread", "mid", "last_price"}
_OP_LABELS = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "=", "!=": "≠"}
_SIDE_LABELS = {
    "yes": "the YES side",
    "no": "the NO side",
    "cheap": "whichever side is cheaper",
    "expensive": "whichever side is more expensive",
}


def _num(value: float | int | None) -> str:
    """Render a spec number without misleading precision (5.0 -> "5")."""
    if value is None:
        return "—"
    f = float(value)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def _condition_text(cond: dict) -> str:
    metric = str(cond.get("metric", "?"))
    label = _METRIC_LABELS.get(metric, metric)
    op = _OP_LABELS.get(str(cond.get("op", "")), str(cond.get("op", "")))
    value = _num(cond.get("value"))
    unit = "¢" if metric in _CENT_METRICS else ("h" if metric == "hours_to_close" else "")
    return f"{label} {op} {value}{unit}"


def _universe_text(uni: dict) -> str:
    prefixes = [str(p) for p in (uni.get("series_prefixes") or [])]
    categories = [str(c) for c in (uni.get("categories") or [])]
    if prefixes:
        scope = "markets in " + ", ".join(prefixes[:6])
        if len(prefixes) > 6:
            scope += f" (+{len(prefixes) - 6} more)"
    elif categories:
        scope = "markets in " + ", ".join(categories[:6])
    else:
        scope = "any market"
    excluded = [str(p) for p in (uni.get("exclude_series_prefixes") or [])]
    if excluded:
        scope += ", excluding " + ", ".join(excluded[:4])
    return scope


def _exit_text(ex: dict) -> str:
    mode = str(ex.get("mode") or "settlement")
    if mode == "tp_sl":
        parts = []
        if ex.get("take_profit_cents") is not None:
            parts.append(f"takes profit at +{_num(ex['take_profit_cents'])}¢")
        if ex.get("stop_loss_cents") is not None:
            parts.append(f"stops out at −{_num(ex['stop_loss_cents'])}¢")
        return " and ".join(parts) if parts else "exits on a take-profit / stop-loss rule"
    if mode == "timed":
        hours = ex.get("max_hold_hours")
        return (
            f"closes after {_num(hours)}h" if hours is not None else "closes on a timer"
        )
    return "holds to settlement"


def summarize_spec(spec: dict) -> str:
    """One plain-English sentence describing what the strategy actually does."""
    if not isinstance(spec, dict) or not spec:
        return "No declarative strategy is active for this bot."
    entry = spec.get("entry") or {}
    uni = spec.get("universe") or {}
    risk = spec.get("risk") or {}
    style = "as a taker" if str(entry.get("style", "taker")) == "taker" else "with resting maker orders"
    side = _SIDE_LABELS.get(str(entry.get("side", "yes")), str(entry.get("side", "yes")))
    lo, hi = entry.get("min_price_cents", 1), entry.get("max_price_cents", 99)
    band = f"between {_num(lo)}¢ and {_num(hi)}¢"
    conds = [_condition_text(c) for c in (entry.get("conditions") or []) if isinstance(c, dict)]
    when = f" when {', '.join(conds)}" if conds else ""
    size = _num(entry.get("size_contracts", 5))
    tail = _exit_text(spec.get("exit") or {})
    cap = _num(risk.get("max_concurrent_positions", 10))
    return (
        f"Buys {side} of {_universe_text(uni)} {style} {band}{when}, "
        f"{size} contracts at a time, and {tail} "
        f"(at most {cap} positions open at once)."
    )


def _section(title: str, items: list[tuple[str, str]]) -> dict:
    return {
        "title": title,
        "items": [{"label": label, "value": value} for label, value in items if value],
    }


def describe_spec(spec: dict) -> dict:
    """Structured, readable breakdown of a StrategySpec — the parameters, filters,
    sizing, entry and exit rules an operator needs to judge what is being tested."""
    if not isinstance(spec, dict) or not spec:
        return {"summary": summarize_spec({}), "sections": [], "raw": None}
    uni = spec.get("universe") or {}
    entry = spec.get("entry") or {}
    ex = spec.get("exit") or {}
    risk = spec.get("risk") or {}

    conds = [_condition_text(c) for c in (entry.get("conditions") or []) if isinstance(c, dict)]
    sections = [
        _section("Market filters", [
            ("Series", _universe_text(uni)),
            ("Minimum volume", _num(uni.get("min_volume", 0))),
            ("Maximum spread", f"{_num(uni.get('max_spread_cents', 99))}¢"),
            ("Time to close", f"{_num(uni.get('min_hours_to_close', 0))}h – "
                              f"{_num(uni.get('max_hours_to_close', 720))}h"),
        ]),
        _section("Entry rules", [
            ("Side", _SIDE_LABELS.get(str(entry.get("side", "yes")), str(entry.get("side")))),
            ("Order style", str(entry.get("style", "taker"))),
            ("Maker offset", f"{_num(entry.get('maker_offset_cents', 0))}¢ inside the bid"
                             if str(entry.get("style")) == "maker" else ""),
            ("Entry price band", f"{_num(entry.get('min_price_cents', 1))}¢ – "
                                 f"{_num(entry.get('max_price_cents', 99))}¢"),
            ("Conditions", "; ".join(conds) if conds else "none (any admitted market)"),
        ]),
        _section("Sizing & exit", [
            ("Size per entry", f"{_num(entry.get('size_contracts', 5))} contracts"),
            ("Exit mode", str(ex.get("mode", "settlement"))),
            ("Exit rule", _exit_text(ex)),
        ]),
        _section("Risk limits", [
            ("Max concurrent positions", _num(risk.get("max_concurrent_positions", 10))),
            ("Max positions per event", _num(risk.get("max_per_event", 1))),
            ("Max cost per position", f"${_num(risk.get('max_cost_per_position_usd', 50))}"),
        ]),
    ]
    return {
        "summary": summarize_spec(spec),
        "sections": [s for s in sections if s["items"]],
        "raw": spec,
    }


# ---------------------------------------------------------------------------
# Trading genome (the bot's stated thesis and rules, independent of any spec)
# ---------------------------------------------------------------------------

# (genome field, display label) in the order an operator wants to read them.
_GENOME_FIELDS = [
    ("entry_rules", "Entry rules"),
    ("exit_rules", "Exit rules"),
    ("holding_period", "Holding period"),
    ("no_trade_rules", "No-trade rules"),
    ("opportunity_selection", "Opportunity selection"),
    ("forecasting_method", "Forecasting method"),
    ("fair_value_method", "Fair-value method"),
    ("probability_model", "Probability model"),
    ("fill_assumptions", "Fill assumptions"),
]
_GENOME_RISK_FIELDS = [
    ("max_position_cost_usd", "Max position cost", "$"),
    ("max_event_cost_usd", "Max event cost", "$"),
    ("max_open_positions", "Max open positions", ""),
    ("max_daily_new_risk_usd", "Max new risk per day", "$"),
]


def describe_genome(doc: dict, *, cap) -> dict:
    """Readable view of the trading genome: the thesis plus the rules and limits the
    bot says it trades by. `cap` is data.py's length-capping helper, applied to every
    agent-authored free-text field."""
    doc = doc if isinstance(doc, dict) else {}
    rules = [
        {"label": label, "value": cap(doc.get(field))}
        for field, label in _GENOME_FIELDS
        if doc.get(field)
    ]
    risk = doc.get("risk") if isinstance(doc.get("risk"), dict) else {}
    limits = [
        {"label": label, "value": f"{prefix}{_num(risk.get(field))}"}
        for field, label, prefix in _GENOME_RISK_FIELDS
        if risk.get(field) is not None
    ]
    sizing = None
    if doc.get("sizing_rule"):
        sizing = f"{doc['sizing_rule']} ({_num(doc.get('sizing_value'))})"
    return {
        "family": doc.get("strategy_family") or "unassigned",
        "thesis": cap(doc.get("thesis")),
        "predictions": [cap(p) for p in (doc.get("falsifiable_predictions") or [])[:5]],
        "rules": rules,
        "limits": limits,
        "sizing": sizing,
        "order_style": doc.get("order_style"),
        "data_dependencies": [str(d)[:64] for d in (doc.get("data_dependencies") or [])[:8]],
    }


# ---------------------------------------------------------------------------
# Lineage: what this bot inherited and what it changed
# ---------------------------------------------------------------------------


def _genome_history(session, agent_uuid: str, limit: int = 6) -> list[EvoGenome]:
    return list(
        session.scalars(
            select(EvoGenome)
            .where(EvoGenome.agent_uuid == agent_uuid, EvoGenome.kind == "trading")
            .order_by(EvoGenome.revision.desc())
            .limit(limit)
        )
    )


def strategy_view(session, agent: EvoAgent, *, cap, iso) -> dict:
    """The full strategy picture for one bot: the declarative spec it is running (if
    any), its trading genome, and its lineage — parent, inherited-from revision, and
    what changed at each of its own recent revisions."""
    active = session.scalar(
        select(EvoStrategy)
        .where(EvoStrategy.agent_uuid == agent.agent_uuid, EvoStrategy.status == "active")
        .order_by(EvoStrategy.id.desc())
        .limit(1)
    )
    # A bot with no ACTIVE spec may still have validated drafts waiting; showing them
    # is the difference between "doing nothing" and "built something it hasn't armed".
    drafts = list(
        session.scalars(
            select(EvoStrategy)
            .where(EvoStrategy.agent_uuid == agent.agent_uuid,
                   EvoStrategy.status.in_(("validated", "draft")))
            .order_by(EvoStrategy.id.desc())
            .limit(5)
        )
    )
    history = _genome_history(session, agent.agent_uuid)
    current = history[0] if history else None
    doc = (current.document_json if current else {}) or {}

    revisions = [
        {
            "revision": g.revision,
            "at": iso(g.created_at),
            "material": bool(g.material),
            "changed": [str(c)[:48] for c in (g.changed_fields_json or [])[:10]],
            "hypothesis": cap(g.hypothesis),
            "is_rollback": bool(g.is_rollback),
        }
        for g in history
    ]

    parent_name = None
    if agent.parent_uuid:
        parent = session.scalar(
            select(EvoAgent).where(EvoAgent.agent_uuid == agent.parent_uuid)
        )
        parent_name = parent.display_name if parent else None

    spec = describe_spec(active.spec_json if active else {})
    return {
        "active_strategy": (
            {
                "name": active.name,
                "revision": active.revision,
                "status": active.status,
                "description": cap((active.spec_json or {}).get("description")),
                "family": (active.spec_json or {}).get("family") or doc.get("strategy_family"),
                "forked_from": active.forked_from_uuid,
                "created_at": iso(active.created_at),
            }
            if active else None
        ),
        "spec": spec,
        "drafts": [
            {"name": s.name, "revision": s.revision, "status": s.status,
             "summary": summarize_spec(s.spec_json or {}), "created_at": iso(s.created_at)}
            for s in drafts
        ],
        "genome": describe_genome(doc, cap=cap),
        "genome_revision": current.revision if current else None,
        "lineage": {
            "parent": parent_name,
            "parent_uuid": agent.parent_uuid,
            "generation": agent.generation,
            "origin": agent.origin,
            "family": agent.surname,
            "revisions": revisions,
        },
    }
