"""NWS (api.weather.gov) daily-high-temperature forecast client.

Free, no API key — just a descriptive `User-Agent`. Two steps (gridpoint cached
per location): `/points/{lat},{lon}` -> `properties.forecastGridData` URL ->
that gridpoint's `properties.maxTemperature.values` (degC time series). We pick the
entry for the target date and convert to degF.
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
        self._grid_cache: dict[tuple[float, float], str | None] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> NwsForecastClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _gridpoint_url(self, lat: float, lon: float) -> str | None:
        key = (round(lat, 4), round(lon, 4))
        if key in self._grid_cache:
            return self._grid_cache[key]
        resp = self._client.get(f"/points/{lat},{lon}")
        resp.raise_for_status()
        url = (resp.json().get("properties") or {}).get("forecastGridData")
        self._grid_cache[key] = url
        return url

    def daily_high_f(self, lat: float, lon: float, target_date: date) -> float | None:
        """Forecast daily high (degF) for target_date at the given location, or None."""
        url = self._gridpoint_url(lat, lon)
        if not url:
            return None
        resp = self._client.get(url)  # absolute URL -> base_url is ignored
        resp.raise_for_status()
        values = (
            ((resp.json().get("properties") or {}).get("maxTemperature") or {}).get("values") or []
        )
        for entry in values:
            start = str(entry.get("validTime", "")).split("/")[0]
            dt = parse_dt(start)
            if dt is not None and dt.date() == target_date and entry.get("value") is not None:
                return round(float(entry["value"]) * 9 / 5 + 32, 1)  # degC -> degF
        return None
