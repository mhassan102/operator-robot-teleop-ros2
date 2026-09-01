"""Shared command values, argument parsing, and velocity clamping."""

from __future__ import annotations

import argparse

from geometry_msgs.msg import Twist


COMMANDS = {
    "forward": (0.5, 0.0),
    "backward": (-0.5, 0.0),
    "left": (0.0, 0.8),
    "right": (0.0, -0.8),
    "stop": (0.0, 0.0),
}


def command_direction(message: Twist) -> str:
    linear = message.linear.x
    angular = message.angular.z
    if linear > 0.0:
        return "FORWARD"
    if linear < 0.0:
        return "BACKWARD"
    if angular > 0.0:
        return "LEFT"
    if angular < 0.0:
        return "RIGHT"
    return "STOP"


def clamp_velocity(
    linear: float, angular: float, max_linear: float, max_angular: float
) -> tuple[float, float]:
    linear = max(-max_linear, min(max_linear, linear))
    angular = max(-max_angular, min(max_angular, angular))
    return linear, angular


def build_sequence_list(count: int, inject: str) -> list[int]:
    sequences = list(range(1, count + 1))
    if inject == "duplicate" and count >= 2:
        sequences.insert(2, 2)
    elif inject == "reorder" and count >= 3:
        sequences[1], sequences[2] = sequences[2], sequences[1]
    return sequences


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a scripted stamped velocity command.")
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
