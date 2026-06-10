"""Kalshi daily temperature cities and their NWS settlement stations.

Each city has a daily HIGH series (`KXHIGH*`) and a daily LOW series (`KXLOWT*`).
Tickers verified against kalshi.com (note the quirks: lows are `KXLOWT` + suffix,
and NYC's low suffix is `NYC` while its high suffix is `NY`). AUS/PHIL lows follow
the same pattern but weren't individually confirmed — verify against the first
run's logs and override via WEATHER_LOW_SERIES (e.g. "AUS=KXLOWTAUSTIN") if needed.

lat/lon are the NWS station locations used for forecast lookups, chosen to match
Kalshi's settlement source (the NWS Daily Climate Report at `station`). `tz` is the
station's IANA timezone, used to bound "today" for intraday station observations
and ensemble daily extremes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    code: str  # short label, e.g. "NYC"
    name: str  # human name as it appears in Kalshi titles, e.g. "New York City"
    series_high: str  # Kalshi daily-high series, e.g. "KXHIGHNY"
    series_low: str | None  # Kalshi daily-low series, e.g. "KXLOWTNYC"
    station: str  # NWS station id, e.g. "KNYC"
    lat: float
    lon: float
    tz: str  # IANA timezone of the station's local day


# Kalshi's daily temperature cities (settlement = NWS Daily Climate Report).
CITIES: list[City] = [
    City("NYC", "New York City", "KXHIGHNY", "KXLOWTNYC", "KNYC", 40.7790, -73.9693, "America/New_York"),
    City("CHI", "Chicago", "KXHIGHCHI", "KXLOWTCHI", "KMDW", 41.7860, -87.7524, "America/Chicago"),
    City("MIA", "Miami", "KXHIGHMIA", "KXLOWTMIA", "KMIA", 25.7959, -80.2870, "America/New_York"),
    City("AUS", "Austin", "KXHIGHAUS", "KXLOWTAUS", "KAUS", 30.1975, -97.6664, "America/Chicago"),
    City("LAX", "Los Angeles", "KXHIGHLAX", "KXLOWTLAX", "KLAX", 33.9381, -118.3889, "America/Los_Angeles"),
    City("PHIL", "Philadelphia", "KXHIGHPHIL", "KXLOWTPHIL", "KPHL", 39.8729, -75.2437, "America/New_York"),
    City("DEN", "Denver", "KXHIGHDEN", "KXLOWTDEN", "KDEN", 39.8467, -104.6562, "America/Denver"),
]
