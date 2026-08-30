"""MARKTANGLE Phase-A probe — the serial-dependence scanner (docs/MARKTANGLE_THESIS.md).

Read-only, public Kalshi API, stdlib only. Places nothing, writes nothing.

WHAT IS BEING TESTED, AND WHAT IS NOT
-------------------------------------
NOT tested: "ten YESes mean a NO is due." Under independence that claim is false
by construction, and no amount of history can make it true. A family can resolve
YES exactly 50% of the time and still be perfectly memoryless.

Tested: whether a recurring binary family carries **negative serial dependence
that survives out of sample and exceeds the market's own price**. The unit of
observation is the RESOLUTION of consecutive events in one recurring family —
not an intraday price path (that question is already answered and dead: the
scanner TA books and the weather backfill probes both refuted price-history
signals at the fee scale).

Three quantities, in the order that can kill the idea cheapest:

  SEQUENCE   per family: base rate P(Y), the transition matrix, and the
             streak-conditioned reversal rate P(reverse | streak = k) for
             k = 1..MAX_K, with exact counts and a Wilson 95% lower bound.
             Reported for the TRAIN segment (first 70% of each family's history
             by close time) and the HOLDOUT (last 30%) separately, never pooled.
  HOLDOUT    a family is a candidate only if a threshold k* chosen on TRAIN still
             shows reversal > 50% on the HOLDOUT it was never fitted to. This is
             the step that kills the fake patterns, and it is why the threshold
             is picked before the holdout is read rather than after.
  PRICE      for the surviving candidates, the market-implied probability of the
             reversal side at a fixed decision offset before close, and the net
             edge after WORST-CASE taker fees. A 60%-accurate reversal model is
             worth nothing if the reversal side already costs 64c.

Everything is reported with its n. A 70% reversal rate on 10 observations is
noise wearing a percentage sign, and the report says so rather than ranking it.

Usage (ops channel):
  {"type":"script","name":"marktangle_probe","args":["--pages","12"]}
  {"type":"script","name":"marktangle_probe","args":["--series","KXHIGHNY,KXBTCD","--price"]}
"""

from __future__ import annotations

import argparse
import calendar
import math
import time
from collections import defaultdict

import xvenue_leadlag as xl  # _get (browser UA + retries), _num

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"

#: Longest streak the conditional table reports. Beyond this the counts are
#: always too thin to say anything, and printing them invites reading noise.
MAX_K = 15

#: Fraction of each family's history reserved for fitting. The remainder is the
#: holdout, and nothing in the fitting stage may look at it.
TRAIN_FRAC = 0.70

#: Minutes before close at which the PRICE stage reads the quote. Far enough out
#: that the outcome is not yet mechanically determined, close enough that the
#: streak is fully known.
DECISION_MIN = 60

#: A family needs this many usable resolutions before any of its numbers are
#: reported as anything but context.
MIN_FAMILY_N = 40

#: The unconditional-balance band. The thesis admits "recurring binary families
#: whose unconditional outcome is roughly balanced", and the first exchange-wide
#: run showed why that clause has to be CODE and not prose: a KXBTCD strike
#: ladder contributes ~90 families that resolve NO 100% of the time (strikes
#: permanently out of the money). They are not memoryless — they are constant.
#: A constant sequence has no conditional structure to find, its transition
#: matrix is undefined on one side, and leaving it in buries the handful of
#: families that could carry a signal under a hundred rows of noise.
#:
#: Excluded families are COUNTED, never silently dropped: the funnel is part of
#: the result, and "we looked at 198 families" is a different claim from "we
#: looked at 198 and 190 of them could not have shown anything".
BASE_RATE_BAND = (25.0, 75.0)


def _iso_to_unix(iso: str) -> int:
    try:
        return calendar.timegm(time.strptime((iso or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0


def taker_fee_c(price_c: float) -> float:
    """Kalshi taker fee in cents/contract, worst-case rounded up (fee schedule:
    0.07 * C * P * (1-P), C = 1 contract)."""
    p = max(0.0, min(1.0, price_c / 100.0))
    return math.ceil(7.0 * p * (1 - p))


def wilson_lower(successes: int, n: int, z: float = 1.645) -> float:
    """One-sided Wilson 95% lower bound on a proportion, in percent.

    Wilson rather than Wald: at the small n these tables produce, the normal
    approximation's interval is exactly where the mirages live — it has zero
    width at a perfect record, which would rank 10/10 above 610/1150."""
    if n <= 0:
        return 0.0
    phat = successes / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return 100.0 * max(0.0, (centre - margin) / denom)


# ------------------------------------------------------------------ data source
def discover_series(pages: int, min_vol: float) -> list[str]:
    """Every series ticker on the live board, from `/events`.

    WHY NOT THE SETTLED LISTING. Two runs died here, one layer apart, and both
    for the same underlying reason: the un-restricted `status=settled` listing is
    not an enumeration of the exchange, it is a sample of recently-closed
    markets.

      * run 1 (`mkt-probe-1`) read family history straight from it -- 6,270
        markets, 0 families reaching the floor, because one page of it spans
        hundreds of series x strikes and gives each a handful of rows;
      * run 3 (`mkt-probe-3`) used it only to enumerate, and still found
        THREE series -- 6,000 markets of listing surfaced KXMVECROSSCATEGORY,
        KXLIGAMXSPREAD and KXUSLTOTAL, one of which supplied 9,066 of the
        9,790 markets. Whichever series has the most CLOSED markets crowds
        out everything else.

    `/events?status=open` is an enumeration rather than a sample: every series
    currently listing anything appears exactly once regardless of how many
    markets it has closed. Volume is not filtered here on purpose -- `min_vol`
    belongs to the HISTORY query, and applying it to discovery would drop a
    thin-but-live series before its history was ever looked at.

    Ordered by how many open events each series carries: a series with more
    concurrent events is a family that recurs more often, which is what the
    hypothesis needs. `--max-series` then takes a prefix of a real ranking
    instead of a prefix of an accident."""
    seen: dict[str, int] = {}
    cursor = ""
    for _ in range(max(1, pages)):
        page = xl._get(f"{KALSHI}/events?status=open&limit=200"
                       f"&with_nested_markets=false&cursor={cursor}")
        events = (page or {}).get("events") or []
        for e in events:
            series = e.get("series_ticker") or (e.get("event_ticker") or "").split("-", 1)[0]
            if series:
                seen[series] = seen.get(series, 0) + 1
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not events:
            break
    return [s for s, _ in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))]


def fetch_settled(pages: int, min_vol: float, series: str | None = None) -> list[dict]:
    """Settled binaries, newest first. `series` restricts to one series ticker."""
    out: list[dict] = []
    cursor = ""
    q = f"&series_ticker={series}" if series else ""
    for _ in range(max(1, pages)):
        page = xl._get(f"{KALSHI}/markets?status=settled&limit=1000&cursor={cursor}{q}")
        mkts = (page or {}).get("markets") or []
        for m in mkts:
            res = (m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            vol = xl._num(m.get("volume_fp")) or xl._num(m.get("volume"))
            if vol < min_vol:
                continue
            close = _iso_to_unix(m.get("close_time"))
            ticker = m.get("ticker") or ""
            event = m.get("event_ticker") or ""
            if not close or not ticker or not event:
                continue
            out.append({
                "ticker": ticker, "event": event, "close": close,
                "result": res, "vol": vol,
            })
        cursor = (page or {}).get("cursor") or ""
        if not cursor or not mkts:
            break
    return out


def family_key(market: dict) -> str:
    """`SERIES|SUFFIX` — one recurring binary question through time.

    The suffix matters. A multi-strike ladder (`KXHIGHNY-25AUG29-B82.5`) is not
    one recurring binary; each STRIKE is, and pooling the rungs of a ladder into
    one sequence would manufacture serial structure out of the ladder's own
    geometry. Splitting on the event ticker keeps `B82.5` a sequence of its own."""
    event = market["event"]
    ticker = market["ticker"]
    series = event.split("-", 1)[0]
    suffix = ticker[len(event) + 1:] if ticker.startswith(event + "-") else ""
    return f"{series}|{suffix}"


def build_families(markets: list[dict]) -> dict[str, list[dict]]:
    """family -> resolutions ordered oldest-first by close time.

    Same-close ties are dropped, not ordered arbitrarily: a "streak" across
    markets that resolved at the same instant is not a sequence, and guessing an
    order would invent the dependence the probe exists to measure."""
    fams: dict[str, list[dict]] = defaultdict(list)
    for m in markets:
        fams[family_key(m)].append(m)
    out: dict[str, list[dict]] = {}
    for key, rows in fams.items():
        rows.sort(key=lambda r: (r["close"], r["ticker"]))
        deduped = [r for i, r in enumerate(rows)
                   if i == 0 or r["close"] != rows[i - 1]["close"]]
        if len(deduped) >= MIN_FAMILY_N:
            out[key] = deduped
    return out


def screen_balance(families: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], dict]:
    """Keep only families whose unconditional yes-rate is inside BASE_RATE_BAND.

    Returns (kept, funnel). The funnel is reported, because a screen nobody can
    see is indistinguishable from a bug."""
    kept: dict[str, list[dict]] = {}
    degenerate = lopsided = 0
    for key, rows in families.items():
        yes = 100.0 * sum(1 for r in rows if r["result"] == "yes") / len(rows)
        if yes in (0.0, 100.0):
            degenerate += 1
        elif not (BASE_RATE_BAND[0] <= yes <= BASE_RATE_BAND[1]):
            lopsided += 1
        else:
            kept[key] = rows
    return kept, {"considered": len(families), "constant": degenerate,
                  "outside_band": lopsided, "kept": len(kept)}


# ------------------------------------------------------------------ the tables
def streak_table(seq: list[str], max_k: int = MAX_K) -> dict[int, tuple[int, int]]:
    """k -> (observations whose current run length is k, of which the NEXT one
    reversed). This is literally P(reverse | run = k) with its denominator.

    Runs are counted per POSITION, so one run of length 6 contributes an
    observation at k = 1..6. That is what the conditional probability means, and
    it is also why the rows of this table are not independent of each other: a
    single long run moves several rows at once. The holdout stage exists because
    of that, and the threshold is fitted on TRAIN only for the same reason."""
    table: dict[int, tuple[int, int]] = {k: (0, 0) for k in range(1, max_k + 1)}
    run = 0
    for i, outcome in enumerate(seq):
        run = run + 1 if i and outcome == seq[i - 1] else 1
        if i + 1 >= len(seq):
            break                      # no successor: nothing to condition on
        if 1 <= run <= max_k:
            n, rev = table[run]
            table[run] = (n + 1, rev + (1 if seq[i + 1] != outcome else 0))
    return table


def transition_matrix(seq: list[str]) -> dict[str, tuple[int, int]]:
    """'yes'/'no' -> (transitions from that state, of which stayed)."""
    out = {"yes": (0, 0), "no": (0, 0)}
    for a, b in zip(seq, seq[1:], strict=False):
        n, same = out[a]
        out[a] = (n + 1, same + (1 if a == b else 0))
    return out


def split(seq: list, frac: float = TRAIN_FRAC) -> tuple[list, list]:
    cut = int(len(seq) * frac)
    return seq[:cut], seq[cut:]


def pick_threshold(train: list[str], min_n: int) -> tuple[int, float, int, int] | None:
    """The k* fitted on TRAIN ONLY: the smallest k whose reversal rate has a
    Wilson lower bound above 50% on at least `min_n` observations.

    Smallest rather than best-looking: picking the maximum over 15 candidate
    thresholds is a 15-way search, and its winner's lower bound is not a 95%
    bound on anything. The holdout is what grades it, so the fitting rule only
    has to be pre-specified and cheap."""
    table = streak_table(train)
    for k in range(1, MAX_K + 1):
        n, rev = table[k]
        if n >= min_n and wilson_lower(rev, n) > 50.0:
            return k, wilson_lower(rev, n), rev, n
    return None


def holdout_at_least(seq: list[str], k: int) -> tuple[int, int]:
    """(observations at a run of AT LEAST k, of which reversed) — the tradeable
    form of the rule. Once k* is fixed, every run at or beyond it is an entry, so
    this is what the arm would actually have done."""
    n = rev = 0
    run = 0
    prev: str | None = None
    for outcome in seq:
        if prev is not None and run >= k:
            n += 1
            rev += 1 if outcome != prev else 0
        run = run + 1 if outcome == prev else 1
        prev = outcome
    return n, rev


# ------------------------------------------------------------------ price stage
def reversal_side_price_c(candle: dict, reversal_is_yes: bool) -> float | None:
    """Taker cost in cents of the reversal side at this candle, or None.

    We would be lifting an offer, so YES costs the yes-ask and NO costs
    `100 - yes_bid` — the same book event read from the other side. Using a mid
    or a last-trade price here would quietly hand the probe a price nobody could
    have transacted at, which is the fill-realism error the mmsell fill model
    exists to prevent."""
    ask = ((candle.get("yes_ask") or {}).get("close"))
    bid = ((candle.get("yes_bid") or {}).get("close"))
    if reversal_is_yes:
        p = xl._num(ask)
    else:
        p = 100.0 - xl._num(bid) if bid is not None else 0.0
    return p if 1.0 <= p <= 99.0 else None


def decision_candle(series: str, ticker: str, close_ts: int) -> dict | None:
    """The 1-minute candle covering T-DECISION_MIN, from whichever archive holds
    this market. Settled markets migrate out of the live data set, so both
    endpoints are tried before concluding there is no quote."""
    at = close_ts - DECISION_MIN * 60
    for url in (
        f"{KALSHI}/series/{series}/markets/{ticker}/candlesticks"
        f"?start_ts={at - 300}&end_ts={at + 60}&period_interval=1",
        f"{KALSHI}/historical/markets/{ticker}/candlesticks"
        f"?start_ts={at - 300}&end_ts={at + 60}&period_interval=1",
    ):
        data = xl._get(url)
        candles = (data or {}).get("candlesticks") or []
        if candles:
            return candles[-1]
    return None


def price_stage(rows: list[dict], k: int, p_model: float, max_fetch: int) -> dict:
    """Net edge of the rule against the quote it would actually have paid.

    `rows` is the family's HOLDOUT segment in order. An entry is every position
    whose current run length is >= k; the reversal side is the opposite of the
    running outcome. Edge per contract, in cents:

        EV = 100 * p_model - price_paid - taker_fee(price_paid)

    p_model comes from TRAIN. Using the holdout's own realized reversal rate here
    would be scoring the rule against its own answers."""
    entries = 0
    priced = 0
    edges: list[float] = []
    wins = 0
    run = 0
    for i, row in enumerate(rows):
        run = run + 1 if i and row["result"] == rows[i - 1]["result"] else 1
        if i + 1 >= len(rows) or run < k:
            continue
        entries += 1
        if priced >= max_fetch:
            continue
        nxt = rows[i + 1]
        series = nxt["event"].split("-", 1)[0]
        candle = decision_candle(series, nxt["ticker"], nxt["close"])
        if not candle:
            continue
        reversal_is_yes = row["result"] == "no"
        price = reversal_side_price_c(candle, reversal_is_yes)
        if price is None:
            continue
        priced += 1
        edges.append(100.0 * p_model - price - taker_fee_c(price))
        won = (nxt["result"] == "yes") == reversal_is_yes
        wins += 1 if won else 0
    return {
        "entries": entries,
        "priced": priced,
        "mean_edge_c": (sum(edges) / len(edges)) if edges else None,
        "mean_price_c": None if not edges else
            (100.0 * p_model - sum(edges) / len(edges)),
        "realized_reversal_pct": (100.0 * wins / priced) if priced else None,
    }


# ------------------------------------------------------------------ the report
def analyse(families: dict[str, list[dict]], min_k_n: int) -> list[dict]:
    """One screened record per family. Fitting reads TRAIN only, in every branch."""
    out: list[dict] = []
    for key, rows in sorted(families.items()):
        seq = [r["result"] for r in rows]
        train_rows, hold_rows = split(rows)
        train, hold = [r["result"] for r in train_rows], [r["result"] for r in hold_rows]
        yes_rate = 100.0 * sum(1 for o in seq if o == "yes") / len(seq)
        trans = transition_matrix(seq)
        fitted = pick_threshold(train, min_k_n)
        rec = {
            "family": key, "n": len(seq), "n_train": len(train), "n_hold": len(hold),
            "yes_rate_pct": yes_rate, "transitions": trans,
            "train_table": streak_table(train), "hold_table": streak_table(hold),
            "k": None, "train_lb_pct": None, "hold_n": 0, "hold_rev": 0,
            "hold_lb_pct": None, "p_model": None, "rows": rows,
        }
        if fitted:
            k, lb, rev, n = fitted
            hn, hrev = holdout_at_least(hold, k)
            rec.update(
                k=k, train_lb_pct=lb, train_rev=rev, train_n=n,
                hold_n=hn, hold_rev=hrev,
                hold_lb_pct=wilson_lower(hrev, hn) if hn else None,
                p_model=rev / n,
            )
        out.append(rec)
    return out


def print_report(records: list[dict], min_hold_n: int, edge_bar: float,
                 priced: dict[str, dict]) -> None:
    print("=== SEQUENCE — every family with at least "
          f"{MIN_FAMILY_N} usable resolutions ===")
    print(f"{'family':<34}{'n':>6}{'train':>7}{'hold':>6}{'yes%':>7}"
          f"{'P(Y|Y)%':>9}{'P(N|N)%':>9}")
    for r in sorted(records, key=lambda x: -x["n"]):
        ny, sy = r["transitions"]["yes"]
        nn, sn = r["transitions"]["no"]
        print(f"{r['family']:<34}{r['n']:>6}{r['n_train']:>7}{r['n_hold']:>6}"
              f"{r['yes_rate_pct']:>7.1f}"
              f"{(100.0 * sy / ny) if ny else float('nan'):>9.1f}"
              f"{(100.0 * sn / nn) if nn else float('nan'):>9.1f}")

    print("\n=== HOLDOUT — k* fitted on TRAIN, graded on data it never saw ===")
    print("A family appears here only if TRAIN produced a k* at all. The holdout "
          "column is the verdict;\nthe train column is what was promised.")
    print(f"{'family':<34}{'k*':>4}{'train lb%':>11}{'hold n':>8}"
          f"{'hold rev%':>11}{'hold lb%':>10}")
    candidates = [r for r in records if r["k"]]
    if not candidates:
        print("  (none — no family produced a fitted threshold on TRAIN)")
    for r in sorted(candidates, key=lambda x: -(x["hold_lb_pct"] or 0)):
        hrev = (100.0 * r["hold_rev"] / r["hold_n"]) if r["hold_n"] else float("nan")
        print(f"{r['family']:<34}{r['k']:>4}{r['train_lb_pct']:>11.1f}"
              f"{r['hold_n']:>8}{hrev:>11.1f}"
              f"{(r['hold_lb_pct'] or 0.0):>10.1f}")

    survivors = [r for r in candidates
                 if r["hold_n"] >= min_hold_n and (r["hold_lb_pct"] or 0) > 50.0]
    print(f"\nHOLDOUT SURVIVORS (n >= {min_hold_n} and lower bound > 50%): "
          f"{len(survivors)}")

    if priced:
        print(f"\n=== PRICE — net edge vs the quote at T-{DECISION_MIN}m, "
              "worst-case taker fees ===")
        print(f"{'family':<34}{'entries':>9}{'priced':>8}{'mean price':>12}"
              f"{'net edge c':>12}{'realized rev%':>15}")
        for key, p in sorted(priced.items()):
            print(f"{key:<34}{p['entries']:>9}{p['priced']:>8}"
                  f"{(p['mean_price_c'] if p['mean_price_c'] is not None else float('nan')):>12.1f}"
                  f"{(p['mean_edge_c'] if p['mean_edge_c'] is not None else float('nan')):>12.2f}"
                  f"{(p['realized_reversal_pct'] if p['realized_reversal_pct'] is not None else float('nan')):>15.1f}")

    print("\n=== VERDICT (docs/MARKTANGLE_THESIS.md, pre-registered) ===")
    if not priced:
        print("  PRICE stage not run (--price) — SEQUENCE/HOLDOUT only. No verdict.")
        return
    passing = [k for k, p in priced.items()
               if p["priced"] >= min_hold_n and (p["mean_edge_c"] or -99) >= edge_bar]
    if passing:
        print(f"  PASS — {len(passing)} family(ies) clear the pre-registered bar "
              f"(holdout n >= {min_hold_n}, net edge >= +{edge_bar:.1f}c): "
              f"{', '.join(sorted(passing))}")
    elif any(p["priced"] < min_hold_n for p in priced.values()):
        print("  HOLD — no family reached the pre-registered holdout sample floor "
              f"of {min_hold_n} priced entries. Thin sample is not a negative "
              "result; it is no result.")
    else:
        print("  FAIL — every holdout survivor is priced at or through its edge. "
              "The reversal structure, where it exists, is already in the quote.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=int, default=12,
                    help="settled-market pages to sweep (1000 markets per page)")
    ap.add_argument("--series", default="",
                    help="comma-separated series tickers; default DISCOVERS them "
                         "from the exchange-wide settled listing")
    ap.add_argument("--discover-pages", type=int, default=25,
                    help="pages of /events?status=open to enumerate series from "
                         "(discovery only — history is fetched per-series)")
    ap.add_argument("--max-series", type=int, default=40,
                    help="how many discovered series to pull history for")
    ap.add_argument("--min-vol", type=float, default=50.0)
    ap.add_argument("--min-k-n", type=int, default=30,
                    help="TRAIN observations required at k before k* may be fitted")
    ap.add_argument("--min-hold-n", type=int, default=100,
                    help="pre-registered holdout sample floor")
    ap.add_argument("--edge-bar", type=float, default=3.0,
                    help="pre-registered net edge bar, cents/contract")
    ap.add_argument("--price", action="store_true",
                    help="run the PRICE stage on holdout survivors (slow: one "
                         "candle fetch per entry)")
    ap.add_argument("--max-fetch", type=int, default=400,
                    help="cap on candle fetches per family (rate-limit guard)")
    args = ap.parse_args(argv)

    markets: list[dict] = []
    series_list = [s.strip().upper() for s in args.series.split(",") if s.strip()]
    if not series_list:
        series_list = discover_series(args.discover_pages, args.min_vol)
        print(f"discovered {len(series_list)} series in the settled listing; "
              f"pulling history for the top {args.max_series}")
        series_list = series_list[:args.max_series]
    for s in series_list:
        got = fetch_settled(args.pages, args.min_vol, series=s)
        print(f"  {s}: {len(got)} settled markets")
        markets.extend(got)
    print(f"{len(markets)} settled binaries with volume >= {args.min_vol:.0f}")
    if not markets:
        print("no data — nothing to say. This is not a negative result.")
        return 0

    families = build_families(markets)
    families, funnel = screen_balance(families)
    print(f"{funnel['considered']} recurring families with >= {MIN_FAMILY_N} "
          f"resolutions; {funnel['constant']} constant (0% or 100% YES) and "
          f"{funnel['outside_band']} outside the {BASE_RATE_BAND[0]:.0f}-"
          f"{BASE_RATE_BAND[1]:.0f}% balance band were screened out, leaving "
          f"{funnel['kept']}\n")
    if not families:
        print("no family is unconditionally balanced enough to carry the "
              "hypothesis. That is a universe finding, not a verdict on the "
              "mechanism.")
        return 0
    records = analyse(families, args.min_k_n)

    priced: dict[str, dict] = {}
    if args.price:
        survivors = [r for r in records if r["k"]
                     and r["hold_n"] >= args.min_hold_n
                     and (r["hold_lb_pct"] or 0) > 50.0]
        for r in survivors:
            _, hold_rows = split(r["rows"])
            priced[r["family"]] = price_stage(
                hold_rows, r["k"], r["p_model"], args.max_fetch
            )

    print_report(records, args.min_hold_n, args.edge_bar, priced)
    return 0


if __name__ == "__main__":  # pragma: no cover - ops channel calls main()
    raise SystemExit(main())
