import numpy as np

from teleop_demo.arm_kinematics import (
    HOME_POSITION,
    cartesian_step,
    forward_kinematics,
    gripper_positions,
)


def test_zero_pose_reaches_along_x() -> None:
    position, _rotation, jacobian = forward_kinematics(np.zeros(6))
    assert abs(position[0] - 0.50) < 1e-9
    assert abs(position[1]) < 1e-9
    assert abs(position[2] - 0.16) < 1e-9
    assert jacobian.shape == (6, 6)


def test_home_pose_is_in_front_of_base() -> None:
    position, _rotation, _jacobian = forward_kinematics(HOME_POSITION)
    assert position[0] > 0.15
    assert position[2] > 0.05


def test_plus_x_jog_moves_tool() -> None:
    start, rotation, _jacobian = forward_kinematics(HOME_POSITION)
    positions = HOME_POSITION.copy()
    linear = np.array([0.05, 0.0, 0.0])
    angular = np.zeros(3)
    for _ in range(50):
        positions = cartesian_step(positions, linear, angular, 0.02)
    end, _rotation, _j = forward_kinematics(positions)
    delta_tool = rotation.T @ (end - start)
    assert delta_tool[0] > 0.02


def test_slew_limits_rate() -> None:
    from teleop_demo.arm_kinematics import slew

    current = np.zeros(6)
    target = np.ones(6)
    stepped = slew(current, target, 0.1, 0.8)
    assert float(np.max(np.abs(stepped))) == 0.08


def test_gripper_open_amount() -> None:
    assert gripper_positions(0.0)[0] == 0.0
    assert abs(gripper_positions(1.0)[0] - 0.03) < 1e-9
