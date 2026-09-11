"""Injectable clocks. Tests use FakeClock; production will use SystemClock."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def monotonic_us(self) -> int:
        """Monotonic microseconds (not wall clock)."""


class SystemClock:
    def monotonic_us(self) -> int:
        return time.monotonic_ns() // 1000


class FakeClock:
    def __init__(self, start_us: int = 0) -> None:
        self._now = int(start_us)

    def monotonic_us(self) -> int:
        return self._now

    def advance(self, us: int) -> None:
        if us < 0:
            raise ValueError("cannot go backwards")
        self._now += int(us)

    def set(self, us: int) -> None:
        self._now = int(us)
