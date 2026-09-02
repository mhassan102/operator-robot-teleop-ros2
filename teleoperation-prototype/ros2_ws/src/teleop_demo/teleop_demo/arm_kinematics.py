"""Geometric FK and Jacobian for the teleop_demo 6-DOF tabletop arm.

Joint origins and axes must match urdf/teleop_arm.urdf.xacro.
"""

from __future__ import annotations

import math

import numpy as np

# (translation in parent, rotation axis in parent)
JOINT_SPECS = (
    ((0.0, 0.0, 0.08), (0.0, 0.0, 1.0)),
    ((0.0, 0.0, 0.08), (0.0, 1.0, 0.0)),
    ((0.20, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ((0.18, 0.0, 0.0), (0.0, 1.0, 0.0)),
    ((0.04, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
)
TOOL_OFFSET = np.array([0.08, 0.0, 0.0])

JOINT_LIMITS = (
    (-2.8, 2.8),
    (-1.6, 1.6),
    (-2.4, 2.4),
    (-1.8, 1.8),
    (-2.8, 2.8),
    (-2.8, 2.8),
)

HOME_POSITION = np.array([0.0, 0.55, -1.15, 0.60, 0.0, 0.0])
FOLD_POSITION = np.array([1.2, 0.35, -1.70, 0.80, 0.0, 0.0])
NAMED_POSES = {
    "home": HOME_POSITION,
    "fold": FOLD_POSITION,
}
ARM_JOINT_NAMES = (
    "joint1",
    "joint2",
    "joint3",
    "joint4",
    "joint5",
    "joint6",
)
GRIPPER_JOINT_NAMES = ("finger_left", "finger_right")
GRIPPER_OPEN = 0.03


def _rot(axis: tuple[float, float, float], angle: float) -> np.ndarray:
    x, y, z = axis
    c = math.cos(angle)
    s = math.sin(angle)
    t = 1.0 - c
    return np.array(
        [
            [t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )


def _trans(xyz: tuple[float, float, float]) -> np.ndarray:
    matrix = np.eye(4)
    matrix[0, 3] = xyz[0]
    matrix[1, 3] = xyz[1]
    matrix[2, 3] = xyz[2]
    return matrix


def forward_kinematics(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return tool position, rotation, and 6x6 geometric Jacobian in base_link."""
    transform = np.eye(4)
    origins = []
    axes = []
    for angle, (xyz, axis) in zip(positions, JOINT_SPECS):
        transform = transform @ _trans(xyz)
        origins.append(transform[:3, 3].copy())
        axis_vec = transform[:3, :3] @ np.array(axis)
        axes.append(axis_vec / np.linalg.norm(axis_vec))
        transform = transform @ _rot(axis, float(angle))
    transform = transform @ _trans(tuple(TOOL_OFFSET))
    position = transform[:3, 3].copy()
    rotation = transform[:3, :3].copy()
    jacobian = np.zeros((6, 6))
    for index, (origin, axis) in enumerate(zip(origins, axes)):
        jacobian[:3, index] = np.cross(axis, position - origin)
        jacobian[3:, index] = axis
    return position, rotation, jacobian


def clamp_joints(positions: np.ndarray) -> np.ndarray:
    clamped = positions.copy()
    for index, (lower, upper) in enumerate(JOINT_LIMITS):
        clamped[index] = min(upper, max(lower, clamped[index]))
    return clamped


def slew(current: np.ndarray, target: np.ndarray, dt: float, max_rate: float) -> np.ndarray:
    delta = target - current
    max_step = max_rate * dt
    biggest = float(np.max(np.abs(delta)))
    if biggest <= max_step or biggest < 1e-9:
        return target.copy()
    return current + delta * (max_step / biggest)


def cartesian_step(
    positions: np.ndarray,
    linear: np.ndarray,
    angular: np.ndarray,
    dt: float,
    damping: float = 0.08,
) -> np.ndarray:
    """Integrate a tool-frame Twist for dt seconds using damped least squares."""
    _position, rotation, jacobian = forward_kinematics(positions)
    linear_base = rotation @ linear
    angular_base = rotation @ angular
    twist = np.concatenate((linear_base, angular_base))
    if float(np.linalg.norm(twist)) < 1e-6:
        return positions.copy()
    jjt = jacobian @ jacobian.T
    damped = jjt + (damping ** 2) * np.eye(6)
    dq = jacobian.T @ np.linalg.solve(damped, twist)
    next_positions = clamp_joints(positions + dq * dt)
    return next_positions


def gripper_positions(open_amount: float) -> np.ndarray:
    width = max(0.0, min(1.0, open_amount)) * GRIPPER_OPEN
    return np.array([width, width])
