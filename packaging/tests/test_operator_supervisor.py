"""Operator start plan, dry-run, and the window close handler.

The planner is called directly. Dry-run must not spawn and must not load
a console page. Nothing here opens a serial port or starts mlink, Docker,
the camera, or the arm.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from packaging.supervisor.operator_commands import (  # noqa: E402
    camera_page_of,
    decide_operator,
    default_route_ipv4,
    operator_plan,
    operator_stop_plan,
    stop_operator_local,
)
from packaging.supervisor.operator_exec import execute_command  # noqa: E402
from packaging.supervisor.robot_commands import PlanError  # noqa: E402

_CAMERA = "http://100.120.193.52:8889/cam/"
_CONSOLE = f"http://127.0.0.1:8090/?cam={_CAMERA}"
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
_APP: Any = None


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
    assert [item.label for item in plan.commands] == ["mlink-op", "operator"]
    assert plan.commands[0].argv == (
        str(root / "mlink-transport" / "scripts" / "start_daemon.sh"),
        "op",
        "--remote-laptop",
    )
    assert "--ice" not in plan.commands[0].argv
    assert plan.commands[1].argv == (
        str(root / "teleoperation-prototype" / "scripts" / "start_operator_mlink.sh"),
        "--remote-laptop",
    )
    assert plan.commands[1].env == (
        ("REMOTE_LAPTOP_CAM", _CAMERA),
        ("TELEOP_GRIPPER_ONLY", "1"),
    )
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
    assert plan.commands[1].env[0] == ("REMOTE_LAPTOP_CAM", _CAMERA)
    assert plan.commands[1].argv[-1] == "--remote-laptop"
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


def test_stop_order_is_compose_then_mlink_op(tmp_path: Path) -> None:
    plan = operator_stop_plan(tmp_path)
    project = tmp_path / "teleoperation-prototype"
    assert [item.label for item in plan] == ["operator-compose", "mlink-op"]
    assert plan[0].argv[:4] == (
        "docker",
        "compose",
        "--project-directory",
        str(project),
    )
    assert plan[0].argv[4:6] == ("-f", str(project / "compose.operator-mlink.yaml"))
    assert plan[0].argv[-2:] == ("down", "--remove-orphans")
    assert plan[1].argv[1] == "-c"
    assert plan[1].argv[-1].endswith("packaging/run/mlink-op.pid")
    assert "stop_mlink.sh" not in " ".join(plan[0].argv)


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
    assert seen == ["operator-compose", "mlink-op"]


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


def _load_window() -> Any:
    _qapp()
    import packaging.operator_app.window as window_mod

    return window_mod


def _open(window_mod: Any, inventory: dict[str, Any], root: Path) -> Any:
    from packaging.operator_app.login import LoginResult

    window = window_mod.LoginWindow("ws://127.0.0.1:9", root=root)
    window.present(LoginResult("ok", "lab-robot"))
    window.show_inventory(inventory, operator_nic="wlo1")
    return window


def test_close_handler_calls_local_stop(monkeypatch: Any, tmp_path: Path) -> None:
    window_mod = _load_window()
    calls: list[tuple[Path, bool]] = []

    def fake(repo: Path, *, live: bool = False) -> None:
        calls.append((Path(repo), live))

    monkeypatch.setattr(window_mod, "stop_operator_local", fake)
    window = window_mod.LoginWindow("ws://127.0.0.1:9", root=tmp_path)
    try:
        window.show()
        window.close()
        _qapp().processEvents()
    finally:
        _qapp().processEvents()
    assert calls == [(tmp_path, False)]


def test_close_after_a_live_start_passes_live(
    monkeypatch: Any, tmp_path: Path
) -> None:
    window_mod = _load_window()
    calls: list[bool] = []
    monkeypatch.setattr(
        window_mod,
        "stop_operator_local",
        lambda _repo, *, live=False: calls.append(live),
    )
    window = window_mod.LoginWindow("ws://127.0.0.1:9", root=tmp_path)
    window._operator_started = True
    try:
        window.close()
        _qapp().processEvents()
    finally:
        _qapp().processEvents()
    assert calls == [True]


def test_stop_stops_operator_before_the_robot(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    window_mod = _load_window()
    order: list[object] = []

    def fake_send(_self: Any, message: dict[str, Any]) -> bool:
        order.append(message)
        return True

    def fake_stop(_repo: Path, *, live: bool = False) -> None:
        order.append(("local", live))

    monkeypatch.setattr(window_mod.LoginWindow, "_try_send", fake_send)
    monkeypatch.setattr(window_mod, "stop_operator_local", fake_stop)
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        from PyQt5.QtWidgets import QWidget

        window.show()
        cover = QWidget()
        window.stack.addWidget(cover)
        window.stack.setCurrentWidget(cover)
        _qapp().processEvents()
        assert window.stop_button.isVisible()
        assert window.logout_button.isVisible()
        assert window.config_page is not None
        assert not window.config_page.isVisible()
        window._accepted_config = window.config_page.current_config()
        window.stop_session()
        assert order == [("local", False), {"v": 1, "type": "stop"}]
        assert window._stop_timer.isActive()
        assert window._stop_timer.interval() == window_mod.STOP_CONFIRM_MS
        window.present_inbound(
            {"v": 1, "type": "status", "phase": "stopped", "detail": ""}
        )
        assert window.stack.currentWidget() is window.config_page
        assert window.config_page.start_button.isEnabled()
        assert window.stop_button.isVisible()
        assert window._accepted_config is not None
        assert {"v": 1, "type": "logout"} not in order
    finally:
        window.close()
        _qapp().processEvents()


def test_stop_timeout_says_the_robot_did_not_confirm(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    window_mod = _load_window()
    monkeypatch.setattr(window_mod.LoginWindow, "_try_send", lambda *_a, **_k: True)
    monkeypatch.setattr(window_mod, "stop_operator_local", lambda *_a, **_k: None)
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        window.stop_session()
        window._on_stop_timeout()
        assert window.bar_status.text() == "The robot did not confirm stop."
        assert window.stack.currentWidget() is window.config_page
        assert window.config_page is not None
        assert window.config_page.start_button.isEnabled()
    finally:
        window.close()
        _qapp().processEvents()


def test_start_again_uses_the_accepted_config(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    _forbid_spawn(monkeypatch)
    window_mod = _load_window()
    sent: list[dict[str, Any]] = []

    def fake_send(_self: Any, message: dict[str, Any]) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(window_mod.LoginWindow, "_try_send", fake_send)
    monkeypatch.setattr(window_mod, "stop_operator_local", lambda *_a, **_k: None)
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        page = window.config_page
        assert page is not None
        window._accepted_config = page.current_config()
        page.link_turn.setChecked(True)
        window.start_session()
        assert sent == []
        assert page.session_status.text() == "Review the changed config before Start."
        page.link_tailscale.setChecked(True)
        window.start_session()
        assert sent == [{"v": 1, "type": "start"}]
    finally:
        window.close()
        _qapp().processEvents()


def test_logout_returns_to_login_and_keeps_the_robot_id(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    window_mod = _load_window()
    order: list[object] = []

    def fake_send(_self: Any, message: dict[str, Any]) -> bool:
        order.append(dict(message))
        return True

    def fake_stop(_repo: Path, *, live: bool = False) -> None:
        order.append(("local", live))

    monkeypatch.setattr(window_mod.LoginWindow, "_try_send", fake_send)
    monkeypatch.setattr(window_mod, "stop_operator_local", fake_stop)
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        window.show()
        window.id_edit.setText("123456789")
        page = window.config_page
        assert page is not None
        window._accepted_config = page.current_config()
        window.logout_session()
        assert order == [("local", False), {"v": 1, "type": "stop"}]
        window.present_inbound(
            {"v": 1, "type": "status", "phase": "stopped", "detail": ""}
        )
        assert order[-1] == {"v": 1, "type": "logout"}
        window.present_inbound({"v": 1, "type": "logged_out"})
        _qapp().processEvents()
        assert window.stack.currentWidget() is window.form_page
        assert window.logged_in is False
        assert not window.session_bar.isVisible()
        assert not page.start_button.isEnabled()
        assert window.id_edit.text() == "123456789"
        assert window._accepted_config is None
        assert window.status_label.text() == "Logged out."
    finally:
        window.close()
        _qapp().processEvents()


def test_close_stops_both_sides_and_does_not_logout(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    window_mod = _load_window()
    order: list[object] = []

    def fake_send(_self: Any, message: dict[str, Any]) -> bool:
        order.append(dict(message))
        return True

    def fake_stop(_repo: Path, *, live: bool = False) -> None:
        order.append(("local", live))

    monkeypatch.setattr(window_mod.LoginWindow, "_try_send", fake_send)
    monkeypatch.setattr(window_mod, "stop_operator_local", fake_stop)
    window = _open(window_mod, _inventory(), tmp_path)
    window._operator_started = True
    window.close()
    _qapp().processEvents()
    assert order == [("local", True), {"v": 1, "type": "stop"}]


def test_dry_run_start_still_sends(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    _forbid_spawn(monkeypatch)
    window_mod = _load_window()
    sent: list[dict[str, Any]] = []

    def fake_send(_self: Any, message: dict[str, Any]) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(window_mod.LoginWindow, "_try_send", fake_send)
    monkeypatch.setattr(
        window_mod,
        "webengine_installed",
        lambda: (_ for _ in ()).throw(AssertionError("webengine checked")),
    )
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        window.start_session()
        page = window.config_page
        assert page is not None
        assert sent == [{"v": 1, "type": "start"}]
        assert page.session_status.text() == "starting"
        assert window._awaiting_ready is True
        window.present_inbound(
            {"v": 1, "type": "status", "phase": "ready", "detail": "dry-run"}
        )
        assert page.session_status.text() == "dry-run"
        assert window._start_thread is None
        assert window.console_view is None
        assert "PyQt5.QtWebEngineWidgets" not in sys.modules
    finally:
        window.close()
        _qapp().processEvents()


def test_missing_webengine_does_not_send_start(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    window_mod = _load_window()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(window_mod, "webengine_installed", lambda: False)
    monkeypatch.setattr(
        window_mod.LoginWindow,
        "_try_send",
        lambda _self, message: sent.append(message) or True,
    )
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        window.start_session()
        page = window.config_page
        assert page is not None
        assert sent == []
        assert page.session_status.text() == "Qt WebEngine is not installed."
        assert window._awaiting_ready is False
    finally:
        window.close()
        _qapp().processEvents()


def test_dry_run_ready_does_not_load_the_console(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    _forbid_spawn(monkeypatch)
    window_mod = _load_window()

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("operator plan ran during dry-run")

    monkeypatch.setattr(window_mod, "decide_operator", boom)
    monkeypatch.setattr(
        window_mod,
        "webengine_installed",
        lambda: boom(),
    )
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        assert window.console_view is None
        window._awaiting_ready = True
        window.present_inbound(
            {"v": 1, "type": "status", "phase": "ready", "detail": "dry-run"}
        )
        page = window.config_page
        assert page is not None
        assert page.session_status.text() == "dry-run"
        assert window.console_view is None
        assert window._start_thread is None
        assert "PyQt5.QtWebEngineWidgets" not in sys.modules
        window.start_session()
        assert page.session_status.text() == "Cannot reach the registry."
        assert window._awaiting_ready is False
    finally:
        window.close()
        _qapp().processEvents()


def test_error_status_does_not_plan(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    _forbid_spawn(monkeypatch)
    window_mod = _load_window()

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("operator plan ran on error")

    monkeypatch.setattr(window_mod, "decide_operator", boom)
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        window._awaiting_ready = True
        window.present_inbound(
            {"v": 1, "type": "status", "phase": "error", "detail": "no config"}
        )
        page = window.config_page
        assert page is not None
        assert page.session_status.text() == "no config"
        assert window.console_view is None
        assert window._start_thread is None
    finally:
        window.close()
        _qapp().processEvents()


def test_ready_without_start_does_not_plan(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    _forbid_spawn(monkeypatch)
    window_mod = _load_window()

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("operator plan ran before Start")

    monkeypatch.setattr(window_mod, "decide_operator", boom)
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        window.present_inbound(
            {"v": 1, "type": "status", "phase": "ready", "detail": ""}
        )
        assert window._start_thread is None
        assert window.console_view is None
    finally:
        window.close()
        _qapp().processEvents()


def test_empty_camera_page_blocks_start(tmp_path: Path) -> None:
    window_mod = _load_window()
    window = _open(window_mod, _inventory(""), tmp_path)
    try:
        window.start_session()
        page = window.config_page
        assert page is not None
        assert page.session_status.text() == "camera page is not set"
        assert window._awaiting_ready is False
        assert window._start_thread is None
    finally:
        window.close()
        _qapp().processEvents()


def test_ready_uses_stubbed_exec_and_does_not_load_a_server(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    spawned: list[int] = []

    def boom(*_args: Any, **_kwargs: Any) -> None:
        spawned.append(1)
        raise AssertionError("spawned a process")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    window_mod = _load_window()
    monkeypatch.setattr(window_mod, "stop_operator_local", lambda *_a, **_k: None)
    monkeypatch.setattr(window_mod, "default_route_ipv4", lambda *_a, **_k: _BIND)
    loaded: list[str] = []
    monkeypatch.setattr(
        window_mod.LoginWindow,
        "_show_console",
        lambda _self, url: loaded.append(url),
    )
    import packaging.supervisor.operator_exec as operator_exec

    recorded: list[Any] = []
    monkeypatch.setattr(
        operator_exec,
        "execute_command",
        lambda command, _root: recorded.append(command),
    )
    window = _open(window_mod, _inventory(), tmp_path)
    try:
        window._awaiting_ready = True
        window.present_inbound(
            {"v": 1, "type": "status", "phase": "ready", "detail": ""}
        )
        thread = window._start_thread
        assert thread is not None
        thread.join(5)
        assert _pump_until(lambda: len(loaded) == 1)
        assert loaded == [_CONSOLE]
        assert window.console_view is None
        assert "PyQt5.QtWebEngineWidgets" not in sys.modules
        assert [item.label for item in recorded] == ["mlink-op", "operator"]
        assert recorded[0].argv[1:] == ("op", "--remote-laptop")
        assert recorded[1].argv[-1] == "--remote-laptop"
        assert recorded[1].env == (
            ("REMOTE_LAPTOP_CAM", _CAMERA),
            ("TELEOP_GRIPPER_ONLY", "1"),
        )
        assert not (tmp_path / "packaging" / "run" / "ice-op.yaml").exists()
    finally:
        window.close()
        _qapp().processEvents()
    assert spawned == []


def test_prepare_qt_imports_webengine_before_application() -> None:
    """A fresh process can import WebEngine and then create QApplication."""
    root = Path(__file__).resolve().parents[2]
    script = """
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
from packaging.operator_app.cli import prepare_qt
assert QApplication.instance() is None
prepare_qt()
from PyQt5.QtWebEngineWidgets import QWebEngineView
app = QApplication(["teleop-operator-prepare"])
assert QWebEngineView is not None
assert app is not None
"""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
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
