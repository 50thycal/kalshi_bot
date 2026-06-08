"""Parse Kalshi temperature-bucket subtitles into numeric ranges (degF).

Examples:
    "74° to 75°"   -> (74.0, 75.0)
    "83° or below" -> (None, 83.0)
    "95° or above" -> (95.0, None)
"""

from __future__ import annotations

import re

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def parse_bucket_range(subtitle: str | None) -> tuple[float | None, float | None]:
    if not subtitle:
        return (None, None)
    s = subtitle.lower()
    nums = [float(x) for x in _NUM.findall(subtitle)]
    if any(w in s for w in ("below", "or less", "under", "<")):
        return (None, nums[0] if nums else None)
    if any(w in s for w in ("above", "or more", "over", ">")):
        return (nums[0] if nums else None, None)
    if len(nums) >= 2:
        return (min(nums[:2]), max(nums[:2]))
    if len(nums) == 1:
        return (nums[0], nums[0])
    return (None, None)


def forecast_in_bucket(
    forecast_high_f: float | None, low: float | None, high: float | None
) -> bool:
    """Does the (rounded) forecast high fall in [low, high] (open-ended sides allowed)?"""
    if forecast_high_f is None:
        return False
    r = round(forecast_high_f)
    if low is not None and r < low:
        return False
    if high is not None and r > high:
        return False
    return True
