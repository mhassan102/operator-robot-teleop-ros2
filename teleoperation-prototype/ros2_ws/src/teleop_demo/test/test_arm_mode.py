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
    REAL_HARDWARE_PLACEHOLDER_LOG,
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


def test_placeholder_log_is_exact() -> None:
    assert REAL_HARDWARE_PLACEHOLDER_LOG == (
        "TELEOP_ARM=real; hardware bridge not started"
    )


def test_start_script_default_is_gazebo() -> None:
    result = _parse_start_script()
    assert result.returncode == 0, result.stderr
    assert "TELEOP_ARM=gazebo" in result.stdout
    assert REAL_HARDWARE_PLACEHOLDER_LOG not in result.stdout


def test_start_script_real_arm_flag() -> None:
    result = _parse_start_script("--real-arm")
    assert result.returncode == 0, result.stderr
    assert "TELEOP_ARM=real" in result.stdout
    assert REAL_HARDWARE_PLACEHOLDER_LOG in result.stdout


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


def test_robot_mlink_compose_passes_arm_without_serial() -> None:
    text = (_prototype_root() / "compose.robot-mlink.yaml").read_text(encoding="utf-8")
    assert "TELEOP_ARM: ${TELEOP_ARM:-gazebo}" in text
    assert "/dev/ttyACM0" not in text
    assert "/teleop/state" in text
    assert "/joint_states" in text
    assert "\ndevices:" not in text


def test_localhost_compose_stays_gazebo() -> None:
    text = (_prototype_root() / "compose.yaml").read_text(encoding="utf-8")
    assert "TELEOP_ARM" not in text
    assert "grep -q /joint_states" in text
    assert "ttyACM0" not in text


def test_launch_file_has_no_serial_and_has_placeholder() -> None:
    launch_text = (
        Path(__file__).resolve().parents[1] / "launch" / "robot_sim.launch.py"
    ).read_text(encoding="utf-8")
    assert "ttyACM0" not in launch_text
    assert "feetech" not in launch_text.lower()
    assert "REAL_HARDWARE_PLACEHOLDER_LOG" in launch_text
    assert "parse_teleop_arm" in launch_text


def test_real_launch_graph_is_receiver_only(monkeypatch) -> None:
    module = _load_launch_module()
    monkeypatch.setenv("TELEOP_ARM", "real")
    monkeypatch.delenv("TELEOP_MLINK", raising=False)
    executables, includes, logs = _graph(module.generate_launch_description())
    assert executables == {"robot_receiver"}
    assert includes == []
    assert any(REAL_HARDWARE_PLACEHOLDER_LOG in log for log in logs)
    assert executables.isdisjoint(GAZEBO_MOTION_EXECUTABLES)


def test_real_launch_graph_with_mlink(monkeypatch) -> None:
    module = _load_launch_module()
    monkeypatch.setenv("TELEOP_ARM", "real")
    monkeypatch.setenv("TELEOP_MLINK", "1")
    executables, includes, _logs = _graph(module.generate_launch_description())
    assert executables == {"robot_receiver", "robot_mlink_bridge"}
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
    assert any("gazebo.launch.py" in item for item in includes)
