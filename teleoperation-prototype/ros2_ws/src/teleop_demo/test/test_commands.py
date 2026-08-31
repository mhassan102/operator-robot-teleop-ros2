from geometry_msgs.msg import Twist

from teleop_demo.operator_command import COMMANDS, parse_arguments
from teleop_demo.robot_receiver import command_direction


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
