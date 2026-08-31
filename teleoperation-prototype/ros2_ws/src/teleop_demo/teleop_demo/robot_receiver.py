from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node


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


class RobotReceiver(Node):
    def __init__(self) -> None:
        super().__init__("robot_command_receiver")
        self.received_count = 0
        self.subscription = self.create_subscription(
            Twist,
            "/cmd_vel_raw",
            self.command_callback,
            10,
        )
        self.get_logger().info("ROBOT RECEIVER READY topic=/cmd_vel_raw")

    def command_callback(self, message: Twist) -> None:
        self.received_count += 1
        receive_time_ns = self.get_clock().now().nanoseconds
        self.get_logger().info(
            f"COMMAND RECEIVED count={self.received_count} "
            f"direction={command_direction(message)} "
            f"linear_x={message.linear.x:.3f} "
            f"angular_z={message.angular.z:.3f} "
            f"receive_time_ns={receive_time_ns}"
        )


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RobotReceiver()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
