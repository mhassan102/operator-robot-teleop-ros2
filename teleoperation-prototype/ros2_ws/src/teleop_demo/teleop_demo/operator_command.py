import argparse
import sys
import time

from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node
from rclpy.utilities import remove_ros_args


COMMANDS = {
    "forward": (0.5, 0.0),
    "backward": (-0.5, 0.0),
    "left": (0.0, 0.8),
    "right": (0.0, -0.8),
    "stop": (0.0, 0.0),
}


class OperatorCommand(Node):
    def __init__(self) -> None:
        super().__init__("operator_command")
        self.publisher = self.create_publisher(Twist, "/cmd_vel_raw", 10)

    def wait_for_robot(self, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline and rclpy.ok():
            if self.publisher.get_subscription_count() > 0:
                return True
            rclpy.spin_once(self, timeout_sec=0.1)
        return False

    def publish_direction(
        self, direction: str, count: int, rate: float, quiet: bool = False
    ) -> None:
        linear, angular = COMMANDS[direction]
        message = Twist()
        message.linear.x = linear
        message.angular.z = angular
        period = 1.0 / rate

        for index in range(1, count + 1):
            self.publisher.publish(message)
            if not quiet:
                self.get_logger().info(
                    f"COMMAND SENT direction={direction.upper()} "
                    f"sample={index}/{count} linear_x={linear:.3f} "
                    f"angular_z={angular:.3f}"
                )
            rclpy.spin_once(self, timeout_sec=period)


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a scripted velocity command.")
    parser.add_argument("--direction", choices=COMMANDS, required=True)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--rate", type=float, default=10.0)
    parser.add_argument("--quiet", action="store_true")
    parsed = parser.parse_args(arguments)
    if parsed.count < 1:
        parser.error("--count must be at least 1")
    if parsed.rate <= 0.0:
        parser.error("--rate must be greater than zero")
    return parsed


def main(args=None) -> None:
    raw_args = sys.argv if args is None else args
    non_ros_args = remove_ros_args(args=raw_args)
    parsed = parse_arguments(non_ros_args[1:])

    rclpy.init(args=raw_args)
    node = OperatorCommand()
    exit_code = 0
    try:
        if not node.wait_for_robot(timeout_sec=10.0):
            node.get_logger().error("No /cmd_vel_raw subscriber discovered within 10 seconds")
            exit_code = 2
        else:
            node.publish_direction(
                parsed.direction, parsed.count, parsed.rate, quiet=parsed.quiet
            )
    finally:
        node.destroy_node()
        rclpy.shutdown()

    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
