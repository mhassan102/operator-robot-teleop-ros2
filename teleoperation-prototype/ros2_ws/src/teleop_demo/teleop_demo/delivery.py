"""Sequence tracking and latency statistics for stamped teleop commands."""

from __future__ import annotations

from typing import Optional


def time_msg_to_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


class DeliveryTracker:
    """Track received, missing, duplicate, and out-of-order sequence numbers."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.received = 0
        self.duplicates = 0
        self.out_of_order = 0
        self.seen: set[int] = set()
        self.lowest: Optional[int] = None
        self.highest: Optional[int] = None

    @property
    def unique(self) -> int:
        return len(self.seen)

    @property
    def missing(self) -> int:
        if self.highest is None or self.lowest is None:
            return 0
        return (self.highest - self.lowest + 1) - self.unique

    def observe(self, sequence: int) -> None:
        self.received += 1
        if sequence in self.seen:
            self.duplicates += 1
            return
        if self.highest is not None and sequence < self.highest:
            self.out_of_order += 1
        self.seen.add(sequence)
        if self.lowest is None or sequence < self.lowest:
            self.lowest = sequence
        if self.highest is None or sequence > self.highest:
            self.highest = sequence


class LatencyStats:
    """Current, minimum, maximum, and average latency in nanoseconds."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.count = 0
        self.total_ns = 0
        self.current_ns = 0
        self._min_ns: Optional[int] = None
        self._max_ns: Optional[int] = None

    @property
    def min_ns(self) -> int:
        return 0 if self._min_ns is None else self._min_ns

    @property
    def max_ns(self) -> int:
        return 0 if self._max_ns is None else self._max_ns

    @property
    def average_ns(self) -> int:
        if self.count == 0:
            return 0
        return self.total_ns // self.count

    def add(self, latency_ns: int) -> None:
        self.count += 1
        self.current_ns = latency_ns
        self.total_ns += latency_ns
        if self._min_ns is None or latency_ns < self._min_ns:
            self._min_ns = latency_ns
        if self._max_ns is None or latency_ns > self._max_ns:
            self._max_ns = latency_ns
