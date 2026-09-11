"""Per-path up/down, loss, RTT, last-heard."""

from __future__ import annotations

from collections import deque
from typing import Any

from proto.config import PathConfig


class Path:
    def __init__(
        self,
        path_id: int,
        cfg: PathConfig,
        socket: Any,
        *,
        now_us: int,
        loss_window: int,
        rtt_alpha: float = 0.2,
    ) -> None:
        self.path_id = path_id
        self.name = cfg.name
        self.ifname = cfg.ifname
        self.bind_ip = cfg.bind_ip
        self.bind_port = cfg.bind_port if cfg.bind_port is not None else cfg.peer_port
        self.peer_ip = cfg.peer_ip
        self.peer_port = cfg.peer_port
        self.socket = socket

        self.up = True
        self.last_heard_us = now_us
        self.last_hb_tx_us = now_us
        self.last_probe_tx_us = now_us
        self.hb_seq = 0
        self.rtt_us: float | None = None
        self.rtt_alpha = rtt_alpha

        self._loss_window = loss_window
        self.hb_outcomes: deque[bool] = deque(maxlen=loss_window)
        self.data_outcomes: deque[bool] = deque(maxlen=loss_window)
        self._last_hb_rx_seq: int | None = None

    @property
    def peer_addr(self) -> tuple[str, int]:
        return (self.peer_ip, self.peer_port)

    def loss(self) -> float:
        """Inbound heartbeat loss over the sliding window.

        Empty / short window → 0 so a path is not excluded before we
        have samples. Scheduler uses this plus `up`.
        """
        if len(self.hb_outcomes) < self._loss_window:
            return 0.0
        hits = sum(1 for ok in self.hb_outcomes if ok)
        return 1.0 - (hits / len(self.hb_outcomes))

    def data_loss(self) -> float:
        if not self.data_outcomes:
            return 0.0
        hits = sum(1 for ok in self.data_outcomes if ok)
        return 1.0 - (hits / len(self.data_outcomes))

    def observe_heartbeat(self, seq: int) -> None:
        """Record inbound heartbeat/probe (not echoes) for loss."""
        if self._last_hb_rx_seq is None:
            if seq > 1:
                missed = min(seq - 1, self._loss_window)
                for _ in range(missed):
                    self.hb_outcomes.append(False)
            self.hb_outcomes.append(True)
            self._last_hb_rx_seq = seq
            return
        gap = seq - self._last_hb_rx_seq - 1
        if gap < 0:
            return
        for _ in range(gap):
            self.hb_outcomes.append(False)
        self.hb_outcomes.append(True)
        self._last_hb_rx_seq = seq

    def observe_data_outcome(self, received: bool) -> None:
        self.data_outcomes.append(received)

    def observe_rtt(self, sample_us: int) -> None:
        if sample_us < 0:
            return
        sample = float(sample_us)
        if self.rtt_us is None:
            self.rtt_us = sample
        else:
            a = self.rtt_alpha
            self.rtt_us = a * sample + (1.0 - a) * self.rtt_us

    def mark_heard(self, now_us: int) -> None:
        self.last_heard_us = now_us
        self.up = True
