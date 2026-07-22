"""Strategy sandbox: validation, historical replay/backtest, walk-forward
(spec §15). Runs the SAME interpreter as live paper trading (strategy_spec) over
the repo's provenance-labeled history tables, with hard row/time bounds and a
budget charge per run.

Datasets:
  backfill_weather — backfill_weather_markets + backfill_weather_candles
    (Kalshi REST archive; result labels; hourly candles). The largest settled
    corpus in the DB, and clearly provenance-separated per repo convention.

No-lookahead construction: the replay cursor walks candles in time order; entry
decisions see only the current candle's quote; settlement applies only after the
market's close_time."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select

from ..models import BackfillWeatherCandle, BackfillWeatherMarket
from ..paper.engine import kalshi_fee
from . import budgets
from .audit import audit
from .config import EvoSettings
from .marketdata import Quote
from .models import EvoSandboxRun, EvoStrategy
from .strategy_spec import entry_signal, exit_signal, validate_spec

logger = logging.getLogger(__name__)

DATASETS = ("backfill_weather",)


def spec_fingerprint(spec_doc: dict | None) -> str:
    """Short stable hash of a strategy spec so a heartbeat prompt can show when the
    agent has already run an IDENTICAL backtest (same fingerprint => same result)."""
    try:
        canonical = json.dumps(spec_doc or {}, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = str(spec_doc)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:10]


def recent_runs(session, agent_uuid: str, *, limit: int = 8) -> list[dict]:
    """Compact view of the agent's most recent sandbox runs for the heartbeat prompt,
    each tagged with the spec fingerprint and how many of these recent runs share it
    (times_run_recently >= 2 => a repeated identical backtest — a KNOWN result, not new
    information). Makes self-repetition impossible to miss."""
    rows = list(
        session.scalars(
            select(EvoSandboxRun)
            .where(EvoSandboxRun.agent_uuid == agent_uuid)
            .order_by(EvoSandboxRun.created_at.desc())
            .limit(limit)
        )
    )
    freq: dict[str, int] = {}
    prepared: list[tuple] = []
    for r in rows:
        spec = (
            (r.params_json or {}).get("spec") if isinstance(r.params_json, dict) else None
        )
        fp = spec_fingerprint(spec)
        freq[fp] = freq.get(fp, 0) + 1
        prepared.append((r, fp))
    out: list[dict] = []
    for r, fp in prepared:
        res = r.result_json or {}
        out.append(
            {
                "run_id": r.id,
                "kind": r.kind,
                "fingerprint": fp,
                "times_run_recently": freq[fp],
                "n_trades": res.get("n_trades"),
                "win_rate": res.get("win_rate"),
                "total_pnl_usd": res.get("total_pnl_usd"),
                "per_trade_usd": res.get("per_trade_usd"),
            }
        )
    return out


def _quote_from_candle(
    market: BackfillWeatherMarket, candle: BackfillWeatherCandle
) -> Quote:
    """Candle -> conservative Quote. Depth is unknown in candle history, so a
    deep synthetic ladder is used and fills are capped by the spec's size — the
    conservative element is the recorded bid/ask spread itself.

    close_time is translated to wall-relative (now + remaining-at-candle-time) so
    the interpreter's hours_to_close gates — which compare against wall clock —
    see the horizon the strategy would have seen live (no lookahead: `remaining`
    uses only the candle's own timestamp)."""
    ts = candle.end_period_ts if candle.end_period_ts.tzinfo else candle.end_period_ts.replace(
        tzinfo=timezone.utc
    )
    wall_close = None
    if market.close_time is not None:
        mc = market.close_time if market.close_time.tzinfo else market.close_time.replace(
            tzinfo=timezone.utc
        )
        wall_close = datetime.now(timezone.utc) + (mc - ts)
    yes_bid = candle.yes_bid_close
    yes_ask = candle.yes_ask_close
    if yes_bid is None and candle.price_close is not None:
        yes_bid = max(1.0, candle.price_close - 1)
    if yes_ask is None and candle.price_close is not None:
        yes_ask = min(99.0, candle.price_close + 1)
    yes_bid_i = int(yes_bid) if yes_bid is not None else None
    yes_ask_i = int(yes_ask) if yes_ask is not None else None
    no_bid_i = 100 - yes_ask_i if yes_ask_i is not None else None
    q = Quote(
        ticker=market.market_ticker,
        captured_at=ts,
        source="backfill_weather",
        status="active",
        result="",
        yes_bid=yes_bid_i,
        yes_ask=yes_ask_i,
        no_bid=no_bid_i,
        no_ask=100 - yes_bid_i if yes_bid_i is not None else None,
        yes_levels=[(yes_bid_i, 500)] if yes_bid_i else [],
        no_levels=[(no_bid_i, 500)] if no_bid_i else [],
        last_price=int(candle.price_close) if candle.price_close is not None else None,
        volume=candle.volume,
        open_interest=candle.open_interest,
        close_time=wall_close,
    )
    return q


def _hours_between(a: datetime, b: datetime) -> float:
    aa = a if a.tzinfo else a.replace(tzinfo=timezone.utc)
    bb = b if b.tzinfo else b.replace(tzinfo=timezone.utc)
    return (bb - aa).total_seconds() / 3600.0


def run_backtest(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    cohort_id: int,
    spec_doc: dict,
    dataset: str = "backfill_weather",
    date_from: str | None = None,
    date_to: str | None = None,
    strategy_id: int | None = None,
    heartbeat_id: int | None = None,
    kind: str = "backtest",
    charge_budget: bool = True,
) -> tuple[dict | None, str | None]:
    """Replay the spec over settled history. Returns (result, None) or (None, err).
    Result: n, wins, gross/net P&L, per-trade, max drawdown, by-month split."""
    if dataset not in DATASETS:
        return None, f"unknown dataset {dataset!r} (available: {DATASETS})"
    spec, err = validate_spec(spec_doc, max_bytes=settings.strategy_spec_max_bytes)
    if err:
        return None, err
    if charge_budget and not budgets.spend(
        session, agent_uuid, cohort_id, "sandbox_runs", 1
    ):
        return None, "sandbox-run budget exhausted"

    started = time.monotonic()
    deadline = started + settings.sandbox_max_seconds
    max_rows = settings.sandbox_max_rows

    q = select(BackfillWeatherMarket).where(
        BackfillWeatherMarket.result.in_(("yes", "no")),
        BackfillWeatherMarket.candles_fetched.is_(True),
    )
    if date_from:
        q = q.where(BackfillWeatherMarket.target_date >= date_from)
    if date_to:
        q = q.where(BackfillWeatherMarket.target_date <= date_to)
    markets = list(session.scalars(q.order_by(BackfillWeatherMarket.close_time)))

    trades: list[dict] = []
    rows_processed = 0
    truncated = False
    for market in markets:
        if time.monotonic() > deadline or rows_processed >= max_rows:
            truncated = True
            break
        if not spec.universe.admits_ticker(market.market_ticker):
            continue
        candles = list(
            session.scalars(
                select(BackfillWeatherCandle)
                .where(BackfillWeatherCandle.market_ticker == market.market_ticker)
                .order_by(BackfillWeatherCandle.end_period_ts)
            )
        )
        rows_processed += len(candles)
        open_pos: dict | None = None
        for candle in candles:
            quote = _quote_from_candle(market, candle)
            if open_pos is None:
                intent = entry_signal(spec, quote)
                if intent is None:
                    continue
                price = intent["limit_price_cents"]
                if intent["style"] == "maker":
                    # conservative: a resting maker order in candle history fills
                    # only if a LATER candle trades strictly through the limit —
                    # approximated by price_low < limit
                    continue_fill = candle.price_low is not None and (
                        candle.price_low < price
                    )
                    if not continue_fill:
                        continue
                qty = intent["quantity"]
                fee = kalshi_fee(price, qty)
                open_pos = {
                    "side": intent["side"],
                    "price": price,
                    "qty": qty,
                    "fee": fee,
                    "entered_at": candle.end_period_ts,
                }
                continue
            # manage open position
            reason = exit_signal(
                spec, quote, side=open_pos["side"],
                entry_price_cents=open_pos["price"],
                held_hours=_hours_between(open_pos["entered_at"], candle.end_period_ts),
            )
            if reason is not None:
                bid = quote.best_exit_bid(open_pos["side"])
                if bid is None:
                    continue
                exit_fee = kalshi_fee(bid, open_pos["qty"])
                # NB: entry side cost basis — yes positions exit at yes_bid, no at no_bid
                gross = open_pos["qty"] * (bid - open_pos["price"]) / 100.0
                trades.append({
                    "ticker": market.market_ticker,
                    "month": (market.target_date or "")[:7],
                    "pnl": gross - open_pos["fee"] - exit_fee,
                    "exit": reason,
                    "win": gross > 0,
                })
                open_pos = None
        if open_pos is not None:
            won = open_pos["side"] == market.result
            value = 100 if won else 0
            gross = open_pos["qty"] * (value - open_pos["price"]) / 100.0
            trades.append({
                "ticker": market.market_ticker,
                "month": (market.target_date or "")[:7],
                "pnl": gross - open_pos["fee"],
                "exit": "settlement",
                "win": won,
            })

    n = len(trades)
    total = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    by_month: dict[str, dict] = {}
    for t in trades:
        m = by_month.setdefault(t["month"], {"n": 0, "pnl": 0.0})
        m["n"] += 1
        m["pnl"] = round(m["pnl"] + t["pnl"], 4)
    result = {
        "dataset": dataset,
        "provenance": "kalshi_rest_backfill",
        "markets_considered": len(markets),
        "rows_processed": rows_processed,
        "truncated": truncated,
        "n_trades": n,
        "wins": wins,
        "win_rate": round(wins / n, 3) if n else None,
        "total_pnl_usd": round(total, 4),
        "per_trade_usd": round(total / n, 4) if n else None,
        "per_trade_cents_per_contract": (
            round(100.0 * total / sum(spec.entry.size_contracts for _ in trades)
                  / 1.0, 3) if n else None
        ),
        "max_drawdown_usd": round(max_dd, 4),
        "by_month": by_month,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    run = EvoSandboxRun(
        agent_uuid=agent_uuid,
        heartbeat_id=heartbeat_id,
        strategy_id=strategy_id,
        kind=kind,
        params_json={"spec": spec_doc, "date_from": date_from, "date_to": date_to},
        dataset=dataset,
        provenance="kalshi_rest_backfill",
        status="completed",
        result_json=result,
        rows_processed=rows_processed,
        elapsed_ms=result["elapsed_ms"],
    )
    session.add(run)
    session.flush()
    return result, None


def run_walkforward(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    cohort_id: int,
    spec_doc: dict,
    split_date: str,
    heartbeat_id: int | None = None,
) -> tuple[dict | None, str | None]:
    """Two-window walk-forward: fit-window read + held-out window read (the repo's
    split-half OOS convention). One budget charge covers both legs."""
    if not budgets.spend(session, agent_uuid, cohort_id, "sandbox_runs", 1):
        return None, "sandbox-run budget exhausted"
    in_sample, err = run_backtest(
        session, settings, agent_uuid=agent_uuid, cohort_id=cohort_id,
        spec_doc=spec_doc, date_to=split_date, heartbeat_id=heartbeat_id,
        kind="walkforward", charge_budget=False,
    )
    if err:
        return None, err
    out_sample, err = run_backtest(
        session, settings, agent_uuid=agent_uuid, cohort_id=cohort_id,
        spec_doc=spec_doc, date_from=split_date, heartbeat_id=heartbeat_id,
        kind="walkforward", charge_budget=False,
    )
    if err:
        return None, err
    return {"in_sample": in_sample, "out_of_sample": out_sample,
            "split_date": split_date}, None


# ---------------------------------------------------------------------------
# Strategy artifact lifecycle
# ---------------------------------------------------------------------------


def save_strategy(
    session,
    settings: EvoSettings,
    *,
    agent_uuid: str,
    spec_doc: dict,
    heartbeat_id: int | None = None,
    graveyard_check: dict | None = None,
    forked_from_uuid: str | None = None,
) -> tuple[EvoStrategy | None, str | None]:
    spec, err = validate_spec(spec_doc, max_bytes=settings.strategy_spec_max_bytes)
    if err:
        return None, err
    prev = session.scalar(
        select(EvoStrategy)
        .where(EvoStrategy.agent_uuid == agent_uuid, EvoStrategy.name == spec.name)
        .order_by(EvoStrategy.revision.desc())
        .limit(1)
    )
    row = EvoStrategy(
        agent_uuid=agent_uuid,
        name=spec.name,
        revision=(prev.revision + 1) if prev else 1,
        spec_json=spec.model_dump(),
        validation_json={"validated": True},
        status="validated",
        heartbeat_id=heartbeat_id,
        forked_from_uuid=forked_from_uuid,
        graveyard_check_json=graveyard_check,
    )
    session.add(row)
    session.flush()
    return row, None


def activate_strategy(
    session, agent_uuid: str, strategy_id: int
) -> tuple[EvoStrategy | None, str | None]:
    """Autonomous activation (spec §15): approved interfaces + validation passed +
    no shared deployment required — all true by construction for a saved spec."""
    row = session.get(EvoStrategy, strategy_id)
    if row is None:
        return None, f"strategy {strategy_id} not found"
    if row.agent_uuid != agent_uuid:
        return None, "cannot activate another agent's strategy"
    if row.status not in ("validated", "inactive"):
        return None, f"strategy is {row.status}, not activatable"
    # one active revision per (agent, name): deactivate siblings
    for sibling in session.scalars(
        select(EvoStrategy).where(
            EvoStrategy.agent_uuid == agent_uuid,
            EvoStrategy.name == row.name,
            EvoStrategy.status == "active",
        )
    ):
        sibling.status = "inactive"
    row.status = "active"
    session.flush()
    audit(session, "strategy_activated", agent_uuid=agent_uuid, strategy_id=row.id,
          name=row.name, revision=row.revision)
    return row, None


def active_strategies(session, agent_uuid: str) -> list[EvoStrategy]:
    return list(
        session.scalars(
            select(EvoStrategy).where(
                EvoStrategy.agent_uuid == agent_uuid, EvoStrategy.status == "active"
            )
        )
    )
