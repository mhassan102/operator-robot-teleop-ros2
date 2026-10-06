"""Config page and the robot's review of that message.

Page values come from a fixture inventory. A matching message is
``config_ok``, including when Interface 2 is set. An unknown video
path is ``bad_config``. Nothing here opens a serial port or starts
mlink, Docker, the camera, or the arm.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

_TURN = Path(__file__).resolve().parents[2] / "turn"
if str(_TURN) not in sys.path:
    sys.path.insert(0, str(_TURN))

from packaging.operator_app.config import (  # noqa: E402
    LINK_HELP,
    config_message,
    interface1_choices,
    interface2_choices,
    local_default_nic,
    operator_network_text,
    preferred_arm,
    preferred_video,
)
from packaging.robot_app.config import review_config  # noqa: E402
from packaging.robot_app.inventory import inventory_from  # noqa: E402
from packaging.robot_app.session import (  # noqa: E402
    RobotState,
    interpret,
    run_robot_session,
)
from signalling.server import listening_uri, start_server  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

_FORBIDDEN = (
    frozenset(range(50000, 50101))
    | frozenset(range(5501, 5505))
    | {8765, 8766, 3479, 8090, 8091}
)
_ROBOT_ID = "123456789"
_PASSWORD = "AB23CD45"
_HOSTNAME = "lab-robot"
FOLLOWER = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00"
LEADER = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B3E090040-if00"
_GOOD_LINE = (
    "Config  link tailscale  iface1 wlp0s20f3  iface2 none  "
    f"arm {FOLLOWER}  video /dev/video2  config_ok"
)

SYSFS = """
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
"""

LINKS = """
addr wlp0s20f3 10.255.254.58
addr enx00e04c2c4570 10.10.10.1
addr lo 127.0.0.1
addr tailscale0 100.120.193.52
route wlp0s20f3
"""

SERIAL = {
    "usb-1a86_USB_Single_Serial_5B3E090040-if00": "/dev/ttyACM1",
    "usb-1a86_USB_Single_Serial_5B61033180-if00": "/dev/ttyACM0",
    "usb-0403_FTDI_other-if00": "/dev/ttyUSB0",
}

_ROUTE = """
Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT
wlo1 00000000 0123A8C0 0003 0 0 600 00000000 0 0 0
tailscale0 00000000 00000000 0001 0 0 50 00000000 0 0 0
eth0 00000000 0101A8C0 0003 0 0 100 00000000 0 0 0
lo 00000000 00000000 0001 0 0 0 00000000 0 0 0
docker0 00000000 00000000 0001 0 0 10 00000000 0 0 0
"""


def _inventory() -> dict[str, Any]:
    return inventory_from(
        SYSFS,
        LINKS,
        SERIAL,
        camera_page="http://100.120.193.52:8889/cam/",
    )


def _good() -> dict[str, Any]:
    return config_message("tailscale", "wlp0s20f3", None, FOLLOWER, "/dev/video2")


def test_choices_from_fixture_inventory() -> None:
    inv = _inventory()
    names = [item["name"] for item in inv["interfaces"]]
    assert names == ["wlp0s20f3", "enx00e04c2c4570", "wlan1"]
    assert [choice.value for choice in interface1_choices(inv)] == ["wlp0s20f3"]
    second = interface2_choices(inv)
    assert [(choice.label, choice.value) for choice in second] == [
        ("None", None),
        ("wlp0s20f3", "wlp0s20f3"),
        ("enx00e04c2c4570 (local-only)", "enx00e04c2c4570"),
        ("wlan1 (local-only)", "wlan1"),
    ]
    assert preferred_arm(inv) == FOLLOWER
    assert preferred_video(inv) == "/dev/video2"
    assert all("Metadata" not in video["name"] for video in inv["videos"])
    assert "tailscale0" not in names
    assert "docker0" not in names


def test_review_config_ok_iface2_and_unknown_video() -> None:
    inv = _inventory()
    reply, line = review_config(_good(), inv)
    assert reply == {"v": 1, "type": "config_ok"}
    assert line == _GOOD_LINE
    assert _PASSWORD not in line

    chosen = config_message(
        "tailscale", "wlp0s20f3", "enx00e04c2c4570", FOLLOWER, "/dev/video2"
    )
    reply, line = review_config(chosen, inv)
    assert reply == {"v": 1, "type": "config_ok"}
    assert "iface2 enx00e04c2c4570" in line
    assert "(local-only)" not in line
    assert line.endswith("config_ok")

    turned = config_message("turn", "wlp0s20f3", None, FOLLOWER, "/dev/video2")
    assert review_config(turned, inv)[0] == {"v": 1, "type": "config_ok"}

    bad = dict(_good())
    bad["video"] = "/dev/video9"
    reply, line = review_config(bad, inv)
    assert reply == {
        "v": 1,
        "type": "error",
        "code": "bad_config",
        "detail": "unknown video",
    }
    assert line.endswith("bad_config  unknown video")
    assert _PASSWORD not in json.dumps(reply)

    sneaky = dict(_good())
    sneaky["password"] = _PASSWORD
    reply, line = review_config(sneaky, inv)
    assert reply == {"v": 1, "type": "config_ok"}
    assert _PASSWORD not in line
    assert "password" not in reply


def test_review_rejects_other_bad_fields() -> None:
    inv = _inventory()
    local = config_message(
        "tailscale", "enx00e04c2c4570", None, FOLLOWER, "/dev/video2"
    )
    assert (
        review_config(local, inv)[0]["detail"]
        == "iface1 is not a default-route interface"
    )
    missing = _good()
    del missing["iface2"]
    assert review_config(missing, inv)[0]["detail"] == "unknown interface"
    unknown = config_message(
        "tailscale", "wlp0s20f3", "not-a-nic", FOLLOWER, "/dev/video2"
    )
    assert review_config(unknown, inv)[0]["detail"] == "unknown interface"
    ice = config_message("ice", "wlp0s20f3", None, FOLLOWER, "/dev/video2")
    assert review_config(ice, inv)[0]["detail"] == "link must be tailscale or turn"
    empty = {
        "interfaces": [{"name": "wlo1", "default_route": True}],
        "arms": [],
        "videos": [{"path": "/dev/video0", "name": "Integrated Camera"}],
    }
    no_arm = config_message("tailscale", "wlo1", None, "", "")
    reply, line = review_config(no_arm, empty)
    assert reply["detail"] == "unknown arm"
    assert "iface2 none" in line
    assert review_config("nope", inv)[0]["detail"] == "bad config"


def test_operator_nic_skips_overlay() -> None:
    assert local_default_nic(_ROUTE) == "eth0"
    only = (
        "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
        "tailscale0 00000000 00000000 0001 0 0 5 00000000 0 0 0\n"
        "wlo1 00000000 0123A8C0 0003 0 0 600 00000000 0 0 0\n"
    )
    assert local_default_nic(only) == "wlo1"
    assert local_default_nic("") == ""
    assert operator_network_text("wlo1") == "Operator network: wlo1"
    assert operator_network_text("  ") == "Operator network: (none)"


def test_interpret_marks_config() -> None:
    event = interpret(json.dumps(_good()))
    assert event.status is None
    assert event.send_inventory is False
    assert event.config is not None
    assert event.config["link"] == "tailscale"
    assert interpret('{"v":1,"type":"start"}').config is None


@contextlib.asynccontextmanager
async def _listening():
    server = None
    for _ in range(16):
        candidate = await start_server("127.0.0.1", 0)
        safe = True
        for sock in candidate.sockets:
            host, port = sock.getsockname()[:2]
            if host != "127.0.0.1" or port in _FORBIDDEN:
                safe = False
                break
        if safe and candidate.sockets:
            server = candidate
            break
        candidate.close()
        await candidate.wait_closed()
    if server is None:
        raise RuntimeError("could not bind 127.0.0.1 on a permitted port")
    try:
        yield listening_uri(server)
    finally:
        server.close()
        await server.wait_closed()


async def _login_when_up(ws: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    while True:
        await ws.send(
            json.dumps(
                {
                    "v": 1,
                    "type": "login",
                    "robot_id": _ROBOT_ID,
                    "password": _PASSWORD,
                }
            )
        )
        msg = json.loads(await asyncio.wait_for(ws.recv(), 2))
        if msg.get("type") == "logged_in":
            return
        assert msg == {"v": 1, "type": "error", "code": "offline"}
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("robot did not register")
        await asyncio.sleep(0.05)


async def _robot_answers(uri: str) -> None:
    inv = _inventory()
    state = RobotState(_ROBOT_ID, _PASSWORD, _HOSTNAME)
    statuses: list[str] = []
    lines: list[str] = []
    task = asyncio.create_task(
        run_robot_session(
            uri,
            state,
            lambda: inv,
            statuses.append,
            on_config=lines.append,
        )
    )
    try:
        async with connect(uri) as operator:
            await _login_when_up(operator)
            inventory = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert inventory["type"] == "inventory"
            await operator.send(json.dumps(_good()))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2)) == {
                "v": 1,
                "type": "config_ok",
            }
            bad = dict(_good())
            bad["video"] = "/dev/video9"
            await operator.send(json.dumps(bad))
            reply = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert reply == {
                "v": 1,
                "type": "error",
                "code": "bad_config",
                "detail": "unknown video",
            }
            chosen = config_message(
                "turn", "wlp0s20f3", "enx00e04c2c4570", FOLLOWER, "/dev/video2"
            )
            await operator.send(json.dumps(chosen))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2)) == {
                "v": 1,
                "type": "config_ok",
            }
        assert lines[0] == _GOOD_LINE
        assert lines[1].endswith("bad_config  unknown video")
        assert "iface2 enx00e04c2c4570" in lines[2]
        assert lines[2].endswith("config_ok")
        assert "link turn" in lines[2]
        assert all(_PASSWORD not in line for line in lines)
        assert statuses.count("config_ok") == 2
        assert statuses.count("bad_config") == 1
    finally:
        state.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_robot_answers_config_on_the_socket() -> None:
    async def body() -> None:
        async with _listening() as uri:
            await _robot_answers(uri)

    asyncio.run(asyncio.wait_for(body(), 15))



_WEB = Path(__file__).resolve().parents[2] / "teleoperation-prototype" / "web"


class _Script:
    def __init__(self) -> None:
        self.ws: Any = None
        self._loop: Any = None
        self._queue: Any = None

    def bind(self, loop: Any) -> None:
        self._loop = loop
        self._queue = asyncio.Queue()

    async def login(self, url: str, robot_id: str, password: str) -> Any:
        from packaging.operator_app.login import LoginResult

        self.ws = object()
        return LoginResult("ok", _HOSTNAME)

    async def send_json(self, message: dict[str, Any]) -> None:
        return None

    async def next_message(self) -> dict[str, Any] | None:
        return await self._queue.get()

    def push(self, message: dict[str, Any] | None) -> None:
        self._loop.call_soon_threadsafe(self._queue.put_nowait, message)

    async def close(self) -> None:
        self.ws = None
        if self._queue is not None:
            self._queue.put_nowait(None)


def _until(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _post(app: Any, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = b"{}" if body is None else json.dumps(body).encode()
    result = app.dispatch("POST", path, raw)
    assert result is not None and result.status == 200
    return json.loads(result.body)


def test_config_page_values() -> None:
    from packaging.operator_app.console import ConsoleApp

    script = _Script()
    app = ConsoleApp(
        "ws://127.0.0.1:9",
        web_root=_WEB,
        operator_nic="wlo1",
        session=script,
    )
    try:
        page = (_WEB / "config.html").read_text(encoding="utf-8")
        assert "Review" in page and "Start" in page
        assert 'id="session-stop"' in page
        assert 'id="session-logout"' in page
        assert 'id="session-quit"' in page
        reply = _post(
            app, "/api/login", {"robot_id": _ROBOT_ID, "password": _PASSWORD}
        )
        assert reply["ok"] is True
        script.push(_inventory())
        assert _until(lambda: app.snapshot()["view"] == "config")
        snap = app.snapshot()
        assert snap["hostname"] == _HOSTNAME
        assert snap["link_help"] == LINK_HELP
        assert snap["selected"]["link"] == "tailscale"
        assert snap["selected"]["iface1"] == "wlp0s20f3"
        assert snap["selected"]["iface2"] is None
        iface1_values = [item["value"] for item in snap["choices"]["iface1"]]
        assert iface1_values == ["wlp0s20f3"]
        iface2 = snap["choices"]["iface2"]
        assert iface2[0] == {"label": "None", "value": None}
        local = next(item for item in iface2 if item["value"] == "enx00e04c2c4570")
        assert local["label"] == "enx00e04c2c4570 (local-only)"
        assert snap["selected"]["arm"] == FOLLOWER
        assert any(item["value"] == LEADER for item in snap["choices"]["arm"])
        assert "ttyACM0" in next(
            item["label"] for item in snap["choices"]["arm"] if item["value"] == FOLLOWER
        )
        assert snap["selected"]["video"] == "/dev/video2"
        joined = " ".join(item["label"] for item in snap["choices"]["video"])
        assert "Metadata" not in joined
        assert snap["operator_network"] == "Operator network: wlo1"
        assert "wlo1" not in json.dumps(_good())
        offline = _post(app, "/api/review", _good())
        assert offline["message"] == "Sending..."
    finally:
        app.close()

    bare_script = _Script()
    bare = ConsoleApp(
        "ws://127.0.0.1:9",
        web_root=_WEB,
        operator_nic="wlo1",
        session=bare_script,
    )
    try:
        _post(bare, "/api/login", {"robot_id": _ROBOT_ID, "password": _PASSWORD})
        bare_inventory = inventory_from(
            "net wlo1 up device\nvideo video0 Integrated Camera\n",
            "addr wlo1 192.168.222.56\nroute wlo1\n",
            {"usb-1a86_USB_Single_Serial_5B3E090040-if00": "/dev/ttyACM1"},
        )
        bare_script.push(bare_inventory)
        assert _until(lambda: bare.snapshot()["view"] == "config")
        selected = bare.snapshot()["selected"]
        assert len(bare.snapshot()["choices"]["arm"]) == 1
        assert "5B3E090040" in bare.snapshot()["choices"]["arm"][0]["label"]
        assert selected["arm"] == ""
        assert selected["video"] == ""
        assert selected["iface1"] == "wlo1"
    finally:
        bare.close()

class _Rig:
    def __init__(self, inventory: dict[str, Any]) -> None:
        self.inventory = inventory
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.uri = ""
        self.server: Any = None
        self.error: BaseException | None = None
        self.robot_task: asyncio.Task[None] | None = None
        self.state = RobotState(_ROBOT_ID, _PASSWORD, _HOSTNAME)
        self.statuses: list[str] = []
        self.lines: list[str] = []
        self.thread = threading.Thread(target=self._run, name="config-test-server", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._open())
        except Exception as exc:
            self.error = exc
        self.ready.set()
        if self.server is None:
            return
        self.loop.call_soon(self._start_robot)
        self.loop.run_forever()

    async def _open(self) -> None:
        for _ in range(16):
            candidate = await start_server("127.0.0.1", 0)
            safe = True
            for sock in candidate.sockets:
                host, port = sock.getsockname()[:2]
                if host != "127.0.0.1" or port in _FORBIDDEN:
                    safe = False
                    break
            if safe and candidate.sockets:
                self.server = candidate
                self.uri = listening_uri(candidate)
                return
            candidate.close()
            await candidate.wait_closed()
        raise RuntimeError("could not bind 127.0.0.1 on a permitted port")

    def _start_robot(self) -> None:
        self.robot_task = asyncio.create_task(
            run_robot_session(
                self.uri,
                self.state,
                lambda: self.inventory,
                self.statuses.append,
                on_config=self.lines.append,
            )
        )

    def start(self) -> str:
        self.thread.start()
        assert self.ready.wait(5)
        if self.error is not None:
            raise self.error
        assert self.uri.startswith("ws://127.0.0.1:")
        return self.uri

    def stop(self) -> None:
        self.state.stop()
        loop = self.loop
        if loop.is_running():

            async def _finish() -> None:
                task = self.robot_task
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                server = self.server
                if server is not None:
                    server.close()
                    await server.wait_closed()

            fut = asyncio.run_coroutine_threadsafe(_finish(), loop)
            try:
                fut.result(timeout=3)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        self.thread.join(timeout=3)

def test_review_button_reaches_config_ok() -> None:
    from packaging.operator_app.console import ConsoleApp

    inv = _inventory()
    rig = _Rig(inv)
    app = None
    try:
        uri = rig.start()
        assert _until(lambda: "registered" in rig.statuses)
        app = ConsoleApp(uri, web_root=_WEB, operator_nic="wlo1")
        reply = _post(
            app, "/api/login", {"robot_id": _ROBOT_ID, "password": _PASSWORD}
        )
        assert reply["ok"] is True, reply
        assert _until(lambda: app.snapshot()["view"] == "config"), app.snapshot()
        snap = app.snapshot()
        assert snap["hostname"] == _HOSTNAME
        assert snap["operator_network"] == "Operator network: wlo1"
        assert snap["selected"]["arm"] == FOLLOWER
        assert snap["selected"]["video"] == "/dev/video2"
        assert snap["selected"]["iface2"] is None
        reviewed = _post(app, "/api/review", {})
        assert reviewed["review_status"] == "Sending..."
        assert _until(
            lambda: app.snapshot()["review_status"] == "config_ok"
            and any(line == _GOOD_LINE for line in rig.lines)
        ), (app.snapshot()["review_status"], list(rig.lines))
        assert all("wlo1" not in line for line in rig.lines)
        chosen = config_message(
            "tailscale", "wlp0s20f3", "enx00e04c2c4570", FOLLOWER, "/dev/video2"
        )
        _post(app, "/api/review", chosen)
        assert _until(
            lambda: any(
                "iface2 enx00e04c2c4570" in line and line.endswith("config_ok")
                for line in rig.lines
            )
        ), list(rig.lines)
        assert all(_PASSWORD not in line for line in rig.lines)
        assert "start_daemon" not in "\n".join(rig.lines)
    finally:
        if app is not None:
            app.close()
        rig.stop()
