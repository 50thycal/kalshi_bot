"""Open-Meteo ensemble forecast client (free, no API key).

Temperature buckets are priced off a *distribution*, not a point. Ensemble models run
the same forecast many times with perturbed initial conditions; the spread of member
daily highs/lows is an empirical distribution over the settled temperature — exactly
what's needed to estimate P(high lands in bucket) and spot an overconfident favorite.

One request per model: `GET /v1/ensemble` with hourly `temperature_2m`, `timezone` set
to the station's zone (so hourly timestamps are local), `temperature_unit=fahrenheit`.
The response carries one hourly series per ensemble member (`temperature_2m`,
`temperature_2m_member01`, ...); we reduce each member to its daily max/min for the
target local date.

Caveat: like the NWS gridded forecast, this is a model grid cell, not the settlement
station's micro-site — the same per-city bias the `cal` book corrects applies here and
can be learned offline from the stored members vs settlements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import httpx

logger = logging.getLogger(__name__)

ENSEMBLE_BASE = "https://ensemble-api.open-meteo.com"

# Require most of the local day before trusting a member's max/min (hourly steps).
_MIN_HOURS = 12


@dataclass(frozen=True)
class EnsembleDay:
    """Per-member daily extremes for one model on one local date."""

    model: str
    highs: list[float]  # one daily max per ensemble member, degF
    lows: list[float]  # one daily min per ensemble member, degF


class OpenMeteoEnsembleClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(base_url=ENSEMBLE_BASE, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenMeteoEnsembleClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def daily_extremes(
        self,
        lat: float,
        lon: float,
        tz_name: str,
        target_date: date,
        models: list[str],
    ) -> list[EnsembleDay]:
        """Per-member daily highs/lows for target_date, one EnsembleDay per model that
        returned usable data. Fails soft per model (a down model doesn't kill the rest)."""
        out: list[EnsembleDay] = []
        target = target_date.isoformat()
        for model in models:
            try:
                resp = self._client.get(
                    "/v1/ensemble",
                    params={
                        "latitude": lat,
                        "longitude": lon,
                        "hourly": "temperature_2m",
                        "models": model,
                        "forecast_days": 2,
                        "timezone": tz_name,
                        "temperature_unit": "fahrenheit",
                    },
                )
                resp.raise_for_status()
                hourly = (resp.json() or {}).get("hourly") or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ensemble fetch failed",
                    extra={"extra_fields": {"model": model, "error": str(exc)}},
                )
                continue
            times = hourly.get("time") or []
            idx = [i for i, ts in enumerate(times) if str(ts)[:10] == target]
            highs: list[float] = []
            lows: list[float] = []
            for key, series in hourly.items():
                # Every member arrives as its own series: temperature_2m, _member01, ...
                if not key.startswith("temperature_2m") or not isinstance(series, list):
                    continue
                values = [series[i] for i in idx if i < len(series) and series[i] is not None]
                if len(values) >= _MIN_HOURS:
                    highs.append(round(max(values), 1))
                    lows.append(round(min(values), 1))
            if highs:
                out.append(EnsembleDay(model=model, highs=highs, lows=lows))
        return out
