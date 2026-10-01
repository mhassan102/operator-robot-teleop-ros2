"""Terminal robot program.

Prints the generated ID and password, registers with the signalling
server, and prints inventory when an operator attaches. A reviewed
config is printed and answered. No PyQt and no display, so it runs
over SSH. ``TELEOP_SUPERVISOR_DRY_RUN=1`` answers start and stop and
does not spawn mlink, Docker, the camera, or the arm. SIGINT, SIGTERM,
and leaving the process run the robot stop plan, then the process exits.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import socket
import sys
import threading
from pathlib import Path
from typing import Any

from packaging.robot_app.credentials import new_credentials, new_password
from packaging.robot_app.inventory import format_inventory, live_inventory
from packaging.robot_app.session import (
    DEFAULT_REGISTRY,
    RobotState,
    dry_run_enabled,
    repo_root,
    run_robot_session,
)


def command_for(line: str) -> str | None:
    """``n`` issues a new password. ``q`` quits. Anything else is ignored."""
    text = line.strip().lower()
    if text == "n":
        return "new-password"
    if text in {"q", "quit"}:
        return "quit"
    return None


def install_exit_signals(state: RobotState) -> Any:
    """SIGINT and SIGTERM ask the session to leave. SIGINT still interrupts."""

    def _handle(signum: int, _frame: Any) -> None:
        state.stop()
        if signum == signal.SIGINT:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    return _handle


def run_robot_stop(root: Path | None = None) -> None:
    """Run the robot stop plan. Dry-run returns before any process is created.

    A failed step does not skip the later ones, so the camera still stops
    when the container step fails.
    """
    from packaging.supervisor.robot_stop import decide_stop

    repo = root if root is not None else repo_root()
    decision = decide_stop(repo, dry_run=dry_run_enabled())
    if decision.commands is None:
        return
    from packaging.supervisor import robot_exec

    for command in decision.commands:
        try:
            robot_exec.execute_command(command, repo)
        except Exception as exc:
            label = command.label or "robot stop"
            print(f"{label} failed: {exc}", file=sys.stderr)


def _summary(state: RobotState, status: str) -> str:
    password, _revision = state.snapshot()
    return f"ID {state.robot_id}  Password {password}  status {status}"


def launch(registry_url: str) -> int:
    robot_id, password = new_credentials()
    state = RobotState(robot_id, password, socket.gethostname())
    lock = threading.Lock()

    def emit(text: str) -> None:
        with lock:
            print(text, flush=True)

    def on_status(status: str) -> None:
        emit(_summary(state, status))

    def on_config(line: str) -> None:
        emit(line)

    def publish() -> dict:
        inv = live_inventory()
        emit(format_inventory(inv))
        return inv

    def read_commands() -> None:
        while not state.stopped():
            try:
                line = sys.stdin.readline()
            except Exception:
                state.stop()
                return
            if line == "":
                state.stop()
                return
            action = command_for(line)
            if action == "quit":
                state.stop()
                return
            if action == "new-password":
                fresh = new_password()
                state.set_password(fresh)
                emit(f"Password {fresh}")

    emit(f"Robot  registry {registry_url}")
    emit(_summary(state, "waiting"))
    try:
        emit(format_inventory(live_inventory()))
    except Exception:
        emit("(inventory unavailable)")
    emit("Type n and press Enter for a new password. Type q or Ctrl-C to quit.")

    reader = threading.Thread(target=read_commands, name="robot-stdin", daemon=True)
    reader.start()
    install_exit_signals(state)
    try:
        asyncio.run(
            run_robot_session(
                registry_url,
                state,
                publish,
                on_status,
                on_config=on_config,
                on_line=emit,
            )
        )
    except KeyboardInterrupt:
        state.stop()
    finally:
        run_robot_stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Robot terminal")
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"registry WebSocket URL (default {DEFAULT_REGISTRY})",
    )
    args = parser.parse_args(argv)
    return launch(args.registry)
