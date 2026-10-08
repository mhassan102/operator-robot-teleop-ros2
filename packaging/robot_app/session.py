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
PRESENCE_INTERVAL_S = 10
_CLOSED = object()


class RobotState:
    """ID and device token stay fixed. The token is not printed."""

    def __init__(
        self,
        robot_id: str,
        token: str,
        hostname: str,
        mode: str = "manual",
        location: str = "",
    ) -> None:
        self.robot_id = robot_id
        self.hostname = hostname
        self.mode = mode
        self.location = location
        self._token = token
        self._phase = "idle"
        self._stopped = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None

    def __repr__(self) -> str:
        return (
            f"RobotState({self.robot_id!r}, hostname={self.hostname!r}, "
            f"mode={self.mode!r})"
        )

    def attach_loop(
        self, loop: asyncio.AbstractEventLoop, wake: asyncio.Event
    ) -> None:
        self._loop = loop
        self._wake = wake

    def registration(self) -> dict[str, Any]:
        with self._lock:
            token = self._token
        return register_message(
            self.robot_id, token, self.hostname, self.mode, self.location
        )

    def presence_tuple(self) -> tuple[str, str, str]:
        with self._lock:
            return self.mode, self.location, self._phase

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase
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


def register_message(
    robot_id: str,
    token: str,
    hostname: str,
    mode: str = "manual",
    location: str = "",
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "register",
        "robot_id": robot_id,
        "token": token,
        "hostname": hostname,
        "mode": mode,
        "location": location,
    }


def presence_message(
    mode: str, location: str, hostname: str, phase: str
) -> dict[str, Any]:
    return {
        "v": 1,
        "type": "presence",
        "mode": mode,
        "location": location,
        "hostname": hostname,
        "phase": phase,
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


async def _emit_presence(
    ws: Any, state: RobotState, clock: dict[str, Any]
) -> None:
    """Send presence after ``registered``, on phase change, and every 10 s."""
    if not clock.get("registered"):
        return
    mode, location, phase = state.presence_tuple()
    now = asyncio.get_running_loop().time()
    same = phase == clock.get("phase")
    if same and now - float(clock.get("sent", 0.0)) < PRESENCE_INTERVAL_S:
        return
    await ws.send(json.dumps(presence_message(mode, location, state.hostname, phase)))
    clock["phase"] = phase
    clock["sent"] = now


async def _announce(
    ws: Any,
    message: Mapping[str, Any],
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
    state: RobotState | None = None,
    clock: dict[str, Any] | None = None,
) -> None:
    if on_line is not None:
        on_line(_status_line(message))
    phase = message.get("phase")
    if isinstance(phase, str) and phase:
        on_status(phase)
        if state is not None:
            state.set_phase(phase)
    await ws.send(json.dumps(dict(message)))
    if state is not None and clock is not None:
        await _emit_presence(ws, state, clock)


async def _run_live(
    ws: Any,
    commands: tuple[Any, ...],
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
    root: Path,
    *,
    announce: bool,
    done: dict[str, Any],
    state: RobotState | None = None,
    clock: dict[str, Any] | None = None,
) -> None:
    from packaging.supervisor import robot_exec

    for command in commands:
        if announce and command.label:
            await _announce(
                ws,
                status_message("starting", command.label),
                on_status,
                on_line,
                state,
                clock,
            )
        try:
            await asyncio.to_thread(robot_exec.execute_command, command, root)
        except Exception:
            detail = command.label or "start failed"
            await _announce(
                ws, status_message("error", detail), on_status, on_line, state, clock
            )
            return
    await _announce(ws, done, on_status, on_line, state, clock)


async def _on_start(
    ws: Any,
    accepted: Mapping[str, Any] | None,
    inventory: Mapping[str, Any] | None,
    root: Path,
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
    state: RobotState | None = None,
    clock: dict[str, Any] | None = None,
) -> None:
    decision = decide_start(
        accepted,
        inventory,
        root,
        dry_run=dry_run_enabled(),
    )
    if decision.commands is None:
        if decision.status is not None:
            await _announce(ws, decision.status, on_status, on_line, state, clock)
        return
    await _run_live(
        ws,
        decision.commands,
        on_status,
        on_line,
        root,
        announce=True,
        done=status_message("ready", ""),
        state=state,
        clock=clock,
    )


async def _on_stop(
    ws: Any,
    root: Path,
    on_status: Callable[[str], None],
    on_line: Callable[[str], None] | None,
    state: RobotState | None = None,
    clock: dict[str, Any] | None = None,
) -> None:
    decision = decide_stop(root, dry_run=dry_run_enabled())
    if decision.commands is None:
        if decision.status is not None:
            await _announce(ws, decision.status, on_status, on_line, state, clock)
        return
    await _run_live(
        ws,
        decision.commands,
        on_status,
        on_line,
        root,
        announce=False,
        done=status_message("stopped", ""),
        state=state,
        clock=clock,
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
    last_inventory: dict[str, Any] | None = None
    accepted: dict[str, Any] | None = None
    clock: dict[str, Any] = {"registered": False, "sent": 0.0, "phase": ""}
    try:
        await ws.send(json.dumps(state.registration()))
        while not state.stopped():
            await _emit_presence(ws, state, clock)
            raw, _woke = await _next(incoming, wake)
            if raw is _CLOSED:
                return
            if raw is None:
                continue
            event = interpret(raw)
            if event.status and event.action != "detach":
                on_status(event.status)
            if event.status == "registered":
                clock["registered"] = True
                clock["phase"] = ""
                clock["sent"] = 0.0
                await _emit_presence(ws, state, clock)
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
                    ws,
                    accepted,
                    last_inventory,
                    root,
                    on_status,
                    on_line,
                    state,
                    clock,
                )
            elif event.action == "stop":
                await _on_stop(ws, root, on_status, on_line, state, clock)
            elif event.action == "detach":
                accepted = None
                state.set_phase("idle")
                on_status("operator_detached")
                await _emit_presence(ws, state, clock)
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
