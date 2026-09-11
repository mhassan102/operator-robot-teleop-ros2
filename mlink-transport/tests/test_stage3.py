"""Stage 3: SO_BINDTODEVICE when ifname is set. No real eth/wifi required."""

from __future__ import annotations

import errno
import socket
from pathlib import Path

import pytest

from proto.clock import FakeClock
from proto.config import PathConfig, load_config
from proto.sockets import FakeNetwork, UdpSocketFactory, bind_to_device
from tests.util import make_pair, reserve_udp_ports

ROOT = Path(__file__).resolve().parents[1]


def test_lab_yaml_pair_is_complementary() -> None:
    op = load_config(ROOT / "config" / "lab-op.yaml")
    edge = load_config(ROOT / "config" / "lab-edge.yaml")
    assert op.session_id == edge.session_id == 1
    assert [p.name for p in op.paths] == ["eth", "wifi"]
    assert [p.name for p in edge.paths] == ["eth", "wifi"]
    assert op.paths[0].ifname == "enx00e04c681cc3"
    assert op.paths[1].ifname == "wlo1"
    assert edge.paths[0].ifname == "eno1"
    assert edge.paths[1].ifname == "wlP1p1s0"
    for a, b in zip(op.paths, edge.paths):
        assert a.bind_ip == b.peer_ip
        assert a.peer_ip == b.bind_ip
        assert a.bind_port == b.peer_port
        assert a.peer_port == b.bind_port
        assert not a.bind_ip.startswith("100.")
        assert not a.peer_ip.startswith("100.")
        assert a.ifname != "tailscale0"
        assert b.ifname != "tailscale0"
    assert {op.listen_app, op.send_app, edge.listen_app, edge.send_app} == {
        "127.0.0.1:5501",
        "127.0.0.1:5502",
        "127.0.0.1:5503",
        "127.0.0.1:5504",
    }


def test_udp_factory_unknown_ifname_raises() -> None:
    port = reserve_udp_ports(1)[0]
    cfg = PathConfig(
        name="eth",
        bind_ip="127.0.0.1",
        bind_port=port,
        peer_ip="127.0.0.1",
        peer_port=1,
        ifname="this_iface_does_not_exist",
    )
    factory = UdpSocketFactory()
    try:
        with pytest.raises(OSError) as excinfo:
            factory.create(cfg)
        assert excinfo.value.errno == errno.ENODEV
        assert "this_iface_does_not_exist" in str(excinfo.value)
    finally:
        factory.close()


def test_udp_factory_bindtodevice_lo() -> None:
    port = reserve_udp_ports(1)[0]
    cfg = PathConfig(
        name="lo",
        bind_ip="127.0.0.1",
        bind_port=port,
        peer_ip="127.0.0.1",
        peer_port=1,
        ifname="lo",
    )
    factory = UdpSocketFactory()
    try:
        sock = factory.create(cfg)
        assert sock.ifname == "lo"
        assert sock.bound_device() == "lo"
        assert sock.bind_addr == ("127.0.0.1", port)
    except OSError as exc:
        if exc.errno == errno.EPERM:
            pytest.skip("SO_BINDTODEVICE needs CAP_NET_ADMIN")
        raise
    finally:
        factory.close()


def test_udp_factory_sets_bindtodevice_only_when_ifname_set(monkeypatch) -> None:
    seen: list[tuple] = []
    real_socket = socket.socket

    class Spy(socket.socket):
        def setsockopt(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(args)
            return super().setsockopt(*args, **kwargs)

    monkeypatch.setattr("proto.sockets.socket.socket", Spy)
    opt = socket.SO_BINDTODEVICE
    port_a, port_b = reserve_udp_ports(2)

    factory = UdpSocketFactory()
    try:
        factory.create(
            PathConfig(
                name="plain",
                bind_ip="127.0.0.1",
                bind_port=port_a,
                peer_ip="127.0.0.1",
                peer_port=1,
                ifname=None,
            )
        )
        device_calls = [c for c in seen if len(c) >= 2 and c[1] == opt]
        assert device_calls == []
        seen.clear()
        factory.create(
            PathConfig(
                name="lo",
                bind_ip="127.0.0.1",
                bind_port=port_b,
                peer_ip="127.0.0.1",
                peer_port=1,
                ifname="lo",
            )
        )
        device_calls = [c for c in seen if len(c) >= 2 and c[1] == opt]
        assert device_calls, "SO_BINDTODEVICE must be set when ifname is set"
        assert device_calls[0][2] == b"lo"
    except OSError as exc:
        if exc.errno == errno.EPERM:
            pytest.skip("SO_BINDTODEVICE needs CAP_NET_ADMIN")
        raise
    finally:
        factory.close()
        monkeypatch.setattr("proto.sockets.socket.socket", real_socket)


def test_session_records_first_good_path_winner(clock: FakeClock, net: FakeNetwork) -> None:
    a, b = make_pair(clock, net)
    a.send(b"one")
    got = b.poll()
    assert got == [b"one"]
    assert b.last_poll_winners == [("eth", 0, 1)]
    assert b.delivered_by_path["eth"] == 1
    assert b.delivered_by_path["wifi"] == 0
    # Duplicate of seq=1 on wifi is dropped; winner counts unchanged.
    leftover = b.poll()
    assert leftover == []
    assert b.delivered_by_path["eth"] == 1


def test_bind_to_device_empty_ifname_is_noop() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        bind_to_device(sock, "")
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, 16)
        assert raw.split(b"\0", 1)[0] == b""
    finally:
        sock.close()
