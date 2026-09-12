"""Operator backend: ROS 2 node plus localhost HTTP console (F5.1).

F5.2 will add a WebSocket session that publishes /teleop/command and
/teleop/heartbeat. This file must not open a socket or those topics yet.
"""

from __future__ import annotations

import json
import os
import signal
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import rclpy
from rclpy.node import Node

from teleop_demo.parameters import declare_teleop_parameters

WEB_ROOT = Path(os.environ.get("TELEOP_WEB_ROOT", "/teleop/web"))
HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8090
NODE_NAME = "operator_backend"

_node_ready = threading.Event()


class OperatorBackend(Node):
    def __init__(self) -> None:
        super().__init__(NODE_NAME)
        declare_teleop_parameters(self)
        # F5.2: create command/heartbeat publishers only while a WS session is open.
        _node_ready.set()
        self.get_logger().info(
            f"HTTP {HTTP_HOST}:{HTTP_PORT} serving {WEB_ROOT}"
        )


class ConsoleHandler(SimpleHTTPRequestHandler):
    """Static operate-console plus /api/health. F5.2 can add /ws/session here."""

    def __init__(self, request, client_address, server) -> None:
        super().__init__(
            request, client_address, server, directory=str(WEB_ROOT)
        )

    def log_message(self, fmt: str, *args) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            return
        super().log_message(fmt, *args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_health()
            return
        if path in ("/", "/index.html"):
            self._send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
            return
        super().do_GET()

    def _send_health(self) -> None:
        if _node_ready.is_set() and rclpy.ok():
            payload = {"ok": True, "node": NODE_NAME}
            self._send_json(200, payload)
            return
        self._send_json(503, {"ok": False, "node": NODE_NAME})

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


def _start_http() -> ThreadingHTTPServer:
    if not WEB_ROOT.is_dir():
        raise SystemExit(f"web root missing: {WEB_ROOT}")
    try:
        httpd = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), ConsoleHandler)
    except OSError as exc:
        raise SystemExit(
            f"failed to bind HTTP {HTTP_HOST}:{HTTP_PORT}: {exc}"
        ) from exc
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main(args=None) -> None:
    rclpy.init(args=args)
    httpd = _start_http()
    node = OperatorBackend()

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
        httpd.shutdown()
        httpd.server_close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
