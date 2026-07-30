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
from ..live.sizing import maker_no_price, order_quantity
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close
from ..twin import harness as twin_codes
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
    opened: int = 0         # across control + revision books (twins excluded)
    twin_opened: int = 0    # live/paper twin books only (paper shadow of the live run)
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

    def __init__(
        self, client, settings: Settings, spot_client: CoinbaseSpotClient | None = None,
        live_executor=None, twin_harness=None,
    ):
        self.client = client
        self.settings = settings
        self.spot = spot_client or CoinbaseSpotClient()
        # Set only in live mode (BOT_MODE=live); mirrors allowlisted paper entries into real
        # resting maker NO-buys. None in paper/weather mode -> the book stays paper-only.
        self.live_executor = live_executor
        # Live/paper parallel-run harness (docs/LIVE_PAPER_TWIN.md): runs a FRESH paper book
        # beside each armed live theta tag, priced/sized on the LIVE knobs, so the only
        # difference from live is the fill assumption. None -> no twins.
        self.twin_harness = twin_harness
        # Set by the live cycle before run_once so the live mirror can pass it to the balance gate.
        self._account_state: dict | None = None

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
        """The control book (base knobs, tag 'theta' — NEVER reparameterized), then the
        configured revision books, then any live/paper twin books. All share the scan/model;
        only gates differ. Twins come LAST so each market's live decision is already recorded in
        the parity tape by the time the twin (its paper shadow) is evaluated."""
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
        books = [control, *s.theta_variant_list]
        return [*books, *self._twin_books(books)]

    def _twin_books(self, books: list[dict]) -> list[dict]:
        """One twin book per ARMED live theta tag: the same market selection as its live parent
        (identical band/edge/window/thronly gates, evaluated through the SAME model-probability
        math the book loop already runs generically over `book[...]` keys — a twin must see
        exactly the candidate set live saw), but the LIVE execution parameters where they differ
        from paper: entry price (live maker rule), size (live dollar-cap sizing, not paper's
        `theta_order_size` clip), open cap (live's, not paper's much larger one), spread gate
        (live's sanity cap). A twin whose live tag isn't a theta book is skipped — another
        tracker owns it."""
        h = self.twin_harness
        if h is None or not h.enabled:
            return []
        by_tag = {b["tag"]: b for b in books}
        out: list[dict] = []
        for spec in h.active_specs():
            parent = by_tag.get(spec.live_tag)
            if parent is None:
                continue
            twin = dict(parent)
            twin["tag"] = spec.twin_tag
            twin["twin_of"] = spec.live_tag
            out.append(twin)
        return out

    def _twin_params(self, book: dict) -> dict:
        """The parameter snapshot stored on the twin's epoch row. Any later change to these makes
        the twin/live comparison non-comparable, which the harness flags as param drift."""
        s = self.settings
        return {
            "live_tag": book.get("twin_of"),
            "band_cents": [book["lo"], book["hi"]],
            "edge_cents": book["edge"],
            "vol_mult": book["mult"],
            "entry_window_minutes": [book["ttemin"], book["ttemax"]],
            "thronly": book["thronly"],
            "live_price_offset_cents": s.theta_live_price_offset_cents,
            "live_max_spread_cents": s.theta_live_max_spread_cents,
            "live_max_open_positions": s.theta_live_max_open_positions,
            "live_max_order_dollars": s.theta_live_max_order_dollars,
            "live_max_contracts": s.theta_live_max_contracts,
            "twin_max_open_positions": self.twin_harness.max_open_positions(
                s.theta_live_max_open_positions),
        }

    @staticmethod
    def _note(recorder, ticker: str, tag: str, outcome: str, price: int | None = None,
              quantity: int | None = None) -> None:
        """Record one book's decision on one candidate in the parity tape (no-op without twins)."""
        if recorder is not None:
            recorder.note_paper(ticker, tag, outcome, price, quantity)

    # -- one cycle ------------------------------------------------------------
    def run_once(self, session) -> ThetaCycleSummary:
        s = self.settings
        summ = ThetaCycleSummary()
        now_unix = int(time.time())
        # In collect-only (shelved) mode, only these variant tags may still trade; the rest of
        # the family snapshots only. Empty set => fully shelved.
        live_tags = s.theta_live_variant_set if s.theta_collect_only else None

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

        # Live/paper twins: open (or confirm) each twin's epoch BEFORE any entry, so `started_at`
        # is the true start of the parallel run and both sides of the comparison share a window.
        recorder = None
        twins_active = False
        if self.twin_harness is not None and self.twin_harness.enabled:
            recorder = self.twin_harness.recorder()
            for book in books:
                if book.get("twin_of"):
                    twins_active = True
                    spec = twin_codes.TwinSpec(live_tag=book["twin_of"], twin_tag=book["tag"])
                    self.twin_harness.ensure_epoch(session, spec, self._twin_params(book))

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
                # (the research dataset) but skip entries. Fast path: when the family is FULLY
                # shelved (no live variants) AND no twin is active, skip all entry work per
                # market. When a live variant is designated (theta_live_variants) OR a twin is
                # running, fall through and let the per-book gate below admit only that variant
                # (and any twin, which is exempt from the collect-only shelf — see above).
                if live_tags is not None and not live_tags and not twins_active:
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
                    is_twin = bool(book.get("twin_of"))
                    # collect-only: shelve every book except the designated live variant(s). A
                    # twin is exempt — it isn't a shelved revival experiment, it's the paper
                    # shadow of a tag that IS armed live (that's what made it a twin at all), so
                    # gating it the same way as an un-promoted variant would silently starve the
                    # parity read the moment the family is shelved.
                    if live_tags is not None and tag not in live_tags and not is_twin:
                        continue
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
                        self._note(recorder, ticker, tag, twin_codes.SKIP_ALREADY_OPEN)
                        continue
                    # A twin is capped like its LIVE parent (not like paper's much larger book) —
                    # the cap shapes which candidates each side ever sees, so it has to match.
                    # The per-event correlation cap stays shared: it bounds diversification risk
                    # on correlated crypto strikes, not capital, so there's no cross-book knob
                    # hazard in reusing theta's own value here (unlike the dollar/contract caps).
                    open_cap = (self.twin_harness.max_open_positions(s.theta_live_max_open_positions)
                                if is_twin else s.theta_max_open_positions)
                    if open_count[tag] >= open_cap or \
                            per_event_open[tag].get(event, 0) >= s.theta_max_per_event:
                        summ.capped += 1
                        self._note(recorder, ticker, tag, twin_codes.SKIP_CAP)
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
                            if recorder is not None:
                                recorder.discard(ticker)
                            break  # no book can enter this market this cycle
                        metrics = compute_metrics(mkt, ob, top_n=s.orderbook_depth)
                    if not metrics.two_sided or metrics.best_no_bid is None:
                        summ.skipped_illiquid += 1
                        if recorder is not None:
                            recorder.discard(ticker)  # no book could act: nothing to compare
                        break

                    if is_twin:
                        # The twin prices and sizes exactly as the live executor would, from the
                        # shared live/sizing helpers — the ONLY thing it does differently from
                        # live is assume the resting order fills.
                        if metrics.spread is not None \
                                and metrics.spread > s.theta_live_max_spread_cents:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_SPREAD)
                            continue
                        twin_price = maker_no_price(
                            metrics, None, s.theta_live_price_offset_cents)
                        if twin_price is None:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_ILLIQUID)
                            continue
                        twin_qty = order_quantity(
                            twin_price, s.theta_live_max_order_dollars, s.theta_live_max_contracts)
                        if twin_qty <= 0:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_SIZE, twin_price)
                            continue
                        self.twin_harness.open_twin_entry(
                            session, twin_tag=tag, ticker=ticker, side="no",
                            price=twin_price, quantity=twin_qty,
                            note=(f"live-twin sell yes @{100 - twin_price}c no@{twin_price}c "
                                 f"x{twin_qty} tte{tte_min:.0f}m"),
                            model_probability=p_book, edge=excess_book,
                        )
                        self._note(recorder, ticker, tag, twin_codes.OPENED, twin_price, twin_qty)
                        open_count[tag] += 1
                        per_event_open[tag][event] = per_event_open[tag].get(event, 0) + 1
                        open_tickers[tag].add(ticker)
                        summ.twin_opened += 1
                        summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                        continue

                    price = metrics.best_no_bid
                    if not (0 < price < 100):
                        summ.skipped_illiquid += 1
                        if recorder is not None:
                            recorder.discard(ticker)
                        break
                    qty = min(s.theta_order_size, metrics.depth_at_best_bid or 0)
                    if qty <= 0:
                        summ.skipped_illiquid += 1
                        if recorder is not None:
                            recorder.discard(ticker)
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
                    self._note(recorder, ticker, tag, twin_codes.OPENED, price, qty)
                    # Mirror the entry into a real resting maker NO-buy for allowlisted books
                    # (inert unless LIVE_STRATEGIES lists this tag). Self-guarded; a live failure
                    # never rolls back the paper record above (the paper book is the shadow).
                    if self.live_executor is not None:
                        try:
                            outcome = self.live_executor.mirror_theta_entry(
                                session, strategy=tag, event_ticker=event,
                                ticker=ticker, metrics=metrics, no_price=price,
                                account_state=self._account_state,
                            )
                            if recorder is not None:
                                live_px = maker_no_price(
                                    metrics, price, s.theta_live_price_offset_cents)
                                recorder.note_live(
                                    ticker, tag, outcome or twin_codes.LIVE_NOT_ATTEMPTED,
                                    live_px,
                                    order_quantity(live_px, s.theta_live_max_order_dollars,
                                                   s.theta_live_max_contracts)
                                    if live_px else None)
                        except AuthError:
                            raise
                        except Exception:  # noqa: BLE001 — paper record stays intact
                            logger.exception("theta live mirror_entry failed (paper unaffected)")
                            if recorder is not None:
                                recorder.note_live(ticker, tag, "error")
                    open_count[tag] += 1
                    per_event_open[tag][event] = per_event_open[tag].get(event, 0) + 1
                    open_tickers[tag].add(ticker)
                    summ.opened += 1
                    summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                    summ.per_series[series] = summ.per_series.get(series, 0) + 1

                if recorder is not None:
                    recorder.flush(session, ticker, series=series,
                                   hours_to_close=tte_min / 60.0, metrics=metrics)

        if snapshot_rows:
            summ.snapshot_rows = repo.insert_crypto_ladder_snapshots(
                session, snapshot_rows[: s.theta_snapshot_rows_cap]
            )
        return summ
