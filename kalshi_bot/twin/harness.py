"""Live/paper parallel-run harness — a fresh paper book beside every live strategy.

## The problem this exists to solve

A paper book that has been running for months and a live book that started yesterday are not
comparable. They differ in sample size, market regime, position sizing (paper: 1-contract clips,
a 200-position cap, paper's own entry price), and in which markets each was even allowed to
touch. So when live under-performs paper — as the mmsell cohort did — the gap cannot be
attributed: is the paper edge a **mirage** (an assumption the simulator gets wrong), or is it a
real edge the live execution is losing to fills, gates or sizing?

The harness answers that by construction. For any strategy promoted to real money it runs a
**twin**: a brand-new paper book that

1. **starts at the same instant as the live run** (a fresh tag, zero prior history, and an epoch
   row that scopes BOTH sides of the comparison to the same window), and
2. **is parameterized to the LIVE knobs** — the live entry-price rule, the live dollar sizing,
   the live open-position cap, the live market-quality gates — not paper's own.

Which leaves exactly one difference between the twin and the live book: **the twin assumes its
resting order fills, and the live book has to actually get filled.** Every divergence between
them is therefore execution reality, and a twin that beats live by more than the measured fill
gap means one of our paper assumptions is wrong — a mirage, caught with a measurement instead of
an argument.

## What the twin deliberately does NOT copy

Market-selection gates ARE applied to the twin (band, hours-to-close, series filters, the live
spread sanity cap) because they define which markets the strategy trades. Account-state gates
(kill switch aside: balance, daily-loss trip, per-market exposure, in-flight dedup against a
foreign order) are NOT applied — they depend on the live account's momentary condition, and
applying them would make the twin a non-deterministic function of our own bankroll rather than a
clean counterfactual. Instead every one of them is *recorded* in the parity tape, so the report
can say "live traded 40 fewer candidates than its twin, 31 of them blocked by the open cap"
rather than leaving the gap unexplained.

## Symmetric start/stop

Twin activity is gated cycle-by-cycle on the live book actually being armed (live mode, both
master switches, the tag allowlisted). If live is paused, the twin pauses with it — otherwise the
twin quietly accumulates trades over a window the live book never traded, and the one-to-one
property is lost.

Wiring a new book into the harness takes three hook points; see docs/LIVE_PAPER_TWIN.md §5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .. import repository as repo
from ..paper.engine import kalshi_fee

logger = logging.getLogger(__name__)

# Paper-side outcome codes (String(24) in live_paper_parity_events).
OPENED = "opened"
SKIP_ALREADY_OPEN = "skip_already_open"
SKIP_CAP = "skip_cap"
SKIP_SPREAD = "skip_spread"
SKIP_SIZE = "skip_size"
SKIP_ILLIQUID = "skip_illiquid"
SKIP_SETTLEMENT_CAP = "skip_settlement_cap"  # too many open positions already settle this date
SKIP_EVENT_CAP = "skip_event_cap"            # too many distinct events on a CORRELATED date
SKIP_EVENT_RUNG_CAP = "skip_event_rung_cap"  # too many rungs open on ONE non-exclusive event
SKIP_CONTEST_CAP = "skip_contest_cap"        # too many positions on ONE contest, across series
SKIP_LIVE_TIER = "skip_live_tier"            # LIVE only: series below the review-tier bar
SKIP_LIVE_PAUSED = "skip_live_paused"        # LIVE only: real money PAUSED on this series

# Live-side outcome codes (String(32)); the executor returns these from its mirror entry.
LIVE_PLACED = "placed"
LIVE_NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True)
class TwinSpec:
    """One configured parallel pair: the live tag and its fresh paper twin tag."""

    live_tag: str
    twin_tag: str


@dataclass
class _MarketDecisions:
    """Per-candidate scratch state, flushed to one parity row per twin pair."""

    parent: tuple[str, int | None, int | None] | None = None
    twin: tuple[str, int | None, int | None] | None = None
    live: tuple[str, int | None, int | None] | None = None


class TwinHarness:
    """Registry + entry writer for the configured live/paper twin books.

    Construct once per process (cheap; holds no session). Trackers ask it which twin books to
    run, whether a twin is currently armed, and to write twin paper entries."""

    def __init__(self, settings):
        self.settings = settings
        self.specs: list[TwinSpec] = [
            TwinSpec(live_tag=lt, twin_tag=tt) for lt, tt in settings.live_paper_twin_pairs
        ]
        self._twin_by_live = {s.live_tag: s.twin_tag for s in self.specs}
        self._live_by_twin = {s.twin_tag: s.live_tag for s in self.specs}
        self._drift_logged: set[str] = set()
        self._epoch_logged: set[str] = set()

    # --- registry ---

    @property
    def enabled(self) -> bool:
        return bool(self.specs)

    def twin_of(self, live_tag: str) -> str | None:
        return self._twin_by_live.get(live_tag)

    def live_tag_of(self, twin_tag: str) -> str | None:
        return self._live_by_twin.get(twin_tag)

    def is_twin(self, tag: str) -> bool:
        return tag in self._live_by_twin

    def active_for(self, live_tag: str) -> bool:
        """True when the live book is genuinely armed right now — live mode, both master
        switches, and the tag allowlisted. The twin starts and stops WITH live so both sides of
        the comparison cover the same window."""
        s = self.settings
        if not s.live_paper_twin_enabled or live_tag not in self._twin_by_live:
            return False
        if s.bot_mode != "live" or not s.live_enabled or s.kill_switch:
            return False
        return any(live_tag.startswith(p) for p in s.live_strategy_list)

    def active_specs(self) -> list[TwinSpec]:
        return [s for s in self.specs if self.active_for(s.live_tag)]

    def max_open_positions(self, live_cap: int) -> int:
        """The twin's open cap: the live book's own cap by default (the faithful choice), or an
        explicit override when a deployment wants to bound the extra paper bookkeeping."""
        override = int(self.settings.live_paper_twin_max_open_positions or 0)
        return override if override > 0 else int(live_cap)

    # --- epoch ---

    def ensure_epoch(self, session, spec: TwinSpec, params: dict) -> bool:
        """Get-or-create the twin's epoch row. Returns False when the parameter snapshot has
        drifted from what the epoch was opened with — the comparison is no longer apples-to-apples
        and the honest fix is a NEW twin tag, so the drift is logged loudly (once) and reported by
        scripts/live_paper_parity.py, but the twin keeps running rather than silently stopping."""
        row, drifted = repo.sync_twin_epoch(
            session, twin_tag=spec.twin_tag, live_tag=spec.live_tag, params=params
        )
        if spec.twin_tag not in self._epoch_logged:
            self._epoch_logged.add(spec.twin_tag)
            logger.info("live/paper twin epoch", extra={"extra_fields": {
                "twin_tag": spec.twin_tag, "live_tag": spec.live_tag,
                "started_at": row.started_at.isoformat() if row.started_at else None}})
        if drifted and spec.twin_tag not in self._drift_logged:
            self._drift_logged.add(spec.twin_tag)
            logger.warning(
                "live/paper twin PARAM DRIFT — parity is no longer one-to-one; start a new twin tag",
                extra={"extra_fields": {"twin_tag": spec.twin_tag, "live_tag": spec.live_tag,
                                        "epoch_params": row.params_json, "now_params": params}})
            try:
                repo.log_system_event(
                    session, level="WARNING", component="twin",
                    message=f"twin {spec.twin_tag} params drifted from epoch snapshot",
                    raw={"twin_tag": spec.twin_tag, "live_tag": spec.live_tag,
                         "epoch_params": row.params_json, "now_params": params})
            except Exception:  # noqa: BLE001 — a diagnostic must never break a cycle
                logger.exception("twin drift event log failed")
        return not drifted

    # --- entries ---

    def open_twin_entry(
        self, session, *, twin_tag: str, ticker: str, side: str, price: int, quantity: int,
        note: str = "", model_probability: float | None = None, edge: float | None = None,
    ) -> None:
        """Write the twin's paper entry: a trade row plus its open position, sized and priced the
        way the live book would have. Mirrors the paper engine's own bookkeeping so the shared
        settle/mark path picks it up with no special-casing.

        `model_probability`/`edge` are optional pass-throughs for books (theta) whose own paper
        entries carry a real model probability/edge, so the twin's record is directly comparable
        to its parent's, not just to live's fills. mmsell has no model probability of its own and
        leaves these at the default (None/0.0), matching what its own paper entries store."""
        # A twin shadows a LIVE book, and every live book with a twin enters post_only — that is
        # what the twin is for (measuring the fills a resting order misses). So the twin's entry
        # is billed maker, exactly like the live order it mirrors. Charging it taker was the
        # single largest term in the parity report's ACCOUNTING GAP.
        fee = kalshi_fee(price, quantity, self.settings.paper_fees_enabled, maker=True)
        assumption = f"[{twin_tag}] {note}"[:64] if note else f"[{twin_tag}] twin of live"[:64]
        repo.create_paper_trade(
            session,
            signal_id=None,
            ticker=ticker,
            strategy=twin_tag,
            side=side,
            action="buy",
            assumed_price=price,
            quantity=quantity,
            fill_assumption=assumption,
            entry_fee=fee,
            model_probability=model_probability,
            edge=edge if edge is not None else 0.0,
        )
        repo.open_paper_position_for_trade(
            session, ticker=ticker, strategy=twin_tag, side=side,
            quantity=quantity, avg_price=price,
        )

    # --- parity tape ---

    def recorder(self) -> ParityRecorder:
        return ParityRecorder(self, max_events=int(self.settings.live_paper_twin_parity_max))


class ParityRecorder:
    """Accumulates each candidate market's three decisions and flushes one row per twin pair.

    Usage inside a tracker's per-market loop:

        rec.note_paper(ticker, tag, outcome, price, qty)   # for every book that saw the market
        rec.note_live(ticker, live_tag, outcome, price, qty)
        rec.flush(session, ticker, series=..., hours_to_close=..., metrics=...)

    Recording is bounded per cycle and fail-soft: the tape is a diagnostic, so an error here must
    never cost a trade."""

    def __init__(self, harness: TwinHarness, *, max_events: int):
        self.harness = harness
        self.max_events = max(0, int(max_events))
        self.written = 0
        self._by_market: dict[str, dict[str, _MarketDecisions]] = {}
        self._enabled = bool(harness.settings.live_paper_twin_parity_events) and harness.enabled

    def _slot(self, ticker: str, twin_tag: str) -> _MarketDecisions:
        return self._by_market.setdefault(ticker, {}).setdefault(twin_tag, _MarketDecisions())

    def note_paper(
        self, ticker: str, tag: str, outcome: str, price: int | None = None,
        quantity: int | None = None,
    ) -> None:
        """Record a paper book's decision. Routed to the twin slot if `tag` is a twin, or to the
        incumbent (parent) slot if `tag` is a live tag that has a twin. Other books are ignored."""
        if not self._enabled:
            return
        twin_tag = self.harness.twin_of(tag)
        if twin_tag is not None:
            self._slot(ticker, twin_tag).parent = (outcome, price, quantity)
            return
        if self.harness.is_twin(tag):
            self._slot(ticker, tag).twin = (outcome, price, quantity)

    def note_live(
        self, ticker: str, live_tag: str, outcome: str, price: int | None = None,
        quantity: int | None = None,
    ) -> None:
        if not self._enabled:
            return
        twin_tag = self.harness.twin_of(live_tag)
        if twin_tag is not None:
            self._slot(ticker, twin_tag).live = (outcome, price, quantity)

    def flush(
        self, session, ticker: str, *, series: str | None = None,
        hours_to_close: float | None = None, metrics=None,
    ) -> None:
        """Write (at most one row per twin pair) and drop this market's scratch state."""
        pending = self._by_market.pop(ticker, None)
        if not pending or not self._enabled:
            return
        for twin_tag, dec in pending.items():
            if self.written >= self.max_events:
                return
            live_tag = self.harness.live_tag_of(twin_tag)
            if live_tag is None:
                continue
            # A candidate nobody acted on carries no information; skip it.
            if dec.parent is None and dec.twin is None and dec.live is None:
                continue
            p_out, p_px, _p_qty = dec.parent or (None, None, None)
            t_out, t_px, t_qty = dec.twin or (None, None, None)
            l_out, l_px, l_qty = dec.live or (LIVE_NOT_ATTEMPTED, None, None)
            try:
                repo.insert_parity_event(
                    session, twin_tag=twin_tag, live_tag=live_tag, ticker=ticker, series=series,
                    hours_to_close=hours_to_close,
                    parent_outcome=p_out, parent_price=p_px,
                    twin_outcome=t_out, twin_price=t_px, twin_quantity=t_qty,
                    live_outcome=l_out, live_price=l_px, live_quantity=l_qty,
                    yes_mid=getattr(metrics, "midpoint", None),
                    no_bid=getattr(metrics, "best_no_bid", None),
                    no_ask=getattr(metrics, "best_no_ask", None),
                )
                self.written += 1
            except Exception:  # noqa: BLE001 — the tape is diagnostic; never break a cycle
                logger.exception("live/paper parity event write failed")

    def discard(self, ticker: str) -> None:
        """Drop a market's scratch state without writing (e.g. the orderbook fetch failed)."""
        self._by_market.pop(ticker, None)
