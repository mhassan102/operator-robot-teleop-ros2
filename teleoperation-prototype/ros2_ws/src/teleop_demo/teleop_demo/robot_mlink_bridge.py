"""Robot-side UDP shim: mlink control class ↔ local ROS /teleop/*.

Runs in the robot container. mlink-edge stays a separate host process.
"""

from __future__ import annotations

import signal

from geometry_msgs.msg import PoseStamped, Twist
from teleop_demo_msgs.msg import TeleopAck, TeleopCommand, TeleopHeartbeat, TeleopState
from teleop_demo_msgs.srv import GoNamedPose
import rclpy
from rclpy.node import Node

from teleop_demo.mlink_payload import (
    TYPE_COMMAND,
    TYPE_HEARTBEAT,
    TYPE_NAMED_POSE_REQ,
    Ack,
    Command,
    Heartbeat,
    NamedPoseRep,
    NamedPoseReq,
    PayloadError,
    State,
    ToolPose,
    decode,
    encode_ack,
    encode_named_pose_rep,
    encode_state,
    encode_tool_pose,
)
from teleop_demo.mlink_udp import mlink_enabled, open_from_env
from teleop_demo.qos import command_qos

NODE_NAME = "robot_mlink_bridge"
DEFAULT_TX = "127.0.0.1:5503"
DEFAULT_RX = "127.0.0.1:5504"


class RobotMlinkBridge(Node):
    def __init__(self) -> None:
        super().__init__(NODE_NAME)
        if not mlink_enabled():
            raise SystemExit("TELEOP_MLINK is not set; robot_mlink_bridge is F8-only")
        self._mlink = open_from_env(default_tx=DEFAULT_TX, default_rx=DEFAULT_RX)
        qos = command_qos()
        self.command_pub = self.create_publisher(TeleopCommand, "/teleop/command", qos)
        self.heartbeat_pub = self.create_publisher(
            TeleopHeartbeat, "/teleop/heartbeat", qos
        )
        self.create_subscription(TeleopAck, "/teleop/ack", self._on_ack, qos)
        self.create_subscription(TeleopState, "/teleop/state", self._on_state, qos)
        self.create_subscription(
            PoseStamped, "/teleop/tool_pose", self._on_pose, qos
        )
        self._named = self.create_client(GoNamedPose, "/teleop/go_named_pose")
        self._pending: list[tuple[int, str, object]] = []
        self.create_timer(0.005, self._poll)
        self.get_logger().info(
            f"MLINK BRIDGE tx={self._mlink.tx_addr} rx={self._mlink.rx_addr}"
        )

    def close(self) -> None:
        self._mlink.close()

    def _poll(self) -> None:
        while True:
            data = self._mlink.recv(timeout=0.0)
            if not data:
                break
            try:
                kind, msg = decode(data)
            except PayloadError as exc:
                self.get_logger().warn(f"drop bad control datagram: {exc}")
                continue
            if kind == TYPE_COMMAND and isinstance(msg, Command):
                self.command_pub.publish(_command_to_ros(msg))
            elif kind == TYPE_HEARTBEAT and isinstance(msg, Heartbeat):
                self.heartbeat_pub.publish(_heartbeat_to_ros(msg))
            elif kind == TYPE_NAMED_POSE_REQ and isinstance(msg, NamedPoseReq):
                self._handle_named_req(msg)

        still: list[tuple[int, str, object]] = []
        for req_id, name, future in self._pending:
            if not future.done():  # type: ignore[union-attr]
                still.append((req_id, name, future))
                continue
            try:
                result = future.result()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                self._send_named_rep(req_id, False, str(exc))
                continue
            if result is None:
                self._send_named_rep(req_id, False, "empty service response")
                continue
            self._send_named_rep(req_id, bool(result.success), result.message)
        self._pending = still

    def _handle_named_req(self, msg: NamedPoseReq) -> None:
        if not self._named.service_is_ready():
            self._send_named_rep(msg.req_id, False, "/teleop/go_named_pose unavailable")
            return
        request = GoNamedPose.Request()
        request.name = msg.name
        future = self._named.call_async(request)
        self._pending.append((msg.req_id, msg.name, future))
        self.get_logger().info(f"NAMED POSE via mlink name={msg.name} req={msg.req_id}")

    def _send_named_rep(self, req_id: int, success: bool, message: str) -> None:
        self._mlink.send(
            encode_named_pose_rep(NamedPoseRep(req_id, success, message))
        )

    def _on_ack(self, message: TeleopAck) -> None:
        self._mlink.send(
            encode_ack(
                Ack(
                    sequence=int(message.sequence),
                    source_sec=int(message.source_stamp.sec),
                    source_nsec=int(message.source_stamp.nanosec),
                    recv_sec=int(message.receive_stamp.sec),
                    recv_nsec=int(message.receive_stamp.nanosec),
                    session_id=message.session_id,
                    disposition=message.disposition,
                )
            )
        )

    def _on_state(self, message: TeleopState) -> None:
        twist = message.safe_twist
        self._mlink.send(
            encode_state(
                State(
                    stamp_sec=int(message.stamp.sec),
                    stamp_nsec=int(message.stamp.nanosec),
                    session_id=message.session_id,
                    frame_id=message.frame_id,
                    connection_state=message.connection_state,
                    watchdog_state=message.watchdog_state,
                    last_disposition=message.last_disposition,
                    lx=float(twist.linear.x),
                    ly=float(twist.linear.y),
                    lz=float(twist.linear.z),
                    ax=float(twist.angular.x),
                    ay=float(twist.angular.y),
                    az=float(twist.angular.z),
                    gripper=float(message.gripper),
                )
            )
        )

    def _on_pose(self, message: PoseStamped) -> None:
        position = message.pose.position
        orientation = message.pose.orientation
        self._mlink.send(
            encode_tool_pose(
                ToolPose(
                    stamp_sec=int(message.header.stamp.sec),
                    stamp_nsec=int(message.header.stamp.nanosec),
                    x=float(position.x),
                    y=float(position.y),
                    z=float(position.z),
                    qx=float(orientation.x),
                    qy=float(orientation.y),
                    qz=float(orientation.z),
                    qw=float(orientation.w),
                )
            )
        )


def _command_to_ros(msg: Command) -> TeleopCommand:
    out = TeleopCommand()
    out.sequence = msg.sequence
    out.stamp.sec = msg.stamp_sec
    out.stamp.nanosec = msg.stamp_nsec
    out.frame_id = msg.frame_id
    out.session_id = msg.session_id
    out.twist = Twist()
    out.twist.linear.x = msg.lx
    out.twist.linear.y = msg.ly
    out.twist.linear.z = msg.lz
    out.twist.angular.x = msg.ax
    out.twist.angular.y = msg.ay
    out.twist.angular.z = msg.az
    out.gripper = msg.gripper
    return out


def _heartbeat_to_ros(msg: Heartbeat) -> TeleopHeartbeat:
    out = TeleopHeartbeat()
    out.sequence = msg.sequence
    out.stamp.sec = msg.stamp_sec
    out.stamp.nanosec = msg.stamp_nsec
    out.session_id = msg.session_id
    return out


def main(args=None) -> None:
    rclpy.init(args=args)
    node = RobotMlinkBridge()

    def _request_shutdown(*_args) -> None:
        if rclpy.ok():
            rclpy.shutdown()

    signal.signal(signal.SIGTERM, _request_shutdown)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
