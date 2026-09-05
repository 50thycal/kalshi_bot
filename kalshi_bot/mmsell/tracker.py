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
import math
from dataclasses import dataclass, field

from .. import repository as repo
from ..config import Settings
from ..experiment_os.enforcement import LineageBlocked
from ..kalshi.errors import AuthError
from ..live.sizing import (
    arm_book_offset,
    is_hot_entry,
    maker_no_price,
    maker_offset,
    order_quantity,
)
from ..paper.engine import kalshi_fee
from ..scanner.metrics import (
    compute_metrics,
    compute_time_to_close,
    market_price_cents,
    market_volume,
    parse_dt,
)
from ..twin import harness as twin_codes
from .correlation import correlation_key, in_scope
from .market_types import DISCRETE, IN_PLAY, SCHEDULED, classify
from .quote_parity import BandProbe, QuoteParityAccumulator
from .regimes import regime_of

# Bands the inline-quote pre-filter experiment scores its decision table for
# (docs/MMSELL_QUOTE_PARITY.md). FIXED constants, deliberately not reads of live book config:
# the experiment accumulates over days, and a book retuned mid-run would silently redefine its
# own result. "wide" mirrors the control book `mmsell`; "tight" mirrors `mmsell10`, the live
# candidate — the one whose candidate stream a bad pre-filter would actually corrupt.
QUOTE_PARITY_BANDS: tuple[BandProbe, ...] = (
    BandProbe(name="wide", lo=5.0, hi=40.0, maxyes=None),
    BandProbe(name="tight", lo=5.0, hi=10.0, maxyes=7.0),
)

logger = logging.getLogger(__name__)


@dataclass
class MmSellCycleSummary:
    # --- scan-funnel counters (persisted per cycle; see MmSellTracker._record_scan_telemetry) --
    # These exist because Railway's log endpoint returns only the message text and drops the
    # structured fields, so a cycle's funnel was invisible in production. The 2026-08-08 scan
    # starvation could not be measured directly for exactly that reason and had to be inferred.
    pages_fetched: int = 0          # /events pages actually requested
    pagination_exhausted: bool = False  # False => we stopped at the page cap, universe truncated
    events_fetched: int = 0         # events returned by the API, before any filter
    events_out_of_window: int = 0   # dropped: no market inside the htc window
    events_eligible: int = 0        # survived skip/volume/window, i.e. the rankable universe
    events_dropped_by_cap: int = 0  # eligible events the top-N cut left unscanned
    events_seen: int = 0            # events actually scanned this cycle (post-cut)
    # Deep-scan counters (the per-book `scanmax` experiment). `events_seen` and
    # `markets_considered` deliberately stay scoped to the CONTROL's depth so the funnel
    # telemetry keeps meaning the same thing across this change — a jump in them would read as
    # the scan recovering rather than as an experiment being added. These carry the extra reach.
    events_scanned_deep: int = 0       # events actually fetched, incl. beyond the control cap
    markets_considered_deep: int = 0   # markets seen ONLY because a book asked to scan deeper
    markets_considered: int = 0
    in_band: int = 0        # control-book scoped (stable across releases)
    opened: int = 0         # across control + revision books (twins excluded)
    twin_opened: int = 0    # live/paper twin books only (paper shadow of the live run)
    already_open: int = 0
    capped: int = 0
    skipped_illiquid: int = 0
    skipped_htc: int = 0
    # Orderbook fetches the inline-quote pre-filter avoided (0 unless it is armed). This is the
    # saving; the cost is invisible by construction, which is why it is only ever armed off the
    # shadow decision table rather than off this number.
    skipped_prefilter: int = 0
    skipped_vol_gate: int = 0   # anchor-set volatility ENTRY gate rejections
    skipped_strangle_paired: int = 0  # A5: this event's side already has a leg (see below)
    skipped_settlement_cap: int = 0  # too many open positions already settle this candidate's date
    skipped_event_cap: int = 0       # too many distinct events open on a CORRELATED-regime date
    skipped_event_rung_cap: int = 0  # too many rungs open on ONE non-mutually-exclusive event
    skipped_correlation_cap: int = 0  # the candidate's unit of correlation is already held
    #: Books dropped this cycle because their tag resolves to no active Experiment OS
    #: deployment arm. Counted rather than raised: one book's lineage problem must not
    #: cost every other book its cycle (XOS-000011).
    blocked_books: list[str] = field(default_factory=list)
    live_retried: int = 0        # live entry re-posted on a ticker paper already holds
    live_retry_capped: int = 0   # retry declined: mmsell_live_max_attempts_per_ticker reached
    live_retry_drifted: int = 0  # retry declined: market moved off the first attempt's price
    per_series: dict[str, int] = field(default_factory=dict)
    per_book: dict[str, int] = field(default_factory=dict)
    # Inline-quote pre-filter experiment (docs/MMSELL_QUOTE_PARITY.md). Scored off the orderbook
    # the scan already fetched, so it costs no extra API call; None until a cycle runs.
    quote_parity: QuoteParityAccumulator | None = None


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
    def _event_has_window_market(event: dict, s: Settings) -> bool:
        """Does this event carry at least one market inside the control's entry window?

        Applied BEFORE the top-N volume cut so the scan budget is spent on events we could
        actually trade — see the note in run_once. Uses exactly the same bound the per-market
        gate applies later, so an event admitted here can never be admitted for a reason the
        market gate would then reject wholesale.

        An event with no parseable close_time is KEPT: the per-market gate will drop it a moment
        later, and dropping it here on a parse failure would silently shrink the universe for a
        data problem rather than a trading decision."""
        markets = event.get("markets") or []
        if not markets:
            return False
        saw_time = False
        for m in markets:
            htc_s = compute_time_to_close(m.get("close_time"))
            if htc_s is None:
                continue
            saw_time = True
            if s.mmsell_min_hours_to_close <= htc_s / 3600.0 <= s.mmsell_max_hours_to_close:
                return True
        return not saw_time

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

    def _book_arm_offset(self, book: dict, ticker: str) -> int | None:
        """This book's own queue-position offset for `ticker`, or None when the book is not an
        arm book. A per-arm book that returns None from arm_book_offset does not belong to this
        ticker at all and must skip it — see _book_admits_ticker."""
        s = self.settings
        return arm_book_offset(
            ticker, book.get("abarm"),
            arms=s.mmsell_live_offset_ab_arm_list, salt=s.mmsell_live_offset_ab_salt,
        )

    def _book_admits_ticker(self, book: dict, ticker: str) -> bool:
        """False when this is a queue-position ARM book and the ticker belongs to the other arm.

        Every non-arm book (`abarm` unset) admits everything, so this is inert for the whole
        existing cohort. An arm book whose experiment is switched off (no configured arms) admits
        NOTHING rather than silently trading at the default offset — an arm book only has a
        defined price when the experiment is running, so failing closed is the safe direction."""
        if book.get("abarm") is None:
            return True
        return self._book_arm_offset(book, ticker) is not None

    def _settlement_cap_blocks(self, session, s: Settings, *, book_cap: int, tag: str,
                               ticker: str, close_dt, series: str, event_ticker: str,
                               mutually_exclusive: bool | None,
                               summ: MmSellCycleSummary, recorder) -> bool:
        """True when a concentration cap should SKIP this entry: too many of `tag`'s own open
        positions already settle on this candidate's date (docs/MMSELL_SEASONAL_FORECAST.md
        "Reading 3"), or (on a CORRELATED regime's date) too many distinct EVENTS already do, or
        too many rungs are already open on THIS event and the event is not mutually exclusive.

        `book_cap` is the SAME cap `open_count[tag]` was just checked against (paper's 200 or a
        twin's live-sized 60) — the date cap is a percentage OF that, so a twin gets the tighter
        live-shaped number automatically, the same asymmetry the position cap already applies."""
        if not s.mmsell_settlement_cap_enabled or close_dt is None:
            return False
        try:
            n_on_date, events_on_date = repo.open_positions_settlement_summary(
                session, tag, close_dt.date(), ticker)
        except Exception:  # noqa: BLE001 — a gate read must never break the entry scan
            logger.exception("mmsell settlement cap: read failed (entering anyway)")
            return False
        date_cap = max(1, math.ceil(book_cap * s.mmsell_settlement_cap_pct))
        if n_on_date >= date_cap:
            summ.skipped_settlement_cap += 1
            self._note(recorder, ticker, tag, twin_codes.SKIP_SETTLEMENT_CAP)
            return True
        if (regime_of(series) in s.mmsell_settlement_correlated_regimes_list
                and event_ticker not in events_on_date
                and len(events_on_date) >= s.mmsell_settlement_event_cap):
            # A NEW event on an already-saturated correlated date is refused; adding another
            # rung to an event already represented is handled by the rung cap below, which knows
            # whether those rungs actually hedge each other.
            summ.skipped_event_cap += 1
            self._note(recorder, ticker, tag, twin_codes.SKIP_EVENT_CAP)
            return True
        # Within-event rung cap. Same-event rungs are only a hedge when at most one can resolve
        # YES, which is exactly what Kalshi's event-level `mutually_exclusive` asserts. On a
        # disjoint bucket ladder (KXWTIW `between` strikes, mutually_exclusive=true) stacking
        # rungs stays free. On a NESTED threshold ladder (KXWTI `-T` "above X",
        # mutually_exclusive=false) one print resolves every rung the same way, so N rungs is one
        # position at N x size and gets capped. Unknown (field absent) is treated as not
        # exclusive: the cap is cheap and the concentration it prevents is not.
        if (s.mmsell_event_rung_cap_enabled and event_ticker and not mutually_exclusive
                and events_on_date.get(event_ticker, 0) >= s.mmsell_event_rung_cap):
            summ.skipped_event_rung_cap += 1
            self._note(recorder, ticker, tag, twin_codes.SKIP_EVENT_RUNG_CAP)
            return True
        return False

    def _correlation_cap_blocks(self, session, book: dict, *, tag: str, ticker: str,
                                series: str, event_ticker: str,
                                summ: MmSellCycleSummary, recorder) -> bool:
        """True when this book already holds `corrcap` open positions in the candidate's own unit
        of CORRELATION, so the entry would add size to a bet it is already carrying rather than a
        new one (docs/MMSELL_CORRELATION_CAP.md, XOS-000020).

        Inert for every book that does not declare `corrcap` — which is the whole existing
        cohort, so no running book's candidate stream changes by a single market.

        The cap it applies is NOT the rung cap with a different number. The rung cap counts
        `event_ticker`, which is series x occasion; this counts the occasion itself, so an MLB
        game's TOTAL, TEAMTOTAL, SPREAD and HR markets count against ONE budget instead of four.
        `corrscope` decides which kinds of key are subject to it, so the contest axis can be
        tested without also tightening every ladder — see `correlation.in_scope`.

        Fail-soft in the same direction as the settlement cap: a read that raises lets the entry
        through rather than stopping the scan. A cap is a risk refinement, not a safety
        interlock — the position cap and the risk envelope are what bound real exposure — so its
        unavailability must not cost the book its cycle."""
        cap = book.get("corrcap")
        if not cap:
            return False
        kind, key = correlation_key(series, event_ticker)
        if not key or not in_scope(kind, book.get("corrscope") or "all"):
            return False
        try:
            rows = repo.open_positions_correlation_rows(session, tag, ticker)
        except Exception:  # noqa: BLE001 — a gate read must never break the entry scan
            logger.exception("mmsell correlation cap: read failed (entering anyway)")
            return False
        held = sum(1 for s, e in rows if e and correlation_key(s or "", e) == (kind, key))
        if held < cap:
            return False
        summ.skipped_correlation_cap += 1
        self._note(recorder, ticker, tag, twin_codes.SKIP_CORRELATION_CAP)
        return True

    def _live_price_and_size(self, session, ticker: str, no_price: int | None, metrics,
                             book: dict | None = None):
        """The (price, quantity) the live executor would use for this entry right now — the same
        hot-market-aware arithmetic mirror_mmsell_entry runs internally, recomputed here so the
        parity tape records what was actually sent rather than a guess. Nothing in the candidate
        tape changes within a cycle, so the two agree."""
        s = self.settings
        hot = is_hot_entry(
            session, ticker, metrics.best_no_bid,
            move_cents=s.mmsell_live_hot_market_move_cents,
            lookback_minutes=s.mmsell_live_hot_market_lookback_minutes,
            lookup=repo.latest_mmsell_no_bid_before,
        )
        arm_offset = self._book_arm_offset(book or {}, ticker)
        if hot:
            offset = s.mmsell_live_hot_market_defensive_offset_cents
        elif arm_offset is not None:
            offset = arm_offset          # this book IS an arm: its offset is the treatment
        else:
            offset, _arm = maker_offset(
                ticker, hot=hot,
                calm_offset=s.mmsell_live_price_offset_cents,
                hot_offset=s.mmsell_live_hot_market_defensive_offset_cents,
                ab_arms=s.mmsell_live_offset_ab_arm_list,
                ab_salt=s.mmsell_live_offset_ab_salt,
            )
        price = maker_no_price(metrics, no_price, offset, hot=hot)
        size = (book or {}).get("size") or s.max_order_size
        qty = order_quantity(price, s.live_max_order_dollars, size) if price else None
        return price, qty

    def _maybe_retry_live(self, session, *, tag: str, event: dict, ticker: str, metrics,
                          summ: MmSellCycleSummary, recorder, book: dict | None = None) -> None:
        """Re-post the LIVE maker order on a ticker the PAPER book already holds.

        Why this exists: paper never misses a fill, so its position stays open to settlement and
        the caller's skip_already_open guard fires on every later cycle. That guard also skipped
        the live mirror, so live got exactly ONE attempt per ticker for the ticker's whole life.
        Measured live 2026-07-31: all 71 tickers in the epoch had exactly 1 live order, 29 of them
        never filled, and the missed set earned the SAME in paper as the captured one (6.15 vs
        6.26 c/contract) — lost volume, not adverse selection dodged.

        Paper is untouched here; only the live mirror re-fires. The caller has already applied this
        book's band + maxyes checks to the CURRENT quote, so a retry can only happen while the
        market is still a genuine candidate. On top of that:
          * at most `mmsell_live_max_attempts_per_ticker` live attempts ever (cancelled included),
          * only while the current no-bid is within `mmsell_live_retry_max_drift_cents` of the
            FIRST attempt's price, so a retry never chases a market that has repriced away from
            the edge the original entry was sized against,
          * mirror_mmsell_entry's own dedup still refuses a duplicate while an order rests or
            after a fill — that is what stops this from re-posting every single cycle.

        A ticker live never attempted at all is deliberately out of scope: there is no price
        anchor, and that is a different failure (a gate) than a missed retry."""
        s = self.settings
        if self.live_executor is None or metrics is None or metrics.best_no_bid is None:
            return
        attempts, first_price = repo.live_attempt_stats(session, ticker, tag)
        if attempts == 0:
            return
        if attempts >= s.mmsell_live_max_attempts_per_ticker:
            summ.live_retry_capped += 1
            return
        if first_price is not None and abs(int(metrics.best_no_bid) - first_price) \
                > s.mmsell_live_retry_max_drift_cents:
            summ.live_retry_drifted += 1
            return
        try:
            outcome = self.live_executor.mirror_mmsell_entry(
                session, strategy=tag, event_ticker=event.get("event_ticker"),
                ticker=ticker, metrics=metrics, no_price=metrics.best_no_bid,
                account_state=self._account_state,
                arm_offset=self._book_arm_offset(book or {}, ticker),
                max_contracts=(book or {}).get("size"),
            )
        except AuthError:
            raise
        except Exception:  # noqa: BLE001 — a retry must never break the scan
            logger.exception("mmsell live entry retry failed")
            if recorder is not None:
                recorder.note_live(ticker, tag, "error")
            return
        if outcome == twin_codes.LIVE_PLACED:
            summ.live_retried += 1
            logger.info("mmsell live entry retried", extra={"extra_fields": {
                "ticker": ticker, "strategy": tag, "attempt": attempts + 1,
                "first_limit_price": first_price, "no_bid": metrics.best_no_bid}})
        if recorder is not None:
            live_px, live_qty = self._live_price_and_size(
                session, ticker, metrics.best_no_bid, metrics, book)
            recorder.note_live(ticker, tag, outcome or twin_codes.LIVE_NOT_ATTEMPTED,
                               live_px, live_qty)

    @staticmethod
    def _event_has_both_tails(event: dict, maxyes: float) -> bool:
        """True when this event carries BOTH a cheap-YES tail (a high strike) and a cheap-NO tail
        (a low strike), i.e. a strangle is actually available on it.

        Read straight off the nested market payload the scan already holds — no extra API call.
        This pairing is the whole point: the backtest's +3.30c/pair came from events where both
        tails were simultaneously cheap, which is a LOW-VOLATILITY selection. Entering one leg
        alone would be an ordinary mmsell trade wearing a strangle label.

        Prices go through `market_price_cents`, NOT a raw `.get("yes_bid")`: the live events
        endpoint sends `yes_bid_dollars`/`yes_ask_dollars` and omits the integer-cent keys, so
        the raw read returned None for every market and this returned False for every event —
        which is why mmsellA5 never opened a single position."""
        cheap_yes = cheap_no = False
        for mk in event.get("markets") or []:
            yb = market_price_cents(mk, "yes_bid")
            ya = market_price_cents(mk, "yes_ask")
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

    @staticmethod
    def _strangle_leg_taken(session, tag: str, event_ticker: str, side: str) -> bool:
        """True when `tag` already holds a `side` leg on this event — caps the strangle to
        genuinely ONE leg per side per event.

        `_event_has_both_tails` only certifies that a cheap-YES tail and a cheap-NO tail exist
        SOMEWHERE among the event's markets — on a two-market event that is equivalent to "the
        pair", but on a multi-strike ladder (NFL spread/total, weather buckets...) an event can
        carry several markets that independently clear the SAME side's band. Without this check
        every one of them opens its own leg: same-side legs on one ladder are positively
        correlated (a single game result can move several strikes together), not the mutually
        exclusive pair the strangle's whole thesis rests on. Found 2026-08-14 once NFL supply
        made it visible — a spread ladder entered four cheap-NO legs on one event and zero
        cheap-YES legs, so a bad result there could have lost all four together.

        Any status counts (not just open): once an event has taken a side's leg, re-entering
        that side later — even after the first leg has settled or stopped — is still the same
        underlying game result repeated, not a fresh independent pair."""
        if not event_ticker:
            return False           # can't dedup without an event key; fail OPEN like the other gates
        try:
            return repo.event_has_strangle_leg(session, tag, event_ticker, side)
        except Exception:  # noqa: BLE001 — a gate read must never break the entry scan
            logger.exception("mmsell strangle pairing: leg-dedup read failed (entering anyway)")
            return False

    def _book_admissible(self, session, tag: str, summ: MmSellCycleSummary) -> bool:
        """Can this book's tag record a trade at all this cycle?

        `enforcement.tag_admissible` is the sanctioned pre-check and already swallows
        the refusal, so this neither weakens the block nor hides it: the tag is still
        refused, still counted, and still logged once per outage by the enforcement
        layer. What changes is the blast radius — the book is skipped instead of the
        cycle being aborted.

        A failure to ASK is not a licence to trade: an unexpected error here drops the
        book too, because a book whose admissibility is unknown is exactly the case
        NEW_ONLY exists to refuse."""
        from ..experiment_os import enforcement as xos_enforcement

        try:
            if xos_enforcement.tag_admissible(session, tag):
                return True
        except Exception:  # noqa: BLE001 — fail closed for this book, not for the cycle
            logger.exception(
                "mmsell: admissibility check failed; skipping book",
                extra={"extra_fields": {"tag": tag}})
        else:
            logger.warning(
                "mmsell: book skipped — tag has no active Experiment OS deployment arm",
                extra={"extra_fields": {"tag": tag}})
        summ.blocked_books.append(tag)
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
            # ...and no market-type filter: the control trades every structure it finds, which
            # is exactly what makes it the baseline the Wmmsell* type books are read against.
            "mtype": [], "xmtype": [], "mode": [],
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
            "mtype": list(book.get("mtype") or []),
            "xmtype": list(book.get("xmtype") or []),
            "mode": list(book.get("mode") or []),
            "live_price_offset_cents": s.mmsell_live_price_offset_cents,
            # Changing either of these re-randomizes the queue-position experiment mid-flight,
            # which makes twin-vs-live non-comparable across the change — recorded here so the
            # harness reports it as param drift instead of silently blending two experiments.
            "live_offset_ab_arms": list(s.mmsell_live_offset_ab_arm_list),
            "live_offset_ab_salt": s.mmsell_live_offset_ab_salt,
            "live_max_spread_cents": s.mmsell_live_max_spread_cents,
            "live_hot_market_move_cents": s.mmsell_live_hot_market_move_cents,
            "live_hot_market_lookback_minutes": s.mmsell_live_hot_market_lookback_minutes,
            "live_hot_market_defensive_offset_cents": s.mmsell_live_hot_market_defensive_offset_cents,
            "live_max_attempts_per_ticker": s.mmsell_live_max_attempts_per_ticker,
            "live_retry_max_drift_cents": s.mmsell_live_retry_max_drift_cents,
            "live_max_open_positions": s.mmsell_live_max_open_positions,
            "live_max_order_dollars": s.live_max_order_dollars,
            "max_order_size": s.max_order_size,
            "twin_max_open_positions": self.twin_harness.max_open_positions(
                s.mmsell_live_max_open_positions),
        }

    def _record_scan_telemetry(self, session, summ: MmSellCycleSummary) -> None:
        """Persist this cycle's scan funnel to `system_events` so it can be QUERIED.

        Railway's log endpoint returns only a log line's message and drops its structured
        fields, so `log_event(..., in_band=..., out_of_window=...)` was write-only in
        production — the counters existed but could never be read back. That is why the
        2026-08-08 scan starvation had to be inferred from candidate-tick volume and a live
        market survey instead of simply measured.

        Fail-soft: a telemetry write must never break the trading cycle."""
        counts = getattr(self.client, "transient_counts", None)
        transient = {}
        if callable(counts):
            try:
                transient = {str(k): v for k, v in counts().items()}
            except Exception:  # noqa: BLE001
                transient = {}
        try:
            repo.log_system_event(
                session, level="INFO", component="mmsell_scan",
                message="mmsell scan funnel",
                raw={
                    "pages_fetched": summ.pages_fetched,
                    "pagination_exhausted": summ.pagination_exhausted,
                    "events_fetched": summ.events_fetched,
                    "events_out_of_window": summ.events_out_of_window,
                    "events_eligible": summ.events_eligible,
                    "events_dropped_by_cap": summ.events_dropped_by_cap,
                    "events_scanned_deep": summ.events_scanned_deep,
                    "markets_considered_deep": summ.markets_considered_deep,
                    "events_seen": summ.events_seen,
                    "markets_considered": summ.markets_considered,
                    "in_band": summ.in_band,
                    "opened": summ.opened,
                    "already_open": summ.already_open,
                    "capped": summ.capped,
                    "skipped_htc": summ.skipped_htc,
                    "skipped_prefilter": summ.skipped_prefilter,
                    "skipped_illiquid": summ.skipped_illiquid,
                    "skipped_vol_gate": summ.skipped_vol_gate,
                    "skipped_strangle_paired": summ.skipped_strangle_paired,
                    "skipped_settlement_cap": summ.skipped_settlement_cap,
                    "skipped_event_cap": summ.skipped_event_cap,
                    "skipped_event_rung_cap": summ.skipped_event_rung_cap,
                    "skipped_correlation_cap": summ.skipped_correlation_cap,
                    "per_series": dict(sorted(summ.per_series.items(),
                                              key=lambda kv: -kv[1])[:12]),
                    "per_book": summ.per_book,
                    # Retryable Kalshi responses by HTTP status, cumulative since process start
                    # (0 = network error). Consecutive rows subtract to a per-cycle rate. This
                    # is how a 429 becomes VISIBLE: the retry loop treats a rate limit and a
                    # Kalshi 502 identically, and Railway's log endpoint drops the structured
                    # `status` field, so the difference existed nowhere queryable — while a 429
                    # is the one signal that says the scan is too big for our API tier.
                    "transient": transient,
                },
            )
        except Exception:  # noqa: BLE001 — diagnostics must never break the cycle
            logger.exception("mmsell scan telemetry write failed (cycle unaffected)")

        if summ.quote_parity is None:
            return
        try:
            repo.log_system_event(
                session, level="INFO", component="mmsell_quote_parity",
                message="mmsell inline-quote parity",
                raw=summ.quote_parity.as_dict(),
            )
        except Exception:  # noqa: BLE001 — diagnostics must never break the cycle
            logger.exception("mmsell quote-parity telemetry write failed (cycle unaffected)")

    @staticmethod
    def _note(recorder, ticker: str, tag: str, outcome: str, price: int | None = None,
              quantity: int | None = None) -> None:
        """Record one book's decision on one candidate in the parity tape (no-op without twins)."""
        if recorder is not None:
            recorder.note_paper(ticker, tag, outcome, price, quantity)

    @staticmethod
    def _prefilter_skips(s: Settings, market: dict, series: str,
                         interested: list[dict]) -> bool:
        """May we skip this market's ORDERBOOK fetch on the strength of the event page's own
        quote alone? (docs/MMSELL_QUOTE_PARITY.md)

        Off by default. Three properties make the difference between a saving and a silent leak:

        1. **The union of bands, never one book's.** The orderbook fetch is SHARED — one call
           serves every book that reaches this market — so skipping it removes the candidate
           from all of them. The test is therefore against the widest band among the books that
           actually got here, plus the loosest ceiling. A per-book test would starve whichever
           book has the widest band the moment a narrower one declined.
        2. **A missing or unreadable inline quote never skips.** No data is not evidence of
           being out of band, and the failure is invisible — a skipped market produces no error,
           it just stops existing as a candidate.
        3. **in_play is always fetched** unless explicitly trusted. The large disagreements
           concentrate in fast-moving contests, where the event page's one snapshot is stale by
           the time the scan reaches this market. An UNCLASSIFIED series counts as in_play here:
           the conservative reading of "we do not know what this is" is to spend the call."""
        if not interested:
            return False
        mid = None
        bid = market_price_cents(market, "yes_bid")
        ask = market_price_cents(market, "yes_ask")
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        if mid is None:
            return False
        if not s.mmsell_prefilter_trust_in_play and classify(series)[1] not in (SCHEDULED,
                                                                                DISCRETE):
            return False

        margin = float(s.mmsell_prefilter_margin_cents)
        lo = min(b["lo"] for b in interested)
        hi = max(b["hi"] for b in interested)
        if lo - margin <= mid <= hi + margin:
            return False
        # Outside every band even after the margin. One more guard: a book with no `maxyes`
        # cares only about the midpoint, but one WITH a ceiling could still be admitted by a
        # cheap ask on a market whose midpoint sits high, so the ceiling is checked too.
        ceilings = [b.get("maxyes") for b in interested if b.get("maxyes") is not None]
        if ceilings and ask is not None and ask <= max(ceilings) + margin:
            return False
        return True

    @staticmethod
    def _book_admits_series(book: dict, series: str) -> bool:
        """Per-variant series filter: a book with a `skip` list drops any series containing one of
        its substrings; a book with an `only` list trades ONLY series containing one of its
        substrings. Matched case-insensitively against the (already-uppercased) series prefix.
        Empty lists (the control + band-only variants) admit everything.

        Then the market-TYPE filters (docs/MMSELL_TYPE_BOOKS.md), which select on the contract's
        structure via the taxonomy rather than on a series substring:
          mtype  — allowlist of market types  (empty = admit all)
          mode   — allowlist of settle modes  (empty = admit all)
          xmtype — blocklist of market types, applied last so it always wins

        A series the taxonomy does not know classifies as `unclassified`/`unknown`. That is in
        no ALLOWLIST, so a book asking for specific types/modes never silently picks up a
        contract nobody has classified. It is also in no BLOCKLIST, so a pure `xmtype` book
        ("everything except the known bleeders") still takes it — which is the right asymmetry:
        those books are defined as their control minus named types, and dropping unknowns too
        would make them differ from the control by more than the thing under test.
        Books with none of the three keys are unaffected."""
        skip = book.get("skip") or []
        if any(tok in series for tok in skip):
            return False
        only = book.get("only") or []
        if only and not any(tok in series for tok in only):
            return False

        mtype = book.get("mtype") or []
        xmtype = book.get("xmtype") or []
        mode = book.get("mode") or []
        if not (mtype or xmtype or mode):
            return True
        m_type, m_mode = classify(series)
        if mtype and m_type not in mtype:
            return False
        if mode and m_mode not in mode:
            return False
        if xmtype and m_type in xmtype:
            return False
        return True

    def run_once(self, session) -> MmSellCycleSummary:
        s = self.settings
        summ = MmSellCycleSummary()
        if s.mmsell_quote_parity:
            summ.quote_parity = QuoteParityAccumulator(bands=QUOTE_PARITY_BANDS)

        # 1) collect liquid open events (skip parlays/weather), keep only those carrying a market
        #    INSIDE the entry window, rank by volume, then cap the scan.
        #
        # The window filter runs BEFORE the cap deliberately. Ranking the whole universe by volume
        # and taking the top N spends the scan budget on whatever is LARGEST, and Kalshi's largest
        # events are long-dated futures — 2028 nominations, season championships, end-of-year
        # crypto — which the htc gate then discards, leaving the cap spent on nothing.
        #
        # Measured 2026-08-08: in-band series seen per day fell from 32 to 2 over two weeks while
        # Kalshi still listed 9,656 sports markets across 569 series. The cause was exactly this
        # ordering — World Cup game events (high volume AND short-dated) rolled off and election
        # futures took their slots. The cap exists to bound API/compute cost, never to choose
        # which markets we trade.
        events: list[tuple[float, dict]] = []
        cursor = ""
        for _ in range(s.mmsell_event_pages):
            page = self.client.get_events(status="open", with_nested_markets=True,
                                          limit=200, cursor=cursor or None)
            summ.pages_fetched += 1
            evs = (page or {}).get("events") or []
            for e in evs:
                summ.events_fetched += 1
                if self._skip_series(e.get("series_ticker") or ""):
                    continue
                vol = sum(market_volume(m) for m in e.get("markets") or [])
                if vol <= 0:
                    continue
                if not self._event_has_window_market(e, s):
                    summ.events_out_of_window += 1
                    continue
                events.append((vol, e))
            cursor = (page or {}).get("cursor") or ""
            if not cursor or not evs:
                summ.pagination_exhausted = True
                break
        summ.events_eligible = len(events)
        events.sort(key=lambda ev: -ev[0])
        books = self._books()
        # The scan reaches as deep as the DEEPEST book asks for, but each book is then gated on
        # the event's RANK below, so a book with no `scanmax` sees exactly the top-N it always
        # saw. That separation is the whole point: raising the global cap would change the
        # candidate stream of every paper book and both live arms simultaneously, making every
        # number collected before the change incomparable with every number after it. Here the
        # incumbents are untouched and only the book under test is offered the extra events.
        scan_depth = max([s.mmsell_top_events, *(b["scanmax"] for b in books
                                                 if b.get("scanmax"))])
        events = events[:scan_depth]
        # `events_seen` stays scoped to the CONTROL's depth so the funnel telemetry means the
        # same thing across this change — a jump in it would otherwise read as the scan
        # recovering rather than as an experiment being added.
        summ.events_seen = min(len(events), s.mmsell_top_events)
        summ.events_scanned_deep = len(events)
        # Did the top-N cut bind? If eligible > seen we are leaving tradeable events unscanned,
        # which is a capacity signal rather than a fault — but it must be visible either way.
        summ.events_dropped_by_cap = max(0, summ.events_eligible - summ.events_seen)

        # Drop books whose tag has no active Experiment OS deployment arm, ONCE per cycle,
        # before any entry. Under NEW_ONLY such a tag is fail-closed at the write path, and
        # that refusal used to propagate out of this method — where main._run_mmsell_book's
        # single session_scope rolled the whole transaction back, discarding every OTHER
        # book's entries too. That is how one retired experiment's dangling deployment took
        # the entire mmsell family dark for four days (XOS-000011). A blocked book is that
        # book's problem; the rest of the scan is entitled to its cycle.
        books = [b for b in books if self._book_admissible(session, b["tag"], summ)]
        if not books:
            logger.error(
                "mmsell: every book is lineage-blocked; nothing can trade this cycle",
                extra={"extra_fields": {"blocked": summ.blocked_books}})
            return summ

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
        for rank, (_vol, event) in enumerate(events):
            # An event past the control's depth exists only for books that asked to see deeper.
            # Counted separately so the extra reach is visible as its own number rather than
            # inflating markets_considered, which the funnel telemetry is read against.
            deep = rank >= s.mmsell_top_events
            for market in event.get("markets") or []:
                if deep:
                    summ.markets_considered_deep += 1
                else:
                    summ.markets_considered += 1
                ticker = market.get("ticker")
                if not ticker:
                    continue
                if market_volume(market) < s.mmsell_min_volume:
                    continue
                htc_s = compute_time_to_close(market.get("close_time"))
                htc = htc_s / 3600.0 if htc_s is not None else None   # seconds -> hours
                # Forward-looking resolution clock, recorded (never gated on) alongside htc.
                # For an in-play market `close_time` is a far-future fallback — KXUFCFIGHT
                # reports ~335h to close on a fight that resolves in 0.4h — so htc cannot
                # express "enter in the final 30 minutes" and the timing study has to score
                # history on realized hold instead (docs/MMSELL_TIMING_STUDY.md). Kalshi's
                # expected expiration is the only estimate available BEFORE the fact; capturing
                # it now is what makes an in-play timing gate testable later.
                exp_s = compute_time_to_close(
                    market.get("expected_expiration_time") or market.get("expiration_time"))
                hte = exp_s / 3600.0 if exp_s is not None else None
                # control htc gate scopes the shared work + the skipped_htc counter; a variant
                # with a wider htc than the control is not supported (control is the widest).
                if htc is None or not (s.mmsell_min_hours_to_close <= htc
                                       <= s.mmsell_max_hours_to_close):
                    summ.skipped_htc += 1
                    continue

                metrics = None  # lazy: fetched once for the first book that clears the band
                series = (event.get("series_ticker") or ticker.split("-")[0]).upper()
                close_dt = parse_dt(market.get("close_time"))
                event_ticker = event.get("event_ticker") or ""
                # Kalshi's own answer to "can more than one of these rungs resolve YES?", read
                # straight off the event page the scan already fetched (no extra call). Absent
                # on an event we somehow got without the field -> None, which the rung cap
                # treats as NOT exclusive.
                event_exclusive = event.get("mutually_exclusive")
                if close_dt is not None:
                    # Written once per ticker (insert-only), before ANY book's cap check needs
                    # it — a position can only open below in this same iteration, so the row
                    # always exists by the time a later cycle's candidate joins against it.
                    repo.ensure_mmsell_settlement_meta(
                        session, market_ticker=ticker, event_ticker=event_ticker,
                        series_ticker=series, close_time=close_dt)
                if s.mmsell_prefilter_enabled:
                    # Computed ONLY when the pre-filter is armed, so the default path is
                    # byte-identical to before this existed. The union matters: the orderbook
                    # fetch below is shared, so the skip decision must satisfy every book that
                    # reaches this market, not whichever one happens to be first.
                    interested = [
                        b for b in books
                        if rank < (b.get("scanmax") or s.mmsell_top_events)
                        and b["htcmin"] <= htc <= b["htcmax"]
                        and self._book_admits_series(b, series)
                        and self._book_admits_ticker(b, ticker)
                    ]
                    if self._prefilter_skips(s, market, series, interested):
                        summ.skipped_prefilter += 1
                        continue

                for book in books:
                    tag = book["tag"]
                    # Rank gate: a book only sees events inside its own scan depth. Without a
                    # `scanmax` that depth is the global cap, so every existing book's candidate
                    # stream is byte-identical to what it was before deep scanning existed.
                    if rank >= (book.get("scanmax") or s.mmsell_top_events):
                        continue
                    if not (book["htcmin"] <= htc <= book["htcmax"]):
                        continue
                    if not self._book_admits_series(book, series):
                        continue  # per-variant series skip/allow filter
                    if not self._book_admits_ticker(book, ticker):
                        continue  # queue-position arm book: this ticker is the other arm's
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
                        # Inline-quote parity: score the event page's own quote against the
                        # orderbook we just fetched, for THIS market, once. Free — no extra
                        # call — and it is the only place ground truth exists, since a market
                        # we never fetch has no orderbook to compare against. Fail-soft: this
                        # is a diagnostic and must never be what stops an entry scan.
                        if summ.quote_parity is not None:
                            try:
                                summ.quote_parity.observe(
                                    ob_bid=metrics.best_yes_bid,
                                    ob_ask=metrics.best_yes_ask,
                                    inline_bid=market_price_cents(market, "yes_bid"),
                                    inline_ask=market_price_cents(market, "yes_ask"),
                                    # Scores the same market into a second, in-play-EXCLUDED
                                    # decision table, which is the direct shadow test of the
                                    # proposed distrust rule. None (unclassified) is scored
                                    # into the blended table only — counting an unknown as
                                    # "safe to pre-filter" would flatter the rule precisely
                                    # where we know least.
                                    in_play=(classify(series)[1] == IN_PLAY),
                                    # Recorded only for large-disagreement outliers, and only up
                                    # to a per-cycle cap. These are the attributes a stale-quote
                                    # CLASS could hide in — one bad series, thin books, markets
                                    # near expiry — which is what decides whether the pre-filter
                                    # can exclude them instead of eating a ~1% miss rate.
                                    context={
                                        "ticker": ticker, "series": series,
                                        "vol": metrics.volume, "oi": metrics.open_interest,
                                        "htc": round(htc, 2) if htc is not None else None,
                                        "spread": metrics.spread,
                                        "d_bid": getattr(metrics, "depth_at_best_bid", None),
                                        "d_ask_sz": getattr(metrics, "depth_at_best_ask", None),
                                    },
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "mmsell quote-parity observe failed (cycle unaffected)")
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
                        # Caps the pair to ONE leg per side per event — see _strangle_leg_taken.
                        leg_side = "yes" if mirror_leg else "no"
                        if self._strangle_leg_taken(session, tag, event_ticker, leg_side):
                            summ.skipped_strangle_paired += 1
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
                                    session, ticker, metrics, series=series, hours_to_close=htc,
                                    hours_to_expiration=hte, market=market)
                                captured += 1
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "mmsell: candidate tick capture failed",
                                    extra={"extra_fields": {"ticker": ticker, "error": str(exc)}})

                    is_twin = bool(book.get("twin_of"))

                    if repo.get_open_paper_position(session, ticker, tag) is not None:
                        summ.already_open += 1
                        self._note(recorder, ticker, tag, twin_codes.SKIP_ALREADY_OPEN)
                        # Paper holding it must not also lock LIVE out of the ticker: paper's
                        # position is an assumption, live's fill is a fact. See _maybe_retry_live.
                        if not is_twin:
                            self._maybe_retry_live(session, tag=tag, event=event, ticker=ticker,
                                                   metrics=metrics, summ=summ, recorder=recorder,
                                                   book=book)
                        continue
                    # A twin is capped like its LIVE parent (not like paper's much larger book) —
                    # the cap shapes which candidates each side ever sees, so it has to match.
                    cap = (self.twin_harness.max_open_positions(s.mmsell_live_max_open_positions)
                           if is_twin else s.mmsell_max_open_positions)
                    if open_count[tag] >= cap:
                        summ.capped += 1
                        self._note(recorder, ticker, tag, twin_codes.SKIP_CAP)
                        continue

                    if self._settlement_cap_blocks(session, s, book_cap=cap, tag=tag,
                                                   ticker=ticker, close_dt=close_dt,
                                                   series=series, event_ticker=event_ticker,
                                                   mutually_exclusive=event_exclusive,
                                                   summ=summ, recorder=recorder):
                        continue

                    if self._correlation_cap_blocks(session, book, tag=tag, ticker=ticker,
                                                    series=series, event_ticker=event_ticker,
                                                    summ=summ, recorder=recorder):
                        continue

                    if is_twin:
                        # The twin prices and sizes exactly as the live executor would, from the
                        # shared live/sizing helpers — the ONLY thing it does differently from
                        # live is assume the resting order fills.
                        if metrics.spread is not None \
                                and metrics.spread > s.mmsell_live_max_spread_cents:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_SPREAD)
                            continue
                        # Both price AND size come from the shared helper the live executor
                        # uses. Inlining this arithmetic is how the twin silently drifts: it
                        # previously re-derived the offset from the GLOBAL a/b config (ignoring
                        # the arm book's own `abarm`) and sized off the global max_order_size
                        # (2) while its live counterpart sized off the book's `size` (1) — so
                        # mmsell10b_pt2 booked 2 contracts per position against live's 1 and
                        # its dollar figures ran ~2x. A twin that sizes differently from live
                        # is not a twin.
                        price, qty = self._live_price_and_size(session, ticker, None, metrics,
                                                               book)
                        if price is None:
                            self._note(recorder, ticker, tag, twin_codes.SKIP_ILLIQUID)
                            continue
                        if not qty or qty <= 0:
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
                    # Both legs REST (post_only) — the ordinary leg buys NO at the no-bid, the
                    # strangle's mirror leg buys YES at the yes-bid. Neither crosses, so both
                    # are billed maker.
                    fee = kalshi_fee(price, qty, s.paper_fees_enabled, maker=True)
                    sub = market.get("yes_sub_title") or market.get("subtitle") or ""
                    # fill_assumption is String(64); the repo layer also clamps, but keep the
                    # subtitle short and the prices first so truncation only costs subtitle chars.
                    sold, at = ("no", 100 - price) if mirror_leg else ("yes", 100 - price)
                    assumption = (
                        f"[{tag}] sell {sold} '{sub[:24]}' @ {at}c "
                        f"({side}@{price}c mid{metrics.midpoint:.0f}c)"
                    )[:64]
                    try:
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
                    except LineageBlocked:
                        # Belt to the per-cycle pre-check's braces. The refusal fires
                        # before any row is added, so the session is still clean and the
                        # books that already entered this cycle keep their entries — the
                        # whole point of XOS-000011. Drop the book for the rest of the
                        # cycle rather than re-asking on every remaining market.
                        logger.warning(
                            "mmsell: entry refused — tag lost its deployment arm mid-cycle",
                            extra={"extra_fields": {"tag": tag, "ticker": ticker}})
                        summ.blocked_books.append(tag)
                        books = [b for b in books if b["tag"] != tag]
                        continue
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
                                arm_offset=self._book_arm_offset(book, ticker),
                                max_contracts=book.get("size"),
                            )
                            if recorder is not None:
                                # Record what live ACTUALLY did (placed, or the specific gate that
                                # stopped it) so the twin/live gap is attributable, not guessed.
                                live_px, live_qty = self._live_price_and_size(
                                    session, ticker, price, metrics, book)
                                recorder.note_live(
                                    ticker, tag, outcome or twin_codes.LIVE_NOT_ATTEMPTED,
                                    live_px, live_qty)
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

        self._record_scan_telemetry(session, summ)
        return summ
