"""Robot-side arm target: Gazebo sim vs real hardware bring-up.

TELEOP_ARM=gazebo|real (default gazebo). Unknown values must not fall back
to Gazebo. Stage 1 real mode does not start a hardware driver.
"""

from __future__ import annotations

ARM_GAZEBO = "gazebo"
ARM_REAL = "real"
VALID_ARM_MODES = (ARM_GAZEBO, ARM_REAL)
DEFAULT_ARM_MODE = ARM_GAZEBO
REAL_HARDWARE_PLACEHOLDER_LOG = "TELEOP_ARM=real; hardware bridge not started"

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
