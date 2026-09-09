"""Stage 1 contract tests: fake clock + fake sockets, no real NICs."""

from __future__ import annotations

import pytest

from proto.header import (
    FLAG_PROBE,
    TC_CONTROL,
    TC_MEDIA,
    Packet,
    PayloadTooLarge,
    decode,
    encode,
    is_data_frame,
)
from proto.scheduler import eligible_paths
from proto.sockets import drop_heartbeats
from tests.util import make_pair, pump, settle


def _data_links(net) -> list[str]:
    return [e.link for e in net.log if is_data_frame(e.data) and not e.dropped]


def _fill_loss(path, rate: float) -> None:
    n = path._loss_window
    drops: set[int] = set()
    n_drop = round(rate * n)
    if n_drop:
        stride = n / n_drop
        for k in range(n_drop):
            drops.add(int(k * stride) % n)
    path.hb_outcomes.clear()
    for i in range(n):
        path.hb_outcomes.append(i not in drops)


def test_packet_arrives_twice_one_delivered(clock, net) -> None:
    a, b = make_pair(clock, net)
    a.send(b"hello")
    assert b.poll() == [b"hello"]
    wifi_copy = next(
        e.data for e in net.log if e.link == "wifi" and is_data_frame(e.data)
    )
    b.path("wifi").socket.inbox.append(
        (wifi_copy, a.path("wifi").socket.bind_addr)
    )
    assert b.poll() == []


def test_path_b_dead_still_delivered_via_a(clock, net) -> None:
    a, b = make_pair(clock, net)
    net.set_down("wifi")
    a.send(b"via-eth")
    assert _data_links(net) == ["eth"]
    assert b.poll() == [b"via-eth"]


def test_lossy_path_no_data_copies_still_delivers(clock, net) -> None:
    a, b = make_pair(clock, net)
    _fill_loss(a.path("eth"), 0.30)
    _fill_loss(a.path("wifi"), 0.0)
    assert a.path("eth").loss() == pytest.approx(0.30, abs=0.01)
    assert [p.name for p in eligible_paths(a.paths, 0.20)] == ["wifi"]
    net.clear_log()
    a.send(b"prefer-wifi")
    assert _data_links(net) == ["wifi"]
    assert b.poll() == [b"prefer-wifi"]


def test_measured_heartbeat_loss_excludes_path(clock, net) -> None:
    a, b = make_pair(clock, net)
    net.set_rx_loss(a.path("eth").socket.bind_addr, drop_heartbeats(0.30))
    a.tick()
    b.tick()
    settle(a, b)
    pump(a, b, clock, 24)
    assert a.path("eth").loss() > 0.20
    assert a.path("wifi").loss() <= 0.20
    net.clear_log()
    a.send(b"steer")
    assert _data_links(net) == ["wifi"]
    assert b.poll() == [b"steer"]


def test_add_path_c_in_config_traffic_on_c(clock, net) -> None:
    a, b = make_pair(clock, net, names=("eth", "wifi", "lte"))
    a.send(b"three")
    assert set(_data_links(net)) == {"eth", "wifi", "lte"}
    assert b.poll() == [b"three"]


def _inject(session, path_name: str, seq: int, payload: bytes) -> None:
    path = session.path(path_name)
    pkt = Packet(
        flags=0,
        traffic_class=TC_CONTROL,
        path_id=path.path_id,
        session_id=session.session_id,
        seq=seq,
        timestamp_us=0,
        payload=payload,
    )
    path.socket.inbox.append((encode(pkt), ("127.0.0.1", 1)))


def test_reorder_ok_late_dropped_after_window(clock, net) -> None:
    a, b = make_pair(clock, net, dedupe_window=8)
    _inject(b, "eth", 1, b"one")
    assert b.poll() == [b"one"]
    _inject(b, "eth", 10, b"ten")
    assert b.poll() == [b"ten"]
    _inject(b, "eth", 2, b"two")
    assert b.poll() == []
    _inject(b, "eth", 3, b"three")
    assert b.poll() == [b"three"]


def test_heartbeat_timeout_marks_down_probe_brings_back(clock, net) -> None:
    a, b = make_pair(clock, net)
    a.tick()
    b.tick()
    settle(a, b)
    assert a.path("eth").up
    assert a.path("wifi").up

    net.set_down("eth")
    for _ in range(3):
        clock.advance(100_000)
        a.tick()
        b.tick()
        settle(a, b)
    assert not a.path("eth").up
    assert a.path("wifi").up

    net.set_up("eth")
    recovered = False
    for _ in range(12):
        clock.advance(100_000)
        a.tick()
        b.tick()
        settle(a, b)
        if a.path("eth").up:
            recovered = True
            break
    assert recovered
    probes = [
        e
        for e in net.log
        if e.link == "eth" and len(e.data) >= 6 and (e.data[5] & FLAG_PROBE)
    ]
    assert probes


def test_control_drains_before_media(clock, net) -> None:
    a, b = make_pair(clock, net)
    a.enqueue(b"media", TC_MEDIA)
    a.enqueue(b"ctrl", TC_CONTROL)
    a.flush()
    payloads = [
        decode(e.data).payload
        for e in net.log
        if e.link == "eth" and is_data_frame(e.data)
    ]
    assert payloads == [b"ctrl", b"media"]
    assert b.poll() == [b"ctrl", b"media"]


def test_payload_over_mtu_rejected(clock, net) -> None:
    a, _b = make_pair(clock, net)
    with pytest.raises(PayloadTooLarge):
        a.send(b"x" * 1441)


def test_heartbeat_exchange_sets_rtt(clock, net) -> None:
    a, b = make_pair(clock, net)
    a.tick()
    b.tick()
    settle(a, b)
    assert a.path("eth").rtt_us is not None
    assert b.path("wifi").rtt_us is not None
