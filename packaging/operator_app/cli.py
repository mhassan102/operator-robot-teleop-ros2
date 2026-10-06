"""Launch the operator console. ``--registry`` selects the signalling URL.

This starts the operator container in UI-only mode, opens
``http://127.0.0.1:8090/`` in a desktop window, and binds
``127.0.0.1:8091`` for mlink start and stop. It does not start mlink.
Closing the window stops the robot and removes the operator container.
``TELEOP_SUPERVISOR_DRY_RUN=1`` returns before docker or mlink is executed.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from packaging.operator_app.helper import Helper
from packaging.operator_app.login import DEFAULT_REGISTRY
from packaging.supervisor.operator_commands import dry_run_enabled, ui_container_plan
from packaging.supervisor.robot_commands import Command

CONSOLE_URL = "http://127.0.0.1:8090/"
_READY = "http://127.0.0.1:8090/api/health"
_QUIT_WAIT_S = 45.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m packaging.operator_app",
        description="Operator desktop window",
    )
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY,
        help=f"registry WebSocket URL (default {DEFAULT_REGISTRY})",
    )
    return parser.parse_args(argv)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def open_desktop(url: str, until: object = None) -> None:
    """Show the console and return when that window closes."""
    from packaging.operator_app.desktop import show_console

    show_console(url, until=until)


def _post_json(url: str, timeout: float = 15.0) -> bool:
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def request_shutdown(console: str, helper_url: str = "") -> bool:
    """Quit through the console so the robot stops before compose down.

    Returns True when the console accepted it. When the console is
    already gone, ``helper_url`` still removes the operator container.
    """
    if _post_json(console.rstrip("/") + "/api/quit"):
        return True
    if helper_url:
        return _post_json(helper_url.rstrip("/") + "/quit")
    return False


def shutdown_after_close(
    helper: Helper,
    helper_url: str,
    *,
    console: str = CONSOLE_URL,
    wait_s: float = _QUIT_WAIT_S,
) -> None:
    """Stop the robot and remove the operator container."""
    if helper.quit_requested():
        return
    accepted = request_shutdown(console, "")
    if not accepted and helper_url and not helper.quit_requested():
        _post_json(helper_url.rstrip("/") + "/quit")
    if helper.wait_for_quit(wait_s):
        return
    if helper_url and not helper.quit_requested():
        _post_json(helper_url.rstrip("/") + "/quit")
        helper.wait_for_quit(wait_s)


def wait_until_ready(url: str = _READY, timeout: float = 60.0) -> bool:
    """Poll the container healthcheck. This does not start mlink."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def launch(
    registry_url: str,
    *,
    root: Path | None = None,
    helper_port: int = 8091,
    runner: Callable[[Command, Path], None] | None = None,
    browser: Callable[[str], None] | None = None,
    wait_ready: Callable[[], None] | None = None,
) -> int:
    """Start the UI container and the loopback helper. Dry-run does neither."""
    if dry_run_enabled():
        return 0
    repo = repo_root() if root is None else Path(root)
    helper = Helper(repo)
    try:
        bound = helper.serve("127.0.0.1", helper_port)
    except OSError as exc:
        print(f"operator helper failed: {exc}", file=sys.stderr)
        return 1
    helper_url = f"http://127.0.0.1:{bound}"
    command = ui_container_plan(repo, registry_url, helper_url)
    try:
        if runner is None:
            from packaging.supervisor import operator_exec

            operator_exec.execute_command(command, repo)
        else:
            runner(command, repo)
    except Exception as exc:
        print(f"operator container failed: {exc}", file=sys.stderr)
        helper.shutdown()
        return 1
    if wait_ready is None:
        wait_until_ready()
    else:
        wait_ready()
    code = 0
    try:
        if browser is None:
            try:
                open_desktop(CONSOLE_URL, until=helper.quit_event())
            except Exception as exc:
                print(f"operator window failed: {exc}", file=sys.stderr)
                code = 1
            shutdown_after_close(helper, helper_url)
        else:
            browser(CONSOLE_URL)
            helper.wait_for_quit()
    finally:
        helper.shutdown()
    return code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return launch(args.registry)
