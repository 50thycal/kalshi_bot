"""Canonical metric definitions + providers (spec §12) — the universal scoreboard.

One registry of metric definitions, one provider interface, one scope object that
carries the FULL identity of every computed value: experiment / version / epoch /
arm / deployments / concrete strategy tags / evidence window / platform snapshot.
Nothing here guesses scope — resolution happens in the evaluator, and a value
without a resolvable scope is never computed.

The zero / null / missing discipline (spec §29) is structural, not convention:

  * a COUNT over a healthy source with no qualifying rows is a MEANINGFUL ZERO
    (value 0.0, n=0, missing=False);
  * a MEAN/RATE over an empty sample is UNDEFINED-BUT-KNOWN-EMPTY (value None,
    n=0, missing=False, reason says so) — the sample floor or the evaluator's
    HOLD path handles it; it is never coerced to 0;
  * a metric with NO provider yet (the model-based book metrics) is MISSING
    (missing=True) with a `reference` pointing at the existing analysis script
    that remains its reference implementation — the evaluator turns required
    missing metrics into BLOCKED_DATA, because invalid evidence cannot satisfy
    a gate.

Universal providers read `paper_trades` through the deployment-arm strategy tags
of ONE deployment kind (explicit, default "paper" — live tags and twin tags are
never silently mixed into a paper aggregate). Windowing is on `created_at` (entry
time), matching the repo's cohort-floor practice; "settled" means every terminal
status that carries real P&L — settled, closed_sl, closed_tp, closed_timeout —
because filtering to 'settled' alone silently drops stop-closed trades (the
recorded mmsellA1–A3 reading error). `closed_void` (annulled market) is censored,
not an outcome, and is surfaced as its own metric instead of polluting n.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select

from ..models import PaperTrade

# Bump when the meaning/implementation of any universal metric changes; recorded on
# every evaluation so a verdict can never outlive the semantics that computed it.
METRICS_ENGINE_REVISION = "metrics_engine:pr6_fill_model_v1"

# Terminal paper-trade statuses that carry a real economic outcome.
SETTLED_STATUSES = ("settled", "closed_sl", "closed_tp", "closed_timeout")
VOID_STATUSES = ("closed_void",)


@dataclass(frozen=True)
class MetricScope:
    """The full identity behind one computed value. Every field is resolved by the
    evaluator before any computation — a scope is never partially guessed."""

    experiment_key: str
    version: int
    epoch_number: int
    arm_key: str | None  # None only for explicitly experiment-wide scopes
    deployment_kind: str  # paper | live | paper_twin
    strategy_tags: tuple[str, ...]  # concrete tags backing this scope (may be empty)
    deployment_keys: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    platform_snapshot_fingerprint: str

    def label(self) -> str:
        arm = self.arm_key or "(experiment)"
        return f"{self.experiment_key}/v{self.version}/e{self.epoch_number}/{arm}"


@dataclass(frozen=True)
class MetricValue:
    """One computed value with its evidence-accounting metadata."""

    metric: str
    value: float | None
    n: int  # observations backing the value
    unit: str
    missing: bool = False  # True only when the metric could not be computed AT ALL
    reason: str | None = None
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    unit: str
    description: str
    # "count" returns a meaningful zero on empty samples; "mean"/"rate" return
    # value None (undefined) on empty samples.
    kind: str  # count | mean | rate
    source: str  # what the provider reads (documentation + BLOCKED_DATA reporting)
    provided: bool = True  # False => declared, provider not yet implemented
    reference: str | None = None  # reference implementation for unprovided metrics


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_UNIVERSAL: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        key="realizable_cents_per_trade", unit="cents/trade", kind="mean",
        source="paper_trades x FILL_MODEL calibration",
        reference="kalshi_bot/fill_calibration.py (docs/MMSELL_FILL_MODEL.md)",
        description="paper P&L corrected for maker fillability via the live "
        "calibration; MISSING when no trusted cell covers the book's price mix",
    ),
    MetricDefinition(
        key="fill_model_coverage_pct", unit="%", kind="rate",
        source="paper_trades x FILL_MODEL calibration",
        reference="kalshi_bot/fill_calibration.py (docs/MMSELL_FILL_MODEL.md)",
        description="share of a book's settled trades priced in a trusted live "
        "fill cell — read the realizable number against this",
    ),
    MetricDefinition(
        key="clean_pairs", unit="pairs", kind="count",
        source="paper_trades x mmsell_settlement_meta (per-event legs)",
        reference="docs/MMSELL_ANCHOR_SET.md (pairing audit)",
        description="events holding exactly one settled YES leg and one settled NO "
        "leg; one-sided, same-side-multi-leg and part-settled events are excluded "
        "and counted in provenance",
    ),
    MetricDefinition(
        key="pair_win_rate_95lb_pct", unit="%", kind="rate",
        source="paper_trades x mmsell_settlement_meta (per-event legs)",
        reference="docs/MMSELL_ANCHOR_SET.md (pairing audit)",
        description="Clopper-Pearson exact one-sided 95% lower bound on the share of "
        "complete pairs with positive combined P&L",
    ),
    MetricDefinition(
        key="settled_trades", unit="trades", kind="count", source="paper_trades",
        description="terminal-with-P&L trades entered in the window "
        f"(status in {SETTLED_STATUSES})",
    ),
    MetricDefinition(
        key="settled_contracts", unit="contracts", kind="count", source="paper_trades",
        description="sum of contract quantity over settled trades",
    ),
    MetricDefinition(
        key="entries", unit="trades", kind="count", source="paper_trades",
        description="all trades entered in the window, any status (exposure basis)",
    ),
    MetricDefinition(
        key="open_trades", unit="trades", kind="count", source="paper_trades",
        description="trades entered in the window still open",
    ),
    MetricDefinition(
        key="voided_trades", unit="trades", kind="count", source="paper_trades",
        description="annulled-market trades (censored — excluded from settled n)",
    ),
    MetricDefinition(
        key="realized_pnl_usd", unit="USD", kind="count", source="paper_trades",
        description="sum of realized P&L over settled trades (0 when none settled)",
    ),
    MetricDefinition(
        key="pnl_cents_per_trade", unit="cents/trade", kind="mean", source="paper_trades",
        description="mean realized P&L per settled trade, in cents",
    ),
    MetricDefinition(
        key="pnl_cents_per_contract", unit="cents/contract", kind="mean",
        source="paper_trades",
        description="realized P&L per settled contract, in cents",
    ),
    MetricDefinition(
        key="win_rate_pct", unit="%", kind="rate", source="paper_trades",
        description="share of settled trades with positive P&L",
    ),
)

# Declared metrics whose canonical provider does not exist yet. Their reference
# implementations remain the existing analysis scripts; a gate clause requiring one
# evaluates BLOCKED_DATA — never silently skipped, never faked from a proxy.
_DECLARED_UNPROVIDED: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        key="realized_tail_hit_ratio_vs_modeled", unit="ratio", kind="mean",
        source="theta tail model", provided=False,
        reference="scripts/theta_fill_model.py (docs/THETA_THESIS.md)",
        description="observed tail-hit frequency over the model's prediction",
    ),
    MetricDefinition(
        key="live_settled_contracts", unit="contracts", kind="count",
        source="live orders/fills + settlements", provided=False,
        reference="scripts/mmsell_live.py (docs/LIVE_PAPER_TWIN.md)",
        description="live-executed contracts with settled outcomes",
    ),
    MetricDefinition(
        key="live_cents_per_contract", unit="cents/contract", kind="mean",
        source="live orders/fills + settlements", provided=False,
        reference="scripts/mmsell_live.py (docs/LIVE_PAPER_TWIN.md)",
        description="realized live P&L per settled contract",
    ),
    MetricDefinition(
        key="twin_live_winrate_gap_pp", unit="pp", kind="mean",
        source="live outcomes vs twin paper book", provided=False,
        reference="scripts/live_paper_parity.py (docs/LIVE_PAPER_TWIN.md)",
        description="twin paper win% minus live win% over matched windows",
    ),
    MetricDefinition(
        key="twin_live_gap_cents", unit="cents/contract", kind="mean",
        source="live outcomes vs twin paper book", provided=False,
        reference="scripts/mmsell_offset_ab.py (docs/MMSELL_OFFSET_AB.md)",
        description="twin paper c/ct minus live realized c/ct (the execution gap)",
    ),
    MetricDefinition(
        key="candidate_rejection_rate_pct", unit="%", kind="rate",
        source="scan cycle counters (not persisted)", provided=False,
        reference="MmsellCycleSummary.skipped_vol_gate (counted, never persisted)",
        description="share of candidates a book's entry gate rejected",
    ),
    # Probe-instrument metrics: probes are validation instruments, not deployments,
    # and their quantities are computed by the probe script itself (results are
    # recorded manually against the probe gate). They live in this registry like
    # everything else so every gate clause resolves against ONE namespace — an
    # unregistered metric key is always a typo or an undeclared quantity, never
    # silently accepted.
    MetricDefinition(
        key="pooled_cents_per_contract", unit="cents/contract", kind="mean",
        source="probe instrument (backtest over settled REST history)", provided=False,
        reference="scripts/kalshi_freeze_study.py (docs/FREEZE_THESIS.md)",
        description="FREEZE probe: pooled post-pin edge per contract",
    ),
    MetricDefinition(
        key="delta_vs_favorite_control_cents", unit="cents/contract", kind="mean",
        source="probe instrument (backtest over settled REST history)", provided=False,
        reference="scripts/kalshi_freeze_study.py (docs/FREEZE_THESIS.md)",
        description="FREEZE probe: edge over the open-window favorite control",
    ),
    MetricDefinition(
        key="wrong_pins", unit="markets", kind="count",
        source="probe instrument (backtest over settled REST history)", provided=False,
        reference="scripts/kalshi_freeze_study.py (docs/FREEZE_THESIS.md)",
        description="FREEZE probe: markets wrongly called dark/decided",
    ),
)

REGISTRY: dict[str, MetricDefinition] = {
    d.key: d for d in (*_UNIVERSAL, *_DECLARED_UNPROVIDED)
}

# Metrics computable as a PAIRED quantity over (treatment scope, control scope).
# `delta.<metric>` works for any provided universal metric; these are additional
# paired forms with their own semantics.
PAIRED_METRICS: dict[str, str] = {
    # rejection measured by entry-count differential — the registry's own method
    # for mmsellA4, valid only while the two books differ ONLY by the gate.
    "relative_entry_deficit_pct": "entries",
}


def is_delta_metric(key: str) -> bool:
    return key.startswith("delta.")


def delta_base(key: str) -> str:
    return key[len("delta."):]


def resolve_definition(key: str) -> MetricDefinition | None:
    """The definition a clause's metric key resolves to (delta.X resolves via X)."""
    if is_delta_metric(key):
        return REGISTRY.get(delta_base(key))
    if key in PAIRED_METRICS:
        return REGISTRY.get(PAIRED_METRICS[key])
    return REGISTRY.get(key)


# ---------------------------------------------------------------------------
# Universal provider (paper_trades)
# ---------------------------------------------------------------------------


def _paper_aggregates(session, scope: MetricScope) -> dict:
    """One pass over paper_trades for the scope's tags/window; returns raw sums."""
    if not scope.strategy_tags:
        return {"no_tags": True}
    settled = PaperTrade.status.in_(SETTLED_STATUSES)
    row = session.execute(
        select(
            func.count().filter(settled).label("n_settled"),
            func.coalesce(
                func.sum(PaperTrade.quantity).filter(settled), 0
            ).label("contracts"),
            func.coalesce(func.sum(PaperTrade.pnl).filter(settled), 0).label("pnl_usd"),
            func.count()
            .filter(settled, PaperTrade.pnl > 0)
            .label("n_wins"),
            func.count().label("n_entries"),
            func.count().filter(PaperTrade.status == "open").label("n_open"),
            func.count()
            .filter(PaperTrade.status.in_(VOID_STATUSES))
            .label("n_void"),
        ).where(
            PaperTrade.strategy.in_(scope.strategy_tags),
            PaperTrade.created_at >= scope.window_start,
            PaperTrade.created_at < scope.window_end,
        )
    ).one()
    return {
        "n_settled": int(row.n_settled or 0),
        "contracts": int(row.contracts or 0),
        "pnl_usd": float(row.pnl_usd or 0.0),
        "n_wins": int(row.n_wins or 0),
        "n_entries": int(row.n_entries or 0),
        "n_open": int(row.n_open or 0),
        "n_void": int(row.n_void or 0),
    }


def _price_histogram(session, scope: MetricScope) -> dict[int, tuple[int, float]]:
    """The scope's settled trades as yes-equivalent cent -> (n, sum P&L in cents).

    This is the only new read the fill-model metrics need: the projection is a
    property of a book's ENTRY-PRICE MIX, because fillability is a property of the
    price cell (a resting NO bid at 92c and a resting YES bid at 8c are the same
    book event). Trades with no recorded entry price cannot be placed in a cell and
    are counted as uncovered rather than guessed into one."""
    from ..fill_calibration import yes_equivalent_cents

    rows = session.execute(
        select(PaperTrade.side, PaperTrade.assumed_price, PaperTrade.pnl)
        .where(
            PaperTrade.strategy.in_(scope.strategy_tags),
            PaperTrade.created_at >= scope.window_start,
            PaperTrade.created_at <= scope.window_end,
            PaperTrade.status.in_(SETTLED_STATUSES),
        )
    ).all()
    hist: dict[int, tuple[int, float]] = {}
    unpriced = 0
    for side, price, pnl in rows:
        if price is None:
            unpriced += 1
            continue
        cent = yes_equivalent_cents((side or "yes").lower(), float(price))
        n, s = hist.get(cent, (0, 0.0))
        hist[cent] = (n + 1, s + float(pnl or 0.0) * 100.0)
    if unpriced:
        # Surfaced through the projection's coverage, never silently dropped.
        hist.setdefault(-1, (0, 0.0))
        hist[-1] = (hist[-1][0] + unpriced, hist[-1][1])
    return hist


def binomial_lower_bound_95(wins: int, n: int) -> float | None:
    """Clopper-Pearson exact one-sided 95% lower bound on a win rate, in percent.

    Exact rather than normal-approximate because the whole point of A5's gate is a
    small sample: the backtest's 23/23 (a 100% observed rate) has a lower bound of
    87.79%, which is why it FAILED a 93.9% bar. A Wald/normal interval collapses to
    zero width at 100% and would have passed it — the approximation error is the
    entire decision.

    Solved by bisection on the binomial tail P(X >= wins | p) = 0.05, which is
    monotonic in p, so no special-function dependency is needed. For wins == n this
    reduces to the closed form 0.05 ** (1/n), which the tests pin."""
    if n <= 0:
        return None
    if wins <= 0:
        return 0.0
    alpha = 0.05

    def tail(p: float) -> float:
        return sum(math.comb(n, k) * (p ** k) * ((1.0 - p) ** (n - k))
                   for k in range(wins, n + 1))

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if tail(mid) < alpha:
            lo = mid
        else:
            hi = mid
    return round(100.0 * lo, 4)


def _pair_rows(session, scope: MetricScope) -> dict:
    """Group the scope's settled evidence into per-EVENT legs.

    A5's strangle is a two-sided position: one cheap-YES leg and one cheap-NO leg on
    the same event. One settlement can never lose both, which is the whole thesis —
    so the unit of evidence is the PAIR, not the trade. Events are resolved through
    `mmsell_settlement_meta`, the same canonical ticker→event mapping the tracker's
    one-leg-per-side cap uses (`repository.event_has_strangle_leg`); market tickers
    are never string-parsed into events here.

    `docs/MMSELL_ANCHOR_SET.md` records why anything other than exactly-one-leg-per-
    side is not a pair: before the 2026-08-14 pairing boundary a single event could
    open four same-side legs on different strikes of one game. Those are positively
    correlated — precisely the risk a strangle exists to avoid — and counting them
    as pairs is what the boundary exists to prevent. The boundary itself is enforced
    by the GATE (its `evidence_started_at`), not hard-coded here."""
    from ..models import MmSellSettlementMeta

    rows = session.execute(
        select(PaperTrade.side, PaperTrade.pnl, PaperTrade.status,
               MmSellSettlementMeta.event_ticker)
        # OUTER join: a trade whose ticker has no settlement-meta row has no
        # resolvable event. It must be COUNTED as unmapped, not silently dropped
        # by an inner join — an event we cannot resolve is not an event we can
        # assert is one-sided.
        .outerjoin(MmSellSettlementMeta,
                   MmSellSettlementMeta.market_ticker == PaperTrade.market_ticker)
        .where(
            PaperTrade.strategy.in_(scope.strategy_tags),
            PaperTrade.created_at >= scope.window_start,
            PaperTrade.created_at <= scope.window_end,
        )
    ).all()

    events: dict[str, dict] = {}
    unmapped = 0
    for side, pnl, status, event in rows:
        if not event:
            unmapped += 1
            continue
        e = events.setdefault(event, {"yes": [], "no": []})
        leg = {"pnl": float(pnl) if pnl is not None else None,
               "settled": status in SETTLED_STATUSES}
        key = (side or "").lower()
        if key in e:
            e[key].append(leg)

    clean, one_sided, multi_leg, incomplete = [], 0, 0, 0
    for _event, legs in events.items():
        ny, nn = len(legs["yes"]), len(legs["no"])
        if ny == 0 or nn == 0:
            one_sided += 1
            continue
        if ny > 1 or nn > 1:
            # Same-side multi-leg: correlated strikes on one game, not a strangle.
            multi_leg += 1
            continue
        pair = legs["yes"] + legs["no"]
        if not all(p["settled"] and p["pnl"] is not None for p in pair):
            # Censored: one leg still open, so the pair's outcome is UNKNOWN. It is
            # not a loss and not a win; it is excluded and counted.
            incomplete += 1
            continue
        clean.append(sum(p["pnl"] for p in pair))
    return {
        "clean_pnls": clean,
        "one_sided_events": one_sided,
        "multi_leg_events": multi_leg,
        "incomplete_pairs": incomplete,
        "events_seen": len(events),
        "trades_without_event_mapping": unmapped,
    }


def _pair_metric(session, key: str, scope: MetricScope, prov: dict) -> MetricValue:
    """`clean_pairs` / `pair_win_rate_95lb_pct` for the two-sided strangle."""
    agg = _pair_rows(session, scope)
    pnls = agg["clean_pnls"]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    prov = prov | {
        "unit_of_evidence": "event pair (one YES leg + one NO leg)",
        "event_source": "mmsell_settlement_meta.event_ticker",
        "events_seen": agg["events_seen"],
        "clean_pairs": n,
        "one_sided_events": agg["one_sided_events"],
        "multi_leg_events": agg["multi_leg_events"],
        "incomplete_pairs_censored": agg["incomplete_pairs"],
        "trades_without_event_mapping": agg["trades_without_event_mapping"],
        "pair_wins": wins,
        "pairing_boundary": "enforced by the gate's evidence_started_at, not by this "
                            "provider",
    }
    if key == "clean_pairs":
        # A count: zero complete pairs over healthy sources is a MEANINGFUL zero.
        return MetricValue(key, float(n), n, "pairs", provenance=prov)

    if n == 0:
        return MetricValue(
            key, None, 0, "%",
            reason="no complete pairs in window — a bound over an empty sample is "
                   "undefined, not 0",
            provenance=prov,
        )
    return MetricValue(key, binomial_lower_bound_95(wins, n), n, "%", provenance=prov)


def _fill_model_metric(session, key: str, scope: MetricScope, prov: dict) -> MetricValue:
    """`realizable_cents_per_trade` / `fill_model_coverage_pct`, from the canonical
    calibration — the same numbers the evo sandbox and the ops report use.

    The projection corrects paper's headline for which trades a maker actually
    gets. Uncovered price cells (too few live fills to trust) are EXCLUDED, never
    estimated from a neighbour, so a book whose price mix the live calibration
    never reached reports `missing` rather than a confident wrong number."""
    from ..fill_calibration import (
        CALIBRATION_SOURCE,
        CALIBRATION_VERSION,
        MIN_CELL_FILLS,
        project_realizable,
    )

    hist = _price_histogram(session, scope)
    proj = project_realizable(hist)
    prov = prov | {
        "fill_calibration_version": CALIBRATION_VERSION,
        "fill_calibration_source": CALIBRATION_SOURCE,
        "min_cell_fills": MIN_CELL_FILLS,
        "platform_component": "FILL_MODEL",
        "total_n": proj["total_n"],
        "covered_n": proj["covered_n"],
        "optimistic_cents_per_trade": proj["opt_cents"],
    }
    total_n, covered_n = proj["total_n"], proj["covered_n"]

    if key == "fill_model_coverage_pct":
        if total_n == 0:
            return MetricValue(key, None, 0, "%",
                               reason="no settled trades in window", provenance=prov)
        return MetricValue(key, round(100.0 * covered_n / total_n, 2), total_n, "%",
                           provenance=prov)

    # realizable_cents_per_trade
    if total_n == 0:
        return MetricValue(key, None, 0, "cents/trade",
                           reason="no settled trades in window", provenance=prov)
    if covered_n == 0:
        # MISSING, not zero and not the optimistic number: the live calibration has
        # no trusted cell anywhere in this book's price mix, so it cannot say what a
        # maker would realize here. Answering anyway is the mmsell6/mmsell11 mistake.
        return MetricValue(
            key, None, 0, "cents/trade", missing=True,
            reason=(
                f"no trusted fill-model cell covers any of this book's {total_n} "
                f"settled entry prices (calibration {CALIBRATION_VERSION}, cells need "
                f">= {MIN_CELL_FILLS} live fills) — realizable P&L is unmeasured here, "
                "not zero"
            ),
            provenance=prov,
        )
    return MetricValue(key, round(proj["est_realizable_cents"], 4), covered_n,
                       "cents/trade", provenance=prov)


def _provenance(scope: MetricScope) -> dict:
    return {
        "source": "paper_trades",
        "strategy_tags": list(scope.strategy_tags),
        "deployment_kind": scope.deployment_kind,
        "deployments": list(scope.deployment_keys),
        "window": [str(scope.window_start), str(scope.window_end)],
        "window_basis": "created_at (entry time)",
        "settled_statuses": list(SETTLED_STATUSES),
        "platform_snapshot": scope.platform_snapshot_fingerprint[:16],
        "scope": scope.label(),
        "fee_basis": "as-recorded (epoch-floored evidence is post-2026-08-11 maker-fee model)",
    }


def compute_metric(session, key: str, scope: MetricScope) -> MetricValue:
    """Compute one non-delta metric for one scope. Never raises for empty data —
    empty is an answer; only an unknown/unprovided metric is `missing`."""
    definition = REGISTRY.get(key)
    if definition is None:
        return MetricValue(
            metric=key, value=None, n=0, unit="?", missing=True,
            reason=f"unknown metric {key!r} — not in the canonical registry",
        )
    if not definition.provided:
        return MetricValue(
            metric=key, value=None, n=0, unit=definition.unit, missing=True,
            reason=(
                f"no canonical provider yet for {key!r}; reference implementation: "
                f"{definition.reference}"
            ),
        )
    if not scope.strategy_tags:
        # A scope with no concrete tags of the requested deployment kind is a real
        # structural emptiness, not missing data — but a mean over it is undefined.
        empty_reason = (
            f"no {scope.deployment_kind!r} deployment tags for this scope in this epoch"
        )
        if definition.kind == "count":
            return MetricValue(
                metric=key, value=0.0, n=0, unit=definition.unit,
                reason=empty_reason, provenance=_provenance(scope),
            )
        return MetricValue(
            metric=key, value=None, n=0, unit=definition.unit,
            reason=empty_reason, provenance=_provenance(scope),
        )

    agg = _paper_aggregates(session, scope)
    prov = _provenance(scope)
    n = agg["n_settled"]

    if key == "settled_trades":
        return MetricValue(key, float(n), n, "trades", provenance=prov)
    if key == "settled_contracts":
        return MetricValue(key, float(agg["contracts"]), n, "contracts", provenance=prov)
    if key == "entries":
        return MetricValue(key, float(agg["n_entries"]), agg["n_entries"], "trades",
                           provenance=prov)
    if key == "open_trades":
        return MetricValue(key, float(agg["n_open"]), agg["n_open"], "trades",
                           provenance=prov)
    if key == "voided_trades":
        return MetricValue(key, float(agg["n_void"]), agg["n_void"], "trades",
                           provenance=prov)
    if key == "realized_pnl_usd":
        return MetricValue(key, round(agg["pnl_usd"], 4), n, "USD", provenance=prov)

    # Means/rates: undefined over an empty settled sample — never coerced to zero.
    if key == "pnl_cents_per_trade":
        if n == 0:
            return MetricValue(key, None, 0, "cents/trade",
                               reason="no settled trades in window", provenance=prov)
        return MetricValue(key, round(agg["pnl_usd"] * 100.0 / n, 4), n,
                           "cents/trade", provenance=prov)
    if key == "pnl_cents_per_contract":
        if agg["contracts"] == 0:
            return MetricValue(key, None, 0, "cents/contract",
                               reason="no settled contracts in window", provenance=prov)
        return MetricValue(key, round(agg["pnl_usd"] * 100.0 / agg["contracts"], 4), n,
                           "cents/contract", provenance=prov)
    if key == "win_rate_pct":
        if n == 0:
            return MetricValue(key, None, 0, "%",
                               reason="no settled trades in window", provenance=prov)
        return MetricValue(key, round(100.0 * agg["n_wins"] / n, 2), n, "%",
                           provenance=prov)
    if key in ("realizable_cents_per_trade", "fill_model_coverage_pct"):
        return _fill_model_metric(session, key, scope, prov)
    if key in ("clean_pairs", "pair_win_rate_95lb_pct"):
        return _pair_metric(session, key, scope, prov)

    return MetricValue(
        metric=key, value=None, n=0, unit=definition.unit, missing=True,
        reason=f"metric {key!r} registered but not routed — provider bug",
    )


def compute_paired_metric(
    session, key: str, treatment: MetricScope, control: MetricScope
) -> MetricValue:
    """delta.<metric> = treatment − control; plus the named paired forms.

    n = min(n_T, n_C): a delta is only as powered as its thinner side. Both sides'
    full provenance is preserved so the value can never shed its scopes."""
    if is_delta_metric(key):
        base = delta_base(key)
        t = compute_metric(session, base, treatment)
        c = compute_metric(session, base, control)
        prov = {"treatment": t.provenance | {"value": t.value, "n": t.n},
                "control": c.provenance | {"value": c.value, "n": c.n}}
        if t.missing or c.missing:
            return MetricValue(key, None, 0, t.unit, missing=True,
                               reason=t.reason if t.missing else c.reason,
                               provenance=prov)
        if t.value is None or c.value is None:
            side = treatment.label() if t.value is None else control.label()
            return MetricValue(key, None, min(t.n, c.n), t.unit,
                               reason=f"undefined over empty sample on {side}",
                               provenance=prov)
        return MetricValue(key, round(t.value - c.value, 4), min(t.n, c.n), t.unit,
                           provenance=prov)

    if key == "relative_entry_deficit_pct":
        t = compute_metric(session, "entries", treatment)
        c = compute_metric(session, "entries", control)
        prov = {"treatment": t.provenance | {"entries": t.value},
                "control": c.provenance | {"entries": c.value},
                "method": "entry-count differential (valid only while the pair "
                          "differs ONLY by the gated entry)"}
        if not c.value:  # zero or None control entries → rate undefined
            return MetricValue(key, None, 0, "%",
                               reason="control took no entries in window",
                               provenance=prov)
        deficit = 100.0 * (1.0 - float(t.value or 0.0) / float(c.value))
        return MetricValue(key, round(deficit, 2), int(min(t.n, c.n)), "%",
                           provenance=prov)

    return MetricValue(key, None, 0, "?", missing=True,
                       reason=f"unknown paired metric {key!r}")
