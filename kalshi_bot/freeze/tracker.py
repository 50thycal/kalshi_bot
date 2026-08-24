"""FREEZE book tracker (paper) — exchange-closure pin on Kalshi commodity-hub grain/soft markets.

Thesis: `docs/FREEZE_THESIS.md`. Kalshi's commodity hub trades 24/7 but CBOT grains and ICE softs
do not. When a contract's remaining window falls entirely inside a source-dark stretch, the
outcome is mechanically decided while launch-era retail keeps quoting the decided side several
cents from certainty. `scripts/kalshi_freeze_study.py` cleared all five pre-registered gates on
2026-08-12 (pooled +16.10¢/ct on 39,429 post-pin trades) and its decision rule says: promote to a
paper book. This is that book.

## The one thing the backtest could do that this book cannot

The study knew each market's settled `result`, so it could measure "buy the side that won". A live
book has no such column. It must infer the decided side from what it can see at decision time, and
the honest inference is **the market's own favorite**: if the source is frozen and the outcome is
locked, the side the book is pricing above 50¢ is overwhelmingly the side that will settle YES.

That substitution is exactly why this book is worth running rather than trusting the backtest.
A favorite-buy is the shape of `tfav`, which died (-3.6¢/trade at n=210). The claim here is *not*
"favorites are underpriced" — it is "favorites are underpriced **specifically while the source is
dark**, because the residual uncertainty retail is pricing has already been resolved". So the book
ships with its own control:

  * `freeze1`  dark window, discount >= `freeze_min_discount_cents`   <- the thesis
  * `freeze2`  dark window, discount >= 8¢ (deep-discount slice)      <- is more discount better?
  * `freeze3`  **OPEN window**, same discount bar                     <- THE CONTROL
  * `freeze4`  dark window, entry price capped at `maxprice`          <- avoid the ~certain end

`freeze3` is the load-bearing variant. It takes the identical trade at a moment when the source
can still print, so it is a plain favorite-buy with genuine uncertainty left. If the freeze
mechanism is real, `freeze1` beats `freeze3` by a wide margin. If `freeze1` ~= `freeze3`, we have
rediscovered tfav on a new venue and the family closes for good — a verdict worth the same forward
test either way, and one no amount of additional backtesting can produce.

## Forward-testing is also the artifact check

The study's headline (+16.10¢/ct) sits suspiciously close to the +15.82¢ that a v1 lookahead bug
manufactured before it was caught (`docs/RESEARCH_JOURNAL.md`, 2026-07-11), and 100% of it comes
from a single commodity (corn) that went from 0 to 233 settled markets inside one week. Both facts
are consistent with a real, newly-listed market — and equally consistent with a second measurement
artifact. A forward paper test cannot have lookahead by construction, so this book resolves that
question in a way re-reading the backtest never will. **Read the paper numbers, not the backtest,
before any live capital.**

## Mechanics

TAKER, single leg, hold to settlement (the shared paper engine settles it; `freeze` is in the
engine's no-timeout set alongside theta/tfav/pin15/mmsell). Buys the favorite at its ask, sized to
resting depth and the per-book order cap. No exits — matching the proven live path and the
thesis's own promotion spec.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from .. import repository as repo
from ..config import Settings
from ..kalshi.errors import AuthError
from ..obs.series_fetch import SeriesFetchResult, fetch_markets_by_series
from ..paper.engine import kalshi_fee
from ..scanner.metrics import compute_metrics
from .calendar import FREEZE_GROUPS, dark_window_start, is_pinned

logger = logging.getLogger(__name__)

# Title/ticker -> (commodity, group). Kept byte-identical in intent to the study's table so the
# backtest population and the live population are the same universe. Order matters (first wins).
COMMODITY_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(soybean|soy)\b|KX(SOY|SOYBEAN)", re.I), "soybeans", "grain"),
    (re.compile(r"\bcorn\b|KXCORN", re.I), "corn", "grain"),
    (re.compile(r"\bwheat\b|KXWHEAT", re.I), "wheat", "grain"),
    (re.compile(r"\bcoffee\b|KXCOFFEE", re.I), "coffee", "soft"),
    (re.compile(r"\bsugar\b|KXSUGAR", re.I), "sugar", "soft"),
    (re.compile(r"\bcocoa\b|KXCOCOA", re.I), "cocoa", "soft"),
    (re.compile(r"\bcotton\b|KXCOTTON", re.I), "cotton", "soft"),
    (re.compile(r"\b(gold|xau)\b|KXGOLD", re.I), "gold", "metal"),
    (re.compile(r"\b(silver|xag)\b|KXSILVER", re.I), "silver", "metal"),
    (re.compile(r"\bcopper\b|KXCOPPER", re.I), "copper", "metal"),
    (re.compile(r"\b(platinum|palladium)\b|KX(PLATINUM|PALLADIUM)", re.I), "pgm", "metal"),
    (re.compile(r"\bnickel\b|KXNICKEL", re.I), "nickel", "metal"),
    (re.compile(r"\blithium\b|KXLITHIUM", re.I), "lithium", "metal"),
    (re.compile(r"\b(wti|crude|brent|oil)\b|KX(WTI|CRUDE|BRENT|OIL)", re.I), "oil", "energy"),
    (re.compile(r"\b(natural gas|natgas)\b|\bng\b|KX(NATGAS|NG)\b", re.I), "natgas", "energy"),
    (re.compile(r"\b(diesel|heating oil|gasoline|rbob)\b|KX(DIESEL|GASOLINE|RBOB|HEATOIL)",
                re.I), "refined", "energy"),
]


def classify(text: str) -> tuple[str, str] | None:
    """(commodity, group) for a market title/ticker, or None if it is not a tracked commodity."""
    for rx, commodity, group in COMMODITY_RULES:
        if rx.search(text or ""):
            return commodity, group
    return None


@dataclass
class FreezeCycleSummary:
    events_seen: int = 0
    markets_seen: int = 0
    commodity: int = 0          # classified as a tracked commodity
    freeze_eligible: int = 0    # ... and in a grain/soft (freezable) group
    pinned: int = 0             # ... and mechanically decided right now
    candidates: int = 0         # ... priced, with a usable book: a real decision was made
    opened: int = 0
    already_open: int = 0
    capped: int = 0
    skipped_illiquid: int = 0
    skipped_discount: int = 0   # favorite too close to certainty (no edge left)
    skipped_price: int = 0      # outside a book's own price band
    per_book: dict[str, int] = field(default_factory=dict)
    per_commodity: dict[str, int] = field(default_factory=dict)
    # The series-addressed fetch's own report (XOS-000004): which configured
    # series returned nothing, so the cycle line can say so out loud.
    fetch: SeriesFetchResult = field(default_factory=SeriesFetchResult)


class FreezeTracker:
    """Scans open commodity-hub markets each cycle and takes the favorite on the ones whose
    settlement source is currently dark. Never raises into the caller's cycle."""

    def __init__(self, client, settings: Settings):
        self.client = client
        self.settings = settings

    def _books(self) -> list[dict]:
        return self.settings.freeze_book_list

    def _fetch_open_markets(self) -> SeriesFetchResult:
        """Open markets across the configured commodity series.

        Delegates to the shared series-addressed fetch, which paginates defensively (a failed
        page ends that series rather than the cycle) and — the part this book was missing —
        COUNTS what each configured series returned, so an HTTP 200 carrying an empty list is
        reported to the operator instead of passing for a healthy fetch (XOS-000004).
        """
        return fetch_markets_by_series(
            self.client,
            self.settings.freeze_series_list,
            book="freeze",
            max_pages=self.settings.freeze_max_pages,
            log=logger,
        )

    def run_once(self, session) -> FreezeCycleSummary:
        s = self.settings
        summ = FreezeCycleSummary()
        now = time.time()

        books = self._books()
        if not books:
            return summ

        open_counts = {b["tag"]: repo.count_open_paper_positions(session, b["tag"]) for b in books}
        open_tickers = {b["tag"]: repo.open_paper_position_tickers(session, b["tag"])
                        for b in books}

        summ.fetch = self._fetch_open_markets()
        for mkt in summ.fetch.markets:
            summ.markets_seen += 1
            ticker = mkt.get("ticker") or ""
            if not ticker:
                continue
            hit = classify(f"{ticker} {mkt.get('title') or ''} {mkt.get('subtitle') or ''}")
            if hit is None:
                continue
            commodity, group = hit
            summ.commodity += 1
            if group not in FREEZE_GROUPS:
                continue          # Pyth-continuous: never pinned (see calendar.py)
            summ.freeze_eligible += 1

            close_epoch = _close_epoch(mkt)
            if close_epoch is None:
                continue

            pinned = is_pinned(group, close_epoch, now)
            if pinned:
                summ.pinned += 1

            # One orderbook fetch per candidate, and only for markets some book might take.
            wants_pinned = any(b["dark"] for b in books)
            wants_open = any(not b["dark"] for b in books)
            if (pinned and not wants_pinned) or (not pinned and not wants_open):
                continue
            # An OPEN-window control entry is only comparable if the source is genuinely open —
            # not merely "not pinned because the window straddles a reopen".
            source_open = dark_window_start(group, now) is None
            if not pinned and not source_open:
                continue

            try:
                ob = self.client.get_orderbook(ticker, depth=s.orderbook_depth)
            except AuthError:
                raise
            except Exception:  # noqa: BLE001 — a book fetch must not kill the cycle
                summ.skipped_illiquid += 1
                continue
            metrics = compute_metrics(mkt, ob, top_n=s.orderbook_depth)

            # The favorite is whichever side the book prices above 50c. Buy it at its ask;
            # a buy-YES fills against resting NO depth and vice versa (scanner/metrics.py).
            yes_mid = metrics.midpoint
            if yes_mid is None:
                summ.skipped_illiquid += 1
                continue
            side = "yes" if yes_mid >= 50 else "no"
            price = metrics.best_yes_ask if side == "yes" else metrics.best_no_ask
            depth = (metrics.depth_at_best_ask if side == "yes"
                     else metrics.depth_at_best_bid) or 0
            if price is None or not (0 < price < 100):
                summ.skipped_illiquid += 1
                continue

            # A real candidate: priced, with usable depth, and about to be offered to each
            # book's own rules. Counting it is what separates "found nothing to look at" from
            # "looked at things and rejected them" in the cycle line (XOS-000004). This is an
            # observation only — no entry rule reads it.
            summ.candidates += 1

            # Discount to certainty: what the pin is theoretically worth if the outcome is locked.
            discount = 100 - price

            for book in books:
                tag = book["tag"]
                if book["dark"] != pinned:
                    continue
                if ticker in open_tickers[tag]:
                    summ.already_open += 1
                    continue
                if open_counts[tag] >= s.freeze_max_open_positions:
                    summ.capped += 1
                    continue
                if discount < book["mindisc"]:
                    summ.skipped_discount += 1
                    continue
                if book["maxprice"] is not None and price > book["maxprice"]:
                    summ.skipped_price += 1
                    continue
                qty = min(s.freeze_order_size, depth)
                if qty <= 0:
                    summ.skipped_illiquid += 1
                    continue

                fee = kalshi_fee(price, qty, s.paper_fees_enabled)
                repo.create_paper_trade(
                    session,
                    signal_id=None,
                    ticker=ticker,
                    strategy=tag,
                    side=side,
                    action="buy",
                    assumed_price=price,
                    quantity=qty,
                    fill_assumption=(
                        f"[{tag}] buy {side} @{price:.0f}c disc {discount:.0f}c "
                        f"{commodity}/{group} {'DARK' if pinned else 'OPEN'} "
                        f"htc {(close_epoch - now) / 3600.0:.1f}h"
                    ),
                    entry_fee=fee,
                    model_probability=None,
                    edge=float(discount),
                )
                repo.open_paper_position_for_trade(
                    session, ticker=ticker, strategy=tag, side=side,
                    quantity=qty, avg_price=price,
                )
                open_counts[tag] += 1
                open_tickers[tag].add(ticker)
                summ.opened += 1
                summ.per_book[tag] = summ.per_book.get(tag, 0) + 1
                summ.per_commodity[commodity] = summ.per_commodity.get(commodity, 0) + 1

        return summ


def _close_epoch(mkt: dict) -> float | None:
    """Market close time as epoch seconds, or None when absent/unparseable."""
    from datetime import datetime

    raw = mkt.get("close_time") or mkt.get("expected_expiration_time") or ""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
