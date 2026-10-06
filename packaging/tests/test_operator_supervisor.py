"""Operator start plan, dry-run, and the browser console session.

The planner is called directly. Dry-run must not spawn. Nothing here
opens a serial port or starts mlink, Docker, the camera, or the arm.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from packaging.supervisor.operator_commands import (  # noqa: E402
    camera_page_of,
    decide_operator,
    default_route_ipv4,
    operator_plan,
    operator_quit_plan,
    operator_stop_plan,
    stop_operator_local,
)
from packaging.operator_app import cli as operator_cli  # noqa: E402
from packaging.operator_app.console import ConsoleApp  # noqa: E402
from packaging.operator_app.helper import Helper  # noqa: E402
from packaging.operator_app.login import LoginResult  # noqa: E402
from packaging.supervisor.operator_exec import execute_command  # noqa: E402
from packaging.supervisor.robot_commands import PlanError  # noqa: E402

_CAMERA = "http://100.120.193.52:8889/cam/"
_CONSOLE = f"http://127.0.0.1:8090/operate?cam={_CAMERA}"
_BIND = "192.168.222.56"
_TEMPLATE = (
    "# bind_ip: 192.0.2.9\n"
    "role: controlling\n"
    "bind_ip: 192.0.2.10\n"
    "turn_password: fixture-marker\n"
)
_ROUTE = """Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT
wlo1 00000000 0123A8C0 0003 0 0 100 00000000 0 0 0
tailscale0 00000000 00000000 0001 0 0 50 00000000 0 0 0
"""

_WEB = (
    Path(__file__).resolve().parents[2] / "teleoperation-prototype" / "web"
)
_IFACE2 = (
    "second interface is not used in this version; set Interface 2 to None"
)


def _inventory(camera_page: str = _CAMERA) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "inventory",
        "interfaces": [
            {
                "name": "wlp0s20f3",
                "ipv4": "10.255.254.58",
                "up": True,
                "default_route": True,
            }
        ],
        "arms": [],
        "videos": [
            {"path": "/dev/video2", "name": "USB2.0_CAM1", "kind": "capture"}
        ],
        "camera_page": camera_page,
    }


def _config(link: str = "tailscale") -> dict[str, Any]:
    return {
        "v": 1,
        "type": "config",
        "link": link,
        "iface1": "wlp0s20f3",
        "iface2": None,
        "arm": "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00",
        "video": "/dev/video2",
    }


def _yaml_replaced(text: str) -> None:
    bind = [line for line in text.splitlines() if line.startswith("bind_ip:")]
    if bind != [f"bind_ip: {_BIND}"]:
        raise AssertionError("bind_ip line was not replaced")
    if "turn_password: fixture-marker" not in text.splitlines():
        raise AssertionError("template body was not copied")
    if any(line.startswith("bind_ip: 100.") for line in text.splitlines()):
        raise AssertionError("tailscale address was written")


def _boom(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("spawned a process")


def _forbid_spawn(monkeypatch: Any) -> None:
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)


def test_tailscale_plan_is_remote_laptop_then_gripper_only(tmp_path: Path) -> None:
    plan = operator_plan(_config(), _inventory(), tmp_path, "ready")
    root = tmp_path
    assert [item.label for item in plan.commands] == ["mlink-op"]
    joined = " ".join(plan.commands[0].argv)
    assert "start_operator_mlink.sh" not in joined
    assert plan.commands[0].argv == (
        str(root / "mlink-transport" / "scripts" / "start_daemon.sh"),
        "op",
        "--remote-laptop",
    )
    assert "--ice" not in plan.commands[0].argv
    assert plan.console_url == _CONSOLE
    assert not (root / "packaging" / "run" / "ice-op.yaml").exists()


def test_turn_plan_rewrites_bind_ip_from_the_template(tmp_path: Path) -> None:
    plan = operator_plan(
        _config(link="turn"),
        _inventory(),
        tmp_path,
        "ready",
        bind_ip=_BIND,
        template=_TEMPLATE,
    )
    dest = tmp_path / "packaging" / "run" / "ice-op.yaml"
    _yaml_replaced(dest.read_text(encoding="utf-8"))
    assert dest.stat().st_mode & 0o777 == 0o600
    assert plan.commands[0].argv == (
        str(tmp_path / "mlink-transport" / "scripts" / "start_daemon.sh"),
        "op",
        "--ice",
        "--ice-config",
        str(dest),
    )
    assert "--remote-laptop" not in plan.commands[0].argv
    assert len(plan.commands) == 1
    assert "start_operator_mlink.sh" not in " ".join(plan.commands[0].argv)
    assert plan.console_url == _CONSOLE


def test_turn_refuses_a_tailscale_bind_address(tmp_path: Path) -> None:
    dest = tmp_path / "packaging" / "run" / "ice-op.yaml"
    try:
        operator_plan(
            _config(link="turn"),
            _inventory(),
            tmp_path,
            "ready",
            bind_ip="100.120.193.52",
            template=_TEMPLATE,
        )
    except PlanError as exc:
        assert exc.detail == "refusing bind_ip 100.120.193.52"
    else:
        raise AssertionError("100.x bind address was accepted")
    assert not dest.exists()


def test_turn_refuses_a_missing_ipv4(tmp_path: Path) -> None:
    dest = tmp_path / "packaging" / "run" / "ice-op.yaml"
    try:
        operator_plan(
            _config(link="turn"),
            _inventory(),
            tmp_path,
            "ready",
            bind_ip="",
            template=_TEMPLATE,
        )
    except PlanError as exc:
        assert exc.detail == "this PC has no IPv4 for bind_ip"
    else:
        raise AssertionError("missing IPv4 was accepted")
    assert not dest.exists()
    try:
        operator_plan(
            _config(link="turn"),
            _inventory(),
            tmp_path,
            "ready",
            bind_ip=_BIND,
        )
    except PlanError as exc:
        assert exc.detail == "missing turn/config/local_op.yaml"
    else:
        raise AssertionError("missing ICE template was accepted")
    assert not dest.exists()


def test_default_route_ipv4_uses_the_injected_lookup() -> None:
    found = default_route_ipv4(
        _ROUTE,
        ipv4_of=lambda name: _BIND if name == "wlo1" else "",
    )
    assert found == _BIND


def test_error_phase_has_no_operator_command(tmp_path: Path) -> None:
    dest = tmp_path / "packaging" / "run" / "ice-op.yaml"
    for phase in ("error", "starting", "stopped"):
        decision = decide_operator(
            _config(link="turn"),
            _inventory(),
            tmp_path,
            phase,
            dry_run=False,
            bind_ip=_BIND,
            template=_TEMPLATE,
        )
        assert decision.commands is None
        assert decision.console_url == ""
        try:
            operator_plan(
                _config(link="turn"),
                _inventory(),
                tmp_path,
                phase,
                bind_ip=_BIND,
                template=_TEMPLATE,
            )
        except PlanError as exc:
            assert exc.detail == "robot is not ready"
        else:
            raise AssertionError(f"{phase} built a command")
        assert not dest.exists()


def test_camera_page_is_required(tmp_path: Path) -> None:
    dest = tmp_path / "packaging" / "run" / "ice-op.yaml"
    for page in ("", "   ", None):
        inventory = _inventory("" if page is None else page)
        if page is None:
            inventory["camera_page"] = None
        try:
            operator_plan(
                _config(link="turn"),
                inventory,
                tmp_path,
                "ready",
                bind_ip=_BIND,
                template=_TEMPLATE,
            )
        except PlanError as exc:
            assert exc.detail == "camera page is not set"
        else:
            raise AssertionError("empty camera page was accepted")
        assert not dest.exists()
        decision = decide_operator(
            _config(),
            inventory,
            tmp_path,
            "ready",
            dry_run=False,
        )
        assert decision.commands is None
        assert decision.console_url == ""
        assert decision.detail == "camera page is not set"
    assert camera_page_of(_inventory()) == _CAMERA


def test_dry_run_returns_no_commands_and_writes_no_yaml(tmp_path: Path) -> None:
    decision = decide_operator(
        _config(link="turn"),
        _inventory(),
        tmp_path,
        "ready",
        dry_run=True,
        bind_ip=_BIND,
        template=_TEMPLATE,
    )
    assert decision.commands is None
    assert decision.console_url == ""
    assert not (tmp_path / "packaging" / "run" / "ice-op.yaml").exists()


def test_stop_leaves_the_app_and_quit_does_not_compose(tmp_path: Path) -> None:
    stopped = operator_stop_plan(tmp_path)
    assert [item.label for item in stopped] == ["mlink-op"]
    assert "docker" not in stopped[0].argv
    assert stopped[0].argv[-1].endswith("packaging/run/mlink-op.pid")
    quit_plan = operator_quit_plan(tmp_path)
    assert quit_plan == []
    assert "docker" not in " ".join(
        part for command in quit_plan for part in command.argv
    )


def test_dry_run_exec_raises_before_spawn(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    _forbid_spawn(monkeypatch)
    command = operator_plan(_config(), _inventory(), tmp_path, "ready").commands[0]
    try:
        execute_command(command, tmp_path)
    except RuntimeError as exc:
        assert "must not exec" in str(exc)
    else:
        raise AssertionError("dry-run exec returned")


def test_local_stop_does_not_spawn_unless_live(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _forbid_spawn(monkeypatch)
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    stop_operator_local(tmp_path, live=False)
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    stop_operator_local(tmp_path, live=True)

    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    import packaging.supervisor.operator_exec as operator_exec

    seen: list[str] = []
    monkeypatch.setattr(
        operator_exec,
        "execute_command",
        lambda command, _root: seen.append(command.label),
    )
    stop_operator_local(tmp_path, live=True)
    assert seen == ["mlink-op"]

class _Script:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[Any] | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue = asyncio.Queue()

    async def login(self, url: str, robot_id: str, password: str) -> LoginResult:
        assert password
        self.ws = object()
        return LoginResult("ok", "lab-robot")

    async def send_json(self, message: dict[str, Any]) -> None:
        self.sent.append(json.loads(json.dumps(message)))

    async def next_message(self) -> dict[str, Any] | None:
        assert self._queue is not None
        return await self._queue.get()

    def push(self, message: dict[str, Any] | None) -> None:
        assert self._loop is not None and self._queue is not None
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


def _post(app: ConsoleApp, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = b"{}" if body is None else json.dumps(body).encode()
    result = app.dispatch("POST", path, raw)
    assert result is not None and result.status == 200
    payload = json.loads(result.body)
    assert isinstance(payload, dict)
    return payload


def _load_gate() -> tuple[Any, Any]:
    demo = (
        Path(__file__).resolve().parents[2]
        / "teleoperation-prototype"
        / "ros2_ws"
        / "src"
        / "teleop_demo"
    )
    if str(demo) not in sys.path:
        sys.path.insert(0, str(demo))
    from teleop_demo.link_gate import LinkGate, startup_link

    return LinkGate, startup_link


def _session(tmp_path: Path) -> tuple[ConsoleApp, _Script, list[tuple[Any, ...]]]:
    script = _Script()
    calls: list[tuple[Any, ...]] = []

    def helper(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert "password" not in json.dumps(payload)
        calls.append(("helper", path))
        if path == "/mlink/start":
            return {"ok": True, "console_url": _CONSOLE}
        return {"ok": True}

    app = ConsoleApp(
        "ws://127.0.0.1:9",
        web_root=_WEB,
        operator_nic="wlo1",
        session=script,
        helper_post=helper,
        on_enable_link=lambda: calls.append(("enable",)),
        on_disable_link=lambda: calls.append(("disable",)),
    )
    return app, script, calls


def _accept(app: ConsoleApp, script: _Script, inventory: dict[str, Any] | None = None) -> None:
    reply = _post(
        app, "/api/login", {"robot_id": "123456789", "password": "AB23CD45"}
    )
    assert reply["ok"] is True
    script.push(inventory if inventory is not None else _inventory())
    assert _until(lambda: app.snapshot()["view"] == "config")
    _post(app, "/api/review", _config())
    script.push({"v": 1, "type": "config_ok"})
    assert _until(lambda: app.snapshot()["review_status"] == "config_ok")


def test_link_gate_ui_only_skips_the_socket_until_start() -> None:
    link_gate, startup_link = _load_gate()
    assert startup_link({"TELEOP_UI_ONLY": "1", "TELEOP_MLINK": "1"}) == (False, False)
    assert startup_link({"TELEOP_MLINK": "1"}) == (True, True)
    assert startup_link({}) == (False, True)
    gate = link_gate()
    calls: list[str] = []
    gate.apply_startup(
        {"TELEOP_UI_ONLY": "1", "TELEOP_MLINK": "1"},
        lambda: calls.append("open") or "sock",
        lambda: calls.append("arm") or "hb",
        lambda: calls.append("poll") or "poll",
    )
    assert calls == []
    gate.enable(
        lambda: calls.append("open") or "sock",
        lambda: calls.append("arm") or "hb",
        lambda: calls.append("poll") or "poll",
    )
    assert calls == ["open", "poll", "arm"]
    order: list[tuple[str, str]] = []
    gate.disable(
        lambda sock: order.append(("close", sock)),
        lambda timer: order.append(("cancel", timer)),
    )
    assert order == [("cancel", "hb"), ("cancel", "poll"), ("close", "sock")]
    plain = link_gate()
    plain_calls: list[str] = []
    plain.apply_startup(
        {"TELEOP_MLINK": "true"},
        lambda: plain_calls.append("open") or "sock",
        lambda: plain_calls.append("arm") or "hb",
        lambda: plain_calls.append("poll") or "poll",
    )
    assert plain_calls == ["open", "poll", "arm"]
    heartbeat_only = link_gate()
    heartbeat_calls: list[str] = []
    heartbeat_only.apply_startup(
        {},
        lambda: heartbeat_calls.append("open") or "sock",
        lambda: heartbeat_calls.append("arm") or "hb",
        lambda: heartbeat_calls.append("poll") or "poll",
    )
    assert heartbeat_calls == ["arm"]


def test_backend_constructor_uses_the_link_gate() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "teleoperation-prototype"
        / "ros2_ws"
        / "src"
        / "teleop_demo"
        / "teleop_demo"
        / "operator_backend.py"
    )
    text = path.read_text(encoding="utf-8")
    init = text.split("def __init__", 1)[1].split("def _open_mlink_socket", 1)[0]
    assert "apply_startup" in init
    assert "open_from_env" not in init
    assert "heartbeat_rate" not in init
    assert "_hold_heartbeat" in text


def test_quit_plan_has_no_docker_compose(tmp_path: Path) -> None:
    text = " ".join(part for command in operator_quit_plan(tmp_path) for part in command.argv)
    assert "docker" not in text
    assert "compose" not in text
    assert "start_daemon.sh" not in text
    assert "start_operator_mlink.sh" not in text


def test_helper_start_stop_and_quit(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    _forbid_spawn(monkeypatch)
    recorded: list[Any] = []
    helper = Helper(tmp_path, executor=lambda command, _root: recorded.append(command))
    status, body = helper.handle(
        "POST",
        "/mlink/start",
        json.dumps(
            {"config": _config(), "inventory": _inventory(), "phase": "ready"}
        ).encode(),
    )
    assert status == 200
    assert body["ok"] is True
    assert body["console_url"] == _CONSOLE
    assert [item.label for item in recorded] == ["mlink-op"]
    assert "start_operator_mlink.sh" not in " ".join(recorded[0].argv)
    recorded.clear()
    status, body = helper.handle("POST", "/mlink/stop", b"{}")
    assert body["ok"] is True
    assert [item.label for item in recorded] == ["mlink-op"]
    assert "docker" not in recorded[0].argv
    recorded.clear()
    status, body = helper.handle("POST", "/quit", b"{}")
    assert body["ok"] is True
    helper.wait_for_quit()
    assert recorded == []


def test_helper_dry_run_does_not_exec(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    _forbid_spawn(monkeypatch)
    recorded: list[Any] = []
    helper = Helper(tmp_path, executor=lambda command, _root: recorded.append(command))
    _status, body = helper.handle(
        "POST",
        "/mlink/start",
        json.dumps(
            {
                "config": _config(link="turn"),
                "inventory": _inventory(),
                "phase": "ready",
            }
        ).encode(),
    )
    assert body == {"ok": False, "detail": "dry-run"}
    assert recorded == []
    assert not (tmp_path / "packaging" / "run" / "ice-op.yaml").exists()
    _status, stopped = helper.handle("POST", "/mlink/stop", b"{}")
    assert stopped["dry_run"] is True
    assert recorded == []
    helper.handle("POST", "/quit", b"{}")
    helper.wait_for_quit()
    assert recorded == []


def test_launch_dry_run_does_not_bind(monkeypatch: Any) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    _forbid_spawn(monkeypatch)

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("operator bound a port")

    import packaging.operator_app.host as host_mod

    monkeypatch.setattr(host_mod.OperatorHost, "serve", boom)
    assert operator_cli.launch("ws://127.0.0.1:9") == 0


def test_launch_opens_the_window_without_docker_or_mlink(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    _forbid_spawn(monkeypatch)
    seen: list[str] = []

    class _FakeHost:
        def serve(self, host: str, port: int) -> int:
            assert host == "127.0.0.1"
            assert port == 8090
            seen.append("serve")
            return 8090

        def wait_for_quit(self, timeout: float | None = None) -> bool:
            seen.append("wait")
            return True

        def shutdown(self) -> None:
            seen.append("down")

        @property
        def heartbeat_armed(self) -> bool:
            return False

        @property
        def socket_open(self) -> bool:
            return False

    opened: list[str] = []
    assert (
        operator_cli.launch(
            "ws://127.0.0.1:9",
            root=tmp_path,
            service=_FakeHost(),
            browser=opened.append,
            wait_ready=lambda: None,
        )
        == 0
    )
    assert opened == ["http://127.0.0.1:8090/"]
    assert seen == ["serve", "wait", "down"]


def test_closing_the_desktop_window_stops_the_process(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    _forbid_spawn(monkeypatch)
    calls: list[tuple[str, str]] = []

    class _FakeHost:
        def serve(self, host: str, port: int) -> int:
            assert host == "127.0.0.1"
            return 8090

        def quit_event(self) -> threading.Event:
            return threading.Event()

        def shutdown(self) -> None:
            calls.append(("down", ""))

    def _window(url: str, until: object = None) -> None:
        assert until is not None
        calls.append(("window", url))

    def _shut(service: object, *, console: str, **_kwargs: object) -> None:
        calls.append(("shut", console))

    monkeypatch.setattr(operator_cli, "open_desktop", _window)
    monkeypatch.setattr(operator_cli, "shutdown_after_close", _shut)
    assert (
        operator_cli.launch(
            "ws://127.0.0.1:9",
            root=tmp_path,
            service=_FakeHost(),
            wait_ready=lambda: None,
        )
        == 0
    )
    assert calls == [
        ("window", "http://127.0.0.1:8090/"),
        ("shut", "http://127.0.0.1:8090/"),
        ("down", ""),
    ]


def test_a_failed_window_still_stops_the_process(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    _forbid_spawn(monkeypatch)
    calls: list[str] = []

    class _FakeHost:
        def serve(self, host: str, port: int) -> int:
            return 8090

        def quit_event(self) -> threading.Event:
            return threading.Event()

        def shutdown(self) -> None:
            calls.append("down")

    def _window(url: str, until: object = None) -> None:
        raise RuntimeError("no window")

    def _shut(service: object, *, console: str, **_kwargs: object) -> None:
        calls.append("shut")

    monkeypatch.setattr(operator_cli, "open_desktop", _window)
    monkeypatch.setattr(operator_cli, "shutdown_after_close", _shut)
    assert (
        operator_cli.launch(
            "ws://127.0.0.1:9",
            root=tmp_path,
            service=_FakeHost(),
            wait_ready=lambda: None,
        )
        == 1
    )
    assert calls == ["shut", "down"]


def test_window_close_asks_the_console_before_the_process_exits(tmp_path: Path) -> None:
    console, console_seen = _ephemeral_server()
    try:
        console_url = f"http://127.0.0.1:{console.server_address[1]}/"
        assert operator_cli.request_shutdown(console_url) is True
        assert console_seen == ["/api/quit"]

        class _AlreadyQuit:
            def quit_requested(self) -> bool:
                return True

            def wait_for_quit(self, timeout: float | None = None) -> bool:
                return True

            def request_quit(self) -> None:
                raise AssertionError("quit ran twice")

        operator_cli.shutdown_after_close(
            _AlreadyQuit(), console=console_url, wait_s=0.2
        )
        assert console_seen == ["/api/quit"]

        class _QuitsFromConsole:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def quit_requested(self) -> bool:
                return False

            def wait_for_quit(self, timeout: float | None = None) -> bool:
                return True

            def request_quit(self) -> None:
                self.calls.append("quit")

        waiting = _QuitsFromConsole()
        operator_cli.shutdown_after_close(waiting, console=console_url, wait_s=0.2)
        assert console_seen == ["/api/quit", "/api/quit"]
        assert waiting.calls == []
    finally:
        console.shutdown()
        console.server_close()


def test_window_close_stops_the_process_when_the_console_is_down() -> None:
    closed = _closed_port()

    class _NeedsQuit:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._quit = threading.Event()

        def quit_requested(self) -> bool:
            return self._quit.is_set()

        def wait_for_quit(self, timeout: float | None = None) -> bool:
            return self._quit.wait(timeout)

        def request_quit(self) -> None:
            self.calls.append("quit")
            self._quit.set()

    waiting = _NeedsQuit()
    operator_cli.shutdown_after_close(
        waiting, console=f"http://127.0.0.1:{closed}/", wait_s=0.2
    )
    assert waiting.calls == ["quit"]


def test_desktop_window_prefers_qt_without_opening_one(monkeypatch: Any) -> None:
    from packaging.operator_app import desktop

    monkeypatch.setattr(desktop, "_qt_available", lambda: True)
    assert desktop.backend_name() == "qt"
    monkeypatch.setattr(desktop, "_qt_available", lambda: False)
    monkeypatch.setattr(desktop, "_chrome_bin", lambda: "/usr/bin/google-chrome")
    assert desktop.backend_name() == "chrome"
    argv = desktop.chrome_command(
        "/usr/bin/google-chrome",
        "http://127.0.0.1:8090/",
        "/tmp/teleop-profile",
    )
    assert argv[0] == "/usr/bin/google-chrome"
    assert "--app=http://127.0.0.1:8090/" in argv
    assert "--user-data-dir=/tmp/teleop-profile" in argv
    assert "xdg-open" not in " ".join(argv)
    monkeypatch.setattr(desktop, "_chrome_bin", lambda: None)
    assert desktop.backend_name() == ""
    try:
        desktop.show_console("http://127.0.0.1:9/")
    except RuntimeError as exc:
        assert "window" in str(exc)
    else:
        raise AssertionError("show_console returned without a window")


def _ephemeral_server() -> tuple[ThreadingHTTPServer, list[str]]:
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            if length:
                self.rfile.read(length)
            seen.append(self.path.split("?", 1)[0])
            raw = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = int(httpd.server_address[1])
    assert port not in {8090, 8091, 8765, 5501, 5502, 5503, 5504}
    threading.Thread(target=httpd.serve_forever, name="quit-test", daemon=True).start()
    return httpd, seen


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    assert port not in {8090, 8091, 8765, 5501, 5502, 5503, 5504}
    return port


def test_cli_does_not_import_pyqt() -> None:
    root = Path(__file__).resolve().parents[2]
    script = """
import sys
import packaging.operator_app.cli as cli
assert "PyQt5" not in sys.modules
assert "packaging.operator_app.desktop" not in sys.modules
assert not hasattr(cli, "prepare_qt")
text = open("packaging/operator_app/cli.py", encoding="utf-8").read()
assert "PyQt5" not in text
assert "QtWebEngine" not in text
assert "prepare_qt" not in text
assert "xdg-open" not in text
assert "def open_desktop" in text
"""
    env = os.environ.copy()
    env.pop("QT_QPA_PLATFORM", None)
    env["PYTHONPATH"] = str(root)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    build = (root / "packaging" / "debian" / "build.sh").read_text(encoding="utf-8")
    assert "python3-pyqt5" not in build
    assert "operator Depends still lists a Qt package" in build
    desktop = (root / "packaging" / "debian" / "teleop-operator.desktop").read_text(
        encoding="utf-8"
    )
    assert "console" in desktop
    wrapper = (root / "packaging" / "debian" / "teleop-operator.wrapper").read_text(
        encoding="utf-8"
    )
    assert "PyQt" not in wrapper


def test_pypi_packaging_version_stays_importable(tmp_path: Path) -> None:
    """ROS imports packaging.version while this tree is first on the path."""
    site = tmp_path / "site"
    real = site / "packaging"
    real.mkdir(parents=True)
    (real / "__init__.py").write_text('MARKER = "pypi"\n', encoding="utf-8")
    (real / "_structures.py").write_text('Infinity = "inf"\n', encoding="utf-8")
    (real / "version.py").write_text(
        "from packaging._structures import Infinity\n"
        'Version = "pypi-marker-" + Infinity\n',
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    script = """
import sys
repo, fake = sys.argv[1], sys.argv[2]
sys.path[:0] = [repo, fake]
import packaging.version as version
assert version.Version == "pypi-marker-inf", version.Version
from packaging.operator_app.cli import CONSOLE_URL
assert CONSOLE_URL == "http://127.0.0.1:8090/"
"""
    proc = subprocess.run(
        [sys.executable, "-S", "-c", script, str(root), str(site)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_start_runs_mlink_then_enables_the_link(tmp_path: Path) -> None:
    app, script, calls = _session(tmp_path)
    try:
        blocked = app.dispatch("GET", "/operate")
        assert blocked is not None and blocked.status == 403
        _accept(app, script)
        started = _post(app, "/api/start", _config())
        assert started["message"] == "starting"
        assert [item.get("type") for item in script.sent[-2:]] == ["config", "start"]
        script.push({"v": 1, "type": "status", "phase": "ready", "detail": ""})
        assert _until(lambda: app.snapshot()["view"] == "operate")
        assert calls == [("helper", "/mlink/start"), ("enable",)]
        assert app.snapshot()["operate_url"] == f"/operate?cam={_CAMERA}"
        page = app.dispatch("GET", "/operate")
        assert page is not None and page.status == 200
        assert b"operate.js" in page.body
        hidden = app.dispatch("GET", "/index.html")
        assert hidden is not None and hidden.status == 404
    finally:
        app.close()


def test_stop_leaves_the_container_and_logout_returns_to_login(tmp_path: Path) -> None:
    app, script, calls = _session(tmp_path)
    try:
        _accept(app, script)
        _post(app, "/api/start", _config())
        script.push({"v": 1, "type": "status", "phase": "ready", "detail": ""})
        assert _until(lambda: app.snapshot()["view"] == "operate")
        _post(app, "/api/stop")
        assert calls[-2:] == [("disable",), ("helper", "/mlink/stop")]
        script.push({"v": 1, "type": "status", "phase": "stopped", "detail": ""})
        assert _until(lambda: app.snapshot()["view"] == "config")
        snap = app.snapshot()
        assert snap["start_enabled"] is True
        assert snap["logged_in"] is True
        assert ("helper", "/quit") not in calls
        before = len(script.sent)
        _post(app, "/api/start", _config())
        assert [item.get("type") for item in script.sent[before : before + 2]] == [
            "config",
            "start",
        ]
        _post(app, "/api/logout")
        script.push({"v": 1, "type": "status", "phase": "stopped", "detail": ""})
        script.push({"v": 1, "type": "logged_out"})
        assert _until(lambda: app.snapshot()["view"] == "login")
        done = app.snapshot()
        assert done["logged_in"] is False
        assert done["robot_id"] == "123456789"
        assert done["status"] == "Logged out."
        assert any(item.get("type") == "logout" for item in script.sent)
        assert ("helper", "/quit") not in calls
    finally:
        app.close()


def test_quit_exits_after_mlink_stop(tmp_path: Path) -> None:
    app, script, calls = _session(tmp_path)
    try:
        _accept(app, script)
        _post(app, "/api/quit")
        script.push({"v": 1, "type": "status", "phase": "stopped", "detail": ""})
        assert _until(lambda: ("helper", "/quit") in calls)
        assert app.snapshot()["view"] == "quit"
        assert any(item.get("type") == "stop" for item in script.sent)
        assert not any(item.get("type") == "logout" for item in script.sent)
    finally:
        app.close()


def test_dry_run_start_does_not_enable_the_link(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    _forbid_spawn(monkeypatch)
    app, script, calls = _session(tmp_path)
    try:
        _accept(app, script)
        before = len(script.sent)
        _post(app, "/api/start", _config())
        assert [item.get("type") for item in script.sent[before:]] == ["config", "start"]
        script.push({"v": 1, "type": "status", "phase": "ready", "detail": "dry-run"})
        assert _until(lambda: app.snapshot()["status"] == "dry-run")
        assert app.snapshot()["view"] == "config"
        assert calls == []
        assert app.dispatch("GET", "/operate").status == 403
    finally:
        app.close()


def test_start_refuses_a_changed_config_iface2_and_empty_camera(tmp_path: Path) -> None:
    app, script, calls = _session(tmp_path)
    try:
        _accept(app, script)
        turned = dict(_config())
        turned["link"] = "turn"
        refused = _post(app, "/api/start", turned)
        assert refused["message"] == "Review the changed config before Start."
        assert not any(item.get("type") == "start" for item in script.sent)
        chosen = dict(_config())
        chosen["iface2"] = "enx00e04c2c4570"
        _post(app, "/api/review", chosen)
        script.push({"v": 1, "type": "config_ok"})
        assert _until(lambda: app.snapshot()["review_status"] == "config_ok")
        blocked = _post(app, "/api/start", chosen)
        assert blocked["message"] == _IFACE2
        assert not any(item.get("type") == "start" for item in script.sent)
    finally:
        app.close()
    empty, empty_script, empty_calls = _session(tmp_path)
    try:
        inventory = _inventory("")
        _accept(empty, empty_script, inventory)
        blocked = _post(empty, "/api/start", _config())
        assert blocked["message"] == "camera page is not set"
        assert not any(item.get("type") == "start" for item in empty_script.sent)
        assert empty_calls == []
    finally:
        empty.close()


def test_ready_without_start_and_error_do_not_plan(tmp_path: Path) -> None:
    app, script, calls = _session(tmp_path)
    try:
        _accept(app, script)
        script.push({"v": 1, "type": "status", "phase": "ready", "detail": ""})
        time.sleep(0.2)
        assert app.snapshot()["view"] == "config"
        assert calls == []
        _post(app, "/api/start", _config())
        script.push({"v": 1, "type": "status", "phase": "error", "detail": "no config"})
        assert _until(lambda: app.snapshot()["status"] == "no config")
        assert app.snapshot()["view"] == "config"
        assert calls == []
    finally:
        app.close()


def test_closing_the_console_does_not_stop_the_link(tmp_path: Path) -> None:
    app, script, calls = _session(tmp_path)
    _accept(app, script)
    app.close()
    assert calls == []
    assert not any(item.get("type") == "stop" for item in script.sent)
