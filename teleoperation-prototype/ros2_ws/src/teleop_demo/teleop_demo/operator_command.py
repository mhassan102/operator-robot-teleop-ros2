import sys
import time
import uuid

from teleop_demo_msgs.msg import TeleopAck, TeleopCommand
import rclpy
from rclpy.node import Node
from rclpy.utilities import remove_ros_args

from teleop_demo.commands import (
    COMMANDS,
    build_sequence_list,
    clamp_gripper,
    clamp_twist,
    fill_twist,
    parse_arguments,
)
from teleop_demo.delivery import DeliveryTracker, LatencyStats, time_msg_to_ns
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos


class OperatorCommand(Node):
    def __init__(self) -> None:
        super().__init__("operator_command")
        declare_teleop_parameters(self)
        self.session_id = uuid.uuid4().hex[:12]
        self.publisher = self.create_publisher(TeleopCommand, "/teleop/command", command_qos())
        self.ack_subscription = self.create_subscription(
            TeleopAck,
            "/teleop/ack",
            self.ack_callback,
            command_qos(),
        )
        self.sent = 0
        self.acks = 0
        self.one_way = LatencyStats()
        self.rtt = LatencyStats()
        self.ack_tracker = DeliveryTracker()

    def wait_for_robot(self, timeout_sec: float) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline and rclpy.ok():
            if self.publisher.get_subscription_count() > 0:
                return True
            rclpy.spin_once(self, timeout_sec=0.1)
        return False

    def ack_callback(self, message: TeleopAck) -> None:
        if message.session_id != self.session_id:
            return
        now = self.get_clock().now()
        source_ns = time_msg_to_ns(message.source_stamp)
        receive_ns = time_msg_to_ns(message.receive_stamp)
        one_way_ns = receive_ns - source_ns
        rtt_ns = now.nanoseconds - source_ns
        self.acks += 1
        self.ack_tracker.observe(message.sequence)
        self.one_way.add(one_way_ns)
        self.rtt.add(rtt_ns)

    def publish_sequence(
        self,
        direction: str,
        sequences: list[int],
        rate: float,
        values: tuple[float, ...],
        frame_id: str,
        quiet: bool,
    ) -> None:
        period = 1.0 / rate
        total = len(sequences)
        next_send = time.monotonic()
        for index, sequence in enumerate(sequences, start=1):
            now = self.get_clock().now()
            message = TeleopCommand()
            message.sequence = sequence
            message.stamp = now.to_msg()
            message.frame_id = frame_id
            message.session_id = self.session_id
            fill_twist(message.twist, values)
            message.gripper = values[6]
            self.publisher.publish(message)
            self.sent += 1
            if not quiet:
                self.get_logger().info(
                    "COMMAND SENT "
                    f"seq={sequence} "
                    f"session={self.session_id} "
                    f"direction={direction} "
                    f"sample={index}/{total} "
                    f"linear_x={message.twist.linear.x:.3f} "
                    f"linear_y={message.twist.linear.y:.3f} "
                    f"linear_z={message.twist.linear.z:.3f} "
                    f"angular_x={message.twist.angular.x:.3f} "
                    f"angular_y={message.twist.angular.y:.3f} "
                    f"angular_z={message.twist.angular.z:.3f} "
                    f"gripper={message.gripper:.3f} "
                    f"frame={frame_id} "
                    f"source_time_ns={now.nanoseconds}"
                )
            next_send += period
            while time.monotonic() < next_send and rclpy.ok():
                remaining = next_send - time.monotonic()
                rclpy.spin_once(self, timeout_sec=max(remaining, 0.0))

    def wait_for_acks(self, expected: int, timeout_sec: float) -> None:
        deadline = time.monotonic() + timeout_sec
        while self.acks < expected and time.monotonic() < deadline and rclpy.ok():
            rclpy.spin_once(self, timeout_sec=0.05)

    def stats_line(self) -> str:
        missing_acks = max(self.sent - self.acks, 0)
        return (
            "OPERATOR STATS "
            f"session={self.session_id} "
            f"sent={self.sent} "
            f"acks={self.acks} "
            f"missing_acks={missing_acks} "
            f"duplicates={self.ack_tracker.duplicates} "
            f"out_of_order={self.ack_tracker.out_of_order} "
            f"one_way_current_ns={self.one_way.current_ns} "
            f"one_way_min_ns={self.one_way.min_ns} "
            f"one_way_max_ns={self.one_way.max_ns} "
            f"one_way_avg_ns={self.one_way.average_ns} "
            f"rtt_current_ns={self.rtt.current_ns} "
            f"rtt_min_ns={self.rtt.min_ns} "
            f"rtt_max_ns={self.rtt.max_ns} "
            f"rtt_avg_ns={self.rtt.average_ns}"
        )


def _resolve_count_and_rate(node: OperatorCommand, parsed) -> tuple[int, float]:
    rate = parsed.rate
    if rate is None:
        rate = float(node.get_parameter("command_rate_hz").value)
    if parsed.duration is not None:
        return max(1, int(round(rate * parsed.duration))), rate
    return parsed.count, rate


def main(args=None) -> None:
    raw_args = sys.argv if args is None else args
    non_ros_args = remove_ros_args(args=raw_args)
    parsed = parse_arguments(non_ros_args[1:])

    rclpy.init(args=raw_args)
    node = OperatorCommand()
    exit_code = 0
    try:
        max_linear = float(node.get_parameter("max_linear_velocity").value)
        max_angular = float(node.get_parameter("max_angular_velocity").value)
        min_gripper = float(node.get_parameter("min_gripper").value)
        max_gripper = float(node.get_parameter("max_gripper").value)
        frame_id = str(node.get_parameter("command_frame").value)
        values = list(COMMANDS[parsed.direction])
        message_twist = TeleopCommand().twist
        fill_twist(message_twist, values)
        clamp_twist(message_twist, max_linear, max_angular)
        values[0] = message_twist.linear.x
        values[1] = message_twist.linear.y
        values[2] = message_twist.linear.z
        values[3] = message_twist.angular.x
        values[4] = message_twist.angular.y
        values[5] = message_twist.angular.z
        values[6] = clamp_gripper(values[6], min_gripper, max_gripper)
        count, rate = _resolve_count_and_rate(node, parsed)
        sequences = build_sequence_list(count, parsed.inject)

        if not node.wait_for_robot(timeout_sec=10.0):
            node.get_logger().error("No /teleop/command subscriber discovered within 10 seconds")
            exit_code = 2
        else:
            node.publish_sequence(
                parsed.direction,
                sequences,
                rate,
                tuple(values),
                frame_id,
                parsed.quiet,
            )
            node.wait_for_acks(expected=len(sequences), timeout_sec=10.0)
            node.get_logger().info(node.stats_line())
            if parsed.inject == "none" and node.acks != node.sent:
                node.get_logger().error(
                    f"Expected {node.sent} acknowledgements, received {node.acks}"
                )
                exit_code = 1
    finally:
        node.destroy_node()
        rclpy.shutdown()

    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
