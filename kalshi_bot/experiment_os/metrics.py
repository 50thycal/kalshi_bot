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
from dataclasses import dataclass, field, replace
from datetime import datetime

from sqlalchemy import func, select

from ..models import PaperTrade

# Bump when the meaning/implementation of any universal metric changes; recorded on
# every evaluation so a verdict can never outlive the semantics that computed it.
METRICS_ENGINE_REVISION = "metrics_engine:pr6_fill_model_v1"

# Recorded in place of a provider revision while a metric is declared but has no
# implementation. Distinguishing this from a real revision is the whole point:
# a gate blocked for want of a provider and a gate evaluated by one must not share
# an identity.
UNPROVIDED_REVISION = "unprovided"

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
    # Standard error of `value`, when the provider can compute one. Required by
    # any clause carrying a `normal` bound; None means such a clause evaluates
    # MISSING rather than silently falling back to the point estimate.
    stderr: float | None = None


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
    # This metric's OWN implementation revision. Bumped when THIS provider's
    # meaning changes, which is a far smaller blast radius than the engine-wide
    # constant: a fill-model change must not invalidate a pair-metric verdict.
    # `UNPROVIDED_REVISION` is used while no provider exists, so "we could not
    # compute this" and "we computed it with implementation v1" are never the
    # same recorded identity.
    revision: str = "universal_v1"
    # Which way is "good" for THIS metric. Load-bearing because a delta inherits
    # its base metric's direction, and the two mmsell diagnostics point opposite
    # ways: `delta.live_cents_per_contract` positive means the treatment earned
    # more (better), while `delta.twin_live_gap_cents` positive means the
    # treatment suffered more adverse selection (worse). Left as prose, that is a
    # sign error waiting to happen in a gate; recorded here, provenance can state
    # what a positive value means and a test can check it.
    direction: str = "higher_better"  # higher_better | lower_better | neutral

    @property
    def effective_revision(self) -> str:
        return self.revision if self.provided else UNPROVIDED_REVISION

    @property
    def positive_means(self) -> str:
        return {
            "higher_better": "better",
            "lower_better": "worse",
        }.get(self.direction, "neither better nor worse — this metric is a count "
              "or a neutral quantity")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_UNIVERSAL: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        key="realizable_cents_per_trade", unit="cents/trade", kind="mean",
        source="paper_trades x FILL_MODEL calibration",
        reference="kalshi_bot/fill_calibration.py (docs/MMSELL_FILL_MODEL.md)",
        revision="fill_model_v1",
        description="paper P&L corrected for maker fillability via the live "
        "calibration; MISSING when no trusted cell covers the book's price mix",
    ),
    MetricDefinition(
        key="fill_model_coverage_pct", unit="%", kind="rate",
        source="paper_trades x FILL_MODEL calibration",
        reference="kalshi_bot/fill_calibration.py (docs/MMSELL_FILL_MODEL.md)",
        revision="fill_model_v1",
        description="share of a book's settled trades priced in a trusted live "
        "fill cell — read the realizable number against this",
    ),
    MetricDefinition(
        key="clean_pairs", direction="neutral", unit="pairs", kind="count",
        source="paper_trades x mmsell_settlement_meta (per-event legs)",
        reference="docs/MMSELL_ANCHOR_SET.md (pairing audit)",
        revision="pair_metrics_v1",
        description="events holding exactly one settled YES leg and one settled NO "
        "leg; one-sided, same-side-multi-leg and part-settled events are excluded "
        "and counted in provenance",
    ),
    MetricDefinition(
        key="pair_win_rate_95lb_pct", unit="%", kind="rate",
        source="paper_trades x mmsell_settlement_meta (per-event legs)",
        reference="docs/MMSELL_ANCHOR_SET.md (pairing audit)",
        revision="pair_metrics_v1",
        description="Clopper-Pearson exact one-sided 95% lower bound on the share of "
        "complete pairs with positive combined P&L",
    ),
    MetricDefinition(
        key="live_settled_contracts", direction="neutral", unit="contracts", kind="count",
        source="live_orders x fills x positions (settled live markets)",
        reference="scripts/mmsell_live.py (docs/LIVE_PAPER_TWIN.md)",
        revision="live_exec_v1",
        description="filled contracts on live markets whose position has closed; "
        "MISSING unless addressed at deployment_kind='live'",
    ),
    MetricDefinition(
        key="live_cents_per_contract", unit="cents/contract", kind="mean",
        source="live_orders x fills x positions (settled live markets)",
        reference="scripts/mmsell_live.py (docs/LIVE_PAPER_TWIN.md)",
        revision="live_exec_v1",
        description="total realized live P&L / actual filled contracts on settled "
        "markets — per CONTRACT, not per position; MISSING unless addressed at "
        "deployment_kind='live'",
    ),
    MetricDefinition(
        key="twin_live_winrate_gap_pp", direction="lower_better", unit="pp", kind="mean",
        source="positions (live) vs paper_trades (the registered twin)",
        reference="scripts/live_paper_parity.py (docs/LIVE_PAPER_TWIN.md)",
        revision="live_exec_v1",
        description="twin paper win% minus live win%; addressed at the LIVE scope "
        "and resolved against that deployment's registered twin — MISSING when no "
        "twin is registered",
    ),
    MetricDefinition(
        key="live_settled_markets", direction="neutral", unit="markets", kind="count",
        source="live_orders x fills x positions (settled live markets)",
        revision="live_exec_v1",
        description="settled live markets — the INDEPENDENT unit, since contracts "
        "on one market share one settlement; MISSING unless kind='live'",
    ),
    MetricDefinition(
        key="twin_mirror_coverage_pct", direction="higher_better", unit="%", kind="rate",
        source="live_orders vs the registered twin's paper_trades",
        revision="twin_coverage_v1",
        description="share of live markets ENTERED that the twin also entered — a "
        "twin mirroring a fraction of the book is not an execution control",
    ),
    MetricDefinition(
        key="twin_model_coverage_pct", direction="higher_better", unit="%", kind="rate",
        source="settled live markets vs the twin's model_probability",
        revision="twin_coverage_v1",
        description="share of settled live markets whose modeled probability "
        "resolves from the registered twin — the tail metric's denominator",
    ),
    MetricDefinition(
        key="twin_live_gap_cents", direction="lower_better", unit="cents/contract",
        kind="mean", source="twin paper rate vs live realized rate (each own set)",
        reference="scripts/mmsell_offset_ab.py (docs/MMSELL_OFFSET_AB.md)",
        revision="twin_gap_v1",
        description="twin c/ct minus live c/ct over each leg's OWN settled set — "
        "the adverse-selection read; higher is worse",
    ),
    MetricDefinition(
        key="twin_live_paired_gap_cents", direction="lower_better",
        unit="cents/contract", kind="mean",
        source="twin vs live on markets BOTH legs settled",
        revision="twin_gap_v1",
        description="per-market paired twin-minus-live difference — an execution "
        "FIDELITY check, not adverse selection: it conditions on live having "
        "filled, which is the channel adverse selection operates through",
    ),
    MetricDefinition(
        key="live_realized_pnl_usd", unit="USD", kind="count",
        source="positions (settled live markets this arm entered)",
        revision="live_exec_v1",
        description="total realized live P&L over settled live markets, in dollars "
        "— the quantity a total canary loss BUDGET is denominated in (the "
        "per-contract rate cannot express one); MISSING unless kind='live'",
    ),
    MetricDefinition(
        key="live_fill_rate_pct", unit="%", kind="rate",
        source="live_orders (entry buys that reached the venue) x fills",
        reference="scripts/live_paper_parity.py (docs/LIVE_PAPER_TWIN.md)",
        revision="live_exec_v1",
        description="filled contracts over contracts ORDERED on entry buys that "
        "actually reached the venue — the maker's realized fill rate; MISSING "
        "unless addressed at deployment_kind='live'",
    ),
    MetricDefinition(
        key="live_open_exposure_usd", direction="neutral", unit="USD", kind="count",
        source="live_orders (resting notional) + positions (held exposure)",
        reference="kalshi_bot/experiment_os/control_tower._live_exposure",
        revision="live_exec_v1",
        description="real money committed right now: resting-order notional plus "
        "held-position exposure. A stood-down book drains its resting orders "
        "within a cycle; its HELD positions are still real money",
    ),
    MetricDefinition(
        key="live_max_realized_loss_usd", direction="lower_better", unit="USD",
        kind="count",
        source="positions (settled live markets this arm entered)",
        revision="live_exec_v1",
        description="magnitude of the WORST single settled live market's realized "
        "loss (0.0 when no settled market lost) — the severity half of the tail "
        "read; MISSING unless addressed at deployment_kind='live'",
    ),
    MetricDefinition(
        key="live_tail_loss_markets", direction="lower_better", unit="markets",
        kind="count",
        source="positions (settled live markets this arm entered)",
        revision="live_exec_v1",
        description="settled live MARKETS that realized a loss. For a "
        "hold-to-settlement cheap-longshot maker book the losing market IS the "
        "tail event, so no cents threshold is invented; severity is "
        "live_max_realized_loss_usd",
    ),
    MetricDefinition(
        key="live_blocked_entries", direction="neutral", unit="candidates",
        kind="count",
        source="live_paper_parity_events (per-candidate live outcome) + live_orders",
        reference="kalshi_bot/twin/harness.py (docs/LIVE_PAPER_TWIN.md)",
        revision="live_exec_v1",
        description="candidates the twin took that live did NOT, because a risk "
        "gate stopped them — provenance carries the per-gate breakdown and "
        "venue rejections separately",
    ),
    MetricDefinition(
        key="settled_trades", direction="neutral", unit="trades", kind="count", source="paper_trades",
        description="terminal-with-P&L trades entered in the window "
        f"(status in {SETTLED_STATUSES})",
    ),
    MetricDefinition(
        key="settled_contracts", direction="neutral", unit="contracts", kind="count", source="paper_trades",
        description="sum of contract quantity over settled trades",
    ),
    MetricDefinition(
        key="entries", direction="neutral", unit="trades", kind="count", source="paper_trades",
        description="all trades entered in the window, any status (exposure basis)",
    ),
    MetricDefinition(
        key="open_trades", direction="neutral", unit="trades", kind="count", source="paper_trades",
        description="trades entered in the window still open",
    ),
    MetricDefinition(
        key="voided_trades", direction="neutral", unit="trades", kind="count", source="paper_trades",
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
        key="realized_tail_hit_ratio_vs_modeled", direction="lower_better", unit="ratio", kind="mean",
        source="settled markets x model_probability x settlement outcome",
        # Built from a written specification, since no reference implementation
        # exists to check against — scripts/theta_fill_model.py was cited here and
        # computes a maker-fill projection with no tail logic at all.
        reference=("NONE — specified in docs/RESEARCH_SUCCESSOR_GATE_DESIGN.md §4, "
                   "hypothesis in docs/THETA_THESIS.md"),
        revision="tail_v1",
        description="observed tail hits over the sum of modeled probabilities; "
        "counts MARKETS, not contracts (one tail hits or does not, once)",
    ),
    MetricDefinition(
        key="candidate_rejection_rate_pct", direction="neutral", unit="%", kind="rate",
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
        key="wrong_pins", direction="lower_better", unit="markets", kind="count",
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


# ---------------------------------------------------------------------------
# Live execution
# ---------------------------------------------------------------------------

#: Metrics that can ONLY be answered from real-money execution. Addressing one at
#: any other deployment kind is structurally impossible, not merely empty, and
#: returns MISSING — see `_live_metric`.
LIVE_ONLY_METRICS: frozenset[str] = frozenset({
    "live_settled_contracts", "live_cents_per_contract", "twin_live_winrate_gap_pp",
    "live_settled_markets", "live_fill_rate_pct", "live_open_exposure_usd",
    "live_max_realized_loss_usd", "live_tail_loss_markets", "live_blocked_entries",
    "live_realized_pnl_usd",
})

#: Metrics that compare a live book against its REGISTERED twin. Same live-only
#: addressing rule, resolved through `twin_of_deployment_id` rather than a `_pt`
#: naming convention.
TWIN_METRICS: frozenset[str] = frozenset({
    "twin_mirror_coverage_pct", "twin_model_coverage_pct",
    "realized_tail_hit_ratio_vs_modeled",
    "twin_live_gap_cents", "twin_live_paired_gap_cents",
})


def _live_market_rows(session, tags: tuple[str, ...], scope: MetricScope) -> dict:
    """Settled live economics for one arm's live tags, per CONTRACT.

    Three decisions here are deliberate and each one can move a promotion verdict:

    **Per contract, not per position.** `scripts/mmsell_live.py` reports
    `avg(realized_pnl)` over settled positions and labels it `live_$/ct`. That is
    dollars per POSITION; it equals per-contract only while every position is a
    1-lot. This provider divides total realized P&L by the contracts actually
    filled, so a 5-lot that lost a dollar counts as five contracts losing 20c
    each. The two agree on 1-contract positions and diverge on multi-contract
    ones, by design — `tests/test_experiment_os_live_metrics.py` pins both.

    **The denominator is restricted to SETTLED markets.** Realized P&L only
    exists for closed positions, so dividing it by every filled contract —
    including contracts still open — would understate the rate and would make the
    number move simply because new positions opened. Numerator and denominator
    are drawn from the same set of markets.

    **Entry fills only.** A position is entered by buys and closed by sells, and
    both produce fill rows. Counting both would double the denominator, so only
    fills belonging to `action='buy'` orders count as contracts transacted.

    Markets that more than one strategy tag traded are CONTESTED and excluded:
    `positions` is keyed by market, not by strategy, so a shared market's P&L
    cannot be split between arms. The reference script silently attributes the
    full position to every strategy that touched it; for an A/B promotion gate
    that would be double-counting. Exclusions are counted in provenance, never
    dropped silently."""
    from ..models import Fill, LiveOrder, Position

    # Markets this arm entered inside the window, and every strategy that traded
    # them (the contested check needs the full set, not just ours).
    mine_rows = session.execute(
        select(LiveOrder.market_ticker, LiveOrder.side)
        .where(
            LiveOrder.strategy.in_(tags),
            LiveOrder.action == "buy",
            LiveOrder.created_at >= scope.window_start,
            LiveOrder.created_at <= scope.window_end,
        )
        .distinct()
    ).all()
    # Side is carried per market because the tail metric needs the LIVE side to
    # decide what "the tail hit" means. A market entered on both sides is
    # ambiguous for that purpose and is recorded as such rather than guessed.
    sides: dict[str, set[str]] = {}
    for ticker, side in mine_rows:
        sides.setdefault(ticker, set()).add((side or "").lower())
    mine = set(sides)
    if not mine:
        return {"markets": 0, "settled_markets": 0, "contracts": 0, "pnl_usd": 0.0,
                "wins": 0, "contested_markets": 0, "open_markets": 0,
                "unpriced_markets": 0, "never_held_markets": 0, "per_market": [],
                "settled_tickers": [], "entered_tickers": [], "sides": {},
                "per_market_by_ticker": []}

    contested = set(session.execute(
        select(LiveOrder.market_ticker)
        .where(LiveOrder.market_ticker.in_(mine), LiveOrder.strategy.notin_(tags))
        .distinct()
    ).scalars().all())
    ours = mine - contested

    settled, open_markets, unpriced, never_held = set(), 0, 0, 0
    pnl_usd, wins = 0.0, 0
    cents_by_market: dict[str, float] = {}   # settled market -> realized cents
    for ticker in ours:
        # The NEWEST snapshot decides: `positions` is append-only, so an older row
        # showing quantity=0 may simply predate a re-entry.
        row = session.execute(
            select(Position.quantity, Position.realized_pnl)
            .where(Position.market_ticker == ticker)
            .order_by(Position.captured_at.desc())
            .limit(1)
        ).first()
        if row is None:
            # An order that never became a position — a resting maker order that
            # was cancelled or timed out. Counted SEPARATELY from a settled
            # position with a missing price: they are excluded for opposite
            # reasons, and merging them makes a normal book (most maker orders
            # never fill) look like a data-quality incident.
            never_held += 1
            continue
        qty, realized = row
        if qty is None or int(qty) != 0:
            open_markets += 1          # still carrying risk — outcome UNKNOWN
            continue
        if realized is None:
            unpriced += 1              # closed but unpriced — not a zero
            continue
        settled.add(ticker)
        pnl_usd += float(realized)
        if float(realized) > 0:
            wins += 1
        cents_by_market[ticker] = float(realized) * 100.0

    contracts = 0
    per_market: list[tuple[float, float]] = []
    if settled:
        # Per market, so the ratio estimator's standard error can be computed from
        # the same rows the value comes from rather than from a second query.
        by_market = dict(session.execute(
            select(LiveOrder.market_ticker, func.coalesce(func.sum(Fill.quantity), 0))
            .join(Fill, Fill.kalshi_order_id == LiveOrder.kalshi_order_id)
            .where(
                LiveOrder.strategy.in_(tags),
                LiveOrder.action == "buy",
                LiveOrder.market_ticker.in_(settled),
                LiveOrder.created_at >= scope.window_start,
                LiveOrder.created_at <= scope.window_end,
            )
            .group_by(LiveOrder.market_ticker)
        ).all())
        contracts = int(sum(by_market.values()))
        # Keyed by ticker on both sides — never positional, since `settled` is a set.
        per_market = [
            (cents_by_market[t], float(by_market.get(t, 0) or 0))
            for t in sorted(settled)
        ]

    return {
        "markets": len(mine),
        "settled_markets": len(settled),
        "contracts": contracts,
        "pnl_usd": pnl_usd,
        "wins": wins,
        "contested_markets": len(contested),
        "open_markets": open_markets,
        "unpriced_markets": unpriced,
        "never_held_markets": never_held,
        "per_market": per_market,
        "settled_tickers": sorted(settled),
        "sides": {t: (next(iter(v)) if len(v) == 1 else None)
                  for t, v in sides.items()},
        "entered_tickers": sorted(ours),
        "per_market_by_ticker": [
            (t, cents_by_market[t], float(by_market.get(t, 0) or 0))
            for t in sorted(settled)
        ] if settled else [],
    }


def _ratio_stderr(per_market: list[tuple[float, float]], ratio: float) -> float | None:
    """Standard error of a pooled per-contract rate, on the MARKET as the unit.

    The value is a ratio estimator, `sum(cents) / sum(contracts)`, not a mean of
    per-market rates — so its uncertainty is the ratio-estimator variance, not the
    dispersion of the rates. Contracts held on one market share one settlement and
    are perfectly correlated for it, which is why n here counts markets:

        r_i = cents_i - R * contracts_i          (residuals, mean 0 by construction)
        SE  = sqrt( sum r_i^2 / (n-1) ) / ( sqrt(n) * mean(contracts) )

    Returns None below two markets, where the variance is undefined — a clause
    carrying a normal bound then evaluates MISSING rather than inventing one."""
    n = len(per_market)
    if n < 2:
        return None
    qbar = sum(q for _c, q in per_market) / n
    if qbar <= 0:
        return None
    ss = sum((c - ratio * q) ** 2 for c, q in per_market)
    return math.sqrt(ss / (n - 1)) / (math.sqrt(n) * qbar)


def _twin_tags(session, scope: MetricScope) -> tuple[tuple[str, ...], str | None]:
    """The registered twin's tags for this arm, or a reason there are none.

    Resolved from the deployment graph — `twin_of_deployment_id` — not from a
    naming convention. A tag that merely looks like a twin (`*_pt3`) is not one."""
    from .models import ExperimentArm, ExperimentDeployment, ExperimentDeploymentArm

    if not scope.deployment_keys:
        return (), "the live scope names no deployment to find a twin of"
    live_ids = session.execute(
        select(ExperimentDeployment.id).where(
            ExperimentDeployment.deployment_key.in_(scope.deployment_keys),
            ExperimentDeployment.kind == "live",
        )
    ).scalars().all()
    if not live_ids:
        return (), "no live deployment in this scope"
    tags = session.execute(
        select(ExperimentDeploymentArm.strategy_tag)
        .join(ExperimentDeployment,
              ExperimentDeployment.id == ExperimentDeploymentArm.deployment_id)
        # Match the SAME arm: a twin runs every arm the live book runs, and
        # comparing one arm's live outcomes against another arm's paper twin
        # would silently answer a different question.
        .join(ExperimentArm, ExperimentArm.id == ExperimentDeploymentArm.arm_id)
        .where(
            ExperimentDeployment.twin_of_deployment_id.in_(live_ids),
            ExperimentArm.arm_key == scope.arm_key,
            ExperimentDeploymentArm.strategy_tag.is_not(None),
        )
    ).scalars().all()
    if not tags:
        return (), (
            "no paper_twin deployment is registered against this live deployment "
            f"for arm {scope.arm_key!r}"
        )
    return tuple(sorted(set(tags))), None


def live_open_exposure(session, tags: list[str]) -> dict:
    """Real money these tags have committed RIGHT NOW — resting orders AND held
    positions. Deliberately window-free: "what is still exposed?" is a question
    about the present, and an entry made before the window is exactly the money an
    operator most needs to see during a stand-down.

    Both halves are counted because only one of them used to be. A RESTING order is
    money that could be committed; a FILLED position is money that already is. When
    live entry stands down, every resting order drains within a cycle and the
    resting number goes to $0.00 while the positions those orders produced sit open
    — measured 2026-08-20 mid-pause: 25 open positions holding $43.04, reported as
    "$0.00 at risk".

    Shared with the Control Tower's `at risk` column, which delegates here, so the
    operator-facing number and the gate-facing number cannot drift apart."""
    from ..models import LiveOrder, Position

    empty = {"open_orders": 0, "contracts": 0, "notional_usd": 0.0,
             "open_positions": 0, "position_usd": 0.0, "total_usd": 0.0}
    if not tags:
        return empty
    rows = session.execute(
        select(LiveOrder.status, func.count(), func.sum(LiveOrder.quantity),
               func.sum(LiveOrder.quantity * LiveOrder.limit_price))
        .where(LiveOrder.strategy.in_(tags))
        .group_by(LiveOrder.status)
    ).all()
    resting = {"pending", "resting", "open", "partially_filled"}
    open_orders = contracts = 0
    notional = 0.0
    for status, n, qty, cents in rows:
        if (status or "").lower() in resting:
            open_orders += int(n or 0)
            contracts += int(qty or 0)
            notional += float(cents or 0) / 100.0

    # Held positions: the NEWEST snapshot per market these tags entered, since
    # `positions` is append-only and an older row may predate a later exit.
    tickers = session.execute(
        select(LiveOrder.market_ticker)
        .where(LiveOrder.strategy.in_(tags), LiveOrder.action == "buy")
        .distinct()
    ).scalars().all()
    n_pos, pos_usd = 0, 0.0
    for ticker in tickers:
        row = session.execute(
            select(Position.quantity, Position.quantity_fp, Position.market_exposure)
            .where(Position.market_ticker == ticker)
            .order_by(Position.captured_at.desc())
            .limit(1)
        ).first()
        if row is None:
            continue
        qty, qty_fp, exposure = row
        held = float(qty_fp) if qty_fp is not None else float(qty or 0)
        if abs(held) > 0.01:          # sub-0.01 dust cannot be traded out
            n_pos += 1
            pos_usd += float(exposure or 0)
    return {"open_orders": open_orders, "contracts": contracts,
            "notional_usd": round(notional, 2),
            "open_positions": n_pos, "position_usd": round(pos_usd, 2),
            "total_usd": round(notional + pos_usd, 2)}


#: `live_orders.status` values meaning the order reached Kalshi and was therefore
#: GIVEN a chance to fill. The fill-rate denominator is exactly this set: an order
#: that never reached the venue did not fail to fill, and counting it would
#: understate the maker's realized fill rate by however many sends errored.
_SENT_ORDER_STATUSES = frozenset({
    "resting", "open", "partially_filled", "filled", "canceled", "cancelled",
    "expired", "settled", "submitted",
})
#: Sent-or-not is genuinely unknown for these — reconcile resolves them later. They
#: are excluded from BOTH sides and counted, rather than guessed into either.
_INDETERMINATE_ORDER_STATUSES = frozenset({"unknown", "pending"})


def _live_fill_rows(session, tags: tuple[str, ...], scope: MetricScope) -> dict:
    """Ordered-vs-filled contracts on this arm's ENTRY buys inside the window.

    Entry buys only. A position is entered by buys and closed by sells, and both
    produce orders and fills; counting closes would measure a taker's exit, not the
    resting maker's entry fill rate, which is the whole quantity the twin exists to
    price."""
    from ..models import Fill, LiveOrder

    rows = session.execute(
        select(LiveOrder.kalshi_order_id, LiveOrder.status, LiveOrder.quantity)
        .where(
            LiveOrder.strategy.in_(tags),
            LiveOrder.action == "buy",
            LiveOrder.created_at >= scope.window_start,
            LiveOrder.created_at <= scope.window_end,
        )
    ).all()
    ordered = 0
    sent_ids: list[str] = []
    never_sent = indeterminate = 0
    for koid, status, qty in rows:
        st = (status or "").lower()
        if st in _INDETERMINATE_ORDER_STATUSES:
            indeterminate += 1
            continue
        if st not in _SENT_ORDER_STATUSES:
            never_sent += 1          # rejected / error — never given a chance
            continue
        ordered += int(qty or 0)
        if koid:
            sent_ids.append(koid)
    filled = 0
    if sent_ids:
        filled = int(session.scalar(
            select(func.coalesce(func.sum(Fill.quantity), 0))
            .where(Fill.kalshi_order_id.in_(sent_ids), Fill.action == "buy")
        ) or 0)
    return {
        "ordered_contracts": ordered,
        "filled_contracts": filled,
        "sent_orders": len(sent_ids),
        "excluded_never_sent": never_sent,
        "excluded_indeterminate": indeterminate,
    }


#: Live outcome codes on the parity tape that are NOT a risk-gate block: the entry
#: succeeded, or no live attempt was made for this candidate at all.
_NON_BLOCK_LIVE_OUTCOMES = frozenset({"placed", "not_attempted", "unknown"})


def _live_blocked_entries(session, key: str, scope: MetricScope, prov: dict):
    """Candidates the TWIN took that live did not, broken down by the gate that
    stopped each one.

    Read off the parity tape rather than off `live_orders`, because a gate block
    places no order — there is nothing in `live_orders` to count. The tape is the
    only record that the candidate existed and what stopped it.

    The denominator condition is `twin_outcome == 'opened'`: a candidate the twin
    also declined is not a live-side block, and counting it would attribute the
    twin's own cap to the live risk engine."""
    from ..models import LivePaperParityEvent

    rows = session.execute(
        select(LivePaperParityEvent.live_outcome, func.count())
        .where(
            LivePaperParityEvent.live_tag.in_(scope.strategy_tags),
            LivePaperParityEvent.twin_outcome == "opened",
            LivePaperParityEvent.recorded_at >= scope.window_start,
            LivePaperParityEvent.recorded_at <= scope.window_end,
        )
        .group_by(LivePaperParityEvent.live_outcome)
    ).all()
    by_gate: dict[str, int] = {}
    placed = other = 0
    for outcome, n in rows:
        code = (outcome or "").strip()
        if code in _NON_BLOCK_LIVE_OUTCOMES:
            if code == "placed":
                placed += int(n)
            else:
                other += int(n)
            continue
        by_gate[code] = by_gate.get(code, 0) + int(n)
    blocked = sum(by_gate.values())
    # Venue-side refusals are a DIFFERENT failure from our own risk gates (their
    # remedy is an order-shape fix, not a cap change), so they are reported beside
    # the breakdown rather than inside it.
    from ..models import LiveOrder

    rejected = int(session.scalar(
        select(func.count()).select_from(LiveOrder).where(
            LiveOrder.strategy.in_(scope.strategy_tags),
            LiveOrder.action == "buy",
            func.lower(LiveOrder.status).in_(("rejected", "error")),
            LiveOrder.created_at >= scope.window_start,
            LiveOrder.created_at <= scope.window_end,
        )
    ) or 0)
    return MetricValue(
        key, float(blocked), placed + blocked, "candidates",
        provenance=prov | {
            "by_gate": dict(sorted(by_gate.items())),
            "twin_opened_and_live_placed": placed,
            "twin_opened_live_no_attempt": other,
            "venue_rejected_orders": rejected,
        },
    )


def _live_metric(session, key: str, scope: MetricScope) -> MetricValue:
    """The three live-execution providers.

    The addressing rule is the point of this function. A live metric requested at
    `deployment_kind="paper"` returns MISSING with the mismatch named — it does
    NOT quietly read the live deployment instead. Two imported live-canary gates
    are malformed in exactly that way (their clauses default to `"paper"` while
    their epochs hold only live and paper_twin). A provider that inferred "they
    probably meant live" would make those gates appear to work, hide the defect
    that a corrected Version exists to fix, and let a promotion turn on evidence
    the registered contract never asked for."""
    definition = REGISTRY[key]
    if scope.deployment_kind != "live":
        return MetricValue(
            metric=key, value=None, n=0, unit=definition.unit, missing=True,
            reason=(
                f"{key!r} measures real-money execution and is only defined at "
                f"deployment_kind='live'; this clause addresses "
                f"{scope.deployment_kind!r}. The provider will not substitute a "
                "different deployment kind — correct the gate's addressing"
            ),
            provenance=_live_provenance(scope) | {"addressing_error": True},
        )
    if not scope.strategy_tags:
        return MetricValue(
            metric=key, value=None, n=0, unit=definition.unit, missing=True,
            reason="no live deployment tags for this scope in this epoch",
            provenance=_live_provenance(scope),
        )

    agg = _live_market_rows(session, scope.strategy_tags, scope)
    prov = _live_provenance(scope) | {
        "live_markets_entered": agg["markets"],
        "settled_markets": agg["settled_markets"],
        "filled_contracts_on_settled_markets": agg["contracts"],
        "realized_pnl_usd": round(agg["pnl_usd"], 4),
        "winning_settled_markets": agg["wins"],
        "excluded_contested_markets": agg["contested_markets"],
        "excluded_still_open_markets": agg["open_markets"],
        "excluded_unpriced_markets": agg["unpriced_markets"],
        "orders_that_never_held_a_position": agg["never_held_markets"],
    }

    if key == "live_settled_markets":
        # The INDEPENDENT unit. Contracts on one market share one settlement, so a
        # floor denominated in contracts overstates precision by the contracts-per-
        # market factor — measured 1.4x to 3.0x on these books.
        return MetricValue(key, float(agg["settled_markets"]), agg["settled_markets"],
                           "markets", provenance=prov)
    if key == "live_settled_contracts":
        # A count: zero settled contracts is a real answer, and a sample floor
        # reading it as 0 is exactly right.
        return MetricValue(key, float(agg["contracts"]), agg["settled_markets"],
                           "contracts", provenance=prov)

    if key == "live_cents_per_contract":
        if agg["contracts"] == 0:
            return MetricValue(
                key, None, 0, "cents/contract",
                reason="no settled live contracts in window", provenance=prov,
            )
        # n is CONTRACTS — the rate's own denominator — so `value * n` reproduces
        # the realized total. Reporting markets here would describe a per-contract
        # rate with a per-market sample count. The STANDARD ERROR, by contrast,
        # counts markets: that is the independent unit.
        ratio = agg["pnl_usd"] * 100.0 / agg["contracts"]
        se = _ratio_stderr(agg["per_market"], ratio)
        return MetricValue(
            key, round(ratio, 4), agg["contracts"], "cents/contract",
            provenance=prov | {
                "stderr_basis": "ratio estimator over settled MARKETS",
                "stderr_n_markets": agg["settled_markets"],
            },
            stderr=se,
        )

    if key == "live_realized_pnl_usd":
        # A count, so an empty book answers 0.0: no settled market means nothing
        # realized, which is the right reading for a LOSS BUDGET (a budget is not
        # breached by a book that has not traded). The companion floor on
        # live_settled_contracts is what distinguishes that from a healthy zero.
        return MetricValue(key, round(agg["pnl_usd"], 4), agg["settled_markets"],
                           "USD", provenance=prov)

    if key == "live_max_realized_loss_usd":
        # A magnitude, not a signed P&L: "the worst market lost $1.20" reads the
        # same way whichever sign convention the caller expects, and `lower_better`
        # then means what it says. 0.0 with settled markets present is a real
        # answer (nothing lost); 0.0 with NO settled markets is not, so that case
        # reports undefined rather than a reassuring zero.
        losses = [-c / 100.0 for _t, c, _q in agg["per_market_by_ticker"] if c < 0]
        if not agg["settled_tickers"]:
            return MetricValue(
                key, None, 0, "USD",
                reason="no settled live markets in window", provenance=prov,
            )
        return MetricValue(key, round(max(losses, default=0.0), 4),
                           agg["settled_markets"], "USD",
                           provenance=prov | {"losing_markets": len(losses)})

    if key == "live_tail_loss_markets":
        # Counting MARKETS, not contracts: contracts on one market share one
        # settlement, so a 5-lot that lost is one tail event, not five.
        losses = [(t, c) for t, c, _q in agg["per_market_by_ticker"] if c < 0]
        return MetricValue(
            key, float(len(losses)), agg["settled_markets"], "markets",
            provenance=prov | {
                "loss_usd_total": round(sum(c for _t, c in losses) / 100.0, 4),
                "worst_market_loss_usd": round(
                    max((-c / 100.0 for _t, c in losses), default=0.0), 4),
            },
        )

    if key == "live_open_exposure_usd":
        exp = live_open_exposure(session, list(scope.strategy_tags))
        return MetricValue(
            key, exp["total_usd"], exp["open_orders"] + exp["open_positions"], "USD",
            provenance=prov | {"exposure": exp},
        )

    if key == "live_fill_rate_pct":
        fills = _live_fill_rows(session, scope.strategy_tags, scope)
        if fills["ordered_contracts"] == 0:
            return MetricValue(
                key, None, 0, "%",
                reason=(
                    "no entry-buy contracts reached the venue in this window "
                    f"({fills['excluded_never_sent']} order(s) never sent, "
                    f"{fills['excluded_indeterminate']} indeterminate)"
                ),
                provenance=prov | fills,
            )
        pct = 100.0 * fills["filled_contracts"] / fills["ordered_contracts"]
        return MetricValue(key, round(pct, 4), fills["ordered_contracts"], "%",
                           provenance=prov | fills)

    if key == "live_blocked_entries":
        return _live_blocked_entries(session, key, scope, prov)

    # twin_live_winrate_gap_pp
    twin_tags, why = _twin_tags(session, scope)
    prov = prov | {"twin_tags": list(twin_tags)}
    if not twin_tags:
        return MetricValue(
            key, None, 0, "pp", missing=True,
            reason=f"cannot compare against a twin: {why}", provenance=prov,
        )
    live_n = agg["settled_markets"]
    twin = _paper_aggregates(session, replace(
        scope, deployment_kind="paper_twin", strategy_tags=twin_tags,
    ))
    prov = prov | {"twin_settled_trades": twin["n_settled"],
                   "twin_wins": twin["n_wins"], "live_settled_markets": live_n}
    if live_n == 0 or twin["n_settled"] == 0:
        return MetricValue(
            key, None, 0, "pp",
            reason=(
                "win-rate gap is undefined without settled evidence on both legs "
                f"(live {live_n}, twin {twin['n_settled']})"
            ),
            provenance=prov,
        )
    live_win = 100.0 * agg["wins"] / live_n
    twin_win = 100.0 * twin["n_wins"] / twin["n_settled"]
    prov = prov | {"live_win_pct": round(live_win, 4),
                   "twin_win_pct": round(twin_win, 4)}
    # n is the SMALLER leg: a sample floor must bind on the leg that limits the
    # comparison, not on whichever side happens to have more evidence.
    return MetricValue(key, round(twin_win - live_win, 4),
                       min(live_n, twin["n_settled"]), "pp", provenance=prov)


#: Minimum share of the evidence set whose modeled probability must resolve from
#: the twin before the tail ratio is trustworthy. Below it the metric is MISSING:
#: the surviving markets were selected by a data defect, so the bias in R has an
#: unknown DIRECTION — worse than a wide interval, which at least advertises its
#: own width. Derived in docs/RESEARCH_SUCCESSOR_GATE_DESIGN.md §5.8 from a bias
#: bound, not from any observed coverage.
MIN_TWIN_MODEL_COVERAGE_PCT = 90.0


def _twin_paper_rows(session, twin_tags: tuple[str, ...], scope: MetricScope) -> dict:
    """The twin's settled rows, keyed by market, with model probability and outcome.

    The twin is the measurement instrument for anything the live tables cannot
    record. `live_orders` carries no `model_probability`, and a live position that
    was exited early carries a P&L sign that is not a settlement outcome — so both
    the modeled probability AND the tail-hit outcome come from the twin, which
    holds to settlement on the same market. Settlement is a property of the
    market, not of who held it."""
    rows = session.execute(
        select(PaperTrade.market_ticker, PaperTrade.model_probability,
               PaperTrade.resolved_value, PaperTrade.pnl, PaperTrade.quantity,
               PaperTrade.status, PaperTrade.side)
        .where(
            PaperTrade.strategy.in_(twin_tags),
            PaperTrade.created_at >= scope.window_start,
            PaperTrade.created_at <= scope.window_end,
        )
    ).all()
    out: dict[str, dict] = {}
    for ticker, p, resolved, pnl, qty, status, side in rows:
        cur = out.setdefault(ticker, {"model_p": None, "resolved": None,
                                      "pnl": 0.0, "qty": 0, "settled": False,
                                      "sides": set(), "yes_resolved": None})
        cur["sides"].add((side or "").lower())
        if p is not None and cur["model_p"] is None:
            cur["model_p"] = float(p)
        if resolved is not None and cur["resolved"] is None:
            cur["resolved"] = int(resolved)
            # `resolved_value` is the settlement value FOR THAT TRADE'S SIDE, not
            # a property of the market. Translate it into the market's own
            # outcome — did YES resolve — so the tail classification cannot
            # silently invert when the twin holds the opposite side.
            sl = (side or "").lower()
            if sl == "yes":
                cur["yes_resolved"] = int(resolved) == 100
            elif sl == "no":
                cur["yes_resolved"] = int(resolved) == 0
        if status in SETTLED_STATUSES and pnl is not None:
            cur["settled"] = True
            cur["pnl"] += float(pnl)
            cur["qty"] += int(qty or 0)
    return out


def _twin_metric(session, key: str, scope: MetricScope) -> MetricValue:
    """Coverage, the tail ratio, and the two gap diagnostics.

    All are addressed at the LIVE scope and resolved against that deployment's
    registered twin — the structural `twin_of_deployment_id` edge, never a `_pt`
    naming convention."""
    definition = REGISTRY[key]
    if scope.deployment_kind != "live":
        return MetricValue(
            metric=key, value=None, n=0, unit=definition.unit, missing=True,
            reason=(
                f"{key!r} compares a live book against its registered twin and is "
                f"only defined at deployment_kind='live'; this clause addresses "
                f"{scope.deployment_kind!r}"
            ),
            provenance=_live_provenance(scope) | {"addressing_error": True},
        )
    if not scope.strategy_tags:
        return MetricValue(metric=key, value=None, n=0, unit=definition.unit,
                           missing=True,
                           reason="no live deployment tags for this scope in this epoch",
                           provenance=_live_provenance(scope))
    twin_tags, why = _twin_tags(session, scope)
    prov = _live_provenance(scope) | {"twin_tags": list(twin_tags)}
    if not twin_tags:
        return MetricValue(metric=key, value=None, n=0, unit=definition.unit,
                           missing=True,
                           reason=f"cannot compare against a twin: {why}",
                           provenance=prov)

    twin = _twin_paper_rows(session, twin_tags, scope)
    agg = _live_market_rows(session, scope.strategy_tags, scope)
    settled_live = agg["settled_tickers"]
    prov = prov | {"settled_live_markets": len(settled_live),
                   "twin_markets_seen": len(twin)}

    if key == "twin_mirror_coverage_pct":
        # Denominator is markets ENTERED, not settled: the mirror fires at entry,
        # so a mirror that never fired is invisible in the settled set.
        entered = agg["entered_tickers"]
        if not entered:
            return MetricValue(key, None, 0, "%", reason="no live markets entered",
                               provenance=prov)
        hit = sum(1 for t in entered if t in twin)
        return MetricValue(key, round(100.0 * hit / len(entered), 2), len(entered),
                           "%", provenance=prov | {"live_markets_entered": len(entered),
                                                   "mirrored": hit})

    if key == "twin_model_coverage_pct":
        if not settled_live:
            return MetricValue(key, None, 0, "%",
                               reason="no settled live markets in window",
                               provenance=prov)
        covered = sum(1 for t in settled_live
                      if twin.get(t, {}).get("model_p") is not None)
        return MetricValue(key, round(100.0 * covered / len(settled_live), 2),
                           len(settled_live), "%",
                           provenance=prov | {"covered": covered,
                                              "uncovered": len(settled_live) - covered})

    if key == "realized_tail_hit_ratio_vs_modeled":
        return _tail_ratio(key, settled_live, twin, agg["sides"], prov)

    if key == "twin_live_gap_cents":
        # Each leg over its OWN settled set — different denominators on purpose.
        # The adverse selection this measures lives in WHICH markets each book
        # ends up holding, so restricting to shared markets would define it away.
        tw = [(v["pnl"] * 100.0, v["qty"]) for v in twin.values()
              if v["settled"] and v["qty"] > 0]
        tq = sum(q for _c, q in tw)
        if not tw or tq == 0 or agg["contracts"] == 0:
            return MetricValue(key, None, 0, "cents/contract",
                               reason="both legs need settled contracts for a gap",
                               provenance=prov)
        twin_rate = sum(c for c, _q in tw) / tq
        live_rate = agg["pnl_usd"] * 100.0 / agg["contracts"]
        return MetricValue(
            key, round(twin_rate - live_rate, 4),
            min(len(tw), len(settled_live)), "cents/contract",
            provenance=prov | {"twin_cents_per_contract": round(twin_rate, 4),
                               "live_cents_per_contract": round(live_rate, 4),
                               "basis": "each leg over its own settled set (unpaired)"},
        )

    # twin_live_paired_gap_cents — per-market, both legs settled.
    pairs = []
    for ticker, live_c, live_q in agg["per_market_by_ticker"]:
        t = twin.get(ticker)
        if not t or not t["settled"] or t["qty"] <= 0 or live_q <= 0:
            continue
        pairs.append((t["pnl"] * 100.0 / t["qty"]) - (live_c / live_q))
    if not pairs:
        return MetricValue(key, None, 0, "cents/contract",
                           reason="no market has BOTH legs settled",
                           provenance=prov)
    mean = sum(pairs) / len(pairs)
    se = None
    if len(pairs) >= 2:
        var = sum((x - mean) ** 2 for x in pairs) / (len(pairs) - 1)
        se = math.sqrt(var / len(pairs))
    return MetricValue(
        key, round(mean, 4), len(pairs), "cents/contract",
        provenance=prov | {
            "paired_markets": len(pairs),
            "basis": (
                "per-market paired difference; conditions on live having FILLED, "
                "so it measures execution fidelity and NOT adverse selection — "
                "adverse selection operates through which orders fill"
            ),
        },
        stderr=se,
    )


def _tail_ratio(key: str, settled_live: list[str], twin: dict,
                live_sides: dict, prov: dict) -> MetricValue:
    """R = observed tail hits / sum of modeled probabilities, over settled MARKETS.

    A tail "hits" when the side the LIVE book sold loses at settlement. Three
    substitutions are deliberately refused along the way, because each would
    change the numerator without changing the denominator:

    * **not the live P&L sign.** A live position exited early under TP/SL loses
      money without the tail hitting at all.
    * **not the twin trade's P&L classification.** `resolved_value` is the
      settlement value FOR THAT TRADE'S SIDE, not a property of the market. It is
      translated into the market's own outcome — did YES resolve — so the
      classification cannot invert when the twin holds the other side.
    * **not a twin on the other side of the same market.** A twin holding the
      opposite side is not a mirror of the live position; such a market is
      excluded and counted, never read as though the sides agreed.

    Markets whose modeled probability cannot be resolved are excluded from BOTH O
    and E, never imputed from the book's mean: imputing pulls R toward 1, which is
    toward PASSING. Above the coverage threshold that exclusion is small enough to
    bound; below it the metric is MISSING."""
    covered, side_mismatch, side_unknown = [], 0, 0
    for t in settled_live:
        row = twin.get(t)
        if not row or row.get("model_p") is None:
            continue
        live_side = live_sides.get(t)
        twin_sides = row.get("sides") or set()
        if live_side is None or len(twin_sides) != 1:
            side_unknown += 1          # entered on both sides — not a clean mirror
            continue
        if next(iter(twin_sides)) != live_side:
            side_mismatch += 1
            continue
        covered.append((t, row))
    total = len(settled_live)
    if total == 0:
        return MetricValue(key, None, 0, "ratio",
                           reason="no settled live markets in window",
                           provenance=prov)
    coverage = 100.0 * len(covered) / total
    prov = prov | {"coverage_pct": round(coverage, 2),
                   "covered_markets": len(covered),
                   "excluded_uncovered": total - len(covered),
                   "excluded_side_mismatch": side_mismatch,
                   "excluded_side_ambiguous": side_unknown,
                   "coverage_threshold_pct": MIN_TWIN_MODEL_COVERAGE_PCT}
    if coverage < MIN_TWIN_MODEL_COVERAGE_PCT:
        return MetricValue(
            key, None, 0, "ratio", missing=True,
            reason=(
                f"twin model-probability coverage {coverage:.1f}% is below the "
                f"pre-registered {MIN_TWIN_MODEL_COVERAGE_PCT:.0f}% — the surviving "
                "markets were selected by a data defect, so the bias in this ratio "
                "has an unknown direction. Missing model data is not evidence"
            ),
            provenance=prov,
        )
    expected = sum(v["model_p"] for _t, v in covered)
    unresolved = sum(1 for _t, v in covered if v.get("yes_resolved") is None)
    observed = 0
    for t, v in covered:
        yes_resolved = v.get("yes_resolved")
        if yes_resolved is None:
            continue
        # The live book SOLD this side; the tail hit when that side lost.
        observed += int(yes_resolved if live_sides.get(t) == "no"
                        else not yes_resolved)
    prov = prov | {
        # `observed` and `expected` are the exact keys the poisson_exact bound
        # reads — the clause form this metric exists to serve.
        "observed": observed, "expected": round(expected, 6),
        "markets_without_a_settlement_value": unresolved,
        "outcome_basis": (
            "the underlying market's own settlement (did YES resolve), derived "
            "from the twin's (side, resolved_value) and compared against the LIVE "
            "side — not the twin trade's P&L classification"
        ),
        "unit_of_evidence": "settled market",
    }
    if expected <= 0:
        return MetricValue(key, None, 0, "ratio",
                           reason="modeled probabilities sum to zero — ratio undefined",
                           provenance=prov)
    return MetricValue(key, round(observed / expected, 4), len(covered), "ratio",
                       provenance=prov)


def _live_provenance(scope: MetricScope) -> dict:
    return _provenance(scope) | {
        "source": "live_orders x fills x positions",
        "window_basis": "live_orders.created_at (entry time)",
        "settled_basis": "newest positions snapshot with quantity=0 and realized_pnl",
        "per_contract_basis": (
            "total realized P&L / filled BUY contracts on settled markets — "
            "deliberately NOT scripts/mmsell_live.py's per-position average"
        ),
        "fee_basis": "realized_pnl as reported by Kalshi (fees already netted)",
    }


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
    """Compute one metric and stamp the PROVIDER REVISION that produced it.

    Every value carries which implementation computed it, so a gate result binds
    to the providers it actually used rather than to one engine-wide constant.
    That matters both ways: implementing a provider must not invalidate verdicts
    that never touched it, and a verdict recorded while the provider was missing
    must never share an identity with one computed by the implementation."""
    definition = REGISTRY.get(key)
    revision = definition.effective_revision if definition else UNPROVIDED_REVISION
    mv = _compute_metric(session, key, scope)
    return replace(mv, provenance=(mv.provenance or {}) | {"provider_revision": revision})


def provider_revisions(values) -> dict[str, str]:
    """metric key -> the provider revision that computed it, over any iterable of
    MetricValues or their recorded clause dicts."""
    out: dict[str, str] = {}
    for v in values or ():
        if isinstance(v, MetricValue):
            metric, prov = v.metric, v.provenance or {}
        elif isinstance(v, dict):
            metric = (v.get("clause") or {}).get("metric") or v.get("metric")
            prov = v.get("provenance") or {}
        else:
            continue
        rev = prov.get("provider_revision")
        if metric and rev:
            out[str(metric)] = str(rev)
        # Paired metrics carry their legs' revisions instead of their own.
        for leg in ("treatment", "control"):
            sub = prov.get(leg)
            if isinstance(sub, dict) and sub.get("provider_revision"):
                out.setdefault(str(sub.get("metric") or metric),
                               str(sub["provider_revision"]))
    return out


def _compute_metric(session, key: str, scope: MetricScope) -> MetricValue:
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
    if key in TWIN_METRICS:
        return _twin_metric(session, key, scope)
    if key in LIVE_ONLY_METRICS:
        # Routed BEFORE the empty-tags fallback below. That fallback answers 0 for
        # a count, which for `live_settled_contracts` under a paper scope would be
        # a confident, wrong "no live contracts" rather than "you addressed the
        # wrong deployment kind" — and a `<=` clause could even pass on it.
        return _live_metric(session, key, scope)
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
        base_def = REGISTRY.get(base)
        prov = prov | {
            "orientation": "delta = treatment - control",
            "base_metric_direction": base_def.direction if base_def else "unknown",
            "positive_delta_means": (
                f"treatment {base_def.positive_means} than control"
                if base_def else "unknown — base metric not in the registry"
            ),
        }
        # Independent arms, so the variances add. None if either leg lacks one —
        # a half-known uncertainty is not an uncertainty.
        se = (math.sqrt(t.stderr ** 2 + c.stderr ** 2)
              if t.stderr is not None and c.stderr is not None else None)
        return MetricValue(key, round(t.value - c.value, 4), min(t.n, c.n), t.unit,
                           provenance=prov | {
                               "treatment_stderr": t.stderr,
                               "control_stderr": c.stderr,
                               "stderr_basis": "independent arms, variances added",
                           },
                           stderr=se)

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
