"""TELEOP_ARM parsing, start-script CLI, compose wiring, and launch graphs."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from teleop_demo.arm_mode import (
    ARM_GAZEBO,
    ARM_REAL,
    GAZEBO_MOTION_EXECUTABLES,
    InvalidArmMode,
    REAL_ARM_COMPOSE_OVERLAY,
    REAL_ARM_GRIPPER_LOG,
    REAL_ARM_SERIAL_PORT,
    REMOTE_LAPTOP_CAM,
    REMOTE_LAPTOP_CONSOLE,
    drops_cartesian_jog,
    gripper_only_from_env,
    key_direction_allowed,
    parse_teleop_arm,
)


def _prototype_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts" / "start_robot_mlink.sh").is_file() and (
            parent / "compose.robot-mlink.yaml"
        ).is_file():
            return parent
    pytest.skip("teleoperation-prototype compose/scripts are not mounted here")


def _load_launch_module():
    pytest.importorskip("launch")
    pytest.importorskip("launch_ros")
    pytest.importorskip("ament_index_python")
    path = Path(__file__).resolve().parents[1] / "launch" / "robot_sim.launch.py"
    spec = importlib.util.spec_from_file_location("robot_sim_launch", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _walk_launch(entity):
    stack = [entity]
    while stack:
        obj = stack.pop()
        if obj is None:
            continue
        typename = type(obj).__name__
        if typename == "LaunchDescription":
            stack.extend(reversed(list(getattr(obj, "entities", []))))
            continue
        if isinstance(obj, (list, tuple)):
            stack.extend(reversed(list(obj)))
            continue
        if typename == "TimerAction":
            stack.extend(reversed(list(getattr(obj, "actions", []))))
            continue
        if typename == "RegisterEventHandler":
            handler = getattr(obj, "event_handler", None)
            if handler is None:
                handler = getattr(obj, "_RegisterEventHandler__event_handler", None)
            on_exit = None
            if handler is not None:
                on_exit = getattr(handler, "on_exit", None)
                if on_exit is None:
                    on_exit = getattr(
                        handler, "_OnActionEventBase__actions_on_event", None
                    )
            if on_exit is not None and not callable(on_exit):
                stack.extend(reversed(list(on_exit)))
            continue
        yield obj


def _stringify(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (list, tuple)):
        return " ".join(_stringify(item) for item in obj)
    text = getattr(obj, "text", None)
    if isinstance(text, str):
        return text
    location = getattr(obj, "location", None)
    if location is not None:
        return _stringify(location)
    substitutions = getattr(obj, "substitutions", None)
    if substitutions is not None:
        return _stringify(substitutions)
    return str(obj)


def _graph(launch_description):
    executables = set()
    includes = []
    logs = []
    for entity in _walk_launch(launch_description):
        typename = type(entity).__name__
        if typename == "Node":
            executable = getattr(entity, "node_executable", None)
            if executable is None:
                executable = getattr(entity, "executable", "")
            executables.add(executable if isinstance(executable, str) else str(executable))
        elif typename == "IncludeLaunchDescription":
            source = getattr(entity, "launch_description_source", None)
            includes.append(_stringify(source))
        elif typename == "LogInfo":
            logs.append(_stringify(getattr(entity, "msg", "")))
    return executables, includes, logs


def _parse_start_script(*args, extra_env=None) -> subprocess.CompletedProcess:
    root = _prototype_root()
    env = os.environ.copy()
    env.pop("TELEOP_ARM", None)
    env["TELEOP_ARM_PARSE_ONLY"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(root / "scripts" / "start_robot_mlink.sh"), *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_parse_default_and_empty() -> None:
    assert parse_teleop_arm(None) == ARM_GAZEBO
    assert parse_teleop_arm("") == ARM_GAZEBO
    assert parse_teleop_arm("  ") == ARM_GAZEBO


def test_parse_valid_modes() -> None:
    assert parse_teleop_arm("gazebo") == ARM_GAZEBO
    assert parse_teleop_arm("real") == ARM_REAL
    assert parse_teleop_arm(" Gazebo ") == ARM_GAZEBO
    assert parse_teleop_arm("REAL") == ARM_REAL


def test_parse_rejects_unknown_without_fallback() -> None:
    with pytest.raises(InvalidArmMode, match="refusing to fall back to Gazebo"):
        parse_teleop_arm("so101")
    with pytest.raises(InvalidArmMode, match="refusing to fall back to Gazebo"):
        parse_teleop_arm("hardware")
    with pytest.raises(InvalidArmMode, match="refusing to fall back"):
        parse_teleop_arm("sim")


def test_real_arm_log_is_exact() -> None:
    assert REAL_ARM_GRIPPER_LOG == (
        "TELEOP_ARM=real; feetech gripper on /dev/ttyACM0"
    )
    assert REAL_ARM_SERIAL_PORT == "/dev/ttyACM0"


def test_start_script_default_is_gazebo() -> None:
    result = _parse_start_script()
    assert result.returncode == 0, result.stderr
    assert "TELEOP_ARM=gazebo" in result.stdout
    assert REAL_ARM_GRIPPER_LOG not in result.stdout


def test_start_script_real_arm_flag() -> None:
    result = _parse_start_script("--real-arm")
    assert result.returncode == 0, result.stderr
    assert "TELEOP_ARM=real" in result.stdout
    assert REAL_ARM_GRIPPER_LOG in result.stdout


def test_start_script_env_real_without_flag() -> None:
    result = _parse_start_script(extra_env={"TELEOP_ARM": "real"})
    assert result.returncode == 0, result.stderr
    assert "TELEOP_ARM=real" in result.stdout


def test_start_script_real_arm_does_not_fallback_on_conflict() -> None:
    result = _parse_start_script("--real-arm", extra_env={"TELEOP_ARM": "gazebo"})
    assert result.returncode != 0
    assert "refusing to fall back to Gazebo" in result.stderr


def test_start_script_invalid_env_does_not_fallback() -> None:
    result = _parse_start_script(extra_env={"TELEOP_ARM": "so101"})
    assert result.returncode != 0
    assert "refusing to fall back to Gazebo" in result.stderr


def test_start_robot_script_rejects_remote_laptop_flag() -> None:
    result = _parse_start_script("--remote-laptop")
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


def _parse_operator_script(*args, extra_env=None) -> subprocess.CompletedProcess:
    root = _prototype_root()
    env = os.environ.copy()
    env.pop("TELEOP_GRIPPER_ONLY", None)
    env["TELEOP_OPERATOR_PARSE_ONLY"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(root / "scripts" / "start_operator_mlink.sh"), *args],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_start_operator_script_default_is_orin_console() -> None:
    result = _parse_operator_script()
    assert result.returncode == 0, result.stderr
    assert "TELEOP_GRIPPER_ONLY=0" in result.stdout
    assert "operator console: http://127.0.0.1:8090/" in result.stdout
    assert "cam=" not in result.stdout


def test_start_operator_script_remote_laptop() -> None:
    result = _parse_operator_script("--remote-laptop")
    assert result.returncode == 0, result.stderr
    assert "TELEOP_GRIPPER_ONLY=1" in result.stdout
    assert REMOTE_LAPTOP_CONSOLE in result.stdout
    assert REMOTE_LAPTOP_CAM in result.stdout


def test_start_operator_script_unknown_arg() -> None:
    result = _parse_operator_script("--reflect")
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


def test_drops_cartesian_jog_only_in_real_mode() -> None:
    assert drops_cartesian_jog(ARM_REAL) is True
    assert drops_cartesian_jog(ARM_GAZEBO) is False


def test_gripper_only_env_and_key_filter() -> None:
    assert gripper_only_from_env(None) is False
    assert gripper_only_from_env("") is False
    assert gripper_only_from_env("0") is False
    assert gripper_only_from_env("1") is True
    assert gripper_only_from_env("true") is True
    assert key_direction_allowed("+x", False) is True
    assert key_direction_allowed("+x", True) is False
    assert key_direction_allowed("open", True) is True
    assert key_direction_allowed("close", True) is True
    assert key_direction_allowed("stop", True) is True
    assert key_direction_allowed("yaw-", True) is False


def test_operator_mlink_compose_gripper_only_defaults_off() -> None:
    text = (_prototype_root() / "compose.operator-mlink.yaml").read_text(
        encoding="utf-8"
    )
    assert "TELEOP_GRIPPER_ONLY: ${TELEOP_GRIPPER_ONLY:-0}" in text
    assert "TELEOP_MLINK: \"1\"" in text


def test_stop_mlink_uses_real_arm_overlay() -> None:
    text = (_prototype_root() / "scripts" / "stop_mlink.sh").read_text(encoding="utf-8")
    assert REAL_ARM_COMPOSE_OVERLAY in text
    assert "compose.operator-mlink.yaml" in text


def test_robot_mlink_compose_gazebo_has_no_serial() -> None:
    root = _prototype_root()
    text = (root / "compose.robot-mlink.yaml").read_text(encoding="utf-8")
    assert "TELEOP_ARM: ${TELEOP_ARM:-gazebo}" in text
    assert "/dev/ttyACM0" not in text
    assert "/teleop/state" in text
    assert "/joint_states" in text
    assert "\ndevices:" not in text


def test_real_arm_compose_overlay_mounts_serial_only() -> None:
    root = _prototype_root()
    overlay = (root / REAL_ARM_COMPOSE_OVERLAY).read_text(encoding="utf-8")
    assert "/dev/ttyACM0:/dev/ttyACM0" in overlay
    assert "TELEOP_SERIAL_PORT: /dev/ttyACM0" in overlay
    start = (root / "scripts" / "start_robot_mlink.sh").read_text(encoding="utf-8")
    assert REAL_ARM_COMPOSE_OVERLAY in start
    assert "compose.robot-mlink.yaml" in start


def test_localhost_compose_stays_gazebo() -> None:
    text = (_prototype_root() / "compose.yaml").read_text(encoding="utf-8")
    assert "TELEOP_ARM" not in text
    assert "grep -q /joint_states" in text
    assert "ttyACM0" not in text


def test_robot_receiver_drops_cartesian_in_real_mode() -> None:
    text = (
        Path(__file__).resolve().parents[1] / "teleop_demo" / "robot_receiver.py"
    ).read_text(encoding="utf-8")
    assert "drops_cartesian_jog" in text
    assert "zero_twist" in text
    assert "REAL ARM drop cartesian" in text


def test_operator_backend_gripper_only_gates() -> None:
    text = (
        Path(__file__).resolve().parents[1]
        / "teleop_demo"
        / "operator_backend.py"
    ).read_text(encoding="utf-8")
    assert "gripper_only_from_env" in text
    assert "key_direction_allowed" in text
    assert "named poses disabled (gripper-only)" in text


def test_share_files_skips_pycache_dirs() -> None:
    pkg = Path(__file__).resolve().parents[1]
    text = (pkg / "setup.py").read_text(encoding="utf-8")
    assert "os.path.isfile(path)" in text
    assert 'glob(os.path.join(subdir, "*"))' in text
    launch_glob = [str(path) for path in (pkg / "launch").glob("*")]
    files = [path for path in launch_glob if Path(path).is_file()]
    assert files
    assert any(path.endswith("robot_sim.launch.py") for path in files)
    assert not any(Path(path).name == "__pycache__" for path in files)


def test_launch_file_wires_feetech_gripper_for_real() -> None:
    launch_text = (
        Path(__file__).resolve().parents[1] / "launch" / "robot_sim.launch.py"
    ).read_text(encoding="utf-8")
    assert "feetech_gripper" in launch_text
    assert "REAL_ARM_GRIPPER_LOG" in launch_text
    assert "parse_teleop_arm" in launch_text
    assert "gripper_id" in launch_text


def test_real_launch_graph_is_receiver_and_gripper(monkeypatch) -> None:
    module = _load_launch_module()
    monkeypatch.setenv("TELEOP_ARM", "real")
    monkeypatch.delenv("TELEOP_MLINK", raising=False)
    executables, includes, logs = _graph(module.generate_launch_description())
    assert executables == {"robot_receiver", "feetech_gripper"}
    assert includes == []
    assert any(REAL_ARM_GRIPPER_LOG in log for log in logs)
    assert executables.isdisjoint(GAZEBO_MOTION_EXECUTABLES)


def test_real_launch_graph_with_mlink(monkeypatch) -> None:
    module = _load_launch_module()
    monkeypatch.setenv("TELEOP_ARM", "real")
    monkeypatch.setenv("TELEOP_MLINK", "1")
    executables, includes, _logs = _graph(module.generate_launch_description())
    assert executables == {"robot_receiver", "feetech_gripper", "robot_mlink_bridge"}
    assert includes == []
    assert executables.isdisjoint(GAZEBO_MOTION_EXECUTABLES)


def test_invalid_arm_raises_at_launch(monkeypatch) -> None:
    module = _load_launch_module()
    monkeypatch.setenv("TELEOP_ARM", "so101")
    with pytest.raises(RuntimeError, match="refusing to fall back to Gazebo"):
        module.generate_launch_description()


def test_gazebo_launch_graph_matches_today(monkeypatch) -> None:
    module = _load_launch_module()
    pkg = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        module,
        "get_package_share_directory",
        lambda name: str(pkg) if name == "teleop_demo" else (_ for _ in ()).throw(
            KeyError(name)
        ),
    )
    monkeypatch.delenv("TELEOP_ARM", raising=False)
    monkeypatch.delenv("TELEOP_MLINK", raising=False)
    executables, includes, _logs = _graph(module.generate_launch_description())
    expected = {
        "robot_state_publisher",
        "spawn_entity.py",
        "spawner",
        "robot_receiver",
        "move_group",
        "servo_node_main",
        "servo_bridge",
        "named_pose",
    }
    assert expected <= executables
    assert "robot_mlink_bridge" not in executables
    assert "feetech_gripper" not in executables
    assert any("gazebo.launch.py" in item for item in includes)


def test_gazebo_launch_graph_with_mlink(monkeypatch) -> None:
    module = _load_launch_module()
    pkg = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(
        module,
        "get_package_share_directory",
        lambda name: str(pkg) if name == "teleop_demo" else (_ for _ in ()).throw(
            KeyError(name)
        ),
    )
    monkeypatch.setenv("TELEOP_ARM", "gazebo")
    monkeypatch.setenv("TELEOP_MLINK", "1")
    executables, includes, _logs = _graph(module.generate_launch_description())
    assert "robot_receiver" in executables
    assert "robot_mlink_bridge" in executables
    assert "servo_node_main" in executables
    assert "named_pose" in executables
    assert "feetech_gripper" not in executables
    assert any("gazebo.launch.py" in item for item in includes)
