"""Live-vs-paper comparison and divergence decomposition.

Two jobs:

1. **Compare** matched quantities and label each with an intentional verdict.
   A verdict is never "match because both sides are non-null" — every metric
   declares its own tolerance, and a metric that cannot be compared (one side
   missing, no marks, nothing settled yet) says so instead of showing a number.

2. **Decompose** the total P&L gap into named components that sum back to it
   exactly. The residual is a real term, computed as the balancing figure, and is
   labelled as unexplained rather than distributed into the other buckets. If the
   residual is large that is the finding.

The decomposition is per-ticker and then aggregated:

    gap(ticker) = paper_pnl − live_pnl
                = quantity impact + entry-price impact + fee impact + residual

with a separate **missed-fill** bucket for tickers the paper twin traded and the
live book never filled — structurally the largest term for a maker book, and the
one thing paper is definitionally incapable of modelling.

Thresholds live in `Thresholds` (env prefix `LIVEDASH_`), deliberately separate
from any trading/risk configuration. They change how a difference is *labelled*,
never what any strategy does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Verdict vocabulary. Explicit, so a caller cannot invent an optimistic one.
MATCH = "match"
MINOR = "minor"
MATERIAL = "material"
CANNOT_COMPARE = "cannot_compare"
MISSING_LIVE = "missing_live"
MISSING_PAPER = "missing_paper"
STALE = "stale"


@dataclass(frozen=True)
class Thresholds:
    """Display-only tolerances. Nothing here can affect trading behaviour."""

    entry_price_minor_cents: float = 0.25
    # A full cent of systematic entry-price error is material for this book, not a
    # rounding detail: mmsell's measured realizable edge is only ~1.4¢/contract, so
    # 1¢ of cost-basis drift is most of the edge (docs/LIVE_PAPER_TWIN.md §4).
    entry_price_material_cents: float = 0.75
    pnl_minor_usd: float = 0.25
    pnl_material_usd: float = 1.00
    per_contract_minor_cents: float = 0.5      # matches the parity script's ALIGNED tolerance
    per_contract_material_cents: float = 1.5
    fill_time_minor_seconds: float = 300
    fill_time_material_seconds: float = 1800
    stale_minutes: int = 30

    @classmethod
    def from_env(cls, env: dict | None = None) -> Thresholds:
        import os

        env = env if env is not None else os.environ
        values = {}
        for f in cls.__dataclass_fields__:  # noqa: SLF001 — dataclass public contract
            raw = env.get(f"LIVEDASH_{f.upper()}")
            if raw is None or raw == "":
                continue
            try:
                values[f] = type(getattr(cls(), f))(raw)
            except (TypeError, ValueError):
                continue  # a bad env value must never break the page
        return cls(**values)


def _verdict(diff: float | None, minor: float, material: float) -> str:
    if diff is None:
        return CANNOT_COMPARE
    magnitude = abs(diff)
    if magnitude > material:
        return MATERIAL
    if magnitude > minor:
        return MINOR
    return MATCH


def _row(
    metric: str, live, paper, *, diff=None, unit: str = "", verdict: str = CANNOT_COMPARE,
    note: str | None = None, pct: float | None = None,
) -> dict:
    return {
        "metric": metric, "live": live, "paper": paper, "difference": diff,
        "pct_difference": pct, "unit": unit, "verdict": verdict, "note": note,
    }


def _pct(diff: float | None, base: float | None) -> float | None:
    """Percentage difference only where it is mathematically meaningful — a
    percentage against a near-zero or sign-flipping base is noise dressed as
    precision, so it is omitted."""
    if diff is None or base is None or abs(base) < 0.01:
        return None
    return round(diff / abs(base) * 100, 1)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ---------------------------------------------------------------------------
# Headline comparison
# ---------------------------------------------------------------------------


def compare_legs(live, paper, thresholds: Thresholds, *, now: datetime | None = None) -> dict:
    """Metric-by-metric comparison of the two legs, each with its own verdict."""
    now = now or datetime.now(timezone.utc)
    live_s, paper_s = live.summary(), paper.summary()
    rows: list[dict] = []

    # A leg with nothing to measure reports a real 0.00, and 0.00 can sit inside any
    # tolerance — so a P&L row must first ask whether both sides actually HAVE the
    # thing being compared. Otherwise "live has not traded at all" renders as a
    # green match, which is the precise failure mode this dashboard exists to avoid.
    def coverage_verdict(live_n: int, paper_n: int) -> str | None:
        if live_n == 0 and paper_n > 0:
            return MISSING_LIVE
        if paper_n == 0 and live_n > 0:
            return MISSING_PAPER
        if live_n == 0 and paper_n == 0:
            return CANNOT_COMPARE
        return None

    closed_cover = coverage_verdict(live_s["positions_closed"], paper_s["positions_closed"])
    open_cover = coverage_verdict(live_s["positions_open"], paper_s["positions_open"])
    any_cover = coverage_verdict(
        live_s["positions_closed"] + live_s["positions_open"],
        paper_s["positions_closed"] + paper_s["positions_open"],
    )

    def money(metric: str, key: str, note: str | None = None, cover: str | None = None):
        lv, pv = live_s[key], paper_s[key]
        diff = None if (lv is None or pv is None) else pv - lv
        if lv is None:
            verdict = MISSING_LIVE
        elif pv is None:
            verdict = MISSING_PAPER
        elif cover is not None:
            verdict = cover
        else:
            verdict = _verdict(diff, thresholds.pnl_minor_usd, thresholds.pnl_material_usd)
        if verdict in (MISSING_LIVE, MISSING_PAPER) and not note:
            note = "one side has no comparable position — this is not a match"
        rows.append(_row(metric, lv, pv, diff=_r(diff), unit="usd", verdict=verdict,
                         note=note, pct=_pct(diff, lv)))

    money("Realized P&L", "realized_pnl_usd", cover=closed_cover)
    money("Unrealized P&L", "unrealized_pnl_usd",
          note="both legs marked off the same tick", cover=open_cover)
    money("Total P&L", "total_pnl_usd", cover=any_cover)
    money("Entry fees", "entry_fees_usd",
          note="live from fills; paper from the simulator's fee model", cover=any_cover)
    money("Capital deployed", "capital_deployed_usd",
          note="sum of entry cost across every filled trade, open or closed — not netted "
               "against exits", cover=any_cover)

    # Per-contract is the comparison that survives a size difference.
    lv, pv = live_s["pnl_per_contract_cents"], paper_s["pnl_per_contract_cents"]
    diff = None if (lv is None or pv is None) else pv - lv
    rows.append(_row(
        "Realized P&L per contract", lv, pv, diff=_r(diff), unit="cents",
        verdict=(MISSING_LIVE if lv is None else MISSING_PAPER if pv is None
                 else closed_cover or _verdict(diff, thresholds.per_contract_minor_cents,
                                               thresholds.per_contract_material_cents)),
        note="the size-neutral read", pct=_pct(diff, lv)))

    # Counts: any difference at all is real, so these use exact-match verdicts.
    for metric, key, note in (
        ("Positions opened", "positions_closed", None),
        ("Contracts closed", "contracts_closed", None),
        ("Open positions", "positions_open", None),
    ):
        lv, pv = live_s[key], paper_s[key]
        diff = pv - lv
        rows.append(_row(metric, lv, pv, diff=diff, unit="count",
                         verdict=MATCH if diff == 0 else MATERIAL,
                         note=note, pct=_pct(float(diff), float(lv) if lv else None)))

    unfilled = live_s["positions_unfilled"]
    rows.append(_row(
        "Live orders that never filled", unfilled, 0, diff=-unfilled, unit="count",
        verdict=MATCH if unfilled == 0 else MATERIAL,
        note="paper cannot produce this state — it assumes its resting order was lifted"))

    # Win rate: only meaningful once both sides have closed something.
    lv, pv = live_s["win_rate"], paper_s["win_rate"]
    rows.append(_row(
        "Win rate", lv, pv,
        diff=_r(None if (lv is None or pv is None) else pv - lv), unit="pct",
        verdict=(CANNOT_COMPARE if (lv is None or pv is None)
                 else _verdict(pv - lv, 5.0, 15.0)),
        note=None if (lv is not None and pv is not None) else "needs a closed trade on both sides"))

    rows.extend(_execution_rows(live, paper, thresholds))
    rows.append(_freshness_row(live, paper, thresholds, now))
    return {
        "rows": rows,
        "worst_verdict": _worst([r["verdict"] for r in rows]),
        "thresholds": thresholds.__dict__,
    }


_SEVERITY = {MATCH: 0, MINOR: 1, STALE: 2, MISSING_LIVE: 3, MISSING_PAPER: 3,
             CANNOT_COMPARE: 1, MATERIAL: 4}


def _worst(verdicts: list[str]) -> str:
    return max(verdicts, key=lambda v: _SEVERITY.get(v, 0)) if verdicts else CANNOT_COMPARE


def _r(value, places: int = 4):
    return None if value is None else round(float(value), places)


def _execution_rows(live, paper, thresholds: Thresholds) -> list[dict]:
    """Entry price and fill timing, on the markets both sides actually took."""
    live_by = {p.ticker: p for p in live.positions if p.fill_count}
    paper_by = paper.by_ticker()
    shared = sorted(set(live_by) & set(paper_by))
    rows: list[dict] = []

    if not shared:
        return [_row("Entry price (matched markets)", None, None, unit="cents",
                     verdict=CANNOT_COMPARE, note="no market filled on both sides yet")]

    live_px = _mean([live_by[t].entry_price_cents for t in shared
                     if live_by[t].entry_price_cents is not None])
    paper_px = _mean([paper_by[t].entry_price_cents for t in shared
                      if paper_by[t].entry_price_cents is not None])
    diff = None if (live_px is None or paper_px is None) else live_px - paper_px
    rows.append(_row(
        "Entry price (matched markets)", _r(live_px, 2), _r(paper_px, 2), diff=_r(diff, 3),
        unit="cents",
        verdict=_verdict(diff, thresholds.entry_price_minor_cents,
                         thresholds.entry_price_material_cents),
        note="live minus paper: positive means we paid MORE for the same NO than paper assumed"))

    gaps = []
    for ticker in shared:
        lv, pv = live_by[ticker].entry_at, paper_by[ticker].entry_at
        if lv is not None and pv is not None:
            gaps.append((lv - pv).total_seconds())
    mean_gap = _mean(gaps)
    rows.append(_row(
        "Fill time gap (matched markets)", None, None, diff=_r(mean_gap, 1), unit="seconds",
        verdict=(CANNOT_COMPARE if mean_gap is None
                 else _verdict(mean_gap, thresholds.fill_time_minor_seconds,
                               thresholds.fill_time_material_seconds)),
        note="mean live-fill minus paper-assumed-fill; paper fills the instant it decides"))
    return rows


def _freshness_row(live, paper, thresholds: Thresholds, now: datetime) -> dict:
    def age(dt):
        return None if dt is None else (now - dt).total_seconds()

    la, pa = age(live.last_data_at), age(paper.last_data_at)
    diff = None if (la is None or pa is None) else pa - la
    stale_s = thresholds.stale_minutes * 60
    verdict = CANNOT_COMPARE
    if la is not None and pa is not None:
        verdict = STALE if max(la, pa) > stale_s else _verdict(diff, 300, 1800)
    return _row("Data freshness", _r(la, 0), _r(pa, 0), diff=_r(diff, 0), unit="seconds",
                verdict=verdict, note=f"age of each side's newest record; stale above "
                                      f"{thresholds.stale_minutes}m")


# ---------------------------------------------------------------------------
# Divergence decomposition
# ---------------------------------------------------------------------------


@dataclass
class Component:
    key: str
    label: str
    amount_usd: float
    explanation: str
    tickers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label,
            "amount_usd": round(self.amount_usd, 4),
            "direction": "paper_ahead" if self.amount_usd > 0 else (
                "live_ahead" if self.amount_usd < 0 else "neutral"),
            "explanation": self.explanation,
            "tickers": self.tickers[:20],
            "ticker_count": len(self.tickers),
        }


def decompose(live, paper, thresholds: Thresholds) -> dict:
    """Break `paper realized − live realized` into components that sum to it exactly.

    Scoped to **settled** positions: an open position's gap is a mark difference,
    not a realized divergence, and is reported separately so the two are never
    conflated.
    """
    live_by = {p.ticker: p for p in live.closed_positions}
    paper_by = {p.ticker: p for p in paper.closed_positions}
    live_filled = {p.ticker for p in live.positions if p.fill_count}

    live_realized = sum(p.realized_pnl_usd or 0.0 for p in live_by.values())
    paper_realized = sum(p.realized_pnl_usd or 0.0 for p in paper_by.values())
    total_gap = paper_realized - live_realized

    missed: list[str] = []
    missed_amount = 0.0
    live_only: list[str] = []
    live_only_amount = 0.0
    qty_amount = 0.0
    qty_tickers: list[str] = []
    price_amount = 0.0
    price_tickers: list[str] = []
    fee_amount = 0.0
    fee_tickers: list[str] = []
    residual_amount = 0.0
    residual_tickers: list[str] = []
    per_ticker: list[dict] = []

    for ticker, ppos in paper_by.items():
        lpos = live_by.get(ticker)
        p_pnl = ppos.realized_pnl_usd or 0.0
        if lpos is None:
            # Paper traded it and live never got a settled position there. If live
            # never filled at all, that is a missed fill; otherwise it is still open
            # or unaccounted, and is excluded from the settled decomposition.
            if ticker not in live_filled:
                missed.append(ticker)
                missed_amount += p_pnl
            continue

        l_pnl = lpos.realized_pnl_usd or 0.0
        gap = p_pnl - l_pnl
        p_qty = abs(ppos.quantity or 0.0)
        l_qty = abs(lpos.quantity or 0.0)
        p_px = ppos.entry_price_cents
        l_px = lpos.entry_price_cents

        # 1. quantity: the contracts paper had and live did not, valued at live's
        #    own realized rate per contract (what those extra contracts would have
        #    earned at live's outcome).
        q_comp = 0.0
        if l_qty:
            q_comp = (p_qty - l_qty) * (l_pnl / l_qty)
        elif p_qty:
            q_comp = p_pnl
        if abs(q_comp) > 1e-9:
            qty_amount += q_comp
            qty_tickers.append(ticker)

        # 2. entry price on the contracts both sides held: a NO bought higher costs
        #    live money it would otherwise have kept.
        common = min(p_qty, l_qty)
        px_comp = 0.0
        if common and p_px is not None and l_px is not None:
            px_comp = common * (l_px - p_px) / 100.0
        if abs(px_comp) > 1e-9:
            price_amount += px_comp
            price_tickers.append(ticker)

        # 3. fees: live realized is net of exchange fees, paper of its own model.
        fee_comp = (lpos.entry_fees_usd or 0.0) - (ppos.entry_fees_usd or 0.0)
        if abs(fee_comp) > 1e-9:
            fee_amount += fee_comp
            fee_tickers.append(ticker)

        # 4. whatever is left is settlement/valuation/rounding we cannot attribute.
        resid = gap - q_comp - px_comp - fee_comp
        if abs(resid) > 1e-9:
            residual_amount += resid
            residual_tickers.append(ticker)

        per_ticker.append({
            "ticker": ticker,
            "live_pnl_usd": round(l_pnl, 4), "paper_pnl_usd": round(p_pnl, 4),
            "gap_usd": round(gap, 4),
            "live_quantity": l_qty, "paper_quantity": p_qty,
            "live_entry_cents": l_px, "paper_entry_cents": p_px,
            "quantity_impact_usd": round(q_comp, 4),
            "price_impact_usd": round(px_comp, 4),
            "fee_impact_usd": round(fee_comp, 4),
            "residual_usd": round(resid, 4),
        })

    for ticker, lpos in live_by.items():
        if ticker not in paper_by:
            live_only.append(ticker)
            live_only_amount -= lpos.realized_pnl_usd or 0.0

    components = [
        Component("missed_fills", "Missed fills", missed_amount,
                  "Paper assumed a fill on a market the live book never filled. This is the "
                  "one difference a paper simulator is structurally incapable of modelling.",
                  missed),
        Component("live_only", "Live-only markets", live_only_amount,
                  "Live settled a market the paper twin never traded.", live_only),
        Component("quantity", "Quantity difference", qty_amount,
                  "Different contract counts on the same market, valued at live's own realized "
                  "rate per contract.", qty_tickers),
        Component("entry_price", "Entry-price slippage", price_amount,
                  "The real NO cost basis versus the price paper assumed, over the contracts "
                  "both sides held.", price_tickers),
        Component("fees", "Fee difference", fee_amount,
                  "Fees actually charged versus the simulator's fee model.", fee_tickers),
        Component("residual", "Unexplained residual", residual_amount,
                  "Settlement value, valuation timing or rounding that the available records "
                  "do not attribute. NOT distributed into the other buckets.", residual_tickers),
    ]
    explained = sum(c.amount_usd for c in components)
    unrealized_gap = None
    if live.unrealized_pnl_usd is not None and paper.unrealized_pnl_usd is not None:
        unrealized_gap = paper.unrealized_pnl_usd - live.unrealized_pnl_usd

    # The size-neutral gap is the one to judge on: this book trades ~$1.86 positions,
    # so a dollar threshold that looks sane in the abstract is enormous relative to a
    # ~1.4c/contract edge. Matches scripts/live_paper_parity.py's per-contract read.
    live_contracts = sum(abs(p.quantity or 0.0) for p in live_by.values())
    paper_contracts = sum(abs(p.quantity or 0.0) for p in paper_by.values())
    live_per_ct = (live_realized / live_contracts * 100) if live_contracts else None
    paper_per_ct = (paper_realized / paper_contracts * 100) if paper_contracts else None
    gap_per_ct = (None if (live_per_ct is None or paper_per_ct is None)
                  else paper_per_ct - live_per_ct)

    return {
        "live_realized_usd": round(live_realized, 4),
        "paper_realized_usd": round(paper_realized, 4),
        "total_gap_usd": round(total_gap, 4),
        "live_per_contract_cents": None if live_per_ct is None else round(live_per_ct, 2),
        "paper_per_contract_cents": None if paper_per_ct is None else round(paper_per_ct, 2),
        "gap_per_contract_cents": None if gap_per_ct is None else round(gap_per_ct, 2),
        "components": [c.to_dict() for c in components],
        "explained_usd": round(explained, 4),
        # Exact by construction — the residual is the balancing term. A non-zero
        # check value would mean this module has a bug, so it is published.
        "reconciles": abs(explained - total_gap) < 0.005,
        "reconciliation_check_usd": round(explained - total_gap, 6),
        "per_ticker": sorted(per_ticker, key=lambda r: abs(r["gap_usd"]), reverse=True),
        "matched_settled": len(per_ticker),
        "unrealized_gap_usd": None if unrealized_gap is None else round(unrealized_gap, 4),
        "unrealized_note": (
            "Both legs are marked off the same tick, so an unrealized gap is a position "
            "difference, not a mark-source difference."),
        "verdict": _divergence_verdict(
            components, gap_per_ct, live_contracts, len(per_ticker), thresholds),
    }


def _divergence_verdict(components, gap_per_ct, live_contracts, matched: int,
                        thresholds) -> dict:
    """Name the failure mode, judged **per contract** — the size-neutral read, on the
    same tolerance scripts/live_paper_parity.py uses, so the dashboard and the ops
    report cannot disagree about what is wrong."""
    if matched < 5:
        return {
            "code": "too_early",
            "label": "Too early to judge",
            "detail": f"only {matched} market{'' if matched == 1 else 's'} settled on both "
                      "sides; the epoch is the sample, so keep both running.",
        }
    tol = thresholds.per_contract_material_cents
    # Split by which side of the gap the money sits on: markets both sides traded
    # (an accounting problem) versus trades live never got (an execution problem).
    by_key = {c.key: c.amount_usd for c in components}
    accounting_usd = abs(by_key.get("residual", 0.0)) + abs(by_key.get("entry_price", 0.0))
    execution_usd = abs(by_key.get("missed_fills", 0.0)) + abs(by_key.get("quantity", 0.0))
    as_cents = lambda usd: (usd / live_contracts * 100) if live_contracts else 0.0  # noqa: E731

    if gap_per_ct is None:
        return {"code": "incomplete", "label": "Incomplete",
                "detail": "not enough contract data on one side to compute a per-contract gap."}
    if abs(gap_per_ct) <= thresholds.per_contract_minor_cents:
        return {
            "code": "aligned", "label": "Aligned",
            "detail": f"Live and paper agree within {thresholds.per_contract_minor_cents}¢ per "
                      f"contract ({gap_per_ct:+.2f}¢). Paper is a fair model of this book, so "
                      "its gates can be trusted for sizing.",
        }
    if as_cents(accounting_usd) > tol and accounting_usd >= execution_usd:
        return {
            "code": "accounting_gap", "label": "Accounting gap",
            "detail": f"{gap_per_ct:+.2f}¢/contract apart, and most of it is on markets BOTH "
                      "sides traded — that indicts the simulator's own arithmetic, not "
                      "execution. Treat every paper book's gate as suspect until it is "
                      "explained.",
        }
    if execution_usd > 0:
        return {
            "code": "execution_gap", "label": "Execution gap",
            "detail": f"{gap_per_ct:+.2f}¢/contract apart, concentrated in trades live never "
                      "captured. The paper edge is real arithmetic that live execution is not "
                      "getting — read the fill rate and the blocked-order reasons.",
        }
    return {
        "code": "unexplained", "label": "Unexplained gap",
        "detail": f"{gap_per_ct:+.2f}¢/contract apart with no dominant attributed component. "
                  "See the residual row.",
    }


def discrepancies(live, paper, thresholds: Thresholds) -> list[dict]:
    """Individual, addressable discrepancies with magnitude, direction, first
    observation and the records that support them."""
    out: list[dict] = []
    live_by = live.by_ticker()
    paper_by = paper.by_ticker()

    for ticker in sorted(set(live_by) | set(paper_by)):
        lpos, ppos = live_by.get(ticker), paper_by.get(ticker)
        if ppos is not None and (lpos is None or lpos.status == "unfilled"):
            out.append({
                "kind": "missed_fill",
                "ticker": ticker,
                "magnitude_usd": round(
                    (ppos.realized_pnl_usd if ppos.realized_pnl_usd is not None
                     else ppos.unrealized_pnl_usd or 0.0), 4),
                "direction": "paper_only",
                "first_observed": ppos.entry_at.isoformat() if ppos.entry_at else None,
                "status": ppos.status,
                "likely_source": ("live order placed but never filled"
                                  if lpos is not None else "live never placed an order"),
                "support": {"paper_entry_cents": ppos.entry_price_cents,
                            "paper_quantity": ppos.quantity,
                            "live_orders": lpos.order_count if lpos else 0},
            })
            continue
        if lpos is not None and ppos is None and lpos.fill_count:
            out.append({
                "kind": "live_only",
                "ticker": ticker,
                "magnitude_usd": round(lpos.realized_pnl_usd or 0.0, 4),
                "direction": "live_only",
                "first_observed": lpos.entry_at.isoformat() if lpos.entry_at else None,
                "status": lpos.status,
                "likely_source": "paper twin did not take this market",
                "support": {"live_entry_cents": lpos.entry_price_cents,
                            "live_quantity": lpos.quantity},
            })
            continue
        if lpos is None or ppos is None or not lpos.fill_count:
            continue

        if (lpos.entry_price_cents is not None and ppos.entry_price_cents is not None):
            gap = lpos.entry_price_cents - ppos.entry_price_cents
            if abs(gap) > thresholds.entry_price_minor_cents:
                out.append({
                    "kind": "entry_price",
                    "ticker": ticker,
                    "magnitude_cents": round(gap, 3),
                    "magnitude_usd": round(
                        min(abs(lpos.quantity or 0), abs(ppos.quantity or 0)) * gap / 100.0, 4),
                    "direction": "live_paid_more" if gap > 0 else "live_paid_less",
                    "first_observed": lpos.entry_at.isoformat() if lpos.entry_at else None,
                    "status": lpos.status,
                    "likely_source": "real cost basis differs from the assumed resting price",
                    "support": {"live_entry_cents": lpos.entry_price_cents,
                                "paper_entry_cents": ppos.entry_price_cents},
                })
        l_qty, p_qty = abs(lpos.quantity or 0), abs(ppos.quantity or 0)
        if l_qty != p_qty:
            out.append({
                "kind": "quantity",
                "ticker": ticker,
                "magnitude_contracts": p_qty - l_qty,
                "direction": "paper_larger" if p_qty > l_qty else "live_larger",
                "first_observed": lpos.entry_at.isoformat() if lpos.entry_at else None,
                "status": lpos.status,
                "likely_source": "partial fill or a different size decision",
                "support": {"live_quantity": l_qty, "paper_quantity": p_qty,
                            "live_fills": lpos.fill_count},
            })
        if lpos.status != ppos.status:
            out.append({
                "kind": "status_mismatch",
                "ticker": ticker,
                "direction": f"live={lpos.status} paper={ppos.status}",
                "first_observed": (lpos.exit_at or lpos.entry_at).isoformat()
                if (lpos.exit_at or lpos.entry_at) else None,
                "status": "open",
                "likely_source": "one side has settled and the other has not yet been updated",
                "support": {"live_status": lpos.status, "paper_status": ppos.status},
            })
    return out
