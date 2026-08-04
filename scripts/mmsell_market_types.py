"""mmsell MARKET-TYPE census — what KINDS of market do the mmsell books actually trade, how
much of the flow is each, and what does each one pay?

WHY THIS EXISTS
---------------
Every mmsell read so far slices the book by PRICE (`mmsell_fill_model`), by BOOK
(`mmsell_live`), by EXIT RULE (`mmsell_exit_study`) or by SPORT/REGIME
(`mmsell_regime_backtest`, `mmsell_supply_forecast`). None of them slices by the thing the
entry rule is actually blind to: the **structure of the contract**. mmsell sells any cheap
tail it finds, so a Trump-mention market, a BTC daily strike, an MLB run-line and a tennis
match winner are all the same trade to the engine — and they are emphatically not the same
trade to the market.

`docs/MMSELL_ROADMAP.md` Sec 9 already found the first crack in that assumption (h2h winners
carry the loss engine, every total/spread/prop cell does not), and `mmsell_h2h_study` chased
it into Kalshi's settled history. But "h2h vs everything else" is a two-cell view of a book
that spans 118 series. This script builds the full taxonomy so a per-type decision (allowlist,
blocklist, price cap, entry window) can be made on our OWN trading data instead of on the one
cell somebody happened to look at.

THE TAXONOMY IS AN EXPLICIT TABLE, NOT A HEURISTIC
--------------------------------------------------
`SERIES_TYPES` maps a series prefix to (type, settle_mode). It is deliberately a hand-audited
table rather than a regex over the series name, for the same reason `mmsell_h2h_study` uses
one: the classification IS the hypothesis under test, so it has to be reviewable and
falsifiable rather than inferred at runtime. Longest-prefix wins, so KXMLBHRDERBYMATCHUP is
not swallowed by KXMLBHRDERBY, and KXWC1HTOTAL is not swallowed by KXWC1H.

Anything unmatched lands in `unclassified` and is printed loudly with its series and volume,
so a new series the bot starts trading shows up as a gap to be classified rather than being
silently folded into a bucket it does not belong in.

SETTLE MODE — the axis a timing study needs
-------------------------------------------
Orthogonal to type, each series is tagged with HOW it resolves, because "time to close" means
three different things across the book:

  in_play    the contract resolves through a live contest; the clock/tape is running and the
             outcome is progressively revealed (h2h, totals, spreads, in-game props)
  scheduled  the contract resolves at a fixed instant off an external print, and nothing about
             the underlying is "revealed" before then (BTC daily strike, CPI, gas price)
  discrete   the contract resolves whenever some event does or does not occur inside a window,
             with no monotone path to it (will X say Y, award announcements, dropouts)

An "enter in the final hour" rule means *the outcome is nearly determined* for `in_play` and
means *nothing has happened yet* for `scheduled`. Pooling them is how a timing result becomes
a mirage, so every table here carries the split.

READING THE OUTPUT — three traps
--------------------------------
1. **n is book-trades, not independent bets.** Up to 16 books trade the same ticker in the
   same cycle, so a type's `n` is inflated by book duplication and its trades are strongly
   correlated. `mkts` (distinct tickers) is the honest independence measure; use the
   `--book` table for a single-book, duplication-free read.
2. **The pool is mostly the OLD wide-band config.** mmsell/1/2/3 are ~80% of settled trades
   and they entered above the cheap-band ceiling the current live candidate (mmsell10) uses.
   A type that looks bad in the pool may only look bad at prices we no longer pay — always
   read the entry-price column next to the P&L.
3. **This is paper P&L, fill-everything.** It does not carry the maker adverse-selection
   haircut. `mmsell_fill_model` is what converts a per-price paper number into a realizable
   one; nothing here is a promote signal on its own.

Read-only, self-contained (stdlib + psycopg); runs locally or via the ops channel:

    DATABASE_URL_RO=postgresql://... python scripts/mmsell_market_types.py
    # or:  {"type": "script", "name": "mmsell_market_types"}
    #      {"type": "script", "name": "mmsell_market_types", "args": ["--book", "mmsell10"]}
    #      {"type": "script", "name": "mmsell_market_types", "args": ["--series"]}

The classifier and the stats maths are pure functions unit-tested in
tests/test_mmsell_market_types.py, independent of the DB.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

RO_OPTIONS = (
    "-c default_transaction_read_only=on "
    "-c statement_timeout=120000 "
    "-c idle_in_transaction_session_timeout=120000"
)

# Settle modes — see the module docstring. Kept as bare strings (not an enum) so the table
# below stays readable as data.
IN_PLAY = "in_play"
SCHEDULED = "scheduled"
DISCRETE = "discrete"

# --- the taxonomy under review -----------------------------------------------------------
# (series_prefix, market_type, settle_mode). LONGEST prefix wins, so ordering here is for
# human reading only — `classify` does not depend on it.
#
# type vocabulary:
#   h2h          full-contest winner (moneyline / match winner)
#   h2h_period   winner of a half/period rather than the whole contest
#   spread       handicap or margin of victory
#   total        over/under on an aggregate produced by play (runs, goals, points, corners)
#   exact_score  the precise scoreline / set score, not a threshold
#   player_prop  a named player does/produces something
#   game_prop    a contest-level occurrence that is not a total, spread or winner
#   outright     tournament/season winner, top-N finish, award, elimination stage
#   mention      will a named person/broadcast say or name something in a window
#   price_strike a threshold on a traded price series at a scheduled instant
#   econ_release a threshold on a scheduled economic print or policy decision
#   rank_culture chart/rating/ranking outcomes
#   event_stat   a numeric outcome of an event that play does not produce (attendance, counts)
#   politics     a discrete political occurrence (dropout, confirmation, ouster)
SERIES_TYPES: tuple[tuple[str, str, str], ...] = (
    # --- head-to-head winners -------------------------------------------------------------
    ("KXMLBGAME", "h2h", IN_PLAY),
    ("KXNPBGAME", "h2h", IN_PLAY),
    ("KXWNBAGAME", "h2h", IN_PLAY),
    ("KXNBASUMMERGAME", "h2h", IN_PLAY),
    ("KXWCGAME", "h2h", IN_PLAY),
    ("KXMLSGAME", "h2h", IN_PLAY),
    ("KXNWSLGAME", "h2h", IN_PLAY),
    ("KXLIGAMXGAME", "h2h", IN_PLAY),
    ("KXCLUBFGAME", "h2h", IN_PLAY),
    ("KXUCLGAME", "h2h", IN_PLAY),
    ("KXECULPGAME", "h2h", IN_PLAY),
    ("KXBRASILEIROGAME", "h2h", IN_PLAY),
    ("KXBRASILEIROBGAME", "h2h", IN_PLAY),
    ("KXARGPREMDIVGAME", "h2h", IN_PLAY),
    ("KXALLSVENSKANGAME", "h2h", IN_PLAY),
    ("KXPERLIGA1GAME", "h2h", IN_PLAY),
    ("KXATPMATCH", "h2h", IN_PLAY),
    ("KXATPCHALLENGERMATCH", "h2h", IN_PLAY),
    ("KXWTAMATCH", "h2h", IN_PLAY),
    ("KXWTACHALLENGERMATCH", "h2h", IN_PLAY),
    ("KXITFMATCH", "h2h", IN_PLAY),
    ("KXITFWMATCH", "h2h", IN_PLAY),
    ("KXT20MATCH", "h2h", IN_PLAY),
    ("KXODIMATCH", "h2h", IN_PLAY),
    ("KXWODIMATCH", "h2h", IN_PLAY),
    ("KXTESTMATCH", "h2h", IN_PLAY),
    ("KXWTESTMATCH", "h2h", IN_PLAY),
    ("KXHUNDREDMATCH", "h2h", IN_PLAY),
    ("KXWHUNDREDMATCH", "h2h", IN_PLAY),
    ("KXUFCFIGHT", "h2h", IN_PLAY),
    ("KXBOXING", "h2h", IN_PLAY),
    ("KXLOLGAME", "h2h", IN_PLAY),
    ("KXCS2GAME", "h2h", IN_PLAY),
    ("KXCODGAME", "h2h", IN_PLAY),
    ("KXMLBHRDERBYMATCHUP", "h2h", IN_PLAY),
    # --- period winners -------------------------------------------------------------------
    ("KXWC1H", "h2h_period", IN_PLAY),
    ("KXWC2H", "h2h_period", IN_PLAY),
    ("KXATPSETWINNER", "h2h_period", IN_PLAY),
    # --- spreads / handicaps --------------------------------------------------------------
    ("KXMLBSPREAD", "spread", IN_PLAY),
    ("KXWCSPREAD", "spread", IN_PLAY),
    ("KXWNBASPREAD", "spread", IN_PLAY),
    ("KXNBASUMMERSPREAD", "spread", IN_PLAY),
    ("KXWCMOV", "spread", IN_PLAY),
    # --- totals ---------------------------------------------------------------------------
    ("KXMLBTOTAL", "total", IN_PLAY),
    ("KXWNBATOTAL", "total", IN_PLAY),
    ("KXNBASUMMERTOTAL", "total", IN_PLAY),
    ("KXWCTOTAL", "total", IN_PLAY),
    ("KXWC1HTOTAL", "total", IN_PLAY),
    ("KXWCTEAMTOTAL", "total", IN_PLAY),
    ("KXLIGAMXTOTAL", "total", IN_PLAY),
    ("KXMLSTOTAL", "total", IN_PLAY),
    ("KXWCCORNERS", "total", IN_PLAY),
    ("KXWCTCORNERS", "total", IN_PLAY),
    ("KXMLBHRDERBYOU", "total", IN_PLAY),
    ("KXMLBHRDERBY500", "total", IN_PLAY),
    # --- exact score ----------------------------------------------------------------------
    ("KXWCSCORE", "exact_score", IN_PLAY),
    ("KXWC1HSCORE", "exact_score", IN_PLAY),
    ("KXATPEXACTMATCH", "exact_score", IN_PLAY),
    # --- player props ---------------------------------------------------------------------
    ("KXMLBHR", "player_prop", IN_PLAY),
    ("KXMLBASGHR", "player_prop", IN_PLAY),
    ("KXWCGOAL", "player_prop", IN_PLAY),
    ("KXWCAST", "player_prop", IN_PLAY),
    ("KXWCSOA", "player_prop", IN_PLAY),
    ("KXMLBHRDERBYDISTANCE", "player_prop", IN_PLAY),
    ("KXMLBHRDERBYLONGEST", "player_prop", IN_PLAY),
    ("KXMLBHRDERBYFORECAST", "player_prop", IN_PLAY),
    # --- game props -----------------------------------------------------------------------
    ("KXWCFIRSTGOAL", "game_prop", IN_PLAY),
    ("KXWCFTTS", "game_prop", IN_PLAY),
    ("KXWCBTTS", "game_prop", IN_PLAY),
    ("KXWC1HBTTS", "game_prop", IN_PLAY),
    ("KXWC2HBTTS", "game_prop", IN_PLAY),
    ("KXWCMOF", "game_prop", IN_PLAY),
    ("KXUFCMOV", "game_prop", IN_PLAY),
    ("KXUFCVICROUND", "game_prop", IN_PLAY),
    # --- outrights / futures --------------------------------------------------------------
    ("KXPGATOUR", "outright", IN_PLAY),
    ("KXPGATOP5", "outright", IN_PLAY),
    ("KXPGATOP10", "outright", IN_PLAY),
    ("KXPGATOP20", "outright", IN_PLAY),
    ("KXPGAR3LEAD", "outright", IN_PLAY),
    ("KXLPGATOUR", "outright", IN_PLAY),
    ("KXLIVTOUR", "outright", IN_PLAY),
    ("KXDPWORLDTOUR", "outright", IN_PLAY),
    ("KXWTA", "outright", IN_PLAY),
    ("KXMLBHRDERBY", "outright", IN_PLAY),
    ("KXMLBHRDERBYSEMI", "outright", IN_PLAY),
    ("KXMLBHRDERBYR1LEAD", "outright", IN_PLAY),
    ("KXMLBASGMVP", "outright", IN_PLAY),
    ("KXWCAWARD", "outright", DISCRETE),
    ("KXWCSTAGEOFELIM", "outright", DISCRETE),
    ("KXWCMATCHUP", "outright", DISCRETE),
    ("KXLIUSAELIMINATIONW", "outright", DISCRETE),
    # --- mention / speech -----------------------------------------------------------------
    ("KXTRUMPSAY", "mention", DISCRETE),
    ("KXTRUMPSAYMONTH", "mention", DISCRETE),
    ("KXTRUMPSAYCOMPANY", "mention", DISCRETE),
    ("KXFEDMENTION", "mention", DISCRETE),
    ("KXWCMENTION", "mention", DISCRETE),
    ("KXWCFIRSTSONG", "mention", DISCRETE),
    # --- scheduled price strikes ----------------------------------------------------------
    ("KXBTCD", "price_strike", SCHEDULED),
    ("KXBTCMAXMON", "price_strike", SCHEDULED),
    ("KXWTI", "price_strike", SCHEDULED),
    ("KXWTIW", "price_strike", SCHEDULED),
    ("KXBRENTW", "price_strike", SCHEDULED),
    ("KXAAAGASM", "price_strike", SCHEDULED),
    ("KXAAAGASW", "price_strike", SCHEDULED),
    # --- scheduled economic prints --------------------------------------------------------
    ("KXFED", "econ_release", SCHEDULED),
    ("KXFEDDECISION", "econ_release", SCHEDULED),
    ("KXCPIYOY", "econ_release", SCHEDULED),
    ("KXECONSTATCPIYOY", "econ_release", SCHEDULED),
    # --- rank / culture -------------------------------------------------------------------
    ("KXRT", "rank_culture", SCHEDULED),
    ("KXRANKLISTSONGSPOTUSA", "rank_culture", SCHEDULED),
    ("KXNETFLIXRANKSHOW", "rank_culture", SCHEDULED),
    ("KXTOPMODEL", "rank_culture", DISCRETE),
    # --- event stats ----------------------------------------------------------------------
    ("KXWCATTEND", "event_stat", SCHEDULED),
    ("KXSPACEXCOUNT", "event_stat", SCHEDULED),
    # --- discrete political events --------------------------------------------------------
    ("KXPLATNERDROPOUT", "politics", DISCRETE),
    ("KXKASHOUT", "politics", DISCRETE),
    ("KXMEDNOMJUL", "politics", DISCRETE),
    # --- announcements --------------------------------------------------------------------
    # "which team does player X get announced for" — a destination market. Kept separate from
    # `outright` because nothing about it is contested on a field: it resolves the instant an
    # announcement lands, so it has a discrete jump risk an outright's slow ladder does not.
    ("KXNBATEAMANNOUNCE", "announcement", DISCRETE),
)

UNCLASSIFIED = ("unclassified", "unknown")


def series_of(market_ticker: str) -> str:
    """KXMLBGAME-26AUG012110BOSLAD-BOS -> KXMLBGAME."""
    return (market_ticker or "").split("-", 1)[0].upper()


def classify(series: str) -> tuple[str, str]:
    """(market_type, settle_mode) for a series ticker; UNCLASSIFIED when the table has no entry.

    Longest-prefix match so a more specific series always beats a shorter one that happens to
    prefix it (KXMLBHRDERBYMATCHUP over KXMLBHRDERBY, KXWC1HTOTAL over KXWC1H)."""
    s = (series or "").upper()
    best: tuple[str, str, str] | None = None
    for prefix, mtype, mode in SERIES_TYPES:
        if s.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, mtype, mode)
    return (best[1], best[2]) if best else UNCLASSIFIED


def pctile(sorted_vals: list[float], q: float) -> float | None:
    """Linear-interpolated percentile over an ALREADY-SORTED list. q in [0, 1].

    statistics.quantiles can't do arbitrary q cheaply and drops the exact endpoints, and this
    has to agree with the p5 convention used by scripts/mmsell_exit_study.py."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _breakeven(pnls: list[float], wins: int) -> dict:
    """Break-even loss rate implied by this cell's own realized win/loss SIZES, and the margin.

    Why this column carries the comparison: the raw loss rate is not comparable across types
    because each type is entered at a different price. h2h averages ~17c of premium and totals
    ~16c while player props average ~13c — a 12% loss rate is a disaster at 13c and comfortable
    at 17c. Selling a tail for an average of W and losing an average of L, the cell breaks even
    at loss rate W/(W+|L|); the margin (be - actual, in percentage points) is the same number
    for every type regardless of what it was priced at, and it is what c/trade is made of.

    Returns be_loss/edge_pp = None when the cell has no losses (nothing to normalize against).
    """
    n = len(pnls)
    win_sum = sum(p for p in pnls if p > 0)
    loss_sum = sum(p for p in pnls if p < 0)
    n_loss = n - wins
    if not wins or not n_loss:
        return {"be_loss": None, "edge_pp": None}
    avg_win = win_sum / wins
    avg_loss = abs(loss_sum / n_loss)
    be = avg_win / (avg_win + avg_loss)
    return {"be_loss": be, "edge_pp": 100.0 * (be - n_loss / n)}


def summarize(rows: list[dict]) -> dict:
    """Per-contract P&L stats for one group of trades.

    rows: dicts with keys pnl_c (cents/contract), ticker, book, entry_c (cent price of the
    tail we sold), hold_h (hours held, may be None).
    """
    pnls = sorted(r["pnl_c"] for r in rows)
    n = len(pnls)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for p in pnls if p > 0)
    losses = [p for p in pnls if p < 0]
    holds = [r["hold_h"] for r in rows if r.get("hold_h") is not None]
    entries = [r["entry_c"] for r in rows if r.get("entry_c") is not None]
    return {
        "n": n,
        "mkts": len({r["ticker"] for r in rows}),
        "books": len({r["book"] for r in rows}),
        "win": wins / n,
        "mean": sum(pnls) / n,
        "total": sum(pnls) / 100.0,  # dollars, at 1 contract/trade
        "worst": pnls[0],
        # The percentile grid is a poor tail read on a >90%-win book: with 1 loss in 20, p5
        # interpolates BETWEEN the loss and the premium and comes out positive. loss_rate x
        # avg_loss is the tail that actually drives the mean, so both are reported beside it.
        "loss_rate": len(losses) / n,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        **_breakeven(pnls, wins),
        "p5": pctile(pnls, 0.05),
        "p10": pctile(pnls, 0.10),
        "p50": pctile(pnls, 0.50),
        "p90": pctile(pnls, 0.90),
        "p95": pctile(pnls, 0.95),
        "entry": (sum(entries) / len(entries)) if entries else None,
        "hold": (sum(holds) / len(holds)) if holds else None,
    }


# --- DB ------------------------------------------------------------------------------------

def _to_libpq_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgresql+"):
        url = "postgresql://" + url.split("://", 1)[1]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def load_trades(cur, statuses: tuple[str, ...], include_twins: bool) -> list[dict]:
    """Every mmsell paper trade with a realized P&L, normalized to ONE contract.

    Statuses default to settled + closed_sl: filtering to 'settled' alone silently drops every
    position a stop actually closed, which is the exact reading error recorded against the
    anchor books in docs/BOOK_REGISTRY.md. closed_void is excluded — a voided market is a
    no-trade, not a result.

    Twin books (mmsell10_pt) are excluded by default: a twin enters at the LIVE maker price
    with live sizing, so pooling it with the paper books mixes two entry conventions.
    """
    twin_clause = "" if include_twins else \
        " AND strategy NOT IN (SELECT twin_tag FROM live_paper_twins)"
    cur.execute(
        "SELECT market_ticker, strategy, status, assumed_price, quantity, pnl,"
        "       created_at, closed_at"
        " FROM paper_trades"
        " WHERE strategy LIKE '%%mmsell%%'"
        "   AND status = ANY(%s)"
        "   AND NOT coalesce(legacy, false)"
        "   AND pnl IS NOT NULL AND quantity IS NOT NULL AND quantity > 0"
        + twin_clause,
        (list(statuses),),
    )
    out: list[dict] = []
    for tkr, book, status, px, qty, pnl, created, closed in cur.fetchall():
        hold_h = None
        if created is not None and closed is not None:
            hold_h = (closed - created).total_seconds() / 3600.0
        mtype, mode = classify(series_of(tkr))
        out.append({
            "ticker": tkr,
            "series": series_of(tkr),
            "book": book,
            "status": status,
            "type": mtype,
            "mode": mode,
            # The cheap tail we SOLD, in cents. The engine buys the other side at
            # `assumed_price`, so the premium collected is 100 - assumed_price for both the
            # ordinary leg (buy NO) and the strangle mirror leg (buy YES).
            "entry_c": (100 - int(px)) if px is not None else None,
            "pnl_c": float(pnl) / int(qty) * 100.0,
            "hold_h": hold_h,
        })
    return out


# --- rendering -----------------------------------------------------------------------------

def _f(x, spec="{:+.2f}") -> str:
    return spec.format(x) if x is not None else "n/a"


HDR = (f"  {'market type':13s} {'mode':9s} {'n':>5} {'mkts':>5} {'bks':>4} {'flow%':>6}"
       f" {'win%':>6} {'c/trade':>8} {'total$':>9} {'entry':>6} {'hold_h':>7}"
       f" {'loss%':>6} {'be%':>6} {'edge':>7} {'avgloss':>8} {'worst':>7} {'p5':>7}"
       f" {'p10':>7} {'p50':>6} {'p90':>6} {'p95':>6}")


def _row(label: str, mode: str, s: dict, flow_n: int) -> str:
    return (f"  {label:13s} {mode:9s} {s['n']:5d} {s['mkts']:5d} {s['books']:4d}"
            f" {100.0*s['n']/flow_n:5.1f}% {100.0*s['win']:5.1f}% {s['mean']:+7.2f}c"
            f" {s['total']:+8.2f} {_f(s['entry'], '{:5.1f}'):>6} {_f(s['hold'], '{:6.1f}'):>7}"
            f" {100.0*s['loss_rate']:5.1f}%"
            f" {_f(100.0*s['be_loss'] if s['be_loss'] is not None else None, '{:5.1f}'):>5}%"
            f" {_f(s['edge_pp'], '{:+6.1f}'):>7} {_f(s['avg_loss'], '{:+7.1f}'):>8}"
            f" {s['worst']:+6.0f}c {_f(s['p5'], '{:+6.1f}'):>7} {_f(s['p10'], '{:+6.1f}'):>7}"
            f" {_f(s['p50'], '{:+5.1f}'):>6} {_f(s['p90'], '{:+5.1f}'):>6}"
            f" {_f(s['p95'], '{:+5.1f}'):>6}")


def _table(title: str, groups: dict[str, list[dict]], flow_n: int, min_n: int = 0) -> None:
    print(f"\n=== {title} ===")
    print(HDR)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    hidden = 0
    for key, rows in ranked:
        if len(rows) < min_n:
            hidden += len(rows)
            continue
        s = summarize(rows)
        modes = {r["mode"] for r in rows}
        mode = modes.pop() if len(modes) == 1 else "mixed"
        print(_row(key, mode, s, flow_n))
    if hidden:
        print(f"  (+{hidden} trades in cells below --min-n, not shown)")


def report(trades: list[dict], book: str | None, show_series: bool, min_n: int,
           maxyes: int = 7) -> None:
    if not trades:
        print("(no mmsell paper trades matched)")
        return

    n_all = len(trades)
    books = sorted({t["book"] for t in trades})
    statuses: dict[str, int] = defaultdict(int)
    for t in trades:
        statuses[t["status"]] += 1
    print("=== mmsell market-type census ===")
    print(f"  trades={n_all}  distinct markets={len({t['ticker'] for t in trades})}"
          f"  books={len(books)}  ({', '.join(books)})")
    print(f"  statuses: {', '.join(f'{k}={v}' for k, v in sorted(statuses.items()))}")
    print("  P&L is per CONTRACT in cents, paper (fill-everything). n counts BOOK-trades, so it"
          "\n  is inflated by the same ticker being traded by several books; 'mkts' is the"
          "\n  independence measure and the --book table below removes the duplication.")

    by_type: dict[str, list[dict]] = defaultdict(list)
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_type[t["type"]].append(t)
        by_mode[t["mode"]].append(t)
    _table("BY MARKET TYPE (all books pooled)", by_type, n_all)
    _table("BY SETTLE MODE (all books pooled)", by_mode, n_all)

    # Entry price is the confound that makes the pooled table hard to read: each type is
    # entered at a different average premium, so its loss rate is measured against a different
    # break-even. `edge` normalizes that, but the pooled table is still ~80% old wide-band
    # books trading at 12-20c, which is NOT the band the live candidate trades. This cut fixes
    # the price and lets the type comparison stand on its own — at the price we actually pay.
    cheap = [t for t in trades if t["entry_c"] is not None and t["entry_c"] <= maxyes]
    if cheap:
        g: dict[str, list[dict]] = defaultdict(list)
        for t in cheap:
            g[t["type"]].append(t)
        _table(f"BY MARKET TYPE — CHEAP BAND ONLY (entry <= {maxyes}c, all books pooled)",
               g, len(cheap))
        print(f"  (this is the live mmsell10 regime's price band: {len(cheap)} of {n_all} trades."
              "\n   Comparing types here holds the entry price roughly fixed, so a difference is"
              "\n   about the CONTRACT, not about what we paid for it.)")

    # Single-book view: same taxonomy with zero cross-book duplication, so the per-type
    # numbers are independent bets under ONE parameter set.
    if book:
        sub = [t for t in trades if t["book"] == book]
        if not sub:
            print(f"\n(no trades for --book {book}; books present: {', '.join(books)})")
        else:
            g: dict[str, list[dict]] = defaultdict(list)
            for t in sub:
                g[t["type"]].append(t)
            _table(f"BY MARKET TYPE — single book '{book}' (no duplication)", g, len(sub))

    if show_series:
        for mtype in sorted(by_type, key=lambda k: -len(by_type[k])):
            g = defaultdict(list)
            for t in by_type[mtype]:
                g[t["series"]].append(t)
            _table(f"SERIES DETAIL — {mtype}", g, len(by_type[mtype]), min_n=min_n)

    unc = by_type.get("unclassified") or []
    print("\n=== UNCLASSIFIED (series with no taxonomy entry) ===")
    if not unc:
        print("  none — every traded series is classified.")
    else:
        g = defaultdict(int)
        for t in unc:
            g[t["series"]] += 1
        print(f"  {len(unc)} trades across {len(g)} series — add these to SERIES_TYPES:")
        for s, n in sorted(g.items(), key=lambda kv: -kv[1]):
            print(f"    {s:32s} n={n}")

    print("\n  worst = single worst trade; p5/p95 = 5th/95th pctile of per-contract P&L."
          "\n  Selling a cheap tail wins small and often, so p50/p90/p95 all sit at the premium"
          "\n  and carry no information — the whole distribution lives in the left tail. Beware"
          "\n  p5 on a >95%-win cell: it interpolates BETWEEN the losses and the premium and can"
          "\n  print POSITIVE while the cell is bleeding. loss% x avgloss is the tail that"
          "\n  actually drives c/trade; read those two, then c/trade, and ignore win%."
          "\n  be% = the loss rate this cell breaks even at, given its own realized win/loss"
          "\n  sizes; edge = be% - loss% in percentage points. edge is the ONE column that is"
          "\n  comparable across types, because it already divides out the entry price each"
          "\n  type happens to be sold at. edge <= 0 means the cell is not paying for its tail.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", default="mmsell10",
                    help="book for the duplication-free table (default mmsell10, the live "
                         "candidate); 'none' to skip")
    ap.add_argument("--series", action="store_true",
                    help="also print the per-series detail inside each type")
    ap.add_argument("--min-n", type=int, default=5,
                    help="hide series cells below this n in the detail tables (default 5)")
    ap.add_argument("--status", default="settled,closed_sl",
                    help="comma-separated paper_trades statuses to include")
    ap.add_argument("--maxyes", type=int, default=7,
                    help="entry-price ceiling for the cheap-band table (default 7, the "
                         "mmsell10 live-candidate cap)")
    ap.add_argument("--include-twins", action="store_true",
                    help="include live/paper twin books (different entry convention)")
    args = ap.parse_args(argv)

    url = _to_libpq_url(os.environ.get("DATABASE_URL_RO") or os.environ.get("DATABASE_URL") or "")
    if not url:
        print("DATABASE_URL_RO (or DATABASE_URL) is not set.", file=sys.stderr)
        return 1

    import psycopg

    statuses = tuple(s.strip() for s in args.status.split(",") if s.strip())
    with psycopg.connect(url, options=RO_OPTIONS, connect_timeout=15) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            trades = load_trades(cur, statuses, args.include_twins)
    book = None if args.book.strip().lower() in ("", "none") else args.book.strip()
    report(trades, book, args.series, args.min_n, args.maxyes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
