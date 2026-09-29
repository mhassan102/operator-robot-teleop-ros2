"""Register the robot and publish inventory when an operator attaches.

This module does not start mlink, Docker, the camera, or the arm.
Messages other than ``registered``, ``operator_attached``, and
``error`` are ignored.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

DEFAULT_REGISTRY = "ws://127.0.0.1:8765"
_CLOSED = object()


class RobotState:
    """ID stays fixed. A new password bumps the revision and re-registers."""

    def __init__(self, robot_id: str, password: str, hostname: str) -> None:
        self.robot_id = robot_id
        self.hostname = hostname
        self._password = password
        self._revision = 1
        self._stopped = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None

    def attach_loop(
        self, loop: asyncio.AbstractEventLoop, wake: asyncio.Event
    ) -> None:
        self._loop = loop
        self._wake = wake

    def snapshot(self) -> tuple[str, int]:
        with self._lock:
            return self._password, self._revision

    def set_password(self, password: str) -> None:
        with self._lock:
            self._password = password
            self._revision += 1
        self._wake_threadsafe()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
        self._wake_threadsafe()

    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def _wake_threadsafe(self) -> None:
        loop = self._loop
        wake = self._wake
        if loop is None or wake is None:
            return
        loop.call_soon_threadsafe(wake.set)


def register_message(robot_id: str, password: str, hostname: str) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "register",
        "robot_id": robot_id,
        "password": password,
        "hostname": hostname,
    }


class Incoming:
    """What the window should do with one server frame."""

    def __init__(self, status: str | None, send_inventory: bool) -> None:
        self.status = status
        self.send_inventory = send_inventory


def interpret(raw: str | bytes) -> Incoming:
    """Map a server frame to a status token. The password is never a status."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return Incoming("bad_json", False)
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return Incoming("bad_json", False)
    if not isinstance(msg, dict) or msg.get("v") != 1:
        return Incoming(None, False)
    kind = msg.get("type")
    if kind == "registered":
        return Incoming("registered", False)
    if kind == "operator_attached":
        return Incoming("operator_attached", True)
    if kind == "error":
        code = msg.get("code")
        if isinstance(code, str) and code:
            return Incoming(code, False)
        return Incoming("error", False)
    return Incoming(None, False)


async def _next(
    incoming: asyncio.Queue[Any], wake: asyncio.Event
) -> tuple[Any, bool]:
    get_task = asyncio.create_task(incoming.get())
    wake_task = asyncio.create_task(wake.wait())
    done, pending = await asyncio.wait(
        {get_task, wake_task},
        timeout=0.25,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in pending:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    woke = wake_task in done
    if woke:
        wake.clear()
    if get_task in done:
        return get_task.result(), woke
    return None, woke


async def _hold(
    ws: Any,
    state: RobotState,
    wake: asyncio.Event,
    inventory_fn: Callable[[], dict[str, Any]],
    on_status: Callable[[str], None],
    on_inventory: Callable[[dict[str, Any]], None] | None,
) -> None:
    incoming: asyncio.Queue[Any] = asyncio.Queue()

    async def reader() -> None:
        try:
            async for raw in ws:
                await incoming.put(raw)
        except ConnectionClosed:
            pass
        finally:
            incoming.put_nowait(_CLOSED)

    reader_task = asyncio.create_task(reader())
    sent_rev = -1
    try:
        while not state.stopped():
            password, rev = state.snapshot()
            if rev != sent_rev:
                await ws.send(
                    json.dumps(
                        register_message(state.robot_id, password, state.hostname)
                    )
                )
                sent_rev = rev
            raw, _woke = await _next(incoming, wake)
            if raw is _CLOSED:
                return
            if raw is None:
                continue
            event = interpret(raw)
            if event.status:
                on_status(event.status)
            if event.send_inventory:
                payload = inventory_fn()
                await ws.send(json.dumps(payload))
                if on_inventory is not None:
                    on_inventory(payload)
    finally:
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reader_task


async def run_robot_session(
    url: str,
    state: RobotState,
    inventory_fn: Callable[[], dict[str, Any]],
    on_status: Callable[[str], None],
    on_inventory: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Stay registered. Send inventory on each ``operator_attached``."""
    wake = asyncio.Event()
    state.attach_loop(asyncio.get_running_loop(), wake)
    while not state.stopped():
        try:
            async with connect(url, open_timeout=5) as ws:
                if state.stopped():
                    return
                await _hold(ws, state, wake, inventory_fn, on_status, on_inventory)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        if state.stopped():
            return
        on_status("offline")
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), 1.0)
        except asyncio.TimeoutError:
            pass
        wake.clear()
