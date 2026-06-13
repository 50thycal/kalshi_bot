"""Grade the forecast books vs the market favorite on settled daily-temperature
markets (HIGH and LOW), plus forecast accuracy, consistency, and data-collection
health.

Read-only. Run once the day's markets have settled:

    DATABASE_URL=postgresql://... python scripts/weather_score.py
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import case, func, select  # noqa: E402

from kalshi_bot import db  # noqa: E402
from kalshi_bot import models as m  # noqa: E402
from kalshi_bot.config import normalize_database_url  # noqa: E402
from kalshi_bot.weather.buckets import forecast_in_bucket, parse_bucket_range  # noqa: E402

CLOSED = ("settled", "closed_timeout", "closed_tp", "closed_sl", "closed_void", "abandoned")
_HOURS = re.compile(r"_h(\d+)$")

# Book prefixes by kind. Order matters when matching (low prefixes are longer).
BOOKS = {
    "high": {"fav": "weather_fav", "nws": "weather_nws", "cal": "weather_cal",
             "pm": "weather_pm", "cwin": "weather_cwin"},
    "low": {"fav": "weather_low_fav", "nws": "weather_low_nws", "cal": "weather_low_cal",
            "pm": "weather_low_pm"},
}


def _money(x) -> str:
    return f"${float(x or 0):,.2f}"


def _book(strat: str | None) -> tuple[str, str] | None:
    """Map a weather strategy string to (kind, book)."""
    s = strat or ""
    for kind in ("low", "high"):  # low prefixes are longer — match them first
        for book, prefix in BOOKS[kind].items():
            if s.startswith(prefix):
                return (kind, book)
    return None


def _day(ts) -> str:
    if ts is None:
        return "?"
    if hasattr(ts, "date"):
        return ts.date().isoformat()
    return str(ts)[:10]


def _settlement_actual(st) -> float | None:
    low, high = (st.actual_low_f, st.actual_high_f)
    if low is None and high is None:
        low, high = parse_bucket_range(st.winning_subtitle)
    if low is not None and high is not None:
        return (low + high) / 2.0
    if high is not None:
        return high  # "X or below" -> use the edge
    if low is not None:
        return low  # "X or above" -> use the edge
    return None


def main() -> int:
    url = normalize_database_url(os.environ.get("DATABASE_URL"))
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    db.init_engine(url)

    with db.session_scope() as s:
        # 1) Baseline win-rate / P&L per strategy (all weather books).
        print("=== Settled weather books by strategy ===")
        rows = s.execute(
            select(
                m.PaperTrade.strategy,
                func.count(),
                func.sum(case((m.PaperTrade.resolved_value == 100, 1), else_=0)),
                func.coalesce(func.sum(m.PaperTrade.pnl), 0),
            )
            .where(m.PaperTrade.strategy.like("weather%"), m.PaperTrade.status == "settled",
                   m.PaperTrade.legacy.is_(False))
            .group_by(m.PaperTrade.strategy)
            .order_by(m.PaperTrade.strategy)
        ).all()
        if not rows:
            print("  (no settled weather trades yet)")
        for strat, n, wins, pnl in rows:
            wr = f"{(int(wins or 0) / n * 100):.0f}%" if n else "n/a"
            print(f"  {strat:22s} settled={n:4d}  win={wr:>4}  pnl={_money(pnl)}")

        # 1a) Per-book rollup (windows summed): the at-a-glance ledger. P&L per trade
        # is the number that matters — it must clear the ~4-5c round-trip cost to print
        # money; a high win-rate alone just means buying expensive favorites (neg skew).
        print("\n=== Realized P&L by book (all windows summed) ===")
        roll: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0, 0.0])  # n, wins, pnl
        for strat, n, wins, pnl in rows:
            kb = _book(strat)
            if kb is None:
                continue
            agg = roll[kb]
            agg[0] += int(n)
            agg[1] += int(wins or 0)
            agg[2] += float(pnl)
        if not roll:
            print("  (no settled trades yet)")
        else:
            print(f"  {'book':16s} {'settled':>7}  {'win%':>4}  {'total':>8}  {'per-trade':>9}")
            grand = [0, 0, 0.0]
            for kind in ("high", "low"):
                for book in ("fav", "nws", "cal", "pm", "cwin"):
                    if (kind, book) not in roll:
                        continue
                    n, wins, pnl = roll[(kind, book)]
                    grand[0] += n
                    grand[1] += wins
                    grand[2] += pnl
                    wr = f"{wins / n * 100:.0f}%" if n else "n/a"
                    per = f"{pnl / n * 100:+.1f}c" if n else "n/a"
                    print(f"  {kind+' '+book:16s} {n:7d}  {wr:>4}  {_money(pnl):>8}  {per:>9}")
            gn, _gw, gp = grand
            gper = f"{gp / gn * 100:+.1f}c" if gn else "n/a"
            print(f"  {'TOTAL':16s} {gn:7d}  {'':>4}  {_money(gp):>8}  {gper:>9}")

        # 1b) The decision table: per entry window x strategy (fav/nws/cal), each with
        # n, win% and — the number that decides it — P&L per trade (must clear ~4-5c of
        # round-trip cost to make money). Read DOWN a strategy to judge a window ("does
        # nws at midday work?") and ACROSS a window to compare strategies, since
        # real-money trading picks ONE window x strategy, not the whole grid.
        cell: dict[tuple[str, int, str], list[float]] = defaultdict(lambda: [0, 0, 0.0])  # n, wins, pnl
        for strat, n, wins, pnl in rows:
            mobj = _HOURS.search(strat or "")
            kb = _book(strat)
            if mobj and kb:
                c = cell[(kb[0], int(mobj.group(1)), kb[1])]
                c[0] += int(n)
                c[1] += int(wins or 0)
                c[2] += float(pnl)
        for kind in ("high", "low"):
            print(f"\n=== {kind.upper()} books — by window x strategy (settled) ===")
            windows = sorted({w for (k, w, _b) in cell if k == kind}, reverse=True)
            if not windows:
                print("  (no settled trades yet)")
                continue
            print(f"  {'window':6s} {'strat':5s} {'n':>4} {'win%':>5} {'total':>8} {'per-trade':>9}")
            best_combo = None
            for w in windows:
                for b in ("fav", "nws", "cal", "pm", "cwin"):
                    if (kind, w, b) not in cell:
                        continue
                    n, wins, pnl = cell[(kind, w, b)]
                    wr = f"{wins / n * 100:.0f}%" if n else "n/a"
                    per_val = pnl / n * 100 if n else None
                    per = f"{per_val:+.1f}c" if per_val is not None else "n/a"
                    print(f"  h{w:<5d} {b:5s} {n:4d} {wr:>5} {_money(pnl):>8} {per:>9}")
                    if per_val is not None and (best_combo is None or per_val > best_combo[0]):
                        best_combo = (per_val, w, b, n)
                print()
            if best_combo:
                _v, bw, bb, bn = best_combo
                print(f"  best {kind} window x strategy by per-trade: "
                      f"{bb} @ h{bw} ({_v:+.1f}c/trade, n={bn})")

        # 2) Head-to-head + forecast accuracy, per kind.
        settlements = s.scalars(select(m.WeatherSettlement)).all()
        for kind in ("high", "low"):
            kind_settlements = [st for st in settlements if (st.kind or "high") == kind]
            fav_pattern = BOOKS[kind]["fav"] + "_h%"
            print(
                f"\n=== NWS vs market favorite — {kind.upper()} "
                f"({len(kind_settlements)} settled events) ==="
            )
            agg = {"both": 0, "nws_only": 0, "market_only": 0, "neither": 0, "no_data": 0}
            per_city: dict[str, list[int]] = {}  # city -> [nws_right, market_right, n]
            details = []
            for st in kind_settlements:
                # earliest-window FAVORITE trade for this event (the market's pick)
                trades = s.scalars(
                    select(m.PaperTrade)
                    .where(
                        m.PaperTrade.market_ticker.like(f"{st.event_ticker}-%"),
                        m.PaperTrade.strategy.like(fav_pattern),
                        m.PaperTrade.status == "settled",
                        m.PaperTrade.legacy.is_(False),
                    )
                ).all()
                trades.sort(key=lambda t: -(int(_HOURS.search(t.strategy or "").group(1)) if _HOURS.search(t.strategy or "") else 0))
                fav = trades[0] if trades else None

                # Forecast made at/before that entry (the morning value, not the evening null).
                fc_stmt = (
                    select(m.WeatherForecast.forecast_high_f)
                    .where(
                        m.WeatherForecast.event_ticker == st.event_ticker,
                        m.WeatherForecast.forecast_high_f.is_not(None),
                    )
                    .order_by(m.WeatherForecast.captured_at.desc())
                    .limit(1)
                )
                if fav is not None:
                    fc_stmt = fc_stmt.where(m.WeatherForecast.captured_at <= fav.created_at)
                fc = s.scalar(fc_stmt)

                if fc is None or fav is None:
                    agg["no_data"] += 1
                    continue
                nws_right = forecast_in_bucket(fc, st.actual_low_f, st.actual_high_f)
                market_right = fav.resolved_value == 100
                if nws_right and market_right:
                    agg["both"] += 1
                elif nws_right:
                    agg["nws_only"] += 1
                elif market_right:
                    agg["market_only"] += 1
                else:
                    agg["neither"] += 1
                c = per_city.setdefault(st.city or "?", [0, 0, 0])
                c[0] += int(nws_right)
                c[1] += int(market_right)
                c[2] += 1
                details.append(
                    (st.city, st.target_date, fc, st.winning_subtitle, "Y" if nws_right else "n", "Y" if market_right else "n")
                )

            scored = agg["both"] + agg["nws_only"] + agg["market_only"] + agg["neither"]
            print(f"  scored events: {scored}  (no data: {agg['no_data']})")
            if scored:
                print(f"  NWS correct   : {agg['both'] + agg['nws_only']}/{scored}")
                print(f"  market correct: {agg['both'] + agg['market_only']}/{scored}")
                print(f"  NWS-only-right: {agg['nws_only']}   market-only-right: {agg['market_only']}   "
                      f"both: {agg['both']}   neither: {agg['neither']}")
                print("  (NWS-only-right >> market-only-right over enough events => forecast edge)")

            if per_city:
                print("\n  by city (nws_right / market_right / n):")
                for city, (nw, mk, n) in sorted(per_city.items()):
                    print(f"    {city:5s} {nw}/{mk}/{n}")

            if details:
                print("\n  recent events (city, date, forecast, winning bucket, nws?, mkt?):")
                for d in details[-12:]:
                    print(f"    {d[0]:5s} {str(d[1]):10s} fc={d[2]}  won='{d[3]}'  nws={d[4]} mkt={d[5]}")

            # Raw forecast accuracy for this kind: degrees off + directional bias.
            # Uses the EARLIEST non-null forecast per event (the morning value you'd
            # trade on), so this is the tradeable accuracy, not a hindsight bound.
            print(f"\n=== NWS forecast accuracy — {kind.upper()} (earliest/morning forecast) ===")
            errs: list[float] = []
            hits = 0
            graded = 0
            acc_city: dict[str, list[float]] = {}
            for st in kind_settlements:
                actual = _settlement_actual(st)
                if actual is None:
                    continue
                fc = s.scalar(
                    select(m.WeatherForecast.forecast_high_f)
                    .where(
                        m.WeatherForecast.event_ticker == st.event_ticker,
                        m.WeatherForecast.forecast_high_f.is_not(None),
                    )
                    .order_by(m.WeatherForecast.captured_at.asc())
                    .limit(1)
                )
                if fc is None:
                    continue
                graded += 1
                signed = float(fc) - actual  # +ve => forecast runs warm
                errs.append(signed)
                hit = forecast_in_bucket(fc, st.actual_low_f, st.actual_high_f)
                if hit:
                    hits += 1
                c = acc_city.setdefault(st.city or "?", [0.0, 0.0, 0.0, 0.0])
                c[0] += abs(signed)
                c[1] += signed
                c[2] += 1
                c[3] += 1 if hit else 0
            if not graded:
                print("  (no settled events with a stored forecast yet)")
            else:
                mae = sum(abs(e) for e in errs) / graded
                bias = sum(errs) / graded
                within2 = sum(1 for e in errs if abs(e) <= 2.0) / graded * 100
                print(f"  graded events : {graded}")
                print(f"  bucket hit-rate: {hits}/{graded} ({hits / graded * 100:.0f}%)")
                print(f"  MAE           : {mae:.2f}°F   bias: {bias:+.2f}°F "
                      f"({'runs warm' if bias > 0 else 'runs cold' if bias < 0 else 'unbiased'})")
                print(f"  within 2°F    : {within2:.0f}%")
                print("  by city (MAE / bias / hit-rate / n):")
                for city, (sa, ss, n, h) in sorted(acc_city.items()):
                    print(f"    {city:5s} {sa / n:5.2f} / {ss / n:+5.2f} / "
                          f"{h / n * 100:3.0f}% / {int(n)}")

        # 4) Consistency — the goal is reliable small gains, so judge books on EV/trade,
        # variability and drawdown, NOT win-rate (a high win-rate on high-priced
        # favorites hides rare large losses, i.e. negative skew).
        print("\n=== Consistency by book (settled trades) ===")
        ctrades = s.execute(
            select(m.PaperTrade.strategy, m.PaperTrade.pnl, m.PaperTrade.closed_at)
            .where(m.PaperTrade.strategy.like("weather%"), m.PaperTrade.status == "settled",
                   m.PaperTrade.legacy.is_(False))
        ).all()
        all_books = [(k, b) for k in ("high", "low") for b in ("fav", "nws", "cal", "pm", "cwin")]
        book_pnls: dict[tuple[str, str], list[tuple]] = {kb: [] for kb in all_books}
        for strat, pnl, closed in ctrades:
            kb = _book(strat)
            if kb:
                book_pnls[kb].append((closed, float(pnl or 0.0)))
        if not any(book_pnls.values()):
            print("  (no settled weather trades yet)")
        else:
            print(f"  {'book':9s} {'n':>3} {'ev/trade':>9} {'stdev':>7} "
                  f"{'sharpe':>7} {'worst':>7} {'total':>8}")
            for kb in all_books:
                data = [p for _, p in book_pnls[kb]]
                n = len(data)
                if not n:
                    continue
                ev = sum(data) / n
                sd = (sum((x - ev) ** 2 for x in data) / n) ** 0.5
                sharpe = (ev / sd) if sd > 1e-9 else 0.0
                label = f"{kb[0][:2]}_{kb[1]}"
                print(f"  {label:9s} {n:3d} {ev:>+9.3f} {sd:>7.3f} {sharpe:>+7.2f} "
                      f"{min(data):>+7.2f} {sum(data):>+8.2f}")
            print("  (sharpe = ev/trade ÷ stdev, per-trade; higher = more consistent)")

            print("\n  daily P&L per book (worst day / max drawdown / up-days):")
            for kb in all_books:
                if not book_pnls[kb]:
                    continue
                daily: dict[str, float] = defaultdict(float)
                for closed, pnl in book_pnls[kb]:
                    daily[_day(closed)] += pnl
                series = [daily[k] for k in sorted(daily)]
                cum = peak = mdd = 0.0
                for x in series:
                    cum += x
                    peak = max(peak, cum)
                    mdd = min(mdd, cum - peak)
                up = sum(1 for x in series if x > 0)
                label = f"{kb[0][:2]}_{kb[1]}"
                print(f"    {label:9s} days={len(series):2d}  worst_day={_money(min(series))}  "
                      f"max_drawdown={_money(mdd)}  up_days={up}/{len(series)}")

        # 5) Data-collection health: is every dataset still flowing? (last 24h)
        print("\n=== Data collection (last 24h) ===")
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for label, model in (
            ("forecasts", m.WeatherForecast),
            ("observations", m.WeatherObservation),
            ("ensembles", m.WeatherEnsemble),
            ("bucket snapshots", m.WeatherBucketSnapshot),
            ("settlements", m.WeatherSettlement),
        ):
            n = s.scalar(
                select(func.count()).select_from(model).where(model.captured_at >= cutoff)
            )
            print(f"  {label:17s} {n or 0}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
