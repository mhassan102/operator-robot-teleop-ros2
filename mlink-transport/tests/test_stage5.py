"""Stage 5: control vs media app faces on localhost. No real NICs."""

from __future__ import annotations

import socket
import threading
import time

from daemon import run
from proto.config import MlinkConfig
from proto.header import MAX_PAYLOAD
from tests.util import reserve_udp_ports
from tests.test_stage2 import _pair_cfgs


def _sendto(addr: tuple[str, int], payload: bytes) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, addr)
    finally:
        sock.close()


def _recv_n(sock: socket.socket, n: int, timeout: float) -> list[bytes]:
    sock.settimeout(timeout)
    got: list[bytes] = []
    deadline = time.monotonic() + timeout
    while len(got) < n and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        sock.settimeout(max(remaining, 0.001))
        try:
            data, _addr = sock.recvfrom(65535)
        except TimeoutError:
            break
        except OSError:
            break
        got.append(data)
    return got


def test_daemon_routes_control_and_media_to_separate_app_ports() -> None:
    ports = reserve_udp_ports(12)
    (
        eth_a,
        eth_b,
        wifi_a,
        wifi_b,
        op_listen,
        op_send,
        edge_listen,
        edge_send,
        op_listen_media,
        op_send_media,
        edge_listen_media,
        edge_send_media,
    ) = ports
    cfg_op, cfg_edge = _pair_cfgs(
        eth_a,
        eth_b,
        wifi_a,
        wifi_b,
        listen_a=f"127.0.0.1:{op_listen}",
        send_a=f"127.0.0.1:{op_send}",
        listen_b=f"127.0.0.1:{edge_listen}",
        send_b=f"127.0.0.1:{edge_send}",
    )
    cfg_op = MlinkConfig(
        session_id=cfg_op.session_id,
        paths=cfg_op.paths,
        listen_app=cfg_op.listen_app,
        send_app=cfg_op.send_app,
        listen_media=f"127.0.0.1:{op_listen_media}",
        send_media=f"127.0.0.1:{op_send_media}",
        heartbeat_interval_us=cfg_op.heartbeat_interval_us,
        down_timeout_us=cfg_op.down_timeout_us,
        probe_interval_us=cfg_op.probe_interval_us,
        loss_threshold=cfg_op.loss_threshold,
        loss_window=cfg_op.loss_window,
        dedupe_window=cfg_op.dedupe_window,
    )
    cfg_edge = MlinkConfig(
        session_id=cfg_edge.session_id,
        paths=cfg_edge.paths,
        listen_app=cfg_edge.listen_app,
        send_app=cfg_edge.send_app,
        listen_media=f"127.0.0.1:{edge_listen_media}",
        send_media=f"127.0.0.1:{edge_send_media}",
        heartbeat_interval_us=cfg_edge.heartbeat_interval_us,
        down_timeout_us=cfg_edge.down_timeout_us,
        probe_interval_us=cfg_edge.probe_interval_us,
        loss_threshold=cfg_edge.loss_threshold,
        loss_window=cfg_edge.loss_window,
        dedupe_window=cfg_edge.dedupe_window,
    )

    stop = threading.Event()
    errors: list[BaseException] = []

    def _op() -> None:
        try:
            run(cfg_op, stop=stop, role="op")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _edge() -> None:
        try:
            run(cfg_edge, stop=stop, role="edge")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=_op, daemon=True),
        threading.Thread(target=_edge, daemon=True),
    ]
    control_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    media_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        control_rx.bind(("127.0.0.1", edge_send))
        media_rx.bind(("127.0.0.1", edge_send_media))
        for t in threads:
            t.start()
        time.sleep(0.15)
        _sendto(("127.0.0.1", op_listen), b"ctrl-one")
        _sendto(("127.0.0.1", op_listen_media), b"media-one")
        got_ctrl = _recv_n(control_rx, 1, 1.0)
        got_media = _recv_n(media_rx, 1, 1.0)
        assert got_ctrl == [b"ctrl-one"], got_ctrl
        assert got_media == [b"media-one"], got_media
        leftover_ctrl = _recv_n(control_rx, 1, 0.15)
        leftover_media = _recv_n(media_rx, 1, 0.15)
        assert leftover_ctrl == []
        assert leftover_media == []
    finally:
        stop.set()
        control_rx.close()
        media_rx.close()
        for t in threads:
            t.join(timeout=2.0)
    assert errors == []


def test_daemon_control_not_blocked_by_media_burst() -> None:
    ports = reserve_udp_ports(12)
    (
        eth_a,
        eth_b,
        wifi_a,
        wifi_b,
        op_listen,
        op_send,
        edge_listen,
        edge_send,
        op_listen_media,
        op_send_media,
        edge_listen_media,
        edge_send_media,
    ) = ports
    cfg_op, cfg_edge = _pair_cfgs(
        eth_a,
        eth_b,
        wifi_a,
        wifi_b,
        listen_a=f"127.0.0.1:{op_listen}",
        send_a=f"127.0.0.1:{op_send}",
        listen_b=f"127.0.0.1:{edge_listen}",
        send_b=f"127.0.0.1:{edge_send}",
    )
    cfg_op = MlinkConfig(
        session_id=cfg_op.session_id,
        paths=cfg_op.paths,
        listen_app=cfg_op.listen_app,
        send_app=cfg_op.send_app,
        listen_media=f"127.0.0.1:{op_listen_media}",
        send_media=f"127.0.0.1:{op_send_media}",
        heartbeat_interval_us=cfg_op.heartbeat_interval_us,
        down_timeout_us=cfg_op.down_timeout_us,
        probe_interval_us=cfg_op.probe_interval_us,
        loss_threshold=cfg_op.loss_threshold,
        loss_window=cfg_op.loss_window,
        dedupe_window=cfg_op.dedupe_window,
        max_media_queue=8,
    )
    cfg_edge = MlinkConfig(
        session_id=cfg_edge.session_id,
        paths=cfg_edge.paths,
        listen_app=cfg_edge.listen_app,
        send_app=cfg_edge.send_app,
        listen_media=f"127.0.0.1:{edge_listen_media}",
        send_media=f"127.0.0.1:{edge_send_media}",
        heartbeat_interval_us=cfg_edge.heartbeat_interval_us,
        down_timeout_us=cfg_edge.down_timeout_us,
        probe_interval_us=cfg_edge.probe_interval_us,
        loss_threshold=cfg_edge.loss_threshold,
        loss_window=cfg_edge.loss_window,
        dedupe_window=cfg_edge.dedupe_window,
    )

    stop = threading.Event()
    errors: list[BaseException] = []

    def _op() -> None:
        try:
            run(cfg_op, stop=stop, role="op")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def _edge() -> None:
        try:
            run(cfg_edge, stop=stop, role="edge")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=_op, daemon=True),
        threading.Thread(target=_edge, daemon=True),
    ]
    control_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        control_rx.bind(("127.0.0.1", edge_send))
        for t in threads:
            t.start()
        time.sleep(0.15)
        blob = b"m" * min(1200, MAX_PAYLOAD)
        for _ in range(40):
            _sendto(("127.0.0.1", op_listen_media), blob)
        _sendto(("127.0.0.1", op_listen), b"ctrl-after-burst")
        got = _recv_n(control_rx, 1, 1.0)
        assert got == [b"ctrl-after-burst"], got
    finally:
        stop.set()
        control_rx.close()
        for t in threads:
            t.join(timeout=2.0)
    assert errors == []
