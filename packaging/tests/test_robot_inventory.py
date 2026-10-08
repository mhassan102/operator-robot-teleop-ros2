"""Robot credentials and inventory parsing.

Fixtures stand in for sysfs, /proc/net/route, and /dev/serial/by-id.
Nothing here opens a live /dev node, and nothing starts mlink, Docker,
the camera, or the arm.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_TURN = Path(__file__).resolve().parents[2] / "turn"
if str(_TURN) not in sys.path:
    sys.path.insert(0, str(_TURN))

from packaging.robot_app.cli import (  # noqa: E402
    command_for,
    config_path,
    id_line,
    open_identity,
    status_line,
)
from packaging.robot_app.credentials import new_id  # noqa: E402
from packaging.robot_app.inventory import (  # noqa: E402
    camera_page_for,
    collect_links,
    collect_sysfs,
    default_arm,
    default_route_ifaces,
    default_video,
    format_inventory,
    inventory_from,
    read_tailscale_ipv4,
)
from packaging.robot_app.session import (  # noqa: E402
    RobotState,
    _hold,
    interpret,
    register_message,
    run_robot_session,
)
from packaging.tests.drive import (  # noqa: E402
    TOKEN,
    approve_when_up,
    attach,
    listening,
    login,
    recv_type,
    seed,
)
from websockets.asyncio.client import connect  # noqa: E402

SYSFS = """
# physical NICs, plus the ones the window must hide
net wlp0s20f3 up device
net enx00e04c2c4570 up device
net wlan1 down device
net lo unknown loopback
net tailscale0 up tailscale
net docker0 down bridge
net br-7c2a up bridge
net veth1a2b up veth
video video0 Integrated Camera
video video1 USB2.0_CAM1 Metadata
video video2 USB2.0_CAM1
video video3 metadata
video video4 USB2.0_CAM1: USB2.0_CAM1 metadata
"""

LINKS = """
addr wlp0s20f3 10.255.254.58
addr enx00e04c2c4570 10.10.10.1
addr lo 127.0.0.1
addr tailscale0 100.120.193.52
addr docker0 172.17.0.1
route wlp0s20f3
"""

SERIAL = {
    "usb-1a86_USB_Single_Serial_5B3E090040-if00": "/dev/ttyACM1",
    "usb-1a86_USB_Single_Serial_5B61033180-if00": "/dev/ttyACM0",
    "usb-0403_FTDI_other-if00": "/dev/ttyUSB0",
}

FOLLOWER = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00"
LEADER = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B3E090040-if00"

# Taken as this module is imported, before the operator tests build a window.
_ROBOT_MODULES = set(sys.modules)


def test_terminal_commands_and_no_qt() -> None:
    assert command_for("n") is None
    assert command_for(" N ") is None
    assert command_for("q") == "quit"
    assert command_for("quit") == "quit"
    assert command_for("") is None
    assert command_for("password") is None
    assert "PyQt5" not in _ROBOT_MODULES


def test_new_id_is_nine_digits() -> None:
    ids: set[str] = set()
    for _ in range(40):
        robot_id = new_id()
        assert len(robot_id) == 9
        assert robot_id.isdigit()
        ids.add(robot_id)
    assert len(ids) > 1


def test_status_line_has_the_id_and_not_the_password() -> None:
    secret = "not-printed"
    line = status_line("123456789", "registered")
    assert line == "ID 123456789  status registered"
    assert secret not in line
    assert "Password" not in line


def test_identity_file_keeps_mode_and_hides_the_token(tmp_path: Path) -> None:
    path = tmp_path / "robot.json"
    state, created = open_identity(path, None, None)
    assert created is True
    assert state.robot_id.isdigit() and len(state.robot_id) == 9
    assert state.mode == "manual"
    assert state.location == ""
    stored = json.loads(path.read_text(encoding="utf-8"))
    token = stored["token"]
    assert token
    assert token not in repr(state)
    assert token not in id_line(state.robot_id)
    assert token not in status_line(state.robot_id, "registered")
    assert "password" not in path.read_text(encoding="utf-8")
    chosen, again = open_identity(path, "autonomous", "dubai-lab")
    assert again is False
    assert chosen.robot_id == state.robot_id
    assert chosen.mode == "autonomous"
    assert chosen.location == "dubai-lab"
    rewritten = json.loads(path.read_text(encoding="utf-8"))
    assert rewritten["token"] == token
    assert rewritten["mode"] == "autonomous"
    assert rewritten["location"] == "dubai-lab"
    assert (path.stat().st_mode & 0o777) == 0o600
    kept, third = open_identity(path, None, None)
    assert third is False
    assert kept.robot_id == state.robot_id
    assert kept.mode == "autonomous"
    assert kept.location == "dubai-lab"
    assert register_message(
        kept.robot_id, token, kept.hostname, kept.mode, kept.location
    ) == {
        "v": 1,
        "type": "register",
        "robot_id": kept.robot_id,
        "token": token,
        "hostname": kept.hostname,
        "mode": "autonomous",
        "location": "dubai-lab",
    }


def test_config_path_prefers_the_flag_then_the_env(monkeypatch: Any, tmp_path: Path) -> None:
    flag = tmp_path / "flag.json"
    env = tmp_path / "env.json"
    monkeypatch.setenv("TELEOP_ROBOT_CONFIG", str(env))
    assert config_path(str(flag)) == flag
    assert config_path(None) == env
    monkeypatch.delenv("TELEOP_ROBOT_CONFIG")
    assert config_path(None) == Path.home() / ".config" / "teleop" / "robot.json"


def test_inventory_fixture_hides_metadata_and_flags_local_nic() -> None:
    inv = inventory_from(SYSFS, LINKS, SERIAL)
    assert inv["v"] == 1
    assert inv["type"] == "inventory"
    assert inv["camera_page"] == ""
    assert [item["name"] for item in inv["interfaces"]] == [
        "wlp0s20f3",
        "enx00e04c2c4570",
        "wlan1",
    ]
    wifi, usb, down = inv["interfaces"]
    assert wifi == {
        "name": "wlp0s20f3",
        "ipv4": "10.255.254.58",
        "up": True,
        "default_route": True,
    }
    assert usb["name"] == "enx00e04c2c4570"
    assert usb["ipv4"] == "10.10.10.1"
    assert usb["up"] is True
    assert usb["default_route"] is False
    assert down["up"] is False
    assert down["default_route"] is False
    assert down["ipv4"] == ""
    hidden = {"lo", "tailscale0", "docker0", "br-7c2a", "veth1a2b"}
    assert hidden.isdisjoint({item["name"] for item in inv["interfaces"]})

    assert [arm["label"] for arm in inv["arms"]] == ["leader", "follower", "serial"]
    assert default_arm(inv["arms"]) == FOLLOWER
    assert default_arm(inv["arms"]) != LEADER
    follower = inv["arms"][1]
    assert follower["tty"] == "/dev/ttyACM0"
    assert follower["path"] == FOLLOWER

    assert inv["videos"] == [
        {"path": "/dev/video0", "name": "Integrated Camera", "kind": "capture"},
        {"path": "/dev/video2", "name": "USB2.0_CAM1", "kind": "capture"},
    ]
    assert default_video(inv["videos"]) == "/dev/video2"
    assert all("metadata" not in video["name"].lower() for video in inv["videos"])

    page = "http://100.120.193.52:8889/cam/"
    with_page = inventory_from(SYSFS, LINKS, SERIAL, camera_page=page)
    assert with_page["camera_page"] == page
    assert "AB23CD45" not in json.dumps(with_page)


def test_leader_is_not_the_default_without_a_follower() -> None:
    serial = {
        "usb-1a86_USB_Single_Serial_5B3E090040-if00": "/dev/ttyACM1",
        "usb-generic": "/dev/ttyUSB0",
    }
    inv = inventory_from("", "", serial)
    assert [arm["label"] for arm in inv["arms"]] == ["leader", "serial"]
    assert default_arm(inv["arms"]) is None


def test_serial_fixture_does_not_need_dev_nodes(tmp_path: Path) -> None:
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    follower = "usb-1a86_USB_Single_Serial_5B61033180-if00"
    leader = "usb-1a86_USB_Single_Serial_5B3E090040-if00"
    other = "usb-0403_FTDI_other-if00"
    (by_id / follower).symlink_to("../../ttyACM0")
    (by_id / leader).symlink_to("../../ttyACM1")
    (by_id / other).symlink_to("/dev/ttyUSB0")
    (by_id / "not-a-link").write_text("skip")
    inv = inventory_from("", "", by_id)
    by_label = {arm["label"]: arm for arm in inv["arms"]}
    assert set(by_label) == {"follower", "leader", "serial"}
    assert by_label["follower"]["path"] == f"/dev/serial/by-id/{follower}"
    assert by_label["follower"]["tty"] == "/dev/ttyACM0"
    assert by_label["leader"]["tty"] == "/dev/ttyACM1"
    assert by_label["serial"]["tty"] == "/dev/ttyUSB0"
    assert default_arm(inv["arms"]) == by_label["follower"]["path"]
    missing = inventory_from("", "", tmp_path / "absent")
    assert missing["arms"] == []
    assert missing["videos"] == []
    assert missing["interfaces"] == []


def test_window_text_marks_the_follower_and_the_local_nic() -> None:
    text = format_inventory(inventory_from(SYSFS, LINKS, SERIAL))
    assert "wlp0s20f3  10.255.254.58  up  default route" in text
    assert "enx00e04c2c4570  10.10.10.1  up  local-only" in text
    arm_lines = [line for line in text.splitlines() if "serial/by-id" in line]
    follower = next(line for line in arm_lines if "5B61033180" in line)
    leader = next(line for line in arm_lines if "5B3E090040" in line)
    assert follower.endswith("default")
    assert "default" not in leader
    video_lines = [
        line for line in text.splitlines() if "/dev/video" in line
    ]
    assert any(line.endswith("default") and "USB2.0_CAM1" in line for line in video_lines)
    assert all("metadata" not in line.lower() for line in video_lines)


def test_sysfs_fixture_and_proc_route(tmp_path: Path) -> None:
    net = tmp_path / "net"

    def add_iface(name: str, oper: str, bridge: bool = False) -> None:
        iface = net / name
        iface.mkdir(parents=True)
        (iface / "operstate").write_text(oper + "\n")
        if bridge:
            (iface / "bridge").mkdir()

    add_iface("wlp0s20f3", "up")
    add_iface("enx00e04c2c4570", "up")
    add_iface("lo", "unknown")
    add_iface("tailscale0", "up")
    add_iface("docker0", "down")
    add_iface("br-7c2a", "up")
    add_iface("veth1a2b", "up")
    add_iface("br0", "up", bridge=True)
    video = tmp_path / "video4linux"
    (video / "video0").mkdir(parents=True)
    (video / "video0" / "name").write_text("Integrated Camera\n")
    (video / "video2").mkdir()
    (video / "video2" / "name").write_text("USB2.0_CAM1\n")
    (video / "video3").mkdir()
    (video / "video3" / "name").write_text("USB2.0_CAM1 Metadata\n")
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "enx00e04c2c4570\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
        "wlp0s20f3\t00000000\t0101FE0A\t0003\t0\t0\t600\t00000000\t0\t0\t0\n"
    )
    assert default_route_ifaces(route.read_text()) == ["wlp0s20f3"]
    links = collect_links(
        route,
        net,
        ipv4_of=lambda name: {
            "wlp0s20f3": "10.255.254.58",
            "enx00e04c2c4570": "10.10.10.1",
            "lo": "127.0.0.1",
            "br0": "192.168.100.1",
        }.get(name, ""),
    )
    inv = inventory_from(collect_sysfs(net, video), links, {})
    assert [item["name"] for item in inv["interfaces"]] == [
        "enx00e04c2c4570",
        "wlp0s20f3",
    ]
    by_name = {item["name"]: item for item in inv["interfaces"]}
    assert by_name["wlp0s20f3"]["default_route"] is True
    assert by_name["enx00e04c2c4570"]["default_route"] is False
    assert [video_item["path"] for video_item in inv["videos"]] == [
        "/dev/video0",
        "/dev/video2",
    ]
    assert default_video(inv["videos"]) == "/dev/video2"


def test_camera_page_is_blank_when_tailscale_does_not_answer() -> None:
    assert camera_page_for("100.120.193.52") == "http://100.120.193.52:8889/cam/"
    assert camera_page_for("") == ""
    assert camera_page_for(None) == ""
    assert camera_page_for("not-an-ip") == ""
    assert read_tailscale_ipv4(lambda: "100.120.193.52\n") == "100.120.193.52"
    assert read_tailscale_ipv4(lambda: "") == ""
    assert read_tailscale_ipv4(lambda: "not-an-ip\n") == ""

    def missing() -> str:
        raise FileNotFoundError("tailscale")

    def timed_out() -> str:
        raise subprocess.TimeoutExpired(cmd="tailscale", timeout=2)

    assert read_tailscale_ipv4(missing) == ""
    assert read_tailscale_ipv4(timed_out) == ""
    assert camera_page_for(read_tailscale_ipv4(missing)) == ""


def test_interpret_sends_inventory_only_when_the_operator_attaches() -> None:
    attached = interpret('{"v":1,"type":"operator_attached"}')
    assert attached.status == "operator_attached"
    assert attached.send_inventory is True
    registered = interpret('{"v":1,"type":"registered"}')
    assert registered.status == "registered"
    assert registered.send_inventory is False
    error = interpret(
        '{"v":1,"type":"error","code":"bad_id","password":"AB23CD45"}'
    )
    assert error.status == "bad_id"
    assert error.send_inventory is False
    assert "AB23CD45" not in (error.status or "")
    assert interpret("{").status == "bad_json"
    start = interpret(b'{"v":1,"type":"start"}')
    assert start.status is None
    assert start.config is None
    assert start.action == "start"
    assert interpret('{"v":1,"type":"stop"}').action == "stop"
    assert interpret('{"v":2,"type":"inventory"}').send_inventory is False


def _run(coro: Any) -> None:
    asyncio.run(asyncio.wait_for(coro, 15))


async def _attach_sends_inventory(uri: str) -> None:
    inv = inventory_from(
        SYSFS,
        LINKS,
        SERIAL,
        camera_page="http://100.120.193.52:8889/cam/",
    )
    state = RobotState("123456789", TOKEN, "unit-test")
    statuses: list[str] = []
    task = asyncio.create_task(
        run_robot_session(uri, state, lambda: inv, statuses.append)
    )
    try:
        async with connect(uri) as operator:
            await login(operator, "lab")
            await approve_when_up(operator, "123456789")
            await attach(operator, "123456789")
            msg = await recv_type(operator, "inventory")
            assert msg == inv
            assert "password" not in msg
            assert TOKEN not in json.dumps(msg)
            assert msg["interfaces"][1]["default_route"] is False
            await operator.send(json.dumps({"v": 1, "type": "start"}))
            refused = await recv_type(operator, "status")
            assert refused == {
                "v": 1,
                "type": "status",
                "phase": "error",
                "detail": "no config",
            }
        assert "registered" in statuses
        assert "operator_attached" in statuses
        assert TOKEN not in " ".join(statuses)
        assert TOKEN not in repr(state)
    finally:
        state.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_attach_sends_inventory(monkeypatch: Any, tmp_path: Path) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("start must not spawn")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    db = tmp_path / "registry.sqlite"
    seed(db, "lab")

    async def body() -> None:
        async with listening(db) as uri:
            await _attach_sends_inventory(uri)

    _run(body())


class _Pipe:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    def push(self, message: dict[str, Any]) -> None:
        self._queue.put_nowait(json.dumps(message))

    def __aiter__(self) -> _Pipe:
        return self

    async def __anext__(self) -> str:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


def test_presence_follows_register_and_phase(monkeypatch: Any, tmp_path: Path) -> None:
    import packaging.robot_app.session as robot_session

    monkeypatch.setattr(robot_session, "PRESENCE_INTERVAL_S", 0.2)

    async def body() -> None:
        ws = _Pipe()
        state = RobotState("123456789", TOKEN, "unit-test", "manual", "lab")
        wake = asyncio.Event()
        state.attach_loop(asyncio.get_running_loop(), wake)
        statuses: list[str] = []
        task = asyncio.create_task(
            _hold(
                ws,
                state,
                wake,
                lambda: {"v": 1, "type": "inventory"},
                statuses.append,
                None,
                None,
                None,
                tmp_path,
            )
        )
        try:
            deadline = asyncio.get_running_loop().time() + 2
            while not ws.sent and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.02)
            assert ws.sent[0]["type"] == "register"
            assert ws.sent[0]["token"] == TOKEN
            assert "password" not in ws.sent[0]
            ws.push({"v": 1, "type": "registered", "approval": "pending"})

            def _seen(count: int) -> bool:
                return len(ws.sent) >= count

            async def _wait(count: int) -> None:
                limit = asyncio.get_running_loop().time() + 2
                while not _seen(count):
                    if asyncio.get_running_loop().time() > limit:
                        raise AssertionError(ws.sent)
                    await asyncio.sleep(0.02)

            await _wait(2)
            assert ws.sent[1]["type"] == "presence"
            assert ws.sent[1]["phase"] == "idle"
            assert ws.sent[1]["location"] == "lab"
            await _wait(3)
            assert ws.sent[2]["type"] == "presence"
            assert ws.sent[2]["phase"] == "idle"
            state.set_phase("ready")
            await _wait(4)
            assert ws.sent[3]["phase"] == "ready"
            ws.push({"v": 1, "type": "operator_detached"})
            limit = asyncio.get_running_loop().time() + 2
            while not any(
                item.get("type") == "presence" and item.get("phase") == "idle"
                for item in ws.sent[4:]
            ):
                if asyncio.get_running_loop().time() > limit:
                    raise AssertionError(ws.sent)
                await asyncio.sleep(0.02)
            for item in ws.sent:
                if item.get("type") == "presence":
                    assert TOKEN not in json.dumps(item)
            assert TOKEN not in repr(state)
            assert TOKEN not in " ".join(statuses)
        finally:
            state.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    _run(body())
