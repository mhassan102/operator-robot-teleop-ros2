"""Robot-side arm target: Gazebo sim vs real hardware bring-up.

TELEOP_ARM=gazebo|real (default gazebo). Unknown values must not fall back
to Gazebo. Real mode starts robot_receiver plus the Feetech gripper node.
TELEOP_GRIPPER_ONLY on the operator ignores Cartesian keys / named poses.
"""

from __future__ import annotations

import os

ARM_GAZEBO = "gazebo"
ARM_REAL = "real"
VALID_ARM_MODES = (ARM_GAZEBO, ARM_REAL)
DEFAULT_ARM_MODE = ARM_GAZEBO
REAL_ARM_SERIAL_PORT = "/dev/ttyACM0"
REAL_ARM_COMPOSE_OVERLAY = "compose.robot-mlink.real-arm.yaml"
REAL_ARM_GRIPPER_LOG = "TELEOP_ARM=real; feetech gripper on /dev/ttyACM0"
GRIPPER_ONLY_DIRECTIONS = frozenset({"open", "close", "stop"})
REMOTE_LAPTOP_CAM = "http://100.120.193.52:8889/cam"
REMOTE_LAPTOP_CONSOLE = f"http://127.0.0.1:8090/?cam={REMOTE_LAPTOP_CAM}"

# Executables that move the sim/arm. Real mode must not start these.
GAZEBO_MOTION_EXECUTABLES = frozenset(
    {
        "spawn_entity.py",
        "spawner",
        "move_group",
        "servo_node_main",
        "servo_bridge",
        "named_pose",
        "robot_state_publisher",
    }
)


class InvalidArmMode(ValueError):
    """TELEOP_ARM was set to something other than gazebo|real."""


def serial_port_from_env(raw: str | None = None) -> str:
    """Serial device for the real arm.

    ``None`` reads ``TELEOP_SERIAL_PORT``. Unset or blank stays
    ``/dev/ttyACM0``.
    """
    if raw is None:
        raw = os.environ.get("TELEOP_SERIAL_PORT")
    if raw is None:
        return REAL_ARM_SERIAL_PORT
    value = raw.strip()
    if value == "":
        return REAL_ARM_SERIAL_PORT
    return value


def real_arm_gripper_log(port: str | None = None) -> str:
    """Gripper log line naming the serial port in use."""
    if port is None:
        chosen = serial_port_from_env()
    else:
        chosen = port.strip() or REAL_ARM_SERIAL_PORT
    return f"TELEOP_ARM=real; feetech gripper on {chosen}"


def parse_teleop_arm(raw: str | None) -> str:
    """Return gazebo|real. Empty/None defaults to gazebo; anything else errors."""
    if raw is None:
        return DEFAULT_ARM_MODE
    value = raw.strip().lower()
    if value == "":
        return DEFAULT_ARM_MODE
    if value not in VALID_ARM_MODES:
        raise InvalidArmMode(
            f"TELEOP_ARM must be 'gazebo' or 'real' (got {raw!r}); "
            "refusing to fall back to Gazebo"
        )
    return value


def env_flag_enabled(raw: str | None) -> bool:
    return (raw or "").strip().lower() in ("1", "true", "yes", "on")


def drops_cartesian_jog(arm_mode: str) -> bool:
    """Real arm is gripper-only: joints 1-5 are never commanded."""
    return arm_mode == ARM_REAL


def gripper_only_from_env(raw: str | None) -> bool:
    return env_flag_enabled(raw)


def key_direction_allowed(direction: str, gripper_only: bool) -> bool:
    if not gripper_only:
        return True
    return direction in GRIPPER_ONLY_DIRECTIONS
