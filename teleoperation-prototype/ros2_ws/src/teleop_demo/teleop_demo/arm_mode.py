"""Robot-side arm target: Gazebo sim vs real hardware bring-up.

TELEOP_ARM=gazebo|real (default gazebo). Unknown values must not fall back
to Gazebo. Real mode starts robot_receiver plus the Feetech gripper node.
"""

from __future__ import annotations

ARM_GAZEBO = "gazebo"
ARM_REAL = "real"
VALID_ARM_MODES = (ARM_GAZEBO, ARM_REAL)
DEFAULT_ARM_MODE = ARM_GAZEBO
REAL_ARM_SERIAL_PORT = "/dev/ttyACM0"
REAL_ARM_COMPOSE_OVERLAY = "compose.robot-mlink.real-arm.yaml"
REAL_ARM_GRIPPER_LOG = "TELEOP_ARM=real; feetech gripper on /dev/ttyACM0"

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
