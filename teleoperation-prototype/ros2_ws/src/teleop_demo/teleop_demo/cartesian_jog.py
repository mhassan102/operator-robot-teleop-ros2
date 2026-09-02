"""Convert /cmd_vel_safe tool Twist into Gazebo joint position commands."""

from __future__ import annotations

import numpy as np
from geometry_msgs.msg import Pose, PoseStamped, Twist
from sensor_msgs.msg import JointState
from std_msgs.msg import Float64, Float64MultiArray
import rclpy
from rclpy.node import Node

from teleop_demo.arm_kinematics import (
    ARM_JOINT_NAMES,
    HOME_POSITION,
    cartesian_step,
    clamp_joints,
    forward_kinematics,
    gripper_positions,
    slew,
)
from teleop_demo.qos import command_qos

MAX_JOINT_RATE = 0.8


class CartesianJog(Node):
    def __init__(self) -> None:
        super().__init__("cartesian_jog")
        self.declare_parameter("control_rate_hz", 50.0)
        self.declare_parameter("command_frame", "tool0")
        rate = float(self.get_parameter("control_rate_hz").value)
        self.dt = 1.0 / rate
        self.positions = np.zeros(6)
        self.gripper = 0.0
        self.twist = np.zeros(3)
        self.angular = np.zeros(3)
        self.got_joints = False
        self.homed = False
        qos = command_qos()
        self.arm_pub = self.create_publisher(
            Float64MultiArray, "/arm_controller/commands", qos
        )
        self.gripper_pub = self.create_publisher(
            Float64MultiArray, "/gripper_controller/commands", qos
        )
        self.pose_pub = self.create_publisher(PoseStamped, "/teleop/tool_pose", qos)
        self.create_subscription(Twist, "/cmd_vel_safe", self._on_twist, qos)
        self.create_subscription(Float64, "/gripper_safe", self._on_gripper, qos)
        self.create_subscription(JointState, "/joint_states", self._on_joints, 10)
        self.create_timer(self.dt, self._tick)
        self.get_logger().info("CARTESIAN JOG READY topic=/cmd_vel_safe")

    def _on_twist(self, message: Twist) -> None:
        self.twist = np.array(
            [message.linear.x, message.linear.y, message.linear.z], dtype=float
        )
        self.angular = np.array(
            [message.angular.x, message.angular.y, message.angular.z], dtype=float
        )

    def _on_gripper(self, message: Float64) -> None:
        self.gripper = float(message.data)

    def _on_joints(self, message: JointState) -> None:
        name_to_position = dict(zip(message.name, message.position))
        if not all(name in name_to_position for name in ARM_JOINT_NAMES):
            return
        measured = np.array([name_to_position[name] for name in ARM_JOINT_NAMES], dtype=float)
        if not np.all(np.isfinite(measured)):
            return
        if not self.got_joints:
            self.positions = measured
            self.got_joints = True

    def _tick(self) -> None:
        if not self.got_joints:
            self._publish_commands(self.positions, 0.0)
            self._publish_pose()
            return
        if not self.homed:
            self.positions = slew(self.positions, HOME_POSITION, self.dt, MAX_JOINT_RATE)
            if float(np.max(np.abs(self.positions - HOME_POSITION))) < 0.03:
                self.homed = True
                self.get_logger().info("CARTESIAN JOG homed; accepting /cmd_vel_safe")
        else:
            target = cartesian_step(self.positions, self.twist, self.angular, self.dt)
            self.positions = clamp_joints(
                slew(self.positions, target, self.dt, MAX_JOINT_RATE)
            )
        self._publish_commands(self.positions, self.gripper)
        self._publish_pose()

    def _publish_commands(self, arm: np.ndarray, gripper: float) -> None:
        arm_msg = Float64MultiArray()
        arm_msg.data = [float(value) for value in arm]
        self.arm_pub.publish(arm_msg)
        fingers = gripper_positions(gripper)
        grip_msg = Float64MultiArray()
        grip_msg.data = [float(fingers[0]), float(fingers[1])]
        self.gripper_pub.publish(grip_msg)

    def _publish_pose(self) -> None:
        position, rotation, _jacobian = forward_kinematics(self.positions)
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = "base_link"
        pose.pose = _pose_from_pr(position, rotation)
        self.pose_pub.publish(pose)


def _pose_from_pr(position: np.ndarray, rotation: np.ndarray) -> Pose:
    pose = Pose()
    pose.position.x = float(position[0])
    pose.position.y = float(position[1])
    pose.position.z = float(position[2])
    qw, qx, qy, qz = _rotation_to_quaternion(rotation)
    pose.orientation.w = qw
    pose.orientation.x = qx
    pose.orientation.y = qy
    pose.orientation.z = qz
    return pose


def _rotation_to_quaternion(rotation: np.ndarray) -> tuple[float, float, float, float]:
    trace = float(np.trace(rotation))
    if trace > 0.0:
        scale = math_sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (rotation[2, 1] - rotation[1, 2]) / scale
        y = (rotation[0, 2] - rotation[2, 0]) / scale
        z = (rotation[1, 0] - rotation[0, 1]) / scale
        return w, x, y, z
    if rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        scale = math_sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2.0
        w = (rotation[2, 1] - rotation[1, 2]) / scale
        x = 0.25 * scale
        y = (rotation[0, 1] + rotation[1, 0]) / scale
        z = (rotation[0, 2] + rotation[2, 0]) / scale
        return w, x, y, z
    if rotation[1, 1] > rotation[2, 2]:
        scale = math_sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2.0
        w = (rotation[0, 2] - rotation[2, 0]) / scale
        x = (rotation[0, 1] + rotation[1, 0]) / scale
        y = 0.25 * scale
        z = (rotation[1, 2] + rotation[2, 1]) / scale
        return w, x, y, z
    scale = math_sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2.0
    w = (rotation[1, 0] - rotation[0, 1]) / scale
    x = (rotation[0, 2] + rotation[2, 0]) / scale
    y = (rotation[1, 2] + rotation[2, 1]) / scale
    z = 0.25 * scale
    return w, x, y, z


def math_sqrt(value: float) -> float:
    return float(np.sqrt(max(value, 1e-12)))


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CartesianJog()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
