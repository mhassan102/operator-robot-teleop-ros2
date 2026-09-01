from teleop_demo_msgs.msg import TeleopAck, TeleopCommand
import rclpy
from rclpy.node import Node

from teleop_demo.commands import command_direction
from teleop_demo.delivery import DeliveryTracker, LatencyStats, time_msg_to_ns
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos


class RobotReceiver(Node):
    def __init__(self) -> None:
        super().__init__("robot_command_receiver")
        declare_teleop_parameters(self)
        self.session_id = ""
        self.tracker = DeliveryTracker()
        self.latency = LatencyStats()
        self.ack_publisher = self.create_publisher(TeleopAck, "/teleop/ack", command_qos())
        self.subscription = self.create_subscription(
            TeleopCommand,
            "/teleop/command",
            self.command_callback,
            command_qos(),
        )
        self.get_logger().info("ROBOT RECEIVER READY topic=/teleop/command")

    def command_callback(self, message: TeleopCommand) -> None:
        if message.session_id != self.session_id:
            previous = self.session_id
            self.session_id = message.session_id
            self.tracker.reset()
            self.latency.reset()
            if previous:
                self.get_logger().info(
                    f"SESSION RESET previous={previous} new={self.session_id}"
                )
            else:
                self.get_logger().info(f"SESSION START session_id={self.session_id}")

        receive_time = self.get_clock().now()
        source_ns = time_msg_to_ns(message.stamp)
        receive_ns = receive_time.nanoseconds
        one_way_ns = receive_ns - source_ns
        self.tracker.observe(int(message.sequence))
        self.latency.add(one_way_ns)

        ack = TeleopAck()
        ack.sequence = message.sequence
        ack.session_id = message.session_id
        ack.source_stamp = message.stamp
        ack.receive_stamp = receive_time.to_msg()
        ack.disposition = "ACCEPTED"
        self.ack_publisher.publish(ack)

        self.get_logger().info(
            "COMMAND RECEIVED "
            f"seq={message.sequence} "
            f"session={self.session_id} "
            f"direction={command_direction(message.twist)} "
            f"linear_x={message.twist.linear.x:.3f} "
            f"angular_z={message.twist.angular.z:.3f} "
            f"source_time_ns={source_ns} "
            f"receive_time_ns={receive_ns} "
            f"one_way_ns={one_way_ns}"
        )
        self.get_logger().info(
            "STATS "
            f"session={self.session_id} "
            f"received={self.tracker.received} "
            f"unique={self.tracker.unique} "
            f"missing={self.tracker.missing} "
            f"duplicates={self.tracker.duplicates} "
            f"out_of_order={self.tracker.out_of_order} "
            f"latency_current_ns={self.latency.current_ns} "
            f"latency_min_ns={self.latency.min_ns} "
            f"latency_max_ns={self.latency.max_ns} "
            f"latency_avg_ns={self.latency.average_ns}"
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
