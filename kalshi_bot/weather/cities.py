"""Kalshi daily high-temperature cities and their NWS settlement stations.

Series tickers follow `KXHIGH<CITY>`; verify against live logs on first run (the
tracker logs whatever series actually return events). lat/lon are the NWS station
locations used for the forecast lookup, chosen to match Kalshi's settlement source.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    code: str  # short label, e.g. "NYC"
    name: str  # human name as it appears in Kalshi titles, e.g. "New York City"
    series_ticker: str  # Kalshi daily-high series, e.g. "KXHIGHNY"
    station: str  # NWS station id, e.g. "KNYC"
    lat: float
    lon: float


# Kalshi's daily high-temperature cities (settlement = NWS Daily Climate Report).
CITIES: list[City] = [
    City("NYC", "New York City", "KXHIGHNY", "KNYC", 40.7790, -73.9693),
    City("CHI", "Chicago", "KXHIGHCHI", "KMDW", 41.7860, -87.7524),
    City("MIA", "Miami", "KXHIGHMIA", "KMIA", 25.7959, -80.2870),
    City("AUS", "Austin", "KXHIGHAUS", "KAUS", 30.1975, -97.6664),
    City("LAX", "Los Angeles", "KXHIGHLAX", "KLAX", 33.9381, -118.3889),
    City("PHIL", "Philadelphia", "KXHIGHPHIL", "KPHL", 39.8729, -75.2437),
    City("DEN", "Denver", "KXHIGHDEN", "KDEN", 39.8467, -104.6562),
]
