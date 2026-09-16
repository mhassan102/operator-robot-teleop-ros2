"""Localhost UDP face onto mlink listen_app / send_app."""

from __future__ import annotations

import os
import select
import socket


def parse_addr(text: str) -> tuple[str, int]:
    host, sep, port_s = text.rpartition(":")
    if not sep or not host:
        raise ValueError(f"address must be host:port, got {text!r}")
    port = int(port_s)
    if port < 1 or port > 65535:
        raise ValueError(f"port out of range in {text!r}")
    if host.startswith("100."):
        raise ValueError(f"{text} looks like Tailscale; apps use 127.0.0.1")
    return host, port


def mlink_enabled() -> bool:
    return os.environ.get("TELEOP_MLINK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class MlinkUdp:
    """Send to mlink listen_* and bind mlink send_* on 127.0.0.1."""

    def __init__(self, tx: str, rx: str) -> None:
        self.tx_addr = parse_addr(tx)
        self.rx_addr = parse_addr(rx)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(self.rx_addr)
        self._sock.setblocking(False)

    def send(self, payload: bytes) -> None:
        self._sock.sendto(payload, self.tx_addr)

    def recv(self, timeout: float = 0.0) -> bytes | None:
        ready, _, _ = select.select([self._sock], [], [], timeout)
        if not ready:
            return None
        try:
            data, _addr = self._sock.recvfrom(65535)
        except BlockingIOError:
            return None
        except OSError:
            return None
        return data

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def open_from_env(*, default_tx: str, default_rx: str) -> MlinkUdp:
    tx = os.environ.get("TELEOP_MLINK_TX", default_tx).strip() or default_tx
    rx = os.environ.get("TELEOP_MLINK_RX", default_rx).strip() or default_rx
    return MlinkUdp(tx, rx)
