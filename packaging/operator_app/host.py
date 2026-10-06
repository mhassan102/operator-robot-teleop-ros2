"""Host operator process. One Python process, no ROS and no Docker.

Launch serves the pages and holds the registry socket. It does not open
the mlink UDP socket and does not arm heartbeats. Start opens that
socket and arms heartbeats only after mlink-op is up. Stop closes them
and leaves this process up. Quit stops mlink-op first, then this process.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from packaging.operator_app.console import ConsoleApp
from packaging.operator_app.helper import Helper
from packaging.operator_app.keys import (
    COMMAND_RATE_HZ,
    COMMANDS,
    FRAME_ID,
    HEARTBEAT_RATE_HZ,
    KEY_BINDINGS,
    direction_allowed,
)
from packaging.operator_app.payload import (
    TYPE_STATE,
    TYPE_TOOL_POSE,
    Command,
    Heartbeat,
    PayloadError,
    State,
    ToolPose,
    decode,
    encode_command,
    encode_heartbeat,
)
from packaging.operator_app.udp import open_mlink_udp
from packaging.operator_app.wsproto import (
    WsConnection,
    handshake_accept,
    is_websocket_upgrade,
)
from packaging.supervisor.robot_commands import Command as PlanCommand

log = logging.getLogger("teleop.operator")

Opener = Callable[[], Any]
Executor = Callable[[PlanCommand, Path], None]
Clock = Callable[[], tuple[int, int]]
_MAX_BODY = 65536


class OperatorHost:
    """Pages, registry, keyboard, and mlink UDP in this process."""

    def __init__(
        self,
        repo_root: str | Path,
        registry_url: str,
        *,
        web_root: str | Path | None = None,
        opener: Opener | None = None,
        executor: Executor | None = None,
        clock: Clock | None = None,
        timers: bool = True,
    ) -> None:
        self.root = Path(repo_root)
        self.registry_url = registry_url
        self.web_root = (
            Path(web_root)
            if web_root is not None
            else self.root / "teleoperation-prototype" / "web"
        )
        self._opener = open_mlink_udp if opener is None else opener
        self._clock = clock
        self._timers = timers
        self._helper = Helper(self.root, executor=executor)
        self._console = ConsoleApp(
            registry_url,
            web_root=self.web_root,
            on_enable_link=self.enable_link,
            on_disable_link=self.disable_link,
            helper_post=self._control,
        )
        self._lock = threading.Lock()
        self._link_lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._socket: Any = None
        self._hold = False
        self._armed = False
        self._pump: threading.Thread | None = None
        self._pump_stop = threading.Event()
        self._connection: WsConnection | None = None
        self.session_id = ""
        self.command_sequence = 0
        self.heartbeat_sequence = 0
        self._motion = COMMANDS["stop"][:6]
        self._gripper = 0.0
        self._label = "stop"
        self._active_key: str | None = None
        self._state_connection = ""
        self._state_watchdog = ""
        self._state_session_id = ""
        self._state_disposition = ""
        self._pose: dict[str, float] | None = None

    @property
    def heartbeat_armed(self) -> bool:
        return self._armed

    @property
    def socket_open(self) -> bool:
        return self._socket is not None

    def quit_event(self) -> threading.Event:
        return self._helper.quit_event()

    def quit_requested(self) -> bool:
        return self._helper.quit_requested()

    def wait_for_quit(self, timeout: float | None = None) -> bool:
        return self._helper.wait_for_quit(timeout)

    def serve(self, host: str = "127.0.0.1", port: int = 8090) -> int:
        """Bind the pages. This does not open mlink and does not arm heartbeats."""
        if host != "127.0.0.1":
            raise ValueError("operator binds 127.0.0.1 only")
        if self._httpd is not None:
            return int(self._httpd.server_address[1])
        self._console.start()
        self._httpd = ThreadingHTTPServer((host, port), _handler(self))
        self._httpd.daemon_threads = True
        self._http_thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="operator-http",
            daemon=True,
        )
        self._http_thread.start()
        return int(self._httpd.server_address[1])

    def shutdown(self) -> None:
        """Stop heartbeats, close the page socket, and release the registry."""
        self.disable_link()
        self._drop_session()
        self._helper._quit.set()
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._http_thread
        self._http_thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._console.close()
        self._helper.shutdown()

    def enable_link(self) -> None:
        """Open UDP and arm heartbeats. Start calls this after mlink-op is up."""
        with self._link_lock:
            if self._socket is not None:
                return
            self._socket = self._opener()
            self._hold = True
            self._armed = True
            if not self._timers:
                return
            self._pump_stop.clear()
            self._pump = threading.Thread(
                target=self._pump_loop,
                name="operator-link",
                daemon=True,
            )
            self._pump.start()

    def disable_link(self) -> None:
        """Stop heartbeats first, then close the UDP socket."""
        with self._link_lock:
            self._hold = False
            self._armed = False
            thread = self._pump
            self._pump = None
            self._pump_stop.set()
            sock = self._socket
            self._socket = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        if sock is not None:
            sock.close()

    def request_quit(self) -> None:
        """Stop mlink-op, then mark this process to exit. No docker."""
        self.disable_link()
        try:
            self._control("/mlink/stop", {})
        finally:
            self._control("/quit", {})

    def apply_key(self, key: str, down: bool) -> None:
        direction = KEY_BINDINGS.get(key)
        if direction is None:
            return
        if not direction_allowed(direction):
            if down:
                log.info("KEY %s ignored (gripper-only)", direction)
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
            log.info("KEY %s down=%s", direction, down)

    def attach_session(self, connection: WsConnection) -> str:
        with self._lock:
            old = self._end_locked()
            session_id = self._begin_locked(connection)
        if old is not None:
            old.close()
        return session_id

    def detach_session(self, connection: WsConnection) -> None:
        with self._lock:
            if self._connection is not connection:
                return
            old = self._end_locked()
        if old is not None:
            old.close()

    def handle_client_message(self, data: dict[str, Any]) -> None:
        if data.get("type") != "key":
            return
        key = data.get("key")
        down = data.get("down")
        if not isinstance(key, str) or not isinstance(down, bool):
            return
        self.apply_key(key, down)

    def latest_state(self) -> dict[str, Any]:
        with self._lock:
            return self._state_payload_locked()

    def named_pose(self, name: str) -> tuple[int, dict[str, Any]]:
        """Gripper-only teleop does not send named poses."""
        message = "named poses disabled (gripper-only)"
        return 400, {"ok": False, "name": name, "message": message}

    def publish_command(self) -> None:
        """Send one command datagram when a session and the link are up."""
        self._publish_command()

    def publish_heartbeat(self) -> None:
        """Send one heartbeat when the link is armed and a session or hold is up."""
        self._publish_heartbeat()

    def _control(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        _status, body = self._helper.handle("POST", path, json.dumps(payload).encode())
        return body

    def _pump_loop(self) -> None:
        next_cmd = time.monotonic()
        next_hb = time.monotonic()
        next_tel = time.monotonic()
        while not self._pump_stop.is_set():
            now = time.monotonic()
            if now >= next_hb:
                next_hb = now + (1.0 / HEARTBEAT_RATE_HZ)
                self._publish_heartbeat()
            if now >= next_cmd:
                next_cmd = now + (1.0 / COMMAND_RATE_HZ)
                self._publish_command()
            if now >= next_tel:
                next_tel = now + 0.1
                self._publish_telemetry()
            self._poll()
            self._pump_stop.wait(0.005)

    def _publish_command(self) -> None:
        with self._lock:
            if self._connection is None or self._socket is None:
                return
            self.command_sequence += 1
            self._emit_command_locked()

    def _publish_heartbeat(self) -> None:
        with self._lock:
            if self._socket is None:
                return
            if self._connection is None and not self._hold:
                return
            self._publish_heartbeat_locked()

    def _publish_heartbeat_locked(self) -> None:
        if self._socket is None:
            return
        self.heartbeat_sequence += 1
        sec, nsec = self._stamp()
        self._socket.send(
            encode_heartbeat(
                Heartbeat(
                    sequence=self.heartbeat_sequence,
                    stamp_sec=sec,
                    stamp_nsec=nsec,
                    session_id=self.session_id,
                )
            )
        )

    def _emit_command_locked(self) -> None:
        if self._socket is None:
            return
        sec, nsec = self._stamp()
        motion = self._motion
        self._socket.send(
            encode_command(
                Command(
                    sequence=self.command_sequence,
                    stamp_sec=sec,
                    stamp_nsec=nsec,
                    lx=float(motion[0]),
                    ly=float(motion[1]),
                    lz=float(motion[2]),
                    ax=float(motion[3]),
                    ay=float(motion[4]),
                    az=float(motion[5]),
                    gripper=float(self._gripper),
                    frame_id=FRAME_ID,
                    session_id=self.session_id,
                )
            )
        )

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

    def _end_locked(self) -> WsConnection | None:
        old = self._connection
        if old is None:
            return None
        self._connection = None
        self._motion = COMMANDS["stop"][:6]
        self._label = "stop"
        self._active_key = None
        self.command_sequence += 1
        self._emit_command_locked()
        self.session_id = ""
        return old

    def _drop_session(self) -> None:
        with self._lock:
            old = self._connection
            self._connection = None
        if old is not None:
            old.close()

    def _stamp(self) -> tuple[int, int]:
        if self._clock is not None:
            return self._clock()
        sec, nsec = divmod(time.time_ns(), 1_000_000_000)
        return int(sec), int(nsec)

    def _poll(self) -> None:
        sock = self._socket
        if sock is None:
            return
        while True:
            data = sock.recv(0.0)
            if not data:
                break
            try:
                kind, msg = decode(data)
            except PayloadError:
                continue
            if kind == TYPE_STATE and isinstance(msg, State):
                self._apply_state(msg)
            elif kind == TYPE_TOOL_POSE and isinstance(msg, ToolPose):
                self._apply_pose(msg)

    def _apply_state(self, msg: State) -> None:
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
            _emit_state(conn, payload)

    def _apply_pose(self, msg: ToolPose) -> None:
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

    def _publish_telemetry(self) -> None:
        with self._lock:
            conn = self._connection
            if conn is None:
                return
            payload = self._state_payload_locked()
        _emit_state(conn, payload)

    def _state_payload_locked(self) -> dict[str, Any]:
        pose = None if self._pose is None else dict(self._pose)
        return {
            "type": "state",
            "connection_state": self._state_connection,
            "watchdog_state": self._state_watchdog,
            "session_id": self._state_session_id or self.session_id,
            "last_disposition": self._state_disposition,
            "pose": pose,
        }


def _emit_state(conn: WsConnection, payload: dict[str, Any]) -> None:
    try:
        conn.send_json(payload)
    except OSError:
        pass


def _handler(host: OperatorHost) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/ws/session":
                self._websocket()
                return
            if path == "/api/health":
                self._json(200, {"ok": True})
                return
            if path == "/api/state":
                self._json(200, host.latest_state())
                return
            if self._dispatch("GET", b""):
                return
            self.send_error(404, "not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            body = self._body()
            if body is None:
                return
            if path == "/api/named_pose":
                self._named_pose(body)
                return
            if self._dispatch("POST", body):
                return
            self.send_error(404, "not found")

        def _dispatch(self, method: str, body: bytes) -> bool:
            result = host._console.dispatch(method, self.path, body)
            if result is None:
                return False
            self.send_response(result.status)
            self.send_header("Content-Type", result.content_type)
            self.send_header("Content-Length", str(len(result.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(result.body)
            return True

        def _body(self) -> bytes | None:
            header = self.headers.get("Content-Length")
            if header is None:
                return b""
            try:
                length = int(header)
            except ValueError:
                self.send_error(400, "bad body")
                return None
            if length < 0 or length > _MAX_BODY:
                self.send_error(400, "bad body")
                return None
            if length == 0:
                return b""
            raw = self.rfile.read(length)
            if len(raw) != length:
                self.send_error(400, "bad body")
                return None
            return raw

        def _named_pose(self, body: bytes) -> None:
            try:
                data = json.loads(body.decode("utf-8")) if body else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
            if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                self._json(400, {"ok": False, "name": "", "message": "name must be a string"})
                return
            status, payload = host.named_pose(data["name"].strip().lower())
            self._json(status, payload)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _websocket(self) -> None:
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
            session_id = host.attach_session(conn)
            try:
                try:
                    conn.send_json({"type": "session", "session_id": session_id})
                    conn.send_json(host.latest_state())
                except OSError:
                    return
                while not conn.closed:
                    message = conn.read_json()
                    if message is None:
                        break
                    host.handle_client_message(message)
            finally:
                host.detach_session(conn)

    return Handler
