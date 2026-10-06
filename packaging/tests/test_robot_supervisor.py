"""Robot start plan and dry-run status.

The planner is called directly. Dry-run is required to refuse exec.
Nothing here opens a serial port or starts mlink, Docker, the camera,
or the arm.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_TURN = Path(__file__).resolve().parents[2] / "turn"
if str(_TURN) not in sys.path:
    sys.path.insert(0, str(_TURN))

from packaging.robot_app.session import (  # noqa: E402
    RobotState,
    run_robot_session,
)
from packaging.supervisor.robot_commands import (  # noqa: E402
    PlanError,
    decide_start,
    robot_plan,
)
from packaging.supervisor.robot_exec import execute_command  # noqa: E402
from packaging.supervisor.robot_stop import (  # noqa: E402
    decide_stop,
    robot_stop_plan,
)
from signalling.server import listening_uri, start_server  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

_FORBIDDEN = (
    frozenset(range(50000, 50101))
    | frozenset(range(5501, 5505))
    | {8765, 8766, 3479, 8090, 8091}
)
_REPO = Path(__file__).resolve().parents[2]
_DAEMON = _REPO / "mlink-transport" / "scripts" / "start_daemon.sh"
FOLLOWER = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00"
_IFACE2 = (
    "second interface is not used in this version; set Interface 2 to None"
)
_TEMPLATE = (
    "# bind_ip: 192.0.2.9\n"
    "role: controlled\n"
    "bind_ip: 192.0.2.10\n"
    "turn_password: fixture-marker\n"
)


def _inventory(ipv4: str = "10.255.254.58") -> dict[str, Any]:
    return {
        "v": 1,
        "type": "inventory",
        "interfaces": [
            {
                "name": "wlp0s20f3",
                "ipv4": ipv4,
                "up": True,
                "default_route": True,
            },
            {
                "name": "enx00e04c2c4570",
                "ipv4": "10.10.10.1",
                "up": True,
                "default_route": False,
            },
        ],
        "arms": [
            {"path": FOLLOWER, "tty": "/dev/ttyACM0", "label": "follower"}
        ],
        "videos": [
            {"path": "/dev/video2", "name": "USB2.0_CAM1", "kind": "capture"}
        ],
        "camera_page": "",
    }


def _config(
    link: str = "tailscale",
    iface2: str | None = None,
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "config",
        "link": link,
        "iface1": "wlp0s20f3",
        "iface2": iface2,
        "arm": FOLLOWER,
        "video": "/dev/video2",
    }


def _yaml_replaced(text: str) -> None:
    bind = [line for line in text.splitlines() if line.startswith("bind_ip:")]
    if bind != ["bind_ip: 10.255.254.58"]:
        raise AssertionError("bind_ip line was not replaced")
    if "turn_password: fixture-marker" not in text.splitlines():
        raise AssertionError("template body was not copied")
    if any(line.startswith("bind_ip: 100.") for line in text.splitlines()):
        raise AssertionError("tailscale address was written")


def _boom(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("dry-run spawned a process")


def test_tailscale_plan_is_remote_laptop_then_camera_then_arm(tmp_path: Path) -> None:
    plan = robot_plan(_config(), _inventory(), tmp_path, template=_TEMPLATE)
    root = tmp_path
    assert [item.label for item in plan] == ["mlink-edge", "camera", "arm"]
    assert plan[0].argv == (
        str(root / "mlink-transport" / "scripts" / "start_daemon.sh"),
        "edge",
        "--remote-laptop",
    )
    assert "--ice" not in plan[0].argv
    assert plan[1].argv == (str(root / "video" / "so-arm" / "start.sh"),)
    assert plan[1].env == (("DEVICE", "/dev/video2"),)
    assert plan[2].argv == (
        str(root / "teleoperation-prototype" / "scripts" / "start_robot_mlink.sh"),
        "--real-arm",
        "--serial-port",
        FOLLOWER,
    )
    assert not (root / "packaging" / "run" / "ice-edge.yaml").exists()


def test_turn_plan_rewrites_bind_ip_from_the_template(tmp_path: Path) -> None:
    plan = robot_plan(
        _config(link="turn"),
        _inventory("10.255.254.58"),
        tmp_path,
        template=_TEMPLATE,
    )
    dest = tmp_path / "packaging" / "run" / "ice-edge.yaml"
    _yaml_replaced(dest.read_text(encoding="utf-8"))
    assert dest.stat().st_mode & 0o777 == 0o600
    assert plan[0].argv == (
        str(tmp_path / "mlink-transport" / "scripts" / "start_daemon.sh"),
        "edge",
        "--ice",
        "--ice-config",
        str(dest),
    )
    assert "--remote-laptop" not in plan[0].argv
    assert plan[1].env == (("DEVICE", "/dev/video2"),)
    assert plan[2].argv[-2:] == ("--serial-port", FOLLOWER)


def test_turn_refuses_a_tailscale_bind_address(tmp_path: Path) -> None:
    dest = tmp_path / "packaging" / "run" / "ice-edge.yaml"
    try:
        robot_plan(
            _config(link="turn"),
            _inventory("100.120.193.52"),
            tmp_path,
            template=_TEMPLATE,
        )
    except PlanError as exc:
        assert exc.detail == "refusing bind_ip 100.120.193.52"
    else:
        raise AssertionError("100.x bind address was accepted")
    assert not dest.exists()


def test_turn_refuses_a_missing_ipv4(tmp_path: Path) -> None:
    dest = tmp_path / "packaging" / "run" / "ice-edge.yaml"
    try:
        robot_plan(
            _config(link="turn"),
            _inventory(""),
            tmp_path,
            template=_TEMPLATE,
        )
    except PlanError as exc:
        assert exc.detail == "Interface 1 has no IPv4 for bind_ip"
    else:
        raise AssertionError("missing IPv4 was accepted")
    assert not dest.exists()
    try:
        robot_plan(_config(link="turn"), _inventory(), tmp_path)
    except PlanError as exc:
        assert exc.detail == "missing turn/config/local_edge.yaml"
    else:
        raise AssertionError("missing ICE template was accepted")
    assert not dest.exists()


def test_interface_2_is_refused_before_any_yaml_is_written(tmp_path: Path) -> None:
    dest = tmp_path / "packaging" / "run" / "ice-edge.yaml"
    for link in ("tailscale", "turn"):
        try:
            robot_plan(
                _config(link=link, iface2="enx00e04c2c4570"),
                _inventory(),
                tmp_path,
                template=_TEMPLATE,
            )
        except PlanError as exc:
            assert exc.detail == _IFACE2
        else:
            raise AssertionError("Interface 2 was accepted")
        assert not dest.exists()
    decision = decide_start(
        _config(iface2="enx00e04c2c4570"),
        _inventory(),
        tmp_path,
        dry_run=False,
    )
    assert decision.commands is None
    assert decision.status == {
        "v": 1,
        "type": "status",
        "phase": "error",
        "detail": _IFACE2,
    }


def test_dry_run_status_has_no_commands(tmp_path: Path) -> None:
    ready = decide_start(
        _config(),
        _inventory(),
        tmp_path,
        dry_run=True,
    )
    assert ready.commands is None
    assert ready.status == {
        "v": 1,
        "type": "status",
        "phase": "ready",
        "detail": "dry-run",
    }
    refused = decide_start(None, _inventory(), tmp_path, dry_run=False)
    assert refused.commands is None
    assert refused.status == {
        "v": 1,
        "type": "status",
        "phase": "error",
        "detail": "no config",
    }
    live = decide_start(_config(), _inventory(), tmp_path, dry_run=False)
    assert live.status is None
    assert live.commands is not None
    assert live.commands[0].argv[-1] == "--remote-laptop"


def test_stop_order_is_container_then_edge_then_camera(tmp_path: Path) -> None:
    plan = robot_stop_plan(tmp_path)
    assert [item.label for item in plan] == [
        "robot-container",
        "mlink-edge",
        "camera",
    ]
    assert plan[0].argv[0].endswith(
        "teleoperation-prototype/scripts/stop_mlink.sh"
    )
    assert plan[1].argv[1] == "-c"
    assert plan[1].argv[-1].endswith("packaging/run/mlink-edge.pid")
    assert plan[2].argv[0].endswith("video/so-arm/stop.sh")
    dry = decide_stop(tmp_path, dry_run=True)
    assert dry.commands is None
    assert dry.status == {
        "v": 1,
        "type": "status",
        "phase": "stopped",
        "detail": "dry-run",
    }
    live = decide_stop(tmp_path, dry_run=False)
    assert live.commands is not None
    assert [item.label for item in live.commands] == [
        "robot-container",
        "mlink-edge",
        "camera",
    ]


def test_dry_run_exec_raises_before_spawn(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    command = robot_plan(_config(), _inventory(), tmp_path)[0]
    try:
        execute_command(command, tmp_path)
    except RuntimeError as exc:
        assert "must not exec" in str(exc)
    else:
        raise AssertionError("dry-run exec returned")


def test_ice_config_flag_stays_parse_only() -> None:
    env = os.environ.copy()
    env["MLINK_PARSE_ONLY"] = "1"
    env.pop("TELEOP_SUPERVISOR_DRY_RUN", None)
    flagged = subprocess.run(
        [
            str(_DAEMON),
            "edge",
            "--ice",
            "--ice-config",
            "/tmp/teleop-p6-ice-edge.yaml",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert flagged.returncode == 0, flagged.stderr
    assert "config=/tmp/teleop-p6-ice-edge.yaml" in flagged.stdout
    assert "path=ice" in flagged.stdout
    assert "reflect=no" in flagged.stdout
    assert "control=no" in flagged.stdout
    plain = subprocess.run(
        [str(_DAEMON), "edge", "--ice"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert plain.returncode == 0, plain.stderr
    assert "config=config/lab-edge-ice.yaml" in plain.stdout
    assert "path=ice" in plain.stdout
    both = subprocess.run(
        [
            str(_DAEMON),
            "edge",
            "--remote-laptop",
            "--ice-config",
            "/tmp/teleop-p6-ice-edge.yaml",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert both.returncode == 2
    assert "not both" in both.stderr


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


async def _login(ws: Any) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    while True:
        await ws.send(
            json.dumps(
                {
                    "v": 1,
                    "type": "login",
                    "robot_id": "123456789",
                    "password": "AB23CD45",
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


async def _dry_run_start_and_stop(uri: str, tmp_path: Path) -> None:
    state = RobotState("123456789", "AB23CD45", "unit-test")
    lines: list[str] = []
    task = asyncio.create_task(
        run_robot_session(
            uri,
            state,
            _inventory,
            lambda _status: None,
            on_config=lines.append,
            on_line=lines.append,
            root=tmp_path,
        )
    )
    try:
        async with connect(uri) as operator:
            await _login(operator)
            inventory = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert inventory["type"] == "inventory"
            await operator.send(json.dumps(_config(iface2="enx00e04c2c4570")))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2)) == {
                "v": 1,
                "type": "config_ok",
            }
            await operator.send(json.dumps({"v": 1, "type": "start"}))
            refused = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert refused == {
                "v": 1,
                "type": "status",
                "phase": "error",
                "detail": _IFACE2,
            }
            await operator.send(json.dumps(_config()))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2)) == {
                "v": 1,
                "type": "config_ok",
            }
            await operator.send(json.dumps({"v": 1, "type": "start"}))
            ready = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert ready == {
                "v": 1,
                "type": "status",
                "phase": "ready",
                "detail": "dry-run",
            }
            await operator.send(json.dumps({"v": 1, "type": "stop"}))
            stopped = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert stopped == {
                "v": 1,
                "type": "status",
                "phase": "stopped",
                "detail": "dry-run",
            }
        assert any(line == "Status  phase ready  detail dry-run" for line in lines)
        assert not (tmp_path / "packaging" / "run" / "ice-edge.yaml").exists()
        assert not (tmp_path / "packaging" / "run" / "mlink-edge.pid").exists()
        assert "AB23CD45" not in "\n".join(lines)
    finally:
        state.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _stop_keeps_config_until_logout(uri: str, tmp_path: Path) -> None:
    state = RobotState("123456789", "AB23CD45", "unit-test")
    statuses: list[str] = []
    task = asyncio.create_task(
        run_robot_session(
            uri,
            state,
            _inventory,
            statuses.append,
            root=tmp_path,
        )
    )
    try:
        async with connect(uri) as operator:
            await _login(operator)
            assert json.loads(await asyncio.wait_for(operator.recv(), 2))["type"] == (
                "inventory"
            )
            await operator.send(json.dumps(_config()))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2)) == {
                "v": 1,
                "type": "config_ok",
            }
            await operator.send(json.dumps({"v": 1, "type": "start"}))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2))["phase"] == (
                "ready"
            )
            await operator.send(json.dumps({"v": 1, "type": "stop"}))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2))["phase"] == (
                "stopped"
            )
            await operator.send(json.dumps({"v": 1, "type": "start"}))
            again = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert again == {
                "v": 1,
                "type": "status",
                "phase": "ready",
                "detail": "dry-run",
            }
            await operator.send(json.dumps({"v": 1, "type": "logout"}))
            assert json.loads(await asyncio.wait_for(operator.recv(), 2)) == {
                "v": 1,
                "type": "logged_out",
            }
            for _ in range(20):
                if "operator_detached" in statuses:
                    break
                await asyncio.sleep(0.05)
            assert "operator_detached" in statuses
            await operator.send(
                json.dumps(
                    {
                        "v": 1,
                        "type": "login",
                        "robot_id": "123456789",
                        "password": "AB23CD45",
                    }
                )
            )
            assert json.loads(await asyncio.wait_for(operator.recv(), 2))["type"] == (
                "logged_in"
            )
            assert json.loads(await asyncio.wait_for(operator.recv(), 2))["type"] == (
                "inventory"
            )
            await operator.send(json.dumps({"v": 1, "type": "start"}))
            refused = json.loads(await asyncio.wait_for(operator.recv(), 2))
            assert refused == {
                "v": 1,
                "type": "status",
                "phase": "error",
                "detail": "no config",
            }
        assert state.robot_id == "123456789"
        assert state.snapshot()[0] == "AB23CD45"
        assert not (tmp_path / "packaging" / "run" / "mlink-edge.pid").exists()
    finally:
        state.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_stop_keeps_config_until_logout(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)

    async def body() -> None:
        async with _listening() as uri:
            await _stop_keeps_config_until_logout(uri, tmp_path)

    asyncio.run(asyncio.wait_for(body(), 10))


def test_exit_stop_runs_container_then_edge_then_camera(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEOP_SUPERVISOR_DRY_RUN", raising=False)
    labels: list[str] = []

    def fake(command: Any, _root: Path) -> None:
        labels.append(command.label)
        if command.label == "robot-container":
            raise RuntimeError("down failed")

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("stop spawned a process")

    import packaging.supervisor.robot_exec as robot_exec
    from packaging.robot_app.cli import run_robot_stop

    monkeypatch.setattr(robot_exec, "execute_command", fake)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    run_robot_stop(tmp_path)
    assert labels == ["robot-container", "mlink-edge", "camera"]


def test_dry_run_exit_stop_does_not_spawn(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    from packaging.robot_app.cli import run_robot_stop

    run_robot_stop(tmp_path)


def test_sigint_and_sigterm_request_the_same_stop() -> None:
    import signal

    from packaging.robot_app.cli import install_exit_signals

    previous = (
        signal.getsignal(signal.SIGINT),
        signal.getsignal(signal.SIGTERM),
    )
    try:
        state = RobotState("123456789", "AB23CD45", "unit-test")
        handler = install_exit_signals(state)
        assert signal.getsignal(signal.SIGTERM) is handler
        try:
            handler(signal.SIGINT, None)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("SIGINT did not interrupt")
        assert state.stopped()
        other = RobotState("123456789", "AB23CD45", "unit-test")
        handler = install_exit_signals(other)
        handler(signal.SIGTERM, None)
        assert other.stopped()
    finally:
        signal.signal(signal.SIGINT, previous[0])
        signal.signal(signal.SIGTERM, previous[1])


def test_sigint_discards_tty_input_only_during_the_password_prompt(
    monkeypatch: Any,
) -> None:
    import signal

    import packaging.robot_app.cli as robot_cli

    calls: list[str] = []
    monkeypatch.setattr(robot_cli, "restore_tty_echo", lambda: calls.append("restore"))
    previous = (
        signal.getsignal(signal.SIGINT),
        signal.getsignal(signal.SIGTERM),
    )
    robot_cli._prompt_active = False
    try:
        idle = RobotState("123456789", "kept-secret", "unit-test")
        handler = robot_cli.install_exit_signals(idle)
        handler(signal.SIGTERM, None)
        assert calls == []
        assert idle.stopped()
        assert "kept-secret" not in repr(idle)

        prompting = RobotState("123456789", "kept-secret", "unit-test")
        handler = robot_cli.install_exit_signals(prompting)
        robot_cli._prompt_active = True
        try:
            handler(signal.SIGINT, None)
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("SIGINT did not interrupt")
        assert calls == ["restore"]
        assert prompting.stopped()
    finally:
        robot_cli._prompt_active = False
        signal.signal(signal.SIGINT, previous[0])
        signal.signal(signal.SIGTERM, previous[1])


def test_dry_run_session_does_not_spawn(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEOP_SUPERVISOR_DRY_RUN", "1")
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)

    async def body() -> None:
        async with _listening() as uri:
            await _dry_run_start_and_stop(uri, tmp_path)

    asyncio.run(asyncio.wait_for(body(), 10))
