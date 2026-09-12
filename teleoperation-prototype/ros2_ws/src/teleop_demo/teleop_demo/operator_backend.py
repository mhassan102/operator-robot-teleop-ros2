"""Operator backend: ROS 2 node plus localhost HTTP/WebSocket console (F5.4).

Heartbeat and /teleop/command publish only while a /ws/session socket is open.
Telemetry from /teleop/state and /teleop/tool_pose is display-only.
Named poses are a rclpy client of /teleop/go_named_pose (not a second writer).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from geometry_msgs.msg import PoseStamped
from teleop_demo_msgs.msg import TeleopCommand, TeleopHeartbeat, TeleopState
from teleop_demo_msgs.srv import GoNamedPose
import rclpy
from rclpy.node import Node

from teleop_demo.arm_kinematics import NAMED_POSES
from teleop_demo.commands import COMMANDS, KEY_BINDINGS, fill_twist
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos
from teleop_demo.wsproto import (
    WsConnection,
    handshake_accept,
    is_websocket_upgrade,
)

WEB_ROOT = Path(os.environ.get("TELEOP_WEB_ROOT", "/teleop/web"))
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8090
NODE_NAME = "operator_backend"
NAMED_POSE_SERVICE = "/teleop/go_named_pose"
NAMED_POSE_NAMES = frozenset(NAMED_POSES)
NAMED_POSE_WAIT_SEC = 2.0
NAMED_POSE_CALL_SEC = 90.0
MAX_JSON_BODY = 4096

_node_ready = threading.Event()


class OperatorBackend(Node):
    def __init__(self) -> None:
        super().__init__(NODE_NAME)
        declare_teleop_parameters(self)
        self.frame_id = str(self.get_parameter("command_frame").value)
        qos = command_qos()
        self.command_pub = self.create_publisher(
            TeleopCommand, "/teleop/command", qos
        )
        self.heartbeat_pub = self.create_publisher(
            TeleopHeartbeat, "/teleop/heartbeat", qos
        )
        self._lock = threading.Lock()
        self._connection: Optional[WsConnection] = None
        self.session_id = ""
        self.command_sequence = 0
        self.heartbeat_sequence = 0
        self._motion = COMMANDS["stop"][:6]
        self._gripper = 0.0
        self._label = "stop"
        self._active_key: Optional[str] = None
        self._state_connection = ""
        self._state_watchdog = ""
        self._state_session_id = ""
        self._state_disposition = ""
        self._pose: Optional[dict] = None
        self.create_subscription(
            TeleopState, "/teleop/state", self._on_state, qos
        )
        self.create_subscription(
            PoseStamped, "/teleop/tool_pose", self._on_pose, qos
        )
        self._named_pose = self.create_client(GoNamedPose, NAMED_POSE_SERVICE)
        command_rate = float(self.get_parameter("command_rate_hz").value)
        heartbeat_rate = float(self.get_parameter("heartbeat_rate_hz").value)
        telemetry_rate = float(self.get_parameter("telemetry_rate_hz").value)
        self.create_timer(1.0 / command_rate, self._publish_command)
        self.create_timer(1.0 / heartbeat_rate, self._publish_heartbeat)
        self.create_timer(1.0 / telemetry_rate, self._publish_telemetry)
        _node_ready.set()
        self.get_logger().info(
            f"HTTP {HTTP_HOST}:{HTTP_PORT} serving {WEB_ROOT}"
        )

    def attach_session(self, connection: WsConnection) -> str:
        with self._lock:
            old = self._end_locked()
            session_id = self._begin_locked(connection)
        if old is not None:
            old.close()
        self.get_logger().info(f"SESSION OPEN session={session_id}")
        return session_id

    def detach_session(self, connection: WsConnection) -> None:
        with self._lock:
            if self._connection is not connection:
                return
            old = self._end_locked()
        if old is not None:
            old.close()
        self.get_logger().info("SESSION CLOSE")

    def shutdown_session(self) -> None:
        with self._lock:
            old = self._end_locked()
        if old is not None:
            old.close()

    def handle_client_message(self, data: dict) -> None:
        if data.get("type") != "key":
            return
        key = data.get("key")
        down = data.get("down")
        if not isinstance(key, str) or not isinstance(down, bool):
            return
        self.apply_key(key, down)

    def apply_key(self, key: str, down: bool) -> None:
        direction = KEY_BINDINGS.get(key)
        if direction is None:
            return
        values = COMMANDS[direction]
        with self._lock:
            if self._connection is None:
                return
            if direction in ("open", "close"):
                if down:
                    self._gripper = values[6]
                    self._label = direction
            elif direction == "stop":
                if down:
                    self._motion = values[:6]
                    self._label = "stop"
                    self._active_key = None
            elif down:
                self._motion = values[:6]
                self._label = direction
                self._active_key = key
            elif self._active_key == key:
                self._motion = COMMANDS["stop"][:6]
                self._label = "stop"
                self._active_key = None
        if down or direction not in ("open", "close", "stop"):
            self.get_logger().info(f"KEY {direction} down={down}")

    def _begin_locked(self, connection: WsConnection) -> str:
        self._connection = connection
        self.session_id = uuid.uuid4().hex[:12]
        self.command_sequence = 0
        self.heartbeat_sequence = 0
        self._motion = COMMANDS["stop"][:6]
        self._gripper = 0.0
        self._label = "stop"
        self._active_key = None
        self._publish_heartbeat_locked()
        return self.session_id

    def _end_locked(self) -> Optional[WsConnection]:
        old = self._connection
        if old is None:
            return None
        self._connection = None
        self._motion = COMMANDS["stop"][:6]
        self._label = "stop"
        self._active_key = None
        self._publish_stop_locked()
        self.session_id = ""
        return old

    def _publish_command(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            self.command_sequence += 1
            message = self._command_message_locked()
            self.command_pub.publish(message)

    def _publish_heartbeat(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            self._publish_heartbeat_locked()

    def _publish_heartbeat_locked(self) -> None:
        self.heartbeat_sequence += 1
        message = TeleopHeartbeat()
        message.sequence = self.heartbeat_sequence
        message.stamp = self.get_clock().now().to_msg()
        message.session_id = self.session_id
        self.heartbeat_pub.publish(message)

    def _publish_stop_locked(self) -> None:
        self.command_sequence += 1
        message = self._command_message_locked()
        self.command_pub.publish(message)

    def _command_message_locked(self) -> TeleopCommand:
        message = TeleopCommand()
        message.sequence = self.command_sequence
        message.stamp = self.get_clock().now().to_msg()
        message.frame_id = self.frame_id
        message.session_id = self.session_id
        fill_twist(message.twist, self._motion)
        message.gripper = self._gripper
        return message

    def latest_state(self) -> dict:
        with self._lock:
            return self._state_payload_locked()

    def go_named_pose(self, name: str) -> tuple[int, dict]:
        """Call /teleop/go_named_pose off the HTTP request thread.

        Waits on the rclpy future; does not spin. Returns HTTP 200 when the
        robot handled the name (success or failure), 503 if the service is
        missing. Caller must already have validated `name`.
        """
        self.get_logger().info(f"NAMED POSE request name={name}")
        if not self._named_pose.wait_for_service(timeout_sec=NAMED_POSE_WAIT_SEC):
            message = f"{NAMED_POSE_SERVICE} unavailable"
            self.get_logger().warn(message)
            return 503, {"ok": False, "name": name, "message": message}

        request = GoNamedPose.Request()
        request.name = name
        future = self._named_pose.call_async(request)
        deadline = time.monotonic() + NAMED_POSE_CALL_SEC
        while not future.done():
            if not rclpy.ok():
                return 503, {
                    "ok": False,
                    "name": name,
                    "message": "backend shutting down",
                }
            if time.monotonic() >= deadline:
                message = f"timed out waiting for {name}"
                self.get_logger().warn(f"NAMED POSE {message}")
                return 200, {"ok": False, "name": name, "message": message}
            time.sleep(0.05)

        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            self.get_logger().warn(f"NAMED POSE error name={name} {message}")
            return 200, {"ok": False, "name": name, "message": message}
        if result is None:
            message = "empty service response"
            return 200, {"ok": False, "name": name, "message": message}

        payload = {
            "ok": bool(result.success),
            "name": name,
            "message": result.message,
        }
        self.get_logger().info(
            f"NAMED POSE result name={name} ok={payload['ok']} msg={payload['message']}"
        )
        return 200, payload

    def _on_state(self, message: TeleopState) -> None:
        with self._lock:
            changed = (
                message.connection_state != self._state_connection
                or message.watchdog_state != self._state_watchdog
            )
            self._state_connection = message.connection_state
            self._state_watchdog = message.watchdog_state
            self._state_session_id = message.session_id
            self._state_disposition = message.last_disposition
            conn = self._connection if changed else None
            payload = self._state_payload_locked() if conn is not None else None
        if conn is not None and payload is not None:
            self._emit_state(conn, payload)

    def _on_pose(self, message: PoseStamped) -> None:
        position = message.pose.position
        orientation = message.pose.orientation
        with self._lock:
            self._pose = {
                "x": float(position.x),
                "y": float(position.y),
                "z": float(position.z),
                "qx": float(orientation.x),
                "qy": float(orientation.y),
                "qz": float(orientation.z),
                "qw": float(orientation.w),
                "stamp_sec": int(message.header.stamp.sec),
                "stamp_nanosec": int(message.header.stamp.nanosec),
            }

    def _publish_telemetry(self) -> None:
        with self._lock:
            conn = self._connection
            if conn is None:
                return
            payload = self._state_payload_locked()
        self._emit_state(conn, payload)

    def _state_payload_locked(self) -> dict:
        pose = None if self._pose is None else dict(self._pose)
        return {
            "type": "state",
            "connection_state": self._state_connection,
            "watchdog_state": self._state_watchdog,
            "session_id": self._state_session_id or self.session_id,
            "last_disposition": self._state_disposition,
            "pose": pose,
        }

    @staticmethod
    def _emit_state(conn: WsConnection, payload: dict) -> None:
        try:
            conn.send_json(payload)
        except OSError:
            pass


class ConsoleHandler(SimpleHTTPRequestHandler):
    """Static operate-console, health/state, named poses, and /ws/session."""

    protocol_version = "HTTP/1.1"

    def __init__(self, request, client_address, server) -> None:
        super().__init__(
            request, client_address, server, directory=str(WEB_ROOT)
        )

    def log_message(self, fmt: str, *args) -> None:
        path = urlparse(self.path).path
        if path in ("/api/health", "/api/state", "/ws/session"):
            return
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/ws/session":
            self._handle_websocket()
            return
        if path == "/api/health":
            self._send_health()
            return
        if path == "/api/state":
            self._send_state()
            return
        if path in ("/", "/index.html"):
            self._send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/named_pose":
            self._handle_named_pose()
            return
        self.send_error(404, "not found")

    def _handle_websocket(self) -> None:
        node = getattr(self.server, "operator_node", None)
        if node is None or not _node_ready.is_set() or not rclpy.ok():
            self.send_error(503, "backend not ready")
            return
        if not is_websocket_upgrade(self.headers):
            self.send_error(400, "expected WebSocket upgrade")
            return
        key = self.headers.get("Sec-WebSocket-Key", "").strip()
        accept = handshake_accept(key)
        self.close_connection = True
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.connection.settimeout(None)
        except OSError:
            pass
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        try:
            self.wfile.flush()
        except OSError:
            return
        conn = WsConnection(self.connection)
        session_id = node.attach_session(conn)
        try:
            try:
                conn.send_json({"type": "session", "session_id": session_id})
                conn.send_json(node.latest_state())
            except OSError:
                return
            while not conn.closed and rclpy.ok():
                message = conn.read_json()
                if message is None:
                    break
                node.handle_client_message(message)
        finally:
            node.detach_session(conn)

    def _send_health(self) -> None:
        if _node_ready.is_set() and rclpy.ok():
            payload = {"ok": True, "node": NODE_NAME}
            self._send_json(200, payload)
            return
        self._send_json(503, {"ok": False, "node": NODE_NAME})

    def _send_state(self) -> None:
        node = getattr(self.server, "operator_node", None)
        if node is None or not _node_ready.is_set() or not rclpy.ok():
            self._send_json(503, {"ok": False, "node": NODE_NAME})
            return
        self._send_json(200, node.latest_state())

    def _handle_named_pose(self) -> None:
        node = getattr(self.server, "operator_node", None)
        if node is None or not _node_ready.is_set() or not rclpy.ok():
            self._send_json(
                503, {"ok": False, "name": "", "message": "backend not ready"}
            )
            return
        payload, error = self._read_json_object()
        if error is not None:
            self._send_json(400, {"ok": False, "name": "", "message": error})
            return
        raw_name = payload.get("name")
        if not isinstance(raw_name, str):
            self._send_json(
                400, {"ok": False, "name": "", "message": "name must be a string"}
            )
            return
        name = raw_name.strip().lower()
        if name not in NAMED_POSE_NAMES:
            allowed = ", ".join(NAMED_POSES)
            self._send_json(
                400,
                {
                    "ok": False,
                    "name": raw_name,
                    "message": f"unknown pose '{raw_name}'; use {allowed}",
                },
            )
            return
        status, body = node.go_named_pose(name)
        self._send_json(status, body)

    def _read_json_object(self) -> tuple[Optional[dict], Optional[str]]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self.close_connection = True
            return None, "missing Content-Length"
        try:
            length = int(length_header)
        except ValueError:
            self.close_connection = True
            return None, "invalid Content-Length"
        if length < 0:
            self.close_connection = True
            return None, "invalid Content-Length"
        if length > MAX_JSON_BODY:
            self.close_connection = True
            return None, "body too large"
        if length == 0:
            return None, "empty body"
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.close_connection = True
            return None, "truncated body"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "invalid JSON"
        if not isinstance(data, dict):
            return None, "JSON object required"
        return data, None

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path, content_type: str) -> None:
        try:
            data = file_path.read_bytes()
        except OSError:
            self.send_error(404, "console missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def _start_http(node: OperatorBackend) -> ThreadingHTTPServer:
    if not WEB_ROOT.is_dir():
        raise SystemExit(f"web root missing: {WEB_ROOT}")
    try:
        httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), ConsoleHandler)
    except OSError as exc:
        raise SystemExit(
            f"failed to bind HTTP {HTTP_HOST}:{HTTP_PORT}: {exc}"
        ) from exc
    httpd.operator_node = node
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OperatorBackend()
    httpd = _start_http(node)

    def _request_shutdown(*_args) -> None:
        if rclpy.ok():
            rclpy.shutdown()

    signal.signal(signal.SIGTERM, _request_shutdown)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        _node_ready.clear()
        node.shutdown_session()
        httpd.shutdown()
        httpd.server_close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
