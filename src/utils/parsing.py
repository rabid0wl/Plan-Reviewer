"""Parsing helpers for civil station and offset formats."""

from __future__ import annotations

import re

STATION_PATTERN = re.compile(
    r"(?i)(?:STA\s*[:.]?\s*)?(\d{1,4})\s*\+\s*(\d{1,2}(?:\.\d+)?)"
)
OFFSET_PATTERN = re.compile(
    r"(?i)(-?\d+(?:\.\d+)?)\s*(?:'|FT|FEET)?\s*([LR](?:T)?)"
)


def parse_station(station_str: str) -> float | None:
    """
    Parse civil station format to decimal feet.

    Examples:
    - 16+82.45 -> 1682.45
    - STA: 16+82.45 -> 1682.45
    """
    match = STATION_PATTERN.search(station_str)
    if not match:
        return None

    station_major = float(match.group(1))
    station_minor = float(match.group(2))
    return station_major * 100.0 + station_minor


def parse_offset(offset_str: str) -> tuple[float, str] | None:
    """
    Parse offset string to (distance, direction).

    Examples:
    - 28.00' RT -> (28.0, "RT")
    - 45.00' RTGB -> (45.0, "RT")
    """
    normalized = offset_str.upper().replace(",", " ")
    match = OFFSET_PATTERN.search(normalized)
    if not match:
        return None

    distance = float(match.group(1))
    direction_raw = match.group(2).upper()
    direction = "RT" if direction_raw in {"R", "RT"} else "LT"
    return distance, direction


def parse_signed_offset(offset_str: str) -> float | None:
    """
    Parse offset string to signed decimal feet.

    Convention:
    - RT / R => positive
    - LT / L => negative
    """
    parsed = parse_offset(offset_str)
    if not parsed:
        return None
    distance, direction = parsed
    return distance if direction == "RT" else -distance
