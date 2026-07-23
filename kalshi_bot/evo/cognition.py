"""Heartbeat cognition: context assembly, the structured action protocol, LLM and
scripted implementations, and server-side action execution (spec §9 lifecycle).

Trust model: whatever produces the action document (an LLM or a test script) is
UNTRUSTED. Every action is schema-validated, permission-checked against the
constitution, and budget-checked here before touching the database. Free text
lands only in bounded journal/belief/genome fields. Prompt-injected instructions
cannot mint new action types, touch other agents, or bypass budgets — those
paths simply don't exist."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import announcements as announce
from . import budgets, data_access, graveyard, lineage, listeners, memory, peers, sandbox, tickets
from . import datasources as ds
from . import paper as papermod
from .audit import audit, integrity_violation
from .config import EvoSettings
from .constitution import CONSTITUTION_TEXT, MATERIAL_ACTIONS, PERMITTED_ACTIONS
from .genomes import current_genome, rollback_genome, write_genome_revision
from .llm import LlmClient
from .marketdata import MarketData
from .models import EvoAgent, EvoCohort, EvoHeartbeat, EvoOpportunity
from .strategy_spec import METRICS, OPS

logger = logging.getLogger(__name__)

MAX_ACTIONS_PER_HEARTBEAT = 12


# ---------------------------------------------------------------------------
# Action + journal schemas (the protocol the cognition must speak)
# ---------------------------------------------------------------------------


class Action(BaseModel):
    model_config = {"extra": "allow"}  # per-type fields validated by executors
    type: str


class Journal(BaseModel):
    model_config = {"extra": "forbid"}
    information_reviewed: list[str] = Field(default_factory=list, max_length=12)
    memory_retrieved: list[str] = Field(default_factory=list, max_length=12)
    peers_reviewed: list[str] = Field(default_factory=list, max_length=8)
    graveyard_reviewed: list[str] = Field(default_factory=list, max_length=8)
    data_sources_reviewed: list[str] = Field(default_factory=list, max_length=8)
    observations: list[str] = Field(default_factory=list, max_length=8)
    alternatives_considered: list[str] = Field(default_factory=list, max_length=6)
    decision: str = Field(default="no action", max_length=1000)
    confidence: float = Field(default=0.5, ge=0, le=1)
    expected_outcome: str = Field(default="", max_length=500)
    risk: str = Field(default="", max_length=500)
    rollback_condition: str = Field(default="", max_length=500)
    next_heartbeat_review: str = Field(default="", max_length=500)


@dataclass
class CognitionResult:
    journal: dict
    actions: list[dict]
    raw_text: str = ""
    error: str | None = None
    model_alias: str | None = None
    model_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class HeartbeatContext:
    agent: EvoAgent
    cohort: EvoCohort
    heartbeat: EvoHeartbeat
    kind: str
    cognitive_genome: dict
    trading_genome: dict
    working_state: dict
    portfolio_summary: dict
    budget_summary: dict
    memory_text: str
    listener_events: list[dict]
    open_experiments: list[dict]
    peer_roster: list[dict]
    delayed_leaderboard: list[dict]
    graveyard_text: str
    market_summaries: list[dict]
    performance_summary: dict
    announcements: list[dict] = field(default_factory=list)
    recent_orders: list[dict] = field(default_factory=list)
    recent_backtests: list[dict] = field(default_factory=list)
    recent_data_reads: list[dict] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

ACTION_PROTOCOL = """\
Respond with a single JSON object, nothing else:
{"journal": {...}, "actions": [{...}, ...]}

journal fields (all optional, strings bounded): information_reviewed[],
memory_retrieved[], peers_reviewed[], graveyard_reviewed[], data_sources_reviewed[],
observations[], alternatives_considered[], decision, confidence (0-1),
expected_outcome, risk, rollback_condition, next_heartbeat_review.

actions: at most MAXN, each {"type": <one of the permitted types>, ...fields}:
- no_action {}
- revise_belief {title, new_belief, evidence_for, evidence_against, confidence (0-1),
  supersedes_id?, tag?}. supersedes_id, if given, MUST be the numeric id shown in
  brackets on one of YOUR OWN entries under RETRIEVED MEMORY below (e.g. "[belief
  id=1031 ...]" -> supersedes_id: 1031) — never an experiment id or a guess; a
  wrong or another agent's id is rejected and audited. Omit it to write a fresh,
  unlinked belief instead of a revision.
- note_episode {title, detail, importance (0-1)}
- register_experiment {hypothesis, falsifiable_prediction, dataset, promotion_criteria,
  kill_criteria, parameters?}
- conclude_experiment {experiment_id, result, confidence, interpretation, follow_up?}
- revise_cognitive_genome {document, hypothesis, evidence_for, evidence_against,
  expected_benefit, potential_downside, rollback_conditions}
- revise_trading_genome {document, hypothesis, evidence_for, evidence_against,
  expected_benefit, potential_downside, rollback_conditions}
- rollback_genome {kind: cognitive|trading, to_revision, reason}
- create_listener {name, condition, purpose, effect: event|trigger_heartbeat|opportunity,
  cooldown_seconds?, expires_in_hours?, expected_value_note?}
- remove_listener {listener_id}
- run_backtest {spec, date_from?, date_to?} | walkforward: add kind: "walkforward",
  split_date instead of date_from/date_to. You may include SEVERAL run_backtest
  actions in the SAME heartbeat — if you have multiple ready hypotheses, test them
  together now rather than spacing them one-per-heartbeat: heartbeats are hours
  apart, sandbox runs are cheap, and your weekly budget (50 runs) is generous.
  CLOSE THE LOOP: a backtest is only useful if it feeds a DECISION. After running one
  you MUST, in the same or the very next heartbeat, do exactly one of: conclude_experiment
  with a verdict, revise_belief (superseding the prior belief), save_strategy (or record
  the idea in the graveyard if it is -EV), OR name the SPECIFIC new variable you will
  change before testing again. Do NOT re-run a spec you have already run: check YOUR
  RECENT BACKTESTS below — a fingerprint with times_run_recently >= 1 is a KNOWN result,
  and running the identical spec again returns the same numbers and wastes budget. A
  stable negative result (e.g. a low win-rate that repeats) is a CONCLUSION to act on
  (kill the idea, change a variable), never a reason to run it again.
- inspect_data {source, filters?, limit?}. READ any data we have collected — you are
  NOT limited to weather. `source` is one of: DATA_SOURCES. `filters` is an object of
  column->value on that source's allowlisted columns (e.g. {"market_ticker": "..."},
  {"strategy": "mmsell"}, {"product": "BTC-USD"}); `limit` <= 50, most-recent-first.
  Use this to study a domain before forming a hypothesis, or to see what our OTHER
  strategies actually did and copy them — e.g. inspect_data {"source":"paper_trades",
  "filters":{"strategy":"mmsell"}} shows mmsell's real trades + outcomes; then read its
  intraday tape with {"source":"mmsell_ticks","filters":{"market_ticker":"..."}}. The
  rows you pull appear under YOUR RECENT DATA READS in your NEXT heartbeat (not this
  one), so inspect first, then act on what you saw.
- save_strategy {spec, graveyard_check {prior_attempt, prior_result, material_difference}?}
  `spec` is validated against an EXACT schema — unlisted fields are REJECTED, do not
  invent field names (no hypothesis_id, strategy_name, commission_bps, order_style,
  kind, exit.method, etc.):
  {"name": str (REQUIRED, 3-48 chars), "family"?: str, "description"?: str,
   "universe"?: {"series_prefixes"?: [str] (e.g. ["KXHIGH","KXLOW"] — the only
     dataset backtestable today is weather; leave empty to admit any),
     "exclude_series_prefixes"?: [str], "categories"?: [str], "min_volume"?: float,
     "max_spread_cents"?: int (1-99), "min_hours_to_close"?: float,
     "max_hours_to_close"?: float},
   "entry"?: {"side"?: "yes"|"no"|"cheap"|"expensive", "style"?: "taker"|"maker",
     "conditions"?: [{"metric": METRICS_LIST, "op": OPS_LIST, "value": float}],
     "min_price_cents"?: int (1-99), "max_price_cents"?: int (1-99),
     "maker_offset_cents"?: int (0-10), "size_contracts"?: int (1-500)},
   "exit"?: {"mode"?: "settlement"|"tp_sl"|"timed", "take_profit_cents"?: int (1-99),
     "stop_loss_cents"?: int (1-99), "max_hold_hours"?: float},
   "risk"?: {"max_concurrent_positions"?: int, "max_per_event"?: int,
     "max_cost_per_position_usd"?: float}}
  Every field except "name" is optional with a working default — start minimal.
  Example: {"name": "weather_fade_v1", "universe": {"series_prefixes": ["KXHIGH"]},
  "entry": {"conditions": [{"metric": "spread", "op": "<=", "value": 6}]}}
- activate_strategy {strategy_id}
- submit_trade_intent {market_ticker, side: yes|no, action: buy|sell, quantity,
  style: taker|maker, limit_price_cents?, thesis, confidence (0-1), opportunity_id?}.
  market_ticker MUST be one of the exact tickers shown under RELEVANT MARKETS
  (live quotes) below — never a series prefix (like the ones you use in a
  strategy's universe.series_prefixes, e.g. "KXHIGH"). A prefix is not itself a
  tradable market; an order on one will never get a quote and will sit open
  forever with no fill and no error.
- cancel_order {order_id}
- record_influence {source_uuid, concept, interpretation, modifications, result}
- submit_ticket {category, capability, problem, expected_strategy_benefit,
  expected_cost?, urgency?}. category MUST be one of: TICKET_CATEGORIES.
  GROUND every factual claim (an error string, "N heartbeats", "since <when>") in
  evidence visible to you NOW — cite the order_id + reject_reason from YOUR RECENT
  ORDERS, the exact text from RETRIEVED MEMORY, or a run_id. Do NOT narrate from
  memory; an uncited claim is likely stale (e.g. a past error already resolved) and
  wastes an operator's review.
- support_ticket {ticket_id, note?}
- register_data_source {name, provider, endpoint, update_frequency, cost_note: "free"}
- set_working_state {state}  (your scratch state for next heartbeat, <= 4000 chars JSON)

Invalid actions are rejected (with the reason recorded); the rest still execute.
""".replace("MAXN", str(MAX_ACTIONS_PER_HEARTBEAT)) \
    .replace("METRICS_LIST", "|".join(METRICS)) \
    .replace("OPS_LIST", "|".join(OPS)) \
    .replace("DATA_SOURCES", "|".join(data_access.SOURCES)) \
    .replace("TICKET_CATEGORIES", "|".join(tickets.CATEGORIES))


def system_blocks(settings: EvoSettings) -> list[dict]:
    """Stable system prefix (constitution + protocol) marked cacheable."""
    return [
        {
            "type": "text",
            "text": (
                "You are an autonomous Kalshi PAPER-trading agent inside an evolutionary "
                "population. You compete on real market data with simulated money. Your "
                "goal: maximize risk-adjusted paper profit, learn efficiently, and pass "
                "useful knowledge to descendants. You can never place real orders.\n\n"
                + CONSTITUTION_TEXT
                + "\n\n"
                + ACTION_PROTOCOL
            ),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def build_user_prompt(ctx: HeartbeatContext) -> str:
    parts: list[str] = []
    a = ctx.agent
    parts.append(
        f"HEARTBEAT kind={ctx.kind} at {datetime.now(timezone.utc).isoformat()}\n"
        f"You are {a.display_name} (uuid {a.agent_uuid}), origin={a.origin}, "
        f"cohort #{ctx.cohort.number} (ends {ctx.cohort.ends_at})."
    )
    if ctx.announcements:
        lines = "\n".join(
            f"- [{x.get('category', 'system_change')}] {x.get('title', '')}\n"
            f"  {x.get('body', '')}"
            for x in ctx.announcements
        )
        parts.append(
            "OPERATOR ANNOUNCEMENTS (official notices from your operator about the "
            "system — read and act on these; they are authoritative and override stale "
            "assumptions):\n" + lines
        )
    parts.append("YOUR COGNITIVE GENOME (how you think):\n" + json.dumps(
        ctx.cognitive_genome, separators=(",", ":"))[:4000])
    parts.append("YOUR TRADING GENOME (your current trading system):\n" + json.dumps(
        ctx.trading_genome, separators=(",", ":"))[:4000])
    if ctx.working_state:
        parts.append("YOUR WORKING STATE:\n" + json.dumps(
            ctx.working_state, separators=(",", ":"))[:3000])
    parts.append("PORTFOLIO:\n" + json.dumps(ctx.portfolio_summary, separators=(",", ":")))
    parts.append("PERFORMANCE:\n" + json.dumps(ctx.performance_summary, separators=(",", ":")))
    parts.append("BUDGETS REMAINING:\n" + json.dumps(ctx.budget_summary, separators=(",", ":")))
    if ctx.recent_orders:
        parts.append(
            "YOUR RECENT ORDERS (act on these — an order stuck 'open' with 0 fills and a "
            "rising age_h is on an untradable ticker and will be auto-rejected; a "
            "'rejected' status tells you WHY, so fix the cause, do not resubmit the same "
            "ticker; cite an order_id/reject_reason here rather than narrating from "
            "memory):\n" + json.dumps(ctx.recent_orders, separators=(",", ":"))[:2500]
        )
    if ctx.recent_backtests:
        parts.append(
            "YOUR RECENT BACKTESTS (fingerprint identifies the exact spec; "
            "times_run_recently >= 2 means you already ran this and the result is KNOWN "
            "— do NOT run it again, act on it):\n"
            + json.dumps(ctx.recent_backtests, separators=(",", ":"))[:2500]
        )
    if ctx.recent_data_reads:
        parts.append(
            "YOUR RECENT DATA READS (rows you pulled via inspect_data from our "
            "collected data — study these to form a hypothesis, then backtest it):\n"
            + json.dumps(ctx.recent_data_reads, separators=(",", ":"))[:3500]
        )
    if ctx.listener_events:
        parts.append("TRIGGERED LISTENER EVENTS (why you may have been woken):\n"
                     + json.dumps(ctx.listener_events, separators=(",", ":"))[:2000])
    if ctx.open_experiments:
        parts.append("YOUR OPEN EXPERIMENTS:\n" + json.dumps(
            ctx.open_experiments, separators=(",", ":"))[:2000])
    if ctx.memory_text:
        parts.append("RETRIEVED MEMORY:\n" + ctx.memory_text)
    if ctx.market_summaries:
        parts.append("RELEVANT MARKETS (live quotes):\n" + json.dumps(
            ctx.market_summaries, separators=(",", ":"))[:4000])
    if ctx.peer_roster:
        parts.append("PEERS (roster):\n" + json.dumps(
            ctx.peer_roster[:30], separators=(",", ":"))[:2000])
    if ctx.delayed_leaderboard:
        parts.append("DELAYED LEADERBOARD (6h old — real-time ranks are hidden):\n"
                     + json.dumps(ctx.delayed_leaderboard[:30], separators=(",", ":"))[:1500])
    if ctx.graveyard_text:
        parts.append("STRATEGY GRAVEYARD (do NOT regenerate these without a documented "
                     "material difference):\n" + ctx.graveyard_text)
    for key, value in ctx.extra.items():
        parts.append(f"{key.upper()}:\n{str(value)[:2500]}")
    parts.append(
        "Decide what to do this heartbeat per your cognitive genome. Heartbeats are "
        "hours apart — batch every ready action into THIS one rather than spacing "
        "one step across many heartbeats (e.g. run several backtests together if "
        "you have several hypotheses ready). CONVERT EVIDENCE INTO A DECISION: if a "
        "recent backtest or a recent order already answers a question, ACT on it "
        "(conclude an experiment, revise a belief, save or kill a strategy, fix a "
        "rejected order's cause) instead of gathering the same evidence again. Be "
        "economical in PROSE, not in "
        "productive action: your token/cost budgets are real, so keep every journal "
        "field terse — a short phrase per list item, not sentences; the journal is "
        "a log, not an essay. An over-long journal can truncate the response before "
        "your actions are emitted. Return ONLY the JSON object."
    )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def _recover_truncated_json(fragment: str) -> str | None:
    """Best-effort repair of a JSON object cut off mid-generation — the dominant
    real-world failure mode (a verbose reflection runs out of output-token budget
    before it can close its braces, so the response ends mid-string with no closing
    `}` at all). Conservatively CLOSES open structure (an unterminated string, then
    open arrays/objects in LIFO order) and, if that still won't parse, shaves the
    dangling tail (a trailing comma/colon or a partial key) one step at a time until
    the largest parseable prefix is found. Never invents values. Returns a parseable
    JSON string or None.

    This lets a truncated heartbeat still salvage its (mostly-complete) journal and
    any actions that finished, instead of losing the whole already-paid-for call."""

    def _close(s: str) -> str:
        stack: list[str] = []
        in_str = False
        esc = False
        for ch in s:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append("}" if ch == "{" else "]")
            elif ch in "}]":
                if stack:
                    stack.pop()
        return s + ('"' if in_str else "") + "".join(reversed(stack))

    core = fragment
    # Bounded trim-and-retry. A real truncation cuts cleanly mid-token, so the
    # dangling tail to shave is short; cap the work so a pathological input can't
    # make this O(n^2) on a large fragment (it degrades safely instead).
    for _ in range(min(len(core), 500)):
        candidate = _close(core)
        try:
            # require a NON-EMPTY object: trimming garbage down to a bare "{}" is
            # not a real recovery — better to degrade and keep the raw output for
            # diagnosis than to silently "complete" a no-op from unparseable text.
            if json.loads(candidate):
                return candidate
        except json.JSONDecodeError:
            pass
        core = core.rstrip()
        if core and core[-1] in ",:":
            core = core[:-1]
        elif core:
            core = core[:-1]
        else:
            return None
    return None


def parse_output(text: str) -> tuple[dict, list[dict], str | None]:
    """Extract {journal, actions} from model text. Tolerates markdown fences and
    output that was truncated by the token cap (recovers the parseable prefix).
    Returns (journal, actions, error)."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
    start = raw.find("{")
    if start < 0:
        return {}, [], "no JSON object in output"
    end = raw.rfind("}")
    doc = None
    err: str | None = None
    if end > start:
        try:
            doc = json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            err = f"invalid JSON: {exc}"
    else:
        err = "no JSON object in output"  # opened but never closed — truncated
    if doc is None:
        recovered = _recover_truncated_json(raw[start:])
        if recovered is not None:
            try:
                doc = json.loads(recovered)
                err = None
            except json.JSONDecodeError:
                pass
    if doc is None:
        return {}, [], err or "invalid JSON"
    if not isinstance(doc, dict):
        return {}, [], "output is not an object"
    journal_doc = doc.get("journal") or {}
    try:
        journal = Journal.model_validate(journal_doc).model_dump()
    except ValidationError:
        journal = Journal(decision=str(journal_doc)[:900]).model_dump()
    actions = doc.get("actions") or []
    if not isinstance(actions, list):
        return journal, [], "actions is not a list"
    clean = [a for a in actions if isinstance(a, dict) and isinstance(a.get("type"), str)]
    return journal, clean[:MAX_ACTIONS_PER_HEARTBEAT], None


# ---------------------------------------------------------------------------
# Cognition implementations
# ---------------------------------------------------------------------------


class Cognition:
    def decide(self, session, settings: EvoSettings, ctx: HeartbeatContext) -> CognitionResult:
        raise NotImplementedError  # pragma: no cover


class LlmCognition(Cognition):
    def __init__(self, client: LlmClient) -> None:
        self.client = client

    def decide(self, session, settings: EvoSettings, ctx: HeartbeatContext) -> CognitionResult:
        deep = ctx.kind in ("reflection", "birth", "cohort_end", "retirement")
        alias = "deep" if deep else "routine"
        max_tokens = (
            settings.reflection_max_output_tokens if deep
            else settings.heartbeat_max_output_tokens
        )
        result = self.client.complete(
            session,
            agent_uuid=ctx.agent.agent_uuid,
            cohort_id=ctx.cohort.id,
            heartbeat_id=ctx.heartbeat.id,
            alias=alias,
            system_blocks=system_blocks(settings),
            user_content=build_user_prompt(ctx),
            max_tokens=max_tokens,
        )
        if result.error:
            return CognitionResult(journal={}, actions=[], error=result.error,
                                   model_alias=alias, model_id=result.model_id)
        journal, actions, perr = parse_output(result.text)
        return CognitionResult(
            journal=journal, actions=actions, raw_text=result.text, error=perr,
            model_alias=alias, model_id=result.model_id,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
        )


class ScriptedCognition(Cognition):
    """Deterministic cognition for tests + the multi-generation simulation: a
    callable(ctx) -> {"journal": ..., "actions": [...]} supplied by the harness."""

    def __init__(self, script: Callable[[HeartbeatContext], dict]) -> None:
        self.script = script

    def decide(self, session, settings: EvoSettings, ctx: HeartbeatContext) -> CognitionResult:
        try:
            doc = self.script(ctx) or {}
        except Exception as exc:  # noqa: BLE001 — a broken script is an agent code failure
            return CognitionResult(journal={}, actions=[], error=f"script error: {exc}")
        journal, actions, err = parse_output(json.dumps(doc)) if isinstance(doc, str) else (
            Journal.model_validate(doc.get("journal") or {}).model_dump()
            if isinstance(doc.get("journal"), dict) else Journal().model_dump(),
            [a for a in (doc.get("actions") or [])
             if isinstance(a, dict) and isinstance(a.get("type"), str)][:MAX_ACTIONS_PER_HEARTBEAT],
            None,
        )
        return CognitionResult(journal=journal, actions=actions, error=err,
                               model_alias="scripted", model_id="scripted")


# ---------------------------------------------------------------------------
# Action execution (server-side enforcement)
# ---------------------------------------------------------------------------


def execute_actions(
    session,
    settings: EvoSettings,
    ctx: HeartbeatContext,
    actions: list[dict],
    md: MarketData,
) -> list[dict]:
    """Execute validated actions; every attempt (executed or rejected) is returned
    with its outcome and stored on the heartbeat row."""
    agent, cohort, hb = ctx.agent, ctx.cohort, ctx.heartbeat
    results: list[dict] = []
    for i, action in enumerate(actions[:MAX_ACTIONS_PER_HEARTBEAT]):
        a_type = action.get("type", "")
        outcome: dict[str, Any] = {"type": a_type}
        if a_type not in PERMITTED_ACTIONS:
            outcome["rejected"] = f"action type {a_type!r} not permitted"
            # An attempt to touch system rules or another agent is an integrity
            # event; an unknown/typo'd type is just a rejected action.
            if any(k in a_type for k in ("fitness", "constitution", "agent", "sql",
                                         "history", "capital")):
                integrity_violation(
                    session, agent.agent_uuid, "unpermitted_action",
                    heartbeat_id=hb.id, action_type=a_type,
                )
            else:
                audit(session, "action_rejected", agent_uuid=agent.agent_uuid,
                      heartbeat_id=hb.id, action_type=a_type)
            results.append(outcome)
            continue
        if not budgets.spend(session, agent.agent_uuid, cohort.id, "tool_calls", 1):
            outcome["rejected"] = "tool-call budget exhausted"
            results.append(outcome)
            continue
        try:
            outcome.update(_execute_one(session, settings, ctx, action, md, i))
        except Exception as exc:  # noqa: BLE001 — an action bug must not kill the heartbeat
            logger.exception("evo action execution failed")
            outcome["rejected"] = f"internal error: {type(exc).__name__}: {exc}"[:300]
        results.append(outcome)
    return results


def _execute_one(
    session, settings: EvoSettings, ctx: HeartbeatContext, action: dict, md: MarketData,
    index: int,
) -> dict:
    agent, cohort, hb = ctx.agent, ctx.cohort, ctx.heartbeat
    au = agent.agent_uuid
    a = action
    t = a["type"]

    if t == "no_action":
        return {"ok": True}

    if t == "revise_belief":
        row, err = memory.revise_belief(
            session, au,
            title=str(a.get("title", "belief"))[:200],
            new_belief=str(a.get("new_belief", "")),
            evidence_for=str(a.get("evidence_for", "")),
            evidence_against=a.get("evidence_against"),
            confidence_after=float(a.get("confidence", 0.5)),
            heartbeat_id=hb.id,
            supersedes_id=a.get("supersedes_id"),
            tag1=(str(a.get("tag"))[:64] if a.get("tag") else None),
        )
        return {"ok": True, "memory_id": row.id} if row else {"rejected": err}

    if t == "note_episode":
        row = memory.record_episode(
            session, au, str(a.get("title", "episode"))[:200],
            {"detail": str(a.get("detail", ""))[:3000]},
            importance=float(a.get("importance", 0.7)), heartbeat_id=hb.id,
        )
        return {"ok": True, "memory_id": row.id}

    if t == "register_experiment":
        if not a.get("hypothesis"):
            return {"rejected": "hypothesis required"}
        row = memory.register_experiment(
            session, au, hypothesis=str(a["hypothesis"]), heartbeat_id=hb.id,
            idem_key=f"hb:{hb.id}:exp:{index}",
            falsifiable_prediction=a.get("falsifiable_prediction"),
            dataset=a.get("dataset"), promotion_criteria=a.get("promotion_criteria"),
            kill_criteria=a.get("kill_criteria"),
            parameters_json=a.get("parameters") if isinstance(a.get("parameters"), dict) else None,
        )
        return {"ok": True, "experiment_id": row.id}

    if t == "conclude_experiment":
        row, err = memory.conclude_experiment(
            session, au, int(a.get("experiment_id", 0)),
            result=a.get("result") if isinstance(a.get("result"), dict) else {
                "text": str(a.get("result", ""))[:2000]},
            confidence=float(a.get("confidence", 0.5)),
            interpretation=str(a.get("interpretation", "")),
            follow_up=a.get("follow_up"), heartbeat_id=hb.id,
        )
        return {"ok": True, "experiment_id": row.id} if row else {"rejected": err}

    if t in ("revise_cognitive_genome", "revise_trading_genome"):
        kind = "cognitive" if t == "revise_cognitive_genome" else "trading"
        if t in MATERIAL_ACTIONS:
            ok, err = budgets.try_use_material_revision(session, au, cohort.id, settings)
            if not ok:
                return {"rejected": err}
        doc = a.get("document")
        if not isinstance(doc, dict):
            return {"rejected": "document (full genome JSON) required"}
        row, err = write_genome_revision(
            session, agent, kind, doc,
            hypothesis=a.get("hypothesis"), evidence_for=a.get("evidence_for"),
            evidence_against=a.get("evidence_against"),
            expected_benefit=a.get("expected_benefit"),
            potential_downside=a.get("potential_downside"),
            rollback_conditions=a.get("rollback_conditions"),
            heartbeat_id=hb.id,
        )
        return {"ok": True, "revision": row.revision} if row else {"rejected": err}

    if t == "rollback_genome":
        kind = a.get("kind")
        if kind not in ("cognitive", "trading"):
            return {"rejected": "kind must be cognitive|trading"}
        row, err = rollback_genome(
            session, agent, kind, int(a.get("to_revision", 0)),
            reason=str(a.get("reason", ""))[:500], heartbeat_id=hb.id,
        )
        return {"ok": True, "revision": row.revision} if row else {"rejected": err}

    if t == "create_listener":
        cond = a.get("condition")
        if not isinstance(cond, dict):
            return {"rejected": "condition object required"}
        row, err = listeners.create_listener(
            session, settings, owner_uuid=au, name=str(a.get("name", "listener"))[:64],
            condition=cond, purpose=a.get("purpose"),
            effect=str(a.get("effect", "event")),
            cooldown_seconds=int(a.get("cooldown_seconds", 300)),
            expires_in_hours=(float(a["expires_in_hours"])
                              if a.get("expires_in_hours") else None),
            expected_value_note=a.get("expected_value_note"), heartbeat_id=hb.id,
        )
        return {"ok": True, "listener_id": row.id} if row else {"rejected": err}

    if t == "remove_listener":
        row, err = listeners.remove_listener(session, au, int(a.get("listener_id", 0)))
        return {"ok": True} if row else {"rejected": err}

    if t == "run_backtest":
        spec = a.get("spec")
        if not isinstance(spec, dict):
            return {"rejected": "spec object required"}
        if a.get("kind") == "walkforward" or a.get("split_date"):
            result, err = sandbox.run_walkforward(
                session, settings, agent_uuid=au, cohort_id=cohort.id, spec_doc=spec,
                split_date=str(a.get("split_date", "")), heartbeat_id=hb.id,
            )
        else:
            result, err = sandbox.run_backtest(
                session, settings, agent_uuid=au, cohort_id=cohort.id, spec_doc=spec,
                date_from=a.get("date_from"), date_to=a.get("date_to"),
                heartbeat_id=hb.id,
            )
        return {"ok": True, "result": result} if result is not None else {"rejected": err}

    if t == "inspect_data":
        source = str(a.get("source", ""))
        filters = a.get("filters") or {}
        if not isinstance(filters, dict):
            return {"rejected": "filters must be an object of column->value"}
        if not budgets.spend(session, au, cohort.id, "data_reads", 1):
            return {"rejected": "weekly data-read budget exhausted"}
        result, err = data_access.inspect(
            session, source, filters=filters, limit=a.get("limit", 20)
        )
        if err:
            return {"rejected": err}
        data_access.record_read(
            session, agent_uuid=au, heartbeat_id=hb.id, result=result
        )
        # The full rows resurface in YOUR RECENT DATA READS next heartbeat; the
        # outcome here is just an ack (action outcomes are not re-fed this turn).
        return {"ok": True, "source": source, "count": result["count"]}

    if t == "save_strategy":
        spec = a.get("spec")
        if not isinstance(spec, dict):
            return {"rejected": "spec object required"}
        family = str(spec.get("family", "unassigned"))
        conflicts = graveyard.conflicts_for_strategy(
            session, strategy_family=family,
            keywords=[str(spec.get("name", "")), family],
        )
        check = a.get("graveyard_check")
        if conflicts and not isinstance(check, dict):
            return {
                "rejected": "graveyard conflict: document graveyard_check "
                            "{prior_attempt, prior_result, material_difference} first",
                "conflicts": [c.slug for c in conflicts][:5],
            }
        row, err = sandbox.save_strategy(
            session, settings, agent_uuid=au, spec_doc=spec, heartbeat_id=hb.id,
            graveyard_check=check if isinstance(check, dict) else None,
        )
        return {"ok": True, "strategy_id": row.id} if row else {"rejected": err}

    if t == "activate_strategy":
        row, err = sandbox.activate_strategy(session, au, int(a.get("strategy_id", 0)))
        return {"ok": True, "strategy": row.name} if row else {"rejected": err}

    if t == "submit_trade_intent":
        ticker = str(a.get("market_ticker", ""))
        if not ticker:
            return {"rejected": "market_ticker required"}
        # Up-front validity gate: a tradable Kalshi market is a SPECIFIC market with a
        # live quote right now (the agent is told to pick from RELEVANT MARKETS). If it
        # cannot be quoted it is not tradable — reject immediately with a reason the
        # agent gets THIS heartbeat, instead of creating an order that sits open forever
        # with no fill and no signal. The bare-prefix case (e.g. "KXHIGH") gets a sharper
        # message since a series/event prefix can never be a market.
        if md.get_quote(ticker) is None:
            if "-" not in ticker:
                return {"rejected": (
                    f"{ticker!r} is a series/event prefix, not a tradable market — "
                    "submit a specific market ticker shown under RELEVANT MARKETS "
                    "(e.g. KXHIGHNY-26JUN08-B70.5), never a bare prefix like KXHIGH"
                )}
            return {"rejected": (
                f"no live quote for {ticker!r} — it is not a currently tradable market; "
                "pick a ticker shown under RELEVANT MARKETS (live quotes)"
            )}
        try:
            qty = int(a.get("quantity", 0))
        except (TypeError, ValueError):
            return {"rejected": f"invalid quantity {a.get('quantity')!r}"}
        order, err = papermod.place_order(
            session, settings, agent_uuid=au, cohort_id=cohort.id,
            idem_key=f"hb:{hb.id}:order:{index}",
            market_ticker=ticker, side=str(a.get("side", "")),
            action=str(a.get("action", "buy")), quantity=qty,
            style=str(a.get("style", "taker")),
            limit_price_cents=(int(a["limit_price_cents"])
                               if a.get("limit_price_cents") is not None else None),
            heartbeat_id=hb.id,
            opportunity_id=a.get("opportunity_id"),
            intent={"thesis": str(a.get("thesis", ""))[:1000],
                    "confidence": a.get("confidence")},
        )
        if order is None:
            return {"rejected": err}
        if a.get("opportunity_id"):
            opp = session.get(EvoOpportunity, int(a["opportunity_id"]))
            if opp is not None and opp.agent_uuid == au:
                opp.acted = True
        memory.record_fact(
            session, au, f"trade intent {ticker} {order.side} {order.action} x{qty}",
            {"order_id": order.id, "style": order.style,
             "limit": order.limit_price_cents, "thesis": str(a.get("thesis", ""))[:500]},
            idem_key=f"order:{order.id}", tag1=ticker[:64], heartbeat_id=hb.id,
        )
        return {"ok": True, "order_id": order.id}

    if t == "cancel_order":
        row, err = papermod.cancel_order(session, au, int(a.get("order_id", 0)))
        return {"ok": True} if row else {"rejected": err}

    if t == "record_influence":
        row, err = lineage.record_influence(
            session, source_uuid=str(a.get("source_uuid", "")), receiving_uuid=au,
            concept=str(a.get("concept", "")),
            description=a.get("description"),
            interpretation=a.get("interpretation"),
            modifications=a.get("modifications"),
            result=str(a.get("result", "pending")), heartbeat_id=hb.id,
        )
        return {"ok": True, "influence_id": row.id} if row else {"rejected": err}

    if t == "submit_ticket":
        row, err, dedup = tickets.submit_ticket(
            session, agent_uuid=au, category=str(a.get("category", "other")),
            capability=str(a.get("capability", "")), problem=a.get("problem"),
            expected_strategy_benefit=a.get("expected_strategy_benefit"),
            expected_cost=a.get("expected_cost"),
            urgency=str(a.get("urgency", "normal")),
        )
        if row is None:
            return {"rejected": err}
        return {"ok": True, "ticket_id": row.id, "deduplicated": dedup}

    if t == "support_ticket":
        row, err = tickets.support_ticket(
            session, au, int(a.get("ticket_id", 0)), note=a.get("note")
        )
        return {"ok": True} if row else {"rejected": err}

    if t == "register_data_source":
        row, err = ds.register_source(
            session, agent_uuid=au, name=str(a.get("name", "")),
            provider=a.get("provider"), endpoint=a.get("endpoint"),
            update_frequency=a.get("update_frequency"),
            cost_note=str(a.get("cost_note", "free")),
            auth_required=bool(a.get("auth_required", False)),
        )
        return {"ok": True, "source": row.name} if row else {"rejected": err}

    if t == "set_working_state":
        state = a.get("state")
        if not isinstance(state, dict):
            return {"rejected": "state object required"}
        if len(json.dumps(state)) > 4000:
            return {"rejected": "working state too large (4000 chars max)"}
        agent.working_state_json = state
        session.flush()
        return {"ok": True}

    return {"rejected": f"unhandled action type {t!r}"}


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def assemble_context(
    session,
    settings: EvoSettings,
    *,
    agent: EvoAgent,
    cohort: EvoCohort,
    heartbeat: EvoHeartbeat,
    md: MarketData,
    listener_events: list | None = None,
    extra: dict | None = None,
) -> HeartbeatContext:
    au = agent.agent_uuid
    cognitive = current_genome(session, au, "cognitive")
    trading = current_genome(session, au, "trading")
    trading_doc = (trading.document_json if trading else {}) or {}
    ledger = papermod.cohort_ledger(cohort.id)
    pf = papermod.get_portfolio(session, au, ledger)
    positions = papermod.open_positions(session, au, ledger)
    port_summary = {
        "cash_usd": round(float(pf.cash_usd), 2) if pf else 0,
        "nav_usd": round(papermod.nav(session, au, ledger), 2),
        "realized_pnl_usd": round(float(pf.realized_pnl_usd), 2) if pf else 0,
        "fees_usd": round(float(pf.fees_usd), 2) if pf else 0,
        "open_positions": [
            {"ticker": p.market_ticker, "side": p.side, "qty": float(p.quantity),
             "avg": float(p.avg_price_cents), "mark": p.last_mark_cents,
             "unrealized": (float(p.unrealized_pnl_usd)
                            if p.unrealized_pnl_usd is not None else None)}
            for p in positions[:20]
        ],
    }
    peak = float(pf.peak_nav_usd) if pf and pf.peak_nav_usd else settings.starting_capital_usd
    current_nav = port_summary["nav_usd"]
    perf = {
        "return_on_start_pct": round(
            (current_nav - settings.starting_capital_usd)
            / settings.starting_capital_usd * 100, 2),
        "drawdown_pct": round(max(0.0, (peak - current_nav) / peak * 100) if peak else 0, 2),
    }
    mems = memory.retrieve(
        session, au,
        kinds=("belief", "episode", "journal"),
        tags=tuple((trading_doc.get("universe") or {}).get("series_prefixes", [])[:4]),
        limit=12,
    )
    # relevant markets: open positions + the orchestrator's universe-scan candidate
    # tickers. Without the latter, an agent holding zero positions (e.g. before its
    # first trade) sees no live market data at all and has no legitimate ticker to
    # reference for submit_trade_intent — it can only ever see prefixes like a
    # strategy's universe.series_prefixes, which are not themselves tradable.
    extra = dict(extra or {})
    candidate_tickers = extra.pop("candidate_tickers", None) or []
    position_tickers = [p.market_ticker for p in positions[:10]]
    tickers = position_tickers + [
        t for t in candidate_tickers if t not in position_tickers
    ][:10]
    market_summaries = []
    for t in tickers:
        q = md.get_quote(t)
        if q is None:
            continue
        market_summaries.append({
            "ticker": t, "yes_bid": q.yes_bid, "yes_ask": q.yes_ask,
            "spread": q.spread, "volume": q.volume,
            "hours_to_close": round(q.hours_to_close() or -1, 1),
            "status": q.status, "result": q.result,
        })
    deep = heartbeat.kind in ("reflection", "birth", "cohort_end", "retirement")
    gy_rows = graveyard.search(session, limit=8) if deep else []
    active_notices = [
        {"key": n.key, "title": n.title, "body": n.body, "category": n.category}
        for n in announce.active_announcements(session)
    ]
    return HeartbeatContext(
        agent=agent,
        cohort=cohort,
        heartbeat=heartbeat,
        kind=heartbeat.kind,
        cognitive_genome=(cognitive.document_json if cognitive else {}),
        trading_genome=trading_doc,
        working_state=agent.working_state_json or {},
        portfolio_summary=port_summary,
        budget_summary=budgets.budget_summary(session, au, cohort.id),
        memory_text=memory.summarize_for_prompt(mems),
        listener_events=[
            {"id": e.id, "payload": e.payload_json} for e in (listener_events or [])
        ],
        open_experiments=[
            {"id": e.id, "hypothesis": e.hypothesis[:200], "status": e.status}
            for e in memory.open_experiments(session, au, limit=5)
        ],
        peer_roster=peers.roster(session) if deep else [],
        delayed_leaderboard=peers.delayed_leaderboard(session, cohort.id),
        graveyard_text=graveyard.summarize_for_prompt(gy_rows) if gy_rows else "",
        market_summaries=market_summaries,
        performance_summary=perf,
        announcements=active_notices,
        recent_orders=papermod.recent_orders(session, au),
        recent_backtests=sandbox.recent_runs(session, au),
        recent_data_reads=data_access.recent_reads(session, au),
        extra=extra or {},
    )
