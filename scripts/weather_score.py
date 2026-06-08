"""Grade NWS forecast vs the market favorite on settled daily-temperature markets.

Read-only. Run once the day's markets have settled:

    DATABASE_URL=postgresql://... python scripts/weather_score.py
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import case, func, select  # noqa: E402

from kalshi_bot import db  # noqa: E402
from kalshi_bot import models as m  # noqa: E402
from kalshi_bot.config import normalize_database_url  # noqa: E402
from kalshi_bot.weather.buckets import forecast_in_bucket  # noqa: E402

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

        # 2) Head-to-head: NWS forecast vs market favorite on settled events.
        settlements = s.scalars(select(m.WeatherSettlement)).all()
        print(f"\n=== NWS vs market favorite ({len(settlements)} settled events) ===")
        agg = {"both": 0, "nws_only": 0, "market_only": 0, "neither": 0, "no_data": 0}
        per_city: dict[str, list[int]] = {}  # city -> [nws_right, market_right, n]
        details = []
        for st in settlements:
            # latest forecast for this event
            fc = s.scalar(
                select(m.WeatherForecast.forecast_high_f)
                .where(m.WeatherForecast.event_ticker == st.event_ticker)
                .order_by(m.WeatherForecast.captured_at.desc())
                .limit(1)
            )
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
