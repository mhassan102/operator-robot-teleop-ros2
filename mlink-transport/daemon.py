"""Run loop for mlink-op / mlink-edge: one MlinkSession + localhost app face."""

from __future__ import annotations

import argparse
import logging
import select
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from proto.clock import Clock, SystemClock
from proto.config import MlinkConfig, load_config
from proto.header import PayloadTooLarge
from proto.session import MlinkSession
from proto.sockets import SocketFactory, UdpSocketFactory

log = logging.getLogger("mlink")


def parse_addr(s: str) -> tuple[str, int]:
    host, sep, port_s = s.rpartition(":")
    if not sep or not host:
        raise ValueError(f"address must be host:port, got {s!r}")
    port = int(port_s)
    if port < 1 or port > 65535:
        raise ValueError(f"port out of range in {s!r}")
    return host, port


class BlackholeSocket:
    """Drop all datagrams. Swapped in after a local path is killed."""

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        return len(data)

    def recvfrom(self) -> tuple[bytes, tuple[str, int]] | None:
        return None

    def close(self) -> None:
        return None

    def fileno(self) -> int:
        return -1


def disable_path(session: MlinkSession, name: str) -> None:
    """Close that path's UDP socket so copies on it vanish (not iptables)."""
    path = session.path(name)
    old = path.socket
    path.socket = BlackholeSocket()
    close = getattr(old, "close", None)
    if close is not None:
        try:
            close()
        except OSError:
            pass
    log.info("disabled path %s (socket closed)", name)


def _bind_udp(addr: tuple[str, int]) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(addr)
    sock.setblocking(False)
    return sock


def _recv_all(sock: socket.socket) -> list[tuple[bytes, tuple[str, int]]]:
    out: list[tuple[bytes, tuple[str, int]]] = []
    while True:
        try:
            out.append(sock.recvfrom(65535))
        except BlockingIOError:
            break
        except OSError:
            break
    return out


def _select_wait(socks: list[object], timeout: float) -> None:
    ready: list[object] = []
    for s in socks:
        fileno = getattr(s, "fileno", None)
        if fileno is None:
            continue
        try:
            fd = fileno()
        except OSError:
            continue
        if isinstance(fd, int) and fd >= 0:
            ready.append(s)
    if not ready:
        time.sleep(timeout)
        return
    try:
        select.select(ready, [], [], timeout)
    except (OSError, ValueError):
        time.sleep(timeout)


def run(
    config: MlinkConfig | str | Path,
    *,
    clock: Clock | None = None,
    socket_factory: SocketFactory | None = None,
    control_addr: tuple[str, int] | None = None,
    reflect: bool = False,
    stop: threading.Event | None = None,
    role: str = "mlink",
) -> None:
    if not isinstance(config, MlinkConfig):
        config = load_config(config)
    clock = clock or SystemClock()
    factory: SocketFactory = socket_factory or UdpSocketFactory()
    session = MlinkSession(config, clock, factory)

    listen_addr = parse_addr(config.listen_app)
    send_addr = parse_addr(config.send_app)
    app_rx = _bind_udp(listen_addr)
    app_tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    app_tx.setblocking(False)
    control = _bind_udp(control_addr) if control_addr else None

    log.info(
        "%s session=%s listen_app=%s send_app=%s reflect=%s paths=%s",
        role,
        config.session_id,
        config.listen_app,
        config.send_app,
        reflect,
        ", ".join(
            f"{p.name} if={p.ifname or '-'} "
            f"{p.bind_ip}:{p.bind_port}->{p.peer_ip}:{p.peer_port}"
            for p in session.paths
        ),
    )
    if control_addr:
        log.info("control %s:%s (UDP text: down <path>)", control_addr[0], control_addr[1])

    prev_up = {p.name: p.up for p in session.paths}
    last_stats = 0.0

    def cleanup() -> None:
        app_rx.close()
        app_tx.close()
        if control is not None:
            control.close()
        close_factory = getattr(factory, "close", None)
        if close_factory is not None:
            close_factory()
        else:
            for p in session.paths:
                c = getattr(p.socket, "close", None)
                if c is not None:
                    try:
                        c()
                    except OSError:
                        pass

    try:
        while stop is None or not stop.is_set():
            wait: list[object] = [app_rx]
            if control is not None:
                wait.append(control)
            wait.extend(session.paths[i].socket for i in range(len(session.paths)))
            _select_wait(wait, 0.01)

            for data, _addr in _recv_all(app_rx):
                try:
                    session.send(data)
                except PayloadTooLarge as exc:
                    log.warning("drop app datagram: %s", exc)

            if control is not None:
                for data, addr in _recv_all(control):
                    _handle_control(session, data, addr, control)

            session.tick()
            delivered = session.poll()
            for payload, winner in zip(delivered, session.last_poll_winners):
                name, path_id, seq = winner
                log.debug(
                    "first-good seq=%s path=%s path_id=%s bytes=%s",
                    seq,
                    name,
                    path_id,
                    len(payload),
                )
                if reflect:
                    try:
                        session.send(payload)
                    except PayloadTooLarge as exc:
                        log.warning("drop reflect: %s", exc)
                else:
                    try:
                        app_tx.sendto(payload, send_addr)
                    except OSError as exc:
                        log.warning("send_app failed: %s", exc)

            for p in session.paths:
                if p.up != prev_up[p.name]:
                    rtt = None if p.rtt_us is None else round(p.rtt_us)
                    log.info(
                        "path %s %s loss=%.2f rtt_us=%s",
                        p.name,
                        "up" if p.up else "down",
                        p.loss(),
                        rtt,
                    )
                    prev_up[p.name] = p.up

            now = time.monotonic()
            if now - last_stats >= 1.0:
                last_stats = now
                bits = []
                for p in session.paths:
                    rtt = "n/a" if p.rtt_us is None else f"{p.rtt_us / 1000:.2f}ms"
                    wins = session.delivered_by_path.get(p.name, 0)
                    bits.append(
                        f"{p.name}={'up' if p.up else 'down'} "
                        f"loss={p.loss():.2f} rtt={rtt} win={wins}"
                    )
                log.info("stats %s", " ".join(bits))
    finally:
        cleanup()


def _handle_control(
    session: MlinkSession,
    data: bytes,
    addr: tuple[str, int],
    control: socket.socket,
) -> None:
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return
    parts = text.split()
    cmd = parts[0].lower()
    if cmd == "down" and len(parts) >= 2:
        name = parts[1]
        try:
            disable_path(session, name)
            control.sendto(b"ok\n", addr)
        except KeyError:
            log.warning("control down unknown path %s", name)
            control.sendto(b"unknown path\n", addr)
    elif cmd == "status":
        lines = [
            f"{p.name} {'up' if p.up else 'down'} loss={p.loss():.2f}"
            for p in session.paths
        ]
        control.sendto(("\n".join(lines) + "\n").encode(), addr)
    else:
        log.warning("unknown control %r", text)
        control.sendto(b"unknown command\n", addr)


def _install_stop(stop: threading.Event) -> None:
    def _handler(signum: int, _frame: object) -> None:
        log.info("signal %s, stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _main(role: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"mlink-{role}")
    parser.add_argument("--config", required=True, help="YAML config path")
    parser.add_argument(
        "--control",
        default=None,
        help="UDP host:port for 'down <path>' (kill one local path)",
    )
    parser.add_argument(
        "--reflect",
        action="store_true",
        help="echo delivered payloads back through the session (far-side ping)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    control_addr = parse_addr(args.control) if args.control else None
    stop = threading.Event()
    _install_stop(stop)
    try:
        run(
            args.config,
            control_addr=control_addr,
            reflect=args.reflect,
            stop=stop,
            role=f"mlink-{role}",
        )
    except KeyboardInterrupt:
        return 0
    return 0


def main_op(argv: list[str] | None = None) -> int:
    return _main("op", argv)


def main_edge(argv: list[str] | None = None) -> int:
    return _main("edge", argv)


if __name__ == "__main__":
    sys.exit(main_op())
