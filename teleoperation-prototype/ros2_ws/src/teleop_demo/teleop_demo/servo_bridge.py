"""Bridge safety Twist/gripper into MoveIt Servo and publish tool pose from TF.

Keeps /cmd_vel_safe as geometry_msgs/Twist. Humble Servo requires TwistStamped
with a fresh header stamp on /servo_node/delta_twist_cmds.
"""

from __future__ import annotations

from geometry_msgs.msg import PoseStamped, Twist, TwistStamped
from std_msgs.msg import Float64, Float64MultiArray
from std_srvs.srv import Trigger
import rclpy
from rclpy.node import Node
from tf2_ros import Buffer, TransformListener

from teleop_demo.arm_kinematics import gripper_positions
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos


class ServoBridge(Node):
    def __init__(self) -> None:
        super().__init__("servo_bridge")
        declare_teleop_parameters(self)
        rate = float(self.get_parameter("controller_rate_hz").value)
        self.dt = 1.0 / rate
        self.frame_id = str(self.get_parameter("command_frame").value)
        self.twist = Twist()
        self.gripper = 0.0
        self._servo_started = False
        qos = command_qos()
        self.twist_pub = self.create_publisher(
            TwistStamped, "/servo_node/delta_twist_cmds", qos
        )
        self.gripper_pub = self.create_publisher(
            Float64MultiArray, "/gripper_controller/commands", qos
        )
        self.pose_pub = self.create_publisher(PoseStamped, "/teleop/tool_pose", qos)
        self.create_subscription(Twist, "/cmd_vel_safe", self._on_twist, qos)
        self.create_subscription(Float64, "/gripper_safe", self._on_gripper, qos)
        self._start_cli = self.create_client(Trigger, "/servo_node/start_servo")
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)
        self.create_timer(self.dt, self._tick)
        self.create_timer(1.0, self._try_start_servo)
        self.get_logger().info(
            "SERVO BRIDGE READY cmd=/cmd_vel_safe servo=/servo_node/delta_twist_cmds"
        )

    def _on_twist(self, message: Twist) -> None:
        self.twist = message

    def _on_gripper(self, message: Float64) -> None:
        self.gripper = float(message.data)

    def _try_start_servo(self) -> None:
        if self._servo_started:
            return
        if not self._start_cli.service_is_ready():
            return
        future = self._start_cli.call_async(Trigger.Request())

        def _done(done_future) -> None:
            try:
                result = done_future.result()
            except Exception as exc:  # noqa: BLE001
                self.get_logger().warn(f"start_servo failed: {exc}")
                return
            if result is not None and result.success:
                self._servo_started = True
                self.get_logger().info("SERVO started via /servo_node/start_servo")
            else:
                message = getattr(result, "message", "")
                self.get_logger().warn(f"start_servo returned unsuccessful: {message}")

        future.add_done_callback(_done)

    def _tick(self) -> None:
        stamped = TwistStamped()
        stamped.header.stamp = self.get_clock().now().to_msg()
        stamped.header.frame_id = self.frame_id
        stamped.twist = self.twist
        self.twist_pub.publish(stamped)

        fingers = gripper_positions(self.gripper)
        grip_msg = Float64MultiArray()
        grip_msg.data = [float(fingers[0]), float(fingers[1])]
        self.gripper_pub.publish(grip_msg)
        self._publish_pose()

    def _publish_pose(self) -> None:
        try:
            trans = self.tf_buffer.lookup_transform("base_link", "tool0", rclpy.time.Time())
        except Exception:  # noqa: BLE001
            return
        pose = PoseStamped()
        pose.header.stamp = trans.header.stamp
        pose.header.frame_id = "base_link"
        pose.pose.position.x = trans.transform.translation.x
        pose.pose.position.y = trans.transform.translation.y
        pose.pose.position.z = trans.transform.translation.z
        pose.pose.orientation = trans.transform.rotation
        self.pose_pub.publish(pose)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ServoBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
