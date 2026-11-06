"""Read a Kalshi market's settlement without losing it to the historical cutoff.

THE PROBLEM. Kalshi splits market data into a live and a historical tier. Live `GET /markets`
and `GET /markets/{ticker}` stop returning a settled market once it is older than the cutoff
`GET /historical/cutoff` reports (`market_settled_ts`, about three months, advancing over time).
Those markets are served by `GET /historical/markets` instead. A research script that reads
`result` from the live tier alone does not error past the cutoff: it gets a 404 or an empty
list, and the market quietly drops out of the population. MOMO-H probe run 2 lost 29.6% of its
labels that way with zero fetch errors (docs/handoffs/HANDOFF-kalshi-historical-cutoff.md).

THE RULE. Live first; the historical tier only when live does not have the market (a 404 for
one market, an empty answer for a list); anything else — a 400, a 403, a 5xx that survives the
retries — propagates. Every answer says where it came from (`LIVE`, `HISTORICAL`, `NOT_FOUND`),
and `Tally` turns that into the one line every caller prints:

    labels: live N / historical N / not found N

so a market that could not be read is counted, never dropped in silence. No cutoff date is
hardcoded: the fallback is driven by what live does not return, and `cutoff()` only reports
the current value for the record.

Stdlib only, so it runs on the ops runner beside the scripts that import it
(`sys.path[0]` is `scripts/` when the runner executes `scripts/<name>.py`).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

LIVE = "live"
HISTORICAL = "historical"
NOT_FOUND = "not_found"
SOURCES = (LIVE, HISTORICAL, NOT_FOUND)

PAGE_LIMIT = 1000          # Kalshi's list maximum, live and historical
TICKER_BATCH = 100         # tickers per `?tickers=` call (the batch event_timing_study uses)
MAX_PAGES = 50             # one event or one ticker batch never needs more; a guard, not a cap
PAUSE_S = 0.06             # between list pages: ~15 requests/second against one host

#: `get(path, params) -> dict`. Raises `urllib.error.HTTPError` for an HTTP failure, so callers
#: (and tests) can tell a 404 from everything else.
Getter = Callable[[str, dict], dict]


def http_get(path: str, params: dict | None = None, *, timeout: float = 30.0,
             attempts: int = 5) -> dict:
    """GET `KALSHI + path` with the public, unauthenticated market-data headers.

    Retries 429/5xx and transport errors with backoff; any other HTTP status is raised at once
    (a 404 is an answer, not a transient)."""
    qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = f"{KALSHI}{path}" + (f"?{qs}" if qs else "")
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed host)
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < attempts - 1:
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
    return {}


def _is_404(exc: BaseException) -> bool:
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 404


def _quote(ticker: str) -> str:
    return urllib.parse.quote(ticker, safe="")


# --- one market -------------------------------------------------------------------------------

def market(ticker: str, get: Getter = http_get) -> tuple[dict | None, str]:
    """`(market, source)`: `/markets/{t}`, then `/historical/markets/{t}` on a 404 only."""
    for prefix, source in (("/markets/", LIVE), ("/historical/markets/", HISTORICAL)):
        try:
            data = get(prefix + _quote(ticker), {})
        except urllib.error.HTTPError as exc:
            if _is_404(exc):
                continue
            raise
        found = (data or {}).get("market")
        if found:
            return found, source
    return None, NOT_FOUND


# --- lists ------------------------------------------------------------------------------------

def _pages(path: str, params: dict, get: Getter) -> list[dict]:
    out: list[dict] = []
    params = dict(params)
    cursor = params.pop("cursor", "") or ""
    for _ in range(MAX_PAGES):
        page = get(path, {**params, "limit": PAGE_LIMIT, "cursor": cursor}) or {}
        out.extend(page.get("markets") or [])
        cursor = page.get("cursor") or ""
        if not cursor:
            break
        time.sleep(PAUSE_S)
    return out


def _of_event(markets: Iterable[dict], event_ticker: str) -> list[dict]:
    # Keep only markets that belong to the event. An endpoint that ignored the filter would
    # otherwise hand back unrelated markets and they would be graded as this event's.
    return [m for m in markets if (m.get("event_ticker") or "") == event_ticker
            or (m.get("ticker") or "").startswith(event_ticker + "-")]


def markets_by_ticker(tickers: Iterable[str], get: Getter = http_get) -> dict[str, tuple[dict | None, str]]:
    """`{ticker: (market, source)}` for every ticker asked for — none is left out.

    Batched `?tickers=` against live, then the historical list for what live did not return,
    then `/historical/markets/{t}` one at a time for anything a batch still missed."""
    todo = sorted({t for t in tickers if t})
    out: dict[str, tuple[dict | None, str]] = {}
    for path, source in (("/markets", LIVE), ("/historical/markets", HISTORICAL)):
        missing = [t for t in todo if t not in out]
        for i in range(0, len(missing), TICKER_BATCH):
            chunk = missing[i:i + TICKER_BATCH]
            want = set(chunk)
            for m in _pages(path, {"tickers": ",".join(chunk)}, get):
                tk = m.get("ticker")
                if tk in want and tk not in out:
                    out[tk] = (m, source)
            time.sleep(PAUSE_S)
    for t in todo:
        if t not in out:
            out[t] = market(t, get)
    return out


#: Whether `/historical/markets?event_ticker=` is honoured. None until the first call answers.
#: The Kalshi SDK documents `event_ticker` and `tickers` as mutually exclusive filters but not
#: whether the historical list accepts `event_ticker` at all, so the first answer is recorded
#: and reported (`historical_event_filter()`) rather than assumed.
_HIST_EVENT_FILTER: bool | None = None


def historical_event_filter() -> bool | None:
    return _HIST_EVENT_FILTER


def event_markets(event_ticker: str, tickers: Iterable[str] = (),
                  get: Getter = http_get) -> tuple[list[dict], str]:
    """`(markets, source)` for one event: live `?event_ticker=`, then the historical tier.

    An event settles as a unit, so it is wholly live or wholly historical. In the historical
    tier the `event_ticker` filter is tried first; if the API refuses it (HTTP 400), ignores it
    (answers with other events' markets) or has nothing for the event, and the caller named the
    `tickers` it needs, those are read by ticker instead. Any other HTTP error propagates."""
    global _HIST_EVENT_FILTER
    live = _of_event(_pages("/markets", {"event_ticker": event_ticker}, get), event_ticker)
    if live:
        return live, LIVE
    hist: list[dict] = []
    if _HIST_EVENT_FILTER is not False:
        try:
            first = get("/historical/markets",
                        {"event_ticker": event_ticker, "limit": PAGE_LIMIT, "cursor": ""}) or {}
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            _HIST_EVENT_FILTER = False
        else:
            page = first.get("markets") or []
            hist = _of_event(page, event_ticker)
            if len(hist) < len(page):
                # Answered, but with other events' markets: the filter is being ignored. Paging
                # on would walk the whole archive once per event; use the ticker path instead.
                _HIST_EVENT_FILTER, hist = False, []
            elif hist:
                _HIST_EVENT_FILTER = True
                if first.get("cursor"):
                    hist += _of_event(_pages("/historical/markets", {
                        "event_ticker": event_ticker, "cursor": first["cursor"]}, get),
                        event_ticker)
    wanted = sorted({t for t in tickers if t})
    if not hist and wanted:
        hist = [m for m, src in markets_by_ticker(wanted, get).values()
                if m is not None and src == HISTORICAL]
        hist = _of_event(hist, event_ticker)
    if hist:
        return hist, HISTORICAL
    return [], NOT_FOUND


# --- the record -------------------------------------------------------------------------------

def result_of(m: dict | None) -> str | None:
    """`"yes"`/`"no"` for a settled binary market, else None (open, voided, scalar, absent)."""
    res = str((m or {}).get("result") or "").lower()
    return res if res in ("yes", "no") else None


def cutoff(get: Getter = http_get) -> dict | None:
    """The current `/historical/cutoff` answer, for the record only — never a decision input."""
    try:
        return get("/historical/cutoff", {}) or None
    except Exception:  # noqa: BLE001 - reporting only; the fallback does not depend on it
        return None


class Tally:
    """Counts labels by where they came from. `line()` is what every caller prints."""

    def __init__(self) -> None:
        self.counts = {s: 0 for s in SOURCES}

    def add(self, source: str, n: int = 1) -> None:
        if source not in self.counts:
            raise ValueError(f"unknown label source {source!r}")
        self.counts[source] += n

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def not_found_share(self) -> float:
        return self.counts[NOT_FOUND] / self.total if self.total else 0.0

    def line(self) -> str:
        c = self.counts
        return (f"labels: live {c[LIVE]} / historical {c[HISTORICAL]} / "
                f"not found {c[NOT_FOUND]}")


def report(tally: Tally, get: Getter = http_get, out=None) -> None:
    """Print the source line, the historical-filter observation and the cutoff in force."""
    out = out or sys.stdout
    print(tally.line(), file=out)
    filt = historical_event_filter()
    if filt is not None:
        print(f"historical event_ticker filter honoured: {filt}", file=out)
    cut = cutoff(get)
    if cut:
        print("historical cutoff: " + json.dumps(cut, sort_keys=True), file=out)
