"""Human-readable tags for a market ticker: category, league/subject, and market type.

mmsell trades essentially any liquid Kalshi event — not just sports. A live check
against 727 real tickers this book has traded found the `markets` table (populated
only by the unrelated scanner) has **zero rows** for any of them, so there is no
stored title/category to join against for this universe; a raw ticker like
`KXWCGOAL-...` or `KXMLBHRDERBY-...` is the only thing on hand.

So this module classifies from the ticker's **series prefix** — the segment before
the first `-`, which Kalshi assigns per market series and which this repo already
treats as the unit of meaning (`live_paper_parity_events.series`,
`mmsell/tracker.py`'s per-variant `skip=`/`only=` filters). The rules below are not
guessed: every category tag they can produce is one this repo has already
documented rather than inferred fresh — `docs/MMSELL_VARIANTS_THESIS.md` and the
`mmsell_variants` comment block in `kalshi_bot/config.py` confirm `WC` = World Cup
soccer (including its "exotic in-play" entertainment/broadcast props), and
`ATP`/`WTA`/`ITF` (tennis), `T20`/`ODI` (cricket), `BTCD`/`ETH` (crypto) are
Kalshi's own standard series codes used the same way throughout this codebase.

A prefix that matches nothing here is labelled **Other** with the raw prefix shown
verbatim — never guessed at. `KXAAAGASM`, `KXTRUMPSAYMONTH`, `KXRANKLISTSONGSPOTUSA`
and similar one-off series observed in production are real examples of this: this
module does not know what they are, and says so rather than inventing a label.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import select

from .. import models as m

OTHER = "Other"


@dataclass(frozen=True)
class MarketTag:
    ticker: str
    series: str                    # the raw prefix, e.g. "KXMLBHRDERBY" — always present
    category: str                  # e.g. "Sports", "Crypto", "Other" — always present
    subject: str | None            # e.g. "MLB", "World Cup (soccer)", "BTC"
    market_type: str | None        # e.g. "Home run derby", "Spread", "Total"
    source: str                    # "kalshi" (real title/category) or "inferred"
    title: str | None = None       # real Kalshi title, only when source == "kalshi"

    @property
    def label(self) -> str:
        """One short readable string for a table cell: "MLB · Home run derby"."""
        parts = [p for p in (self.subject, self.market_type) if p]
        return " · ".join(parts) if parts else self.series

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "series": self.series, "category": self.category,
            "subject": self.subject, "market_type": self.market_type,
            "label": self.label, "source": self.source, "title": self.title,
        }


# ---------------------------------------------------------------------------
# Series-prefix -> (category, subject) rules.
#
# Ordered, first (predicate) match wins. Each predicate matches on the prefix
# stripped of the "KX" namespace all Kalshi series share. Every subject here is
# backed by this repo's own documentation (see module docstring) — nothing is a
# fresh guess at what an unfamiliar code means.
# ---------------------------------------------------------------------------


def _starts(*tokens: str):
    return lambda p: any(p.startswith(t) for t in tokens)


def _contains(*tokens: str):
    return lambda p: any(t in p for t in tokens)


_RULES: tuple = (
    # --- Crypto ---
    (_starts("BTCD", "BTCMAXMON", "BTC"), "Crypto", "BTC"),
    (_starts("ETHD", "ETH"), "Crypto", "ETH"),
    # --- Commodities ---
    (_starts("WTI", "WTIW"), "Commodities", "Oil (WTI)"),
    (_starts("BRENT", "BRENTW"), "Commodities", "Oil (Brent)"),
    # --- Weather (mmsell itself skips these, but the module stays general) ---
    (_starts("HIGH", "LOW"), "Weather", None),
    # --- Soccer ---
    (_starts("WC"), "Sports", "World Cup (soccer)"),
    (_starts("MLS"), "Sports", "MLS (soccer)"),
    (_starts("LIGAMX"), "Sports", "Liga MX (soccer)"),
    (_starts("BRASILEIRO"), "Sports", "Brasileirao (soccer)"),
    (_starts("ECULP"), "Sports", "Ecuador LigaPro (soccer)"),
    # --- Baseball ---
    (_starts("MLB"), "Sports", "MLB (baseball)"),
    (_starts("NPB"), "Sports", "NPB (Japanese baseball)"),
    # --- Basketball ---
    (_starts("WNBA"), "Sports", "WNBA (basketball)"),
    (_starts("NBA"), "Sports", "NBA (basketball)"),
    # --- Tennis (ATP before generic to catch ATPCHALLENGER) ---
    (_starts("ATP"), "Sports", "ATP (tennis)"),
    (_starts("WTA"), "Sports", "WTA (tennis)"),
    (_starts("ITF"), "Sports", "ITF (tennis)"),
    # --- Cricket ---
    (_starts("T20"), "Sports", "T20 (cricket)"),
    (_starts("ODI"), "Sports", "ODI (cricket)"),
    (_starts("TEST"), "Sports", "Test match (cricket)"),
    # --- Golf ---
    (_starts("PGA"), "Sports", "PGA (golf)"),
    (_starts("LIV"), "Sports", "LIV Tour (golf)"),
    # --- MMA ---
    (_starts("UFC"), "Sports", "UFC (MMA)"),
)

# Market-type suffix (what's left of the prefix after the subject token is
# stripped) -> a readable label. Matched by substring on the remainder, longest
# token first so e.g. "HRDERBY500" doesn't fall through to "HR".
_TYPE_TOKENS: tuple[tuple[str, str], ...] = (
    ("HRDERBYDISTANCE", "Home run derby distance"),
    ("HRDERBY500", "Home run derby (500ft+)"),
    ("HRDERBY", "Home run derby"),
    ("TEAMTOTAL", "Team total"),
    ("1HSCORE", "First-half score"),
    ("TCORNERS", "Total corners"),
    ("SPREAD", "Spread"),
    ("TOTAL", "Total"),
    ("SCORE", "Score"),
    ("GOAL", "Goal"),
    ("ATTEND", "Attendance"),
    ("MENTION", "Broadcast mention"),
    ("AWARD", "Award"),
    ("FIRSTSONG", "Opening ceremony song"),
    ("MATCH", "Match"),
    ("GAME", "Game"),
    ("MOV", "Method of victory"),
    ("SOA", "Shots on target"),
    ("TOP5", "Top-5 finish"),
    ("TOP10", "Top-10 finish"),
    ("TOP20", "Top-20 finish"),
    ("HR", "Home run"),
)


def _classify_prefix(prefix: str) -> tuple[str, str | None, str | None]:
    """(category, subject, market_type) for one series prefix, e.g. 'KXMLBHRDERBY'."""
    body = prefix[2:] if prefix.startswith("KX") else prefix
    for predicate, category, subject in _RULES:
        if predicate(body):
            remainder = body
            # Strip the subject's own league/sport token so the type search below
            # can't match inside it (e.g. "MLB" itself never contains a type
            # token, but stripping generically avoids relying on that by luck).
            # WNBA before NBA matters: NBA is a substring-prefix of WNBA is false
            # (differs at char 0), so order is actually irrelevant here, but kept
            # for readability alongside the _RULES ordering above.
            for tok in ("WNBA", "MLB", "NBA", "ATP", "WTA", "ITF", "T20", "ODI",
                       "TEST", "PGA", "LIV", "UFC", "WC", "MLS", "LIGAMX", "NPB"):
                if remainder.startswith(tok):
                    remainder = remainder[len(tok):]
                    break
            market_type = None
            for token, label in _TYPE_TOKENS:
                if token in remainder:
                    market_type = label
                    break
            # Avoid a redundant "... (cricket) · Match" when the subject already
            # says "match" (Test match, and Match/Game are otherwise fine paired
            # with a real league name like "MLB (baseball) · Game").
            if market_type in ("Match", "Game") and subject and "match" in subject.lower():
                market_type = None
            return category, subject, market_type
    return OTHER, None, None


@lru_cache(maxsize=1024)
def _classify_cached(series: str) -> tuple[str, str | None, str | None]:
    return _classify_prefix(series)


def _series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0]


def classify(ticker: str) -> MarketTag:
    """Classify one ticker with no database access — used where a batch lookup
    already ran, or in tests."""
    series = _series_of(ticker)
    category, subject, market_type = _classify_cached(series)
    return MarketTag(ticker=ticker, series=series, category=category, subject=subject,
                     market_type=market_type, source="inferred")


def classify_many(session, tickers) -> dict[str, MarketTag]:
    """Batch classification for a page render. Prefers a real Kalshi title/category
    from the `markets` table when the scanner happens to have covered the same
    ticker (rare for mmsell's universe today, but free to check and correct if that
    ever changes); falls back to the prefix rules above."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    real: dict[str, m.Market] = {}
    if session is not None:
        for row in session.scalars(select(m.Market).where(m.Market.ticker.in_(tickers))):
            if row.title or row.category:
                real[row.ticker] = row
    out: dict[str, MarketTag] = {}
    for ticker in tickers:
        row = real.get(ticker)
        if row is not None:
            out[ticker] = MarketTag(
                ticker=ticker, series=_series_of(ticker), category=row.category or OTHER,
                subject=None, market_type=None, source="kalshi", title=row.title,
            )
        else:
            out[ticker] = classify(ticker)
    return out
