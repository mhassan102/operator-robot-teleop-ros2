"""Shared teleoperation parameter declarations matching config/teleop.yaml."""

from rclpy.node import Node


def declare_teleop_parameters(node: Node) -> None:
    node.declare_parameter("command_rate_hz", 20.0)
    node.declare_parameter("heartbeat_rate_hz", 10.0)
    node.declare_parameter("controller_rate_hz", 50.0)
    node.declare_parameter("watchdog_timeout_ms", 500)
    node.declare_parameter("command_timeout_ms", 200)
    node.declare_parameter("max_linear_velocity", 0.1)
    node.declare_parameter("max_angular_velocity", 0.3)
    node.declare_parameter("min_gripper", 0.0)
    node.declare_parameter("max_gripper", 1.0)
    node.declare_parameter("command_frame", "tool0")
    node.declare_parameter("telemetry_rate_hz", 10.0)
    node.declare_parameter("watchdog_keep_alive", "command_or_heartbeat")
