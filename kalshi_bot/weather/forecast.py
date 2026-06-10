"""NWS (api.weather.gov) forecast + station-observation client.

Free, no API key — just a descriptive `User-Agent`.

Forecasts: two steps (forecast URL cached per location): `/points/{lat},{lon}` ->
`properties.forecast` URL -> that forecast's `properties.periods`.
- Daily HIGH for date D: the **daytime** period starting on D (`temperature` is the
  forecast high, already degF).
- Daily LOW for date D: the **overnight** (non-daytime) period **ending** on D — the
  night leading into D's morning, where the daily minimum usually falls. Once that
  night has passed, NWS no longer serves the period and the low forecast for D is
  unavailable (the intraday observations then carry the signal).

This uses the daily-periods forecast rather than the raw `maxTemperature` grid series:
the periods are clearly day-labelled in local time with an `isDaytime` flag, which avoids
the date/timezone ambiguities that produced bad highs for some grids (e.g. Denver).

Observations: `/stations/{id}/observations` over the station's local calendar day ->
running max/min observed so far today (the settlement station's actual readings; by
mid-afternoon the day's high is often already locked in). Note: NWS climate reports
technically use local *standard* time for the climate day; we use the local clock day,
which only differs in rare boundary cases.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from ..scanner.metrics import parse_dt

logger = logging.getLogger(__name__)

NWS_BASE = "https://api.weather.gov"


@dataclass(frozen=True)
class ObservedExtremes:
    """Station readings so far in the local day."""

    max_f: float
    min_f: float
    obs_count: int
    last_obs_at: datetime | None


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

    def _periods(self, lat: float, lon: float) -> list[dict]:
        url = self._forecast_url(lat, lon)
        if not url:
            return []
        resp = self._client.get(url)  # absolute URL -> base_url is ignored
        resp.raise_for_status()
        return (resp.json().get("properties") or {}).get("periods") or []

    @staticmethod
    def _period_temp_f(period: dict) -> float | None:
        if period.get("temperature") is None:
            return None
        temp = float(period["temperature"])
        unit = (period.get("temperatureUnit") or "F").upper()
        return round(temp * 9 / 5 + 32, 1) if unit == "C" else round(temp, 1)

    def daily_high_f(self, lat: float, lon: float, target_date: date) -> float | None:
        """Forecast daily high (degF) for target_date at the given location, or None."""
        for period in self._periods(lat, lon):
            if not period.get("isDaytime"):
                continue
            start = parse_dt(period.get("startTime"))
            if start is not None and start.date() == target_date:
                temp = self._period_temp_f(period)
                if temp is not None:
                    return temp
        return None

    def daily_low_f(self, lat: float, lon: float, target_date: date) -> float | None:
        """Forecast daily low (degF) for target_date: the overnight period ending on
        target_date. None once that night has passed (or no data)."""
        for period in self._periods(lat, lon):
            if period.get("isDaytime"):
                continue
            end = parse_dt(period.get("endTime"))
            if end is not None and end.date() == target_date:
                temp = self._period_temp_f(period)
                if temp is not None:
                    return temp
        return None

    def observed_extremes_f(
        self, station: str, target_date: date, tz_name: str
    ) -> ObservedExtremes | None:
        """Max/min temperature observed at the station so far during target_date's
        local calendar day, or None when there are no usable observations yet."""
        tz = ZoneInfo(tz_name)
        start_local = datetime.combine(target_date, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)

        def _z(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        resp = self._client.get(
            f"/stations/{station}/observations",
            params={"start": _z(start_local), "end": _z(end_local), "limit": 500},
        )
        resp.raise_for_status()
        temps: list[float] = []
        last_at: datetime | None = None
        for feature in resp.json().get("features") or []:
            props = feature.get("properties") or {}
            temp_obj = props.get("temperature") or {}
            value = temp_obj.get("value")
            ts = parse_dt(props.get("timestamp"))
            if value is None or ts is None:
                continue  # QC-failed / missing readings come back as null
            local = ts.astimezone(tz)
            if not (start_local <= local < end_local):
                continue
            unit = (temp_obj.get("unitCode") or "").lower()
            value_f = float(value) * 9 / 5 + 32 if "degc" in unit else float(value)
            temps.append(round(value_f, 1))
            if last_at is None or ts > last_at:
                last_at = ts
        if not temps:
            return None
        return ObservedExtremes(
            max_f=max(temps), min_f=min(temps), obs_count=len(temps), last_obs_at=last_at
        )
