"""Grade NWS forecast vs the market favorite on settled daily-temperature markets.

Read-only. Run once the day's markets have settled:

    DATABASE_URL=postgresql://... python scripts/weather_score.py
"""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import case, func, select  # noqa: E402

from kalshi_bot import db  # noqa: E402
from kalshi_bot import models as m  # noqa: E402
from kalshi_bot.config import normalize_database_url  # noqa: E402
from kalshi_bot.weather.buckets import forecast_in_bucket, parse_bucket_range  # noqa: E402

CLOSED = ("settled", "closed_timeout", "closed_tp", "closed_sl", "closed_void", "abandoned")
_HOURS = re.compile(r"_h(\d+)$")


def _money(x) -> str:
    return f"${float(x or 0):,.2f}"


def main() -> int:
    url = normalize_database_url(os.environ.get("DATABASE_URL"))
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    db.init_engine(url)

    with db.session_scope() as s:
        # 1) Baseline (market favorite) by entry window.
        print("=== Buy-the-favorite baseline by entry window ===")
        rows = s.execute(
            select(
                m.PaperTrade.strategy,
                func.count(),
                func.sum(case((m.PaperTrade.resolved_value == 100, 1), else_=0)),
                func.coalesce(func.sum(m.PaperTrade.pnl), 0),
            )
            .where(m.PaperTrade.strategy.like("weather%"), m.PaperTrade.status == "settled")
            .group_by(m.PaperTrade.strategy)
            .order_by(m.PaperTrade.strategy)
        ).all()
        if not rows:
            print("  (no settled weather favorites yet)")
        for strat, n, wins, pnl in rows:
            wr = f"{(int(wins or 0) / n * 100):.0f}%" if n else "n/a"
            print(f"  {strat:18s} settled={n:4d}  win={wr:>4}  pnl={_money(pnl)}")

        # 1b) The headline: NWS vs favorite realized P&L, side by side, by window.
        print("\n=== NWS vs favorite by window (settled realized P&L) ===")
        by_win: dict[int, dict[str, tuple[int, float]]] = defaultdict(
            lambda: {"fav": (0, 0.0), "nws": (0, 0.0)}
        )
        for strat, n, _wins, pnl in rows:
            mobj = _HOURS.search(strat or "")
            if not mobj:
                continue
            w = int(mobj.group(1))
            book = "fav" if (strat or "").startswith("weather_fav") else (
                "nws" if (strat or "").startswith("weather_nws") else None
            )
            if book:
                by_win[w][book] = (int(n), float(pnl))
        if not by_win:
            print("  (no settled weather trades yet)")
        for w in sorted(by_win, reverse=True):
            f_n, f_pnl = by_win[w]["fav"]
            n_n, n_pnl = by_win[w]["nws"]
            spread = n_pnl - f_pnl
            flag = "  <-- nws ahead" if spread > 0 else ("  <-- fav ahead" if spread < 0 else "")
            print(
                f"  h{w:<3} fav={_money(f_pnl)} (n={f_n})   nws={_money(n_pnl)} (n={n_n})   "
                f"nws-fav={_money(spread)}{flag}"
            )

        # 2) Head-to-head: NWS forecast vs market favorite on settled events.
        settlements = s.scalars(select(m.WeatherSettlement)).all()
        print(f"\n=== NWS vs market favorite ({len(settlements)} settled events) ===")
        agg = {"both": 0, "nws_only": 0, "market_only": 0, "neither": 0, "no_data": 0}
        per_city: dict[str, list[int]] = {}  # city -> [nws_right, market_right, n]
        details = []
        for st in settlements:
            # earliest-window favorite trade for this event
            trades = s.scalars(
                select(m.PaperTrade)
                .where(
                    m.PaperTrade.market_ticker.like(f"{st.event_ticker}-%"),
                    m.PaperTrade.strategy.like("weather%"),
                    m.PaperTrade.status == "settled",
                )
            ).all()
            trades.sort(key=lambda t: -(int(_HOURS.search(t.strategy or "").group(1)) if _HOURS.search(t.strategy or "") else 0))
            fav = trades[0] if trades else None

            # Forecast made at/before that entry (the morning forecast, not the evening null).
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
            for d in details[-20:]:
                print(f"    {d[0]:5s} {str(d[1]):10s} fc={d[2]}  won='{d[3]}'  nws={d[4]} mkt={d[5]}")

        # 3) Raw forecast accuracy: how many degrees off is NWS from the actual high?
        # Uses the EARLIEST non-null forecast per event (the morning value you'd trade on),
        # so this is the tradeable accuracy, not a hindsight upper bound.
        print("\n=== NWS forecast accuracy vs actual high (earliest/morning forecast) ===")
        errs: list[float] = []
        hits = 0
        graded = 0
        acc_city: dict[str, list[float]] = {}  # city -> [sum_abs_err, sum_signed_err, n, hits]
        for st in settlements:
            low, high = (st.actual_low_f, st.actual_high_f)
            if low is None and high is None:
                low, high = parse_bucket_range(st.winning_subtitle)
            # bucket midpoint as the proxy for the realized station high
            if low is not None and high is not None:
                actual = (low + high) / 2.0
            elif high is not None:
                actual = high  # "X or below" -> use the edge
            elif low is not None:
                actual = low  # "X or above" -> use the edge
            else:
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
            signed = float(fc) - actual  # +ve => NWS forecast runs warm
            errs.append(signed)
            if forecast_in_bucket(fc, st.actual_low_f, st.actual_high_f):
                hits += 1
            c = acc_city.setdefault(st.city or "?", [0.0, 0.0, 0.0, 0.0])
            c[0] += abs(signed)
            c[1] += signed
            c[2] += 1
            c[3] += 1 if forecast_in_bucket(fc, st.actual_low_f, st.actual_high_f) else 0
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
