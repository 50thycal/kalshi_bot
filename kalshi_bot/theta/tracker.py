"""Theta book tracker (paper) — sell model-overpriced tails on hourly crypto ladders.

Validated basis (docs/THETA_THESIS.md, scripts/kalshi_theta_study.py, 2026-07-03):
selling every tail at the posted quote is ~0 EV (P1 FAIL — the quotes are calibrated),
but (a) the realized maker-SELL flow on these series nets +5.2c/contract with the edge
concentrated inside the final hour (P2), and (b) a trailing spot-vol model separates the
dead tails from the live ones: selling only model-overpriced 3-40c tails at the ask
netted +4.4c/contract net of worst-case fees, while model-fair tails lost (P3). So the
book trades ONLY:
  - markets with <= entry_max_minutes to settlement (the final-hour edge window),
  - yes-mid in the tail band (3-40c),
  - model excess (mid - 100*P_model) >= min_edge_cents.
Entry is the maker-sell convention the mmsell book uses: sell YES at the ask == buy NO
at the no-bid, held the <1h to settlement (the shared paper engine settles it).

Every cycle also snapshots the near-settlement ladders WITH the model probability into
crypto_ladder_snapshots — the accumulating research dataset — and maintains the rolling
1-min spot window in crypto_spot_candles.

SHELVED 2026-07-09 (settings.theta_collect_only, default True): the family failed its
pre-registered "positive AND calibrated" gate on every book and gave back a full calm-streak
gain live (control +$15.53 -> -$0.07 in two windows; realized tails 1.4-2.6x the model). In
collect-only mode run_once still refreshes spot + writes the model-priced ladder snapshots
but opens NO entries (control or variants). Flip theta_collect_only=False to resume trading,
which requires a fresh pre-registration (docs/THETA_THESIS.md).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close
from .spot import CoinbaseSpotClient, SpotModel

logger = logging.getLogger(__name__)


@dataclass
class ThetaCycleSummary:
    products_ok: int = 0
    markets_seen: int = 0
    snapshot_rows: int = 0
    # gate counters are CONTROL-book scoped (stable semantics across releases); entry
    # outcome counters accumulate across control + revision books, with per_book detail.
    in_window: int = 0
    in_band: int = 0
    model_priced: int = 0
    edged: int = 0
    opened: int = 0
    already_open: int = 0
    capped: int = 0
    skipped_no_model: int = 0
    skipped_illiquid: int = 0
    per_series: dict[str, int] = field(default_factory=dict)
    per_book: dict[str, int] = field(default_factory=dict)


def _price_c(market: dict, key: str) -> float | None:
    """Read a market-object price as cents, tolerating int-cent vs dollar-string fields."""
    v = market.get(f"{key}_dollars")
    if v not in (None, ""):
        try:
            return float(v) * 100.0
        except (TypeError, ValueError):
            return None
    v = market.get(key)
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _strike(v) -> float | None:
    """Numeric strike or None — a weird API value must degrade, not kill the cycle."""
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _volume(market: dict) -> float:
    for k in ("volume_fp", "volume"):
        v = market.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


class ThetaTracker:
    STRATEGY = "theta"

    def __init__(self, client, settings: Settings, spot_client: CoinbaseSpotClient | None = None):
        self.client = client
        self.settings = settings
        self.spot = spot_client or CoinbaseSpotClient()

    # -- spot maintenance ---------------------------------------------------
    def _refresh_spot(self, session, product: str) -> SpotModel | None:
        s = self.settings
        now = datetime.now(timezone.utc)
        trail_start = now - timedelta(days=s.theta_trail_days)
        latest = repo.latest_spot_minute(session, product)
        # Gap fetch: from the latest stored minute (or the full trailing window on first
        # run) up to now. Coinbase serves ~300 candles/call; the client chunks.
        fetch_from = trail_start if latest is None else latest
        if fetch_from.tzinfo is None:
            fetch_from = fetch_from.replace(tzinfo=timezone.utc)
        start_unix = int(fetch_from.timestamp())
        end_unix = int(now.timestamp())
        if end_unix - start_unix >= 60:
            closes = self.spot.candles(product, start_unix, end_unix)
            if closes:
                repo.insert_spot_candles(session, product, closes)
        repo.prune_spot_candles(session, product, trail_start - timedelta(days=1))
        stored = repo.load_spot_closes(session, product, trail_start)
        if len(stored) < SpotModel.MIN_SAMPLES:
            logger.warning(
                "theta: spot window too thin; model disabled this cycle",
                extra={"extra_fields": {"product": product, "minutes": len(stored)}},
            )
            return None
        return SpotModel(stored, trail_days=s.theta_trail_days)

    def _books(self) -> list[dict]:
        """The control book (base knobs, tag 'theta' — NEVER reparameterized) followed by
        the configured revision books. All share the scan/model; only gates differ."""
        s = self.settings
        control = {
            "tag": self.STRATEGY,
            "lo": s.theta_price_lo_cents,
            "hi": s.theta_price_hi_cents,
            "edge": s.theta_min_edge_cents,
            "mult": 1.0,
            "ttemin": s.theta_entry_min_minutes,
            "ttemax": s.theta_entry_max_minutes,
            "thronly": False,
        }
        return [control, *s.theta_variant_list]

    # -- one cycle ------------------------------------------------------------
    def run_once(self, session) -> ThetaCycleSummary:
        s = self.settings
        summ = ThetaCycleSummary()
        now_unix = int(time.time())

        models: dict[str, SpotModel | None] = {}
        for product in sorted(set(s.theta_series_map.values())):
            models[product] = self._refresh_spot(session, product)
            if models[product] is not None:
                summ.products_ok += 1

        books = self._books()
        open_count: dict[str, int] = {}
        open_tickers: dict[str, set[str]] = {}
        per_event_open: dict[str, dict[str, int]] = {}
        for b in books:
            tag = b["tag"]
            open_count[tag] = repo.count_open_paper_positions(session, tag)
            open_tickers[tag] = repo.open_paper_position_tickers(session, tag)
            per_event_open[tag] = {}
            for tk in open_tickers[tag]:
                ev = tk.rsplit("-", 1)[0]
                per_event_open[tag][ev] = per_event_open[tag].get(ev, 0) + 1
        returns_cache: dict[tuple[str, int], list[float]] = {}

        snapshot_rows: list[dict] = []
        for series, product in s.theta_series_map.items():
            model = models.get(product)
            markets: list[dict] = []
            cursor: str | None = None
            for _ in range(4):
                try:
                    page = self.client.get_markets(
                        status="open", series_ticker=series, limit=200, cursor=cursor
                    )
                except AuthError:
                    raise
                except Exception as exc:  # noqa: BLE001 — a series fetch must not kill the cycle
                    logger.warning(
                        "theta: markets fetch failed",
                        extra={"extra_fields": {"series": series, "error": str(exc)[:200]}},
                    )
                    break
                markets.extend((page or {}).get("markets") or [])
                cursor = (page or {}).get("cursor") or None
                if not cursor:
                    break

            for mkt in markets:
                summ.markets_seen += 1
                ticker = mkt.get("ticker") or ""
                if not ticker:
                    continue
                htc_s = compute_time_to_close(mkt.get("close_time"))
                if htc_s is None:
                    continue
                tte_min = htc_s / 60.0
                if tte_min > s.theta_snapshot_max_minutes or tte_min <= 0:
                    continue
                yb = _price_c(mkt, "yes_bid")
                ya = _price_c(mkt, "yes_ask")
                if yb is None or ya is None or not (0 <= yb <= 100) or not (0 < ya <= 100):
                    continue
                mid = (yb + ya) / 2.0
                strike_type = (mkt.get("strike_type") or "").lower()
                floor_k = _strike(mkt.get("floor_strike"))
                cap_k = _strike(mkt.get("cap_strike"))

                p = None
                if model is not None:
                    p = model.p_yes(now_unix, max(1, int(tte_min)), strike_type, floor_k, cap_k)
                if p is not None:
                    summ.model_priced += 1
                excess = (mid - p * 100.0) if p is not None else None

                snapshot_rows.append({
                    "series": series,
                    "event_ticker": ticker.rsplit("-", 1)[0],
                    "market_ticker": ticker,
                    "strike_type": strike_type or None,
                    "floor_strike": floor_k,
                    "cap_strike": cap_k,
                    "yes_bid_cents": yb,
                    "yes_ask_cents": ya,
                    "mid_cents": mid,
                    "volume": _volume(mkt),
                    "minutes_to_close": tte_min,
                    "spot": model.spot_at(now_unix) if model is not None else None,
                    "model_p": p,
                    "model_excess_cents": excess,
                })

                # SHELVED (theta_collect_only): keep snapshotting the model-priced ladder
                # (the research dataset) but skip ALL entries — control and every revision
                # book. The snapshot row above is already captured, so the labeled dataset a
                # future recalibrated model rebuilds from keeps accumulating untouched.
                if s.theta_collect_only:
                    continue

                # ---- entries: control first, then revision books (shared scan/model/
                # orderbook; only the gates differ per book) ----
                if s.theta_entry_min_minutes <= tte_min <= s.theta_entry_max_minutes:
                    summ.in_window += 1
                    if s.theta_price_lo_cents <= mid <= s.theta_price_hi_cents:
                        summ.in_band += 1

                if _volume(mkt) < s.theta_min_volume:
                    summ.skipped_illiquid += 1
                    continue

                spot = model.spot_at(now_unix) if model is not None else None
                metrics = None  # lazy: fetched once for the first book that wants in
                event = ticker.rsplit("-", 1)[0]
                for book in books:
                    tag = book["tag"]
                    if not (book["ttemin"] <= tte_min <= book["ttemax"]):
                        continue
                    if not (book["lo"] <= mid <= book["hi"]):
                        continue
                    if book["thronly"] and strike_type == "between":
                        continue
                    if model is None or spot is None:
                        if tag == self.STRATEGY:
                            summ.skipped_no_model += 1
                        continue
                    if book["mult"] == 1.0:
                        p_book = p
                    else:
                        h = max(1, int(tte_min))
                        key = (product, h)
                        if key not in returns_cache:
                            returns_cache[key] = model.returns(now_unix, h)
                        p_book = SpotModel.prob_from_returns(
                            returns_cache[key], spot, strike_type, floor_k, cap_k,
                            vol_mult=book["mult"],
                        )
                    if p_book is None:
                        if tag == self.STRATEGY:
                            summ.skipped_no_model += 1
                        continue
                    excess_book = mid - p_book * 100.0
                    if excess_book < book["edge"]:
                        continue
                    if tag == self.STRATEGY:
                        summ.edged += 1

                    if ticker in open_tickers[tag]:
                        summ.already_open += 1
                        continue
                    if open_count[tag] >= s.theta_max_open_positions or \
                            per_event_open[tag].get(event, 0) >= s.theta_max_per_event:
                        summ.capped += 1
                        continue

                    # confirm two-sided + maker entry price (buy NO at no-bid == sell YES
                    # at the ask), qty capped by resting depth like every paper book
                    if metrics is None:
                        try:
                            ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
                        except AuthError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "theta: orderbook fetch failed",
                                extra={"extra_fields": {"ticker": ticker,
                                                        "error": str(exc)[:200]}},
                            )
                            break  # no book can enter this market this cycle
                        metrics = compute_metrics(mkt, ob, top_n=s.orderbook_depth)
                    if not metrics.two_sided or metrics.best_no_bid is None:
                        summ.skipped_illiquid += 1
                        break
                    price = metrics.best_no_bid
                    if not (0 < price < 100):
                        summ.skipped_illiquid += 1
                        break
                    qty = min(s.theta_order_size, metrics.depth_at_best_bid or 0)
                    if qty <= 0:
                        summ.skipped_illiquid += 1
                        break

                    fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                    repo.create_paper_trade(
                        session,
                        signal_id=None,
                        ticker=ticker,
                        strategy=tag,
                        side="no",
                        action="buy",
                        assumed_price=price,
                        quantity=qty,
                        fill_assumption=(
                            f"[{tag}] sell yes @ {100 - price:.0f}c mid {mid:.0f} "
                            f"p {p_book * 100.0:.0f} tte {tte_min:.0f}m"
                        ),
                        entry_fee=fee,
                        model_probability=p_book,
                        edge=excess_book,
                    )
                    repo.open_paper_position_for_trade(
                        session, ticker=ticker, strategy=tag, side="no",
                        quantity=qty, avg_price=price,
                    )
                    open_count[tag] += 1
                    per_event_open[tag][event] = per_event_open[tag].get(event, 0) + 1
                    open_tickers[tag].add(ticker)
                    summ.opened += 1
                    summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                    summ.per_series[series] = summ.per_series.get(series, 0) + 1

        if snapshot_rows:
            summ.snapshot_rows = repo.insert_crypto_ladder_snapshots(
                session, snapshot_rows[: s.theta_snapshot_rows_cap]
            )
        return summ
