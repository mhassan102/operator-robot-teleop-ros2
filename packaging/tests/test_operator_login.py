"""Operator login against the registry.

A test client registers as the robot. The operator session checks
auth, offline, busy, and success, and it keeps the socket after
login. Widgets are built with the offscreen Qt platform. Nothing
here starts mlink, Docker, the camera, or the arm.
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

from packaging.operator_app.cli import parse_args  # noqa: E402
from packaging.operator_app.login import (  # noqa: E402
    DEFAULT_REGISTRY,
    LoginResult,
    interpret_login,
    login_message,
    message_for,
)
from packaging.operator_app.session import OperatorSession  # noqa: E402
from signalling.server import listening_uri, start_server  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

_FORBIDDEN = frozenset(range(50000, 50101)) | {8766, 3479}
_ROBOT_ID = "123456789"
_PASSWORD = "AB23CD45"
_HOSTNAME = "lab-robot"

_APP: Any = None


def _register(hostname: str = _HOSTNAME) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "register",
        "robot_id": _ROBOT_ID,
        "password": _PASSWORD,
        "hostname": hostname,
    }


def _inventory() -> dict[str, Any]:
    return {
        "v": 1,
        "type": "inventory",
        "interfaces": [],
        "arms": [],
        "videos": [],
        "camera_page": "",
    }


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


def _run(coro: Any) -> None:
    asyncio.run(asyncio.wait_for(coro, 15))


def test_registry_flag_defaults_to_loopback() -> None:
    assert parse_args([]).registry == DEFAULT_REGISTRY
    assert parse_args([]).registry == "ws://127.0.0.1:8765"
    chosen = "ws://127.0.0.1:9"
    assert parse_args(["--registry", chosen]).registry == chosen


def test_interpret_login_frames() -> None:
    ok = interpret_login('{"v":1,"type":"logged_in","hostname":"lab-robot"}')
    assert ok.ok
    assert ok.hostname == "lab-robot"
    assert interpret_login({"v": 1, "type": "logged_in"}).hostname == ""
    assert interpret_login(
        {"v": 1, "type": "logged_in", "hostname": 5}
    ).hostname == ""
    assert interpret_login('{"v":1,"type":"error","code":"auth"}').code == "auth"
    assert interpret_login({"v": 1, "type": "error", "code": "offline"}).code == "offline"
    assert interpret_login({"v": 1, "type": "error", "code": "busy"}).code == "busy"
    assert interpret_login("{").code == "error"
    assert interpret_login({"v": 2, "type": "logged_in"}).code == "error"
    assert _PASSWORD not in repr(ok)
    assert message_for("auth") == "Wrong password."
    sent = login_message(_ROBOT_ID, _PASSWORD)
    assert sent["type"] == "login"
    assert sent["robot_id"] == _ROBOT_ID


async def _outcomes(uri: str) -> None:
    lone = OperatorSession()
    offline = await lone.login(uri, "000000000", _PASSWORD)
    assert offline.code == "offline"
    assert lone.ws is not None
    await lone.close()

    down = OperatorSession()
    unreachable = await down.login("ws://127.0.0.1:1", _ROBOT_ID, _PASSWORD)
    assert unreachable.code == "unreachable"
    assert down.ws is None

    async with connect(uri) as robot:
        await robot.send(json.dumps(_register()))
        assert json.loads(await asyncio.wait_for(robot.recv(), 2)) == {
            "v": 1,
            "type": "registered",
        }
        operator = OperatorSession()
        auth = await operator.login(uri, _ROBOT_ID, "WRONGPWD")
        assert auth.code == "auth"
        assert auth.hostname == ""
        held = operator.ws
        assert held is not None
        leaked = asyncio.create_task(robot.recv())
        done, _pending = await asyncio.wait({leaked}, timeout=0.3)
        assert not done
        leaked.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await leaked

        success = await operator.login(uri, _ROBOT_ID, _PASSWORD)
        assert success.ok
        assert success.hostname == _HOSTNAME
        assert operator.ws is held
        assert operator.hostname == _HOSTNAME
        assert _PASSWORD not in repr(operator)
        assert _PASSWORD not in success.hostname
        attached = json.loads(await asyncio.wait_for(robot.recv(), 2))
        assert attached == {"v": 1, "type": "operator_attached"}

        other = OperatorSession()
        busy = await other.login(uri, _ROBOT_ID, _PASSWORD)
        assert busy.code == "busy"
        assert other.ws is not None

        await robot.send(json.dumps(_inventory()))
        relayed = json.loads(await asyncio.wait_for(operator.ws.recv(), 2))
        assert relayed == _inventory()
        assert _PASSWORD not in json.dumps(relayed)

        await operator.close()
        await other.close()
        again = await other.login(uri, _ROBOT_ID, _PASSWORD)
        assert again.ok
        assert again.hostname == _HOSTNAME
        await other.close()


def test_login_auth_offline_busy_and_success() -> None:
    async def body() -> None:
        async with _listening() as uri:
            await _outcomes(uri)

    _run(body())


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


class _ServerThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.robot_up = threading.Event()
        self.uri = ""
        self.server: Any = None
        self.error: BaseException | None = None
        self.robot: Any = None
        self.thread = threading.Thread(target=self._run, name="login-test-server", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._open())
        except Exception as exc:
            self.error = exc
        self.ready.set()
        if self.server is None:
            return
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

    def start(self) -> str:
        self.thread.start()
        assert self.ready.wait(5)
        if self.error is not None:
            raise self.error
        assert self.uri.startswith("ws://127.0.0.1:")
        return self.uri

    def hold_robot(self) -> None:
        async def _hold() -> None:
            async with connect(self.uri) as ws:
                await ws.send(json.dumps(_register()))
                raw = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert raw["type"] == "registered"
                self.robot_up.set()
                await asyncio.Future()

        self.robot = asyncio.run_coroutine_threadsafe(_hold(), self.loop)
        if not self.robot_up.wait(5):
            error = self.robot.exception() if self.robot.done() else None
            raise RuntimeError(f"robot did not register: {error}")

    def stop(self) -> None:
        robot = self.robot
        if robot is not None and not robot.done():
            robot.cancel()
            try:
                robot.result(timeout=2)
            except Exception:
                pass
        server = self.server
        if server is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(server.close)
            closed = asyncio.run_coroutine_threadsafe(server.wait_closed(), self.loop)
            try:
                closed.result(timeout=2)
            except Exception:
                pass
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)


def test_window_shows_errors_and_hostname() -> None:
    _qapp()
    from PyQt5.QtWidgets import QComboBox, QLineEdit, QRadioButton

    from packaging.operator_app.window import LoginWindow

    window = LoginWindow("ws://127.0.0.1:9")
    try:
        window.show()
        assert window.login_button.text() == "Log in"
        assert window.password_edit.echoMode() == QLineEdit.Password
        assert window.findChildren(QComboBox) == []
        assert window.findChildren(QRadioButton) == []
        assert window.stack.currentWidget() is window.form_page
        window.submit()
        assert window.status_label.text() == message_for("empty")
        assert window._thread is None

        window.present(LoginResult("auth"))
        assert window.stack.currentWidget() is window.form_page
        assert window.status_label.text() == "Wrong password."
        assert window.session is None
        window.present(LoginResult("offline"))
        assert window.status_label.text() == "That robot is offline."
        assert window.stack.currentWidget() is window.form_page
        window.present(LoginResult("busy"))
        assert window.status_label.text() == "Another operator is already connected."
        assert window.stack.currentWidget() is window.form_page

        window.id_edit.setText(_ROBOT_ID)
        window.password_edit.setText(_PASSWORD)
        window.present(LoginResult("ok", _HOSTNAME))
        assert window.logged_in
        assert window.hostname_label.text() == _HOSTNAME
        assert window.waiting_label.text() == "Waiting for the robot."
        assert window.stack.currentWidget() is window.waiting_page
        assert window.password_edit.text() == ""
        assert window.findChildren(QComboBox) == []
    finally:
        window.close()
        _qapp().processEvents()


def test_window_login_against_registry() -> None:
    _qapp()
    from packaging.operator_app.window import LoginWindow

    server = _ServerThread()
    windows: list[Any] = []
    try:
        uri = server.start()
        server.hold_robot()
        first = LoginWindow(uri)
        second = LoginWindow(uri)
        windows.extend((first, second))
        first.show()
        second.show()

        first.id_edit.setText(_ROBOT_ID)
        first.password_edit.setText("WRONGPWD")
        first.submit()
        assert _pump_until(lambda: first.status_label.text() == "Wrong password.")
        assert first.stack.currentWidget() is first.form_page
        assert first.session is None
        assert first.password_edit.text() == "WRONGPWD"

        first.password_edit.setText(_PASSWORD)
        first.submit()
        assert _pump_until(lambda: first.logged_in)
        assert first.hostname_label.text() == _HOSTNAME
        assert first.waiting_label.text() == "Waiting for the robot."
        assert first.stack.currentWidget() is first.waiting_page
        assert first.session is not None
        assert first.session.ws is not None
        assert first.session.hostname == _HOSTNAME
        assert _PASSWORD not in repr(first.session)

        second.id_edit.setText(_ROBOT_ID)
        second.password_edit.setText(_PASSWORD)
        second.submit()
        assert _pump_until(
            lambda: second.status_label.text()
            == "Another operator is already connected."
        )
        assert second.stack.currentWidget() is second.form_page
        assert second.session is None

        first.close()
        windows.remove(first)
        _qapp().processEvents()
        assert _pump_until(
            lambda: _retry_second(second),
            timeout=5.0,
        )
        assert second.logged_in
        assert second.hostname_label.text() == _HOSTNAME
        assert second.session is not None
    finally:
        for window in windows:
            window.close()
        _qapp().processEvents()
        server.stop()


def _retry_second(window: Any) -> bool:
    if window.logged_in:
        return True
    if window.login_button.isEnabled() and window.status_label.text().startswith(
        "Another"
    ):
        window.submit()
    return False
