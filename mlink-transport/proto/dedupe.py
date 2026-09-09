"""First-good copy wins. No reorder hold."""

from __future__ import annotations

from typing import Literal

Verdict = Literal["deliver", "duplicate", "late"]


class DedupeWindow:
    """Dedupe by data seq (session is checked by the caller).

    A seq is late when `highest - seq >= window`. Gaps are allowed:
    delivering seq=5 does not wait for seq=4, and seq=4 is still
    deliverable if it arrives before it falls out of the window.
    """

    def __init__(self, window: int = 1024) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self.highest: int | None = None
        self._delivered: set[int] = set()

    def observe(self, seq: int) -> Verdict:
        if self.highest is not None and self.highest - seq >= self.window:
            return "late"
        if seq in self._delivered:
            return "duplicate"
        self._delivered.add(seq)
        if self.highest is None or seq > self.highest:
            self.highest = seq
            cutoff = self.highest - self.window
            if cutoff >= 0 and len(self._delivered) > self.window:
                self._delivered = {s for s in self._delivered if s > cutoff}
        return "deliver"
