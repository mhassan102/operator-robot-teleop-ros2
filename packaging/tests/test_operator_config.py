"""Config page and the robot's review of that message.

Widget values come from a fixture inventory. A matching message is
``config_ok``, including when Interface 2 is set. An unknown video
path is ``bad_config``. Nothing here opens a serial port or starts
mlink, Docker, the camera, or the arm.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

_FORBIDDEN = frozenset(range(50000, 50101)) | {8766, 3479}
_ROBOT_ID = "123456789"
_PASSWORD = "AB23CD45"
_HOSTNAME = "lab-robot"
_APP: Any = None

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


def _qapp() -> Any:
    global _APP
    from PyQt5.QtWidgets import QApplication

    if _APP is None:
        existing = QApplication.instance()
        _APP = existing if existing is not None else QApplication(["teleop-operator-test"])
    return _APP


def _pump_until(predicate: Any, timeout: float = 5.0) -> bool:
    app = _qapp()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate())


def test_config_page_values() -> None:
    _qapp()
    from PyQt5.QtWidgets import QComboBox, QLineEdit, QPushButton

    from packaging.operator_app.config_page import ConfigPage
    from packaging.operator_app.login import LoginResult
    from packaging.operator_app.window import LoginWindow

    inv = _inventory()
    window = LoginWindow("ws://127.0.0.1:9", operator_nic="wlo1")
    try:
        window.show()
        assert window.findChildren(QComboBox) == []
        window.present(LoginResult("ok", _HOSTNAME))
        assert window.stack.currentWidget() is window.waiting_page
        assert window.findChildren(QComboBox) == []
        window.show_inventory(inv)
        page = window.config_page
        assert page is not None
        assert window.stack.currentWidget() is page
        assert page.hostname_label.text() == _HOSTNAME
        assert page.link_tailscale.isChecked()
        assert not page.link_turn.isChecked()
        assert page.link_help.text() == LINK_HELP
        assert page.iface1.currentData() == "wlp0s20f3"
        assert page.iface1.findData("enx00e04c2c4570") < 0
        assert page.iface2.currentIndex() == 0
        local = page.iface2.findData("enx00e04c2c4570")
        assert local > 0
        assert page.iface2.itemText(local) == "enx00e04c2c4570 (local-only)"
        assert "tailscale0" not in page.iface2.itemText(0)
        assert page.arm.currentData() == FOLLOWER
        assert page.arm.findData(LEADER) >= 0
        assert "ttyACM0" in page.arm.currentText()
        assert page.video.currentData() == "/dev/video2"
        assert page.video.findData("/dev/video0") >= 0
        joined = " ".join(
            page.video.itemText(i) for i in range(page.video.count())
        )
        assert "Metadata" not in joined
        assert page.operator_network.text() == "Operator network: wlo1"
        assert page.findChildren(QLineEdit) == []
        assert [button.text() for button in page.findChildren(QPushButton)] == [
            "Review"
        ]
        message = page.current_config()
        assert message == _good()
        assert "wlo1" not in json.dumps(message)
        page.review_button.click()
        assert page.review_status.text() == "Cannot reach the registry."

        page.link_turn.setChecked(True)
        page.iface2.setCurrentIndex(local)
        page.apply(inv, "wlo1", _HOSTNAME)
        assert page.link_turn.isChecked()
        assert page.iface2.currentData() == "enx00e04c2c4570"
        kept = page.current_config()
        assert kept["link"] == "turn"
        assert kept["iface2"] == "enx00e04c2c4570"
        assert set(kept) == {"v", "type", "link", "iface1", "iface2", "arm", "video"}
        reply, line = review_config(kept, inv)
        assert reply == {"v": 1, "type": "config_ok"}
        assert "(local-only)" not in line

        window.present_inbound(
            {
                "v": 1,
                "type": "error",
                "code": "bad_config",
                "detail": "unknown video",
            }
        )
        assert page.review_status.text() == "unknown video"
        window.present_inbound({"v": 1, "type": "config_ok"})
        assert page.review_status.text() == "config_ok"
    finally:
        window.close()
        _qapp().processEvents()

    bare = inventory_from(
        "net wlo1 up device\nvideo video0 Integrated Camera\n",
        "addr wlo1 192.168.222.56\nroute wlo1\n",
        {"usb-1a86_USB_Single_Serial_5B3E090040-if00": "/dev/ttyACM1"},
    )
    bare_page = ConfigPage()
    bare_page.apply(bare, "wlo1", _HOSTNAME)
    try:
        assert bare_page.arm.count() == 1
        assert bare_page.arm.currentIndex() == -1
        assert "5B3E090040" in bare_page.arm.itemText(0)
        assert bare_page.video.count() == 1
        assert bare_page.video.currentIndex() == -1
        assert bare_page.current_config()["arm"] == ""
        assert bare_page.current_config()["video"] == ""
        assert bare_page.iface1.currentData() == "wlo1"
    finally:
        bare_page.close()
        _qapp().processEvents()


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
    _qapp()
    from packaging.operator_app.window import LoginWindow

    inv = _inventory()
    rig = _Rig(inv)
    window = None
    try:
        uri = rig.start()
        assert _pump_until(lambda: "registered" in rig.statuses)
        window = LoginWindow(uri, operator_nic="wlo1")
        window.show()
        window.id_edit.setText(_ROBOT_ID)
        window.password_edit.setText(_PASSWORD)
        window.submit()
        assert _pump_until(
            lambda: window is not None
            and window.config_page is not None
            and window.stack.currentWidget() is window.config_page
        ), window.status_label.text()
        page = window.config_page
        assert page is not None
        assert page.hostname_label.text() == _HOSTNAME
        assert page.operator_network.text() == "Operator network: wlo1"
        assert page.arm.currentData() == FOLLOWER
        assert page.video.currentData() == "/dev/video2"
        assert page.iface2.currentIndex() == 0
        page.review_button.click()
        assert page.review_status.text() == "Sending..."
        assert _pump_until(
            lambda: page.review_status.text() == "config_ok"
            and any(line == _GOOD_LINE for line in rig.lines)
        ), page.review_status.text()
        assert all("wlo1" not in line for line in rig.lines)
        local = page.iface2.findData("enx00e04c2c4570")
        page.iface2.setCurrentIndex(local)
        page.review_button.click()
        assert _pump_until(
            lambda: page.review_status.text() == "config_ok"
            and any(
                "iface2 enx00e04c2c4570" in line and line.endswith("config_ok")
                for line in rig.lines
            )
        ), (page.review_status.text(), list(rig.lines))
        assert all(_PASSWORD not in line for line in rig.lines)
        assert "start_daemon" not in "\n".join(rig.lines)
    finally:
        if window is not None:
            window.close()
            _qapp().processEvents()
        rig.stop()
