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
    JOINT_HOLD,
    JOINT_JOG_NEGATIVE,
    JOINT_JOG_POSITIVE,
    JOG_ANGULAR,
    JOG_LINEAR,
    KEY_BINDINGS,
    direction_allowed,
)
from packaging.operator_app.payload import (
    Command,
    Heartbeat,
    JointCommand,
    JointTicks,
    encode_command,
    encode_heartbeat,
    encode_joint_command,
    encode_joint_ticks,
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
    ticks = dict(
        shoulder_pan=11,
        shoulder_lift=22,
        elbow_flex=33,
        wrist_flex=44,
        wrist_roll=55,
        gripper=66,
    )
    assert encode_joint_ticks(JointTicks(**ticks)) == robot.encode_joint_ticks(
        robot.JointTicks(**ticks)
    )
    joints = dict(
        shoulder_pan=0.0,
        shoulder_lift=2.0,
        elbow_flex=-1.0,
        wrist_flex=0.2,
        wrist_roll=0.498,
        gripper=0.5,
    )
    assert encode_joint_command(JointCommand(**joints)) == robot.encode_joint_command(
        robot.JointCommand(**joints)
    )
    assert robot.TYPE_COMMAND == 1
    assert robot.TYPE_JOINT_CMD == 9


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
            host.apply_key(key, False)
        assert host._motion == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        assert host._gripper == 0.5
        assert host._joints == [JOINT_HOLD] * 5
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
        hold_joints = robot.encode_joint_command(
            robot.JointCommand(0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        )
        assert fake.sent == [open_bytes, hold_joints, heart, close_bytes, hold_joints]
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


def test_leader_input_sends_a_fraction_and_ignores_gripper_keys(tmp_path: Path) -> None:
    from packaging.operator_app.leader import encode_gripper_fraction

    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (10, 20),
        timers=False,
    )

    class _ScriptLeader:
        def __init__(self) -> None:
            self.value: float | None = 0.4
            self.started = 0
            self.stopped = 0

        def start(self, wait_s: float = 0.4) -> None:
            self.started += 1

        def stop(self) -> None:
            self.stopped += 1

        def fraction(self) -> float | None:
            return self.value

    script = _ScriptLeader()
    host._leader = script  # type: ignore[assignment]
    try:
        host._connection = _Open()  # type: ignore[assignment]
        host.session_id = "sess"
        host._input_mode = "leader"
        host.enable_link()
        host.apply_key("g", True)
        host.apply_key("h", True)
        assert host._gripper == 0.5
        host.command_sequence = 0
        host.publish_command()
        msg = _last(fake, robot, robot.TYPE_COMMAND)
        assert msg.gripper == encode_gripper_fraction(0.4)
        assert msg.gripper not in (0.0, 0.25, 0.5, 0.75, 1.0)
        assert (msg.lx, msg.ly, msg.lz, msg.ax, msg.ay, msg.az) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        held_arm = _last(fake, robot, robot.TYPE_JOINT_CMD)
        assert (
            held_arm.shoulder_pan,
            held_arm.shoulder_lift,
            held_arm.elbow_flex,
            held_arm.wrist_flex,
            held_arm.wrist_roll,
            held_arm.gripper,
        ) == (0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        script.value = None
        host.publish_command()
        held = _last(fake, robot, robot.TYPE_COMMAND)
        assert held.gripper == 0.5
        host._console._accepted_input = "leader"
        conn = _Open()
        host.attach_session(conn)
        assert script.started == 1
        assert host._input_mode == "leader"
        host.detach_session(conn)
        assert host._input_mode == "keyboard"
        assert script.stopped >= 1
    finally:
        host.shutdown()


def _last(fake: _FakeUdp, robot: Any, kind: int) -> Any:
    found = None
    for payload in fake.sent:
        got_kind, msg = robot.decode(payload)
        if got_kind == kind:
            found = msg
    assert found is not None
    return found


def _command_grippers(fake: _FakeUdp, robot: Any) -> list[float]:
    values: list[float] = []
    for payload in fake.sent:
        kind, msg = robot.decode(payload)
        if kind == robot.TYPE_JOINT_CMD:
            assert (
                msg.shoulder_pan,
                msg.shoulder_lift,
                msg.elbow_flex,
                msg.wrist_flex,
                msg.wrist_roll,
                msg.gripper,
            ) == (0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
            continue
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
        assert host._joints[1] == JOINT_JOG_POSITIVE
        fake.sent.clear()
        host._gripper = 1.0
        host.command_sequence = 0
        host.publish_command()
        msg = _last(fake, robot, robot.TYPE_COMMAND)
        assert msg.gripper == 1.0
        assert (msg.lx, msg.ly, msg.lz, msg.ax, msg.ay, msg.az) == (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        arm = _last(fake, robot, robot.TYPE_JOINT_CMD)
        assert arm.shoulder_lift == JOINT_JOG_POSITIVE
        assert arm.shoulder_pan == JOINT_HOLD
        assert arm.gripper == JOINT_HOLD
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


def test_arm_keys_jog_one_joint_and_space_holds(tmp_path: Path) -> None:
    """Page keys move servos 1-5. The twist stays zero. g/h stays one float."""
    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (10, 20),
        timers=False,
    )
    expected = {
        "w": (1, JOINT_JOG_POSITIVE),
        "s": (1, JOINT_JOG_NEGATIVE),
        "a": (0, JOINT_JOG_POSITIVE),
        "d": (0, JOINT_JOG_NEGATIVE),
        "r": (2, JOINT_JOG_POSITIVE),
        "f": (2, JOINT_JOG_NEGATIVE),
        "j": (0, JOINT_JOG_POSITIVE),
        "l": (0, JOINT_JOG_NEGATIVE),
        "u": (4, JOINT_JOG_POSITIVE),
        "o": (4, JOINT_JOG_NEGATIVE),
        "i": (3, JOINT_JOG_POSITIVE),
        "k": (3, JOINT_JOG_NEGATIVE),
    }
    try:
        assert direction_allowed("+x") is True
        assert direction_allowed("+x", gripper_only=True) is False
        assert direction_allowed("open", gripper_only=True) is True
        conn = _Open()
        host._connection = conn  # type: ignore[assignment]
        host.session_id = "sess"
        host.enable_link()
        status, body = host.named_pose("fold")
        assert status == 400
        assert body["message"] == "named poses disabled (gripper-only)"
        for key, (index, level) in expected.items():
            host.apply_key(key, True)
            host.apply_key(key, True)
            assert host._motion == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            assert host._gripper == 0.5
            assert host._joints[index] == level
            assert host._joints.count(level) == 1
            host.apply_key(key, False)
            assert host._joints == [JOINT_HOLD] * 5
            assert host._joint_key is None
        host.apply_key("w", True)
        host.apply_key("g", True)
        assert host._joints[1] == JOINT_JOG_POSITIVE
        assert host._gripper == 1.0
        host.apply_key(" ", True)
        assert host._joints == [JOINT_HOLD] * 5
        assert host._gripper == 1.0
        assert host._motion == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        fake.sent.clear()
        host.publish_command()
        arm = _last(fake, robot, robot.TYPE_JOINT_CMD)
        assert (
            arm.shoulder_pan,
            arm.shoulder_lift,
            arm.elbow_flex,
            arm.wrist_flex,
            arm.wrist_roll,
            arm.gripper,
        ) == (0.5, 0.5, 0.5, 0.5, 0.5, 0.5)
        command = _last(fake, robot, robot.TYPE_COMMAND)
        assert command.gripper == 1.0
        host.detach_session(conn)
        closing = _last(fake, robot, robot.TYPE_JOINT_CMD)
        assert closing.shoulder_lift == JOINT_HOLD
        assert _last(fake, robot, robot.TYPE_COMMAND).gripper == 1.0
    finally:
        host.shutdown()


def test_leader_arm_fractions_fill_the_joint_datagram(tmp_path: Path) -> None:
    from packaging.operator_app.leader import encode_gripper_fraction, encode_joint_fraction

    robot = _robot_codec()
    fake = _FakeUdp()
    host = OperatorHost(
        tmp_path,
        "ws://127.0.0.1:9",
        opener=lambda: fake,
        clock=lambda: (10, 20),
        timers=False,
    )

    class _ScriptLeader:
        def __init__(self) -> None:
            self.value: float | None = 0.4
            self.arms: list[float | None] = [0.0, 1.0, 0.5, 0.2, None]

        def start(self, wait_s: float = 0.4) -> None:
            return None

        def stop(self) -> None:
            return None

        def fraction(self) -> float | None:
            return self.value

        def arm_fractions(self) -> list[float | None]:
            return list(self.arms)

    host._leader = _ScriptLeader()  # type: ignore[assignment]
    try:
        host._connection = _Open()  # type: ignore[assignment]
        host.session_id = "sess"
        host._input_mode = "leader"
        host.enable_link()
        host.apply_key("w", True)
        host.apply_key("g", True)
        host.publish_command()
        command = _last(fake, robot, robot.TYPE_COMMAND)
        assert command.gripper == encode_gripper_fraction(0.4)
        assert (command.lx, command.ly, command.lz, command.ax, command.ay, command.az) == (
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        arm = _last(fake, robot, robot.TYPE_JOINT_CMD)
        assert arm.shoulder_pan == encode_joint_fraction(0.0)
        assert arm.shoulder_lift == encode_joint_fraction(1.0)
        assert arm.elbow_flex == encode_joint_fraction(0.5)
        assert arm.elbow_flex != 0.5
        assert arm.wrist_flex == encode_joint_fraction(0.2)
        assert arm.wrist_roll == JOINT_HOLD
        assert arm.gripper == JOINT_HOLD
        assert arm.shoulder_lift != JOINT_JOG_POSITIVE
    finally:
        host.shutdown()
