"""Place name for a robot whose saved location is blank.

One lookup at process start, from the robot's public address. A failure
leaves the location blank. The address and the raw reply are not printed.
"""

from __future__ import annotations

import json
import urllib.request

_MAX = 64
_SOURCES = (
    "https://ipwho.is/",
    "https://ipinfo.io/json",
)


def lookup_place(timeout: float = 3.0) -> str:
    """``City, Country`` for this machine, or ``""`` when the lookup fails.

    The first source returns a country name, so a robot in Dubai becomes
    ``Dubai, United Arab Emirates``. The second source is ipinfo.io, whose
    free reply carries the country code instead.
    """
    for url in _SOURCES:
        data = _fetch(url, timeout)
        if data is None or data.get("success") is False:
            continue
        text = _join(data.get("city"), data.get("country"))
        if text:
            return text
    return ""


def _fetch(url: str, timeout: float) -> dict | None:
    try:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "teleop-robot"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(8192)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _join(city: object, country: object) -> str:
    left = _piece(city)
    right = _piece(country)
    if left and right and left.casefold() != right.casefold():
        text = f"{left}, {right}"
    else:
        text = left or right
    return text[:_MAX]


def _piece(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text or "\x00" in text:
        return ""
    return text
