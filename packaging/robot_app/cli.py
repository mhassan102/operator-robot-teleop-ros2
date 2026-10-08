"""Terminal robot program.

Prints the 9-digit ID and registers with the device token. The token
is not printed, logged, or shown in a prompt. A reviewed config is
printed and answered. No PyQt and no display, so it runs over SSH.
``TELEOP_SUPERVISOR_DRY_RUN=1`` answers start and stop and does not
spawn mlink, Docker, the camera, or the arm. SIGINT, SIGTERM, and
leaving the process run the robot stop plan, then the process exits.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packaging.robot_app.credentials import load_or_create, read_profile, write_profile
from packaging.robot_app.inventory import format_inventory, live_inventory
from packaging.robot_app.session import (
    DEFAULT_REGISTRY,
    RobotState,
    dry_run_enabled,
    repo_root,
    run_robot_session,
)

_HELP = "Type q or Ctrl-C to quit."
_STORED = "Credentials stored in "


def command_for(line: str) -> str | None:
    """``q`` quits. Anything else is ignored."""
    text = line.strip().lower()
    if text in {"q", "quit"}:
        return "quit"
    return None


def id_line(robot_id: str) -> str:
    return f"ID {robot_id}"


def status_line(robot_id: str, status: str) -> str:
    """SSH status text. The device token is not included."""
    return f"{id_line(robot_id)}  status {status}"


def config_path(explicit: str | None) -> Path:
    """``--config``, else ``TELEOP_ROBOT_CONFIG``, else the home default."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("TELEOP_ROBOT_CONFIG", "")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "teleop" / "robot.json"


def open_identity(
    path: Path, mode: str | None, location: str | None
) -> tuple[RobotState, bool]:
    """Load the identity file. Flags override the file and are written back.

    The return is the state and whether this call created the file.
    The token is not printed.
    """
    created = not path.exists()
    robot_id, token = load_or_create(path)
    stored_mode, stored_location = read_profile(path)
    chosen_mode = stored_mode if mode is None else mode
    chosen_location = stored_location if location is None else location
    if (chosen_mode, chosen_location) != (stored_mode, stored_location):
        write_profile(path, chosen_mode, chosen_location)
    state = RobotState(
        robot_id, token, socket.gethostname(), chosen_mode, chosen_location
    )
    return state, created


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


def launch(
    registry_url: str,
    mode: str | None = None,
    location: str | None = None,
    config: str | None = None,
) -> int:
    path = config_path(config)
    state, created = open_identity(path, mode, location)
    lock = threading.Lock()

    def emit(text: str) -> None:
        with lock:
            print(text, flush=True)

    emit(f"Robot  registry {registry_url}")
    emit(id_line(state.robot_id))
    if created:
        emit(_STORED + str(path))

    def on_status(status: str) -> None:
        emit(status_line(state.robot_id, status))

    def on_config(line: str) -> None:
        emit(line)

    def publish() -> dict[str, Any]:
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
            if command_for(line) == "quit":
                state.stop()
                return

    emit(status_line(state.robot_id, "waiting"))
    try:
        emit(format_inventory(live_inventory()))
    except Exception:
        emit("(inventory unavailable)")
    emit(_HELP)

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
    parser.add_argument(
        "--mode",
        choices=("manual", "autonomous"),
        default=None,
        help="presence mode (default manual, or the mode stored in the file)",
    )
    parser.add_argument(
        "--location",
        default=None,
        help="presence location (blank displays as the hostname)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="identity file (default ~/.config/teleop/robot.json)",
    )
    args = parser.parse_args(argv)
    return launch(args.registry, args.mode, args.location, args.config)
