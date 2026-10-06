"""Operator backend: ROS 2 node plus localhost HTTP/WebSocket console.

TELEOP_UI_ONLY=1 serves login and config and does not open the mlink
socket or arm the heartbeat timer. Start enables both. Reloading the
page does not stop heartbeats. Closing the desktop window is Quit:
heartbeats stop and the operator container is removed.
Command publish still requires an open /ws/session. Telemetry from
/teleop/state and /teleop/tool_pose is display-only. Named poses use
/teleop/go_named_pose locally, or the compact UDP mux when mlink is
open. mlink stays a separate host process.
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
from teleop_demo.arm_mode import gripper_only_from_env, key_direction_allowed
from teleop_demo.commands import COMMANDS, KEY_BINDINGS, fill_twist
from teleop_demo.parameters import declare_teleop_parameters
from teleop_demo.qos import command_qos
from teleop_demo.wsproto import (
    WsConnection,
    handshake_accept,
    is_websocket_upgrade,
)

from teleop_demo.mlink_payload import (
    TYPE_NAMED_POSE_REP,
    TYPE_STATE,
    TYPE_TOOL_POSE,
    Command,
    Heartbeat,
    NamedPoseRep,
    NamedPoseReq,
    PayloadError,
    State,
    ToolPose,
    decode,
    encode_command,
    encode_heartbeat,
    encode_named_pose_req,
)
from teleop_demo.link_gate import LinkGate
from teleop_demo.mlink_udp import open_from_env

WEB_ROOT = Path(os.environ.get("TELEOP_WEB_ROOT", "/teleop/web"))
HTTP_HOST = os.environ.get("TELEOP_HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("TELEOP_HTTP_PORT", "8090"))
MLINK_DEFAULT_TX = "127.0.0.1:5501"
MLINK_DEFAULT_RX = "127.0.0.1:5502"
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
        self._mlink = None
        self._pose_req = 0
        self._pose_wait: dict[int, tuple[threading.Event, Optional[dict]]] = {}
        self._gripper_only = gripper_only_from_env(os.environ.get("TELEOP_GRIPPER_ONLY"))
        if self._gripper_only:
            self.get_logger().info("GRIPPER ONLY: cartesian keys and named poses ignored")
        self._link_gate = LinkGate()
        self._hold_heartbeat = False
        self._link_job: Optional[tuple[str, threading.Event]] = None
        self._heartbeat_timer = None
        self._poll_timer = None
        self._console = None
        self._link_gate.apply_startup(
            os.environ,
            self._open_mlink_socket,
            lambda: self._arm_heartbeat(False),
            self._arm_poll,
        )
        command_rate = float(self.get_parameter("command_rate_hz").value)
        telemetry_rate = float(self.get_parameter("telemetry_rate_hz").value)
        self.create_timer(1.0 / command_rate, self._publish_command)
        self.create_timer(1.0 / telemetry_rate, self._publish_telemetry)
        self._start_console()
        _node_ready.set()
        self.get_logger().info(
            f"HTTP {HTTP_HOST}:{HTTP_PORT} serving {WEB_ROOT}"
        )

    def _open_mlink_socket(self):
        self._mlink = open_from_env(
            default_tx=MLINK_DEFAULT_TX, default_rx=MLINK_DEFAULT_RX
        )
        self.get_logger().info(
            f"MLINK control tx={self._mlink.tx_addr} rx={self._mlink.rx_addr}"
        )
        return self._mlink

    def _arm_heartbeat(self, hold: bool):
        self._hold_heartbeat = hold
        if self._heartbeat_timer is None:
            rate = float(self.get_parameter("heartbeat_rate_hz").value)
            self._heartbeat_timer = self.create_timer(1.0 / rate, self._publish_heartbeat)
        return self._heartbeat_timer

    def _arm_poll(self):
        if self._poll_timer is None:
            self._poll_timer = self.create_timer(0.005, self._poll_mlink)
        return self._poll_timer

    def _enable_link(self) -> None:
        self._link_gate.enable(
            self._open_mlink_socket,
            lambda: self._arm_heartbeat(True),
            self._arm_poll,
        )

    def _disable_link(self) -> None:
        self._hold_heartbeat = False

        def _cancel(timer) -> None:
            timer.cancel()

        def _close(sock) -> None:
            try:
                sock.close()
            finally:
                if self._mlink is sock:
                    self._mlink = None

        self._link_gate.disable(_close, _cancel)
        self._heartbeat_timer = None
        self._poll_timer = None

    def _service_link_job(self) -> None:
        with self._lock:
            job = self._link_job
            self._link_job = None
        if job is None:
            return
        kind, event = job
        try:
            if kind == "enable":
                self._enable_link()
            else:
                self._disable_link()
        finally:
            event.set()

    def _request_link(self, kind: str) -> None:
        event = threading.Event()
        with self._lock:
            self._link_job = (kind, event)
        if not event.wait(2.0):
            self.get_logger().error(f"link {kind} was not applied")

    def request_enable_link(self) -> None:
        """Open UDP and arm heartbeats. Called from the console thread."""
        self._request_link("enable")

    def request_disable_link(self) -> None:
        """Stop heartbeats, then close UDP. Called from the console thread."""
        self._request_link("disable")

    def _start_console(self) -> None:
        """Login and config when the packaging tree is on PYTHONPATH."""
        try:
            from packaging.operator_app.console import ConsoleApp
        except ImportError:
            self.get_logger().info("operator console pages are not mounted")
            return
        registry = os.environ.get("TELEOP_REGISTRY", "ws://127.0.0.1:8765")
        helper = os.environ.get("TELEOP_HELPER_URL", "http://127.0.0.1:8091")
        self._console = ConsoleApp(
            registry,
            helper_url=helper,
            web_root=WEB_ROOT,
            on_enable_link=self.request_enable_link,
            on_disable_link=self.request_disable_link,
        )
        self._console.start()

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
        if not key_direction_allowed(direction, self._gripper_only):
            if down:
                self.get_logger().info(f"KEY {direction} ignored (gripper-only)")
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
        self._service_link_job()
        with self._lock:
            if self._connection is None:
                return
            self.command_sequence += 1
            self._emit_command_locked()

    def _publish_heartbeat(self) -> None:
        with self._lock:
            if self._connection is None and not self._hold_heartbeat:
                return
            self._publish_heartbeat_locked()

    def _publish_heartbeat_locked(self) -> None:
        self.heartbeat_sequence += 1
        message = TeleopHeartbeat()
        message.sequence = self.heartbeat_sequence
        message.stamp = self.get_clock().now().to_msg()
        message.session_id = self.session_id
        if self._mlink is None:
            self.heartbeat_pub.publish(message)
            return
        self._mlink.send(
            encode_heartbeat(
                Heartbeat(
                    sequence=int(message.sequence),
                    stamp_sec=int(message.stamp.sec),
                    stamp_nsec=int(message.stamp.nanosec),
                    session_id=message.session_id,
                )
            )
        )

    def _publish_stop_locked(self) -> None:
        self.command_sequence += 1
        self._emit_command_locked()

    def _emit_command_locked(self) -> None:
        message = self._command_message_locked()
        if self._mlink is None:
            self.command_pub.publish(message)
            return
        twist = message.twist
        self._mlink.send(
            encode_command(
                Command(
                    sequence=int(message.sequence),
                    stamp_sec=int(message.stamp.sec),
                    stamp_nsec=int(message.stamp.nanosec),
                    lx=float(twist.linear.x),
                    ly=float(twist.linear.y),
                    lz=float(twist.linear.z),
                    ax=float(twist.angular.x),
                    ay=float(twist.angular.y),
                    az=float(twist.angular.z),
                    gripper=float(message.gripper),
                    frame_id=message.frame_id,
                    session_id=message.session_id,
                )
            )
        )

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
        if self._gripper_only:
            message = "named poses disabled (gripper-only)"
            self.get_logger().warn(message)
            return 400, {"ok": False, "name": name, "message": message}
        if self._mlink is not None:
            return self._mlink_named_pose(name)
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

    def _mlink_named_pose(self, name: str) -> tuple[int, dict]:
        if self._mlink is None:
            return 503, {"ok": False, "name": name, "message": "mlink is not enabled"}
        event = threading.Event()
        with self._lock:
            self._pose_req += 1
            req_id = self._pose_req
            self._pose_wait[req_id] = (event, None)
        try:
            self._mlink.send(encode_named_pose_req(NamedPoseReq(req_id, name)))
        except OSError as exc:
            with self._lock:
                self._pose_wait.pop(req_id, None)
            return 503, {"ok": False, "name": name, "message": str(exc)}
        if not event.wait(NAMED_POSE_CALL_SEC):
            with self._lock:
                self._pose_wait.pop(req_id, None)
            message = f"timed out waiting for {name}"
            self.get_logger().warn(f"NAMED POSE {message}")
            return 200, {"ok": False, "name": name, "message": message}
        with self._lock:
            _event, payload = self._pose_wait.pop(req_id, (event, None))
        if payload is None:
            return 200, {"ok": False, "name": name, "message": "empty mlink reply"}
        payload = dict(payload)
        payload["name"] = name
        return 200, payload

    def _poll_mlink(self) -> None:
        if self._mlink is None:
            return
        while True:
            data = self._mlink.recv(timeout=0.0)
            if not data:
                break
            try:
                kind, msg = decode(data)
            except PayloadError as exc:
                self.get_logger().warn(f"drop bad control datagram: {exc}")
                continue
            if kind == TYPE_STATE and isinstance(msg, State):
                self._apply_mlink_state(msg)
            elif kind == TYPE_TOOL_POSE and isinstance(msg, ToolPose):
                self._apply_mlink_pose(msg)
            elif kind == TYPE_NAMED_POSE_REP and isinstance(msg, NamedPoseRep):
                self._apply_named_rep(msg)

    def _apply_mlink_state(self, msg: State) -> None:
        with self._lock:
            changed = (
                msg.connection_state != self._state_connection
                or msg.watchdog_state != self._state_watchdog
            )
            self._state_connection = msg.connection_state
            self._state_watchdog = msg.watchdog_state
            self._state_session_id = msg.session_id
            self._state_disposition = msg.last_disposition
            conn = self._connection if changed else None
            payload = self._state_payload_locked() if conn is not None else None
        if conn is not None and payload is not None:
            self._emit_state(conn, payload)

    def _apply_mlink_pose(self, msg: ToolPose) -> None:
        with self._lock:
            self._pose = {
                "x": msg.x,
                "y": msg.y,
                "z": msg.z,
                "qx": msg.qx,
                "qy": msg.qy,
                "qz": msg.qz,
                "qw": msg.qw,
                "stamp_sec": msg.stamp_sec,
                "stamp_nanosec": msg.stamp_nsec,
            }

    def _apply_named_rep(self, msg: NamedPoseRep) -> None:
        with self._lock:
            pending = self._pose_wait.get(msg.req_id)
            if pending is None:
                return
            event, _old = pending
            payload = {
                "ok": bool(msg.success),
                "name": "",
                "message": msg.message,
            }
            self._pose_wait[msg.req_id] = (event, payload)
        event.set()

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
        if path in (
            "/api/health",
            "/api/state",
            "/api/session",
            "/api/login",
            "/api/review",
            "/api/start",
            "/api/stop",
            "/api/logout",
            "/api/quit",
            "/ws/session",
        ):
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
        if self._dispatch("GET", b""):
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
        body = self._read_body()
        if body is None:
            return
        if self._dispatch("POST", body):
            return
        self.send_error(404, "not found")

    def _console_app(self):
        node = getattr(self.server, "operator_node", None)
        if node is None:
            return None
        return getattr(node, "_console", None)

    def _dispatch(self, method: str, body: bytes) -> bool:
        console = self._console_app()
        if console is None:
            return False
        result = console.dispatch(method, self.path, body)
        if result is None:
            return False
        self._send_result(result)
        return True

    def _read_body(self) -> Optional[bytes]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return b""
        try:
            length = int(length_header)
        except ValueError:
            self.send_error(400, "bad body")
            return None
        if length < 0 or length > 65536:
            self.send_error(400, "bad body")
            return None
        if length == 0:
            return b""
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.send_error(400, "bad body")
            return None
        return raw

    def _send_result(self, result) -> None:
        self.send_response(result.status)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Content-Length", str(len(result.body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(result.body)

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
        console = getattr(node, "_console", None)
        if console is not None:
            console.close()
        if node._mlink is not None:
            node._mlink.close()
        httpd.shutdown()
        httpd.server_close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
