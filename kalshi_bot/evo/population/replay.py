"""Deterministic historical replay + the per-run virtual ledger.

One genome, one window, one independent virtual account. The replay itself is not
reimplemented here: it delegates to `evo.sandbox.run_backtest`, which is the same loop
the LLM organism's backtests and the ops probes use. A second replay implementation
would mean two answers to "what would this strategy have done", and the proving run
would only prove things about the copy.

What this module owns is everything around that call:

* **No look-ahead.** A run's window is clamped to its generation's `data_cutoff`, and a
  request that reaches past it is refused rather than silently trimmed.
* **Determinism.** The engine has no wall-clock or RNG dependence — the maker-fill gate
  is a hash of the market key, not a random draw — so the same genome over the same
  window returns the same tape. `fingerprint()` makes that checkable.
* **Isolation.** Every run gets its own ledger built from its own tape. Nothing is
  shared between candidates, so no candidate's trades can contaminate another's.
* **The three quantities kept apart.** Theoretical opportunity (gross, before costs),
  paper execution (net of fees, what the replay banked), and the realizable estimate
  (net projected through the measured maker-fill calibration) are reported separately.
  Collapsing them is how a paper edge that only exists in fills we would never receive
  gets mistaken for a real one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from .. import sandbox
from ..config import EvoSettings
from . import genome as genome_mod
from .models import EvoCandidateLedger, EvoGeneration, EvoProgram, EvoRun, EvoRunTrade

# Bumped whenever a change alters a replayed number. Recorded on every run so two runs
# produced under different engines are never compared as if they were comparable.
ENGINE_REVISION = "replay-1"


class ReplayRefused(Exception):
    """The run was not attempted. Distinct from a run that failed: a refusal means the
    request was invalid (look-ahead, unknown dataset, invalid genome), and recording it
    as a zero-trade result would read as 'no edge'."""


# ---------------------------------------------------------------------------
# Windows and cutoffs
# ---------------------------------------------------------------------------


def _as_date(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ReplayRefused(f"invalid ISO date {value!r}") from exc
    return text


def check_window(
    window_start: str | None, window_end: str | None, data_cutoff: str | None
) -> tuple[str | None, str | None]:
    """Validate a replay window against the generation's no-look-ahead boundary.

    Refuses rather than clamps. Silently trimming a window would mean two candidates
    asking for different windows quietly get the same one, and the run rows would claim
    a window the evidence does not cover."""
    start, end = _as_date(window_start), _as_date(window_end)
    cutoff = _as_date(data_cutoff)
    if start and end and start > end:
        raise ReplayRefused(f"window start {start} is after window end {end}")
    if cutoff:
        if end is None:
            raise ReplayRefused(
                f"window has no end but the generation cutoff is {cutoff} — an open-ended "
                "window would replay data past the boundary"
            )
        if end > cutoff:
            raise ReplayRefused(
                f"window end {end} is past the generation data cutoff {cutoff} "
                "(look-ahead refused)"
            )
        if start and start > cutoff:
            raise ReplayRefused(f"window start {start} is past the data cutoff {cutoff}")
    return start, end


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def _event_root(ticker: str) -> str:
    """Cluster key: the event portion of a market ticker. Same convention as
    `evo/fitness.py`, so concentration means the same thing in both layers."""
    return ticker.rsplit("-", 1)[0] if "-" in ticker else ticker


def _ts(value) -> datetime | None:
    return value if isinstance(value, datetime) else None


@dataclass
class Ledger:
    """One candidate's virtual account for one run, derived from its own trade tape."""

    starting_capital_usd: float
    realized_pnl_usd: float = 0.0
    unrealized_pnl_usd: float = 0.0
    fees_usd: float = 0.0
    gross_pnl_usd: float = 0.0
    turnover_usd: float = 0.0
    peak_exposure_usd: float = 0.0
    max_concurrent_positions: int = 0
    max_drawdown_usd: float = 0.0
    contracts: int = 0
    markets: int = 0
    trades_settled: int = 0
    trades_open: int = 0
    concentration_top_family: float | None = None
    concentration_hhi: float | None = None
    capital_breached: bool = False
    equity_curve: list[float] = field(default_factory=list)
    by_family: dict[str, dict] = field(default_factory=dict)

    @property
    def ending_capital_usd(self) -> float:
        return round(self.starting_capital_usd + self.realized_pnl_usd, 4)

    @property
    def return_on_capital(self) -> float:
        base = self.starting_capital_usd or 1.0
        return round(self.realized_pnl_usd / base, 6)


def build_ledger(trades: list[dict], *, starting_capital_usd: float) -> Ledger:
    """Reconstruct the account from the tape.

    Drawdown is computed on the equity curve ordered by **exit time**, not by the order
    the replay happened to visit markets in. The replay iterates market by market, so
    its own trade order is not chronological; a drawdown read off that order would be an
    artifact of iteration and would badly misrank a candidate whose losses clustered in
    time. That clustering is exactly what the drawdown component is supposed to catch."""
    led = Ledger(starting_capital_usd=float(starting_capital_usd))
    if not trades:
        return led

    tickers: set[str] = set()
    fam_pnl: dict[str, float] = {}
    fam_n: dict[str, int] = {}

    for t in trades:
        pnl = float(t.get("pnl") or 0.0)
        fees = float(t.get("fees") or 0.0)
        qty = int(t.get("quantity") or 0)
        entry_c = float(t.get("entry_price_cents") or 0.0)
        led.realized_pnl_usd += pnl
        led.fees_usd += fees
        led.turnover_usd += qty * entry_c / 100.0
        led.contracts += qty
        tickers.add(str(t.get("ticker")))
        if t.get("settled"):
            led.trades_settled += 1
        else:
            led.trades_open += 1
        root = _event_root(str(t.get("ticker") or ""))
        fam_pnl[root] = round(fam_pnl.get(root, 0.0) + pnl, 6)
        fam_n[root] = fam_n.get(root, 0) + 1

    led.realized_pnl_usd = round(led.realized_pnl_usd, 4)
    led.fees_usd = round(led.fees_usd, 4)
    led.turnover_usd = round(led.turnover_usd, 4)
    led.gross_pnl_usd = round(led.realized_pnl_usd + led.fees_usd, 4)
    led.markets = len(tickers)

    # --- concurrency and exposure: a sweep line over the open intervals -------
    events: list[tuple[datetime, int, float]] = []
    for t in trades:
        start, end = _ts(t.get("entered_at")), _ts(t.get("exited_at"))
        if start is None or end is None:
            continue
        cost = int(t.get("quantity") or 0) * float(t.get("entry_price_cents") or 0.0) / 100.0
        events.append((start, +1, cost))
        events.append((end, -1, -cost))
    if events:
        # Closes before opens at the same instant: a position that ends exactly when
        # another begins never actually held capital at the same time.
        events.sort(key=lambda e: (e[0], e[1]))
        open_n, open_cost = 0, 0.0
        for _, delta, cost in events:
            open_n += delta
            open_cost += cost
            led.max_concurrent_positions = max(led.max_concurrent_positions, open_n)
            led.peak_exposure_usd = max(led.peak_exposure_usd, open_cost)
        led.peak_exposure_usd = round(led.peak_exposure_usd, 4)
    led.capital_breached = led.peak_exposure_usd > led.starting_capital_usd

    # --- drawdown on the chronological equity curve ---------------------------
    dated = [t for t in trades if _ts(t.get("exited_at")) is not None]
    dated.sort(key=lambda t: (_ts(t.get("exited_at")), str(t.get("ticker"))))
    undated = [t for t in trades if _ts(t.get("exited_at")) is None]
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in dated + undated:
        equity += float(t.get("pnl") or 0.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        led.equity_curve.append(round(equity, 4))
    led.max_drawdown_usd = round(max_dd, 4)

    # --- concentration --------------------------------------------------------
    total_n = sum(fam_n.values())
    if total_n:
        shares = [n / total_n for n in fam_n.values()]
        led.concentration_top_family = round(max(shares), 4)
        led.concentration_hhi = round(sum(s * s for s in shares), 4)
    led.by_family = {
        root: {"n": fam_n[root], "pnl_usd": fam_pnl[root]} for root in sorted(fam_n)
    }
    return led


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


@dataclass
class ReplayResult:
    result: dict
    trades: list[dict]
    ledger: Ledger
    outcome: dict
    reproducibility: dict
    integrity: dict


def fingerprint(outcome: dict) -> str:
    """Stable hash of the measured quantities of a run.

    Two runs of the same genome over the same window must produce the same fingerprint.
    The proving run asserts exactly that, which is what makes 'runs reproduce' a test
    rather than a claim."""
    keys = (
        "n_trades", "gross_pnl_usd", "net_pnl_usd", "fees_usd", "per_trade_usd",
        "realizable_cents_per_contract", "max_drawdown_usd", "contracts", "markets",
    )
    payload = {k: outcome.get(k) for k in keys}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()[:32]


def replay(
    session,
    settings: EvoSettings,
    *,
    document: dict,
    dataset: str,
    window_start: str | None,
    window_end: str | None,
    data_cutoff: str | None,
    starting_capital_usd: float,
) -> ReplayResult:
    """Replay one genome over one window and build its ledger. Pure read: nothing is
    persisted here, so a caller can replay for a dry run without leaving state."""
    start, end = check_window(window_start, window_end, data_cutoff)
    if dataset not in sandbox.available_datasets():
        raise ReplayRefused(
            f"unknown dataset {dataset!r} (available: {sandbox.available_datasets()})"
        )
    norm, err = genome_mod.validate(document)
    if err or norm is None:
        raise ReplayRefused(f"invalid genome: {err}")

    result, run_err = sandbox.run_backtest(
        session,
        settings,
        # The population layer is not an evo agent and has no agent budget: these
        # identify the caller in the sandbox's signature but nothing is charged or
        # persisted against them (charge_budget=False, persist=False).
        agent_uuid="evo-population",
        cohort_id=0,
        spec_doc=norm,
        dataset=dataset,
        date_from=start,
        date_to=end,
        charge_budget=False,
        persist=False,
        return_trades=True,
    )
    if run_err or result is None:
        raise ReplayRefused(run_err or "replay produced no result")

    trades = list(result.pop("trades", []) or [])
    led = build_ledger(trades, starting_capital_usd=starting_capital_usd)
    fill = result.get("fill_model") or {}

    n = int(result.get("n_trades") or 0)
    outcome = {
        # theoretical opportunity — before costs
        "gross_pnl_usd": led.gross_pnl_usd,
        "gross_per_trade_usd": round(led.gross_pnl_usd / n, 4) if n else None,
        # paper execution — what the replay actually banked, net of fees
        "net_pnl_usd": led.realized_pnl_usd,
        "per_trade_usd": round(led.realized_pnl_usd / n, 4) if n else None,
        "per_trade_cents_per_contract": (
            round(100.0 * led.realized_pnl_usd / led.contracts, 4) if led.contracts else None
        ),
        "fees_usd": led.fees_usd,
        # realizable — the same trades projected through the measured fill calibration
        "optimistic_cents_per_contract": fill.get("optimistic_cents_per_contract"),
        "realizable_cents_per_contract": fill.get("realizable_cents_per_contract"),
        "fill_coverage": fill.get("coverage"),
        "fill_verdict": fill.get("verdict"),
        # shape
        "n_trades": n,
        "wins": result.get("wins"),
        "win_rate": result.get("win_rate"),
        "contracts": led.contracts,
        "markets": led.markets,
        "max_drawdown_usd": led.max_drawdown_usd,
        "return_on_capital": led.return_on_capital,
        "by_month": result.get("by_month"),
        "by_exit": result.get("by_exit"),
        "by_family": led.by_family,
    }
    reproducibility = {
        "engine_revision": ENGINE_REVISION,
        "genome_schema_revision": genome_mod.GENOME_SCHEMA_REVISION,
        "genome_hash": genome_mod.genome_hash(norm),
        "dataset": dataset,
        "provenance": result.get("provenance"),
        "window_start": start,
        "window_end": end,
        "data_cutoff": _as_date(data_cutoff),
        "fill_calibration_version": fill.get("version"),
        "fill_calibration_source": fill.get("source"),
        "markets_considered": result.get("markets_considered"),
        "rows_processed": result.get("rows_processed"),
        "outcome_fingerprint": fingerprint(outcome),
    }
    crossed = int(result.get("crossed_quotes") or 0)
    integrity = {
        "truncated": bool(result.get("truncated")),
        # A corrupt book (bid >= ask) means the corpus is not what it claims to be. The
        # engine skips those steps rather than minting P&L from them, but a run built on
        # data containing them is not evidence about the genome — it is a data defect,
        # and the evaluator classifies it invalid rather than ranking it badly.
        "crossed_quotes": crossed,
        "data_broken": crossed > 0,
        "data_broken_reason": (
            f"{crossed} corrupt quotes (bid >= ask) in the replayed corpus" if crossed else None
        ),
        "capital_breached": led.capital_breached,
        "peak_exposure_usd": led.peak_exposure_usd,
        "max_concurrent_positions": led.max_concurrent_positions,
        "fill_model_applied": fill.get("applied"),
        "markets_blocked": fill.get("markets_blocked"),
        "zero_trades": n == 0,
        # The replay visits markets one at a time, so it does not itself enforce
        # `risk.max_concurrent_positions`. The realized concurrency is measured above
        # and compared here rather than assumed to be within the cap.
        "concurrency_over_cap": None,
    }
    risk_cap = int(((norm.get("risk") or {}).get("max_concurrent_positions")) or 0)
    if risk_cap:
        integrity["concurrency_over_cap"] = led.max_concurrent_positions > risk_cap
    return ReplayResult(
        result=result,
        trades=trades,
        ledger=led,
        outcome=outcome,
        reproducibility=reproducibility,
        integrity=integrity,
    )


def persist_run(
    session,
    *,
    program: EvoProgram,
    generation: EvoGeneration,
    candidate_uuid: str,
    genome_id: int,
    genome_hash: str,
    replayed: ReplayResult | None,
    error: str | None = None,
    persist_trades: bool = True,
) -> EvoRun:
    """Write the run, its tape and its ledger. A refused or failed run is recorded too —
    a candidate that could not be evaluated is evidence, not an absence."""
    run = EvoRun(
        program_id=program.id,
        generation_id=generation.id,
        generation_number=generation.number,
        candidate_uuid=candidate_uuid,
        genome_id=genome_id,
        genome_hash=genome_hash,
        mode=generation.mode,
        dataset=generation.dataset,
        window_start=generation.window_start,
        window_end=generation.window_end,
        starting_capital_usd=float(program.starting_capital_usd),
        rng_seed=generation.rng_seed,
        status="completed" if replayed is not None else "refused",
        error=error,
    )
    if replayed is not None:
        run.provenance = replayed.reproducibility.get("provenance")
        run.outcome_json = replayed.outcome
        run.reproducibility_json = replayed.reproducibility
        run.integrity_json = replayed.integrity
        run.rows_processed = int(replayed.result.get("rows_processed") or 0)
        run.elapsed_ms = int(replayed.result.get("elapsed_ms") or 0)
    session.add(run)
    session.flush()

    if replayed is None:
        return run

    if persist_trades:
        for t in replayed.trades:
            session.add(
                EvoRunTrade(
                    run_id=run.id,
                    market_ticker=str(t.get("ticker") or "")[:128],
                    event_root=_event_root(str(t.get("ticker") or ""))[:64],
                    month=str(t.get("month") or "")[:7] or None,
                    side=str(t.get("side") or "")[:4] or None,
                    style=str(t.get("style") or "")[:8] or None,
                    quantity=int(t.get("quantity") or 0),
                    entry_price_cents=t.get("entry_price_cents"),
                    exit_price_cents=t.get("exit_price_cents"),
                    entered_at=_ts(t.get("entered_at")),
                    exited_at=_ts(t.get("exited_at")),
                    fees_usd=float(t.get("fees") or 0.0),
                    pnl_usd=float(t.get("pnl") or 0.0),
                    cents_per_contract=t.get("cents_per_contract"),
                    maker_yes_c=t.get("maker_yes_c"),
                    exit_reason=str(t.get("exit") or "")[:32] or None,
                    settled=bool(t.get("settled")),
                    win=bool(t.get("win")),
                )
            )

    led = replayed.ledger
    session.add(
        EvoCandidateLedger(
            program_id=program.id,
            generation_number=generation.number,
            candidate_uuid=candidate_uuid,
            run_id=run.id,
            starting_capital_usd=led.starting_capital_usd,
            realized_pnl_usd=led.realized_pnl_usd,
            unrealized_pnl_usd=led.unrealized_pnl_usd,
            fees_usd=led.fees_usd,
            ending_capital_usd=led.ending_capital_usd,
            peak_exposure_usd=led.peak_exposure_usd,
            turnover_usd=led.turnover_usd,
            max_drawdown_usd=led.max_drawdown_usd,
            max_concurrent_positions=led.max_concurrent_positions,
            contracts=led.contracts,
            markets=led.markets,
            trades_settled=led.trades_settled,
            trades_open=led.trades_open,
            concentration_top_family=led.concentration_top_family,
            concentration_hhi=led.concentration_hhi,
            capital_breached=led.capital_breached,
            detail_json={
                "by_family": led.by_family,
                "equity_curve": led.equity_curve[:500],
                "gross_pnl_usd": led.gross_pnl_usd,
                "return_on_capital": led.return_on_capital,
            },
        )
    )
    session.flush()
    return run


__all__ = [
    "ENGINE_REVISION",
    "Ledger",
    "ReplayRefused",
    "ReplayResult",
    "build_ledger",
    "check_window",
    "fingerprint",
    "persist_run",
    "replay",
]
