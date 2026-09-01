import uuid

from teleop_demo_msgs.msg import TeleopHeartbeat
import rclpy
from rclpy.node import Node

from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos


class OperatorHeartbeat(Node):
    def __init__(self) -> None:
        super().__init__("operator_heartbeat")
        declare_teleop_parameters(self)
        self.session_id = uuid.uuid4().hex[:12]
        self.sequence = 0
        rate = float(self.get_parameter("heartbeat_rate_hz").value)
        self.publisher = self.create_publisher(
            TeleopHeartbeat, "/teleop/heartbeat", command_qos()
        )
        self.timer = self.create_timer(1.0 / rate, self._tick)
        self.get_logger().info(
            f"HEARTBEAT START session={self.session_id} rate_hz={rate:.1f}"
        )

    def _tick(self) -> None:
        self.sequence += 1
        message = TeleopHeartbeat()
        message.sequence = self.sequence
        message.stamp = self.get_clock().now().to_msg()
        message.session_id = self.session_id
        self.publisher.publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OperatorHeartbeat()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
