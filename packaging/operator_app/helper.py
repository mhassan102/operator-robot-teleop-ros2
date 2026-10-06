"""Loopback control for mlink start, mlink stop, and Quit.

The process that launches ``teleop-operator`` binds ``127.0.0.1:8091``.
The operator container calls it. The browser does not. Dry-run returns
before any docker or mlink process is created.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from packaging.supervisor.operator_commands import (
    decide_operator,
    default_route_ipv4,
    dry_run_enabled,
    operator_quit_plan,
    operator_stop_plan,
)
from packaging.supervisor.robot_commands import Command

_MAX_BODY = 65536
Executor = Callable[[Command, Path], None]


class Helper:
    """Host side of Start, Stop, and Quit. ``serve`` binds loopback only."""

    def __init__(self, repo_root: str | Path, *, executor: Executor | None = None) -> None:
        self.root = Path(repo_root)
        self._executor = executor
        self._quit = threading.Event()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def serve(self, host: str = "127.0.0.1", port: int = 8091) -> int:
        if host != "127.0.0.1":
            raise ValueError("helper binds 127.0.0.1 only")
        handler = _handler(self)
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="operator-helper",
            daemon=True,
        )
        self._thread.start()
        bound = self._httpd.server_address[1]
        return int(bound)

    def quit_event(self) -> threading.Event:
        return self._quit

    def quit_requested(self) -> bool:
        return self._quit.is_set()

    def wait_for_quit(self, timeout: float | None = None) -> bool:
        return self._quit.wait(timeout)

    def shutdown(self) -> None:
        self._quit.set()
        httpd = self._httpd
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            self._httpd = None
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def handle(self, method: str, target: str, body: bytes) -> tuple[int, dict[str, Any]]:
        path = urlparse(target).path
        if method == "GET" and path == "/health":
            return 200, {"ok": True}
        if method != "POST":
            return 404, {"ok": False, "detail": "not found"}
        payload = _object(body)
        if path == "/mlink/start":
            return 200, self._start(payload)
        if path == "/mlink/stop":
            return 200, self._stop_mlink()
        if path == "/quit":
            return 200, self._quit_request()
        return 404, {"ok": False, "detail": "not found"}

    def _start(self, payload: dict[str, Any]) -> dict[str, Any]:
        if dry_run_enabled():
            return {"ok": False, "detail": "dry-run"}
        config = payload.get("config")
        inventory = payload.get("inventory")
        phase = payload.get("phase")
        decision = decide_operator(
            config if isinstance(config, dict) else None,
            inventory if isinstance(inventory, dict) else None,
            self.root,
            phase if isinstance(phase, str) else "",
            dry_run=False,
            bind_ip=default_route_ipv4(),
        )
        if decision.commands is None:
            return {"ok": False, "detail": decision.detail or "not started"}
        for command in decision.commands:
            if any(part.endswith("start_operator_mlink.sh") for part in command.argv):
                return {"ok": False, "detail": "refusing operator container start"}
            self._exec(command)
        return {"ok": True, "console_url": decision.console_url}

    def _stop_mlink(self) -> dict[str, Any]:
        """mlink-op only. This does not compose down."""
        if dry_run_enabled():
            return {"ok": True, "dry_run": True}
        for command in operator_stop_plan(self.root):
            self._exec(command)
        return {"ok": True}

    def _quit_request(self) -> dict[str, Any]:
        threading.Thread(target=self._finish_quit, name="operator-quit", daemon=True).start()
        return {"ok": True}

    def _finish_quit(self) -> None:
        try:
            if not dry_run_enabled():
                for command in operator_quit_plan(self.root):
                    self._exec(command)
        finally:
            self._quit.set()

    def _exec(self, command: Command) -> None:
        if dry_run_enabled():
            raise RuntimeError("TELEOP_SUPERVISOR_DRY_RUN=1 must not exec")
        if self._executor is not None:
            self._executor(command, self.root)
            return
        from packaging.supervisor import operator_exec

        operator_exec.execute_command(command, self.root)


def _object(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _handler(helper: Helper) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            self._reply(*helper.handle("GET", self.path, b""))

        def do_POST(self) -> None:
            length = _length(self.headers.get("Content-Length"))
            if length is None or length > _MAX_BODY:
                self._reply(400, {"ok": False, "detail": "bad body"})
                return
            body = self.rfile.read(length) if length else b""
            self._reply(*helper.handle("POST", self.path, body))

        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def _length(header: str | None) -> int | None:
    if header is None:
        return 0
    try:
        value = int(header)
    except ValueError:
        return None
    if value < 0:
        return None
    return value
