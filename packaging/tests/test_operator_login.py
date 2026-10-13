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
    dashboard_message,
    interpret_login,
    login_message,
    message_for,
)
from packaging.operator_app.session import OperatorSession  # noqa: E402
from packaging.tests.drive import (  # noqa: E402
    OPERATOR_PASSWORD,
    TOKEN,
    listening,
    seed,
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


def _register(hostname: str = _HOSTNAME) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "register",
        "robot_id": _ROBOT_ID,
        "token": TOKEN,
        "hostname": hostname,
        "mode": "manual",
        "location": "",
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
    assert message_for("empty") == "Enter the operator name and password."
    assert message_for("already_attached") == "This account is already signed in."
    assert message_for("legacy_login") == "The registry sent an unexpected reply."
    assert dashboard_message("not_found") == "That robot is not registered."
    assert dashboard_message("pending") == "That robot is not approved."
    assert dashboard_message("offline") == "That robot is offline."
    assert dashboard_message("busy") == "Another operator is already connected."
    assert (
        dashboard_message("already_attached")
        == "This account is already operating a robot."
    )
    assert (
        dashboard_message("stale")
        == "The robot is not answering. Stopping the arm."
    )
    sent = login_message("lab", _PASSWORD)
    assert sent == {
        "v": 1,
        "type": "login",
        "username": "lab",
        "password": _PASSWORD,
    }
    assert "robot_id" not in sent


async def _session_frame(session: OperatorSession) -> dict[str, Any]:
    msg = await asyncio.wait_for(session.next_message(), 5)
    assert isinstance(msg, dict)
    return msg


async def _until(session: OperatorSession, kind: str) -> dict[str, Any]:
    for _ in range(8):
        msg = await _session_frame(session)
        if msg.get("type") == "fleet":
            continue
        if msg.get("type") != kind:
            raise AssertionError(msg)
        return msg
    raise AssertionError(kind)


async def _outcomes(uri: str) -> None:
    down = OperatorSession()
    unreachable = await down.login("ws://127.0.0.1:1", "lab", OPERATOR_PASSWORD)
    assert unreachable.code == "unreachable"
    assert down.ws is None

    async with connect(uri) as robot:
        await robot.send(json.dumps(_register()))
        registered = json.loads(await asyncio.wait_for(robot.recv(), 2))
        assert registered == {
            "v": 1,
            "type": "registered",
            "approval": "pending",
        }
        operator = OperatorSession()
        auth = await operator.login(uri, "lab", "WRONGPWD")
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

        success = await operator.login(uri, "lab", OPERATOR_PASSWORD)
        assert success.ok
        assert success.hostname == ""
        assert operator.ws is held
        assert operator.hostname == ""
        assert OPERATOR_PASSWORD not in repr(operator)
        assert TOKEN not in repr(operator)
        pending = operator.take_pending()
        if pending:
            assert pending[0]["type"] == "fleet"
        else:
            fleet = await _session_frame(operator)
            assert fleet["type"] == "fleet"

        await operator.send_json({"v": 1, "type": "attach", "robot_id": _ROBOT_ID})
        early = await _until(operator, "error")
        assert early["code"] == "pending"

        await operator.send_json({"v": 1, "type": "approve", "robot_id": _ROBOT_ID})
        approved = await _until(operator, "approved")
        assert approved["robot_id"] == _ROBOT_ID
        await operator.send_json({"v": 1, "type": "attach", "robot_id": _ROBOT_ID})
        attached = await _until(operator, "attached")
        assert attached["robot_id"] == _ROBOT_ID
        assert attached["hostname"] == _HOSTNAME

        robot_note = json.loads(await asyncio.wait_for(robot.recv(), 2))
        assert robot_note == {"v": 1, "type": "operator_attached"}
        await robot.send(json.dumps(_inventory()))
        relayed = await _until(operator, "inventory")
        assert relayed == _inventory()
        assert OPERATOR_PASSWORD not in json.dumps(relayed)
        assert TOKEN not in json.dumps(relayed)

        same = OperatorSession()
        taken = await same.login(uri, "lab", OPERATOR_PASSWORD)
        assert taken.code == "already_attached"
        assert same.ws is not None
        await same.close()

        other = OperatorSession()
        other_login = await other.login(uri, "other", OPERATOR_PASSWORD)
        assert other_login.ok
        assert other_login.hostname == ""
        other_pending = other.take_pending()
        if not other_pending:
            assert (await _session_frame(other))["type"] == "fleet"
        await other.send_json({"v": 1, "type": "attach", "robot_id": _ROBOT_ID})
        busy = await _until(other, "error")
        assert busy["code"] == "busy"
        await operator.close()
        await other.close()


def test_login_auth_offline_busy_and_success(tmp_path: Path) -> None:
    db = tmp_path / "registry.sqlite"
    seed(db, "lab", "other")

    async def body() -> None:
        async with listening(db) as uri:
            await _outcomes(uri)

    _run(body())



class _ServerThread:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
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
            candidate = await start_server("127.0.0.1", 0, db_path=self.db_path)
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
                assert raw.get("approval") == "pending"
                self.robot_up.set()
                async for incoming in ws:
                    msg = json.loads(incoming)
                    if msg.get("type") == "operator_attached":
                        await ws.send(json.dumps(_inventory()))

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
    assert 'placeholder="Operator"' in login
    assert 'placeholder="Password"' in login
    assert 'type="password"' in login
    assert ">Log in<" in login
    assert "login-button" in login
    assert "width: 120px" in css
    pages = "\n".join(
        (_WEB / name).read_text(encoding="utf-8")
        for name in (
            "login.html",
            "login.js",
            "config.html",
            "config.js",
            "session_bar.js",
            "fleet.html",
            "fleet.js",
        )
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
        "Release",
        "Sign out",
        "Quit",
    ):
        assert label in config
    assert "Logout" not in config
    assert "TURN changes gripper control" in config
    drive = (_WEB / "index.html").read_text(encoding="utf-8")
    assert 'id="session-stop"' in drive
    assert 'id="session-release"' in drive
    assert 'id="session-signout"' in drive
    assert 'id="session-quit"' in drive
    assert 'id="session-logout"' not in drive
    assert "/operate.js?v=f9v" in drive
    assert 'SESSION_PATH = "/ws/session"' in (_WEB / "operate.js").read_text(
        encoding="utf-8"
    )
    login_js = (_WEB / "login.js").read_text(encoding="utf-8")
    assert "if (reply.ok) password.value = \"\";" in login_js
    assert 'window.location.assign("/fleet")' in login_js
    fleet = (_WEB / "fleet.html").read_text(encoding="utf-8")
    for label in (
        "count-registered",
        "count-autonomous",
        "count-needs-manual",
        "count-in-teleop",
        ">ID<",
        ">Location<",
        ">Status<",
        ">Mode<",
        ">Action<",
        'id="session-signout"',
        'id="session-quit"',
    ):
        assert label in fleet
    assert "Release" not in fleet
    assert "Logout" not in fleet
    assert 'id="session-stop"' not in fleet
    assert 'id="session-release"' not in fleet
    fleet_js = (_WEB / "fleet.js").read_text(encoding="utf-8")
    assert "Approve" in fleet_js
    assert "Operate" in fleet_js
    assert "/api/approve" in fleet_js
    assert "/api/attach" in fleet_js
    assert "Release" not in fleet_js
    assert "Sign out" not in fleet_js
    assert 'snapshot.view === "fleet"' in (_WEB / "session_bar.js").read_text(
        encoding="utf-8"
    )


def _until_snap(predicate: Any, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def test_login_page_shows_auth_text_and_a_refresh_does_not_login_again(
    tmp_path: Path,
) -> None:
    from packaging.operator_app.console import ConsoleApp

    db = tmp_path / "registry.sqlite"
    seed(db, "lab")
    server = _ServerThread(str(db))
    apps: list[Any] = []
    try:
        uri = server.start()
        server.hold_robot()
        first = ConsoleApp(uri, web_root=_WEB)
        second = ConsoleApp(uri, web_root=_WEB)
        apps.extend((first, second))
        bad = _post(first, "/api/login", {"username": "lab", "password": "WRONGPWD"})
        assert bad["ok"] is False
        assert bad["message"] == "Wrong password."
        assert OPERATOR_PASSWORD not in json.dumps(bad)
        assert first.login_sends == 1
        good = _post(
            first, "/api/login", {"username": "lab", "password": OPERATOR_PASSWORD}
        )
        assert good["ok"] is True
        assert good["view"] == "fleet"
        assert good["hostname"] == ""
        assert good["message"] == "Waiting for the fleet."
        assert first.login_sends == 2
        assert _until_snap(lambda: first.snapshot()["fleet_seen"] is True)
        seen = first.snapshot()
        assert seen["view"] == "fleet"
        assert seen["counts"]["registered"] == 0
        assert seen["robots"][0]["robot_id"] == _ROBOT_ID
        assert seen["robots"][0]["status"] == "pending"
        assert TOKEN not in json.dumps(seen)
        refresh = _post(
            first, "/api/login", {"username": "lab", "password": OPERATOR_PASSWORD}
        )
        assert refresh["ok"] is True
        assert first.login_sends == 2
        session = json.loads(first.dispatch("GET", "/api/session").body)
        assert session["logged_in"] is True
        assert session["robot_id"] == ""
        assert OPERATOR_PASSWORD not in json.dumps(session)
        assert "ws://" not in json.dumps(session)
        assert first.login_sends == 2
        busy = _post(
            second, "/api/login", {"username": "lab", "password": OPERATOR_PASSWORD}
        )
        assert busy["message"] == "This account is already signed in."
        _post(first, "/api/approve", {"robot_id": _ROBOT_ID})
        _post(first, "/api/attach", {"robot_id": _ROBOT_ID})
        assert _until_snap(lambda: first.snapshot()["view"] == "config"), first.snapshot()
        assert first.snapshot()["hostname"] == _HOSTNAME
        assert first.snapshot()["robot_id"] == _ROBOT_ID
        assert first.snapshot()["status"] != "The robot is not answering. Stopping the arm."
        first.close()
        apps.remove(first)
        logged = False
        reply: dict[str, Any] = {}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            reply = _post(
                second, "/api/login", {"username": "lab", "password": OPERATOR_PASSWORD}
            )
            if reply.get("ok"):
                logged = True
                break
            time.sleep(0.05)
        assert logged, reply
        assert reply["view"] == "fleet"
        assert reply["hostname"] == ""
    finally:
        for app in list(apps):
            app.close()
        server.stop()


def test_empty_login_does_not_open_a_socket() -> None:
    from packaging.operator_app.console import ConsoleApp

    app = ConsoleApp("ws://127.0.0.1:9", web_root=_WEB)
    try:
        reply = _post(app, "/api/login", {"username": "  ", "password": ""})
        assert reply["message"] == message_for("empty")
        assert app.login_sends == 0
        assert app._session.ws is None
    finally:
        app.close()
