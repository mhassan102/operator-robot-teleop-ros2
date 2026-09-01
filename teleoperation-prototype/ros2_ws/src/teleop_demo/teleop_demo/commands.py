"""Cartesian command values, argument parsing, and clamping."""

from __future__ import annotations

import argparse
import math

from geometry_msgs.msg import Twist


# Tool jog rates. linear_* are m/s, angular_* are rad/s, gripper is 0..1.
JOG_LINEAR = 0.05
JOG_ANGULAR = 0.2

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


def make_twist(
    linear_x: float = 0.0,
    linear_y: float = 0.0,
    linear_z: float = 0.0,
    angular_x: float = 0.0,
    angular_y: float = 0.0,
    angular_z: float = 0.0,
) -> Twist:
    message = Twist()
    message.linear.x = linear_x
    message.linear.y = linear_y
    message.linear.z = linear_z
    message.angular.x = angular_x
    message.angular.y = angular_y
    message.angular.z = angular_z
    return message


def fill_twist(message: Twist, values: tuple[float, ...]) -> None:
    message.linear.x = values[0]
    message.linear.y = values[1]
    message.linear.z = values[2]
    message.angular.x = values[3]
    message.angular.y = values[4]
    message.angular.z = values[5]


def command_direction(message: Twist) -> str:
    components = (
        (message.linear.x, "+X", "-X"),
        (message.linear.y, "+Y", "-Y"),
        (message.linear.z, "+Z", "-Z"),
        (message.angular.x, "+ROLL", "-ROLL"),
        (message.angular.y, "+PITCH", "-PITCH"),
        (message.angular.z, "+YAW", "-YAW"),
    )
    best = max(components, key=lambda item: abs(item[0]))
    if best[0] == 0.0:
        return "STOP"
    return best[1] if best[0] > 0.0 else best[2]


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def clamp_twist(message: Twist, max_linear: float, max_angular: float) -> bool:
    """Clamp all six Twist axes in place. Return True if any value changed."""
    original = (
        message.linear.x,
        message.linear.y,
        message.linear.z,
        message.angular.x,
        message.angular.y,
        message.angular.z,
    )
    message.linear.x = _clamp(message.linear.x, max_linear)
    message.linear.y = _clamp(message.linear.y, max_linear)
    message.linear.z = _clamp(message.linear.z, max_linear)
    message.angular.x = _clamp(message.angular.x, max_angular)
    message.angular.y = _clamp(message.angular.y, max_angular)
    message.angular.z = _clamp(message.angular.z, max_angular)
    clamped = (
        message.linear.x,
        message.linear.y,
        message.linear.z,
        message.angular.x,
        message.angular.y,
        message.angular.z,
    )
    return clamped != original


def clamp_gripper(value: float, min_gripper: float, max_gripper: float) -> float:
    return max(min_gripper, min(max_gripper, value))


def twist_is_finite(message: Twist) -> bool:
    values = (
        message.linear.x,
        message.linear.y,
        message.linear.z,
        message.angular.x,
        message.angular.y,
        message.angular.z,
    )
    return all(math.isfinite(value) for value in values)


def zero_twist() -> Twist:
    return Twist()


def build_sequence_list(count: int, inject: str) -> list[int]:
    sequences = list(range(1, count + 1))
    if inject == "duplicate" and count >= 2:
        sequences.insert(2, 2)
    elif inject == "reorder" and count >= 3:
        sequences[1], sequences[2] = sequences[2], sequences[1]
    return sequences


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish a scripted stamped Cartesian command."
    )
    parser.add_argument("--direction", choices=COMMANDS, required=True)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument(
        "--rate",
        type=float,
        default=None,
        help="Publish rate in Hz. Defaults to the command_rate_hz parameter.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Override --count with rate * duration seconds.",
    )
    parser.add_argument(
        "--inject",
        choices=("none", "duplicate", "reorder"),
        default="none",
        help="Optional delivery fault for counter tests.",
    )
    parser.add_argument("--quiet", action="store_true")
    parsed = parser.parse_args(arguments)
    if parsed.count < 1:
        parser.error("--count must be at least 1")
    if parsed.rate is not None and parsed.rate <= 0.0:
        parser.error("--rate must be greater than zero")
    if parsed.duration is not None and parsed.duration <= 0.0:
        parser.error("--duration must be greater than zero")
    return parsed
