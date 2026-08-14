"""Capability-request queue (spec §16): submission with semantic dedup (a near-
duplicate converts the submitter into a supporter of the existing ticket),
unique supporter rows, the human-review summary used by the evo digest, and
resolution — including auto-closing tickets whose capability has since shipped.

Resolution was missing for the queue's whole life. `EvoTicket.status` always carried
`open | in_review | approved | rejected | implemented | duplicate` plus `human_decision`
and `implementation_result`, and nothing ever wrote them, so the queue could only grow.
It showed: the fleet filed ~25 tickets for an off switch between 2026-07-31 and
2026-08-08, `deactivate_strategy` shipped 2026-08-08, and all of them were still open
five days later — burying the live requests (a CPI backtest corpus) under delivered ones.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select

from .audit import audit
from .models import EvoAgent, EvoTicket, EvoTicketSupporter

CATEGORIES = (
    "external_data_pipeline", "paid_data_source", "api_credentials",
    "shared_code_capability", "sandbox_operator", "database_schema",
    "stored_information", "data_collection", "platform_deployment",
    "infrastructure", "dashboard", "research_tooling", "performance",
    "bug_report", "security", "permissions", "other",
)

_STOPWORDS = frozenset(
    "a an the for to of in on with and or we i need want would like please new add".split()
)
DEDUP_JACCARD = 0.6

# The closed set from the model comment. Validated on write so a typo cannot invent a
# state no query filters on, which would hide a ticket from every view at once.
TICKET_STATUSES = ("open", "in_review", "approved", "rejected", "implemented", "duplicate")


@dataclass(frozen=True)
class ShippedCapability:
    """A capability agents asked for that now EXISTS, so their tickets can be closed.

    Matching is deliberately a hand-written token predicate rather than fuzzy similarity.
    A wrong auto-close silently discards an agent's request; being too shy only leaves a
    ticket for the operator to read. So: every group in `all_of` must contribute at least
    one token, and nothing in `none_of` may appear. Sharing a single word with a shipped
    capability is not evidence — `strategy_execution` and `strategy_management` sit in the
    same queue as `strategy_deactivation` and must survive untouched.
    """

    action: str          # the PERMITTED_ACTIONS entry that now exists
    shipped_on: str      # ISO date, so a closed ticket stays auditable
    note: str            # what to write into implementation_result
    all_of: tuple[frozenset[str], ...]
    none_of: frozenset[str] = field(default_factory=frozenset)


SHIPPED_CAPABILITIES: tuple[ShippedCapability, ...] = (
    # The off-switch wave: ~25 tickets across 8 categories, 2026-07-31..2026-08-08. Every
    # phrasing the fleet actually used is covered by these two groups — see the test.
    ShippedCapability(
        action="deactivate_strategy",
        shipped_on="2026-08-08",
        note="shipped as the `deactivate_strategy` action; deactivate your own strategy "
             "directly instead of filing a ticket",
        all_of=(
            frozenset({"deactivate", "deactivated", "deactivating", "deactivation"}),
            frozenset({"strategy", "strategies"}),
        ),
    ),
    # The econ corpus: 3 tickets (data_collection x2, external_data_pipeline x1) from
    # 2026-07-22 asking for settled CPI history as a run_backtest dataset. PARTIAL delivery,
    # and the note says so — the corpus shipped, the official CPI ACTUALS they also asked for
    # are not collected. Closing with a note that claimed the whole ask would teach the fleet
    # its request was met and stop it re-asking for the half that is missing.
    ShippedCapability(
        action="run_backtest",
        shipped_on="2026-08-13",
        note="the settled econ corpus shipped: run_backtest with dataset='econ' replays "
             "KXCPI/KXCPIYOY/KXPAYROLL/KXPCE/KXGDP/KXUNRATE/KXJOBLESS/KXPPI order-book "
             "history. The official CPI actuals you also asked for are NOT collected, so a "
             "spec cannot gate on the released number — file a new ticket for that half",
        all_of=(
            frozenset({"cpi", "kxcpi"}),
            frozenset({"backtest", "backtests", "backtesting", "corpus", "dataset", "pipeline"}),
        ),
    ),
)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _shipped_match(capability: str) -> ShippedCapability | None:
    """The registry entry whose capability this ticket is asking for, or None."""
    toks = _tokens(capability)
    if not toks:
        return None
    for entry in SHIPPED_CAPABILITIES:
        if entry.none_of & toks:
            continue
        if all(group & toks for group in entry.all_of):
            return entry
    return None


def _signature(category: str, capability: str) -> tuple[str, frozenset[str]]:
    tokens = frozenset(
        t for t in re.findall(r"[a-z0-9]+", capability.lower()) if t not in _STOPWORDS
    )
    return f"{category}:{' '.join(sorted(tokens))[:180]}", tokens


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_duplicate(session, category: str, capability: str) -> EvoTicket | None:
    _, tokens = _signature(category, capability)
    candidates = list(
        session.scalars(
            select(EvoTicket).where(
                EvoTicket.category == category,
                EvoTicket.status.in_(("open", "in_review", "approved")),
            )
        )
    )
    best, best_score = None, 0.0
    for t in candidates:
        _, t_tokens = _signature(t.category, t.capability)
        score = _jaccard(tokens, t_tokens)
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= DEDUP_JACCARD else None


def support_ticket(
    session, agent_uuid: str, ticket_id: int, *, note: str | None = None
) -> tuple[EvoTicketSupporter | None, str | None]:
    ticket = session.get(EvoTicket, ticket_id)
    if ticket is None:
        return None, f"ticket {ticket_id} not found"
    existing = session.scalar(
        select(EvoTicketSupporter).where(
            EvoTicketSupporter.ticket_id == ticket_id,
            EvoTicketSupporter.agent_uuid == agent_uuid,
        )
    )
    if existing is not None:
        return existing, None
    agent = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))
    row = EvoTicketSupporter(
        ticket_id=ticket_id,
        agent_uuid=agent_uuid,
        family=agent.surname if agent else None,
        note=note,
    )
    session.add(row)
    session.flush()
    return row, None


def submit_ticket(
    session,
    *,
    agent_uuid: str,
    category: str,
    capability: str,
    problem: str | None = None,
    expected_strategy_benefit: str | None = None,
    expected_portfolio_benefit: str | None = None,
    existing_workaround: str | None = None,
    required: dict | None = None,
    expected_cost: str | None = None,
    expected_effort: str | None = None,
    urgency: str = "normal",
    evidence: dict | None = None,
    related: dict | None = None,
) -> tuple[EvoTicket | None, str | None, bool]:
    """Returns (ticket, error, deduplicated). A semantic duplicate returns the
    EXISTING ticket with deduplicated=True and records the agent as a supporter."""
    if category not in CATEGORIES:
        return None, f"unknown category {category!r} (valid: {CATEGORIES})", False
    if not capability or len(capability.strip()) < 10:
        return None, "capability description too short", False
    if urgency not in ("low", "normal", "high"):
        urgency = "normal"
    dup = find_duplicate(session, category, capability)
    if dup is not None:
        support_ticket(session, agent_uuid, dup.id, note=f"dedup of: {capability[:120]}")
        audit(session, "ticket_deduplicated", agent_uuid=agent_uuid, ticket_id=dup.id)
        return dup, None, True
    agent = session.scalar(select(EvoAgent).where(EvoAgent.agent_uuid == agent_uuid))
    sig, _ = _signature(category, capability)
    row = EvoTicket(
        requesting_uuid=agent_uuid,
        requesting_family=agent.surname if agent else None,
        category=category,
        capability=capability[:200],
        problem=problem,
        expected_strategy_benefit=expected_strategy_benefit,
        expected_portfolio_benefit=expected_portfolio_benefit,
        existing_workaround=existing_workaround,
        required_json=required,
        expected_cost=expected_cost,
        expected_effort=expected_effort,
        urgency=urgency,
        evidence_json=evidence,
        related_json=related,
        norm_signature=sig[:200],
    )
    session.add(row)
    session.flush()
    audit(session, "ticket_submitted", agent_uuid=agent_uuid, ticket_id=row.id,
          category=category)
    return row, None, False


def review_queue(session) -> list[dict]:
    """Human-review ordering: supporters desc, then urgency, then age (spec §16)."""
    from sqlalchemy import func

    rows = session.execute(
        select(EvoTicket, func.count(EvoTicketSupporter.id),
               func.count(func.distinct(EvoTicketSupporter.family)))
        .outerjoin(EvoTicketSupporter, EvoTicketSupporter.ticket_id == EvoTicket.id)
        .where(EvoTicket.status.in_(("open", "in_review")))
        .group_by(EvoTicket.id)
    ).all()
    urgency_rank = {"high": 0, "normal": 1, "low": 2}
    out = [
        {
            "id": t.id,
            "requesting_uuid": t.requesting_uuid,
            "category": t.category,
            "capability": t.capability,
            "problem": (t.problem or "")[:300],
            "supporters": int(n),
            "supporting_families": int(fams),
            "expected_strategy_benefit": (t.expected_strategy_benefit or "")[:200],
            "expected_cost": t.expected_cost,
            "urgency": t.urgency,
            "status": t.status,
            "created_at": t.created_at.isoformat(),
        }
        for t, n, fams in rows
    ]
    out.sort(key=lambda r: (-r["supporters"], urgency_rank.get(r["urgency"], 1), r["id"]))
    return out


def resolve_ticket(
    session,
    ticket_id: int,
    *,
    status: str,
    decision: str | None = None,
    result: str | None = None,
) -> tuple[EvoTicket | None, str | None]:
    """Close (or re-stage) a ticket, recording WHY. Returns (ticket, error).

    `decision` is the operator's call (why it was approved/rejected); `result` is what was
    actually delivered. A closed ticket without one of those is just silence with extra
    steps — the queue exists so the fleet can see its requests being answered.
    """
    if status not in TICKET_STATUSES:
        return None, f"unknown ticket status {status!r} (valid: {TICKET_STATUSES})"
    row = session.get(EvoTicket, ticket_id)
    if row is None:
        return None, f"ticket {ticket_id} not found"
    row.status = status
    if decision:
        row.human_decision = decision[:4000]
    if result:
        row.implementation_result = result[:4000]
    session.flush()
    audit(session, "ticket_resolved", ticket_id=row.id, status=status)
    return row, None


def auto_resolve_shipped(session) -> int:
    """Close open tickets asking for a capability that has since shipped. Returns the count.

    Runs every cycle and is idempotent: resolved tickets leave the `open`/`in_review` filter,
    so a second pass matches nothing and rewrites nothing. Safety valve if this ever closes
    something wrongly — `find_duplicate` only looks at open/in_review/approved, so the fleet
    can raise the request again and it lands as a NEW ticket rather than being folded into
    the one we closed.
    """
    rows = list(
        session.scalars(
            select(EvoTicket).where(EvoTicket.status.in_(("open", "in_review")))
        )
    )
    n = 0
    for row in rows:
        entry = _shipped_match(row.capability or "")
        if entry is None:
            continue
        resolve_ticket(
            session, row.id, status="implemented",
            result=f"{entry.note} (shipped {entry.shipped_on}, action `{entry.action}`)",
        )
        n += 1
    return n
