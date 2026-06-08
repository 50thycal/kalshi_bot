"""NWS (api.weather.gov) daily-high-temperature forecast client.

Free, no API key — just a descriptive `User-Agent`. Two steps (forecast URL cached
per location): `/points/{lat},{lon}` -> `properties.forecast` URL -> that forecast's
`properties.periods`; we take the **daytime** period for the target date, whose
`temperature` is the forecast daily high (already in degF).

This uses the daily-periods forecast rather than the raw `maxTemperature` grid series:
the periods are clearly day-labelled in local time with an `isDaytime` flag, which avoids
the date/timezone ambiguities that produced bad highs for some grids (e.g. Denver).
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from ..scanner.metrics import parse_dt

logger = logging.getLogger(__name__)

NWS_BASE = "https://api.weather.gov"


class NwsForecastClient:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=NWS_BASE,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/geo+json"},
            transport=transport,
        )
        self._forecast_cache: dict[tuple[float, float], str | None] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> NwsForecastClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _forecast_url(self, lat: float, lon: float) -> str | None:
        key = (round(lat, 4), round(lon, 4))
        if key in self._forecast_cache:
            return self._forecast_cache[key]
        resp = self._client.get(f"/points/{lat},{lon}")
        resp.raise_for_status()
        url = (resp.json().get("properties") or {}).get("forecast")
        self._forecast_cache[key] = url
        return url

    def daily_high_f(self, lat: float, lon: float, target_date: date) -> float | None:
        """Forecast daily high (degF) for target_date at the given location, or None."""
        url = self._forecast_url(lat, lon)
        if not url:
            return None
        resp = self._client.get(url)  # absolute URL -> base_url is ignored
        resp.raise_for_status()
        periods = (resp.json().get("properties") or {}).get("periods") or []
        for period in periods:
            if not period.get("isDaytime"):
                continue
            start = parse_dt(period.get("startTime"))
            if start is not None and start.date() == target_date and period.get("temperature") is not None:
                temp = float(period["temperature"])
                unit = (period.get("temperatureUnit") or "F").upper()
                return round(temp * 9 / 5 + 32, 1) if unit == "C" else round(temp, 1)
        return None
