"""UDP sockets: fake in-memory (Stage 1) and real localhost (Stage 2).

Stage 2 binds + sendto/recvfrom. `ifname` is ignored; SO_BINDTODEVICE is Stage 3.
"""

from __future__ import annotations

import socket
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

from proto.config import PathConfig
from proto.header import FLAG_ECHO, FLAG_HEARTBEAT, FLAG_PROBE


class DatagramSocket(Protocol):
    def sendto(self, data: bytes, addr: tuple[str, int]) -> int: ...
    def recvfrom(self) -> tuple[bytes, tuple[str, int]] | None: ...


class SocketFactory(Protocol):
    def create(self, path: PathConfig) -> DatagramSocket: ...


DropFn = Callable[[bytes], bool]


def spaced_drop(rate: float, period: int = 10) -> DropFn:
    """Drop `round(rate * period)` of every `period` packets, spaced out.

    30% over 10 packets → 3 drops that are not consecutive, so a 300 ms
    heartbeat timeout does not fire on a merely lossy path.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    n_drop = int(round(rate * period))
    indices: set[int] = set()
    if n_drop > 0:
        stride = period / n_drop
        for k in range(n_drop):
            indices.add(int(k * stride) % period)

    state = {"i": 0}

    def _drop(_data: bytes = b"") -> bool:
        i = state["i"]
        state["i"] = i + 1
        return (i % period) in indices

    return _drop


def drop_heartbeats(rate: float, period: int = 10) -> DropFn:
    """Apply `spaced_drop` only to non-echo heartbeat/probe frames."""
    inner = spaced_drop(rate, period)

    def _drop(data: bytes) -> bool:
        if len(data) < 6:
            return False
        flags = data[5]
        if flags & (FLAG_HEARTBEAT | FLAG_PROBE) and not (flags & FLAG_ECHO):
            return inner(data)
        return False

    return _drop


@dataclass
class WireEvent:
    link: str
    src: tuple[str, int]
    dst: tuple[str, int]
    data: bytes
    dropped: bool
    reason: str | None = None


class FakeSocket:
    def __init__(
        self,
        network: FakeNetwork,
        bind_addr: tuple[str, int],
        link: str,
    ) -> None:
        self.network = network
        self.bind_addr = bind_addr
        self.link = link
        self.inbox: deque[tuple[bytes, tuple[str, int]]] = deque()

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        return self.network.transmit(self, data, addr)

    def recvfrom(self) -> tuple[bytes, tuple[str, int]] | None:
        if not self.inbox:
            return None
        return self.inbox.popleft()


@dataclass
class FakeNetwork:
    """Synchronous in-memory UDP. `link` is the path name (eth/wifi/...)."""

    down_links: set[str] = field(default_factory=set)
    loss_models: dict[str, DropFn] = field(default_factory=dict)
    rx_loss: dict[tuple[str, int], DropFn] = field(default_factory=dict)
    log: list[WireEvent] = field(default_factory=list)
    _by_addr: dict[tuple[str, int], FakeSocket] = field(default_factory=dict)

    def bind(self, bind_ip: str, bind_port: int, link: str) -> FakeSocket:
        addr = (bind_ip, bind_port)
        if addr in self._by_addr:
            raise ValueError(f"address already bound: {addr}")
        sock = FakeSocket(self, addr, link)
        self._by_addr[addr] = sock
        return sock

    def set_down(self, link: str) -> None:
        self.down_links.add(link)

    def set_up(self, link: str) -> None:
        self.down_links.discard(link)

    def set_loss(self, link: str, drop: DropFn | None) -> None:
        if drop is None:
            self.loss_models.pop(link, None)
        else:
            self.loss_models[link] = drop

    def set_rx_loss(self, addr: tuple[str, int], drop: DropFn | None) -> None:
        if drop is None:
            self.rx_loss.pop(addr, None)
        else:
            self.rx_loss[addr] = drop

    def transmit(
        self, src: FakeSocket, data: bytes, dst: tuple[str, int]
    ) -> int:
        dropped = False
        reason: str | None = None
        if src.link in self.down_links:
            dropped = True
            reason = "down"
        elif src.link in self.loss_models and self.loss_models[src.link](data):
            dropped = True
            reason = "loss"
        elif dst in self.rx_loss and self.rx_loss[dst](data):
            dropped = True
            reason = "rx_loss"
        event = WireEvent(
            link=src.link,
            src=src.bind_addr,
            dst=dst,
            data=data,
            dropped=dropped,
            reason=reason,
        )
        self.log.append(event)
        if not dropped:
            dest = self._by_addr.get(dst)
            if dest is None:
                event.reason = "no_dest"
            else:
                dest.inbox.append((data, src.bind_addr))
        return len(data)

    def clear_log(self) -> None:
        self.log.clear()


class FakeSocketFactory:
    def __init__(self, network: FakeNetwork) -> None:
        self.network = network

    def create(self, path: PathConfig) -> FakeSocket:
        port = path.bind_port if path.bind_port is not None else path.peer_port
        return self.network.bind(path.bind_ip, port, path.name)


class UdpSocket:
    """Non-blocking real UDP. No SO_BINDTODEVICE."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        bind_addr: tuple[str, int],
        link: str,
    ) -> None:
        self._sock = sock
        self.bind_addr = bind_addr
        self.link = link
        self._closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        if self._closed:
            return 0
        try:
            return self._sock.sendto(data, addr)
        except OSError:
            # ICMP port-unreachable after a peer close, or a closed fd.
            return 0

    def recvfrom(self) -> tuple[bytes, tuple[str, int]] | None:
        if self._closed:
            return None
        try:
            data, addr = self._sock.recvfrom(65535)
        except BlockingIOError:
            return None
        except OSError:
            return None
        return data, (addr[0], int(addr[1]))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    def fileno(self) -> int:
        if self._closed:
            return -1
        try:
            return self._sock.fileno()
        except OSError:
            return -1


class UdpSocketFactory:
    """Real UDP SocketFactory. Stage 2: localhost only; ifname is ignored."""

    def __init__(self) -> None:
        self._created: list[UdpSocket] = []

    def create(self, path: PathConfig) -> UdpSocket:
        port = path.bind_port if path.bind_port is not None else path.peer_port
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Stage 3 will SO_BINDTODEVICE when ifname is set. Not here.
        sock.bind((path.bind_ip, port))
        sock.setblocking(False)
        udp = UdpSocket(sock, bind_addr=(path.bind_ip, port), link=path.name)
        self._created.append(udp)
        return udp

    def close(self) -> None:
        for sock in self._created:
            sock.close()
        self._created.clear()
