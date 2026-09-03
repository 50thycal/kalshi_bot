"""Fitness evaluation (spec §23-§25): the 100-point rubric, conservative
statistics, hybrid absolute/relative scoring, historical reliability, integrity
penalties, and the 6-hour-delayed peer-visible copy.

Pipeline (spec §24): raw absolute metrics -> conservative adjustment (shrinkage +
deterministic lower-confidence bounds; no RNG so results are reproducible) ->
winsorization across the cohort -> percentile blend -> configured weights.
Every intermediate lands in components_json for audit."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import paper as papermod
from .config import EvoSettings
from .models import (
    EvoAuditEvent,
    EvoFill,
    EvoFitness,
    EvoGenome,
    EvoInfluence,
    EvoListener,
    EvoMemory,
    EvoOpportunity,
    EvoOrder,
    EvoPosition,
)

logger = logging.getLogger(__name__)

NEUTRAL_RELIABILITY = 50.0

# Audit kinds that record a REFUSED action: the write never landed, so nothing was
# tampered with and nothing was gained. Both are id-guess collisions — belief ids and
# experiment ids share one integer space — and both were being scored as misconduct
# (-20 points each, disqualification at 3). Excluded here rather than by rewriting
# history, because rows written before the fix are still in the live DB and the ops
# channel is read-only.
HARMLESS_REFUSAL_KINDS = frozenset({
    "cross_agent_belief_revision",
    "cross_agent_experiment_conclusion",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _squash(x: float, scale: float) -> float:
    """Map an unbounded metric to 0..1 with 0.5 at x=0 (logistic)."""
    try:
        return 1.0 / (1.0 + math.exp(-x / scale))
    except OverflowError:
        return 0.0 if x < 0 else 1.0


def _event_root(ticker: str) -> str:
    """Cluster key: the event portion of a market ticker (before the last '-')."""
    return ticker.rsplit("-", 1)[0] if "-" in ticker else ticker


# ---------------------------------------------------------------------------
# Raw metric collection (per agent, per cohort)
# ---------------------------------------------------------------------------


def collect_metrics(session, settings: EvoSettings, agent_uuid: str, cohort_id: int) -> dict:
    ledger = papermod.cohort_ledger(cohort_id)
    pf = papermod.get_portfolio(session, agent_uuid, ledger)
    start = float(pf.starting_capital_usd) if pf else settings.starting_capital_usd
    nav = papermod.nav(session, agent_uuid, ledger)

    positions = list(
        session.scalars(
            select(EvoPosition).where(
                EvoPosition.agent_uuid == agent_uuid, EvoPosition.ledger == ledger
            )
        )
    )
    closed = [p for p in positions if p.status in ("settled", "closed", "transferred",
                                                   "liquidated")]
    trade_pnls = [float(p.realized_pnl_usd) for p in closed]
    open_pos = [p for p in positions if p.status == "open"]

    # Deployment / turnover, measured from BUY FILLS rather than EvoPosition.quantity.
    # paper.place_order's sell path decrements quantity to 0 when a position is closed,
    # so a position closed by SELLING contributes nothing to a quantity-based sum while
    # one closed by SETTLEMENT keeps its full size. Summing quantity therefore reads an
    # agent that manages its exits as having deployed almost no capital (observed live:
    # turnover 0.068 across 166 closed trades). Fills are immutable, so they are the
    # honest record of what actually went out the door.
    buy_fills = list(
        session.execute(
            select(EvoFill.market_ticker, EvoFill.price_cents, EvoFill.quantity)
            .join(EvoOrder, EvoOrder.id == EvoFill.order_id)
            .where(
                EvoFill.agent_uuid == agent_uuid,
                EvoOrder.cohort_id == cohort_id,
                EvoFill.action == "buy",
            )
        )
    )
    total_cost = sum(q * p / 100.0 for _, p, q in buy_fills)
    turnover = total_cost / start if start else 0.0

    # daily P&L series from closed positions (by close date)
    daily: dict[str, float] = {}
    for p in closed:
        day = (p.closed_at or p.opened_at).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + float(p.realized_pnl_usd)
    daily_pnls = list(daily.values())

    # concentration — same fill basis, so a closed event still counts against the
    # denominator instead of inflating whatever position happens to remain open
    by_event: dict[str, float] = {}
    for ticker, price, qty in buy_fills:
        root = _event_root(ticker)
        by_event[root] = by_event.get(root, 0.0) + qty * price / 100.0
    max_event_frac = (max(by_event.values()) / total_cost) if total_cost > 0 else 0.0

    # effective sample size: clusters of (event_root, open date)
    clusters = {( _event_root(p.market_ticker), p.opened_at.strftime("%Y-%m-%d"))
                for p in closed}
    n_eff = len(clusters)

    # drawdown / ruin
    equity, eq_peak, max_dd = start, start, 0.0
    for p in sorted(closed, key=lambda p: (p.closed_at or p.opened_at)):
        equity += float(p.realized_pnl_usd)
        eq_peak = max(eq_peak, equity)
        max_dd = max(max_dd, (eq_peak - equity) / eq_peak if eq_peak > 0 else 0.0)
    min_equity_frac = min((equity for equity in [nav / start if start else 1.0]), default=1.0)

    # opportunities (ex-ante)
    opps = list(
        session.scalars(
            select(EvoOpportunity).where(
                EvoOpportunity.agent_uuid == agent_uuid,
                EvoOpportunity.cohort_id.in_((cohort_id, None)),
            )
        )
    )
    n_opps = len(opps)
    n_acted = sum(1 for o in opps if o.acted)
    n_justified_no_trade = sum(1 for o in opps if not o.acted and o.no_trade_reason)

    # orders / invalid intents
    orders = list(
        session.scalars(
            select(EvoOrder).where(
                EvoOrder.agent_uuid == agent_uuid, EvoOrder.cohort_id == cohort_id
            )
        )
    )
    n_orders = len(orders)
    n_rejected = sum(1 for o in orders if o.status == "rejected")

    # forecast quality: Brier of intent confidence vs settled outcome
    briers: list[float] = []
    settled_by_key: dict[tuple[str, str], EvoPosition] = {}
    for p in positions:
        if p.status == "settled" and p.resolved_value_cents is not None:
            settled_by_key[(p.market_ticker, p.side)] = p
    for o in orders:
        conf = (o.intent_json or {}).get("confidence")
        if conf is None or o.action != "buy":
            continue
        pos = settled_by_key.get((o.market_ticker, o.side))
        if pos is None:
            continue
        outcome = 1.0 if pos.resolved_value_cents == 100 else 0.0
        try:
            briers.append((float(conf) - outcome) ** 2)
        except (TypeError, ValueError):
            continue

    # adaptation metrics
    revisions = list(
        session.scalars(
            select(EvoGenome)
            .where(EvoGenome.agent_uuid == agent_uuid, EvoGenome.material.is_(True))
            .order_by(EvoGenome.created_at)
        )
    )
    n_revisions = len(revisions)
    prereg = sum(1 for g in revisions if g.hypothesis and g.rollback_conditions)
    # field reversals: same field changed in consecutive revisions of the same kind
    reversals = 0
    by_kind: dict[str, list[EvoGenome]] = {}
    for g in revisions:
        by_kind.setdefault(g.kind, []).append(g)
    for revs in by_kind.values():
        for prev, cur in zip(revs, revs[1:], strict=False):
            overlap = set(prev.changed_fields_json or []) & set(cur.changed_fields_json or [])
            reversals += len(overlap)

    listeners_rows = list(
        session.scalars(select(EvoListener).where(EvoListener.owner_uuid == agent_uuid))
    )
    l_useful = sum(li.useful_count for li in listeners_rows)
    l_useless = sum(li.useless_count for li in listeners_rows)

    influences = list(
        session.scalars(
            select(EvoInfluence).where(EvoInfluence.receiving_uuid == agent_uuid)
        )
    )
    blind_copies = sum(
        1 for i in influences if i.result == "adopted" and not (i.modifications or "").strip()
    )

    journals = list(
        session.scalars(
            select(EvoMemory).where(
                EvoMemory.agent_uuid == agent_uuid, EvoMemory.kind == "journal"
            )
        )
    )
    complete_journals = sum(
        1 for j in journals
        if (j.body_json or {}).get("decision") and (j.body_json or {}).get("expected_outcome")
    )

    integrity_events = list(
        session.scalars(
            select(EvoAuditEvent).where(
                EvoAuditEvent.agent_uuid == agent_uuid,
                EvoAuditEvent.severity == "integrity",
                EvoAuditEvent.kind.not_in(HARMLESS_REFUSAL_KINDS),
            )
        )
    )

    return {
        "start": start,
        "nav": nav,
        "net_pnl": nav - start,
        "n_trades": len(closed),
        "n_eff": n_eff,
        "trade_pnls": trade_pnls,
        "daily_pnls": daily_pnls,
        "turnover": turnover,
        "max_event_frac": max_event_frac,
        "max_drawdown_frac": max_dd,
        "min_equity_frac": min_equity_frac,
        "open_positions": len(open_pos),
        "n_opportunities": n_opps,
        "n_acted": n_acted,
        "n_justified_no_trade": n_justified_no_trade,
        "n_orders": n_orders,
        "n_rejected_orders": n_rejected,
        "brier_mean": (sum(briers) / len(briers)) if briers else None,
        "n_scored_forecasts": len(briers),
        "n_material_revisions": n_revisions,
        "prereg_revisions": prereg,
        "field_reversals": reversals,
        "listener_useful": l_useful,
        "listener_useless": l_useless,
        "n_influences": len(influences),
        "blind_copies": blind_copies,
        "n_journals": len(journals),
        "complete_journals": complete_journals,
        "integrity_events": len(integrity_events),
    }


# ---------------------------------------------------------------------------
# Component scores (absolute, 0..1 each)
# ---------------------------------------------------------------------------


def _shrunk_per_trade(m: dict, settings: EvoSettings) -> float:
    pnls = m["trade_pnls"]
    n = len(pnls)
    if n == 0:
        return 0.0
    mean = sum(pnls) / n
    return mean * n / (n + settings.shrinkage_trades)


def _lcb_per_trade(m: dict) -> float:
    """Deterministic lower-confidence-bound per-trade P&L: mean - 1.28·SE."""
    pnls = m["trade_pnls"]
    n = len(pnls)
    if n == 0:
        return 0.0
    mean = sum(pnls) / n
    if n == 1:
        return min(0.0, mean)
    var = sum((x - mean) ** 2 for x in pnls) / (n - 1)
    return mean - 1.28 * math.sqrt(var / n)


def component_scores(m: dict, settings: EvoSettings) -> dict[str, float]:
    start = m["start"] or 1.0

    # Did this agent ever put capital at risk? `_squash` is a logistic centred on 0,
    # so every profit term reads "no data" as 0.5 — an agent that never traded banked
    # 45% of the profit component. `risk` was worse: no trades means no drawdown and
    # no concentration, which scored a perfect 1.0. Together those made opting out of
    # trading rank above trading and losing, which inverts the north star. Both
    # components are conditional on exposure, so with no exposure they score 0 —
    # matching `evidence`, which already refuses to incubate a no-trade agent.
    deployed = m["turnover"] > 0 or m["n_trades"] > 0 or m["open_positions"] > 0

    # 1. profitability & capital efficiency
    ret = m["net_pnl"] / start
    shrunk = _shrunk_per_trade(m, settings)
    lcb = _lcb_per_trade(m)
    profit = (
        0.45 * _squash(ret * 100, 5.0)          # % return on normalized capital
        + 0.25 * _squash(shrunk, 2.0)            # shrunk per-trade dollars
        + 0.20 * _squash(lcb, 2.0)               # conservative LCB per trade
        + 0.10 * _clamp(min(m["turnover"], 3.0) / 3.0)  # capital actually used
    ) if deployed else 0.0

    # 2. consistency
    pnls = m["trade_pnls"]
    total_profit = sum(x for x in pnls if x > 0)
    largest_share = (max(pnls) / total_profit) if pnls and total_profit > 0 else 1.0
    daily = m["daily_pnls"]
    pos_days = sum(1 for d in daily if d > 0)
    day_ratio = pos_days / len(daily) if daily else 0.0
    if len(daily) >= 2:
        d_mean = sum(daily) / len(daily)
        d_var = sum((x - d_mean) ** 2 for x in daily) / (len(daily) - 1)
        vol_penalty = _clamp(math.sqrt(d_var) / (abs(d_mean) + 5.0) / 5.0)
    else:
        vol_penalty = 0.5
    consistency = (
        0.40 * (1.0 - _clamp(largest_share))     # lottery-trade discount
        + 0.35 * _clamp(day_ratio)
        + 0.25 * (1.0 - vol_penalty)
    )

    # 3. risk quality — only meaningful once capital has been exposed to loss
    risk = (
        0.45 * (1.0 - _clamp(m["max_drawdown_frac"] / 0.5))
        + 0.30 * (1.0 - _clamp(m["max_event_frac"]))
        + 0.25 * _clamp(m["min_equity_frac"])
    ) if deployed else 0.0

    # 4. evidence & opportunity use — no incubation: a no-trade agent scores near 0
    n_eff = m["n_eff"]
    evidence_n = _clamp(math.log1p(n_eff) / math.log1p(30))
    capture = (m["n_acted"] + m["n_justified_no_trade"]) / m["n_opportunities"] if (
        m["n_opportunities"]
    ) else 0.0
    evidence = 0.6 * evidence_n + 0.4 * _clamp(capture)

    # 5. forecast & execution quality
    brier = m["brier_mean"]
    brier_score = (1.0 - _clamp(brier / 0.25)) if brier is not None else 0.4
    invalid_rate = m["n_rejected_orders"] / m["n_orders"] if m["n_orders"] else 0.0
    forecast = 0.6 * brier_score + 0.4 * (1.0 - _clamp(invalid_rate * 2))

    # 6. adaptive intelligence
    n_rev = m["n_material_revisions"]
    prereg_rate = m["prereg_revisions"] / n_rev if n_rev else 0.5
    thrash = _clamp(m["field_reversals"] / 4.0)
    l_total = m["listener_useful"] + m["listener_useless"]
    listener_quality = m["listener_useful"] / l_total if l_total else 0.5
    blind_rate = m["blind_copies"] / m["n_influences"] if m["n_influences"] else 0.0
    journal_quality = (
        m["complete_journals"] / m["n_journals"] if m["n_journals"] else 0.0
    )
    adaptive = (
        0.30 * prereg_rate
        + 0.25 * (1.0 - thrash)
        + 0.15 * listener_quality
        + 0.15 * (1.0 - _clamp(blind_rate))
        + 0.15 * journal_quality
    )

    return {
        "profit": _clamp(profit),
        "consistency": _clamp(consistency),
        "risk": _clamp(risk),
        "evidence": _clamp(evidence),
        "forecast": _clamp(forecast),
        "adaptive": _clamp(adaptive),
        # audit intermediates
        "_return_on_start": ret,
        "_shrunk_per_trade": shrunk,
        "_lcb_per_trade": lcb,
        "_largest_trade_share": largest_share,
    }


# ---------------------------------------------------------------------------
# Cohort evaluation (hybrid absolute + relative)
# ---------------------------------------------------------------------------


def _winsorize(values: list[float], pct: float) -> list[float]:
    if len(values) < 3 or pct <= 0:
        return list(values)
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(len(s) * pct)))
    lo, hi = s[k], s[len(s) - 1 - k]
    return [min(max(v, lo), hi) for v in values]


def _percentiles(values: list[float]) -> list[float]:
    """Rank-based percentile in 0..1 (average rank for ties)."""
    n = len(values)
    if n <= 1:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: values[i])
    pct = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0
        for k in range(i, j + 1):
            pct[order[k]] = avg_rank / (n - 1)
        i = j + 1
    return pct


def historical_reliability(session, settings: EvoSettings, agent_uuid: str,
                           before_cohort_id: int) -> float:
    """Recency-weighted mean of past FINAL cohort scores; neutral prior for new
    agents (children/wildcards never inherit parent credit)."""
    rows = list(
        session.scalars(
            select(EvoFitness)
            .where(
                EvoFitness.agent_uuid == agent_uuid,
                EvoFitness.kind == "final",
                EvoFitness.cohort_id < before_cohort_id,
            )
            .order_by(EvoFitness.cohort_id.desc())
        )
    )
    if not rows:
        return NEUTRAL_RELIABILITY
    num, den = 0.0, 0.0
    weight = 1.0
    prior_weight = 1.0  # the neutral prior decays as evidence accumulates
    for r in rows:
        num += weight * r.cohort_score
        den += weight
        weight *= settings.reliability_decay
    num += prior_weight * NEUTRAL_RELIABILITY * settings.reliability_decay ** len(rows)
    den += prior_weight * settings.reliability_decay ** len(rows)
    return num / den if den else NEUTRAL_RELIABILITY


def evaluate_cohort(
    session,
    settings: EvoSettings,
    cohort_id: int,
    agent_uuids: list[str],
    *,
    kind: str = "interim",
    config_version_id: int | None = None,
    now: datetime | None = None,
) -> list[EvoFitness]:
    """Score every agent: absolute components -> winsorize -> percentile blend ->
    weights -> integrity penalties -> selection score -> rank. Writes EvoFitness
    rows (idempotent per (cohort, agent, kind): re-evaluation updates in place for
    interim; final rows are written once by the transition lock)."""
    now = now or _now()
    weights = {
        "profit": settings.fitness_weight_profit,
        "consistency": settings.fitness_weight_consistency,
        "risk": settings.fitness_weight_risk,
        "evidence": settings.fitness_weight_evidence,
        "forecast": settings.fitness_weight_forecast,
        "adaptive": settings.fitness_weight_adaptive,
    }
    metrics = {au: collect_metrics(session, settings, au, cohort_id) for au in agent_uuids}
    absolute = {au: component_scores(metrics[au], settings) for au in agent_uuids}

    # winsorized percentile blend per component
    comp_names = list(weights)
    winsored: dict[str, list[float]] = {}
    for comp in comp_names:
        winsored[comp] = _winsorize([absolute[au][comp] for au in agent_uuids],
                                    settings.winsor_pct)
    pct: dict[str, list[float]] = {c: _percentiles(winsored[c]) for c in comp_names}

    results: list[EvoFitness] = []
    for i, au in enumerate(agent_uuids):
        components: dict[str, dict] = {}
        score = 0.0
        for comp in comp_names:
            abs_score = absolute[au][comp]
            rel_score = pct[comp][i]
            blended = 0.5 * abs_score + 0.5 * rel_score
            points = weights[comp] * blended
            score += points
            components[comp] = {
                "absolute": round(abs_score, 4),
                "winsorized": round(winsored[comp][i], 4),
                "percentile": round(rel_score, 4),
                "blended": round(blended, 4),
                "points": round(points, 3),
                "weight": weights[comp],
            }
        components["_audit"] = {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in absolute[au].items() if k.startswith("_")
        }
        components["_metrics"] = {
            k: v for k, v in metrics[au].items()
            if k not in ("trade_pnls", "daily_pnls")
        }

        # integrity penalties (spec §25)
        penalties: dict[str, float] = {}
        n_integrity = metrics[au]["integrity_events"]
        if n_integrity:
            penalty = min(score, 20.0 * n_integrity)
            penalties["integrity"] = round(penalty, 3)
            score -= penalty
            if n_integrity >= 3:
                penalties["disqualified"] = 1.0
                score = 0.0

        reliability = historical_reliability(session, settings, au, cohort_id)
        selection = (
            settings.selection_current_weight * score
            + settings.selection_reliability_weight * reliability
        )
        existing = session.scalar(
            select(EvoFitness).where(
                EvoFitness.cohort_id == cohort_id,
                EvoFitness.agent_uuid == au,
                EvoFitness.kind == kind,
            )
        )
        if existing is not None and kind == "interim":
            existing.computed_at = now
            # visible_after is set once, at the row's first insert (below), and never
            # re-armed here: a peer's standing becomes visible a flat
            # leaderboard_delay_hours after its FIRST interim score this cohort, not
            # leaderboard_delay_hours after its most recent recompute. Re-arming it on
            # every update made it unreachable in practice — this function runs far more
            # often (orchestrator.py, hourly) than the delay it was supposed to gate
            # (6h), so visible_after was always in the future and
            # peers.delayed_leaderboard() never returned a row (WS-014).
            existing.components_json = components
            existing.penalties_json = penalties or None
            existing.cohort_score = round(score, 3)
            existing.reliability_score = round(reliability, 3)
            existing.selection_score = round(selection, 3)
            row = existing
        elif existing is not None:
            row = existing  # final already written; never overwrite
        else:
            row = EvoFitness(
                cohort_id=cohort_id,
                agent_uuid=au,
                kind=kind,
                computed_at=now,
                visible_after=now + timedelta(hours=settings.leaderboard_delay_hours),
                components_json=components,
                penalties_json=penalties or None,
                cohort_score=round(score, 3),
                reliability_score=round(reliability, 3),
                selection_score=round(selection, 3),
                config_version_id=config_version_id,
            )
            session.add(row)
        results.append(row)
    session.flush()

    # deterministic ranking: score desc, then agent uuid as the stable tiebreak
    results.sort(key=lambda r: (-r.selection_score, r.agent_uuid))
    for rank, row in enumerate(results, start=1):
        row.rank = rank
    session.flush()
    return results


def group_by_fractions(
    ranked: list[EvoFitness], settings: EvoSettings
) -> dict[str, list[EvoFitness]]:
    """Split ranked (best-first) rows into top/middle/bottom by the configured
    fractions, with deterministic rounding: bottom = round(n·bottom), top =
    round(n·top), middle = remainder (a 30-agent cohort gives exactly 9/12/9)."""
    n = len(ranked)
    n_bottom = round(n * settings.bottom_fraction)
    n_top = round(n * settings.top_fraction)
    n_top = min(n_top, n - n_bottom)
    return {
        "top": ranked[:n_top],
        "middle": ranked[n_top:n - n_bottom],
        "bottom": ranked[n - n_bottom:] if n_bottom else [],
    }
