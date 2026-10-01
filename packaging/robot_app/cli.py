"""Terminal robot program.

Prints the 9-digit ID, reads a password with no echo, and registers
with the signalling server. The password is not printed, logged, or
written to a file. A reviewed config is printed and answered. No PyQt
and no display, so it runs over SSH. ``TELEOP_SUPERVISOR_DRY_RUN=1``
answers start and stop and does not spawn mlink, Docker, the camera,
or the arm. SIGINT, SIGTERM, and leaving the process run the robot
stop plan, then the process exits.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import signal
import socket
import sys
import termios
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packaging.robot_app.credentials import new_id
from packaging.robot_app.inventory import format_inventory, live_inventory
from packaging.robot_app.session import (
    DEFAULT_REGISTRY,
    RobotState,
    dry_run_enabled,
    repo_root,
    run_robot_session,
)

_NEW_PROMPT = "New password: "
_RETYPE_PROMPT = "Retype new password: "
_EMPTY = "No password supplied."
_MISMATCH = "Sorry, passwords do not match."
_UPDATED = "Password updated."
_HELP = (
    "Type n and press Enter to set a new password. The ID stays the same. "
    "Type q or Ctrl-C to quit."
)

# True only while getpass is waiting. An interrupt in that window drops
# unread terminal input so a half-typed password is not echoed.
_prompt_active = False


def command_for(line: str) -> str | None:
    """``n`` asks for a new password. ``q`` quits. Anything else is ignored."""
    text = line.strip().lower()
    if text == "n":
        return "new-password"
    if text in {"q", "quit"}:
        return "quit"
    return None


def id_line(robot_id: str) -> str:
    return f"ID {robot_id}"


def status_line(robot_id: str, status: str) -> str:
    """SSH status text. The password is not included."""
    return f"{id_line(robot_id)}  status {status}"


def read_confirmed_password(
    read_secret: Callable[[str], str],
    tell: Callable[[str], None],
) -> str:
    """Ask twice until both entries match and neither is empty.

    ``read_secret`` must not echo. ``tell`` receives fixed sentences only.
    The return value is the password. Callers must not print it.
    """
    while True:
        first = read_secret(_NEW_PROMPT)
        second = read_secret(_RETYPE_PROMPT)
        if first == "" or second == "":
            tell(_EMPTY)
            continue
        if first != second:
            tell(_MISMATCH)
            continue
        return first


def prompt_password(
    read_secret: Callable[[str], str],
    tell: Callable[[str], None],
) -> str | None:
    """Return a confirmed password, or None if the terminal closes.

    On success ``tell`` is called with a fixed sentence, not the password.
    """
    try:
        password = read_confirmed_password(read_secret, tell)
    except (EOFError, KeyboardInterrupt):
        return None
    tell(_UPDATED)
    return password


def apply_new_password(
    state: RobotState,
    read_secret: Callable[[str], str],
    tell: Callable[[str], None],
) -> bool:
    """Replace the password and keep the ID. Return False if the prompt stops.

    Nothing passed to ``tell`` contains the old or new password.
    """
    fresh = prompt_password(read_secret, tell)
    if fresh is None:
        return False
    state.set_password(fresh)
    return True


def _have_tty() -> bool:
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return False
    os.close(fd)
    return True


def _tty_secret(prompt: str) -> str:
    """Read one password from the controlling terminal with echo off."""
    global _prompt_active
    if not _have_tty():
        print(
            "A terminal is required so the password is not echoed.",
            file=sys.stderr,
            flush=True,
        )
        raise EOFError
    _prompt_active = True
    try:
        return getpass.getpass(prompt)
    finally:
        _prompt_active = False


def restore_tty_echo() -> None:
    """Turn echo on and drop unread input so a password is not shown."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return
    try:
        attrs = termios.tcgetattr(fd)
        attrs[3] = attrs[3] | termios.ECHO
        termios.tcsetattr(fd, termios.TCSAFLUSH, attrs)
    except termios.error:
        pass
    finally:
        os.close(fd)


def install_exit_signals(state: RobotState) -> Any:
    """SIGINT and SIGTERM ask the session to leave. SIGINT still interrupts."""

    def _handle(signum: int, _frame: Any) -> None:
        if _prompt_active:
            restore_tty_echo()
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


def launch(registry_url: str) -> int:
    robot_id = new_id()
    lock = threading.Lock()

    def emit(text: str) -> None:
        with lock:
            print(text, flush=True)

    emit(f"Robot  registry {registry_url}")
    emit(id_line(robot_id))
    password = prompt_password(_tty_secret, emit)
    if password is None:
        restore_tty_echo()
        return 0
    state = RobotState(robot_id, password, socket.gethostname())
    del password

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
            action = command_for(line)
            if action == "quit":
                state.stop()
                return
            if action == "new-password":
                if not apply_new_password(state, _tty_secret, emit):
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
        restore_tty_echo()
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
