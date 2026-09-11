"""UDP sockets: fake in-memory (Stage 1) and real UDP (Stage 2–3).

Stage 2: bind + sendto/recvfrom; `ifname` unset → plain bind.
Stage 3: when `ifname` is set, SO_BINDTODEVICE on that socket.
"""

from __future__ import annotations

import errno
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


def bind_to_device(sock: socket.socket, ifname: str) -> None:
    """SO_BINDTODEVICE so this socket cannot leave `ifname`.

    Leave `ifname` unset in YAML to skip this (Stage 2 localhost).
    May need CAP_NET_ADMIN (or CAP_NET_RAW on older kernels). This lab's
    Ubuntu/Orin allow it unprivileged; EPERM means grant those caps, e.g.
    `sudo setcap cap_net_admin,cap_net_raw+ep $(readlink -f $(command -v python3))`.
    """
    if not ifname:
        return
    opt = getattr(socket, "SO_BINDTODEVICE", None)
    if opt is None:
        raise OSError(
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            "SO_BINDTODEVICE is not available; leave ifname unset for plain bind",
        )
    try:
        sock.setsockopt(socket.SOL_SOCKET, opt, ifname.encode("ascii"))
    except OSError as exc:
        extra = ""
        if exc.errno == errno.EPERM:
            extra = (
                " Need CAP_NET_ADMIN (CAP_NET_RAW on older kernels). "
                "Grant with setcap on python3 or run with those capabilities. "
                "Leave ifname unset to skip (Stage 2 bind)."
            )
        elif exc.errno == errno.ENODEV:
            extra = f" Interface {ifname!r} does not exist."
        raise OSError(
            exc.errno,
            f"SO_BINDTODEVICE {ifname!r} failed: {exc.strerror}.{extra}",
        ) from exc


def bound_device_name(sock: socket.socket) -> str | None:
    """Interface name this UDP socket is bound to, or None."""
    opt = getattr(socket, "SO_BINDTODEVICE", None)
    if opt is None:
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, opt, 16)
    except OSError:
        return None
    name = raw.split(b"\0", 1)[0].decode("ascii", errors="replace")
    return name or None


class UdpSocket:
    """Non-blocking real UDP. SO_BINDTODEVICE when factory was given ifname."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        bind_addr: tuple[str, int],
        link: str,
        ifname: str | None = None,
    ) -> None:
        self._sock = sock
        self.bind_addr = bind_addr
        self.link = link
        self.ifname = ifname
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

    def bound_device(self) -> str | None:
        if self._closed:
            return None
        return bound_device_name(self._sock)


class UdpSocketFactory:
    """Real UDP SocketFactory. SO_BINDTODEVICE when path.ifname is set."""

    def __init__(self) -> None:
        self._created: list[UdpSocket] = []

    def create(self, path: PathConfig) -> UdpSocket:
        port = path.bind_port if path.bind_port is not None else path.peer_port
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if path.ifname:
                bind_to_device(sock, path.ifname)
            sock.bind((path.bind_ip, port))
            sock.setblocking(False)
        except OSError as exc:
            sock.close()
            if exc.errno == errno.EADDRNOTAVAIL:
                iface = f" on {path.ifname}" if path.ifname else ""
                raise OSError(
                    exc.errno,
                    f"Cannot bind {path.bind_ip}:{port}{iface}: that address "
                    f"is not assigned on this machine. Run `ip -br addr` and "
                    f"set bind_ip in this YAML (and the peer's `peer:` for "
                    f"this path) to the live address.",
                ) from exc
            raise
        udp = UdpSocket(
            sock,
            bind_addr=(path.bind_ip, port),
            link=path.name,
            ifname=path.ifname,
        )
        self._created.append(udp)
        return udp

    def close(self) -> None:
        for sock in self._created:
            sock.close()
        self._created.clear()
