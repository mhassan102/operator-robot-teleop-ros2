"""Operator login against the registry.

A test client registers as the robot. The operator session checks
auth, offline, busy, and success, and it keeps the socket after
login. The browser pages read /api/session and do not send login
again on refresh. Nothing here starts mlink, Docker, the camera,
or the arm.
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

_FORBIDDEN = (
    frozenset(range(50000, 50101))
    | frozenset(range(5501, 5505))
    | {8765, 8766, 3479, 8090, 8091}
)
_ROBOT_ID = "123456789"
_PASSWORD = "AB23CD45"
_HOSTNAME = "lab-robot"


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

_WEB = Path(__file__).resolve().parents[2] / "teleoperation-prototype" / "web"


def _post(app: Any, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = b"{}" if body is None else json.dumps(body).encode()
    result = app.dispatch("POST", path, raw)
    assert result is not None and result.status == 200
    payload = json.loads(result.body)
    assert isinstance(payload, dict)
    return payload


def test_login_page_has_no_qt_and_stores_no_password() -> None:
    login = (_WEB / "login.html").read_text(encoding="utf-8")
    css = (_WEB / "operate.css").read_text(encoding="utf-8")
    assert 'placeholder="ID"' in login
    assert 'placeholder="Password"' in login
    assert 'type="password"' in login
    assert ">Log in<" in login
    assert "login-button" in login
    assert "width: 120px" in css
    pages = "\n".join(
        ( _WEB / name).read_text(encoding="utf-8")
        for name in ("login.html", "login.js", "config.html", "config.js", "session_bar.js")
    )
    for banned in (
        "localStorage",
        "sessionStorage",
        "beforeunload",
        "8765",
        "8091",
        "PyQt",
    ):
        assert banned not in pages
    config = (_WEB / "config.html").read_text(encoding="utf-8")
    for label in (
        "Tailscale",
        "TURN",
        "Interface 1",
        "Interface 2",
        "Arm",
        "Video",
        "Review",
        "Start",
        "Stop",
        "Logout",
        "Quit",
    ):
        assert label in config
    assert "TURN changes gripper control" in config
    drive = (_WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="session-stop"' in drive
    assert 'id="session-logout"' in drive
    assert 'id="session-quit"' in drive
    assert "/operate.js?v=f8v" in drive
    assert 'SESSION_PATH = "/ws/session"' in (_WEB / "operate.js").read_text(
        encoding="utf-8"
    )
    login_js = (_WEB / "login.js").read_text(encoding="utf-8")
    assert "if (reply.ok) password.value = \"\";" in login_js


def test_login_page_shows_auth_text_and_a_refresh_does_not_login_again() -> None:
    from packaging.operator_app.console import ConsoleApp

    server = _ServerThread()
    apps: list[Any] = []
    try:
        uri = server.start()
        server.hold_robot()
        first = ConsoleApp(uri, web_root=_WEB)
        second = ConsoleApp(uri, web_root=_WEB)
        apps.extend((first, second))
        bad = _post(
            first, "/api/login", {"robot_id": _ROBOT_ID, "password": "WRONGPWD"}
        )
        assert bad["ok"] is False
        assert bad["message"] == "Wrong password."
        assert _PASSWORD not in json.dumps(bad)
        assert first.login_sends == 1
        good = _post(
            first, "/api/login", {"robot_id": _ROBOT_ID, "password": _PASSWORD}
        )
        assert good["ok"] is True
        assert good["hostname"] == _HOSTNAME
        assert good["message"] == "Waiting for the robot."
        assert first.login_sends == 2
        refresh = _post(
            first, "/api/login", {"robot_id": _ROBOT_ID, "password": _PASSWORD}
        )
        assert refresh["ok"] is True
        assert first.login_sends == 2
        session = json.loads(first.dispatch("GET", "/api/session").body)
        assert session["logged_in"] is True
        assert session["robot_id"] == _ROBOT_ID
        assert _PASSWORD not in json.dumps(session)
        assert "ws://" not in json.dumps(session)
        assert first.login_sends == 2
        busy = _post(
            second, "/api/login", {"robot_id": _ROBOT_ID, "password": _PASSWORD}
        )
        assert busy["message"] == "Another operator is already connected."
        first.close()
        apps.remove(first)
        logged = False
        reply: dict[str, Any] = {}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            reply = _post(
                second, "/api/login", {"robot_id": _ROBOT_ID, "password": _PASSWORD}
            )
            if reply.get("ok"):
                logged = True
                break
            time.sleep(0.05)
        assert logged, reply
        assert reply["hostname"] == _HOSTNAME
    finally:
        for app in list(apps):
            app.close()
        server.stop()


def test_empty_login_does_not_open_a_socket() -> None:
    from packaging.operator_app.console import ConsoleApp

    app = ConsoleApp("ws://127.0.0.1:9", web_root=_WEB)
    try:
        reply = _post(app, "/api/login", {"robot_id": "  ", "password": ""})
        assert reply["message"] == message_for("empty")
        assert app.login_sends == 0
        assert app._session.ws is None
    finally:
        app.close()
