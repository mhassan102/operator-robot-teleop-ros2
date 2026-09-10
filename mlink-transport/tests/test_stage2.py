"""Stage 2: real localhost UDP. No NICs, no SO_BINDTODEVICE."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from daemon import disable_path, run
from ping import run_ping
from proto.clock import SystemClock
from proto.config import MlinkConfig, PathConfig, load_config
from proto.session import MlinkSession
from proto.sockets import UdpSocketFactory
from tests.util import reserve_udp_ports

ROOT = Path(__file__).resolve().parents[1]


def _pair_cfgs(
    eth_a: int,
    eth_b: int,
    wifi_a: int,
    wifi_b: int,
    listen_a: str = "127.0.0.1:1",
    send_a: str = "127.0.0.1:2",
    listen_b: str = "127.0.0.1:3",
    send_b: str = "127.0.0.1:4",
    **kwargs: object,
) -> tuple[MlinkConfig, MlinkConfig]:
    defaults: dict = dict(
        session_id=1,
        heartbeat_interval_us=100_000,
        down_timeout_us=300_000,
        probe_interval_us=1_000_000,
        loss_threshold=0.20,
        loss_window=20,
        dedupe_window=1024,
    )
    defaults.update(kwargs)
    a_paths = (
        PathConfig(
            name="eth",
            bind_ip="127.0.0.1",
            bind_port=eth_a,
            peer_ip="127.0.0.1",
            peer_port=eth_b,
            ifname=None,
        ),
        PathConfig(
            name="wifi",
            bind_ip="127.0.0.1",
            bind_port=wifi_a,
            peer_ip="127.0.0.1",
            peer_port=wifi_b,
            ifname=None,
        ),
    )
    b_paths = (
        PathConfig(
            name="eth",
            bind_ip="127.0.0.1",
            bind_port=eth_b,
            peer_ip="127.0.0.1",
            peer_port=eth_a,
            ifname=None,
        ),
        PathConfig(
            name="wifi",
            bind_ip="127.0.0.1",
            bind_port=wifi_b,
            peer_ip="127.0.0.1",
            peer_port=wifi_a,
            ifname=None,
        ),
    )
    cfg_a = MlinkConfig(paths=a_paths, listen_app=listen_a, send_app=send_a, **defaults)
    cfg_b = MlinkConfig(paths=b_paths, listen_app=listen_b, send_app=send_b, **defaults)
    return cfg_a, cfg_b


def _pump_until(a: MlinkSession, b: MlinkSession, timeout: float) -> list[bytes]:
    got: list[bytes] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        a.tick()
        b.tick()
        got.extend(b.poll())
        a.poll()
        if got:
            return got
        time.sleep(0.001)
    return got


def test_udp_factory_ignores_ifname() -> None:
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
        sock = factory.create(cfg)
        assert sock.bind_addr == ("127.0.0.1", port)
        assert sock.fileno() >= 0
    finally:
        factory.close()


def test_localhost_udp_first_good_one_delivered() -> None:
    eth_a, eth_b, wifi_a, wifi_b = reserve_udp_ports(4)
    cfg_a, cfg_b = _pair_cfgs(eth_a, eth_b, wifi_a, wifi_b)
    fa, fb = UdpSocketFactory(), UdpSocketFactory()
    clock = SystemClock()
    a = MlinkSession(cfg_a, clock, fa)
    b = MlinkSession(cfg_b, clock, fb)
    try:
        seq = a.send(b"hello")
        assert seq == 1
        got = _pump_until(a, b, 0.5)
        assert got == [b"hello"]
        assert b.poll() == []
    finally:
        fa.close()
        fb.close()


def test_localhost_udp_kill_one_path_still_delivers() -> None:
    eth_a, eth_b, wifi_a, wifi_b = reserve_udp_ports(4)
    cfg_a, cfg_b = _pair_cfgs(eth_a, eth_b, wifi_a, wifi_b)
    fa, fb = UdpSocketFactory(), UdpSocketFactory()
    clock = SystemClock()
    a = MlinkSession(cfg_a, clock, fa)
    b = MlinkSession(cfg_b, clock, fb)
    try:
        a.send(b"before")
        assert _pump_until(a, b, 0.5) == [b"before"]
        disable_path(a, "eth")
        a.send(b"after-kill")
        assert _pump_until(a, b, 0.5) == [b"after-kill"]
    finally:
        fa.close()
        fb.close()


def test_daemon_threads_kill_path_stream() -> None:
    (
        eth_a,
        eth_b,
        wifi_a,
        wifi_b,
        op_listen,
        op_send,
        edge_listen,
        edge_send,
        control,
    ) = reserve_udp_ports(9)
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
    stop = threading.Event()
    errors: list[BaseException] = []

    def _op() -> None:
        try:
            run(
                cfg_op,
                control_addr=("127.0.0.1", control),
                stop=stop,
                role="op",
            )
        except BaseException as exc:  # noqa: BLE001 — capture for the parent thread
            errors.append(exc)

    def _edge() -> None:
        try:
            run(cfg_edge, reflect=True, stop=stop, role="edge")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t_op = threading.Thread(target=_op, name="mlink-op", daemon=True)
    t_edge = threading.Thread(target=_edge, name="mlink-edge", daemon=True)
    t_op.start()
    t_edge.start()
    time.sleep(0.05)
    try:
        rc = run_ping(
            bind=("127.0.0.1", op_send),
            target=("127.0.0.1", op_listen),
            count=80,
            interval_s=0.002,
            payload_size=16,
            wait_s=0.5,
            ready_s=2.0,
            kill_after=30,
            kill_path="eth",
            control=("127.0.0.1", control),
        )
        assert rc == 0
        assert errors == []
    finally:
        stop.set()
        t_op.join(timeout=2)
        t_edge.join(timeout=2)
    assert errors == []


def _write_pair_yaml(tmp: Path, ports: list[int]) -> tuple[Path, Path, str]:
    (
        eth_a,
        eth_b,
        wifi_a,
        wifi_b,
        op_listen,
        op_send,
        edge_listen,
        edge_send,
        control,
    ) = ports
    op = {
        "session_id": 1,
        "listen_app": f"127.0.0.1:{op_listen}",
        "send_app": f"127.0.0.1:{op_send}",
        "paths": [
            {
                "name": "eth",
                "bind_ip": "127.0.0.1",
                "bind_port": eth_a,
                "peer": f"127.0.0.1:{eth_b}",
            },
            {
                "name": "wifi",
                "bind_ip": "127.0.0.1",
                "bind_port": wifi_a,
                "peer": f"127.0.0.1:{wifi_b}",
            },
        ],
    }
    edge = {
        "session_id": 1,
        "listen_app": f"127.0.0.1:{edge_listen}",
        "send_app": f"127.0.0.1:{edge_send}",
        "paths": [
            {
                "name": "eth",
                "bind_ip": "127.0.0.1",
                "bind_port": eth_b,
                "peer": f"127.0.0.1:{eth_a}",
            },
            {
                "name": "wifi",
                "bind_ip": "127.0.0.1",
                "bind_port": wifi_b,
                "peer": f"127.0.0.1:{wifi_a}",
            },
        ],
    }
    op_path = tmp / "op.yaml"
    edge_path = tmp / "edge.yaml"
    op_path.write_text(yaml.safe_dump(op), encoding="utf-8")
    edge_path.write_text(yaml.safe_dump(edge), encoding="utf-8")
    return op_path, edge_path, f"127.0.0.1:{control}"


def test_cli_subprocess_1000_kill_path(tmp_path: Path) -> None:
    ports = reserve_udp_ports(9)
    op_yaml, edge_yaml, control = _write_pair_yaml(tmp_path, ports)
    op_listen = ports[4]
    op_send = ports[5]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    op_p = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "op",
            "--config",
            str(op_yaml),
            "--control",
            control,
            "--log-level",
            "WARNING",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    edge_p = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "edge",
            "--config",
            str(edge_yaml),
            "--reflect",
            "--log-level",
            "WARNING",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ping = subprocess.run(
            [
                sys.executable,
                "-m",
                "ping",
                "--bind",
                f"127.0.0.1:{op_send}",
                "--target",
                f"127.0.0.1:{op_listen}",
                "--count",
                "1000",
                "--interval-ms",
                "1",
                "--kill-after",
                "400",
                "--kill-path",
                "eth",
                "--control",
                control,
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if ping.returncode != 0:
            op_err = op_p.stderr.read().decode() if op_p.stderr else ""
            edge_err = edge_p.stderr.read().decode() if edge_p.stderr else ""
            pytest.fail(
                f"ping rc={ping.returncode}\nstdout:\n{ping.stdout}\n"
                f"stderr:\n{ping.stderr}\nop:\n{op_err}\nedge:\n{edge_err}"
            )
        report = json.loads(ping.stdout.strip().splitlines()[-1])
        assert report["sent"] == 1000
        assert report["delivered"] == 1000
        assert report["loss"] == 0.0
        assert report["kill_path"] == "eth"
        assert report["kill_reply"] == "ok"
        # localhost; a multi-millisecond gap is the kill, not a stall
        assert report["max_gap_ms"] < 100.0
    finally:
        for proc in (op_p, edge_p):
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)


def test_load_loopback_yamls() -> None:
    op = load_config(ROOT / "config" / "loopback.yaml")
    edge = load_config(ROOT / "config" / "loopback-edge.yaml")
    assert op.paths[0].peer_port == edge.paths[0].bind_port
    assert edge.paths[1].peer_port == op.paths[1].bind_port
