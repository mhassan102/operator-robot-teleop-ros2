import time

from geometry_msgs.msg import Twist
from std_msgs.msg import Float64
from teleop_demo_msgs.msg import TeleopAck, TeleopCommand, TeleopHeartbeat, TeleopState
import rclpy
from rclpy.node import Node

from teleop_demo.commands import command_direction
from teleop_demo.delivery import DeliveryTracker, LatencyStats, time_msg_to_ns
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos
from teleop_demo.safety import SafetyController, SafetyOutput


class RobotReceiver(Node):
    def __init__(self) -> None:
        super().__init__("robot_command_receiver")
        declare_teleop_parameters(self)
        self.session_id = ""
        self.tracker = DeliveryTracker()
        self.latency = LatencyStats()
        self.safety = SafetyController(
            watchdog_timeout_s=int(self.get_parameter("watchdog_timeout_ms").value) / 1000.0,
            command_timeout_s=int(self.get_parameter("command_timeout_ms").value) / 1000.0,
            max_linear=float(self.get_parameter("max_linear_velocity").value),
            max_angular=float(self.get_parameter("max_angular_velocity").value),
            min_gripper=float(self.get_parameter("min_gripper").value),
            max_gripper=float(self.get_parameter("max_gripper").value),
            default_frame=str(self.get_parameter("command_frame").value),
            keep_alive=str(self.get_parameter("watchdog_keep_alive").value),
        )
        qos = command_qos()
        self.ack_publisher = self.create_publisher(TeleopAck, "/teleop/ack", qos)
        self.safe_twist_publisher = self.create_publisher(Twist, "/cmd_vel_safe", qos)
        self.safe_gripper_publisher = self.create_publisher(Float64, "/gripper_safe", qos)
        self.state_publisher = self.create_publisher(TeleopState, "/teleop/state", qos)
        self.subscription = self.create_subscription(
            TeleopCommand, "/teleop/command", self.command_callback, qos
        )
        self.heartbeat_subscription = self.create_subscription(
            TeleopHeartbeat, "/teleop/heartbeat", self.heartbeat_callback, qos
        )
        controller_rate = float(self.get_parameter("controller_rate_hz").value)
        self.create_timer(1.0 / controller_rate, self._safety_tick)
        self.get_logger().info(
            "ROBOT RECEIVER READY topic=/teleop/command "
            f"keep_alive={self.safety.keep_alive} "
            f"watchdog_ms={int(self.safety.watchdog_timeout_s * 1000)}"
        )

    def heartbeat_callback(self, message: TeleopHeartbeat) -> None:
        transitions = self.safety.on_heartbeat(time.monotonic())
        self._log_transitions(transitions)

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

        disposition, transitions = self.safety.on_command(
            message.twist,
            float(message.gripper),
            message.frame_id,
            time.monotonic(),
        )
        self._log_transitions(transitions)

        ack = TeleopAck()
        ack.sequence = message.sequence
        ack.session_id = message.session_id
        ack.source_stamp = message.stamp
        ack.receive_stamp = receive_time.to_msg()
        ack.disposition = disposition
        self.ack_publisher.publish(ack)

        self.get_logger().info(
            "COMMAND RECEIVED "
            f"seq={message.sequence} "
            f"session={self.session_id} "
            f"direction={command_direction(message.twist)} "
            f"linear_x={message.twist.linear.x:.3f} "
            f"linear_y={message.twist.linear.y:.3f} "
            f"linear_z={message.twist.linear.z:.3f} "
            f"angular_x={message.twist.angular.x:.3f} "
            f"angular_y={message.twist.angular.y:.3f} "
            f"angular_z={message.twist.angular.z:.3f} "
            f"gripper={message.gripper:.3f} "
            f"frame={message.frame_id} "
            f"disposition={disposition} "
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

    def _safety_tick(self) -> None:
        output = self.safety.tick(time.monotonic())
        self._log_transitions(output.transitions)
        self.safe_twist_publisher.publish(output.twist)
        gripper = Float64()
        gripper.data = output.gripper
        self.safe_gripper_publisher.publish(gripper)
        self._publish_state(output)

    def _publish_state(self, output: SafetyOutput) -> None:
        state = TeleopState()
        state.stamp = self.get_clock().now().to_msg()
        state.session_id = self.session_id
        state.frame_id = output.frame_id
        state.connection_state = output.connection_state
        state.watchdog_state = output.watchdog_state
        state.last_disposition = output.last_disposition
        state.safe_twist = output.twist
        state.gripper = output.gripper
        self.state_publisher.publish(state)

    def _log_transitions(self, transitions: list[str]) -> None:
        for name in transitions:
            self.get_logger().info(f"SAFETY STATE={name}")


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
