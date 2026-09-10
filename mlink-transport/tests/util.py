"""Test helpers shared by unit tests."""

from __future__ import annotations

import socket

from proto.clock import FakeClock
from proto.config import MlinkConfig, PathConfig
from proto.session import MlinkSession
from proto.sockets import FakeNetwork, FakeSocketFactory


def path_cfg(name: str, bind_port: int, peer_port: int) -> PathConfig:
    return PathConfig(
        name=name,
        bind_ip="127.0.0.1",
        bind_port=bind_port,
        peer_ip="127.0.0.1",
        peer_port=peer_port,
        ifname=None,
    )


def pair_configs(
    names: tuple[str, ...] = ("eth", "wifi"),
    **kwargs: object,
) -> tuple[MlinkConfig, MlinkConfig]:
    a_paths = []
    b_paths = []
    for i, name in enumerate(names):
        a_bind = 41001 + i
        b_bind = 42001 + i
        a_paths.append(path_cfg(name, a_bind, b_bind))
        b_paths.append(path_cfg(name, b_bind, a_bind))
    defaults: dict = dict(
        session_id=1,
        listen_app="127.0.0.1:5501",
        send_app="127.0.0.1:5502",
        loss_window=20,
        heartbeat_interval_us=100_000,
        down_timeout_us=300_000,
        probe_interval_us=1_000_000,
        loss_threshold=0.20,
        dedupe_window=1024,
    )
    defaults.update(kwargs)
    cfg_a = MlinkConfig(paths=tuple(a_paths), **defaults)
    cfg_b = MlinkConfig(paths=tuple(b_paths), **defaults)
    return cfg_a, cfg_b


def make_pair(
    clock: FakeClock,
    net: FakeNetwork,
    names: tuple[str, ...] = ("eth", "wifi"),
    **kwargs: object,
) -> tuple[MlinkSession, MlinkSession]:
    cfg_a, cfg_b = pair_configs(names, **kwargs)
    factory = FakeSocketFactory(net)
    return MlinkSession(cfg_a, clock, factory), MlinkSession(cfg_b, clock, factory)


def settle(*nodes: MlinkSession) -> dict[int, list[bytes]]:
    """Drain sockets several times so heartbeat echoes complete."""
    got = {id(n): [] for n in nodes}
    for _ in range(4):
        for n in nodes:
            got[id(n)].extend(n.poll())
    return got


def reserve_udp_ports(n: int) -> list[int]:
    """Pick `n` free localhost UDP ports (best-effort; tiny bind race)."""
    socks: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(n):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.1", 0))
            ports.append(int(s.getsockname()[1]))
            socks.append(s)
    finally:
        for s in socks:
            s.close()
    return ports


def pump(
    a: MlinkSession,
    b: MlinkSession,
    clock: FakeClock,
    steps: int,
    step_us: int = 100_000,
) -> None:
    for _ in range(steps):
        clock.advance(step_us)
        a.tick()
        b.tick()
        settle(a, b)
