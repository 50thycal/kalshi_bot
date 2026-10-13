"""Series scorecard: what MMSELL's live, attributed fills earned per approved series.

Read-only and descriptive. It adds up the catalog's own per-market source-ledger economics
(`exclusive-binary-ledger-v4`, the same records the assessments use), groups markets that
share one outcome (the approved exposure key, e.g. one baseball game) into one independent
bet, and reports spread so a reader can see how thin a sample is. It is not a confidence
score, a qualification, a gate verdict or a trading instruction; blocked markets are counted
and named, never guessed into the totals.

Paper sits beside live, never pooled with it. Paper is MMSELL `paper_trades` as imported,
settled rows only, at the source-recorded realized P&L (the same rows and statuses Experiment
OS's `realized_pnl_usd` reads); paper fills are assumed, so a paper number is an optimistic
reference, not a result. Live-paper twins are reported apart from the other paper books, and
`same_markets` compares paper and live only on markets both traded.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from decimal import Decimal

METHOD = "series-scorecard-v2"
# Mirrors experiment_os.metrics.SETTLED_STATUSES (pinned by a test); void and open rows are
# counted, never priced.
PAPER_SETTLED = ("settled", "closed_sl", "closed_tp", "closed_timeout")


def _d(value):
    return Decimal(str(value)) if value is not None else None


def _spread(values):
    """Mean and standard error of per-bet P&L; None below two bets."""
    n = len(values)
    if n < 2:
        return None, None
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance / n)


def series_scorecard(store, series):
    records = list(store.iter_pipeline_items("live-economics", series))
    attributed = [r for r in records if r.get("status") == "attributed_source_ledger"]
    blocked = Counter(
        reason for r in records if r.get("status") != "attributed_source_ledger"
        for reason in r.get("blocked_reasons") or ["unknown"]
    )
    groups = defaultdict(Decimal)
    reviewed = 0
    books = defaultdict(lambda: {"markets": 0, "net": Decimal(0), "contracts": Decimal(0)})
    net = fees = contracts = Decimal(0)
    winners = 0
    live_markets = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for record in attributed:
        pnl = _d(record["net_pnl_dollars"])
        live_markets[record["market_ticker"]][0] += pnl
        live_markets[record["market_ticker"]][1] += _d(record["buy_contracts"])
        net += pnl
        fees += _d(record["actual_fees_dollars"])
        contracts += _d(record["buy_contracts"])
        winners += pnl > 0
        market = store.get("market", record["market_ticker"]) or {}
        if market.get("review_status") == "reviewed":
            reviewed += 1
        key = (market.get("semantics") or {}).get("shared_exposure_key")
        groups[key or "market:" + record["market_ticker"]] += pnl
        book = books[(record.get("strategy_version"), record.get("side"))]
        book["markets"] += 1
        book["net"] += pnl
        book["contracts"] += _d(record["buy_contracts"])
    per_bet = [float(v) for v in groups.values()]
    mean, stderr = _spread(per_bet)
    times = sorted(r["first_fill_at"] for r in attributed if r.get("first_fill_at"))
    return {
        "series": series,
        "live_markets": len(records),
        "attributed_markets": len(attributed),
        "blocked_markets": len(records) - len(attributed),
        "blocked_reasons": dict(blocked.most_common()),
        "reviewed_attributed_markets": reviewed,
        "net_pnl_dollars": str(net),
        "actual_fees_dollars": str(fees),
        "contracts_bought": str(contracts),
        "net_cents_per_contract": float(net * 100 / contracts) if contracts else None,
        "winning_markets": winners,
        "independent_bets": len(groups),
        "independent_bets_winning": sum(v > 0 for v in groups.values()),
        "mean_pnl_per_bet_dollars": round(mean, 4) if mean is not None else None,
        "stderr_pnl_per_bet_dollars": round(stderr, 4) if stderr is not None else None,
        "worst_bet_dollars": str(min(groups.values())) if groups else None,
        "best_bet_dollars": str(max(groups.values())) if groups else None,
        "first_entry_at": times[0] if times else None,
        "last_entry_at": times[-1] if times else None,
        "by_book_and_side": [
            {"book": book, "side": side, "markets": v["markets"],
             "net_pnl_dollars": str(v["net"]), "contracts_bought": str(v["contracts"])}
            for (book, side), v in sorted(books.items(), key=lambda i: str(i[0]))
        ],
        "paper": paper_series_scorecard(store, series, live_markets),
    }


def family_scorecards(store, series_list):
    """Bets that share one outcome across series, e.g. every MLB total, spread, home-run and
    strikeout market on one game, counted once. A family is the approved exposure key without
    its event part ("candidate-game:MLB"). Only families where at least one bet spans two or
    more series are reported: a prefix such as "candidate-event" that merely recurs across
    unrelated series (tennis, rain) shares no outcome and is already in each series row."""
    families = defaultdict(lambda: defaultdict(lambda: defaultdict(Decimal)))
    for series in series_list:
        for record in store.iter_pipeline_items("live-economics", series):
            if record.get("status") != "attributed_source_ledger":
                continue
            market = store.get("market", record["market_ticker"]) or {}
            key = (market.get("semantics") or {}).get("shared_exposure_key")
            if not key or ":" not in key:
                continue
            families[key.rsplit(":", 1)[0]][key][series] += _d(record["net_pnl_dollars"])
    rows = []
    for family, bets in sorted(families.items()):
        members = sorted({series for parts in bets.values() for series in parts})
        if not any(len(parts) > 1 for parts in bets.values()):
            continue
        totals = {key: sum(parts.values()) for key, parts in bets.items()}
        mean, stderr = _spread([float(v) for v in totals.values()])
        rows.append({
            "family": family,
            "series": members,
            "independent_bets": len(totals),
            "independent_bets_winning": sum(v > 0 for v in totals.values()),
            "bets_spanning_two_or_more_series": sum(len(parts) > 1 for parts in bets.values()),
            "net_pnl_dollars": str(sum(totals.values())),
            "mean_pnl_per_bet_dollars": round(mean, 4) if mean is not None else None,
            "stderr_pnl_per_bet_dollars": round(stderr, 4) if stderr is not None else None,
            "worst_bet_dollars": str(min(totals.values())),
            "worst_bet": min(totals, key=totals.get),
            "net_by_series": {
                series: str(sum(parts.get(series, Decimal(0)) for parts in bets.values()))
                for series in members
            },
        })
    return rows


def _cents(net, contracts):
    return float(net * 100 / contracts) if contracts else None


def paper_series_scorecard(store, series, live_markets=None):
    """Paper beside live for one series: settled MMSELL paper trades, one bet per approved
    exposure key, twins apart, and paper-versus-live on the markets both traded."""
    keys = {}

    def key_of(ticker):
        if ticker not in keys:
            market = store.get("market", ticker) or {}
            keys[ticker] = ((market.get("semantics") or {}).get("shared_exposure_key"),
                            market.get("review_status") == "reviewed")
        return keys[ticker]

    statuses = Counter()
    books = defaultdict(lambda: {"trades": 0, "net": Decimal(0), "contracts": Decimal(0)})
    groups = defaultdict(Decimal)
    by_market = {False: defaultdict(lambda: [Decimal(0), Decimal(0)]),
                 True: defaultdict(lambda: [Decimal(0), Decimal(0)])}
    net = fees = contracts = Decimal(0)
    twin = {"trades": 0, "net": Decimal(0), "contracts": Decimal(0)}
    trades = winners = reviewed = 0
    times = []
    for row in store.iter_evidence("paper", series):
        if "mmsell" not in (row.get("strategy") or "").lower():
            continue
        status = row.get("status") or "unknown"
        statuses[status] += 1
        if status not in PAPER_SETTLED or row.get("pnl") is None:
            continue
        quantity = _d(row.get("quantity")) or Decimal(0)
        if quantity <= 0:
            continue
        pnl = _d(row["pnl"])
        ticker = row["market_ticker"]
        market = by_market[bool(row.get("is_twin"))][ticker]
        market[0] += pnl
        market[1] += quantity
        if row.get("is_twin"):
            twin["trades"] += 1
            twin["net"] += pnl
            twin["contracts"] += quantity
            continue
        trades += 1
        net += pnl
        fees += _d(row.get("fees")) or Decimal(0)
        contracts += quantity
        winners += pnl > 0
        key, is_reviewed = key_of(ticker)
        reviewed += is_reviewed
        groups[key or "market:" + ticker] += pnl
        book = books[row.get("strategy")]
        book["trades"] += 1
        book["net"] += pnl
        book["contracts"] += quantity
        if row.get("created_at"):
            times.append(str(row["created_at"]))
    mean, stderr = _spread([float(v) for v in groups.values()])
    times.sort()

    def same_markets(paper):
        shared = sorted(set(paper) & set(live_markets or {}))
        live_net = sum((live_markets[t][0] for t in shared), Decimal(0))
        live_contracts = sum((live_markets[t][1] for t in shared), Decimal(0))
        paper_net = sum((paper[t][0] for t in shared), Decimal(0))
        paper_contracts = sum((paper[t][1] for t in shared), Decimal(0))
        return {"markets": len(shared),
                "live_cents_per_contract": _cents(live_net, live_contracts),
                "paper_cents_per_contract": _cents(paper_net, paper_contracts)}

    return {
        "settled_trades": trades,
        "statuses": dict(statuses.most_common()),
        "markets": len(by_market[False]),
        "reviewed_settled_trades": reviewed,
        "net_pnl_dollars": str(net),
        "fees_dollars": str(fees),
        "contracts": str(contracts),
        "net_cents_per_contract": _cents(net, contracts),
        "winning_trades": winners,
        "independent_bets": len(groups),
        "independent_bets_winning": sum(v > 0 for v in groups.values()),
        "mean_pnl_per_bet_dollars": round(mean, 4) if mean is not None else None,
        "stderr_pnl_per_bet_dollars": round(stderr, 4) if stderr is not None else None,
        "worst_bet_dollars": str(min(groups.values())) if groups else None,
        "best_bet_dollars": str(max(groups.values())) if groups else None,
        "first_entry_at": times[0] if times else None,
        "last_entry_at": times[-1] if times else None,
        "books": [
            {"book": book, "trades": v["trades"], "net_pnl_dollars": str(v["net"]),
             "net_cents_per_contract": _cents(v["net"], v["contracts"])}
            for book, v in sorted(books.items(), key=lambda i: -i[1]["trades"])[:10]
        ],
        "books_total": len(books),
        "twins": {"settled_trades": twin["trades"], "markets": len(by_market[True]),
                  "net_pnl_dollars": str(twin["net"]),
                  "net_cents_per_contract": _cents(twin["net"], twin["contracts"])},
        "same_markets_as_live": same_markets(by_market[False]),
        "twin_same_markets_as_live": same_markets(by_market[True]),
    }


def scorecard(store, series_list):
    rows = [series_scorecard(store, series) for series in series_list]
    return {
        "method": METHOD,
        "evidence": "live MMSELL fills, catalog source-ledger economics (exclusive-binary-ledger-v4); "
                    "paper beside it (settled MMSELL paper_trades, source-recorded P&L, assumed fills)",
        "paper_live_pooled": False,
        "bets_grouped_by": "approved shared_exposure_key; a market without one counts alone",
        "descriptive_only": True,
        "confidence_score": None,
        "qualified": False,
        "independence_verified": False,
        "historical_terms_verified": False,
        "excluded": "blocked live markets (named per series); unsettled and void paper rows "
                    "(counted by status)",
        "series": rows,
        "families": family_scorecards(store, series_list),
        "total": {
            "attributed_markets": sum(r["attributed_markets"] for r in rows),
            "blocked_markets": sum(r["blocked_markets"] for r in rows),
            "net_pnl_dollars": str(sum(Decimal(r["net_pnl_dollars"]) for r in rows)),
            "actual_fees_dollars": str(sum(Decimal(r["actual_fees_dollars"]) for r in rows)),
            "independent_bets": sum(r["independent_bets"] for r in rows),
        },
        "paper_total": {
            "settled_trades": sum(r["paper"]["settled_trades"] for r in rows),
            "net_pnl_dollars": str(sum(Decimal(r["paper"]["net_pnl_dollars"]) for r in rows)),
            "independent_bets": sum(r["paper"]["independent_bets"] for r in rows),
            "twin_net_pnl_dollars": str(
                sum(Decimal(r["paper"]["twins"]["net_pnl_dollars"]) for r in rows)),
        },
    }
