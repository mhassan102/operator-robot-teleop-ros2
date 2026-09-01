from teleop_demo.commands import (
    COMMANDS,
    JOG_ANGULAR,
    JOG_LINEAR,
    build_sequence_list,
    clamp_gripper,
    clamp_twist,
    command_direction,
    make_twist,
    parse_arguments,
    twist_is_finite,
)


def test_cartesian_command_values() -> None:
    assert COMMANDS["+x"][0] == JOG_LINEAR
    assert COMMANDS["x-"][0] == -JOG_LINEAR
    assert COMMANDS["+y"][1] == JOG_LINEAR
    assert COMMANDS["+z"][2] == JOG_LINEAR
    assert COMMANDS["+roll"][3] == JOG_ANGULAR
    assert COMMANDS["+pitch"][4] == JOG_ANGULAR
    assert COMMANDS["+yaw"][5] == JOG_ANGULAR
    assert COMMANDS["open"][6] == 1.0
    assert COMMANDS["close"][6] == 0.0
    assert COMMANDS["stop"] == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert COMMANDS["forward"] == COMMANDS["+x"]
    assert COMMANDS["backward"] == COMMANDS["x-"]
    assert COMMANDS["left"] == COMMANDS["+yaw"]
    assert COMMANDS["right"] == COMMANDS["yaw-"]


def test_direction_classification() -> None:
    assert command_direction(make_twist(linear_x=0.05)) == "+X"
    assert command_direction(make_twist(linear_x=-0.05)) == "-X"
    assert command_direction(make_twist(linear_y=0.05)) == "+Y"
    assert command_direction(make_twist(linear_z=-0.05)) == "-Z"
    assert command_direction(make_twist(angular_x=0.2)) == "+ROLL"
    assert command_direction(make_twist(angular_y=-0.2)) == "-PITCH"
    assert command_direction(make_twist(angular_z=0.2)) == "+YAW"
    assert command_direction(make_twist()) == "STOP"


def test_valid_arguments() -> None:
    parsed = parse_arguments(
        ["--direction", "+yaw", "--count", "3", "--rate", "20", "--quiet"]
    )
    assert parsed.direction == "+yaw"
    assert parsed.count == 3
    assert parsed.rate == 20.0
    assert parsed.quiet is True
    assert parsed.inject == "none"


def test_duration_and_inject_arguments() -> None:
    parsed = parse_arguments(
        ["--direction", "+x", "--duration", "30", "--inject", "duplicate"]
    )
    assert parsed.duration == 30.0
    assert parsed.inject == "duplicate"


def test_six_axis_clamping() -> None:
    twist = make_twist(1.5, -2.0, 0.4, 1.0, -0.9, 0.1)
    changed = clamp_twist(twist, max_linear=0.1, max_angular=0.3)
    assert changed is True
    assert twist.linear.x == 0.1
    assert twist.linear.y == -0.1
    assert twist.linear.z == 0.1
    assert twist.angular.x == 0.3
    assert twist.angular.y == -0.3
    assert twist.angular.z == 0.1
    assert clamp_gripper(1.5, 0.0, 1.0) == 1.0
    assert clamp_gripper(-0.2, 0.0, 1.0) == 0.0


def test_finite_twist_rejects_nan_and_inf() -> None:
    nan_twist = make_twist(linear_x=float("nan"))
    inf_twist = make_twist(angular_z=float("inf"))
    assert twist_is_finite(make_twist(linear_x=0.05)) is True
    assert twist_is_finite(nan_twist) is False
    assert twist_is_finite(inf_twist) is False


def test_sequence_fault_injection() -> None:
    assert build_sequence_list(5, "none") == [1, 2, 3, 4, 5]
    assert build_sequence_list(5, "duplicate") == [1, 2, 2, 3, 4, 5]
    assert build_sequence_list(5, "reorder") == [1, 3, 2, 4, 5]
