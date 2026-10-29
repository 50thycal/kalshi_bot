"""Current best bids for a set of Kalshi markets, read from the public API: the scorecards' marks.

The scorecards value an open position at what it could be SOLD for now — the held side's best
bid — the same convention the Railway overview uses (`livedash.overview._unrealized`). The
database has no price for most of these markets (the incentive snapshots stop when a program
ends, the mmsell tape covers only markets a paper book holds), so the scripts ask Kalshi.

Stdlib only: the ops runner installs psycopg and nothing else for a `script` request. Public,
unauthenticated, read-only. A market Kalshi does not answer for is left out, and its position
is reported as unpriced, never as zero.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
BATCH = 100
_UA = "kalshi-bot-scorecard/1.0"


def _cents(v) -> int | None:
    try:
        c = round(float(v) * 100)
    except (TypeError, ValueError):
        return None
    return c


def bids_from_market(mk: dict) -> tuple[int | None, int | None]:
    """(yes bid, no bid) in cents from one v2 market object; None for an empty side.

    The NO bid is the complement of the YES ask when the object carries no NO quote (a NO bid
    at p IS a YES ask at 100 - p). A zero price means nobody is bidding."""
    yes = _cents(mk.get("yes_bid_dollars"))
    if yes is None and mk.get("yes_bid") is not None:
        yes = int(mk["yes_bid"])
    no = _cents(mk.get("no_bid_dollars"))
    if no is None and mk.get("no_bid") is not None:
        no = int(mk["no_bid"])
    if no is None:
        ask = _cents(mk.get("yes_ask_dollars"))
        if ask is None and mk.get("yes_ask") is not None:
            ask = int(mk["yes_ask"])
        no = None if ask is None else 100 - ask
    return (yes if yes else None), (no if no else None)


def _get(url: str):
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return None
    return None


def fetch_bids(tickers) -> dict[str, tuple[int | None, int | None]]:
    """{ticker: (yes bid, no bid)} for every market Kalshi answered for."""
    tickers = sorted({t for t in tickers if t})
    out: dict[str, tuple[int | None, int | None]] = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        q = urllib.parse.urlencode({"tickers": ",".join(chunk), "limit": len(chunk)})
        doc = _get(f"{KALSHI}/markets?{q}")
        for mk in (doc or {}).get("markets") or []:
            if mk.get("ticker"):
                out[mk["ticker"]] = bids_from_market(mk)
    return out
