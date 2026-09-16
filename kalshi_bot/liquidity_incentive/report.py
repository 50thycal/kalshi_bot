"""Read-only research views over the `incentive_*` tables — shared by the livedash page and
the ops script. Every number that comes from the scoring model is prefixed `est_`; the fill
models are always reported side by side and never summed across `fill_model`.

Three payloads:
  * `build_active(session)`   — the current-program table with the opportunity ranking
  * `build_history(session)`  — daily program counts / pools, outcomes by policy x tier x model
  * `build_headline(session)` — "if this had run at $X for the window, what would each fill
                                 model have earned" — conservative and optimistic separately
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .. import models as m
from . import economics as econ
from .fills import FILL_MODELS, MODEL_CONSERVATIVE, MODEL_OPTIMISTIC, MODEL_QUEUE_AWARE
from .quotes import POLICIES

SORT_KEYS = ("est_net_per_day_usd", "reward_per_capital_dollar_per_day", "period_reward_usd",
             "target_size", "est_single_leg_cost_per_day_usd")


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _f(x, nd: int | None = None):
    if x is None:
        return None
    v = float(x)
    return round(v, nd) if nd is not None else v


def _latest_snapshots(session, tickers: list[str]) -> dict[str, m.IncentiveMarketSnapshot]:
    if not tickers:
        return {}
    sub = (select(m.IncentiveMarketSnapshot.market_ticker, func.max(m.IncentiveMarketSnapshot.id).label("mid"))
           .where(m.IncentiveMarketSnapshot.market_ticker.in_(tickers))
           .group_by(m.IncentiveMarketSnapshot.market_ticker)).subquery()
    rows = session.scalars(select(m.IncentiveMarketSnapshot).join(sub, m.IncentiveMarketSnapshot.id == sub.c.mid)).all()
    return {r.market_ticker: r for r in rows}


def _outcome_rates(session, since: datetime) -> dict[tuple[str, str, int, str], dict]:
    """Per (ticker, policy, tier, model): summed components and rest-seconds over the window."""
    rows = session.execute(
        select(m.IncentiveShadowOutcome.market_ticker, m.IncentiveShadowOutcome.policy,
               m.IncentiveShadowOutcome.capital_tier_usd, m.IncentiveShadowOutcome.fill_model,
               func.count(), func.sum(m.IncentiveShadowOutcome.rest_seconds),
               func.sum(m.IncentiveShadowOutcome.est_reward_usd),
               func.sum(m.IncentiveShadowOutcome.paired_pnl_usd),
               func.sum(m.IncentiveShadowOutcome.fees_usd),
               func.sum(m.IncentiveShadowOutcome.single_leg_mtm_5m_usd),
               func.sum(m.IncentiveShadowOutcome.settlement_pnl_usd),
               func.sum(m.IncentiveShadowOutcome.net_before_settlement_usd),
               func.sum(m.IncentiveShadowOutcome.net_after_settlement_usd))
        .where(m.IncentiveShadowOutcome.ended_at >= since)
        .group_by(m.IncentiveShadowOutcome.market_ticker, m.IncentiveShadowOutcome.policy,
                  m.IncentiveShadowOutcome.capital_tier_usd, m.IncentiveShadowOutcome.fill_model)).all()
    out = {}
    for (t, pol, tier, model, n, rest, rew, paired, fees, mtm, settle, net_b, net_a) in rows:
        out[(t, pol, tier, model)] = {
            "n": int(n or 0), "rest_seconds": _f(rest) or 0.0, "est_reward_usd": _f(rew) or 0.0,
            "paired_pnl_usd": _f(paired) or 0.0, "fees_usd": _f(fees) or 0.0,
            "single_leg_mtm_usd": _f(mtm) or 0.0, "settlement_pnl_usd": _f(settle) or 0.0,
            "net_before_settlement_usd": _f(net_b) or 0.0, "net_after_settlement_usd": _f(net_a) or 0.0,
        }
    return out


def build_active(session, *, policy: str = "A_break_even", tier: int = 100, hours: int = 72,
                 sort: str = "est_net_per_day_usd", now: datetime | None = None) -> dict:
    """Current programs with the latest snapshot, the latest resting quote for (policy, tier),
    and the opportunity ranking computed from the window's CONSERVATIVE outcomes (the
    handoff's rule: never promote on optimistic fills)."""
    now = _now(now)
    since = now - timedelta(hours=max(1, min(hours, 24 * 60)))
    progs = session.scalars(select(m.IncentiveProgram).where(
        m.IncentiveProgram.superseded_at.is_(None), m.IncentiveProgram.disappeared_at.is_(None),
        m.IncentiveProgram.incentive_type == "liquidity")).all()
    progs = [p for p in progs if p.end_date is None or _aware(p.end_date) > now]
    snaps = _latest_snapshots(session, [p.market_ticker for p in progs])
    rates = _outcome_rates(session, since)
    quotes = {}
    if progs:
        sub = (select(m.IncentiveShadowQuote.market_ticker, func.max(m.IncentiveShadowQuote.id).label("qid"))
               .where(m.IncentiveShadowQuote.policy == policy, m.IncentiveShadowQuote.capital_tier_usd == tier,
                      m.IncentiveShadowQuote.market_ticker.in_([p.market_ticker for p in progs]))
               .group_by(m.IncentiveShadowQuote.market_ticker)).subquery()
        for q in session.scalars(select(m.IncentiveShadowQuote).join(sub, m.IncentiveShadowQuote.id == sub.c.qid)).all():
            quotes[q.market_ticker] = q
    rows = []
    for p in progs:
        t = p.market_ticker
        snap = snaps.get(t)
        q = quotes.get(t)
        cons = rates.get((t, policy, tier, MODEL_CONSERVATIVE), {})
        opt = rates.get((t, policy, tier, MODEL_OPTIMISTIC), {})
        rest_days = (cons.get("rest_seconds") or 0.0) / 86400.0
        est_reward_day = _per_day(cons, "est_reward_usd", rest_days) if rest_days > 0 else (
            (_f(q.est_reward_per_hour_usd) or 0.0) * 24 if q is not None else 0.0)
        single_cost = -min(0.0, _per_day(cons, "single_leg_mtm_usd", rest_days)
                           + _per_day(cons, "settlement_pnl_usd", rest_days))
        cap = _f(q.capital_required_usd) if q is not None else 0.0
        share = ((_f(q.est_yes_share) or 0.0) + (_f(q.est_no_share) or 0.0)) / 2.0 if q is not None else 0.0
        both = bool(snap and snap.est_yes_meets_target and snap.est_no_meets_target)
        opp = econ.opportunity(
            est_reward_per_day_usd=est_reward_day,
            est_paired_value_per_day_usd=_per_day(cons, "paired_pnl_usd", rest_days),
            est_single_leg_cost_per_day_usd=single_cost,
            est_fees_per_day_usd=_per_day(cons, "fees_usd", rest_days),
            capital_required_usd=cap or 0.0, both_sides_meet_target=both, our_share_of_field=share,
            sample_outcomes=int(cons.get("n", 0)))
        rows.append({
            "ticker": t, "title": p.market_title, "series": p.series_ticker,
            "program_id": p.program_id, "start_date": _iso(p.start_date), "end_date": _iso(p.end_date),
            "period_reward_usd": _f(p.period_reward_usd, 2),
            "reward_per_day_usd": _reward_per_day(p),
            "target_size": _f(p.target_size, 0), "discount_factor_bps": p.discount_factor_bps,
            "fee_rule": p.fee_rule_json,
            "snapshot_at": _iso(snap.at) if snap else None,
            "best_yes_bid": snap.best_yes_bid if snap else None,
            "best_no_bid": snap.best_no_bid if snap else None,
            "spread_cents": snap.spread_cents if snap else None,
            "yes_depth": _f(snap.yes_depth_total, 0) if snap else None,
            "no_depth": _f(snap.no_depth_total, 0) if snap else None,
            "yes_meets_target": snap.est_yes_meets_target if snap else None,
            "no_meets_target": snap.est_no_meets_target if snap else None,
            "est_reference_price": snap.est_reference_price if snap else None,
            "quote": None if q is None else {
                "placed_at": _iso(q.placed_at), "yes_bid": q.yes_bid, "no_bid": q.no_bid,
                "qty_per_side": _f(q.qty_per_side, 0), "pair_cost_cents": q.pair_cost_cents,
                "pair_edge_cents": q.pair_edge_cents, "capital_required_usd": _f(q.capital_required_usd, 2),
                "capital_unused_usd": _f(q.capital_unused_usd, 2), "reason": q.reason,
                "est_yes_share": _f(q.est_yes_share, 4), "est_no_share": _f(q.est_no_share, 4),
                "est_reward_per_hour_usd": _f(q.est_reward_per_hour_usd, 6),
                "ended_at": _iso(q.ended_at), "end_reason": q.end_reason,
            },
            "conservative": cons or None, "optimistic": opt or None,
            **opp.as_dict(),
        })
    key = sort if sort in SORT_KEYS else "est_net_per_day_usd"
    rows.sort(key=lambda r: (r.get(key) is None, -(r.get(key) or 0.0)))
    return {"generated_at": _iso(now), "policy": policy, "tier": tier, "hours": hours, "sort": key,
            "policies": list(POLICIES), "fill_models": list(FILL_MODELS), "n_programs": len(rows),
            "total_period_reward_usd": round(sum(r["period_reward_usd"] or 0.0 for r in rows), 2),
            "rows": rows, "collector": _collector_health(session, now)}


def _per_day(cell: dict, key: str, rest_days: float) -> float:
    """A summed component over the window, per day of resting time (0 when nothing rested)."""
    return (cell.get(key, 0.0) / rest_days) if rest_days > 0 else 0.0


def _reward_per_day(p) -> float | None:
    if p.period_reward_usd is None or not p.start_date or not p.end_date:
        return None
    days = (_aware(p.end_date) - _aware(p.start_date)).total_seconds() / 86400.0
    return round(float(p.period_reward_usd) / days, 4) if days > 0 else None


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _collector_health(session, now: datetime) -> dict:
    since = now - timedelta(hours=24)
    counts = dict(session.execute(
        select(m.IncentiveCollectorEvent.kind, func.count())
        .where(m.IncentiveCollectorEvent.at >= since).group_by(m.IncentiveCollectorEvent.kind)).all())
    last = session.execute(select(m.IncentiveCollectorEvent.kind, m.IncentiveCollectorEvent.at)
                           .order_by(m.IncentiveCollectorEvent.at.desc()).limit(1)).first()
    last_cycle = session.scalars(select(m.IncentiveDiscoveryCycle)
                                 .order_by(m.IncentiveDiscoveryCycle.id.desc()).limit(1)).first()
    open_pairs = session.scalar(select(func.count()).select_from(m.IncentiveShadowQuote)
                                .where(m.IncentiveShadowQuote.ended_at.is_(None))) or 0
    alive = bool(last and _aware(last[1]) >= now - timedelta(minutes=15)
                 and last[0] != "thread_stopped")
    return {"alive": alive, "last_event": {"kind": last[0], "at": _iso(last[1])} if last else None,
            "events_24h": counts, "open_pairs": int(open_pairs),
            "last_discovery": None if last_cycle is None else {
                "at": _iso(last_cycle.started_at), "programs_listed": last_cycle.programs_listed,
                "liquidity_programs": last_cycle.liquidity_programs, "errors": last_cycle.errors,
                "total_period_reward_usd": _f(last_cycle.total_period_reward_usd, 2)}}


def build_history(session, *, days: int = 14, now: datetime | None = None) -> dict:
    now = _now(now)
    since = now - timedelta(days=max(1, min(days, 120)))
    # Q1: programs active per day + advertised pool per day (from the versioned terms).
    progs = session.scalars(select(m.IncentiveProgram).where(
        m.IncentiveProgram.incentive_type == "liquidity", m.IncentiveProgram.last_seen_at >= since)).all()
    by_day: dict[str, dict] = {}
    for d in range((now.date() - since.date()).days + 1):
        day = (since.date() + timedelta(days=d))
        key = day.isoformat()
        rows = [p for p in progs if _aware(p.first_seen_at).date() <= day
                and (p.superseded_at is None or _aware(p.superseded_at).date() >= day)
                and (p.disappeared_at is None or _aware(p.disappeared_at).date() >= day)]
        pools = [_reward_per_day(p) or 0.0 for p in rows]
        targets = sorted(float(p.target_size) for p in rows if p.target_size is not None)
        by_day[key] = {"programs": len(rows), "reward_per_day_usd": round(sum(pools), 2),
                       "median_reward_per_day_usd": _median(pools),
                       "median_target_size": _median(targets)}
    # Outcomes by policy x tier x model, plus outcome-label mix and single-leg stats.
    econ_rows = session.execute(
        select(m.IncentiveShadowOutcome.policy, m.IncentiveShadowOutcome.capital_tier_usd,
               m.IncentiveShadowOutcome.fill_model, func.count(),
               func.sum(m.IncentiveShadowOutcome.rest_seconds),
               func.sum(m.IncentiveShadowOutcome.capital_hours),
               func.sum(m.IncentiveShadowOutcome.est_reward_usd),
               func.sum(m.IncentiveShadowOutcome.paired_pnl_usd),
               func.sum(m.IncentiveShadowOutcome.fees_usd),
               func.sum(m.IncentiveShadowOutcome.single_leg_mtm_5m_usd),
               func.sum(m.IncentiveShadowOutcome.settlement_pnl_usd),
               func.sum(m.IncentiveShadowOutcome.net_before_settlement_usd),
               func.sum(m.IncentiveShadowOutcome.net_after_settlement_usd),
               func.count(m.IncentiveShadowOutcome.settled_at))
        .where(m.IncentiveShadowOutcome.ended_at >= since)
        .group_by(m.IncentiveShadowOutcome.policy, m.IncentiveShadowOutcome.capital_tier_usd,
                  m.IncentiveShadowOutcome.fill_model)).all()
    cells = []
    for (pol, tier, model, n, rest, cap_h, rew, paired, fees, mtm, settle, net_b, net_a, n_settled) in econ_rows:
        cells.append({"policy": pol, "tier": tier, "fill_model": model, "n": int(n),
                      "rest_hours": round((_f(rest) or 0.0) / 3600, 2), "capital_hours": _f(cap_h, 2),
                      "est_reward_usd": _f(rew, 4), "paired_pnl_usd": _f(paired, 4), "fees_usd": _f(fees, 4),
                      "single_leg_mtm_usd": _f(mtm, 4), "settlement_pnl_usd": _f(settle, 4),
                      "net_before_settlement_usd": _f(net_b, 4), "net_after_settlement_usd": _f(net_a, 4),
                      "n_settled": int(n_settled or 0),
                      "reward_per_capital_hour": (round(float(rew or 0) / float(cap_h), 6) if cap_h else None)})
    mix = session.execute(
        select(m.IncentiveShadowOutcome.fill_model, m.IncentiveShadowOutcome.outcome, func.count())
        .where(m.IncentiveShadowOutcome.ended_at >= since)
        .group_by(m.IncentiveShadowOutcome.fill_model, m.IncentiveShadowOutcome.outcome)).all()
    outcome_mix: dict[str, dict[str, int]] = {}
    for model, label, n in mix:
        outcome_mix.setdefault(model, {})[label] = int(n)
    # Q4: P(both | one) and the lag between legs, per model.
    both_given_one = {}
    for model in FILL_MODELS:
        counts = outcome_mix.get(model, {})
        both = counts.get("both_filled", 0)
        one = both + counts.get("yes_only", 0) + counts.get("no_only", 0) + counts.get("partial_both", 0) \
            + counts.get("partial_yes", 0) + counts.get("partial_no", 0)
        lag = session.scalar(select(func.avg(m.IncentiveShadowOutcome.seconds_between_legs)).where(
            m.IncentiveShadowOutcome.fill_model == model, m.IncentiveShadowOutcome.ended_at >= since,
            m.IncentiveShadowOutcome.seconds_between_legs.isnot(None)))
        both_given_one[model] = {"p_both_given_one": (round(both / one, 4) if one else None),
                                 "n_one_or_more": one, "mean_lag_seconds": _f(lag, 1)}
    # Q5: single-leg adverse selection: marks by horizon, per model.
    mark_rows = session.execute(
        select(m.IncentiveShadowMark.fill_model, m.IncentiveShadowMark.horizon_seconds, func.count(),
               func.avg(m.IncentiveShadowMark.pnl_at_bid_usd), func.min(m.IncentiveShadowMark.pnl_at_bid_usd))
        .where(m.IncentiveShadowMark.at >= since)
        .group_by(m.IncentiveShadowMark.fill_model, m.IncentiveShadowMark.horizon_seconds)).all()
    marks = [{"fill_model": mo, "horizon_seconds": h, "n": int(n), "mean_pnl_at_bid_usd": _f(avg, 4),
              "worst_pnl_at_bid_usd": _f(worst, 4)} for (mo, h, n, avg, worst) in mark_rows]
    end_reasons = dict(session.execute(
        select(m.IncentiveShadowQuote.end_reason, func.count())
        .where(m.IncentiveShadowQuote.ended_at >= since).group_by(m.IncentiveShadowQuote.end_reason)).all())
    return {"generated_at": _iso(now), "days": days, "by_day": by_day, "cells": cells,
            "outcome_mix": outcome_mix, "both_given_one": both_given_one, "marks": marks,
            "end_reasons": {str(k): int(v) for k, v in end_reasons.items()}}


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return round((xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2), 4)


def build_headline(session, *, days: int = 14, now: datetime | None = None) -> dict:
    """"If this had been running at $X capital for the window, what would each fill model have
    earned?" Per tier and policy, per model, with the observation span in days so the reader
    can see how much evidence sits behind each number. Portfolio framing: the tier is the
    per-program allocation; `programs_quoted` says how many programs were quoted at once."""
    now = _now(now)
    since = now - timedelta(days=max(1, min(days, 120)))
    first = session.scalar(select(func.min(m.IncentiveShadowOutcome.placed_at)).where(
        m.IncentiveShadowOutcome.ended_at >= since))
    span_days = ((now - _aware(first)).total_seconds() / 86400.0) if first else 0.0
    rows = session.execute(
        select(m.IncentiveShadowOutcome.policy, m.IncentiveShadowOutcome.capital_tier_usd,
               m.IncentiveShadowOutcome.fill_model, func.count(),
               func.count(func.distinct(m.IncentiveShadowOutcome.market_ticker)),
               func.sum(m.IncentiveShadowOutcome.est_reward_usd),
               func.sum(m.IncentiveShadowOutcome.paired_pnl_usd),
               func.sum(m.IncentiveShadowOutcome.fees_usd),
               func.sum(m.IncentiveShadowOutcome.single_leg_mtm_5m_usd),
               func.sum(m.IncentiveShadowOutcome.settlement_pnl_usd),
               func.sum(m.IncentiveShadowOutcome.net_before_settlement_usd),
               func.sum(m.IncentiveShadowOutcome.capital_hours),
               func.max(m.IncentiveShadowOutcome.net_before_settlement_usd))
        .where(m.IncentiveShadowOutcome.ended_at >= since)
        .group_by(m.IncentiveShadowOutcome.policy, m.IncentiveShadowOutcome.capital_tier_usd,
                  m.IncentiveShadowOutcome.fill_model)).all()
    table = []
    for (pol, tier, model, n, n_programs, rew, paired, fees, mtm, settle, net, cap_h, best) in rows:
        net_v = float(net or 0.0)
        table.append({
            "policy": pol, "tier": tier, "fill_model": model, "n_outcomes": int(n),
            "programs_quoted": int(n_programs), "est_reward_usd": _f(rew, 4),
            "paired_pnl_usd": _f(paired, 4), "fees_usd": _f(fees, 4), "single_leg_mtm_usd": _f(mtm, 4),
            "settlement_pnl_usd": _f(settle, 4), "net_usd": round(net_v, 4),
            "net_per_day_usd": (round(net_v / span_days, 4) if span_days > 0 else None),
            "capital_hours": _f(cap_h, 2),
            "net_per_capital_hour": (round(net_v / float(cap_h), 6) if cap_h else None),
            "largest_single_outcome_usd": _f(best, 4),
            "share_of_net_from_largest": (round(float(best) / net_v, 3) if net_v > 0 and best else None),
        })
    table.sort(key=lambda r: (r["policy"], r["tier"], FILL_MODELS.index(r["fill_model"])))
    return {"generated_at": _iso(now), "days": days, "span_days": round(span_days, 4),
            "conservative_model": MODEL_CONSERVATIVE, "optimistic_model": MODEL_OPTIMISTIC,
            "queue_aware_model": MODEL_QUEUE_AWARE, "table": table}
