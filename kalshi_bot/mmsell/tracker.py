"""Market-making SELL book (BOT_MODE=mmsell) — forward-test the backtested maker edge.

The kalshi_mm backtest showed the resting (maker) side of Kalshi trades is +EV: SELLING yes on
overpriced cheap/underdog contracts (~5-40c) and holding to settlement nets a real edge that
survives worst-case fees, split-half OOS and off-sports. Selling yes = BUYING NO at the no-bid
(the maker price), so this book reuses the paper engine's buy/settle machinery: each cycle it
scans liquid open markets, and for every market whose yes MIDPOINT sits in the entry band it
opens a paper BUY-NO position at the no-bid, tagged strategy 'mmsell', held to settlement.

Honest limitation: paper ASSUMES the resting ask fills (enters at the maker no-bid price). It
therefore forward-tests whether the +EV persists out-of-sample on NEW markets — NOT queue/fill
realism, which only a small live test can prove. Diversification (many small positions) is the
risk control the exit study pointed to; hence the high max-open-positions default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..live.sizing import is_hot_entry, maker_no_price, order_quantity
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics, compute_time_to_close, market_volume
from ..twin import harness as twin_codes

logger = logging.getLogger(__name__)


@dataclass
class MmSellCycleSummary:
    events_seen: int = 0
    markets_considered: int = 0
    in_band: int = 0        # control-book scoped (stable across releases)
    opened: int = 0         # across control + revision books (twins excluded)
    twin_opened: int = 0    # live/paper twin books only (paper shadow of the live run)
    already_open: int = 0
    capped: int = 0
    skipped_illiquid: int = 0
    skipped_htc: int = 0
    skipped_vol_gate: int = 0   # anchor-set volatility ENTRY gate rejections
    per_series: dict[str, int] = field(default_factory=dict)
    per_book: dict[str, int] = field(default_factory=dict)


class MmSellTracker:
    STRATEGY = "mmsell"

    def __init__(self, client, settings: Settings, live_executor=None, twin_harness=None):
        self.client = client
        self.settings = settings
        # Set only in live mode (BOT_MODE=live); mirrors allowlisted paper entries into real
        # resting maker NO-buys. None in paper/weather mode -> the book stays paper-only.
        self.live_executor = live_executor
        # Live/paper parallel-run harness (docs/LIVE_PAPER_TWIN.md): runs a FRESH paper book
        # beside each armed live mmsell tag, priced/sized on the LIVE knobs, so the only
        # difference from live is the fill assumption. None -> no twins.
        self.twin_harness = twin_harness
        # Set by the live cycle before run_once so the live mirror can pass it to the balance gate.
        self._account_state: dict | None = None

    def _skip_series(self, series: str) -> bool:
        s = (series or "").upper()
        return any(s.startswith(p) for p in self.settings.mmsell_skip_series_list)

    @staticmethod
    def _vol_gate_blocks(session, book: dict, ticker: str) -> bool:
        """True when the anchor-set volatility ENTRY gate should SKIP this entry: the market's own
        pre-entry tape has already moved `volv` cents or more over the last `volw` candidate ticks.

        Thesis (docs/MMSELL_ANCHOR_SET.md): a cheap tail that is already moving is one informed
        flow is repricing, so resting into it is the adversely-selected side — the crypto backtest
        measured calm pre-entry tape at +2.85/+5.25c (100% win) vs active at -39c.

        Deliberately does NOT fire on thin history (a newly in-band market has no tape yet): those
        entries are taken exactly as the control takes them, so the gated book differs from
        mmsell10 ONLY by the entries the gate actually rejected — a clean A/B rather than a book
        that also silently changed its market selection."""
        w, v = book.get("volw"), book.get("volv")
        if not w or v is None:
            return False
        try:
            mids = repo.recent_candidate_mids(session, ticker, int(w))
        except Exception:  # noqa: BLE001 — a gate read must never break the entry scan
            logger.exception("mmsell vol gate: candidate history read failed (entering anyway)")
            return False
        if len(mids) < 3:
            return False                      # not enough tape to judge -> behave like the control
        return (max(mids) - min(mids)) >= float(v)

    @staticmethod
    def _event_has_both_tails(event: dict, maxyes: float) -> bool:
        """True when this event carries BOTH a cheap-YES tail (a high strike) and a cheap-NO tail
        (a low strike), i.e. a strangle is actually available on it.

        Read straight off the nested market payload the scan already holds — no extra API call.
        This pairing is the whole point: the backtest's +3.30c/pair came from events where both
        tails were simultaneously cheap, which is a LOW-VOLATILITY selection. Entering one leg
        alone would be an ordinary mmsell trade wearing a strangle label."""
        cheap_yes = cheap_no = False
        for mk in event.get("markets") or []:
            yb, ya = mk.get("yes_bid"), mk.get("yes_ask")
            if yb is None or ya is None:
                continue
            mid = (float(yb) + float(ya)) / 2.0
            if 0 < mid <= maxyes:
                cheap_yes = True              # high strike: YES is the cheap tail
            elif 0 < (100.0 - mid) <= maxyes:
                cheap_no = True               # low strike: NO is the cheap tail
            if cheap_yes and cheap_no:
                return True
        return False

    def _books(self) -> list[dict]:
        """Control book (base knobs, tag 'mmsell' — NEVER reparameterized), then the configured
        revision books, then any live/paper twin books. All share the scan/orderbook; only the
        band + htc differ. Twins come LAST so each market's live decision is already recorded in
        the parity tape by the time the twin (its paper shadow) is evaluated."""
        s = self.settings
        control = {
            "tag": self.STRATEGY,
            "lo": float(s.mmsell_entry_lo_cents),
            "hi": float(s.mmsell_entry_hi_cents),
            "htcmin": s.mmsell_min_hours_to_close,
            "htcmax": s.mmsell_max_hours_to_close,
            "skip": [],  # the control never filters by series (global mmsell_skip_series applies)
            "only": [],
            "maxyes": None,  # the control has no entry-price ceiling
            # The control runs no anchor-set mechanic: no stop, no vol gate, no strangle leg.
            "stopl": None, "stopk": 2, "volw": None, "volv": None, "strangle": False,
        }
        books = [control, *s.mmsell_variant_list]
        return [*books, *self._twin_books(books)]

    def _twin_books(self, books: list[dict]) -> list[dict]:
        """One twin book per ARMED live mmsell tag: the same market selection as its live parent
        (identical band/htc/series filters — a twin must see exactly the candidate set live saw),
        but the LIVE execution parameters, applied where they differ from paper:

          * entry price  = the live maker rule (no-bid + offset, capped at the no-ask)
          * size         = the live dollar-cap sizing, not paper's 1-contract clip
          * open cap     = the live book's cap, not paper's much larger one
          * spread gate  = the live sanity cap

        A twin whose live tag isn't an mmsell book is skipped — another tracker owns it."""
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
            "htc_hours": [book["htcmin"], book["htcmax"]],
            "skip": list(book.get("skip") or []),
            "only": list(book.get("only") or []),
            "maxyes": book.get("maxyes"),
            "live_price_offset_cents": s.mmsell_live_price_offset_cents,
            "live_max_spread_cents": s.mmsell_live_max_spread_cents,
            "live_hot_market_move_cents": s.mmsell_live_hot_market_move_cents,
            "live_hot_market_lookback_minutes": s.mmsell_live_hot_market_lookback_minutes,
            "live_hot_market_defensive_offset_cents": s.mmsell_live_hot_market_defensive_offset_cents,
            "live_max_open_positions": s.mmsell_live_max_open_positions,
            "live_max_order_dollars": s.live_max_order_dollars,
            "max_order_size": s.max_order_size,
            "twin_max_open_positions": self.twin_harness.max_open_positions(
                s.mmsell_live_max_open_positions),
        }

    @staticmethod
    def _note(recorder, ticker: str, tag: str, outcome: str, price: int | None = None,
              quantity: int | None = None) -> None:
        """Record one book's decision on one candidate in the parity tape (no-op without twins)."""
        if recorder is not None:
            recorder.note_paper(ticker, tag, outcome, price, quantity)

    @staticmethod
    def _book_admits_series(book: dict, series: str) -> bool:
        """Per-variant series filter: a book with a `skip` list drops any series containing one of
        its substrings; a book with an `only` list trades ONLY series containing one of its
        substrings. Matched case-insensitively against the (already-uppercased) series prefix.
        Empty lists (the control + band-only variants) admit everything."""
        skip = book.get("skip") or []
        if any(tok in series for tok in skip):
            return False
        only = book.get("only") or []
        if only and not any(tok in series for tok in only):
            return False
        return True

    def run_once(self, session) -> MmSellCycleSummary:
        s = self.settings
        summ = MmSellCycleSummary()

        # 1) collect liquid open events (skip parlays/weather), rank by volume, cap the scan.
        events: list[tuple[float, dict]] = []
        cursor = ""
        for _ in range(40):
            page = self.client.get_events(status="open", with_nested_markets=True,
                                          limit=200, cursor=cursor or None)
            evs = (page or {}).get("events") or []
            for e in evs:
                if self._skip_series(e.get("series_ticker") or ""):
                    continue
                vol = sum(market_volume(m) for m in e.get("markets") or [])
                if vol <= 0:
                    continue
                events.append((vol, e))
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not evs:
                break
        events.sort(key=lambda ev: -ev[0])
        events = events[: s.mmsell_top_events]
        summ.events_seen = len(events)

        books = self._books()
        open_count = {b["tag"]: repo.count_open_paper_positions(session, b["tag"]) for b in books}
        captured = 0  # per-cycle candidate-tick writes (bounded by mmsell_candidate_capture_max)

        # Live/paper twins: open (or confirm) each twin's epoch BEFORE any entry, so `started_at`
        # is the true start of the parallel run and both sides of the comparison share a window.
        recorder = None
        if self.twin_harness is not None and self.twin_harness.enabled:
            recorder = self.twin_harness.recorder()
            for book in books:
                if book.get("twin_of"):
                    spec = twin_codes.TwinSpec(live_tag=book["twin_of"], twin_tag=book["tag"])
                    self.twin_harness.ensure_epoch(session, spec, self._twin_params(book))

        # 2) per market: for each book whose band+htc admit it, open a maker BUY-NO at the
        #    no-bid (== sell yes at the ask), held to settlement. Books share one orderbook
        #    fetch; only the band (and htc) differ.
        for _vol, event in events:
            for market in event.get("markets") or []:
                summ.markets_considered += 1
                ticker = market.get("ticker")
                if not ticker:
                    continue
                if market_volume(market) < s.mmsell_min_volume:
                    continue
                htc_s = compute_time_to_close(market.get("close_time"))
                htc = htc_s / 3600.0 if htc_s is not None else None   # seconds -> hours
                # control htc gate scopes the shared work + the skipped_htc counter; a variant
                # with a wider htc than the control is not supported (control is the widest).
                if htc is None or not (s.mmsell_min_hours_to_close <= htc
                                       <= s.mmsell_max_hours_to_close):
                    summ.skipped_htc += 1
                    continue

                metrics = None  # lazy: fetched once for the first book that clears the band
                series = (event.get("series_ticker") or ticker.split("-")[0]).upper()
                for book in books:
                    tag = book["tag"]
                    if not (book["htcmin"] <= htc <= book["htcmax"]):
                        continue
                    if not self._book_admits_series(book, series):
                        continue  # per-variant series skip/allow filter
                    if metrics is None:
                        try:
                            ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
                        except AuthError:
                            raise
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "mmsell: orderbook fetch failed",
                                extra={"extra_fields": {"ticker": ticker, "error": str(exc)}})
                            if recorder is not None:
                                recorder.discard(ticker)
                            break  # no book can enter this market this cycle
                        metrics = compute_metrics(market, ob, top_n=s.orderbook_depth)
                    if not metrics.two_sided or metrics.midpoint is None \
                            or metrics.best_no_bid is None:
                        summ.skipped_illiquid += 1
                        if recorder is not None:
                            recorder.discard(ticker)  # no book could act: nothing to compare
                        break
                    # A strangle book also admits the MIRROR band (yes trading near 100, where the
                    # cheap tail is NO), and only when this event carries both tails — see
                    # _event_has_both_tails. Every other book sees the ordinary cheap-YES band only.
                    mirror_leg = False
                    if not (book["lo"] <= metrics.midpoint <= book["hi"]):
                        if not (book.get("strangle")
                                and (100.0 - book["hi"]) <= metrics.midpoint
                                <= (100.0 - book["lo"])):
                            continue
                        mirror_leg = True
                    if book.get("strangle"):
                        cap_y = book.get("maxyes") or book["hi"]
                        if not self._event_has_both_tails(event, float(cap_y)):
                            continue
                    # Entry-price ceiling: the band gates the MIDPOINT, but P&L is driven by the
                    # actual sell price (yes-ask = 100 - no-bid), which is always >= the midpoint.
                    # maxyes caps that directly — the live decomposition found the edge lives only
                    # in the cheapest longshots (yes <=7c +2.3c; 8-11c net negative).
                    maxyes = book.get("maxyes")
                    if maxyes is not None:
                        # Cheap side's actual entry price: the YES tail costs (100 - no_bid); the
                        # mirror NO tail costs (100 - yes_bid). Cap whichever leg this is.
                        cheap_px = ((100 - metrics.best_yes_bid) if mirror_leg
                                    else (100 - metrics.best_no_bid))
                        if metrics.best_yes_bid is None or cheap_px > maxyes:
                            continue
                    if tag == self.STRATEGY:
                        summ.in_band += 1
                        # Candidate capture: tape one orderbook snapshot per in-band candidate
                        # per cycle (off the book we already fetched — no extra API), whether or
                        # not a position opens, for the offline fill replay. Scoped to the control
                        # book so each market is taped once; fail-soft and per-cycle capped.
                        if s.mmsell_capture_candidates \
                                and captured < s.mmsell_candidate_capture_max:
                            try:
                                repo.insert_mmsell_candidate_tick(
                                    session, ticker, metrics, series=series, hours_to_close=htc)
                                captured += 1
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "mmsell: candidate tick capture failed",
                                    extra={"extra_fields": {"ticker": ticker, "error": str(exc)}})

                    is_twin = bool(book.get("twin_of"))

                    if repo.get_open_paper_position(session, ticker, tag) is not None:
                        summ.already_open += 1
                        self._note(recorder, ticker, tag, twin_codes.SKIP_ALREADY_OPEN)
                        continue
                    # A twin is capped like its LIVE parent (not like paper's much larger book) —
                    # the cap shapes which candidates each side ever sees, so it has to match.
                    cap = (self.twin_harness.max_open_positions(s.mmsell_live_max_open_positions)
                           if is_twin else s.mmsell_max_open_positions)
                    if open_count[tag] >= cap:
                        summ.capped += 1
                        self._note(recorder, ticker, tag, twin_codes.SKIP_CAP)
                        continue

                    if is_twin:
                        # The twin prices and sizes exactly as the live executor would, from the
                        # shared live/sizing helpers — the ONLY thing it does differently from
                        # live is assume the resting order fills.
                        if metrics.spread is not None \
                                and metrics.spread > s.mmsell_live_max_spread_cents:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_SPREAD)
                            continue
                        hot = is_hot_entry(
                            session, ticker, metrics.best_no_bid,
                            move_cents=s.mmsell_live_hot_market_move_cents,
                            lookback_minutes=s.mmsell_live_hot_market_lookback_minutes,
                        )
                        offset = (s.mmsell_live_hot_market_defensive_offset_cents if hot
                                 else s.mmsell_live_price_offset_cents)
                        price = maker_no_price(metrics, None, offset, hot=hot)
                        if price is None:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_ILLIQUID)
                            continue
                        qty = order_quantity(price, s.live_max_order_dollars, s.max_order_size)
                        if qty <= 0:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_SIZE, price)
                            continue
                        self.twin_harness.open_twin_entry(
                            session, twin_tag=tag, ticker=ticker, side="no",
                            price=price, quantity=qty,
                            note=f"live-twin sell yes @{100 - price}c no@{price}c x{qty}",
                        )
                        self._note(recorder, ticker, tag, twin_codes.OPENED, price, qty)
                        open_count[tag] += 1
                        summ.twin_opened += 1
                        summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                        continue

                    # Volatility ENTRY gate (anchor set): skip if this market's pre-entry tape has
                    # already moved. No-op for every book without volw/volv.
                    if self._vol_gate_blocks(session, book, ticker):
                        summ.skipped_vol_gate += 1
                        continue

                    # Ordinary leg: the maker sells YES at the yes-ask == buys NO at the no-bid.
                    # Mirror leg (strangle only): sells the cheap NO tail == buys YES at the
                    # yes-bid. Both are resting maker orders on the cheap side; the paper engine
                    # already settles a side='yes' position correctly.
                    side = "yes" if mirror_leg else "no"
                    price = metrics.best_yes_bid if mirror_leg else metrics.best_no_bid
                    if price is None or not (0 < price < 100):
                        summ.skipped_illiquid += 1
                        if recorder is not None:
                            recorder.discard(ticker)
                        break
                    qty = s.paper_order_size
                    fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                    sub = market.get("yes_sub_title") or market.get("subtitle") or ""
                    # fill_assumption is String(64); the repo layer also clamps, but keep the
                    # subtitle short and the prices first so truncation only costs subtitle chars.
                    sold, at = ("no", 100 - price) if mirror_leg else ("yes", 100 - price)
                    assumption = (
                        f"[{tag}] sell {sold} '{sub[:24]}' @ {at}c "
                        f"({side}@{price}c mid{metrics.midpoint:.0f}c)"
                    )[:64]
                    repo.create_paper_trade(
                        session,
                        signal_id=None,
                        ticker=ticker,
                        strategy=tag,
                        side=side,
                        action="buy",
                        assumed_price=price,
                        quantity=qty,
                        fill_assumption=assumption,
                        entry_fee=fee,
                        model_probability=None,
                        edge=0.0,
                    )
                    repo.open_paper_position_for_trade(
                        session, ticker=ticker, strategy=tag, side=side,
                        quantity=qty, avg_price=price,
                    )
                    self._note(recorder, ticker, tag, twin_codes.OPENED, price, qty)
                    # Mirror the entry into a real resting maker NO-buy for allowlisted books
                    # (inert unless LIVE_STRATEGIES lists this tag). Self-guarded; a live failure
                    # never rolls back the paper record above (the paper book is the shadow).
                    if self.live_executor is not None:
                        try:
                            outcome = self.live_executor.mirror_mmsell_entry(
                                session, strategy=tag, event_ticker=event.get("event_ticker"),
                                ticker=ticker, metrics=metrics, no_price=price,
                                account_state=self._account_state,
                            )
                            if recorder is not None:
                                # Record what live ACTUALLY did (placed, or the specific gate that
                                # stopped it) so the twin/live gap is attributable, not guessed.
                                # Recomputes the same hotness check mirror_mmsell_entry made
                                # internally moments earlier — nothing in the candidate tape
                                # changes within a cycle, so it agrees with what was actually sent.
                                live_hot = is_hot_entry(
                                    session, ticker, metrics.best_no_bid,
                                    move_cents=s.mmsell_live_hot_market_move_cents,
                                    lookback_minutes=s.mmsell_live_hot_market_lookback_minutes,
                                )
                                live_offset = (s.mmsell_live_hot_market_defensive_offset_cents
                                              if live_hot else s.mmsell_live_price_offset_cents)
                                live_px = maker_no_price(metrics, price, live_offset, hot=live_hot)
                                recorder.note_live(
                                    ticker, tag, outcome or twin_codes.LIVE_NOT_ATTEMPTED,
                                    live_px,
                                    order_quantity(live_px, s.live_max_order_dollars,
                                                   s.max_order_size) if live_px else None)
                        except AuthError:
                            raise
                        except Exception:  # noqa: BLE001 — paper record stays intact
                            logger.exception("mmsell live mirror_entry failed (paper unaffected)")
                            if recorder is not None:
                                recorder.note_live(ticker, tag, "error")
                    open_count[tag] += 1
                    summ.opened += 1
                    summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                    summ.per_series[series] = summ.per_series.get(series, 0) + 1

                if recorder is not None:
                    recorder.flush(session, ticker, series=series,
                                   hours_to_close=htc, metrics=metrics)

        return summ
