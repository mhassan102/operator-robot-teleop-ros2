"""Shared teleoperation parameter declarations matching config/teleop.yaml."""

from rclpy.node import Node


def declare_teleop_parameters(node: Node) -> None:
    node.declare_parameter("command_rate_hz", 20.0)
    node.declare_parameter("heartbeat_rate_hz", 10.0)
    node.declare_parameter("watchdog_timeout_ms", 500)
    node.declare_parameter("max_linear_velocity", 1.0)
    node.declare_parameter("max_angular_velocity", 1.0)
    node.declare_parameter("telemetry_rate_hz", 10.0)
