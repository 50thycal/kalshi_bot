"""mmsell live canary — the crypto vs non-crypto MONITORING breakdown.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
DIAGNOSTIC ONLY. Nothing here is a pre-registered stopping criterion, and it must
not become one after the fact. The canary's keep/stop contract is the registered
`live_canary_keep` gate; a slice that looked bad here would be a reason to LOOK,
never on its own a reason to stop. Excluding crypto from the arm would be a
different market universe — a different Version — and could not inherit the
evidence this arm was promoted on.

Why the slice is worth watching anyway. On the previous live generation:

    Lmmsell10 overall      273 settled, 93.0% win, +$6.63  (~ +1.23c/contract)
    Lmmsell10 non-crypto   261 settled,                +$7.97
    Lmmsell10 crypto        12 settled,                -$1.34

Twelve settlements is not a result. It is a small negative signal in a slice with
a documented mechanism behind it (`docs/MMSELL_CRYPTO_STUDY.md`: the final-hour
crypto cheap tail is where theta died), which is exactly the shape that deserves
monitoring rather than either a conclusion or a shrug. The catastrophic loss in
this family — Lmmsell8, -$19.24 over 22 crypto settlements at a 45.5% win rate —
came from a DIFFERENT strategy specification, not from this arm and not from the
shared execution engine.

HOW "CRYPTO" IS DECIDED, AND WHY NOT BY SUBSTRING
-------------------------------------------------
By an explicit list of whole series tickers, matched exactly. Not by substring,
and not by prefix.

XOS-000009 is the open issue for what substring matching does here: the
production skip list `BTC+ETH+SOL+DOGE+XRP+CRYPTO` silently drops non-crypto
markets, because series like `KXHEGSETHANNOUNCEOUT` contain `ETH`. A monitoring
slice built the same way would quietly move markets between its own buckets and
then be read as evidence about crypto.

Anything not on the list lands in `unclassified` and is REPORTED as such rather
than folded into `non_crypto`. A slice report whose residual bucket is growing is
telling you the list needs extending — which a silent default would hide.

Read-only, self-contained (stdlib + psycopg):

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_canary_slices.py
    # or:  {"type": "script", "name": "mmsell_canary_slices"}
    # args: --live Cmmsell10 --twin Cmmsell10_pt3  (defaults to the canary pair)
"""

from __future__ import annotations

import argparse
import os
import sys

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=60000 "
    "-c idle_in_transaction_session_timeout=60000"
)

#: Whole Kalshi series tickers whose underlying is a crypto asset, matched
#: EXACTLY. Extend it deliberately; `unclassified` in the report is the signal
#: that it needs extending. Drawn from the settlement taxonomy
#: (`kalshi_bot/mmsell/market_types.py`) plus the crypto series the bot's own
#: collectors track.
CRYPTO_SERIES: frozenset[str] = frozenset({
    "KXBTC", "KXBTCD", "KXBTCMAXMON", "KXBTCMINMON", "KXBTCRANGE",
    "KXETH", "KXETHD", "KXETHMAXMON", "KXETHMINMON", "KXETHRANGE",
    "KXSOL", "KXSOLD", "KXDOGE", "KXDOGED", "KXXRP", "KXXRPD",
    "KXBNB", "KXBNBD", "KXBNBMINMON", "KXBNBMAXMON",
    "KXADA", "KXADAD", "KXLTC", "KXLTCD", "KXAVAX", "KXLINK",
})

SLICES = ("crypto", "non_crypto", "unclassified")

DEFAULT_LIVE = "Cmmsell10"
DEFAULT_TWIN = "Cmmsell10_pt3"


def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def q(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def classify_series(series: str | None) -> str:
    """`crypto` | `non_crypto` | `unclassified`, on an EXACT series match.

    A missing series is `unclassified` rather than `non_crypto`: absence of data
    is not evidence of a market type, and the whole point of the residual bucket
    is that it stays visible."""
    if not series:
        return "unclassified"
    s = series.strip().upper()
    if s in CRYPTO_SERIES:
        return "crypto"
    # Known to the settlement taxonomy but not a crypto series -> genuinely
    # non-crypto. Unknown to it as well -> unclassified, and reported.
    return "non_crypto" if _taxonomy_knows(s) else "unclassified"


_TAXONOMY: set[str] | None = None


def _taxonomy_knows(series: str) -> bool:
    """Is this series prefix classified by the settlement taxonomy?

    Imported lazily and tolerantly: the script must still run through the ops
    channel if the package import ever fails there, in which case every
    non-crypto series simply reports as `unclassified` — visibly wrong rather
    than quietly reclassified."""
    global _TAXONOMY
    if _TAXONOMY is None:
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from kalshi_bot.mmsell.market_types import SERIES_TYPES

            _TAXONOMY = {str(row[0]).upper() for row in SERIES_TYPES}
        except Exception:  # noqa: BLE001 — a diagnostic must not fail closed
            _TAXONOMY = set()
    return series in _TAXONOMY


def _series_of(cur, tickers: list[str]) -> dict[str, str | None]:
    """ticker -> series, from the canonical settlement-meta mapping.

    Never string-parsed out of the market ticker. `mmsell_settlement_meta` is the
    same mapping the tracker's own event caps use, and a ticker missing from it
    is left as None (-> `unclassified`) rather than guessed at."""
    if not tickers:
        return {}
    rows = q(cur, "SELECT market_ticker, series_ticker FROM mmsell_settlement_meta"
                  " WHERE market_ticker = ANY(%s)", (tickers,))
    return {r[0]: r[1] for r in rows}


def _blank() -> dict:
    return {"settled_markets": 0, "contracts": 0, "wins": 0, "pnl_usd": 0.0,
            "tail_losses": 0, "worst_loss_usd": 0.0, "open_positions": 0,
            "open_exposure_usd": 0.0, "ordered": 0, "filled": 0,
            "twin_opened": 0, "live_placed": 0}


def collect(cur, live_tag: str, twin_tag: str) -> dict[str, dict]:
    out = {name: _blank() for name in SLICES}

    # Every market this book entered live, with the contracts it actually filled.
    rows = q(cur,
             "SELECT o.market_ticker, sum(coalesce(o.quantity,0)) AS ordered,"
             "       sum(coalesce(f.q,0)) AS filled"
             "  FROM live_orders o"
             "  LEFT JOIN (SELECT kalshi_order_id, sum(quantity) q FROM fills"
             "              WHERE action='buy' GROUP BY 1) f"
             "         ON f.kalshi_order_id = o.kalshi_order_id"
             " WHERE o.strategy=%s AND o.action='buy'"
             "   AND lower(coalesce(o.status,'')) NOT IN ('rejected','error','unknown','pending')"
             " GROUP BY 1", (live_tag,))
    entered = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}
    series = _series_of(cur, list(entered))

    # Newest position snapshot per market — `positions` is append-only.
    pos = {}
    if entered:
        for t, qty, qty_fp, exposure, realized in q(
            cur,
            "SELECT DISTINCT ON (market_ticker) market_ticker, quantity, quantity_fp,"
            "       market_exposure, realized_pnl"
            "  FROM positions WHERE market_ticker = ANY(%s)"
            " ORDER BY market_ticker, captured_at DESC", (list(entered),)
        ):
            pos[t] = (qty, qty_fp, exposure, realized)

    for ticker, (ordered, filled) in entered.items():
        sl = out[classify_series(series.get(ticker))]
        sl["ordered"] += ordered
        sl["filled"] += filled
        row = pos.get(ticker)
        if row is None:
            continue
        qty, qty_fp, exposure, realized = row
        held = float(qty_fp) if qty_fp is not None else float(qty or 0)
        if abs(held) > 0.01:
            sl["open_positions"] += 1
            sl["open_exposure_usd"] += float(exposure or 0.0)
            continue                       # outcome UNKNOWN — never scored
        if realized is None:
            continue                       # closed but unpriced — not a zero
        sl["settled_markets"] += 1
        sl["contracts"] += filled
        sl["pnl_usd"] += float(realized)
        if float(realized) > 0:
            sl["wins"] += 1
        elif float(realized) < 0:
            sl["tail_losses"] += 1
            sl["worst_loss_usd"] = max(sl["worst_loss_usd"], -float(realized))

    # Decision overlap, off the parity tape.
    for ticker, twin_out, live_out in q(
        cur, "SELECT market_ticker, twin_outcome, live_outcome"
             " FROM live_paper_parity_events WHERE twin_tag=%s", (twin_tag,)
    ):
        if twin_out != "opened":
            continue
        sl = out[classify_series(series.get(ticker))]
        sl["twin_opened"] += 1
        if live_out == "placed":
            sl["live_placed"] += 1
    return out


def _rate(num, den, unit="%", places=1):
    return f"{num / den * 100:.{places}f}{unit}" if den else "n/a"


def report(live_tag: str, twin_tag: str, data: dict[str, dict]) -> None:
    print(f"=== mmsell canary monitoring slices — live {live_tag} / twin {twin_tag} ===")
    print("DIAGNOSTIC ONLY. Not a pre-registered stopping criterion; the registered")
    print("live_canary_keep gate is what decides keep/stop.\n")
    head = (f"  {'slice':14s} {'settled':>8} {'ctr':>6} {'win%':>7} {'c/ct':>9} "
            f"{'total$':>9} {'tails':>6} {'worst$':>7} {'open':>5} {'openex$':>8} "
            f"{'fill%':>7} {'overlap%':>9}")
    print(head)
    print("  " + "-" * (len(head) - 2))
    for name in SLICES:
        d = data[name]
        cpc = (d["pnl_usd"] * 100.0 / d["contracts"]) if d["contracts"] else None
        print(f"  {name:14s} {d['settled_markets']:8d} {d['contracts']:6d}"
              f" {_rate(d['wins'], d['settled_markets']):>7}"
              f" {(f'{cpc:+.3f}' if cpc is not None else 'n/a'):>9}"
              f" {d['pnl_usd']:+9.2f} {d['tail_losses']:6d} {d['worst_loss_usd']:7.2f}"
              f" {d['open_positions']:5d} {d['open_exposure_usd']:8.2f}"
              f" {_rate(d['filled'], d['ordered']):>7}"
              f" {_rate(d['live_placed'], d['twin_opened']):>9}")
    unc = data["unclassified"]
    if unc["ordered"] or unc["twin_opened"]:
        print("\n  NOTE: markets landed in `unclassified` — their series is absent from")
        print("  mmsell_settlement_meta or from CRYPTO_SERIES/the settlement taxonomy.")
        print("  Extend CRYPTO_SERIES deliberately rather than reading the residual as")
        print("  non-crypto; that reading is what XOS-000009 is about.")
    if data["crypto"]["settled_markets"] < 30:
        print("\n  Crypto n is thin. The previous generation's crypto slice was 12")
        print("  settlements — a small negative signal, not a result. Do not present a")
        print("  thin slice as a conclusion, and do not stop on it: excluding crypto")
        print("  would be a different Version and could not inherit this arm's evidence.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", default=DEFAULT_LIVE)
    ap.add_argument("--twin", default=DEFAULT_TWIN)
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is required", file=sys.stderr)
        return 2
    import psycopg

    with psycopg.connect(url, options=RO_OPTIONS) as conn, conn.cursor() as cur:
        report(args.live, args.twin, collect(cur, args.live, args.twin))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
