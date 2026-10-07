"""ROS-free operator host: plans stay off the wire, keys match the robot codec.

These tests bind a free port. They do not bind 8090, 8091, 8765, or
5501–5504, and they do not exec docker or mlink.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from packaging.operator_app.host import OperatorHost
from packaging.operator_app.keys import (
    COMMANDS,
    JOG_ANGULAR,
    JOG_LINEAR,
    KEY_BINDINGS,
)
from packaging.operator_app.payload import (
    Command,
    Heartbeat,
    encode_command,
    encode_heartbeat,
)

_FORBIDDEN = {8090, 8091, 8765, 5501, 5502, 5503, 5504}
_ROOT = Path(__file__).resolve().parents[2]


def _robot_codec() -> Any:
    demo = (
        _ROOT
        / "teleoperation-prototype"
        / "ros2_ws"
        / "src"
        / "teleop_demo"
    )
    if str(demo) not in sys.path:
        sys.path.insert(0, str(demo))
    import teleop_demo.mlink_payload as payload

    return payload


class _Open:
    def close(self) -> None:
        return None


class _FakeUdp:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self, timeout: float = 0.0) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def _refuse_udp() -> Any:
    raise AssertionError("mlink udp opened")


def test_operator_modules_do_not_import_ros() -> None:
    package = _ROOT / "packaging" / "operator_app"
    for name in ("host.py", "keys.py", "payload.py", "udp.py", "wsproto.py", "cli.py"):
        text = (package / name).read_text(encoding="utf-8")
        for banned in ("rclpy", "geometry_msgs", "teleop_demo_msgs"):
            assert banned not in text, f"{name} imports {banned}"
    import packaging.operator_app.cli  # noqa: F401
    import packaging.operator_app.host  # noqa: F401

    assert "rclpy" not in sys.modules
    assert "geometry_msgs" not in sys.modules
    assert "teleop_demo_msgs" not in sys.modules


def test_key_table_matches_the_robot_rates() -> None:
    text = (
        _ROOT
        / "teleoperation-prototype"
        / "ros2_ws"
        / "src"
        / "teleop_demo"
        / "teleop_demo"
        / "commands.py"
    ).read_text(encoding="utf-8")
    assert "JOG_LINEAR = 0.05" in text
    assert "JOG_ANGULAR = 0.2" in text
    assert '"g": "open"' in text
    assert '"h": "close"' in text
    assert JOG_LINEAR == 0.05
    assert JOG_ANGULAR == 0.2
    assert KEY_BINDINGS["g"] == "open"
    assert KEY_BINDINGS["h"] == "close"
    assert COMMANDS["open"] == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    assert COMMANDS["close"] == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert COMMANDS["+x"] == (0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert len(COMMANDS["+yaw"]) == 7


def test_command_and_heartbeat_bytes_match_the_robot_codec() -> None:
    robot = _robot_codec()
    fields = dict(
        sequence=4,
        stamp_sec=10,
        stamp_nsec=20,
        lx=0.05,
        ly=-0.05,
        lz=0.05,
        ax=0.2,
        ay=-0.2,
        az=0.2,
        gripper=1.0,
        frame_id="tool0",
        session_id="abc",
    )
    assert encode_command(Command(**fields)) == robot.encode_command(robot.Command(**fields))
    beat = dict(sequence=9, stamp_sec=10, stamp_nsec=20, session_id="abc")
    assert encode_heartbeat(Heartbeat(**beat)) == robot.encode_heartbeat(robot.Heartbeat(**beat))


def test_gripper_keys_send_the_robot_datagram_and_ignore_the_rest(tmp_path: Path) -> None:
    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (10, 20),
        timers=False,
    )
    try:
        assert host.heartbeat_armed is False
        assert host.socket_open is False
        host._connection = _Open()  # type: ignore[assignment]
        host.session_id = "sess"
        for key in ("w", "a", "s", "d", "r", "f", "j", "l", "u", "o", "i", "k"):
            host.apply_key(key, True)
        assert host._motion == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert host._gripper == 0.5
        host.publish_command()
        host.publish_heartbeat()
        assert fake.sent == []

        host.enable_link()
        assert host.heartbeat_armed is True
        assert host.socket_open is True
        fake.sent.clear()
        host.apply_key("g", True)
        assert host._gripper == 1.0
        assert host._motion == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        host.command_sequence = 0
        host.publish_command()
        host.heartbeat_sequence = 0
        host.publish_heartbeat()
        host.apply_key("h", True)
        host.publish_command()
        open_bytes = robot.encode_command(
            robot.Command(
                sequence=1,
                stamp_sec=10,
                stamp_nsec=20,
                lx=0.0,
                ly=0.0,
                lz=0.0,
                ax=0.0,
                ay=0.0,
                az=0.0,
                gripper=1.0,
                frame_id="tool0",
                session_id="sess",
            )
        )
        close_bytes = robot.encode_command(
            robot.Command(
                sequence=2,
                stamp_sec=10,
                stamp_nsec=20,
                lx=0.0,
                ly=0.0,
                lz=0.0,
                ax=0.0,
                ay=0.0,
                az=0.0,
                gripper=0.25,
                frame_id="tool0",
                session_id="sess",
            )
        )
        heart = robot.encode_heartbeat(
            robot.Heartbeat(sequence=1, stamp_sec=10, stamp_nsec=20, session_id="sess")
        )
        assert fake.sent == [open_bytes, heart, close_bytes]
        kind, msg = robot.decode(fake.sent[0])
        assert kind == robot.TYPE_COMMAND
        assert msg.gripper == 1.0
        assert (msg.lx, msg.ly, msg.lz, msg.ax, msg.ay, msg.az) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        host.disable_link()
        assert host.heartbeat_armed is False
        assert host.socket_open is False
        assert fake.closed is True
    finally:
        host.shutdown()


def _command_grippers(fake: _FakeUdp, robot: Any) -> list[float]:
    values: list[float] = []
    for payload in fake.sent:
        kind, msg = robot.decode(payload)
        assert kind == robot.TYPE_COMMAND
        assert (msg.lx, msg.ly, msg.lz, msg.ax, msg.ay, msg.az) == (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        values.append(float(msg.gripper))
    return values


def _goals_for(values: list[float], present: int) -> list[int | None]:
    _robot_codec()
    from teleop_demo.gripper_control import CONNECTED, WATCHDOG_OK, GripperController

    controller = GripperController()
    controller.on_teleop_state(CONNECTED, WATCHDOG_OK)
    now = 0.0
    goals: list[int | None] = []
    for value in values:
        now += 0.05
        controller.on_gripper_safe(value, now)
        output = controller.tick(now + 0.01, present)
        assert output.deadman is False
        goals.append(output.goal_position)
        now += 0.01
    return goals


def test_each_press_steps_once_and_a_held_key_does_not(tmp_path: Path) -> None:
    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (10, 20),
        timers=False,
    )
    try:
        host._connection = _Open()  # type: ignore[assignment]
        host.session_id = "sess"
        host.enable_link()
        host.publish_command()
        host.apply_key("g", True)
        host.publish_command()
        host.publish_command()
        host.publish_command()
        host.apply_key("g", False)
        assert host._gripper == 0.5
        host.publish_command()
        host.apply_key("g", True)
        host.publish_command()
        host.apply_key("g", False)
        host.publish_command()
        host.apply_key("g", True)
        host.publish_command()
        host.apply_key("h", True)
        host.publish_command()
        host.apply_key("h", False)
        assert host._gripper == 0.5
        host.publish_command()
        host.apply_key("h", True)
        host.publish_command()
        values = _command_grippers(fake, robot)
        assert values == [
            0.5,
            1.0,
            1.0,
            1.0,
            0.5,
            1.0,
            0.5,
            1.0,
            0.25,
            0.5,
            0.25,
        ]
        present = 2061
        assert _goals_for(values, present) == [
            present,
            present + 48,
            present + 48,
            present + 48,
            present + 48,
            present + 96,
            present + 96,
            present + 144,
            present + 96,
            present + 96,
            present + 48,
        ]
        script = (_ROOT / "teleoperation-prototype" / "web" / "operate.js").read_text(
            encoding="utf-8"
        )
        assert "event.repeat" in script
    finally:
        host.shutdown()


def test_holding_a_gripper_key_does_not_mint_another_step(tmp_path: Path) -> None:
    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (10, 20),
        timers=False,
    )
    try:
        host._connection = _Open()  # type: ignore[assignment]
        host.session_id = "sess"
        host.enable_link()
        host.publish_command()
        host.apply_key("h", True)
        host.publish_command()
        host.apply_key("h", True)
        host.apply_key("h", True)
        host.publish_command()
        host.apply_key(" ", True)
        assert host._gripper == 0.25
        host.publish_command()
        host.apply_key("h", True)
        host.publish_command()
        values = _command_grippers(fake, robot)
        assert values == [0.5, 0.25, 0.25, 0.25, 0.0]
        present = 3466
        assert _goals_for(values, present) == [
            present,
            present - 48,
            present - 48,
            present - 48,
            present - 96,
        ]
    finally:
        host.shutdown()


def test_keyup_does_not_drop_the_step_or_reverse_it(tmp_path: Path) -> None:
    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (10, 20),
        timers=False,
    )
    try:
        host._connection = _Open()  # type: ignore[assignment]
        host.session_id = "sess"
        host.enable_link()
        host.publish_command()
        host.apply_key("g", True)
        host.apply_key("g", False)
        host.publish_command()
        host.publish_command()
        host.apply_key("g", True)
        host.publish_command()
        host.apply_key("g", False)
        host.apply_key("g", True)
        host.publish_command()
        host.apply_key("h", True)
        host.publish_command()
        host.apply_key("h", False)
        host.apply_key("h", True)
        host.publish_command()
        values = _command_grippers(fake, robot)
        assert values == [0.5, 1.0, 0.5, 1.0, 0.75, 0.25, 0.0]
        present = 3000
        assert _goals_for(values, present) == [
            present,
            present + 48,
            present + 48,
            present + 96,
            present + 144,
            present + 96,
            present + 48,
        ]
    finally:
        host.shutdown()


def test_serve_health_and_login_do_not_arm_the_link() -> None:
    host = OperatorHost(_ROOT, "ws://127.0.0.1:9", opener=_refuse_udp, timers=False)
    port = host.serve("127.0.0.1", 0)
    assert port not in _FORBIDDEN
    try:
        assert host.heartbeat_armed is False
        assert host.socket_open is False
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
            assert response.status == 200
            body = json.loads(response.read().decode("utf-8"))
        assert body == {"ok": True}
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            assert response.status == 200
            page = response.read()
        assert b"Teleop" in page
        assert b'id="login-form"' in page
        assert host.heartbeat_armed is False
        assert host.socket_open is False
    finally:
        host.shutdown()


def _ws_connect(port: int) -> socket.socket:
    raw_key = base64.b64encode(b"0123456789abcdef").decode("ascii")
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    sock.settimeout(2)
    request = (
        f"GET /ws/session HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {raw_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode("ascii"))
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    assert b" 101 " in data.split(b"\r\n", 1)[0]
    return sock


def _ws_send(sock: socket.socket, key: str, mask: bytes) -> None:
    payload = json.dumps({"type": "key", "key": key, "down": True}).encode("utf-8")
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    sock.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + masked)


def _until(predicate: Any, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_websocket_g_and_h_on_a_free_port() -> None:
    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        _ROOT,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (3, 4),
        timers=False,
    )
    port = host.serve("127.0.0.1", 0)
    assert port not in _FORBIDDEN
    sock: socket.socket | None = None
    try:
        assert host.socket_open is False
        host.enable_link()
        sock = _ws_connect(port)
        assert _until(lambda: host.session_id != "")
        _ws_send(sock, "g", b"\x01\x02\x03\x04")
        assert _until(lambda: host._gripper == 1.0)
        _ws_send(sock, "w", b"\x0a\x0b\x0c\x0d")
        _ws_send(sock, "h", b"\x11\x12\x13\x14")
        assert _until(lambda: host._gripper == 0.25)
        assert host._motion == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        fake.sent.clear()
        host._gripper = 1.0
        host.command_sequence = 0
        host.publish_command()
        kind, msg = robot.decode(fake.sent[-1])
        assert kind == robot.TYPE_COMMAND
        assert msg.gripper == 1.0
        assert (msg.lx, msg.ly, msg.lz, msg.ax, msg.ay, msg.az) == (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
    finally:
        if sock is not None:
            sock.close()
        host.shutdown()


def test_quit_stops_mlink_then_the_process(tmp_path: Path) -> None:
    recorded: list[Any] = []
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=_refuse_udp,
        executor=lambda command, _root: recorded.append(command),
        timers=False,
    )
    host.request_quit()
    assert host.wait_for_quit(1)
    assert [item.label for item in recorded] == ["mlink-op"]
    assert "docker" not in recorded[0].argv
    assert "start_daemon.sh" not in " ".join(recorded[0].argv)
    assert recorded[0].argv[-1].endswith("packaging/run/mlink-op.pid")
    host.shutdown()
