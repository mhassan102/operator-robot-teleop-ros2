"""Keyboard rates for the operator process.

The six jog rates and the gripper value match ``teleop_demo.commands``.
That module stays in the ROS tree for the robot. This copy does not
import it. Packaged teleop is gripper-only: ``g`` and ``h`` change the
gripper, and the other keys are ignored.
"""

from __future__ import annotations

JOG_LINEAR = 0.05
JOG_ANGULAR = 0.2

# lx, ly, lz, ax, ay, az, gripper. Linear is m/s, angular is rad/s.
COMMANDS = {
    "+x": (JOG_LINEAR, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "x-": (-JOG_LINEAR, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "+y": (0.0, JOG_LINEAR, 0.0, 0.0, 0.0, 0.0, 0.0),
    "y-": (0.0, -JOG_LINEAR, 0.0, 0.0, 0.0, 0.0, 0.0),
    "+z": (0.0, 0.0, JOG_LINEAR, 0.0, 0.0, 0.0, 0.0),
    "z-": (0.0, 0.0, -JOG_LINEAR, 0.0, 0.0, 0.0, 0.0),
    "+roll": (0.0, 0.0, 0.0, JOG_ANGULAR, 0.0, 0.0, 0.0),
    "roll-": (0.0, 0.0, 0.0, -JOG_ANGULAR, 0.0, 0.0, 0.0),
    "+pitch": (0.0, 0.0, 0.0, 0.0, JOG_ANGULAR, 0.0, 0.0),
    "pitch-": (0.0, 0.0, 0.0, 0.0, -JOG_ANGULAR, 0.0, 0.0),
    "+yaw": (0.0, 0.0, 0.0, 0.0, 0.0, JOG_ANGULAR, 0.0),
    "yaw-": (0.0, 0.0, 0.0, 0.0, 0.0, -JOG_ANGULAR, 0.0),
    "stop": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "open": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0),
    "close": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "forward": (JOG_LINEAR, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "backward": (-JOG_LINEAR, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "left": (0.0, 0.0, 0.0, 0.0, 0.0, JOG_ANGULAR, 0.0),
    "right": (0.0, 0.0, 0.0, 0.0, 0.0, -JOG_ANGULAR, 0.0),
}

KEY_BINDINGS = {
    "w": "+x",
    "s": "x-",
    "a": "+y",
    "d": "y-",
    "r": "+z",
    "f": "z-",
    "u": "+roll",
    "o": "roll-",
    "i": "+pitch",
    "k": "pitch-",
    "j": "+yaw",
    "l": "yaw-",
    " ": "stop",
    "g": "open",
    "h": "close",
}

GRIPPER_ONLY = frozenset({"open", "close", "stop"})
FRAME_ID = "tool0"
COMMAND_RATE_HZ = 20.0
HEARTBEAT_RATE_HZ = 10.0

# Hold is neither an open nor a close. Each keydown sends the other token
# in its band so a latched 1.0 or 0.0 is not the next press. The robot
# treats 1.0 and 0.75 as open, and 0.25 and 0.0 as close.
GRIPPER_HOLD = 0.5


def gripper_step_value(latched: float, direction: str) -> float:
    """Next gripper float for one open or close press. Layout stays one float."""
    if direction == "open":
        return 0.75 if latched == 1.0 else 1.0
    return 0.0 if latched == 0.25 else 0.25


def direction_allowed(direction: str, *, gripper_only: bool = True) -> bool:
    """Packaged teleop allows the gripper and stop. Cartesian keys do not."""
    if not gripper_only:
        return True
    return direction in GRIPPER_ONLY
