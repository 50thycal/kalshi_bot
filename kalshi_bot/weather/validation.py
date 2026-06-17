"""Materialize the persisted forecast->settlement validation dataset.

When a weather event settles, this replays the live-collected raw tables
(weather_forecasts / weather_ensembles / weather_bucket_snapshots /
weather_observations / polymarket_snapshots) into one clean labeled row per intraday
market-state cycle in `weather_forecast_outcomes`, with NO lookahead (each cycle uses
only signals captured at-or-before that cycle's bucket snapshot) and the actual outcome
from weather_settlements attached. This is the join scripts/weather_model_check.py
reconstructs ad-hoc on every run; storing it lets cal/dist/pm calibration and
forecast-vs-market skill accumulate over time.

It runs as a bounded backfill OFF the trading path: each weather cycle, settled events
with no rows yet (an anti-join work queue) are materialized, a few per cycle, in their
own session. New settlements are graded within a cycle or two; historical ones drain in.

The probability math is reused from kalshi_bot/weather/distribution.py and the bucket
parsing from kalshi_bot/weather/buckets.py (the same helpers the live `dist` book uses);
only the cycle-clustering and at-or-before selection live here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean, pstdev

from sqlalchemy import select

from .. import models as m
from .. import repository as repo
from ..config import Settings
from .buckets import parse_bucket_range
from .distribution import model_bucket_probs

logger = logging.getLogger(__name__)

CLUSTER_GAP_SECONDS = 90.0  # rows of one insert batch land within ms; cycles are minutes apart
ENSEMBLE_MAX_AGE_H = 6.0  # don't attach an ensemble staler than this to a cycle
OPEN_BUCKET_HALF_WIDTH = 1.0  # representative temp offset for open-ended below/above buckets

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_DATE_RE = re.compile(r"(\d{2})([A-Z]{3})(\d{2})")


def event_target_date(event_ticker: str | None) -> str | None:
    """ISO target date parsed from tickers like KXHIGHNY-26JUN08."""
    match = _DATE_RE.search(event_ticker or "")
    if not match:
        return None
    month = _MONTHS.get(match.group(2))
    if not month:
        return None
    try:
        return date(2000 + int(match.group(1)), month, int(match.group(3))).isoformat()
    except ValueError:
        return None


# --- cycle clustering (mirrors scripts/weather_model_check.py) ----------------------


@dataclass
class _Bucket:
    market_ticker: str | None
    subtitle: str | None
    low_f: float | None
    high_f: float | None
    mid_cents: float | None


@dataclass
class _Cycle:
    captured_at: datetime
    hours_to_close: float | None
    buckets: list[_Bucket] = field(default_factory=list)


def cluster_cycles(rows: list, gap_seconds: float = CLUSTER_GAP_SECONDS) -> list[_Cycle]:
    """Group bucket-ladder rows into per-cycle snapshots (one insert batch lands within
    ms; cycles are minutes apart)."""
    rows = sorted(rows, key=lambda r: r.captured_at)
    cycles: list[_Cycle] = []
    group: list = []
    for r in rows:
        if group and (r.captured_at - group[-1].captured_at).total_seconds() > gap_seconds:
            cycles.append(_make_cycle(group))
            group = []
        group.append(r)
    if group:
        cycles.append(_make_cycle(group))
    return cycles


def _make_cycle(group: list) -> _Cycle:
    by_ticker: dict = {}
    for r in group:  # keep the last row per bucket if dupes sneak in
        by_ticker[r.market_ticker] = r
    buckets = [
        _Bucket(r.market_ticker, r.subtitle, r.low_f, r.high_f, r.mid_cents)
        for r in by_ticker.values()
    ]
    buckets.sort(key=lambda b: (b.low_f if b.low_f is not None else -1e9))
    htcs = [r.hours_to_close for r in group if r.hours_to_close is not None]
    return _Cycle(
        captured_at=max(r.captured_at for r in group),
        hours_to_close=max(htcs) if htcs else None,
        buckets=buckets,
    )


def _pick_latest_at(rows: list, at_time: datetime, get_time):
    """Latest row with get_time(row) <= at_time (no lookahead), else None."""
    best = None
    best_t = None
    for r in rows:
        t = get_time(r)
        if t is None or t > at_time:
            continue
        if best_t is None or t > best_t:
            best, best_t = r, t
    return best


def _ensembles_at(ens_rows: list, at_time: datetime, max_age_h: float = ENSEMBLE_MAX_AGE_H):
    """Latest members per model captured at-or-before at_time (no lookahead, bounded age)."""
    latest: dict[str, tuple[datetime, list[float]]] = {}
    for r in ens_rows:
        cap = r.captured_at
        if cap > at_time or (at_time - cap).total_seconds() > max_age_h * 3600:
            continue
        model = r.model or "?"
        if model not in latest or cap > latest[model][0]:
            latest[model] = (cap, list(r.members_json or []))
    return {model: members for model, (_, members) in latest.items() if members}


def _bucket_mid_temp(low: float | None, high: float | None) -> float | None:
    """Representative temperature for a bucket; open-ended below/above buckets use the
    finite edge offset by a half-width."""
    if low is not None and high is not None:
        return (low + high) / 2.0
    if high is not None:  # "X or below"
        return high - OPEN_BUCKET_HALF_WIDTH
    if low is not None:  # "X or above"
        return low + OPEN_BUCKET_HALF_WIDTH
    return None


def _normalize_mids(mids: list[float | None]) -> list[float] | None:
    """Market-implied probabilities from bucket mid prices: mid/100, normalized across the
    mutually-exclusive ladder (removes the overround). None when the ladder is degenerate."""
    vals = [(max(0.0, min(100.0, mc)) / 100.0 if mc is not None else 0.0) for mc in mids]
    total = sum(vals)
    if total <= 0:
        return None
    return [v / total for v in vals]


def _find_winner_idx(buckets: list[_Bucket], winning_ticker, winning_subtitle) -> int | None:
    for i, b in enumerate(buckets):
        if winning_ticker and b.market_ticker == winning_ticker:
            return i
    for i, b in enumerate(buckets):
        if winning_subtitle and b.subtitle == winning_subtitle:
            return i
    return None


# --- materialization ---------------------------------------------------------------


def _build_cycle_row(
    st: m.WeatherSettlement,
    kind: str,
    target: str | None,
    cycle: _Cycle,
    forecast_rows: list,
    ens_rows: list,
    obs_rows: list,
    pm_rows: list,
    *,
    sigma: float,
) -> dict:
    """One output row for one market-state cycle, using only signals at-or-before it."""
    cap = cycle.captured_at
    actual_extreme = st.actual_high_f if kind == "high" else st.actual_low_f

    # --- forecast (point NWS; holds the low for kind='low') ---
    fc = _pick_latest_at(forecast_rows, cap, lambda r: r.captured_at)
    forecast_f = fc.forecast_high_f if fc is not None else None
    forecast_source = fc.source if fc is not None else None

    # --- ensemble distribution ---
    members = _ensembles_at(ens_rows, cap)
    pooled = [float(v) for vals in members.values() for v in vals if v is not None]
    ens_mean_f = mean(pooled) if pooled else None
    ens_std_f = pstdev(pooled) if len(pooled) > 1 else None
    ens_models = len(members) or None

    # --- observations (running extreme so far) ---
    obs = _pick_latest_at(obs_rows, cap, lambda r: r.captured_at)
    obs_max = obs.running_max_f if obs is not None else None
    obs_min = obs.running_min_f if obs is not None else None

    # --- market implied distribution ---
    buckets = cycle.buckets
    ranges = [(b.low_f, b.high_f) for b in buckets]
    mids = [b.mid_cents for b in buckets]
    mkt_probs = _normalize_mids(mids)
    market_implied_mean_f = None
    if mkt_probs is not None:
        num = den = 0.0
        for p, (lo, hi) in zip(mkt_probs, ranges, strict=True):
            mt = _bucket_mid_temp(lo, hi)
            if mt is not None:
                num += p * mt
                den += p
        market_implied_mean_f = (num / den) if den > 0 else None

    fav = max(buckets, key=lambda b: (b.mid_cents if b.mid_cents is not None else -1.0), default=None)
    fav_low = fav.low_f if fav is not None else None
    fav_high = fav.high_f if fav is not None else None
    fav_mid = fav.mid_cents if fav is not None else None

    # --- outcome-conditioned calibration labels ---
    winner_idx = _find_winner_idx(buckets, st.winning_ticker, st.winning_subtitle)
    market_prob_winner = (
        mkt_probs[winner_idx] if (mkt_probs is not None and winner_idx is not None) else None
    )
    ens_prob_winner = None
    if members and winner_idx is not None:
        ens_probs = model_bucket_probs(members, ranges, sigma)
        if ens_probs is not None:
            ens_prob_winner = ens_probs[winner_idx]

    # --- polymarket implied mean (nullable) ---
    pm_implied_mean_f = _pm_implied_mean(pm_rows, cap)

    divergence_f = (
        forecast_f - market_implied_mean_f
        if (forecast_f is not None and market_implied_mean_f is not None)
        else None
    )
    forecast_abs_err_f = (
        abs(forecast_f - actual_extreme)
        if (forecast_f is not None and actual_extreme is not None)
        else None
    )
    market_abs_err_f = (
        abs(market_implied_mean_f - actual_extreme)
        if (market_implied_mean_f is not None and actual_extreme is not None)
        else None
    )

    win_low, win_high = parse_bucket_range(st.winning_subtitle)

    return {
        "event_ticker": st.event_ticker,
        "city": st.city,
        "kind": kind,
        "target_date": target,
        "captured_at": cap,
        "hours_to_close": cycle.hours_to_close,
        "forecast_f": forecast_f,
        "forecast_source": forecast_source,
        "ens_mean_f": ens_mean_f,
        "ens_std_f": ens_std_f,
        "ens_models": ens_models,
        "market_implied_mean_f": market_implied_mean_f,
        "market_fav_low_f": fav_low,
        "market_fav_high_f": fav_high,
        "market_fav_mid_cents": fav_mid,
        "divergence_f": divergence_f,
        "obs_running_max_f": obs_max,
        "obs_running_min_f": obs_min,
        "pm_implied_mean_f": pm_implied_mean_f,
        "actual_high_f": st.actual_high_f,
        "actual_low_f": st.actual_low_f,
        "winning_low_f": win_low,
        "winning_high_f": win_high,
        "winning_subtitle": st.winning_subtitle,
        "market_prob_winner": market_prob_winner,
        "ens_prob_winner": ens_prob_winner,
        "forecast_abs_err_f": forecast_abs_err_f,
        "market_abs_err_f": market_abs_err_f,
        "n_buckets": len(buckets),
        "raw_json": {
            "buckets": [
                {
                    "subtitle": b.subtitle,
                    "low": b.low_f,
                    "high": b.high_f,
                    "mid_cents": b.mid_cents,
                    "mkt_prob": (mkt_probs[i] if mkt_probs is not None else None),
                }
                for i, b in enumerate(buckets)
            ]
        },
    }


def _pm_implied_mean(pm_rows: list, at_time: datetime) -> float | None:
    """Probability-weighted mean temperature of the latest Polymarket bucket set at-or-
    before at_time. None when the city isn't PM-tracked or the set is degenerate."""
    eligible = [r for r in pm_rows if r.captured_at <= at_time]
    if not eligible:
        return None
    latest = max(r.captured_at for r in eligible)
    num = den = 0.0
    for r in eligible:
        if r.captured_at != latest or r.yes_prob is None:
            continue
        mt = _bucket_mid_temp(r.low_f, r.high_f)
        if mt is not None:
            num += r.yes_prob * mt
            den += r.yes_prob
    return (num / den) if den > 0 else None


def _sentinel_row(st: m.WeatherSettlement, kind: str, target: str | None) -> dict:
    """One placeholder row for a settled event with no usable bucket snapshots, so the
    anti-join work queue stops re-selecting it. hours_to_close NULL marks it; analysis
    filters these out."""
    win_low, win_high = parse_bucket_range(st.winning_subtitle)
    return {
        "event_ticker": st.event_ticker,
        "city": st.city,
        "kind": kind,
        "target_date": target,
        "captured_at": st.captured_at,
        "hours_to_close": None,
        "actual_high_f": st.actual_high_f,
        "actual_low_f": st.actual_low_f,
        "winning_low_f": win_low,
        "winning_high_f": win_high,
        "winning_subtitle": st.winning_subtitle,
        "n_buckets": 0,
    }


def materialize_event(session, st: m.WeatherSettlement, *, settings: Settings) -> int:
    """Rebuild and store all validation rows for one settled event. Idempotent
    (delete-then-insert). Returns the number of rows written."""
    ev = st.event_ticker
    kind = st.kind or "high"
    target = st.target_date or event_target_date(ev)

    bucket_rows = list(
        session.scalars(
            select(m.WeatherBucketSnapshot)
            .where(m.WeatherBucketSnapshot.event_ticker == ev)
            .order_by(m.WeatherBucketSnapshot.captured_at)
        )
    )
    cycles = cluster_cycles(bucket_rows)
    max_htc = settings.weather_validation_max_htc
    cycles = [c for c in cycles if c.hours_to_close is None or c.hours_to_close <= max_htc]

    if not cycles:
        return repo.replace_event_outcomes(session, ev, [_sentinel_row(st, kind, target)])

    forecast_rows = list(
        session.scalars(
            select(m.WeatherForecast).where(
                m.WeatherForecast.event_ticker == ev,
                m.WeatherForecast.forecast_high_f.is_not(None),
            )
        )
    )
    ens_rows = list(
        session.scalars(
            select(m.WeatherEnsemble).where(
                m.WeatherEnsemble.city == st.city,
                m.WeatherEnsemble.target_date == target,
                m.WeatherEnsemble.kind == kind,
            )
        )
    ) if (st.city and target) else []
    obs_rows = list(
        session.scalars(
            select(m.WeatherObservation).where(
                m.WeatherObservation.city == st.city,
                m.WeatherObservation.target_date == target,
            )
        )
    ) if (st.city and target) else []
    pm_rows = list(
        session.scalars(
            select(m.PolymarketSnapshot).where(
                m.PolymarketSnapshot.city == st.city,
                m.PolymarketSnapshot.kind == kind,
                m.PolymarketSnapshot.target_date == target,
            )
        )
    ) if (st.city and target) else []

    sigma = settings.weather_dist_sigma
    rows = [
        _build_cycle_row(
            st, kind, target, c, forecast_rows, ens_rows, obs_rows, pm_rows, sigma=sigma
        )
        for c in cycles
    ]
    return repo.replace_event_outcomes(session, ev, rows)


@dataclass
class ValidationSummary:
    events_materialized: int = 0
    rows_written: int = 0
    pending: int = 0
    errors: int = 0


class WeatherValidationBackfill:
    """Bounded per-cycle materializer for the forecast->settlement dataset. The work queue
    is the anti-join of settled events with no rows yet, so new settlements are graded
    promptly and historical ones drain a few per cycle. Read-only against Kalshi (it only
    replays the DB), and isolated from the trading session."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run_once(self, session) -> ValidationSummary:
        summary = ValidationSummary()
        pending = repo.pending_outcome_settlements(
            session, limit=self.settings.weather_validation_events_per_cycle
        )
        for st in pending:
            try:
                n = materialize_event(session, st, settings=self.settings)
                summary.events_materialized += 1
                summary.rows_written += n
            except Exception as exc:  # noqa: BLE001 — one event must not sink the cycle
                summary.errors += 1
                logger.warning(
                    "weather validation: materialize failed",
                    extra={"extra_fields": {"event_ticker": st.event_ticker, "error": str(exc)}},
                )
        summary.pending = repo.count_pending_outcome_settlements(session)
        return summary
