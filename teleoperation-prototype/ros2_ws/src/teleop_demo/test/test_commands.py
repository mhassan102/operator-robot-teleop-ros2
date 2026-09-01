from geometry_msgs.msg import Twist

from teleop_demo.commands import (
    COMMANDS,
    build_sequence_list,
    clamp_velocity,
    command_direction,
    parse_arguments,
)


def make_twist(linear: float = 0.0, angular: float = 0.0) -> Twist:
    message = Twist()
    message.linear.x = linear
    message.angular.z = angular
    return message


def test_command_values() -> None:
    assert COMMANDS == {
        "forward": (0.5, 0.0),
        "backward": (-0.5, 0.0),
        "left": (0.0, 0.8),
        "right": (0.0, -0.8),
        "stop": (0.0, 0.0),
    }


def test_direction_classification() -> None:
    assert command_direction(make_twist(linear=0.5)) == "FORWARD"
    assert command_direction(make_twist(linear=-0.5)) == "BACKWARD"
    assert command_direction(make_twist(angular=0.8)) == "LEFT"
    assert command_direction(make_twist(angular=-0.8)) == "RIGHT"
    assert command_direction(make_twist()) == "STOP"


def test_valid_arguments() -> None:
    parsed = parse_arguments(
        ["--direction", "left", "--count", "3", "--rate", "20", "--quiet"]
    )
    assert parsed.direction == "left"
    assert parsed.count == 3
    assert parsed.rate == 20.0
    assert parsed.quiet is True
    assert parsed.inject == "none"


def test_duration_and_inject_arguments() -> None:
    parsed = parse_arguments(
        ["--direction", "forward", "--duration", "30", "--inject", "duplicate"]
    )
    assert parsed.duration == 30.0
    assert parsed.inject == "duplicate"


def test_velocity_clamping() -> None:
    linear, angular = clamp_velocity(1.5, -2.0, max_linear=1.0, max_angular=1.0)
    assert linear == 1.0
    assert angular == -1.0
    linear, angular = clamp_velocity(0.5, 0.8, max_linear=1.0, max_angular=1.0)
    assert linear == 0.5
    assert angular == 0.8


def test_sequence_fault_injection() -> None:
    assert build_sequence_list(5, "none") == [1, 2, 3, 4, 5]
    assert build_sequence_list(5, "duplicate") == [1, 2, 2, 3, 4, 5]
    assert build_sequence_list(5, "reorder") == [1, 3, 2, 4, 5]
