"""Register the robot and publish inventory when an operator attaches.

A ``config`` message is checked against the last inventory and answered
with ``config_ok`` or ``bad_config``. ``start`` and ``stop`` use the
supervisor plan. Stop keeps the accepted config. ``operator_detached``
clears it. ``TELEOP_SUPERVISOR_DRY_RUN=1`` sends status and does not
spawn. Without that variable the live path runs the plan.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from packaging.robot_app.config import review_config
from packaging.supervisor.robot_commands import decide_start, status_message
from packaging.supervisor.robot_stop import decide_stop

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
    """What the terminal should do with one server frame."""

    def __init__(
        self,
        status: str | None,
        send_inventory: bool,
        config: dict[str, Any] | None = None,
        action: str | None = None,
    ) -> None:
        self.status = status
        self.send_inventory = send_inventory
        self.config = config
        self.action = action


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
    if kind == "config":
        return Incoming(None, False, config=msg)
    if kind == "start":
        return Incoming(None, False, action="start")
    if kind == "stop":
        return Incoming(None, False, action="stop")
    if kind == "operator_detached":
        return Incoming("operator_detached", False, action="detach")
    return Incoming(None, False)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def dry_run_enabled() -> bool:
    return os.environ.get("TELEOP_SUPERVISOR_DRY_RUN") == "1"


def _status_line(message: Mapping[str, Any]) -> str:
    detail = message.get("detail")
    shown = detail if isinstance(detail, str) and detail else "-"
    phase = message.get("phase")
    phase_text = phase if isinstance(phase, str) else "-"
    return f"Status  phase {phase_text}  detail {shown}"


def _accepted(msg: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "link": msg.get("link"),
        "iface1": msg.get("iface1"),
        "iface2": msg.get("iface2"),
        "arm": msg.get("arm"),
        "video": msg.get("video"),
    }


async def _announce(
    ws: Any,
    message: Mapping[str, Any],
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
) -> None:
    if on_line is not None:
        on_line(_status_line(message))
    phase = message.get("phase")
    if isinstance(phase, str) and phase:
        on_status(phase)
    await ws.send(json.dumps(dict(message)))


async def _run_live(
    ws: Any,
    commands: tuple[Any, ...],
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
    root: Path,
    *,
    announce: bool,
    done: dict[str, Any],
) -> None:
    from packaging.supervisor import robot_exec

    for command in commands:
        if announce and command.label:
            await _announce(
                ws,
                status_message("starting", command.label),
                on_status,
                on_line,
            )
        try:
            await asyncio.to_thread(robot_exec.execute_command, command, root)
        except Exception:
            detail = command.label or "start failed"
            await _announce(
                ws, status_message("error", detail), on_status, on_line
            )
            return
    await _announce(ws, done, on_status, on_line)


async def _on_start(
    ws: Any,
    accepted: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
    root: Path,
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
) -> None:
    decision = decide_start(
        accepted,
        inventory,
        root,
        dry_run=dry_run_enabled(),
    )
    if decision.commands is None:
        if decision.status is not None:
            await _announce(ws, decision.status, on_status, on_line)
        return
    await _run_live(
        ws,
        decision.commands,
        on_status,
        on_line,
        root,
        announce=True,
        done=status_message("ready", ""),
    )


async def _on_stop(
    ws: Any,
    root: Path,
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
) -> None:
    decision = decide_stop(root, dry_run=dry_run_enabled())
    if decision.commands is None:
        if decision.status is not None:
            await _announce(ws, decision.status, on_status, on_line)
        return
    await _run_live(
        ws,
        decision.commands,
        on_status,
        on_line,
        root,
        announce=False,
        done=status_message("stopped", ""),
    )


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
    on_config: Callable[[str], None] | None,
    on_line: Callable[[str], None] | None,
    root: Path,
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
    last_inventory: dict[str, Any] | None = None
    accepted: dict[str, Any] | None = None
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
                last_inventory = payload
                await ws.send(json.dumps(payload))
                if on_inventory is not None:
                    on_inventory(payload)
            if event.config is not None:
                reply, line = review_config(event.config, last_inventory)
                if reply.get("type") == "config_ok":
                    accepted = _accepted(event.config)
                else:
                    accepted = None
                if on_config is not None:
                    on_config(line)
                on_status(
                    "config_ok" if reply.get("type") == "config_ok" else "bad_config"
                )
                await ws.send(json.dumps(reply))
            if event.action == "start":
                await _on_start(
                    ws, accepted, last_inventory, root, on_status, on_line
                )
            elif event.action == "stop":
                await _on_stop(ws, root, on_status, on_line)
            elif event.action == "detach":
                accepted = None
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
    on_config: Callable[[str], None] | None = None,
    on_line: Callable[[str], None] | None = None,
    root: Path | None = None,
) -> None:
    """Stay registered. Send inventory on each ``operator_attached``."""
    repo = root if root is not None else repo_root()
    wake = asyncio.Event()
    state.attach_loop(asyncio.get_running_loop(), wake)
    while not state.stopped():
        try:
            async with connect(url, open_timeout=5) as ws:
                if state.stopped():
                    return
                await _hold(
                    ws,
                    state,
                    wake,
                    inventory_fn,
                    on_status,
                    on_inventory,
                    on_config,
                    on_line,
                    repo,
                )
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
