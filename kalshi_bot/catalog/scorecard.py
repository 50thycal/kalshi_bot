"""Series scorecard: what MMSELL's live, attributed fills earned per approved series.

Read-only and descriptive. It adds up the catalog's own per-market source-ledger economics
(`exclusive-binary-ledger-v4`, the same records the assessments use), groups markets that
share one outcome (the approved exposure key, e.g. one baseball game) into one independent
bet, and reports spread so a reader can see how thin a sample is. It is not a confidence
score, a qualification, a gate verdict or a trading instruction; blocked markets are counted
and named, never guessed into the totals.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from decimal import Decimal

METHOD = "series-scorecard-v1"


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
    for record in attributed:
        pnl = _d(record["net_pnl_dollars"])
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


def scorecard(store, series_list):
    rows = [series_scorecard(store, series) for series in series_list]
    return {
        "method": METHOD,
        "evidence": "live MMSELL fills, catalog source-ledger economics (exclusive-binary-ledger-v4)",
        "bets_grouped_by": "approved shared_exposure_key; a market without one counts alone",
        "descriptive_only": True,
        "confidence_score": None,
        "qualified": False,
        "independence_verified": False,
        "historical_terms_verified": False,
        "excluded": "blocked markets (named per series), paper evidence",
        "series": rows,
        "families": family_scorecards(store, series_list),
        "total": {
            "attributed_markets": sum(r["attributed_markets"] for r in rows),
            "blocked_markets": sum(r["blocked_markets"] for r in rows),
            "net_pnl_dollars": str(sum(Decimal(r["net_pnl_dollars"]) for r in rows)),
            "actual_fees_dollars": str(sum(Decimal(r["actual_fees_dollars"]) for r in rows)),
            "independent_bets": sum(r["independent_bets"] for r in rows),
        },
    }
